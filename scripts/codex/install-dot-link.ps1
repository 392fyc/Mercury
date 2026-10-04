#requires -Version 7.0
[CmdletBinding()]
param(
  [string]$UserRoot = [Environment]::GetFolderPath('UserProfile'),
  [string]$PythonPath,
  [string]$OpenSslPath,
  [string]$PairingId,
  [string]$RecipientThreadId,
  [string]$RecipientHostId = 'durable',
  [string]$ProtocolPageId,
  [switch]$Rollback,
  [string]$BackupPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$userPath = [IO.Path]::GetFullPath($UserRoot).TrimEnd('\', '/')
function Write-Receipt($Receipt, [string]$Path) {
  [IO.File]::WriteAllText($Path, ($Receipt | ConvertTo-Json -Depth 8) + "`n", $utf8)
}

if ($Rollback) {
  if (-not $BackupPath) { throw 'Rollback requires -BackupPath.' }
  $receiptPath = Join-Path $BackupPath 'installation.json'
  $receipt = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
  if ($receipt.user_root -ne $userPath) { throw 'Backup belongs to a different user root.' }
  if ($receipt.status -eq 'rolled_back') { throw 'This installation was already rolled back.' }
  $writtenEntries = @($receipt.files | Where-Object { $_.written })
  foreach ($entry in $writtenEntries) {
    $target = [IO.Path]::GetFullPath($entry.target)
    if (-not $target.StartsWith($userPath + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
      throw 'Rollback target is outside the selected user root.'
    }
    if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw "Installed file is missing: $target" }
    if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.installed_sha256) {
      throw "File changed after installation; preserve it and reconcile manually: $target"
    }
    if ($entry.existed) {
      $source = Join-Path $BackupPath $entry.backup_file
      if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw 'Original backup is missing.' }
      if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.backup_sha256) {
        throw 'Original backup changed; preserve all installed files.'
      }
    }
  }
  foreach ($entry in $writtenEntries) {
    if ($entry.existed) { Copy-Item -LiteralPath (Join-Path $BackupPath $entry.backup_file) -Destination $entry.target }
    else { Remove-Item -LiteralPath $entry.target }
  }
  $receipt.status = 'rolled_back'
  $receipt | Add-Member -NotePropertyName rolled_back_at -NotePropertyValue ((Get-Date).ToUniversalTime().ToString('o'))
  Write-Receipt $receipt $receiptPath
  Write-Output 'Rolled back installed files. Signing identity is preserved; revoke the receiver pin separately when ending the pairing.'
  exit 0
}

foreach ($value in @($PythonPath, $OpenSslPath, $PairingId, $RecipientThreadId)) {
  if ([string]::IsNullOrWhiteSpace($value)) { throw 'Installation requires PythonPath, OpenSslPath, PairingId and RecipientThreadId.' }
}
foreach ($program in @($PythonPath, $OpenSslPath)) {
  if (-not (Test-Path -LiteralPath $program -PathType Leaf)) { throw "Runtime not found: $program" }
}
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$sourceSkill = Join-Path $repoRoot '.agents/skills/dot-link'
$installedSkill = Join-Path $userPath '.agents/skills/dot-link'
$stateRoot = Join-Path $userPath '.codex/dot-link'
$globalAgents = Join-Path $userPath '.codex/AGENTS.md'
$configPath = Join-Path $stateRoot 'config.json'
$planned = New-Object System.Collections.Generic.List[object]
foreach ($relative in @('SKILL.md', 'scripts/dot_link.py')) {
  $source = Join-Path $sourceSkill $relative
  if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Missing source: $relative" }
  $planned.Add(@{ target = Join-Path $installedSkill $relative; bytes = [IO.File]::ReadAllBytes($source) })
}
if (Test-Path -LiteralPath $configPath) {
  $existing = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
  if ($existing.pairing_id -ne $PairingId -or $existing.recipient_thread_id -ne $RecipientThreadId) {
    throw 'Existing pairing differs. Do not silently replace a paired receiver.'
  }
} else {
  $config = [ordered]@{
    protocol = 'dot-local/0.2'; pairing_id = $PairingId
    recipient_thread_id = $RecipientThreadId; recipient_host_id = $RecipientHostId
    python = [IO.Path]::GetFullPath($PythonPath); openssl = [IO.Path]::GetFullPath($OpenSslPath)
    private_key = Join-Path $stateRoot 'identity/private.pem'
    public_profile = Join-Path $stateRoot 'identity/public-profile.json'
    receiver_status = 'pending_human_binding'
  }
  if (-not [string]::IsNullOrWhiteSpace($ProtocolPageId)) { $config.protocol_page_id = $ProtocolPageId }
  $planned.Add(@{ target = $configPath; bytes = $utf8.GetBytes(($config | ConvertTo-Json -Depth 6) + "`n") })
}
$begin = '<!-- DOT-LINK:START -->'
$end = '<!-- DOT-LINK:END -->'
$original = if (Test-Path -LiteralPath $globalAgents) { [IO.File]::ReadAllText($globalAgents) } else { '' }
$block = @'
<!-- DOT-LINK:START -->
## dot 与本地代理协作

需要与 dot YC 协作、委派云端测试或取回证据时，读取用户级 `.agents/skills/dot-link/SKILL.md` 和 `.codex/dot-link/config.json`，沿用该安装的稳定签名身份登记当前回复会话。用户授权的任务包含其必要的正常工具使用、代理交互与私有材料传递；已授权步骤无需重复确认。首次接收端公钥登记须有真实人类授权依据；签名本身不授予权限。高危操作及用户明确要求另行授权的行为保留确认，GitHub 评论或回复须有明确授权。仅在任务需要 dot 协作时接入。
<!-- DOT-LINK:END -->
'@
$start = $original.IndexOf($begin, [StringComparison]::Ordinal)
$finish = $original.IndexOf($end, [StringComparison]::Ordinal)
if (($start -lt 0) -ne ($finish -lt 0)) { throw 'Incomplete existing managed block; preserve and reconcile.' }
if ($start -ge 0) {
  if ($finish -lt $start -or $original.IndexOf($begin, $start + $begin.Length, [StringComparison]::Ordinal) -ge 0 -or $original.IndexOf($end, $finish + $end.Length, [StringComparison]::Ordinal) -ge 0) { throw 'Ambiguous managed block.' }
  $updated = $original.Substring(0, $start) + $block + $original.Substring($finish + $end.Length)
} else { $updated = $original + "`n" + $block + "`n" }
$planned.Add(@{ target = $globalAgents; bytes = $utf8.GetBytes($updated) })

$backupRoot = Join-Path $userPath ('.codex/backups/dot-link-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $backupRoot | Out-Null
$entries = New-Object System.Collections.Generic.List[object]
foreach ($item in $planned) {
  $existed = Test-Path -LiteralPath $item.target -PathType Leaf
  $backupFile = [string]$entries.Count + '.original'
  $backupDigest = $null
  if ($existed) {
    Copy-Item -LiteralPath $item.target -Destination (Join-Path $backupRoot $backupFile)
    $backupDigest = (Get-FileHash -LiteralPath (Join-Path $backupRoot $backupFile) -Algorithm SHA256).Hash.ToLowerInvariant()
  }
  $entries.Add([ordered]@{ target = [IO.Path]::GetFullPath($item.target); existed = $existed; backup_file = $backupFile; backup_sha256 = $backupDigest; installed_sha256 = $null; written = $false })
}
$receipt = [ordered]@{ user_root = $userPath; installed_at = (Get-Date).ToUniversalTime().ToString('o'); status = 'prepared'; files = @($entries.ToArray()); private_key_copied = $false }
$receiptPath = Join-Path $backupRoot 'installation.json'
Write-Receipt $receipt $receiptPath
try {
  for ($i = 0; $i -lt $planned.Count; $i++) {
    $item = $planned[$i]
    $parent = Split-Path -Parent $item.target
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent | Out-Null }
    [IO.File]::WriteAllBytes($item.target, $item.bytes)
    $entries[$i].written = $true
    $entries[$i].installed_sha256 = (Get-FileHash -LiteralPath $item.target -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Receipt $receipt $receiptPath
  }
  $receipt.status = 'installed'
} finally { Write-Receipt $receipt $receiptPath }
Write-Output ("Installed dot-link; backup: " + $backupRoot)
Write-Output 'Before identity initialization, protect the identity directory ACL. Installation alone does not activate receiver authorization.'
