[CmdletBinding()]
param(
  [Parameter(Mandatory = $true, Position = 0)]
  [ValidateSet("add", "commit", "push", "sync", "pull", "merge", "abort-sync", "fast-forward")]
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
function Invoke-Utf8Native {
  # git and gh write UTF-8, but PowerShell decodes an external command's output with the console code page. Under GBK
  # that garbles non-ASCII text such as Chinese paths and check names, and can swallow a closing quote so JSON stops
  # parsing. Read the output as UTF-8 and put the console back. Without a console the assignment can throw; then keep
  # the old behaviour rather than fail.
  param([string]$Command, [string[]]$NativeArgs, [switch]$WithStderr)

  $previousEncoding = [Console]::OutputEncoding
  $switched = $false
  try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $switched = $true
  } catch {
    $switched = $false
  }
  try {
    if ($WithStderr) {
      # Windows PowerShell 5.1 turns each stderr line merged by 2>&1 into an error record, which "Stop" would throw
      # on; keep going here and let the caller read $LASTEXITCODE.
      $ErrorActionPreference = "Continue"
      & $Command @NativeArgs 2>&1 | ForEach-Object { "$_" }
    } else {
      & $Command @NativeArgs 2>$null
    }
  } finally {
    if ($switched) { [Console]::OutputEncoding = $previousEncoding }
  }
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
# The repository is the Git work tree that holds this script, never the caller's current directory: an agent in
# another checkout that calls this copy acts on this copy's work tree (installed under .mercury/packs/git-safe/scripts/
# or, in Mercury itself, under packs/git-safe/scripts/). Issue #723.
$repoRoot = Invoke-Utf8Native "git" @("-C", $scriptDir, "rev-parse", "--show-toplevel")
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

function Assert-PushTargets {
  # The guard reads only the words of the command; what a push really updates also depends on push.default,
  # remote.<name>.push and refspec patterns (#772). Ask Git: a dry run lists every remote ref the push would update,
  # one "<flag>\t<from>:<to>\t<summary>" line each. Refuse a protected target, a forced update (+) and a deletion (-).
  # The dry run drops -u/--set-upstream so it changes no configuration, -q/--quiet because a quiet push prints no
  # status lines, and runs no pre-push hook (--no-verify): the real push runs it once.
  param([string[]]$PushArgs)

  # Git parses options left to right and the last one wins, so an argument could switch the dry run back into a real
  # push (--no-dry-run or any abbreviation of it), and after "--" nothing is an option any more. Refuse both.
  foreach ($arg in $PushArgs) {
    if ($arg -ceq "--" -or $arg -match '^--no-d') {
      throw "Push argument '$arg' is not allowed: it could turn the target check into a real push."
    }
  }
  $dryArgs = @()
  # Options that take the next argument as their value: the value is copied as it is, never read as an option.
  $takesValue = @("-o", "--push-option", "--repo", "--receive-pack", "--exec")
  $copyNext = $false
  foreach ($arg in $PushArgs) {
    if ($copyNext) { $dryArgs += $arg; $copyNext = $false; continue }
    if ($takesValue -ccontains $arg) { $dryArgs += $arg; $copyNext = $true; continue }
    if (@("-u", "--set-upstream", "-q", "--quiet") -ccontains $arg) { continue }
    if ($arg -cmatch '^-[A-Za-np-z]{2,}$') {  # not -o<value>, whose value must stay as it is
      # Combined short options such as -qu: drop q and u, keep the rest.
      $kept = ($arg.Substring(1).ToCharArray() | Where-Object { $_ -cne [char]'q' -and $_ -cne [char]'u' }) -join ""
      if ($kept) { $dryArgs += "-$kept" }
      continue
    }
    $dryArgs += $arg
  }
  if ($copyNext) {
    # The last argument is an option that needs a value. Git would read the --no-verify added below as that value and
    # the dry run would run the pre-push hook; the real push fails on the missing value anyway, so say it now (#775).
    throw "Push option '$($PushArgs[-1])' needs a value; nothing was pushed."
  }
  # --no-verify comes after the caller's arguments so that a --verify among them cannot run the hook a second time;
  # with "--" refused above, everything after the arguments is still read as an option.
  $output = @(Invoke-Utf8Native "git" (@("-C", $repoRoot, "push", "--dry-run", "--porcelain") + $dryArgs + @("--no-verify")) -WithStderr)
  $dryExit = $LASTEXITCODE
  $protected = @(@("develop", "main", "master") + @(Get-IntegrationBranch) | Select-Object -Unique)
  $updates = 0
  foreach ($line in $output) {
    $fields = "$line" -split "`t"
    if ($fields.Count -lt 3 -or $fields[0].Length -ne 1 -or -not $fields[1].Contains(":")) {
      continue
    }
    $flag = $fields[0]
    $target = $fields[1].Split(":", 2)[1]
    $branch = $target -replace "^refs/heads/", ""
    if ($protected -contains $branch) {
      throw "Push would update the protected branch '$branch': git push $($PushArgs -join ' ')"
    }
    if ($flag -eq "+") {
      throw "Push would force-update '$target'; forced updates are forbidden."
    }
    if ($flag -eq "-") {
      throw "Push would delete '$target'; deletions are forbidden."
    }
    $updates++
  }
  if ($dryExit -ne 0) {
    $reason = ($output | Where-Object { "$_" -notmatch "`t" } | ForEach-Object { "$_".Trim() } | Where-Object { $_ }) -join " | "
    throw "git push --dry-run failed (exit $dryExit); nothing was pushed. Git said: $reason"
  }
  if ($updates -eq 0) {
    throw "git push --dry-run named no remote ref to update; nothing was pushed."
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
  try {
    $lines = Invoke-Utf8Native "git" @("config", "--file", $modulesFile, "--get-regexp", '^submodule\..*\.path$')
  } catch {
    return $false  # a .gitmodules Git cannot parse registers nothing
  }
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
    $top = Invoke-Utf8Native "git" @("-C", $Item.FullName, "rev-parse", "--show-toplevel")
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

function Get-GitPath {
  param([string]$Name)

  $path = Invoke-Utf8Native "git" @("-C", $repoRoot, "rev-parse", "--git-path", $Name)
  if ($LASTEXITCODE -ne 0) {
    throw "Unable to locate $Name in the Git directory."
  }
  if (-not [System.IO.Path]::IsPathRooted($path)) {
    $path = Join-Path $repoRoot $path
  }
  return $path
}

function Test-GitPath {
  param([string]$Name)

  return (Test-Path -LiteralPath (Get-GitPath -Name $Name))
}

# The merge that sync, pull or merge left uncommitted is remembered by its MERGE_HEAD, so that abort-sync abandons only
# a merge this script started. A stale record is harmless: it is compared with the merge actually in progress.
$mergeRecordName = "mercury-git-safe-merge"

function Get-MergeHeads {
  if (-not (Test-GitPath -Name "MERGE_HEAD")) { return @() }
  return @(Get-Content -LiteralPath (Get-GitPath -Name "MERGE_HEAD") | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | ForEach-Object { $_.Trim() })
}

function Save-MergeRecord {
  $heads = @(Get-MergeHeads)
  if ($heads.Count -eq 1) {
    [System.IO.File]::WriteAllText((Get-GitPath -Name $mergeRecordName), $heads[0])
  }
}

function Test-RemoteBranch {
  # True when origin has the branch, false when it has not; throws when origin cannot be read. Read through
  # Invoke-Utf8Native -WithStderr: under Windows PowerShell 5.1 a redirected stderr line would otherwise stop the script.
  param([string]$Name)

  $null = Invoke-Utf8Native "git" @("-C", $repoRoot, "ls-remote", "--exit-code", "--heads", "origin", "refs/heads/$Name") -WithStderr
  if ($LASTEXITCODE -eq 0) { return $true }
  if ($LASTEXITCODE -eq 2) { return $false }
  throw "Unable to read origin (git ls-remote exit $LASTEXITCODE). Nothing was changed."
}

function Get-NulSeparated {
  # The entries of a `-z` listing: names arrive unquoted, whatever characters they hold.
  param([string[]]$GitArgs)

  $raw = (@(Invoke-Utf8Native "git" $GitArgs) -join "`n")
  if ($LASTEXITCODE -ne 0) {
    throw "git command failed: git $($GitArgs -join ' ')"
  }
  return @($raw.Split([char]0) | Where-Object { $_ -ne "" })
}

function Assert-NoLocalFileInTheWay {
  # Git refuses a merge that would overwrite an untracked file, except an ignored one: a true merge replaces it
  # silently, and --no-overwrite-ignore only covers fast-forwards. So before a merge, the paths the other side has and
  # HEAD lacks are compared with the files Git itself lists as untracked, ignored ones included. Asking Git, not the
  # disk, keeps a tracked file out of it: on a case-insensitive file system a rename that only changes case would
  # otherwise look like a file in the way.
  param([string]$Ref, [string]$Label)

  $added = @(Get-NulSeparated -GitArgs @("-C", $repoRoot, "diff", "-z", "--name-only", "--no-renames", "--diff-filter=A", "HEAD", $Ref))
  if ($added.Count -eq 0) { return }
  # Only the top-level entries the other side adds under need listing; that keeps large ignored trees elsewhere out.
  # Exact, case-sensitive de-duplication: `Sort-Object -Unique` treats `Build` and `build` as one name, and where
  # they are two folders the dropped one would go unlisted.
  $topSet = New-Object "System.Collections.Generic.HashSet[string]" ([System.StringComparer]::Ordinal)
  foreach ($path in $added) { [void]$topSet.Add($path.Split("/")[0]) }
  $tops = @($topSet)
  $local = @(Get-NulSeparated -GitArgs (@("--literal-pathspecs", "-C", $repoRoot, "ls-files", "-z", "--others", "--") + $tops))
  if ($local.Count -eq 0) { return }
  $comparer = if ([System.IO.Path]::DirectorySeparatorChar -eq "\") { [System.StringComparer]::OrdinalIgnoreCase } else { [System.StringComparer]::Ordinal }
  $files = New-Object "System.Collections.Generic.HashSet[string]" ($comparer)
  $folders = New-Object "System.Collections.Generic.HashSet[string]" ($comparer)
  foreach ($path in $local) {
    [void]$files.Add($path)
    $cut = $path.LastIndexOf("/")
    while ($cut -gt 0) {
      if (-not $folders.Add($path.Substring(0, $cut))) { break }
      $cut = $path.LastIndexOf("/", $cut - 1)
    }
  }
  $inTheWay = New-Object "System.Collections.Generic.List[string]"
  foreach ($path in $added) {
    # The same path, a local file where the other side needs a folder, or a local folder where it needs a file.
    $hit = $files.Contains($path) -or $folders.Contains($path)
    $cut = $path.LastIndexOf("/")
    while (-not $hit -and $cut -gt 0) {
      $hit = $files.Contains($path.Substring(0, $cut))
      $cut = $path.LastIndexOf("/", $cut - 1)
    }
    if ($hit) { $inTheWay.Add($path) }
  }
  if ($inTheWay.Count -gt 0) {
    $shown = (@($inTheWay) | Select-Object -First 10) -join ", "
    throw "Merging $Label would overwrite local files that are not tracked here: $shown. Move them aside first. Nothing was changed."
  }
}

function Complete-GuardedMerge {
  # Merge $Ref into the current branch without committing, and report under the action's name.
  param([string]$Ref, [string]$Label, [string]$Verb)

  Assert-NoLocalFileInTheWay -Ref $Ref -Label $Label
  & git -C $repoRoot merge --no-ff --no-commit --no-autostash --no-overwrite-ignore $Ref
  $mergeExit = $LASTEXITCODE
  Save-MergeRecord
  $unmerged = @(git -C $repoRoot diff --name-only --diff-filter=U)
  if ($unmerged.Count -gt 0) {
    Write-Output "$Verb=conflicts"
    $unmerged | ForEach-Object { Write-Output "conflict: $_" }
    Write-Output "Resolve each file keeping the intent of both sides, stage it with git-safe add, review the result, run guard.ps1 mark-review, then git-safe commit."
    Write-Output "If the conflicts cannot be resolved, git-safe abort-sync abandons the merge and restores the branch (everything staged since the merge began is discarded)."
    exit 1
  }
  if ($mergeExit -ne 0 -or -not (Test-GitPath -Name "MERGE_HEAD")) {
    throw "git merge of $Label failed (exit $mergeExit)."
  }
  Write-Output "$Verb=merged"
  Write-Output "The merge is staged but not committed: review it, run guard.ps1 mark-review, then git-safe commit."
  exit 0
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
  param([string]$Command = "sync")
  Assert-NoOperationInProgress -Command $Command
  $changes = git -C $repoRoot status --porcelain --untracked-files=no
  if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect the working tree."
  }
  if (-not [string]::IsNullOrWhiteSpace(($changes -join ""))) {
    throw "The branch has staged or unstaged changes. Commit them through git-safe, or set them aside, before git-safe $Command."
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
      if (-not (Test-GitPath -Name "MERGE_HEAD")) {
        # The merge this script started, if any, is concluded: its record must not outlive it.
        Remove-Item -LiteralPath (Get-GitPath -Name $mergeRecordName) -ErrorAction SilentlyContinue
      }
    } catch {
      # Housekeeping only: a commit that succeeded is never reported as failed over it.
    }
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
    Assert-PushTargets -PushArgs $Arguments
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
    Complete-GuardedMerge -Ref $trunkRef -Label "origin/$trunk" -Verb "sync"
  }
  "pull" {
    # Bring the current task branch up to origin/<the same branch> (#783): commits that reached the remote branch
    # without passing through this worktree, such as a helper pull request merged into it or a second session's push.
    # A fast-forward when this branch has no commits of its own; otherwise a merge left uncommitted, concluded like sync.
    if (($Arguments -and $Arguments.Count -gt 0) -or -not [string]::IsNullOrEmpty($Message)) {
      throw "git-safe pull takes no arguments: it always brings the current task branch up to the branch of the same name on origin."
    }
    Invoke-Guard -GuardAction "pre-stage"
    Assert-ReadyToSync -Command "pull"
    $branch = "$(git -C $repoRoot symbolic-ref --quiet --short HEAD)".Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($branch)) {
      throw "git-safe pull needs a checked-out task branch."
    }
    $remoteRef = "refs/remotes/origin/$branch"
    if (-not (Test-RemoteBranch -Name $branch)) {
      throw "origin has no branch '$branch'; there is nothing to pull. Nothing was changed."
    }
    Invoke-Git -GitArgs @("-C", $repoRoot, "fetch", "--no-tags", "--no-recurse-submodules", "origin", "+refs/heads/${branch}:$remoteRef")
    $before = (git -C $repoRoot rev-parse HEAD).Trim()
    $target = (git -C $repoRoot rev-parse $remoteRef).Trim()
    & git -C $repoRoot merge-base --is-ancestor $remoteRef HEAD
    if ($LASTEXITCODE -eq 0) {
      Write-Output "pull=up-to-date"
      Write-Output "head=$before"
      exit 0
    }
    & git -C $repoRoot merge-base --is-ancestor HEAD $remoteRef
    if ($LASTEXITCODE -eq 0) {
      # --no-autostash: with merge.autoStash set, Git would stash local changes, move HEAD and possibly leave them stashed.
      & git -C $repoRoot merge --ff-only --no-autostash --no-overwrite-ignore --quiet $remoteRef
      if ($LASTEXITCODE -ne 0 -or (git -C $repoRoot rev-parse HEAD).Trim() -ne $target) {
        throw "The fast-forward to origin/$branch was refused, usually because it would overwrite an untracked or ignored local file. HEAD was not moved; check git status."
      }
      Write-Output "pull=fast-forwarded"
      Write-Output "from=$before"
      Write-Output "head=$target"
      exit 0
    }
    # Both sides have commits (also after the remote branch was rewritten): stop as a merge for review.
    Complete-GuardedMerge -Ref $remoteRef -Label "origin/$branch" -Verb "pull"
  }
  "merge" {
    # Merge another task branch into the current one (#783), for example a helper branch whose work belongs in this
    # task: `merge <branch>` takes the local branch, `merge origin/<branch>` fetches and takes the remote one. Never
    # committed here; concluded like sync. The trunk comes in through sync, this branch's own remote through pull.
    if (-not [string]::IsNullOrEmpty($Message) -or -not $Arguments -or $Arguments.Count -ne 1) {
      throw "git-safe merge takes exactly one argument: <branch> for a local task branch, or origin/<branch> for one on origin."
    }
    $source = [string]$Arguments[0]
    if ($source -cnotmatch '^(origin/)?[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*\z' -or $source.Contains("..") -or $source.EndsWith(".lock")) {
      throw "git-safe merge takes a plain branch name (letters, digits, '.', '_', '-', '/'), optionally prefixed with origin/."
    }
    Invoke-Guard -GuardAction "pre-stage"
    Assert-ReadyToSync -Command "merge"
    $trunk = Get-IntegrationBranch
    $branch = "$(git -C $repoRoot symbolic-ref --quiet --short HEAD)".Trim()
    $fromOrigin = $source.StartsWith("origin/")
    $name = if ($fromOrigin) { $source.Substring(7) } else { $source }
    if (@("develop", "main", "master", $trunk) -contains $name) {
      throw "git-safe merge does not take a protected branch. Use git-safe sync for origin/$trunk."
    }
    if ($name -ceq $branch) {
      throw "'$name' is the current branch. Use git-safe pull to take in its commits from origin."
    }
    if ($fromOrigin) {
      if (-not (Test-RemoteBranch -Name $name)) {
        throw "origin has no branch '$name'. Nothing was changed."
      }
      $ref = "refs/remotes/origin/$name"
      Invoke-Git -GitArgs @("-C", $repoRoot, "fetch", "--no-tags", "--no-recurse-submodules", "origin", "+refs/heads/${name}:$ref")
    } else {
      $ref = "refs/heads/$name"
      & git -C $repoRoot show-ref --verify --quiet $ref
      if ($LASTEXITCODE -ne 0) {
        throw "There is no local branch '$name'. For a branch on origin, name it origin/$name. Nothing was changed."
      }
    }
    & git -C $repoRoot merge-base --is-ancestor $ref HEAD
    if ($LASTEXITCODE -eq 0) {
      Write-Output "merge=up-to-date"
      exit 0
    }
    Complete-GuardedMerge -Ref $ref -Label $source -Verb "merge"
  }
  "abort-sync" {
    $trunk = Get-IntegrationBranch
    $trunkRef = "refs/remotes/origin/$trunk"
    # Abandon a merge that sync, pull or merge left unconcluded (#719, #783); raw `git merge --abort` stays forbidden.
    # Only a merge this script recorded when it started it is abandoned, so this never discards some other operation.
    if (($Arguments -and $Arguments.Count -gt 0) -or -not [string]::IsNullOrEmpty($Message)) {
      throw "git-safe abort-sync takes no arguments: it abandons the merge that git-safe sync, pull or merge started."
    }
    Invoke-Guard -GuardAction "pre-stage"
    if (-not (Test-GitPath -Name "MERGE_HEAD")) {
      throw "No merge is in progress; there is nothing for git-safe abort-sync to abandon."
    }
    $mergeHeads = @(Get-MergeHeads)
    $ours = $false
    if ($mergeHeads.Count -eq 1) {
      $recordPath = Get-GitPath -Name $mergeRecordName
      if (Test-Path -LiteralPath $recordPath) {
        $ours = ((Get-Content -LiteralPath $recordPath -Raw).Trim() -eq $mergeHeads[0])
      }
      if (-not $ours) {
        # A sync started by a release that kept no record: a merge of trunk commits.
        & git -C $repoRoot merge-base --is-ancestor $mergeHeads[0] $trunkRef
        $ours = ($LASTEXITCODE -eq 0)
      }
    }
    if (-not $ours) {
      throw "The merge in progress was not started by git-safe sync, pull or merge; abandon it by hand or ask the user."
    }
    Invoke-Git -GitArgs @("-C", $repoRoot, "merge", "--abort")
    Remove-Item -LiteralPath (Get-GitPath -Name $mergeRecordName) -ErrorAction SilentlyContinue
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
    $gitDir = [System.IO.Path]::GetFullPath("$(Invoke-Utf8Native "git" @("-C", $repoRoot, "rev-parse", "--absolute-git-dir"))".Trim())
    $commonDir = [System.IO.Path]::GetFullPath("$(Invoke-Utf8Native "git" @("-C", $repoRoot, "rev-parse", "--path-format=absolute", "--git-common-dir"))".Trim())
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
    & git -C $repoRoot merge --ff-only --no-autostash --no-overwrite-ignore --quiet $trunkRef
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
