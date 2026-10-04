#requires -Version 7.0
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('mercury-dot-link-install-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path (Join-Path $testRoot '.codex') | Out-Null
$utf8 = New-Object System.Text.UTF8Encoding($false)
$original = "原有用户规则必须保留。`r`n额外配置由用户维护。`r`n"
$global = Join-Path $testRoot '.codex/AGENTS.md'
[IO.File]::WriteAllText($global, $original, $utf8)
$hostExecutable = (Get-Process -Id $PID).Path
$installer = Join-Path $PSScriptRoot 'install-dot-link.ps1'
# The installer checks executable existence; cryptography is tested separately.
$installed = & $hostExecutable -NoProfile -File $installer -UserRoot $testRoot -PythonPath $hostExecutable -OpenSslPath $hostExecutable -PairingId 'isolated-test' -RecipientThreadId 'receiver-test'
if ($LASTEXITCODE -ne 0) { throw 'Isolated installation failed.' }
$backupLine = @($installed | Where-Object { $_ -like 'Installed dot-link; backup: *' })
if ($backupLine.Count -ne 1) { throw 'Installation did not return exactly one backup.' }
$backup = $backupLine[0].Substring('Installed dot-link; backup: '.Length)
$receipt = Get-Content -LiteralPath (Join-Path $backup 'installation.json') -Raw | ConvertFrom-Json
if ($receipt.private_key_copied -or $receipt.files.Count -ne 4) { throw 'Unexpected installation inventory.' }
if (-not [IO.File]::ReadAllText($global).StartsWith($original.TrimEnd("`r", "`n"))) { throw 'Existing user rules changed.' }
if (Test-Path -LiteralPath (Join-Path $testRoot '.codex/dot-link/identity/private.pem')) { throw 'Installer unexpectedly created a signing key.' }
$originalEntry = @($receipt.files | Where-Object { $_.existed })[0]
$originalBackup = Join-Path $backup $originalEntry.backup_file
$backupBytes = [IO.File]::ReadAllBytes($originalBackup)
Remove-Item -LiteralPath $originalBackup
& $hostExecutable -NoProfile -File $installer -UserRoot $testRoot -Rollback -BackupPath $backup 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { throw 'Rollback accepted a missing backup.' }
foreach ($entry in $receipt.files) {
  if ((Get-FileHash -LiteralPath $entry.target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.installed_sha256) { throw 'Missing-backup rejection partially changed installed files.' }
}
[IO.File]::WriteAllBytes($originalBackup, $backupBytes)
$skill = Join-Path $testRoot '.agents/skills/dot-link/SKILL.md'
$skillBytes = [IO.File]::ReadAllBytes($skill)
[IO.File]::AppendAllText($skill, "`n后来用户的改动。`n", $utf8)
& $hostExecutable -NoProfile -File $installer -UserRoot $testRoot -Rollback -BackupPath $backup 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { throw 'Rollback overwrote a later user edit.' }
if (-not (Test-Path -LiteralPath $global)) { throw 'Rejected rollback partially removed user files.' }
[IO.File]::WriteAllBytes($skill, $skillBytes)
& $hostExecutable -NoProfile -File $installer -UserRoot $testRoot -Rollback -BackupPath $backup | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Rollback failed after restoring installed bytes.' }
if ([IO.File]::ReadAllText($global) -cne $original) { throw 'Rollback did not restore original bytes.' }
$rolledBack = Get-Content -LiteralPath (Join-Path $backup 'installation.json') -Raw | ConvertFrom-Json
if ($rolledBack.status -ne 'rolled_back' -or -not $rolledBack.rolled_back_at) { throw 'Rollback did not persist its completion receipt.' }
foreach ($entry in $receipt.files | Where-Object { -not $_.existed }) {
  if (Test-Path -LiteralPath $entry.target) { throw 'Rollback left an installed file.' }
}
$malformedRoot = Join-Path $testRoot 'malformed-case'
New-Item -ItemType Directory -Path (Join-Path $malformedRoot '.codex') | Out-Null
$malformedGlobal = Join-Path $malformedRoot '.codex/AGENTS.md'
$malformedBytes = $utf8.GetBytes($original + '<!-- DOT-LINK:START -->')
[IO.File]::WriteAllBytes($malformedGlobal, $malformedBytes)
& $hostExecutable -NoProfile -File $installer -UserRoot $malformedRoot -PythonPath $hostExecutable -OpenSslPath $hostExecutable -PairingId 'isolated-test' -RecipientThreadId 'receiver-test' 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { throw 'Installation accepted an incomplete managed block.' }
if ([IO.File]::ReadAllText($malformedGlobal) -cne $utf8.GetString($malformedBytes)) { throw 'Rejected installation changed existing rules.' }
if (Test-Path -LiteralPath (Join-Path $malformedRoot '.agents/skills/dot-link/SKILL.md')) { throw 'Rejected installation partially installed the skill.' }
if (Test-Path -LiteralPath (Join-Path $malformedRoot '.codex/dot-link/config.json')) { throw 'Rejected installation partially wrote the config.' }
Write-Output ('PASS: isolated installation, preflight failures, later-edit protection, byte-exact rollback and receipt; evidence: ' + $testRoot)
