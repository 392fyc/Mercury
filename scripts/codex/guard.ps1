# Forwarder: the guard lives in the git-safe pack (Issue #723); this path stays for existing references.
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true, Position = 0)]
  [ValidateSet("status", "mark-review", "clear-review", "pre-stage", "pre-commit", "pre-push", "pre-merge")]
  [string]$Action,

  [string]$PushCommand,

  [int]$PullRequestNumber,

  [string]$NativeReviewReceipt
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "..\..\packs\git-safe\scripts\guard.ps1") @PSBoundParameters
exit $LASTEXITCODE
