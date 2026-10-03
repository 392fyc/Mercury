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

$nativeReceiptSpecified = $PSBoundParameters.ContainsKey("NativeReviewReceipt")
if ($nativeReceiptSpecified -and $Action -ne "pre-merge") {
  throw "-NativeReviewReceipt is valid only with the pre-merge action."
}
if ($nativeReceiptSpecified -and [string]::IsNullOrWhiteSpace($NativeReviewReceipt)) {
  throw "-NativeReviewReceipt must name a local JSON receipt file."
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..\\..")).Path
# State dir unified with the retired .claude/hooks/pre-commit-guard.sh (which checked
# "$_PROJECT/.mercury/state/review-passed"). Previously this script wrote to
# .codex/state/review-passed, which the Bash hook never read — the Codex
# fallback commit flow was broken on every branch. Mercury Issue #357.
$stateDir = Join-Path $repoRoot ".mercury\\state"
$reviewFlag = Join-Path $stateDir "review-passed"
$protectedBranches = @("develop", "main", "master")
# Accept feature/TASK-* (legacy) and the lane branch forms used in practice
# under Mercury's multi-lane v1 protocol (.mercury/docs/guides/lane-protocol.md Rule 2.1):
#   - lane/<lane-name>/init               — scaffold branch (e.g. lane/side-bug/init)
#   - lane/<lane-name>/<n>                — issue-only suffix
#   - lane/<lane-name>/<n>-<slug>         — typical work branch (e.g. lane/side-bug/357-codex-hooks)
# The pattern is intentionally precise so the throw-message description
# below matches what is actually accepted (Argus iter 1 finding).
$featureTaskPattern = "^(feature/TASK-[A-Za-z0-9._-]+|lane/[A-Za-z0-9._-]+/(init|[0-9]+(-[A-Za-z0-9._-]+)?))$"

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
    throw "Protected branch '$Branch' is not allowed for Codex commits or pushes. Use feature/TASK-<id> or lane/<lane>/(init|<n>|<n>-<slug>)."
  }

  if ($Branch -notmatch $featureTaskPattern) {
    throw "Branch '$Branch' does not match feature/TASK-<id> or lane/<lane>/(init|<n>|<n>-<slug>). Move the work to a task or lane branch before mutating git state."
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

  # Strip leading + (force-push marker: +src:dst or +branch) before parsing
  $cleanToken = if ($Token.StartsWith("+")) { $Token.Substring(1) } else { $Token }

  $destination = $cleanToken
  if ($cleanToken.Contains(":")) {
    $destination = $cleanToken.Split(":", 2)[1]
  }

  if ([string]::IsNullOrWhiteSpace($destination)) {
    return $false
  }

  return $destination -match "^(refs/heads/)?(develop|main|master)$"
}

function Get-ConfiguredRemote {
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
    throw "PushCommand is required for pre-push validation. Invoke push via scripts/codex/git-safe.ps1 or pass the full git push command text."
  }

  if ($CommandText -match '(^|\s)--all(\s|$)' -or $CommandText -match '(^|\s)--mirror(\s|$)') {
    throw "Broad or destructive push flags are forbidden in Codex guard mode."
  }

  if ($CommandText -match '(^|\s)(--force(\S*)?|--delete|-d|-f)(\s|$)') {
    throw "Destructive push flags (--force/--force-with-lease/--delete) are forbidden in Codex guard mode."
  }

  $tokens = $CommandText -split '\s+'
  $knownTokens = @("git", "push") + (Get-ConfiguredRemote)
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

function Get-RemoteHost {
  $hosts = New-Object System.Collections.Generic.List[string]

  foreach ($remoteName in Get-ConfiguredRemote) {
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

  $candidateHosts = Get-RemoteHost
  $errors = @()
  foreach ($remoteHost in $candidateHosts) {
    $null = & gh auth status --active --hostname $remoteHost 2>$null
    if ($LASTEXITCODE -eq 0) {
      return
    }

    $errors += "${remoteHost}: run 'gh auth login' for this host and retry."
  }

  throw "GitHub CLI authentication check failed for hosts [$($candidateHosts -join ', ')]. $($errors -join ' | ')"
}

function Get-RepositoryCoordinate {
  $repoJson = & gh repo view --json owner,name 2>$null
  if ($LASTEXITCODE -ne 0) {
    throw "Unable to determine the current GitHub repository via 'gh repo view --json owner,name'."
  }

  try {
    $repo = $repoJson | ConvertFrom-Json -ErrorAction Stop
  } catch {
    throw "GitHub CLI returned invalid repository metadata."
  }
  if (-not $repo) {
    throw "Unable to determine the current GitHub repository via 'gh repo view --json owner,name'."
  }
  if ([string]::IsNullOrWhiteSpace([string]$repo.owner.login) -or [string]::IsNullOrWhiteSpace([string]$repo.name)) {
    throw "GitHub CLI returned incomplete repository metadata."
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

function Test-JsonProperty {
  param(
    [object]$InputObject,
    [string]$Name
  )

  return ($null -ne $InputObject -and $null -ne $InputObject.PSObject.Properties[$Name])
}

function Assert-GraphqlConnectionShape {
  param(
    [object]$Connection,
    [string]$Description,
    [int]$Number
  )

  if ($null -eq $Connection -or
      -not (Test-JsonProperty -InputObject $Connection -Name "nodes") -or
      $null -eq $Connection.nodes -or
      $Connection.nodes -isnot [array] -or
      -not (Test-JsonProperty -InputObject $Connection -Name "pageInfo") -or
      $null -eq $Connection.pageInfo -or
      -not (Test-JsonProperty -InputObject $Connection.pageInfo -Name "hasNextPage") -or
      $Connection.pageInfo.hasNextPage -isnot [bool] -or
      -not (Test-JsonProperty -InputObject $Connection.pageInfo -Name "endCursor")) {
    throw "GitHub returned incomplete $Description pagination for PR #$Number."
  }

  if ($Connection.pageInfo.hasNextPage -and
      [string]::IsNullOrWhiteSpace([string]$Connection.pageInfo.endCursor)) {
    throw "GitHub returned invalid $Description pagination for PR #$Number."
  }
}

function Assert-NativeReviewReceipt {
  param(
    [string]$Path,
    [string]$Repository,
    [int]$PullRequest,
    [string]$Head
  )

  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "Native review receipt file does not exist or is not a file."
  }

  try {
    $receipt = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
  } catch {
    throw "Native review receipt is not valid JSON."
  }
  if ($null -eq $receipt -or $receipt -isnot [pscustomobject]) {
    throw "Native review receipt must be a JSON object."
  }

  foreach ($field in @("repository", "pull_request", "head", "verdict", "reviewer", "agent_id", "reviewed_at", "findings")) {
    if (-not $receipt.PSObject.Properties[$field]) {
      throw "Native review receipt is missing required field '$field'."
    }
  }

  if ($receipt.repository -isnot [string] -or $receipt.repository -cne $Repository) {
    throw "Native review receipt repository does not match the current repository."
  }
  if (($receipt.pull_request -isnot [int]) -and ($receipt.pull_request -isnot [long])) {
    throw "Native review receipt pull_request must be an integer."
  }
  if ([long]$receipt.pull_request -ne $PullRequest) {
    throw "Native review receipt pull_request does not match the requested PR."
  }
  if ($receipt.head -isnot [string] -or $receipt.head -cne $Head) {
    throw "Native review receipt head does not match the current HEAD."
  }
  if ($receipt.verdict -isnot [string] -or $receipt.verdict -cne "pass") {
    throw "Native review receipt verdict must be 'pass'."
  }
  if ($receipt.reviewer -isnot [string] -or $receipt.reviewer -cne "native-subagent") {
    throw "Native review receipt reviewer must be 'native-subagent'."
  }
  if ($receipt.agent_id -isnot [string] -or [string]::IsNullOrWhiteSpace($receipt.agent_id)) {
    throw "Native review receipt agent_id must be non-empty."
  }
  if (($receipt.reviewed_at -isnot [string] -and
      $receipt.reviewed_at -isnot [DateTime] -and
      $receipt.reviewed_at -isnot [DateTimeOffset]) -or
      [string]::IsNullOrWhiteSpace([string]$receipt.reviewed_at)) {
    throw "Native review receipt reviewed_at must be non-empty."
  }
  if ($receipt.findings -isnot [array] -or $receipt.findings.Count -ne 0) {
    throw "Native review receipt findings must be empty."
  }
}

function Invoke-PreMergeGraphqlQuery {
  param(
    [string]$Query,
    [string]$Owner,
    [string]$Name,
    [object]$Number,
    [string]$After,
    [string]$Oid,
    [string]$Purpose
  )

  $graphqlArgs = @(
    "graphql",
    "-f", "query=$Query",
    "-F", "owner=$Owner",
    "-F", "name=$Name"
  )
  if ($null -ne $Number) {
    $graphqlArgs += @("-F", "number=$Number")
  }
  if (-not [string]::IsNullOrWhiteSpace($Oid)) {
    $graphqlArgs += @("-F", "oid=$Oid")
  }
  if (-not [string]::IsNullOrWhiteSpace($After)) {
    $graphqlArgs += @("-F", "after=$After")
  }

  $rawResponse = & gh api @graphqlArgs 2>$null
  if ($LASTEXITCODE -ne 0) {
    throw "Unable to fetch $Purpose for PR #$Number."
  }
  try {
    $response = ($rawResponse -join "`n") | ConvertFrom-Json -ErrorAction Stop
  } catch {
    throw "GitHub returned invalid $Purpose data for PR #$Number."
  }
  if ($null -eq $response -or
      -not (Test-JsonProperty -InputObject $response -Name "data") -or
      $null -eq $response.data -or
      -not (Test-JsonProperty -InputObject $response.data -Name "repository") -or
      $null -eq $response.data.repository) {
    throw "GitHub returned incomplete $Purpose data for PR #$Number."
  }
  if (Test-JsonProperty -InputObject $response -Name "errors") {
    if (@($response.errors).Count -gt 0) {
      throw "GitHub reported an error while fetching $Purpose for PR #$Number."
    }
  }
  if (-not (Test-JsonProperty -InputObject $response.data.repository -Name "pullRequest") -and
      $null -ne $Number) {
    throw "GitHub returned incomplete $Purpose data for PR #$Number."
  }
  if ($null -eq $Number -and
      -not (Test-JsonProperty -InputObject $response.data.repository -Name "object")) {
    throw "GitHub returned incomplete $Purpose data for the current commit."
  }
  return $response
}

function Assert-NativeChecksReady {
  param(
    [string]$Owner,
    [string]$Name,
    [string]$Head,
    [int]$Number
  )

  $checkQuery = @'
query($owner: String!, $name: String!, $oid: GitObjectID!, $after: String) {
  repository(owner: $owner, name: $name) {
    object(oid: $oid) {
      ... on Commit {
        statusCheckRollup {
          contexts(first: 100, after: $after) {
            pageInfo { hasNextPage endCursor }
            nodes {
              __typename
              ... on CheckRun { name status conclusion }
              ... on StatusContext { context state }
            }
          }
        }
      }
    }
  }
}
'@

  $cursor = $null
  $seenCursors = @{}
  $checkCount = 0
  $failures = @()
  do {
    $response = Invoke-PreMergeGraphqlQuery -Query $checkQuery -Owner $Owner -Name $Name -Number $null -After $cursor -Oid $Head -Purpose "CI check state"
    $commit = $response.data.repository.object
    if ($null -eq $commit -or -not (Test-JsonProperty -InputObject $commit -Name "statusCheckRollup") -or
        $null -eq $commit.statusCheckRollup -or
        -not (Test-JsonProperty -InputObject $commit.statusCheckRollup -Name "contexts")) {
      throw "PR #$Number has no complete CI check rollup; native review receipt cannot replace CI."
    }

    $connection = $commit.statusCheckRollup.contexts
    Assert-GraphqlConnectionShape -Connection $connection -Description "CI check" -Number $Number
    foreach ($check in @($connection.nodes)) {
      $checkCount++
      if (-not (Test-JsonProperty -InputObject $check -Name "__typename")) {
        $failures += "unknown-check [missing check type]"
        continue
      }
      if ($check.__typename -ceq "CheckRun") {
        if (-not (Test-JsonProperty -InputObject $check -Name "status") -or
            -not (Test-JsonProperty -InputObject $check -Name "conclusion") -or
            $check.status -cne "COMPLETED" -or $check.conclusion -cne "SUCCESS") {
          $failures += "$(Get-CheckLabel -Check $check) [$($check.status)/$($check.conclusion)]"
        }
        continue
      }
      if ($check.__typename -ceq "StatusContext") {
        if (-not (Test-JsonProperty -InputObject $check -Name "state") -or $check.state -cne "SUCCESS") {
          $failures += "$(Get-CheckLabel -Check $check) [$($check.state)]"
        }
        continue
      }
      $failures += "$(Get-CheckLabel -Check $check) [unknown check type $($check.__typename)]"
    }

    if ($connection.pageInfo.hasNextPage) {
      $nextCursor = [string]$connection.pageInfo.endCursor
      if ([string]::IsNullOrWhiteSpace($nextCursor) -or $seenCursors.ContainsKey($nextCursor)) {
        throw "GitHub returned invalid CI check pagination for PR #$Number."
      }
      $seenCursors[$nextCursor] = $true
      $cursor = $nextCursor
    } else {
      $cursor = $null
    }
  } while ($cursor)

  if ($checkCount -eq 0) {
    throw "PR #$Number has no CI checks; native review receipt cannot replace CI."
  }
  if ($failures.Count -gt 0) {
    throw "PR #$Number has non-successful status checks: $($failures -join ', ')"
  }
}

function Assert-NoActiveChangesRequested {
  param(
    [string]$Owner,
    [string]$Name,
    [int]$Number
  )

  $reviewQuery = @'
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviews(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { state submittedAt author { login } }
      }
    }
  }
}
'@
  $reviewCursor = $null
  $seenReviewCursors = @{}
  $latestSubstantiveReviews = @{}
  do {
    $reviewResponse = Invoke-PreMergeGraphqlQuery -Query $reviewQuery -Owner $Owner -Name $Name -Number $Number -After $reviewCursor -Purpose "review state"
    $pullRequest = $reviewResponse.data.repository.pullRequest
    if ($null -eq $pullRequest -or -not (Test-JsonProperty -InputObject $pullRequest -Name "reviews")) {
      throw "GitHub returned incomplete review state for PR #$Number."
    }
    $reviewPage = $pullRequest.reviews
    Assert-GraphqlConnectionShape -Connection $reviewPage -Description "review" -Number $Number
    foreach ($review in @($reviewPage.nodes)) {
      if (-not (Test-JsonProperty -InputObject $review -Name "state")) {
        throw "GitHub returned an incomplete review node for PR #$Number."
      }
      if ($review.state -cnotin @("APPROVED", "CHANGES_REQUESTED", "DISMISSED")) {
        continue
      }
      if (-not (Test-JsonProperty -InputObject $review -Name "author") -or $null -eq $review.author -or
          -not (Test-JsonProperty -InputObject $review.author -Name "login") -or
          [string]::IsNullOrWhiteSpace([string]$review.author.login) -or
          -not (Test-JsonProperty -InputObject $review -Name "submittedAt") -or
          [string]::IsNullOrWhiteSpace([string]$review.submittedAt)) {
        throw "GitHub returned incomplete substantive review data for PR #$Number."
      }
      $reviewer = [string]$review.author.login
      $submittedAt = [DateTimeOffset]::MinValue
      if (-not [DateTimeOffset]::TryParse([string]$review.submittedAt, [ref]$submittedAt)) {
        throw "GitHub returned an invalid review timestamp for PR #$Number."
      }
      if (-not $latestSubstantiveReviews.ContainsKey($reviewer) -or
          $submittedAt -gt $latestSubstantiveReviews[$reviewer].submittedAt -or
          ($submittedAt -eq $latestSubstantiveReviews[$reviewer].submittedAt -and
            $review.state -ceq "CHANGES_REQUESTED" -and
            $latestSubstantiveReviews[$reviewer].state -cne "CHANGES_REQUESTED")) {
        $latestSubstantiveReviews[$reviewer] = [pscustomobject]@{
          state = [string]$review.state
          submittedAt = $submittedAt
        }
      }
    }
    if ($reviewPage.pageInfo.hasNextPage) {
      $nextCursor = [string]$reviewPage.pageInfo.endCursor
      if ([string]::IsNullOrWhiteSpace($nextCursor) -or $seenReviewCursors.ContainsKey($nextCursor)) {
        throw "GitHub returned invalid review pagination for PR #$Number."
      }
      $seenReviewCursors[$nextCursor] = $true
      $reviewCursor = $nextCursor
    } else {
      $reviewCursor = $null
    }
  } while ($reviewCursor)

  $changesRequestedReviewers = @(
    foreach ($reviewer in $latestSubstantiveReviews.Keys) {
      if ($latestSubstantiveReviews[$reviewer].state -ceq "CHANGES_REQUESTED") {
        $reviewer
      }
    }
  )
  if ($changesRequestedReviewers.Count -gt 0) {
    throw "PR #$Number has active CHANGES_REQUESTED review(s) from: $($changesRequestedReviewers -join ', ')."
  }
}

function Assert-PreMergeReady {
  param(
    [int]$Number,
    [string]$ExpectedBranch,
    [string]$ExpectedHead,
    [bool]$UseNativeReceipt
  )

  if ($Number -le 0) {
    throw "PullRequestNumber must be a positive integer for pre-merge checks."
  }

  $repo = Get-RepositoryCoordinate
  $repoName = "$($repo.owner)/$($repo.name)"
  $prJson = & gh pr view $Number --repo $repoName --json number,state,isDraft,mergeable,reviewDecision,statusCheckRollup,headRefName,headRefOid,baseRefName 2>$null
  if ($LASTEXITCODE -ne 0 -or -not $prJson) {
    throw "Unable to fetch PR metadata for #$Number."
  }
  try {
    $pr = $prJson | ConvertFrom-Json -ErrorAction Stop
  } catch {
    throw "GitHub CLI returned invalid PR metadata for #$Number."
  }

  foreach ($field in @("number", "state", "isDraft", "mergeable", "reviewDecision", "headRefName", "headRefOid", "baseRefName")) {
    if (-not (Test-JsonProperty -InputObject $pr -Name $field)) {
      throw "GitHub returned incomplete PR metadata for #$Number."
    }
  }

  if ($pr.number -ne $Number) {
    throw "GitHub returned metadata for a different pull request."
  }

  if ($pr.headRefName -ne $ExpectedBranch) {
    throw "PR #$Number belongs to branch '$($pr.headRefName)', but current branch is '$ExpectedBranch'."
  }

  if ($pr.headRefOid -ne $ExpectedHead) {
    throw "PR #$Number is at '$($pr.headRefOid)', but current HEAD is '$ExpectedHead'. Push/fetch and retry."
  }
  if ($UseNativeReceipt -and $pr.baseRefName -cne "develop") {
    throw "PR #$Number targets '$($pr.baseRefName)'; Mercury pre-merge checks require the develop branch."
  }

  if ($pr.state -cne "OPEN") {
    throw "PR #$Number is not open."
  }
  if ($pr.isDraft -ne $false) {
    throw "PR #$Number is still a draft."
  }
  if ($pr.mergeable -cne "MERGEABLE") {
    throw "PR #$Number is not confirmed mergeable."
  }

  $decision = if ($null -eq $pr.reviewDecision) { "" } else { [string]$pr.reviewDecision }
  if ($decision -ceq "CHANGES_REQUESTED") {
    throw "PR #$Number has a GitHub CHANGES_REQUESTED decision."
  }
  if (-not $UseNativeReceipt -and $decision -cne "APPROVED") {
    throw "PR #$Number is not approved. Current reviewDecision: $decision"
  }
  if ($UseNativeReceipt -and $decision -cnotin @("", "APPROVED", "REVIEW_REQUIRED")) {
    throw "PR #$Number has an unsupported GitHub review decision: $decision"
  }

  if ($UseNativeReceipt) {
    Assert-NativeChecksReady -Owner $repo.owner -Name $repo.name -Head $ExpectedHead -Number $Number
  } else {
    $failingChecks = @()
    foreach ($check in @($pr.statusCheckRollup)) {
      $name = Get-CheckLabel -Check $check
      if ($check.__typename -eq "CheckRun") {
        if ($check.status -ne "COMPLETED" -or $check.conclusion -notin @("SUCCESS", "SKIPPED", "NEUTRAL")) {
          $failingChecks += "$name [$($check.status)/$($check.conclusion)]"
        }
        continue
      }

      if ($check.__typename -eq "StatusContext") {
        if ($check.state -ne "SUCCESS") {
          $failingChecks += "$name [$($check.state)]"
        }
      }
    }
    if ($failingChecks.Count -gt 0) {
      throw "PR #$Number has non-successful status checks: $($failingChecks -join ', ')"
    }
  }

  # Native receipt mode checks full review history by substantive state per reviewer.
  # Ordinary GitHub approval mode keeps its existing review-decision gate.
  if ($UseNativeReceipt) {
    Assert-NoActiveChangesRequested -Owner $repo.owner -Name $repo.name -Number $Number
  }

  $cursor = $null
  $seenThreadCursors = @{}
  $threadCount = 0
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
    $response = Invoke-PreMergeGraphqlQuery -Query $threadQuery -Owner $repo.owner -Name $repo.name -Number $Number -After $cursor -Purpose "review thread state"
    $threadPullRequest = $response.data.repository.pullRequest
    if ($null -eq $threadPullRequest -or -not (Test-JsonProperty -InputObject $threadPullRequest -Name "reviewThreads")) {
      throw "GitHub returned incomplete review thread state for PR #$Number."
    }
    $threadPage = $threadPullRequest.reviewThreads
    Assert-GraphqlConnectionShape -Connection $threadPage -Description "review thread" -Number $Number
    $threadCount += $threadPage.nodes.Count
    foreach ($node in @($threadPage.nodes)) {
      if (-not (Test-JsonProperty -InputObject $node -Name "isResolved") -or $node.isResolved -isnot [bool]) {
        throw "GitHub returned an incomplete review thread for PR #$Number."
      }
      if (-not $node.isResolved) {
        $unresolvedCount++
      }
    }

    if ($threadPage.pageInfo.hasNextPage) {
      $nextCursor = [string]$threadPage.pageInfo.endCursor
      if ([string]::IsNullOrWhiteSpace($nextCursor) -or $seenThreadCursors.ContainsKey($nextCursor)) {
        throw "GitHub returned invalid review thread pagination for PR #$Number."
      }
      $seenThreadCursors[$nextCursor] = $true
      $cursor = $nextCursor
    } else {
      $cursor = $null
    }
  } while ($cursor)

  if ($UseNativeReceipt -and $threadCount -gt 0) {
    throw "PR #$Number has $threadCount review thread(s); native review receipt requires zero threads."
  }
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
    if ($nativeReceiptSpecified) {
      $repo = Get-RepositoryCoordinate
      Assert-NativeReviewReceipt -Path $NativeReviewReceipt -Repository "$($repo.owner)/$($repo.name)" -PullRequest $PullRequestNumber -Head $head
    }
    Push-Location $repoRoot
    try {
      Assert-PreMergeReady -Number $PullRequestNumber -ExpectedBranch $branch -ExpectedHead $head -UseNativeReceipt:$nativeReceiptSpecified
    } finally {
      Pop-Location
    }
    if ($nativeReceiptSpecified) {
      Write-Output "native_review_receipt=validated"
    }
    Write-Output "pre_merge=pass"
    exit 0
  }
}
