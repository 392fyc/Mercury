<#
.SYNOPSIS
  轮询一个 PR 的审查状态；替代 Claude Code 的 CronCreate 轮询（Issue #571 / G4-3）。

.DESCRIPTION
  Codex CLI **无法创建或管理定时任务**（官方明文），所以 pr-flow 里「起一个 cron 等
  Argus review」那条链在 Codex 上没有对应物。这里把定时的职责交给 Windows 计划任务，
  本脚本只负责「被叫醒一次时该做什么」：查一次状态、决定是继续等还是收工。

  设计上刻意保留原 cron 方案的两条安全约定：
    - **轮询上限**（默认 3 次）。原约定就是最多 3 次，防止一个卡住的 PR 让任务无限跑下去。
    - **失败退避**：连续失败会拉长下一次的等待，由计划任务的重复间隔配合状态文件实现。

  与原方案的能力差异（无法弥补，接受即可）：原来是会话内闭环——cron 发现 review 完成后
  Claude 会话直接继续处理；现在只能做到「脚本发现完成 → 记录并通知 → 人来起会话继续」。

.PARAMETER Pr
  PR 号。不给则按当前分支自动解析。

.PARAMETER MaxPolls
  轮询次数上限，默认 3。达到上限即停止并在状态里标记 exhausted。

.PARAMETER StateDir
  状态文件目录，默认 <repo>/.mercury/state。

.PARAMETER Reset
  清掉该 PR 的轮询状态后退出（重新开始一轮观察时用）。

.EXAMPLE
  powershell -File scripts/codex/pr-watch.ps1 -Pr 572

.EXAMPLE
  # 注册成每 4 分钟跑一次的计划任务（当前用户，无需管理员）：
  $act = New-ScheduledTaskAction -Execute 'pwsh.exe' `
      -Argument '-NoProfile -File D:\Mercury\Mercury\scripts\codex\pr-watch.ps1 -Pr 572' `
      -WorkingDirectory 'D:\Mercury\Mercury'
  $trg = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
      -RepetitionInterval (New-TimeSpan -Minutes 4) -RepetitionDuration (New-TimeSpan -Hours 2)
  Register-ScheduledTask -TaskName 'Mercury-PRWatch-572' -Action $act -Trigger $trg
  # 收工后： Unregister-ScheduledTask -TaskName 'Mercury-PRWatch-572' -Confirm:$false
#>
[CmdletBinding()]
param(
  [int]$Pr = 0,
  [int]$MaxPolls = 3,
  [string]$StateDir = '',
  [switch]$Reset
)

$ErrorActionPreference = 'Stop'

# `Get-Content -Raw` 对**空文件**返回 $null（不是空字符串），于是 `.Trim()` 会抛
# "You cannot call a method on a null-valued expression"。而 gh 查询失败时正好
# 只创建文件、不写内容 —— 这条路径必然踩中。叠加 $ErrorActionPreference='Stop'，
# 脚本会当场终止，导致下面的失败退避分支永远执行不到（实测：连查 3 次不存在的 PR，
# 三次都是崩溃，状态文件一次都没写出来）。这个 helper 把空文件与不存在都收敛成 ''。
function Read-TextFile {
  param([string]$Path)
  if (-not (Test-Path $Path)) { return '' }
  $content = Get-Content $Path -Raw -ErrorAction SilentlyContinue
  if ($null -eq $content) { return '' }
  return $content.Trim()
}

function Get-RepoRoot {
  $r = (git rev-parse --show-toplevel 2>$null)
  if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($r)) {
    throw '不在 git 仓库里，无法定位状态目录'
  }
  return $r.Trim()
}

$repo = Get-RepoRoot
if ([string]::IsNullOrWhiteSpace($StateDir)) { $StateDir = Join-Path $repo '.mercury/state' }
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

# 未给 PR 号就按当前分支解析。
if ($Pr -le 0) {
  $branch = (git branch --show-current).Trim()
  # 长 JSON 不要走 PowerShell 管道：把原生命令的 stdout 收成 string[] 再 Out-String，
  # 会按控制台宽度折行，把一行 JSON 打断，ConvertFrom-Json 报
  # "JsonToken EndConstructor is not valid"。让 gh 直接写文件，再整份读回来最稳。
  $tmp1 = Join-Path $StateDir "_prlist-$branch.json"
  gh pr list --head $branch --json number --limit 1 > $tmp1 2>$null
  $json = Read-TextFile $tmp1
  if ($LASTEXITCODE -eq 0 -and $json -and $json -ne '[]') {
    $Pr = ($json | ConvertFrom-Json)[0].number
  }
  if ($Pr -le 0) { throw "当前分支 $branch 上找不到 PR，请用 -Pr 显式指定" }
}

$stateFile = Join-Path $StateDir "pr-watch-$Pr.json"

if ($Reset) {
  if (Test-Path $stateFile) { Remove-Item $stateFile -Force }
  Write-Host "已清除 PR #$Pr 的轮询状态"
  exit 0
}

$fresh = [pscustomobject]@{ pr = $Pr; polls = 0; failures = 0; done = $false; lastStatus = ''; lastSeen = '' }

# 状态文件被写坏或截断（例如上一次运行中途被杀）时，ConvertFrom-Json 会得到 $null，
# 随后 $state.done 又会撞上同一个空引用崩溃。这里退回全新状态而不是崩 —— 丢一轮
# 计数远好过让整个观察链断掉。
$state = $fresh
$raw = Read-TextFile $stateFile
if ($raw) {
  try {
    $parsed = $raw | ConvertFrom-Json
    if ($null -ne $parsed) { $state = $parsed }
    else { Write-Warning "状态文件 $stateFile 解析为空，按全新状态重来。" }
  } catch {
    Write-Warning "状态文件 $stateFile 无法解析（$($_.Exception.Message)），按全新状态重来。"
  }
}

if ($state.done) {
  Write-Host "PR #$Pr 已收工（$($state.lastStatus)），本次跳过。要重新观察请加 -Reset。"
  exit 0
}

if ($state.polls -ge $MaxPolls) {
  Write-Host "PR #$Pr 已达轮询上限 $MaxPolls 次，停止。要继续请加 -Reset 或调大 -MaxPolls。"
  $state.done = $true
  $state.lastStatus = 'exhausted'
  $state | ConvertTo-Json | Set-Content $stateFile -Encoding utf8
  exit 0
}

# —— 查一次状态 ——
$state.polls = [int]$state.polls + 1
$state.lastSeen = (Get-Date).ToString('o')

$tmp = Join-Path $StateDir "_prview-$Pr.json"
gh pr view $Pr --json reviews,reviewDecision,state > $tmp 2>$null
$ghExit = $LASTEXITCODE
$reviewJson = Read-TextFile $tmp
if ($ghExit -ne 0 -or [string]::IsNullOrWhiteSpace($reviewJson)) {
  # 失败不算一次有效轮询 —— 否则网络抖动会白白吃掉配额。
  $state.polls = [int]$state.polls - 1
  $state.failures = [int]$state.failures + 1
  $state | ConvertTo-Json | Set-Content $stateFile -Encoding utf8
  $wait = [Math]::Min(60 * [Math]::Pow(2, $state.failures - 1), 900)
  Write-Warning "查询 PR #$Pr 失败（第 $($state.failures) 次）。建议至少等待 $wait 秒后再试。"
  exit 1
}

$state.failures = 0
# 注意：不能叫 $pr —— PowerShell 变量名大小写不敏感，会撞上参数 [int]$Pr。
$prData = $reviewJson | ConvertFrom-Json
$decision = if ($prData.reviewDecision) { $prData.reviewDecision } else { 'PENDING' }
$reviewCount = if ($prData.reviews) { @($prData.reviews).Count } else { 0 }

Write-Host "PR #$Pr  第 $($state.polls)/$MaxPolls 次  状态=$($prData.state)  审查结论=$decision  已有评审 $reviewCount 条"

$state.lastStatus = $decision
if ($decision -in @('APPROVED', 'CHANGES_REQUESTED') -or $prData.state -ne 'OPEN') {
  $state.done = $true
  Write-Host ''
  Write-Host "==> PR #$Pr 有结论了：$decision（PR 状态 $($prData.state)）。起一个会话继续处理。"
}

$state | ConvertTo-Json | Set-Content $stateFile -Encoding utf8
exit 0
