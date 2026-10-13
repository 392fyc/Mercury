#requires -Version 7.0
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('mercury-native-wakeup-install-' + [Guid]::NewGuid().ToString('N'))
$skillPath = Join-Path $testRoot '.agents/skills/dot-link/SKILL.md'
$guidePath = Join-Path $testRoot '.agents/skills/dot-link/references/native-thread-wakeup.md'
$originalSkill = "Existing receiver rules and newer ledger instructions must survive.`r`n"
$fixtures = [ordered]@{
  '.agents/skills/dot-link/SKILL.md' = $originalSkill
  '.agents/skills/dot-link/references/receiver-protocol.md' = "Existing activated protocol; preserve its exact bytes.`r`n"
  '.agents/skills/dot-link/scripts/dot_link.py' = "Existing newer verifier; do not downgrade.`r`n"
  '.codex/dot-link/config.json' = '{"pairing_id":"existing-pairing","recipient_thread_id":"existing-root","receiver_status":"active"}'
  '.codex/dot-link/identity/private.pem' = 'TEST FIXTURE ONLY; NOT A KEY'
  '.codex/dot-link/task-ledger.sqlite' = 'EXISTING LEDGER TEST FIXTURE'
  '.codex/AGENTS.md' = "Independent user rules.`r`n"
}
$before = @{}
foreach ($relative in $fixtures.Keys) {
  $path = Join-Path $testRoot $relative
  New-Item -ItemType Directory -Path (Split-Path -Parent $path) -Force | Out-Null
  [IO.File]::WriteAllText($path, $fixtures[$relative], $utf8)
  $before[$relative] = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
}
$hostExecutable = (Get-Process -Id $PID).Path
$installer = Join-Path $PSScriptRoot 'install-dot-link.ps1'
$output = & $hostExecutable -NoProfile -File $installer -UserRoot $testRoot -WakeupGuideOnly -ExpectedSkillSha256 $before['.agents/skills/dot-link/SKILL.md'].ToLowerInvariant()
if ($LASTEXITCODE -ne 0) { throw 'Supplement installation failed.' }
$lines = @($output | Where-Object { $_ -like 'Installed dot-link; backup: *' })
if ($lines.Count -ne 1) { throw 'Supplement installation did not return one backup.' }
$backup = $lines[0].Substring('Installed dot-link; backup: '.Length)
$receipt = Get-Content -LiteralPath (Join-Path $backup 'installation.json') -Raw | ConvertFrom-Json
if ($receipt.files.Count -ne 2 -or $receipt.private_key_copied) { throw 'Supplement touched unexpected files.' }
foreach ($relative in $fixtures.Keys | Where-Object { $_ -ne '.agents/skills/dot-link/SKILL.md' }) {
  if ((Get-FileHash -LiteralPath (Join-Path $testRoot $relative) -Algorithm SHA256).Hash -cne $before[$relative]) { throw "Supplement changed protected bytes: $relative" }
}
if (-not [IO.File]::ReadAllText($skillPath).StartsWith($originalSkill, [StringComparison]::Ordinal)) { throw 'Supplement replaced existing receiver rules.' }
$sourceGuide = Join-Path $PSScriptRoot '../../.agents/skills/dot-link/references/native-thread-wakeup.md'
if ((Get-FileHash -LiteralPath $guidePath -Algorithm SHA256).Hash -cne (Get-FileHash -LiteralPath $sourceGuide -Algorithm SHA256).Hash) { throw 'Guide bytes differ from reviewed source.' }
& $hostExecutable -NoProfile -File $installer -UserRoot $testRoot -Rollback -BackupPath $backup | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Supplement rollback failed.' }
foreach ($relative in $fixtures.Keys) {
  if ((Get-FileHash -LiteralPath (Join-Path $testRoot $relative) -Algorithm SHA256).Hash -cne $before[$relative]) { throw "Rollback changed fixture bytes: $relative" }
}
if (Test-Path -LiteralPath $guidePath) { throw 'Rollback left the new guide behind.' }
[IO.File]::AppendAllText($skillPath, "`nLater user edit.`n", $utf8)
$laterHash = (Get-FileHash -LiteralPath $skillPath -Algorithm SHA256).Hash
& $hostExecutable -NoProfile -File $installer -UserRoot $testRoot -WakeupGuideOnly -ExpectedSkillSha256 $before['.agents/skills/dot-link/SKILL.md'].ToLowerInvariant() 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { throw 'Supplement accepted a stale reviewed snapshot.' }
if ((Get-FileHash -LiteralPath $skillPath -Algorithm SHA256).Hash -cne $laterHash -or (Test-Path -LiteralPath $guidePath)) { throw 'Stale-snapshot rejection changed later user edits.' }
[IO.File]::AppendAllText($skillPath, '<!-- DOT-LINK-NATIVE-WAKEUP:START -->', $utf8)
$malformedHash = (Get-FileHash -LiteralPath $skillPath -Algorithm SHA256).Hash
& $hostExecutable -NoProfile -File $installer -UserRoot $testRoot -WakeupGuideOnly -ExpectedSkillSha256 $malformedHash.ToLowerInvariant() 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { throw 'Supplement accepted an incomplete managed reference.' }
if ((Get-FileHash -LiteralPath $skillPath -Algorithm SHA256).Hash -cne $malformedHash -or (Test-Path -LiteralPath $guidePath)) { throw 'Rejected supplement partially changed the installation.' }
Write-Output ('PASS: supplement preserves activated protocol, verifier, identity, config, ledger and user rules; exact rollback and malformed-block rejection; evidence: ' + $testRoot)
$global:LASTEXITCODE = 0
