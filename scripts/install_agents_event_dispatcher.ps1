[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$CodexExe,
    [Parameter(Mandatory = $true)][string]$PythonExe,
    [Parameter(Mandatory = $true)][ValidateRange(0, [int]::MaxValue)][int]$InitialAfter,
    [ValidateSet('mercury-local')][string]$Recipient = 'mercury-local',
    [string]$NativeInstallRoot = 'D:\Program Files\MercuryAgentReceiver'
)

# Install only. Activation and task registration are separate reviewed steps.
$ErrorActionPreference = 'Stop'
$taskRoot = Join-Path $env:USERPROFILE '.codex\dot-link'
$policyDirectory = Join-Path $taskRoot 'event-policy'
$workerRoot = Join-Path $taskRoot 'event-dispatch'
$policyFile = Join-Path $policyDirectory 'dispatcher.json'
$bridgeFile = Join-Path $taskRoot 'event-runtime\bridge.json'
$targetsFile = Join-Path $policyDirectory 'targets.json'
$ownerSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$systemSid = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18')
$adminSid = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
$allowedSids = @($ownerSid.Value, $systemSid.Value, $adminSid.Value)
$utf8 = [System.Text.UTF8Encoding]::new($false)

function Assert-PlainPath([string]$Path, [bool]$MustExist = $true) {
    if (-not [System.IO.Path]::IsPathRooted($Path)) { throw 'An absolute installation path is required.' }
    $absolute = [System.IO.Path]::GetFullPath($Path)
    $part = $absolute
    while ($part) {
        if (Test-Path -LiteralPath $part) {
            $item = Get-Item -LiteralPath $part -Force
            if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                throw 'Installation paths must not contain reparse points.'
            }
        }
        $parent = [System.IO.Directory]::GetParent($part)
        if ($null -eq $parent) { break }
        $part = $parent.FullName
    }
    if ($MustExist -and -not (Test-Path -LiteralPath $absolute)) { throw 'Required installation input is missing.' }
    return $absolute
}

function Assert-ProtectedAcl([string]$Path) {
    $acl = Get-Acl -LiteralPath $Path
    $aclOwner = $acl.Owner
    $ownerValue = ([System.Security.Principal.NTAccount]::new($aclOwner)).Translate([System.Security.Principal.SecurityIdentifier]).Value
    if ($ownerValue -ne $ownerSid.Value) { throw 'Existing policy must be owned by the current user.' }
    if (-not $acl.AreAccessRulesProtected) { throw 'Existing policy directory must disable inherited access.' }
    $accessSids = @($acl.Access | ForEach-Object { $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value })
    if (@($accessSids | Where-Object { $_ -notin $allowedSids }).Count) { throw 'Existing policy has broader access than approved.' }
    foreach ($sid in $allowedSids) {
        if ($sid -notin $accessSids) { throw 'Existing policy is missing an approved administrator or owner rule.' }
    }
}

function Protect-Directory([string]$Path) {
    $acl = [System.Security.AccessControl.DirectorySecurity]::new()
    $acl.SetOwner($ownerSid)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @($ownerSid, $systemSid, $adminSid)) {
        $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
            $sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
    Assert-ProtectedAcl $Path
}

function Write-NewJson([string]$Path, $Value) {
    $stream = [System.IO.File]::Open($Path, 'CreateNew', 'Write', 'None')
    try {
        $bytes = $utf8.GetBytes(($Value | ConvertTo-Json -Depth 10) + "`n")
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    } finally { $stream.Dispose() }
}

$CodexExe = Assert-PlainPath $CodexExe
$PythonExe = Assert-PlainPath $PythonExe
if ([System.IO.Path]::GetFileName($PythonExe) -ine 'python.exe' -or (Get-Item -LiteralPath $PythonExe).PSIsContainer) {
    throw 'Select a trusted native python.exe for installation contract validation.'
}
$NativeInstallRoot = Assert-PlainPath $NativeInstallRoot $false
if (-not $NativeInstallRoot.StartsWith('D:\Program Files\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Install the native runtime below D:\Program Files.'
}
if ([System.IO.Path]::GetFileName($CodexExe) -cne 'codex.exe' -or (Get-Item -LiteralPath $CodexExe).PSIsContainer) {
    throw 'Select the native codex.exe, not a shell wrapper.'
}
foreach ($path in @($policyDirectory, $bridgeFile, $targetsFile)) { $null = Assert-PlainPath $path }
$null = Assert-PlainPath $workerRoot $false
$null = Assert-PlainPath $policyFile $false
Assert-ProtectedAcl $policyDirectory
if ((Test-Path -LiteralPath $workerRoot) -or (Test-Path -LiteralPath $policyFile) -or (Test-Path -LiteralPath $NativeInstallRoot)) {
    throw 'Refusing to overwrite an existing dispatcher installation; preserve its ledger.'
}
$approvedTarget = Get-Content -LiteralPath $targetsFile -Raw | ConvertFrom-Json
$bridgeTarget = Get-Content -LiteralPath $bridgeFile -Raw | ConvertFrom-Json
if ($approvedTarget.principal -cne $Recipient -or $bridgeTarget.principal -cne $Recipient) {
    throw 'Recipient must match the existing queue installation principal.'
}
$sourceDispatcher = Assert-PlainPath (Join-Path $PSScriptRoot 'agents_event_dispatcher.py')
$sourceClient = Assert-PlainPath (Join-Path $PSScriptRoot 'agents_event_client.py')

New-Item -ItemType Directory -Path $workerRoot | Out-Null
Protect-Directory $workerRoot
$nativeRoot = $NativeInstallRoot
New-Item -ItemType Directory -Path $nativeRoot | Out-Null
Protect-Directory $nativeRoot
$nativeSourceRoot = [System.IO.Path]::GetDirectoryName($CodexExe)
foreach ($name in @('codex.exe', 'codex-command-runner.exe', 'codex-windows-sandbox-setup.exe')) {
    $source = Join-Path $nativeSourceRoot $name
    if ($name -ne 'codex.exe' -and -not (Test-Path -LiteralPath $source)) { continue }
    $null = Assert-PlainPath $source
    $destination = Join-Path $nativeRoot $name
    Copy-Item -LiteralPath $source -Destination $destination
    if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -cne (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash) {
        throw 'Installed native executable digest mismatch.'
    }
}
$CodexExe = Join-Path $nativeRoot 'codex.exe'
foreach ($source in @($sourceDispatcher, $sourceClient)) {
    $destination = Join-Path $workerRoot ([System.IO.Path]::GetFileName($source))
    Copy-Item -LiteralPath $source -Destination $destination
    if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -cne (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash) {
        throw 'Installed source digest mismatch.'
    }
}
$schema = [ordered]@{
    '$schema' = 'https://json-schema.org/draft/2020-12/schema'
    title = 'Mercury native event probe receipt'
    type = 'object'; additionalProperties = $false
    required = @('body_sha256', 'challenge', 'event_id', 'executed', 'godot_executed', 'production_modified', 'request_id', 'task_id')
    properties = [ordered]@{
        event_id = @{ type = 'string'; maxLength = 128 }; task_id = @{ type = 'string'; maxLength = 128 }; request_id = @{ type = 'string'; maxLength = 128 }
        challenge = @{ type = 'string'; pattern = '^[A-Za-z0-9_-]{43}$' }; body_sha256 = @{ type = 'string'; pattern = '^[0-9a-f]{64}$' }
        executed = @{ type = 'boolean'; const = $true }
        godot_executed = @{ type = 'boolean'; const = $false }
        production_modified = @{ type = 'boolean'; const = $false }
    }
}
Write-NewJson (Join-Path $workerRoot 'receipt.schema.json') $schema
Write-NewJson (Join-Path $workerRoot 'registry.json') ([ordered]@{ schema = 'mercury-native-event-probe-registry/1'; entries = @() })
Write-NewJson $policyFile ([ordered]@{
    schema = 'mercury-local-event-dispatch/1'; recipient = $Recipient
    client_config = $bridgeFile; codex_exe = $CodexExe; model = 'gpt-6-luna'; provider = 'openai'
    worker_root = $workerRoot; ledger_path = (Join-Path $workerRoot 'ledger.sqlite3')
    registry_path = (Join-Path $workerRoot 'registry.json'); output_schema_path = (Join-Path $workerRoot 'receipt.schema.json')
    poll_interval_seconds = 30; native_timeout_seconds = 180; initial_after = $InitialAfter
})
Write-NewJson (Join-Path $workerRoot 'installation.json') ([ordered]@{
    schema = 'mercury-local-event-installation/1'; installed_at = [DateTime]::UtcNow.ToString('o')
    dispatcher_sha256 = (Get-FileHash -LiteralPath (Join-Path $workerRoot 'agents_event_dispatcher.py') -Algorithm SHA256).Hash.ToLowerInvariant()
    client_sha256 = (Get-FileHash -LiteralPath (Join-Path $workerRoot 'agents_event_client.py') -Algorithm SHA256).Hash.ToLowerInvariant()
    codex_sha256 = (Get-FileHash -LiteralPath $CodexExe -Algorithm SHA256).Hash.ToLowerInvariant()
    policy = $policyFile; worker_root = $workerRoot; native_root = $nativeRoot; background_started = $false
})
$readbackCode = @'
import datetime as dt
import json
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import agents_event_dispatcher as dispatcher
policy = dispatcher.load_policy()
if dispatcher.load_registry(policy, dt.datetime.now(dt.timezone.utc)) != {}:
    raise SystemExit('Installed registration must be empty')
manifest = json.loads((Path(sys.argv[1]) / 'installation.json').read_bytes())
if (manifest['background_started'] is not False
        or str(policy.worker_root) != manifest['worker_root']
        or str(policy.codex_exe.parent) != manifest['native_root']):
    raise SystemExit('Installed manifest does not match the loaded policy')
print('Installation contract readback passed; no process or model was started.')
'@
& $PythonExe -B -c $readbackCode $workerRoot
if ($LASTEXITCODE -ne 0) { throw 'Installed dispatcher contract readback failed. Preserve files for inspection.' }
foreach ($directory in @($policyDirectory, $workerRoot, $nativeRoot)) { Assert-ProtectedAcl $directory }
Write-Output 'Dispatcher installed with an empty registry. No process or model was started.'
