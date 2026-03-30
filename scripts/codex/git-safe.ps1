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

function Invoke-Guard {
  param(
    [string]$GuardAction,
    [string]$PushCommandText
  )

  $cmd = @("-ExecutionPolicy", "Bypass", "-File", $guardScript, $GuardAction)
  if ($GuardAction -eq "pre-push" -and -not [string]::IsNullOrWhiteSpace($PushCommandText)) {
    $cmd += @("-PushCommand", $PushCommandText)
  }

  & powershell @cmd
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

function Assert-ExplicitPaths {
  param([string[]]$Paths)

  if (-not $Paths -or $Paths.Count -eq 0) {
    throw "Provide at least one explicit path. Example: powershell -File scripts/codex/git-safe.ps1 add path/to/file"
  }

  foreach ($path in $Paths) {
    if ([string]::IsNullOrWhiteSpace($path)) {
      throw "Empty path arguments are not allowed."
    }

    if ($path -in @(".", "-A", "--all")) {
      throw "Broad staging arguments are forbidden: $path"
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
    Invoke-Guard -GuardAction "clear-review"
    exit 0
  }
  "push" {
    if (-not $Arguments -or $Arguments.Count -eq 0) {
      throw "Provide explicit push arguments. Example: powershell -File scripts/codex/git-safe.ps1 push origin feature/TASK-123"
    }

    $pushCommand = Build-PushCommandText -PushArgs $Arguments
    Invoke-Guard -GuardAction "pre-push" -PushCommandText $pushCommand
    $gitArgs = @("-C", $repoRoot, "push") + $Arguments
    Invoke-Git -GitArgs $gitArgs
    exit 0
  }
}
