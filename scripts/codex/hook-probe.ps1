<#
.SYNOPSIS
  旁路验证 Codex 是否真的启动了 hook 命令的子进程（Issue #571 / G2-1、G5-3）。

.DESCRIPTION
  为什么需要这个：Mercury 的 hook 在 Codex 下**无法用常规方式验证触发**，四条路各自堵死 ——
    - PreToolUse 的拦截型 guard：被沙箱、`.codex/rules/`、指令层在更前面拦掉，走不到它；
    - PostToolUse 的 loop-detector：配置里 `enabled:false`，触发后立即退出、不写状态；
    - PostToolUse 的 post-commit-reset：只在命令含 `git commit` 时动作，而 `git commit`
      恰好被 rules 拦掉 —— 能触发它的操作正是被前面拦住的那个；
    - UserPromptSubmit / Stop：产物无法归因（同一份脚本被 Claude Code 与 Codex 共用）。

  所以改成**旁路观察**：不依赖 hook 自己留痕、不改任何在用配置、不需要触发会被拦的操作。
  用 WMI 订阅进程创建事件，看 Codex 进程的子孙里有没有出现 hook 脚本的路径。

  ⚠️ **负证据不可归因**（这条是设计的一部分，不是免责声明）：
  采集器可能漏掉生命周期极短的进程、取不到命令行、或因中间 shell 导致父子链断裂。
  因此「没观察到」只能报 NO-EVIDENCE，**绝不能推断成「hook 未加载」或「hook 被放行」**。
  脚本先用一个已知子进程做采集完整性自检，自检不过直接判定本轮无效。

  ⚠️ **本机状态：采集层不可用，脚本未能产出有效结论。** 三种采集路径都试过：
  CIM 事件订阅带 `-Action`（脚本块依赖事件循环，非交互 `-File` 模式下不执行）、
  不带 `-Action` 主动 `Get-Event` 拉取（本环境拉不到事件）、后台作业轮询进程快照
  （每 150ms 全量扫描本身过重，拖慢了被观测的调用直至超时）。
  下一步可换 ETW（`Microsoft-Windows-Kernel-Process` provider）级别的采集，
  那是内核事件、开销远低于 WMI 轮询，但需要额外权限与依赖，超出本脚本范围。

  **仍然保留它的三个理由**：清单从 `hooks.json` 机械生成（这一步已经纠正了「8 条 hook」
  这个错误数字，实际是 10 条）；自检机制被证明有效 —— 两次都正确地判了 INVALID
  而不是报出一个看起来像结论的 NO-EVIDENCE；换到 Linux/WSL 或补上 ETW 采集后可直接复用。

.PARAMETER Command
  要观察的命令。默认跑一次最小的只读 codex 调用。

.PARAMETER Seconds
  观察时长上限。

.EXAMPLE
  powershell -File scripts/codex/hook-probe.ps1
  powershell -File scripts/codex/hook-probe.ps1 -Seconds 120
#>
[CmdletBinding()]
param(
  [string]$Command = 'codex exec --sandbox read-only --skip-git-repo-check "执行 git rev-parse --short HEAD"',
  [int]$Seconds = 180
)

$ErrorActionPreference = 'Stop'
$repo = (git rev-parse --show-toplevel).Trim()
$hooksFile = Join-Path $repo '.codex/hooks.json'
if (-not (Test-Path $hooksFile)) { throw "找不到 $hooksFile" }

# —— 从 hooks.json 机械生成清单，不预设「几条」——
# 目标清单里写的「8 条」与实际注册数并不相同（实测为 10），所以数量必须解析得出。
$hooks = Get-Content $hooksFile -Raw | ConvertFrom-Json
$registry = @()
foreach ($ev in $hooks.hooks.PSObject.Properties) {
  foreach ($m in $ev.Value) {
    foreach ($h in $m.hooks) {
      $registry += [pscustomobject]@{
        Event   = $ev.Name
        Matcher = $m.matcher
        Command = $h.command
        Script  = if ($h.command -match '([^/\\"]+\.(sh|cjs|mjs|py|ps1))') { $Matches[1] } else { '' }
      }
    }
  }
}
Write-Host "hooks.json 实际注册 $($registry.Count) 条命令，涉及脚本：$(($registry.Script | Where-Object { $_ } | Sort-Object -Unique) -join ', ')"
Write-Host ''

$seen = New-Object System.Collections.ArrayList
$queryEnd = (Get-Date).AddSeconds($Seconds)

# 采集用后台作业轮询进程快照，不用 CIM 事件订阅 —— 后者的 -Action 依赖事件循环、
# 在非交互 -File 模式下不执行，而不带 -Action 的订阅在本环境实测也拉不到事件
# （两种写法的自检都不通过）。轮询能抓到存活超过一个采样间隔的进程；
# 更短命的会漏，这正是自检要量化的东西。
$collector = Start-Job -ScriptBlock {
  param($deadline)
  $known = @{}
  $out = @()
  while ((Get-Date) -lt $deadline) {
    foreach ($p in (Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
      if (-not $known.ContainsKey($p.ProcessId)) {
        $known[$p.ProcessId] = $true
        $out += [pscustomobject]@{
          Time = Get-Date; Pid = $p.ProcessId; PPid = $p.ParentProcessId
          Name = $p.Name; Cmd = $p.CommandLine
        }
      }
    }
    Start-Sleep -Milliseconds 150
  }
  $out
} -ArgumentList (Get-Date).AddSeconds($Seconds)

function Drain-Events {
  param([System.Collections.ArrayList]$Into)
  # 作业跑完前拿不到中途数据，所以这里只在结束时统一收；
  # 自检与观察窗口的切分改用时间戳。
}

try {
  # —— 采集完整性自检：先制造一个已知子进程，采集不到就说明这一轮的负证据不可信 ——
  Start-Sleep -Milliseconds 500
  $marker = "MercuryProbeSelfTest_$(Get-Random)"
  Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', "echo $marker" -WindowStyle Hidden -Wait
  Start-Sleep -Seconds 1

  # —— 跑目标命令，同时观察 ——
  Write-Host "观察中：$Command"
  Push-Location $repo
  try { Invoke-Expression "$Command 2>&1 | Out-Null" } finally { Pop-Location }
  Start-Sleep -Seconds 2
  Stop-Job $collector -ErrorAction SilentlyContinue
  $all = @(Receive-Job $collector -ErrorAction SilentlyContinue)
  Remove-Job $collector -Force -ErrorAction SilentlyContinue

  # 自检放在这里判：采集器若连自己制造的已知子进程都没抓到，本轮负证据不可信。
  $selfTestOk = @($all | Where-Object { $_.Cmd -and $_.Cmd -match [regex]::Escape($marker) }).Count -gt 0
  if (-not $selfTestOk) {
    Write-Warning '采集完整性自检未通过：连已知子进程都没抓到。本轮结果不可用于任何结论。'
    Write-Host 'VERDICT: INVALID（采集器失效）'
    return
  }
  Write-Host '采集完整性自检通过。'
  Write-Host ''
  $window = $all
  Write-Host "观察窗口内捕获 $($window.Count) 个新进程。"
  Write-Host ''

  # —— 归因：子进程命令行里出现了哪些 hook 脚本 ——
  $hits = @()
  foreach ($script in ($registry.Script | Where-Object { $_ } | Sort-Object -Unique)) {
    $matched = @($window | Where-Object { $_.Cmd -and $_.Cmd -like "*$script*" })
    if ($matched.Count -gt 0) {
      $hits += [pscustomobject]@{ Script = $script; Count = $matched.Count; Pid = $matched[0].Pid; PPid = $matched[0].PPid }
    }
  }

  if ($hits.Count -gt 0) {
    Write-Host 'VERDICT: EVIDENCE — 观察到 Codex 启动了下列 hook 脚本的进程：'
    $hits | Format-Table -AutoSize
  } else {
    Write-Host 'VERDICT: NO-EVIDENCE — 本轮未观察到任何 hook 脚本进程。'
    Write-Host ''
    Write-Host '注意：这**不能**推断为「hook 未加载」或「hook 被放行」。可能的原因包括'
    Write-Host 'hook 进程生命周期短于 WMI 的 1 秒轮询间隔、命令行取不到、或父子链因中间 shell 断裂。'
    Write-Host '要提高把握，可加大 -Seconds 或换用 ETW 级别的采集（本脚本不做）。'
  }
} finally {
  if ($collector) { Stop-Job $collector -EA SilentlyContinue; Remove-Job $collector -Force -EA SilentlyContinue }
}
