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

foreach ($inherited in @("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE")) {
  # An inherited override would point every git call below at another repository than the one holding this script.
  if ([Environment]::GetEnvironmentVariable($inherited)) {
    throw "Unset the inherited $inherited before using this script."
  }
}
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
# The repository is the Git work tree that holds this script, never the caller's current directory: an agent in
# another checkout that calls this copy acts on this copy's work tree (installed under .mercury/packs/git-safe/scripts/
# or, in Mercury itself, under packs/git-safe/scripts/). Issue #723.
$repoRoot = & git -C $scriptDir rev-parse --show-toplevel 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace("$repoRoot")) {
  throw "This script must live inside the Git work tree it guards."
}
$repoRoot = (Resolve-Path -LiteralPath ("$repoRoot".Trim())).Path

function Get-IntegrationBranch {
  # The project's trunk: params.integration_branch in .mercury/config.json, else master (Mercury's single trunk).
  $configPath = Join-Path $repoRoot ".mercury\\config.json"
  $branch = "master"
  if (Test-Path -LiteralPath $configPath) {
    try {
      $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    } catch {
      throw "Unable to read .mercury/config.json."
    }
    if ($config.PSObject.Properties.Name -contains "params" -and $null -ne $config.params -and
        $config.params.PSObject.Properties.Name -contains "integration_branch") {
      $branch = [string]$config.params.integration_branch
    }
  }
  if ($branch -notmatch '^[A-Za-z0-9][A-Za-z0-9._/-]*$' -or $branch -match '\.\.' -or $branch.EndsWith('/')) {
    throw "params.integration_branch '$branch' in .mercury/config.json is not a plain branch name."
  }
  return $branch
}
$guardScript = Join-Path $scriptDir "guard.ps1"
$powerShellHost = [System.Diagnostics.Process]::GetCurrentProcess().MainModule.FileName

function Invoke-Guard {
  param(
    [string]$GuardAction,
    [string]$PushCommandText
  )

  $cmd = @("-ExecutionPolicy", "Bypass", "-File", $guardScript, $GuardAction)
  if ($GuardAction -eq "pre-push" -and -not [string]::IsNullOrWhiteSpace($PushCommandText)) {
    $cmd += @("-PushCommand", $PushCommandText)
  }

  & $powerShellHost @cmd
  if ($LASTEXITCODE -ne 0) {
    throw "Guard action '$GuardAction' failed."
  }
}

function Invoke-Git {
  param([string[]]$GitArgs)

  & git @GitArgs
  if ($LASTEXITCODE -ne 0) {
    throw "git command failed: git $($GitArgs -join ' ')"
  }
}

function Test-RegisteredSubmodule {
  param(
    [string]$Path,
    [System.IO.FileSystemInfo]$Item
  )

  # A directory may be staged only as a gitlink: its path must be a submodule `path` in the working-tree .gitmodules
  # (exact spelling) and it must be the root of its own Git work tree, not a link and not just a folder of this repository.
  $reparse = [System.IO.FileAttributes]::ReparsePoint
  if ($Item.Attributes -band $reparse) {
    return $false
  }

  $modulesFile = Join-Path $repoRoot ".gitmodules"
  if (-not (Test-Path -LiteralPath $modulesFile -PathType Leaf)) {
    return $false
  }
  # -Force: on Linux a dot file counts as hidden and Get-Item would not find it.
  if ((Get-Item -Force -LiteralPath $modulesFile).Attributes -band $reparse) {
    return $false
  }

  $registered = @()
  $lines = & git config --file $modulesFile --get-regexp '^submodule\..*\.path$'
  if ($LASTEXITCODE -eq 0) {
    foreach ($line in @($lines)) {
      if ($line -match '^submodule\..+\.path (?<value>.+)$') {
        $registered += $Matches["value"]
      }
    }
  }
  $normalized = $Path.Replace("\", "/")
  if (-not ($registered -ccontains $normalized)) {
    return $false
  }

  try {
    $top = & git -C $Item.FullName rev-parse --show-toplevel 2>&1
    if ($LASTEXITCODE -ne 0) {
      return $false
    }
  } catch {
    return $false
  }
  $topPath = [System.IO.Path]::GetFullPath(([string]$top).Trim()).TrimEnd("\", "/")
  $itemPath = [System.IO.Path]::GetFullPath($Item.FullName).TrimEnd("\", "/")
  if ([System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT) {
    return ($topPath -ieq $itemPath)
  }
  return ($topPath -ceq $itemPath)
}

function Assert-ExplicitPaths {
  param([string[]]$Paths)

  if (-not $Paths -or $Paths.Count -eq 0) {
    throw "Provide at least one explicit path. Example: powershell -ExecutionPolicy Bypass -File $PSCommandPath add path/to/file"
  }

  foreach ($path in $Paths) {
    if ([string]::IsNullOrWhiteSpace($path)) {
      throw "Empty path arguments are not allowed."
    }

    if ($path -in @(".", "-A", "--all")) {
      throw "Broad staging arguments are forbidden: $path"
    }

    if ($path.StartsWith(":")) {
      throw "Pathspec magic is forbidden: $path"
    }

    if ($path -match '[*?\[\]{}]') {
      throw "Wildcard/pathspec patterns are forbidden: $path"
    }

    if ($path.EndsWith("\\") -or $path.EndsWith("/")) {
      throw "Directories are not allowed in git-safe add. Pass explicit files only: $path"
    }

    $candidate = Join-Path $repoRoot $path
    if (Test-Path -LiteralPath $candidate) {
      $item = Get-Item -Force -LiteralPath $candidate
      if ($item.PSIsContainer -and -not (Test-RegisteredSubmodule -Path $path -Item $item)) {
        throw "Directories are not allowed in git-safe add. Pass explicit files only (a registered submodule path from .gitmodules is the one exception): $path"
      }
    }

    if ($path.StartsWith("-")) {
      throw "Options are not allowed in git-safe add. Pass explicit repo paths only: $path"
    }
  }
}

function Assert-CommitMessage {
  param([string]$CommitMessage)

  if ([string]::IsNullOrWhiteSpace($CommitMessage)) {
    throw "Commit message is required. Use -Message `"type(TASK-id): summary`"."
  }
}

function Assert-HasStagedChanges {
  $staged = git -C $repoRoot diff --cached --name-only
  if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect staged changes."
  }

  # A merge left by `sync` may bring nothing the branch lacks, and still has to be concluded.
  if ([string]::IsNullOrWhiteSpace(($staged -join "")) -and -not (Test-GitPath -Name "MERGE_HEAD")) {
    throw "No staged changes found. Stage explicit files with git-safe add first."
  }
}

function Test-GitPath {
  param([string]$Name)

  $path = git -C $repoRoot rev-parse --git-path $Name
  if ($LASTEXITCODE -ne 0) {
    throw "Unable to locate $Name in the Git directory."
  }
  if (-not [System.IO.Path]::IsPathRooted($path)) {
    $path = Join-Path $repoRoot $path
  }
  return (Test-Path -LiteralPath $path)
}

function Assert-NoOperationInProgress {
  param([string]$Command)

  foreach ($name in @("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "REBASE_HEAD", "rebase-merge", "rebase-apply", "BISECT_LOG")) {
    if (Test-GitPath -Name $name) {
      throw "Another Git operation is in progress ($name). Finish or abandon it before git-safe $Command."
    }
  }
}

function Assert-ReadyToSync {
  # Every state that a merge would mix into, or that a merge would leave half done, is refused before anything changes.
  Assert-NoOperationInProgress -Command "sync"
  $changes = git -C $repoRoot status --porcelain --untracked-files=no
  if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect the working tree."
  }
  if (-not [string]::IsNullOrWhiteSpace(($changes -join ""))) {
    throw "The branch has staged or unstaged changes. Commit them through git-safe, or set them aside, before git-safe sync."
  }
}

function Build-PushCommandText {
  param([string[]]$PushArgs)
  return ("git push " + ($PushArgs -join " ")).Trim()
}

switch ($Action) {
  "add" {
    Invoke-Guard -GuardAction "pre-stage"
    Assert-ExplicitPaths -Paths $Arguments
    $gitArgs = @("-C", $repoRoot, "add", "--") + $Arguments
    Invoke-Git -GitArgs $gitArgs
    exit 0
  }
  "commit" {
    Assert-CommitMessage -CommitMessage $Message
    Invoke-Guard -GuardAction "pre-commit"
    Assert-HasStagedChanges
    $gitArgs = @("-C", $repoRoot, "commit", "-m", $Message)
    Invoke-Git -GitArgs $gitArgs
    try {
      Invoke-Guard -GuardAction "clear-review"
    } catch {
      Write-Warning "Commit succeeded, but review flag cleanup failed: $($_.Exception.Message)"
    }
    exit 0
  }
  "push" {
    if (-not $Arguments -or $Arguments.Count -eq 0) {
      throw "Provide explicit push arguments. Example: powershell -ExecutionPolicy Bypass -File $PSCommandPath push origin feature/TASK-123"
    }

    $pushCommand = Build-PushCommandText -PushArgs $Arguments
    Invoke-Guard -GuardAction "pre-push" -PushCommandText $pushCommand
    $gitArgs = @("-C", $repoRoot, "push") + $Arguments
    Invoke-Git -GitArgs $gitArgs
    exit 0
  }
  "sync" {
    $trunk = Get-IntegrationBranch
    $trunkRef = "refs/remotes/origin/$trunk"
    # Merge the latest trunk into the current task branch (#716). The base is always origin/<integration branch> (params.integration_branch, master in Mercury);
    # the merge is never committed, pushed or replayed here: the result is reviewed and concluded with git-safe commit.
    if (($Arguments -and $Arguments.Count -gt 0) -or -not [string]::IsNullOrEmpty($Message)) {
      throw "git-safe sync takes no arguments: it always merges origin/$trunk into the current task branch."
    }
    Invoke-Guard -GuardAction "pre-stage"
    Assert-ReadyToSync
    Invoke-Git -GitArgs @("-C", $repoRoot, "fetch", "--no-tags", "origin", "+refs/heads/${trunk}:$trunkRef")
    & git -C $repoRoot merge-base --is-ancestor $trunkRef HEAD
    if ($LASTEXITCODE -eq 0) {
      Write-Output "sync=up-to-date"
      exit 0
    }
    & git -C $repoRoot merge --no-ff --no-commit $trunkRef
    $mergeExit = $LASTEXITCODE
    $unmerged = @(git -C $repoRoot diff --name-only --diff-filter=U)
    if ($unmerged.Count -gt 0) {
      Write-Output "sync=conflicts"
      $unmerged | ForEach-Object { Write-Output "conflict: $_" }
      Write-Output "Resolve each file keeping the intent of both sides, stage it with git-safe add, review the result, run guard.ps1 mark-review, then git-safe commit."
      Write-Output "If the conflicts cannot be resolved, git-safe abort-sync abandons the merge and restores the branch (everything staged since the merge began is discarded)."
      exit 1
    }
    if ($mergeExit -ne 0 -or -not (Test-GitPath -Name "MERGE_HEAD")) {
      throw "git merge of origin/$trunk failed (exit $mergeExit)."
    }
    Write-Output "sync=merged"
    Write-Output "The merge is staged but not committed: review it, run guard.ps1 mark-review, then git-safe commit."
    exit 0
  }
  "abort-sync" {
    $trunk = Get-IntegrationBranch
    $trunkRef = "refs/remotes/origin/$trunk"
    # Abandon a merge that sync left unconcluded (#719); raw `git merge --abort` stays forbidden. Only a merge of trunk
    # commits is abandoned, so this never discards some other operation.
    if (($Arguments -and $Arguments.Count -gt 0) -or -not [string]::IsNullOrEmpty($Message)) {
      throw "git-safe abort-sync takes no arguments: it abandons the merge that git-safe sync started."
    }
    Invoke-Guard -GuardAction "pre-stage"
    if (-not (Test-GitPath -Name "MERGE_HEAD")) {
      throw "No merge is in progress; there is nothing for git-safe abort-sync to abandon."
    }
    $mergeHeadPath = git -C $repoRoot rev-parse --git-path MERGE_HEAD
    if (-not [System.IO.Path]::IsPathRooted($mergeHeadPath)) {
      $mergeHeadPath = Join-Path $repoRoot $mergeHeadPath
    }
    $mergeHeads = @(Get-Content -LiteralPath $mergeHeadPath | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    $fromTrunk = $false
    if ($mergeHeads.Count -eq 1) {
      & git -C $repoRoot merge-base --is-ancestor $mergeHeads[0].Trim() $trunkRef
      $fromTrunk = ($LASTEXITCODE -eq 0)
    }
    if (-not $fromTrunk) {
      throw "The merge in progress is not a git-safe sync of origin/$trunk; abandon it by hand or ask the user."
    }
    Invoke-Git -GitArgs @("-C", $repoRoot, "merge", "--abort")
    Write-Output "sync=aborted"
    Write-Output "head=$((git -C $repoRoot rev-parse HEAD).Trim())"
    exit 0
  }
  "fast-forward" {
    $trunk = Get-IntegrationBranch
    $trunkRef = "refs/remotes/origin/$trunk"
    # Bring the main checkout, the human reference that agents never work in, up to origin/<integration branch> (#741).
    # Only a fast-forward: no merge commit, no local commit dropped, no local change overwritten. It runs no guard
    # because the main checkout sits on the protected trunk by design; it refuses on any other branch, so a task
    # worktree is never touched.
    if (($Arguments -and $Arguments.Count -gt 0) -or -not [string]::IsNullOrEmpty($Message)) {
      throw "git-safe fast-forward takes no arguments: it always fast-forwards $trunk to origin/$trunk."
    }
    $branch = git -C $repoRoot symbolic-ref --quiet --short HEAD
    if ($LASTEXITCODE -ne 0 -or "$branch".Trim() -cne $trunk) {
      throw "git-safe fast-forward acts only on the integration branch '$trunk'; this checkout is on '$("$branch".Trim())'."
    }
    # Only the main checkout: a linked worktree that happens to sit on the trunk is someone's working copy.
    $gitDir = [System.IO.Path]::GetFullPath("$(git -C $repoRoot rev-parse --absolute-git-dir)".Trim())
    $commonDir = [System.IO.Path]::GetFullPath("$(git -C $repoRoot rev-parse --path-format=absolute --git-common-dir)".Trim())
    if ($gitDir.TrimEnd("\", "/") -ne $commonDir.TrimEnd("\", "/")) {
      throw "git-safe fast-forward acts only on the main checkout, not on a linked worktree."
    }
    Assert-NoOperationInProgress -Command "fast-forward"
    Invoke-Git -GitArgs @("-C", $repoRoot, "fetch", "--no-tags", "--no-recurse-submodules", "origin", "+refs/heads/${trunk}:$trunkRef")
    $before = (git -C $repoRoot rev-parse HEAD).Trim()
    $target = (git -C $repoRoot rev-parse $trunkRef).Trim()
    if ($before -eq $target) {
      Write-Output "fast-forward=up-to-date"
      Write-Output "head=$before"
      exit 0
    }
    & git -C $repoRoot merge-base --is-ancestor HEAD $trunkRef
    if ($LASTEXITCODE -ne 0) {
      throw "$trunk has commits that origin/$trunk lacks; a fast-forward is impossible. HEAD was not moved (origin/$trunk was refreshed)."
    }
    # --no-autostash: with merge.autoStash set, Git would stash local changes, move HEAD and possibly leave them stashed.
    & git -C $repoRoot merge --ff-only --no-autostash --quiet $trunkRef
    if ($LASTEXITCODE -ne 0 -or (git -C $repoRoot rev-parse HEAD).Trim() -ne $target) {
      throw "The fast-forward was refused, usually because it would overwrite local changes. HEAD was not moved; check git status."
    }
    Write-Output "fast-forward=updated"
    Write-Output "from=$before"
    Write-Output "head=$target"
    # Initialized submodules keep their checkout; report those whose recorded commit now differs. Reporting only: a
    # failure here must not turn a completed fast-forward into a failed run.
    try {
      foreach ($line in @(git -C $repoRoot submodule status 2>&1)) {
        if ("$line" -match '^\+[0-9a-f]+ (.+?)( \(.*\))?$') {
          Write-Output "submodule-differs: $($Matches[1])"
        }
      }
    } catch {
      Write-Output "submodule-status: unavailable ($($_.Exception.Message))"
    }
    exit 0
  }
}
