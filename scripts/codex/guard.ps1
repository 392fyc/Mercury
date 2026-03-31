[CmdletBinding()]
param(
  [Parameter(Mandatory = $true, Position = 0)]
  [ValidateSet("status", "mark-review", "clear-review", "pre-stage", "pre-commit", "pre-push", "pre-merge")]
  [string]$Action,

  [string]$PushCommand,

  [int]$PullRequestNumber
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..\\..")).Path
$stateDir = Join-Path $repoRoot ".codex\\state"
$reviewFlag = Join-Path $stateDir "review-passed"
$protectedBranches = @("develop", "main", "master")
$featureTaskPattern = "^feature/TASK-[A-Za-z0-9._-]+$"

function Initialize-StateDir {
  if (-not (Test-Path -LiteralPath $stateDir)) {
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
  }
}

function Get-CurrentBranch {
  $branchOutput = git -C $repoRoot rev-parse --abbrev-ref HEAD
  if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($branchOutput)) {
    throw "Unable to determine the current git branch."
  }

  return $branchOutput.Trim()
}

function Get-StagedTreeHash {
  $treeOutput = git -C $repoRoot write-tree
  $tree = if ($LASTEXITCODE -eq 0 -and $treeOutput) { $treeOutput.Trim() } else { "" }
  if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($tree)) {
    throw "Unable to capture the staged tree snapshot for review verification."
  }

  return $tree
}

function Get-CurrentHead {
  $headOutput = git -C $repoRoot rev-parse HEAD
  if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($headOutput)) {
    throw "Unable to determine the current git HEAD."
  }

  return $headOutput.Trim()
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

  Initialize-StateDir
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

function Get-ConfiguredRemotes {
  $remotes = @(git -C $repoRoot remote 2>$null)
  if ($LASTEXITCODE -ne 0 -or $remotes.Count -eq 0) {
    return @("origin", "upstream")
  }

  $normalized = @(
    $remotes |
      ForEach-Object { $_.Trim() } |
      Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
  )

  $preferred = @()
  foreach ($name in @("upstream", "origin")) {
    if ($normalized -contains $name) {
      $preferred += $name
    }
  }

  foreach ($name in $normalized) {
    if ($preferred -notcontains $name) {
      $preferred += $name
    }
  }

  return $preferred
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

  if ($CommandText -match '(^|\s)--all(\s|$)' -or $CommandText -match '(^|\s)--mirror(\s|$)') {
    throw "Broad or destructive push flags are forbidden in Codex guard mode."
  }

  if ($CommandText -match '(^|\s)(--force(\S*)?|--delete|-d|-f)(\s|$)') {
    throw "Destructive push flags (--force/--force-with-lease/--delete) are forbidden in Codex guard mode."
  }

  $tokens = $CommandText -split '\s+'
  $knownTokens = @("git", "push") + (Get-ConfiguredRemotes)
  foreach ($token in $tokens) {
    if ($token -match '^-[^-][A-Za-z]+$') {
      $shortFlags = $token.Substring(1).ToCharArray()
      if ($shortFlags -contains 'f' -or $shortFlags -contains 'd') {
        throw "Destructive push flags (--force/--force-with-lease/--delete) are forbidden in Codex guard mode."
      }
    }

    if ($token -in $knownTokens) {
      continue
    }

    if ($token.StartsWith(":")) {
      throw "Branch deletion refspecs are forbidden in Codex guard mode: $CommandText"
    }

    if (Test-ProtectedPushSpec -Token $token) {
      throw "Push command targets a protected branch: $CommandText"
    }
  }
}

function Get-RemoteHosts {
  $hosts = New-Object System.Collections.Generic.List[string]

  foreach ($remoteName in Get-ConfiguredRemotes) {
    $remoteOutput = git -C $repoRoot remote get-url $remoteName 2>$null
    $remote = if ($LASTEXITCODE -eq 0 -and $remoteOutput) { $remoteOutput.Trim() } else { "" }
    if ([string]::IsNullOrWhiteSpace($remote)) {
      continue
    }

    $remoteHost = $null
    try {
      $uri = [Uri]$remote
      if ($uri.IsAbsoluteUri -and -not [string]::IsNullOrWhiteSpace($uri.DnsSafeHost)) {
        $remoteHost = $uri.DnsSafeHost
      }
    } catch {
      $remoteHost = $null
    }

    if (-not $remoteHost -and $remote -match '^[^@]+@(?<host>[^:]+):.+$') {
      $remoteHost = $Matches["host"]
    }

    if (-not [string]::IsNullOrWhiteSpace($remoteHost) -and -not $hosts.Contains($remoteHost)) {
      $hosts.Add($remoteHost)
    }
  }

  if ($hosts.Count -eq 0) {
    $hosts.Add("github.com")
  }

  return @($hosts)
}

function Assert-GhCliAvailable {
  $ghCommand = Get-Command gh -ErrorAction SilentlyContinue
  if (-not $ghCommand) {
    throw "GitHub CLI (gh) is not installed or not in PATH. Install it and run 'gh auth login' before using pre-merge checks."
  }

  $candidateHosts = Get-RemoteHosts
  $errors = @()
  foreach ($remoteHost in $candidateHosts) {
    $authStatus = & gh auth status --active --hostname $remoteHost 2>&1
    if ($LASTEXITCODE -eq 0) {
      return
    }

    $detail = ($authStatus | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($detail)) {
      $detail = "Run 'gh auth login' for this host and retry."
    }
    $errors += "${remoteHost}: $detail"
  }

  throw "GitHub CLI authentication check failed for hosts [$($candidateHosts -join ', ')]. $($errors -join ' | ')"
}

function Get-RepositoryCoordinates {
  $repoJson = & gh repo view --json owner,name 2>&1
  if ($LASTEXITCODE -ne 0) {
    $detail = ($repoJson | Out-String).Trim()
    throw "Unable to determine the current GitHub repository via 'gh repo view --json owner,name'. $detail"
  }

  $repo = $repoJson | ConvertFrom-Json
  if (-not $repo) {
    throw "Unable to determine the current GitHub repository via 'gh repo view --json owner,name'."
  }

  return [pscustomobject]@{
    owner = $repo.owner.login
    name = $repo.name
  }
}

function Get-CheckLabel {
  param([object]$Check)

  $nameProp = $Check.PSObject.Properties["name"]
  if ($nameProp -and -not [string]::IsNullOrWhiteSpace([string]$nameProp.Value)) {
    return [string]$nameProp.Value
  }

  $contextProp = $Check.PSObject.Properties["context"]
  if ($contextProp -and -not [string]::IsNullOrWhiteSpace([string]$contextProp.Value)) {
    return [string]$contextProp.Value
  }

  return "unknown-check"
}

function Assert-PreMergeReady {
  param(
    [int]$Number,
    [string]$ExpectedBranch,
    [string]$ExpectedHead
  )

  if ($Number -le 0) {
    throw "PullRequestNumber must be a positive integer for pre-merge checks."
  }

  $pr = gh pr view $Number --json reviewDecision,statusCheckRollup,headRefName,headRefOid | ConvertFrom-Json
  if ($LASTEXITCODE -ne 0 -or -not $pr) {
    throw "Unable to fetch PR metadata for #$Number."
  }

  if ($pr.headRefName -ne $ExpectedBranch) {
    throw "PR #$Number belongs to branch '$($pr.headRefName)', but current branch is '$ExpectedBranch'."
  }

  if ($pr.headRefOid -ne $ExpectedHead) {
    throw "PR #$Number is at '$($pr.headRefOid)', but current HEAD is '$ExpectedHead'. Push/fetch and retry."
  }

  if ($pr.reviewDecision -ne "APPROVED") {
    throw "PR #$Number is not approved. Current reviewDecision: $($pr.reviewDecision)"
  }

  $failingChecks = @()
  foreach ($check in @($pr.statusCheckRollup)) {
    $name = Get-CheckLabel -Check $check
    if ($check.__typename -eq "CheckRun") {
      if (
        $check.status -ne "COMPLETED" -or
        $check.conclusion -notin @("SUCCESS", "SKIPPED", "NEUTRAL")
      ) {
        $failingChecks += "$name [$($check.status)/$($check.conclusion)]"
      }
      continue
    }

    if ($check.__typename -eq "StatusContext" -and $check.state -ne "SUCCESS") {
      $failingChecks += "$name [$($check.state)]"
    }
  }

  if ($failingChecks.Count -gt 0) {
    throw "PR #$Number has non-successful status checks: $($failingChecks -join ', ')"
  }

  $repo = Get-RepositoryCoordinates
  $cursor = $null
  $unresolvedCount = 0
  $threadQuery = @'
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { isResolved }
      }
    }
  }
}
'@

  do {
    $ghArgs = @(
      "graphql",
      "-f", "query=$threadQuery",
      "-F", "owner=$($repo.owner)",
      "-F", "name=$($repo.name)",
      "-F", "number=$Number"
    )
    if ($cursor) {
      $ghArgs += @("-F", "after=$cursor")
    }

    $response = gh api @ghArgs | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $response) {
      throw "Unable to fetch review thread state for PR #$Number."
    }

    $threadPage = $response.data.repository.pullRequest.reviewThreads
    foreach ($node in @($threadPage.nodes)) {
      if (-not $node.isResolved) {
        $unresolvedCount++
      }
    }

    if ($threadPage.pageInfo.hasNextPage) {
      $cursor = $threadPage.pageInfo.endCursor
    } else {
      $cursor = $null
    }
  } while ($cursor)

  if ($unresolvedCount -gt 0) {
    throw "PR #$Number still has $unresolvedCount unresolved review thread(s)."
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
  "pre-merge" {
    Assert-GhCliAvailable
    $head = Get-CurrentHead
    Assert-PreMergeReady -Number $PullRequestNumber -ExpectedBranch $branch -ExpectedHead $head
    Write-Output "pre_merge=pass"
    exit 0
  }
}
