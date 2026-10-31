# Forwarder: the controlled Git entry lives in the git-safe pack (Issue #723); this path stays for existing references.
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true, Position = 0)]
  [ValidateSet("add", "commit", "push", "sync", "abort-sync", "fast-forward")]
  [string]$Action,

  [string]$Message,

  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$Arguments
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "..\..\packs\git-safe\scripts\git-safe.ps1") @PSBoundParameters
exit $LASTEXITCODE
