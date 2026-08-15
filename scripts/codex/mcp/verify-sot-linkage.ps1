[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$EvidenceRoot,

    [switch]$ReadOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ExpectedEvidenceHashes = @{
    'authority.json' = '7c87e64aa1a653b174dd03aee71c7c3ddf688d2f31c8190fa0614f50be8706a1'
    'rules-parity.json' = '740baec26c39a576813b48e31510ac4fbd677da5670fd3883edbe55da3faeabd'
    'godot-mcp.json' = '736ad5372e7659700eb2d0eae35e07ec7bf408e163ccea19517a4796d610499f'
    'obsidian-mcp.json' = 'eae5ed1a14fc973f5c58ff3ebdc984df82855ec55a6536cc916c16e5e0f6687e'
    'kb-fallback.json' = '35e4562adaa0d33ffc4cb1d421aa245200cec10d2fabea49258eb6bed4dd1900'
}

$ForbiddenSecretPatterns = @(
    '(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}',
    '(?i)Bearer\s+[A-Za-z0-9._~+/=-]{12,}',
    '(?i)authorization\s*[=:]\s*["'']?[^\s"'']{8,}',
    '-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----',
    '(?i)"bearer_token"\s*:\s*"[^"]+"'
)

if (-not $ReadOnly) {
    throw 'This verifier is composition-only. Pass -ReadOnly.'
}

function Get-Sha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Referenced artifact is missing: $Path"
    }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-Equal($Actual, $Expected, [string]$Label) {
    if ($Actual -ne $Expected) {
        throw "$Label mismatch."
    }
}

function Assert-True([bool]$Condition, [string]$Label) {
    if (-not $Condition) {
        throw "$Label failed."
    }
}

function Assert-Sha256([string]$Value, [string]$Label) {
    if ($Value -notmatch '^[0-9a-f]{64}$') {
        throw "$Label is not a lowercase SHA-256 value."
    }
}

function Read-Evidence([string]$Name, [string]$Schema) {
    $path = Join-Path $script:ResolvedEvidenceRoot $Name
    $resolved = (Resolve-Path -LiteralPath $path).Path
    $prefix = $script:ResolvedEvidenceRoot.TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Evidence path escaped the evidence root: $Name"
    }
    $item = Get-Item -LiteralPath $resolved -Force
    if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        throw "Evidence must not be a reparse point: $Name"
    }
    Assert-Equal (Get-Sha256 $resolved) $script:ExpectedEvidenceHashes[$Name] "$Name fixed evidence hash"
    $raw = Get-Content -LiteralPath $resolved -Raw -Encoding UTF8
    foreach ($pattern in $script:ForbiddenSecretPatterns) {
        if ([System.Text.RegularExpressions.Regex]::IsMatch($raw, $pattern)) {
            throw "$Name contains a forbidden secret category."
        }
    }
    $document = $raw | ConvertFrom-Json
    Assert-Equal $document.schema $Schema "$Name schema"
    Assert-Equal $document.issue 'TASK-571' "$Name issue"
    Assert-Equal $document.result 'PASS' "$Name result"
    Assert-True (-not [string]::IsNullOrWhiteSpace([string]$document.owning_lane)) "$Name owning lane"
    $timestamp = [DateTimeOffset]::Parse([string]$document.observed_at_utc)
    Assert-Equal $timestamp.Offset ([TimeSpan]::Zero) "$Name timestamp offset"
    return $document
}

$rootItem = Get-Item -LiteralPath $EvidenceRoot -Force
if (-not $rootItem.PSIsContainer) {
    throw "Evidence root is not a directory: $EvidenceRoot"
}
if ($rootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
    throw 'Evidence root must not be a reparse point.'
}
$ResolvedEvidenceRoot = (Resolve-Path -LiteralPath $EvidenceRoot).Path

$authority = Read-Evidence 'authority.json' 'mercury.sot-linkage.authority-evidence.v1'
Assert-Equal $authority.authority_test.exit_code 0 'authority test exit code'
Assert-Equal $authority.authority_test.passed 17 'authority test pass count'
Assert-Equal $authority.authority_test.failed 0 'authority test failure count'
Assert-Sha256 $authority.source_receipt.sha256 'authority receipt hash'
Assert-Sha256 $authority.mercury_test_receipt.sha256 'Mercury test receipt hash'
Assert-Equal (Get-Sha256 $authority.source_receipt.path) $authority.source_receipt.sha256 'authority receipt content'
Assert-Equal (Get-Sha256 $authority.mercury_test_receipt.path) $authority.mercury_test_receipt.sha256 'Mercury test receipt content'
Assert-Equal $authority.contains_live_rule_values $false 'authority live rule value policy'
Assert-Equal $authority.contains_secrets $false 'authority secret policy'

$parity = Read-Evidence 'rules-parity.json' 'mercury.sot-linkage.rules-parity-evidence.v1'
Assert-Equal $parity.api_http_status 200 'rules API status'
Assert-Equal $parity.api_record_count 23 'rules API count'
Assert-Equal $parity.snapshot_record_count 23 'rules snapshot count'
Assert-Equal $parity.difference_count 0 'rules difference count'
Assert-Sha256 $parity.api_normalized_sha256 'rules API hash'
Assert-Equal $parity.api_normalized_sha256 $parity.snapshot_normalized_sha256 'rules parity hash'
Assert-Equal (Get-Sha256 $parity.source_record.path) $parity.source_record.sha256 'rules acceptance record content'
Assert-Equal $parity.contains_api_body $false 'rules API body policy'
Assert-Equal $parity.contains_rule_values $false 'rules value policy'
Assert-Equal $parity.contains_secrets $false 'rules secret policy'

$godot = Read-Evidence 'godot-mcp.json' 'mercury.sot-linkage.godot-mcp-evidence.v1'
Assert-Equal $godot.configuration.scope 'ephemeral-cli-override' 'Godot configuration scope'
Assert-Equal $godot.configuration.global_server_present $false 'global Godot server state'
Assert-Equal $godot.configuration.command 'npx' 'Godot launcher'
Assert-Equal (($godot.configuration.args | ForEach-Object { [string]$_ }) -join ' ') '-y @satelliteoflove/godot-mcp@2.15.0' 'Godot package pin'
Assert-Equal $godot.configuration.startup_timeout_sec 60 'Godot startup timeout'
Assert-Equal $godot.configuration.legacy_lazy_wrapper_used $false 'Godot lazy wrapper state'
Assert-Equal $godot.tool_call.server 'godot' 'Godot tool server'
Assert-Equal $godot.tool_call.tool 'project' 'Godot tool name'
Assert-Equal $godot.tool_call.arguments.action 'get_info' 'Godot action'
Assert-Equal $godot.tool_call.status 'completed' 'Godot call status'
Assert-Equal $godot.tool_call.exit_code 0 'Godot call exit code'
Assert-True ($godot.tool_call.project_name_match -and $godot.tool_call.project_path_match -and $godot.tool_call.main_scene_match) 'Godot semantic result'
Assert-Sha256 $godot.tool_call.semantic_result_sha256 'Godot result hash'
Assert-Equal (Get-Sha256 'D:\ShipOfTheseus\Ship_of_Theseus\addons\godot_mcp\plugin.cfg') $godot.godot.plugin_cfg_sha256 'Godot plugin content'
Assert-Equal $godot.godot.protected_status_sha256_pre $godot.godot.protected_status_sha256_post 'Godot protected status'
Assert-Equal $godot.lifecycle_postcheck.godot_mcp_process_count 0 'Godot MCP residual process count'
Assert-Equal $godot.lifecycle_postcheck.port_6550_established_count 0 'Godot established connection count'
Assert-Equal $godot.contains_raw_tool_output $false 'Godot raw output policy'
Assert-Equal $godot.contains_secrets $false 'Godot secret policy'

$obsidian = Read-Evidence 'obsidian-mcp.json' 'mercury.sot-linkage.obsidian-mcp-evidence.v1'
Assert-Equal $obsidian.configuration.url 'http://127.0.0.1:27123/mcp/' 'Obsidian URL'
Assert-Equal $obsidian.configuration.bearer_token_env_var 'OBSIDIAN_API_KEY' 'Obsidian token reference'
Assert-Equal $obsidian.configuration.required $false 'Obsidian required mode'
Assert-Equal $obsidian.configuration.default_tools_approval_mode 'writes' 'Obsidian approval mode'
Assert-Equal $obsidian.configuration.static_authorization_present $false 'Obsidian static authorization state'
Assert-Equal (Get-Sha256 (Join-Path $env:USERPROFILE '.codex\config.toml')) $obsidian.configuration.config_sha256 'Obsidian config content'
Assert-True ($obsidian.probe.write_ok -and $obsidian.probe.first_mcp_read_matches_local -and $obsidian.probe.guarded_second_mcp_read_matches_local) 'Obsidian write/read guard'
Assert-True ($obsidian.probe.trash_delete_ok -and $obsidian.probe.probe_absent_after) 'Obsidian cleanup'
Assert-Sha256 $obsidian.probe.content_sha256 'Obsidian content hash'
Assert-Equal $obsidian.kb_invariant.status_sha256_pre $obsidian.kb_invariant.status_sha256_post 'Obsidian KB status'
Assert-Equal $obsidian.kb_invariant.inventory_sha256_pre $obsidian.kb_invariant.inventory_sha256_post 'Obsidian KB inventory'
Assert-Equal $obsidian.contains_note_body $false 'Obsidian note body policy'
Assert-Equal $obsidian.contains_token $false 'Obsidian token policy'
Assert-Equal $obsidian.contains_raw_tool_output $false 'Obsidian raw output policy'
$obsidianProbe = Join-Path 'D:\ShipOfTheseus\ShipOfTheseus-KB' ($obsidian.probe.path -replace '/', '\')
Assert-True (-not (Test-Path -LiteralPath $obsidianProbe)) 'Obsidian probe absence'

$fallback = Read-Evidence 'kb-fallback.json' 'mercury.sot-linkage.kb-fallback-evidence.v1'
Assert-Equal (Get-Sha256 $fallback.tool.path) $fallback.tool.sha256 'KB fallback tool content'
Assert-True ($fallback.configuration.obsidian_mcp_disabled_during_probe -and $fallback.configuration.obsidian_mcp_restored_after_probe) 'KB fallback configuration lifecycle'
Assert-Equal $fallback.probe.kb_write_exit_code 0 'KB fallback write exit code'
Assert-Equal $fallback.probe.kb_cat_exit_code 0 'KB fallback read exit code'
Assert-True ($fallback.probe.local_readback_prefix_match -and $fallback.probe.probe_absent_after) 'KB fallback readback and cleanup'
Assert-Sha256 $fallback.probe.content_sha256 'KB fallback content hash'
Assert-Equal $fallback.kb_invariant.status_sha256_pre $fallback.kb_invariant.status_sha256_post 'KB fallback status'
Assert-Equal $fallback.contains_note_body $false 'KB fallback note body policy'
Assert-Equal $fallback.contains_token $false 'KB fallback token policy'
$fallbackProbe = Join-Path $fallback.kb_root ($fallback.probe.path -replace '/', '\')
Assert-True (-not (Test-Path -LiteralPath $fallbackProbe)) 'KB fallback probe absence'

$verdict = [ordered]@{
    schema = 'mercury.sot-linkage.verdict.v1'
    issue = 'TASK-571'
    read_only = $true
    evidence_root = $ResolvedEvidenceRoot
    sections = [ordered]@{
        authority = 'PASS'
        rules_parity = 'PASS'
        godot_mcp = 'PASS'
        obsidian_mcp = 'PASS'
        kb_fallback = 'PASS'
    }
    contains_secrets = $false
    result = 'PASS'
}

$verdict | ConvertTo-Json -Depth 8 -Compress
