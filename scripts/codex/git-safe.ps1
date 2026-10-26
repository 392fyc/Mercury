[CmdletBinding()]
param(
  [Parameter(Mandatory = $true, Position = 0)]
  [ValidateSet("add", "commit", "push")]
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
  if ((Get-Item -LiteralPath $modulesFile).Attributes -band $reparse) {
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
      $item = Get-Item -LiteralPath $candidate
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

  if ([string]::IsNullOrWhiteSpace(($staged -join ""))) {
    throw "No staged changes found. Stage explicit files with git-safe add first."
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
}
