[CmdletBinding()]
param(
  [Parameter(Mandatory = $true, Position = 0)]
  [ValidateSet("add", "commit", "push", "sync", "abort-sync")]
  [string]$Action,

  [string]$Message,

  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$Arguments
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..\\..")).Path
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
    throw "Provide at least one explicit path. Example: powershell -ExecutionPolicy Bypass -File scripts/codex/git-safe.ps1 add path/to/file"
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

function Assert-ReadyToSync {
  # Every state that a merge would mix into, or that a merge would leave half done, is refused before anything changes.
  foreach ($name in @("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "REBASE_HEAD", "rebase-merge", "rebase-apply", "BISECT_LOG")) {
    if (Test-GitPath -Name $name) {
      throw "Another Git operation is in progress ($name). Finish or abandon it before git-safe sync."
    }
  }
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
      throw "Provide explicit push arguments. Example: powershell -ExecutionPolicy Bypass -File scripts/codex/git-safe.ps1 push origin feature/TASK-123"
    }

    $pushCommand = Build-PushCommandText -PushArgs $Arguments
    Invoke-Guard -GuardAction "pre-push" -PushCommandText $pushCommand
    $gitArgs = @("-C", $repoRoot, "push") + $Arguments
    Invoke-Git -GitArgs $gitArgs
    exit 0
  }
  "sync" {
    # Merge the latest trunk into the current task branch (#716). The base is always origin/master, the single trunk;
    # the merge is never committed, pushed or replayed here: the result is reviewed and concluded with git-safe commit.
    if (($Arguments -and $Arguments.Count -gt 0) -or -not [string]::IsNullOrEmpty($Message)) {
      throw "git-safe sync takes no arguments: it always merges origin/master into the current task branch."
    }
    Invoke-Guard -GuardAction "pre-stage"
    Assert-ReadyToSync
    Invoke-Git -GitArgs @("-C", $repoRoot, "fetch", "--no-tags", "origin", "+refs/heads/master:refs/remotes/origin/master")
    & git -C $repoRoot merge-base --is-ancestor refs/remotes/origin/master HEAD
    if ($LASTEXITCODE -eq 0) {
      Write-Output "sync=up-to-date"
      exit 0
    }
    & git -C $repoRoot merge --no-ff --no-commit refs/remotes/origin/master
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
      throw "git merge of origin/master failed (exit $mergeExit)."
    }
    Write-Output "sync=merged"
    Write-Output "The merge is staged but not committed: review it, run guard.ps1 mark-review, then git-safe commit."
    exit 0
  }
  "abort-sync" {
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
      & git -C $repoRoot merge-base --is-ancestor $mergeHeads[0].Trim() refs/remotes/origin/master
      $fromTrunk = ($LASTEXITCODE -eq 0)
    }
    if (-not $fromTrunk) {
      throw "The merge in progress is not a git-safe sync of origin/master; abandon it by hand or ask the user."
    }
    Invoke-Git -GitArgs @("-C", $repoRoot, "merge", "--abort")
    Write-Output "sync=aborted"
    Write-Output "head=$((git -C $repoRoot rev-parse HEAD).Trim())"
    exit 0
  }
}
