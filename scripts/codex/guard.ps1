[CmdletBinding()]
param(
  [Parameter(Mandatory = $true, Position = 0)]
  [ValidateSet("status", "mark-review", "clear-review", "pre-stage", "pre-commit", "pre-push")]
  [string]$Action,

  [string]$PushCommand
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..\\..")).Path
$stateDir = Join-Path $repoRoot ".codex\\state"
$reviewFlag = Join-Path $stateDir "review-passed"
$protectedBranches = @("develop", "main", "master")
$featureTaskPattern = "^feature/TASK-[A-Za-z0-9._-]+$"

function Ensure-StateDir {
  if (-not (Test-Path -LiteralPath $stateDir)) {
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
  }
}

function Get-CurrentBranch {
  return (git -C $repoRoot rev-parse --abbrev-ref HEAD).Trim()
}

function Get-StagedTreeHash {
  $tree = (git -C $repoRoot write-tree).Trim()
  if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($tree)) {
    throw "Unable to capture the staged tree snapshot for review verification."
  }

  return $tree
}

function Test-ProtectedBranch {
  param([string]$Branch)
  return $protectedBranches -contains $Branch
}

function Assert-TaskBranch {
  param([string]$Branch)

  if (Test-ProtectedBranch -Branch $Branch) {
    throw "Protected branch '$Branch' is not allowed for Codex commits or pushes. Use feature/TASK-*."
  }

  if ($Branch -notmatch $featureTaskPattern) {
    throw "Branch '$Branch' does not match feature/TASK-*. Move the work to a task branch before mutating git state."
  }
}

function Write-ReviewFlag {
  param([string]$Branch)

  Ensure-StateDir
  $snapshot = [ordered]@{
    reviewedAt = [DateTime]::UtcNow.ToString("o")
    branch = $Branch
    stagedTree = Get-StagedTreeHash
  } | ConvertTo-Json -Compress
  Set-Content -LiteralPath $reviewFlag -Value $snapshot -Encoding ascii
}

function Clear-ReviewFlag {
  if (Test-Path -LiteralPath $reviewFlag) {
    Remove-Item -LiteralPath $reviewFlag -Force
  }
}

function Assert-ReviewFlag {
  if (-not (Test-Path -LiteralPath $reviewFlag)) {
    throw "Missing review flag. Complete a code review, then run 'powershell -ExecutionPolicy Bypass -File scripts/codex/guard.ps1 mark-review'."
  }

  try {
    $snapshot = (Get-Content -LiteralPath $reviewFlag -Raw | ConvertFrom-Json)
  } catch {
    throw "Review flag is unreadable. Re-run mark-review before committing."
  }

  $currentBranch = Get-CurrentBranch
  if ($snapshot.branch -ne $currentBranch) {
    throw "Review flag was recorded on branch '$($snapshot.branch)', but current branch is '$currentBranch'. Re-run mark-review."
  }

  $currentTree = Get-StagedTreeHash
  if ($snapshot.stagedTree -ne $currentTree) {
    throw "Staged content changed after review. Re-run mark-review before committing."
  }
}

function Test-ProtectedPushSpec {
  param([string]$Token)

  if ([string]::IsNullOrWhiteSpace($Token) -or $Token.StartsWith("-")) {
    return $false
  }

  $destination = $Token
  if ($Token.Contains(":")) {
    $destination = $Token.Split(":", 2)[1]
  }

  if ([string]::IsNullOrWhiteSpace($destination)) {
    return $false
  }

  return $destination -match "^(refs/heads/)?(develop|main|master)$"
}

function Assert-SafePushTarget {
  param(
    [string]$Branch,
    [string]$CommandText
  )

  Assert-TaskBranch -Branch $Branch

  if ([string]::IsNullOrWhiteSpace($CommandText)) {
    return
  }

  if ($CommandText -match "(^|\\s)--all(\\s|$)" -or $CommandText -match "(^|\\s)--mirror(\\s|$)") {
    throw "Broad push flags (--all/--mirror) are forbidden in Codex guard mode."
  }

  $tokens = $CommandText -split "\\s+"
  foreach ($token in $tokens) {
    if ($token -in @("git", "push", "origin", "upstream")) {
      continue
    }

    if (Test-ProtectedPushSpec -Token $token) {
      throw "Push command targets a protected branch: $CommandText"
    }
  }
}

$branch = Get-CurrentBranch

switch ($Action) {
  "status" {
    $reviewState = if (Test-Path -LiteralPath $reviewFlag) { "present" } else { "missing" }
    Write-Output "branch=$branch"
    Write-Output "review_flag=$reviewState"
    exit 0
  }
  "mark-review" {
    Assert-TaskBranch -Branch $branch
    Write-ReviewFlag -Branch $branch
    Write-Output "review_flag=present"
    exit 0
  }
  "clear-review" {
    Clear-ReviewFlag
    Write-Output "review_flag=cleared"
    exit 0
  }
  "pre-stage" {
    Assert-TaskBranch -Branch $branch
    Write-Output "pre_stage=pass"
    exit 0
  }
  "pre-commit" {
    Assert-TaskBranch -Branch $branch
    Assert-ReviewFlag
    Write-Output "pre_commit=pass"
    exit 0
  }
  "pre-push" {
    Assert-SafePushTarget -Branch $branch -CommandText $PushCommand
    Write-Output "pre_push=pass"
    exit 0
  }
}
