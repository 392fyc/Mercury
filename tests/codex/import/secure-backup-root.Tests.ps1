$ErrorActionPreference = "Stop"

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$script:SecureRootScript = Join-Path $script:RepoRoot "scripts\codex\import\secure_backup_root.ps1"
$script:SharedBase = "D:\Codex-Migration-Backup"
$script:RealRoot = "D:\Codex-Migration-Backup\2026-08-15-mercury-sot"
$script:Prefix = ".secure-root-tests-{0}-{1}" -f $PID, [guid]::NewGuid().ToString("N")
$script:TestBaseCapability = [guid]::NewGuid().ToString("N")
$script:TestBase = Join-Path $script:SharedBase (".secure-root-tests-base-{0}-{1}" -f $PID, $script:TestBaseCapability)
$script:TestBaseIdentity = $null
$script:AllowedBase = $script:TestBase
$script:CurrentUserSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$script:SystemSid = "S-1-5-18"
$script:LocalServiceSid = "S-1-5-19"

$nativeSource = @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class SecureRootTestNative {
    private const uint DELETE = 0x00010000;
    private const uint READ_CONTROL = 0x00020000;
    private const uint FILE_READ_ATTRIBUTES = 0x00000080;
    private const uint FILE_WRITE_DATA = 0x00000002;
    private const uint FILE_SHARE_READ = 0x00000001;
    private const uint FILE_SHARE_WRITE = 0x00000002;
    private const uint FILE_SHARE_DELETE = 0x00000004;
    private const uint OPEN_EXISTING = 3;
    private const uint FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000;
    private const uint FILE_FLAG_BACKUP_SEMANTICS = 0x02000000;
    private const int FileDispositionInfo = 4;
    private const uint FILE_ATTRIBUTE_DIRECTORY = 0x10;
    private const uint FILE_ATTRIBUTE_REPARSE_POINT = 0x400;

    [StructLayout(LayoutKind.Sequential)] private struct FILETIME { public uint Low; public uint High; }
    [StructLayout(LayoutKind.Sequential)] private struct BY_HANDLE_FILE_INFORMATION {
        public uint Attributes; public FILETIME Creation; public FILETIME LastAccess; public FILETIME LastWrite;
        public uint VolumeSerial; public uint SizeHigh; public uint SizeLow; public uint Links;
        public uint FileIndexHigh; public uint FileIndexLow;
    }
    [StructLayout(LayoutKind.Sequential, Pack = 1)] private struct FILE_DISPOSITION_INFO {
        public byte DeleteFile;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFileW(string name, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetFileInformationByHandle(SafeFileHandle handle, out BY_HANDLE_FILE_INFORMATION info);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetFileInformationByHandle(SafeFileHandle handle, int infoClass, ref FILE_DISPOSITION_INFO info, uint size);

    private static SafeFileHandle Open(string path, uint access, uint share) {
        SafeFileHandle handle = CreateFileW(path, access, share, IntPtr.Zero, OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS, IntPtr.Zero);
        if (handle.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error(), "CreateFileW failed for test fixture");
        return handle;
    }
    private static BY_HANDLE_FILE_INFORMATION Info(SafeFileHandle handle) {
        BY_HANDLE_FILE_INFORMATION info;
        if (!GetFileInformationByHandle(handle, out info)) throw new Win32Exception(Marshal.GetLastWin32Error(), "GetFileInformationByHandle failed");
        return info;
    }
    public static string Identity(string path) {
        using (SafeFileHandle handle = Open(path, FILE_READ_ATTRIBUTES | READ_CONTROL, FILE_SHARE_READ | FILE_SHARE_WRITE)) {
            BY_HANDLE_FILE_INFORMATION info = Info(handle);
            return info.VolumeSerial.ToString("X8") + ":" + info.FileIndexHigh.ToString("X8") + info.FileIndexLow.ToString("X8");
        }
    }
    public static SafeFileHandle HoldWriter(string path) {
        return Open(path, FILE_WRITE_DATA | DELETE, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE);
    }
    public static void TouchForVerification(string path) {
        using (SafeFileHandle handle = Open(path, FILE_READ_ATTRIBUTES | READ_CONTROL | DELETE, FILE_SHARE_READ)) { Info(handle); }
    }
    public static int FileDispositionInfoSize() { return Marshal.SizeOf(typeof(FILE_DISPOSITION_INFO)); }
    public static void SafeDeleteTree(string path) {
        using (SafeFileHandle handle = Open(path, DELETE | FILE_READ_ATTRIBUTES | READ_CONTROL, FILE_SHARE_READ)) {
            BY_HANDLE_FILE_INFORMATION before = Info(handle);
            bool isDirectory = (before.Attributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
            bool isReparse = (before.Attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0;
            if (isDirectory && !isReparse) {
                foreach (string child in Directory.GetFileSystemEntries(path)) SafeDeleteTree(child);
            }
            BY_HANDLE_FILE_INFORMATION after = Info(handle);
            if (before.VolumeSerial != after.VolumeSerial || before.FileIndexHigh != after.FileIndexHigh || before.FileIndexLow != after.FileIndexLow)
                throw new InvalidOperationException("Fixture identity changed during cleanup");
            FILE_DISPOSITION_INFO disposition = new FILE_DISPOSITION_INFO { DeleteFile = 1 };
            if (!SetFileInformationByHandle(handle, FileDispositionInfo, ref disposition, (uint)Marshal.SizeOf(typeof(FILE_DISPOSITION_INFO))))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "Identity-bound fixture delete failed");
        }
        if (File.Exists(path) || Directory.Exists(path)) throw new IOException("Fixture still exists after identity-bound delete");
    }
}
'@

function Invoke-SecureRootScript {
    param([string[]]$Arguments)
    $effectiveArguments = @($Arguments)
    if ($effectiveArguments -notcontains "-TestBase") { $effectiveArguments += @("-TestBase", $script:TestBase) }
    if ($effectiveArguments -notcontains "-TestBaseCapability") { $effectiveArguments += @("-TestBaseCapability", $script:TestBaseCapability) }
    if ($effectiveArguments -notcontains "-TestBaseIdentity") { $effectiveArguments += @("-TestBaseIdentity", $script:TestBaseIdentity) }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $lines = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $script:SecureRootScript @effectiveArguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
    [pscustomobject]@{
        ExitCode = $exitCode
        Output = (($lines | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine).Trim()
    }
}

function New-TestPath {
    param([Parameter(Mandatory = $true)][string]$Suffix)
    Join-Path $script:AllowedBase ("{0}-{1}" -f $script:Prefix, $Suffix)
}

function Get-AccessOnlyAcl {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)
    $sections = [System.Security.AccessControl.AccessControlSections]::Access -bor [System.Security.AccessControl.AccessControlSections]::Owner
    return (Get-Item -LiteralPath $LiteralPath -Force).GetAccessControl($sections)
}

function Set-AccessOnlyAcl {
    param([Parameter(Mandatory = $true)][string]$LiteralPath, [Parameter(Mandatory = $true)]$Acl)
    (Get-Item -LiteralPath $LiteralPath -Force).SetAccessControl($Acl)
}

function Ensure-MismatchedBaseAcl {
    $acl = Get-AccessOnlyAcl -LiteralPath $script:AllowedBase
    $rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
    if (-not $acl.AreAccessRulesProtected -or $rules.Count -ne 2) { return }
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
        (New-Object System.Security.Principal.SecurityIdentifier($script:LocalServiceSid)),
        [System.Security.AccessControl.FileSystemRights]::ReadAndExecute,
        [System.Security.AccessControl.InheritanceFlags]::None,
        [System.Security.AccessControl.PropagationFlags]::None,
        [System.Security.AccessControl.AccessControlType]::Allow
    )))
    Set-AccessOnlyAcl -LiteralPath $script:AllowedBase -Acl $acl
}

function New-ExactDirectoryAcl {
    param(
        [System.Security.AccessControl.InheritanceFlags]$InheritanceFlags = ([System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit),
        [System.Security.AccessControl.PropagationFlags]$PropagationFlags = [System.Security.AccessControl.PropagationFlags]::None,
        [string]$ExtraSid
    )
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner((New-Object System.Security.Principal.SecurityIdentifier($script:CurrentUserSid)))
    foreach ($sid in @($script:CurrentUserSid, $script:SystemSid)) {
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
            (New-Object System.Security.Principal.SecurityIdentifier($sid)),
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            $InheritanceFlags,
            $PropagationFlags,
            [System.Security.AccessControl.AccessControlType]::Allow
        )))
    }
    if (-not [string]::IsNullOrEmpty($ExtraSid)) {
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
            (New-Object System.Security.Principal.SecurityIdentifier($ExtraSid)),
            [System.Security.AccessControl.FileSystemRights]::ReadAndExecute,
            [System.Security.AccessControl.InheritanceFlags]::None,
            [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Allow
        )))
    }
    return $acl
}

function Set-RootAccessShape {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][System.Security.AccessControl.InheritanceFlags]$InheritanceFlags,
        [Parameter(Mandatory = $true)][System.Security.AccessControl.PropagationFlags]$PropagationFlags
    )
    Set-AccessOnlyAcl -LiteralPath $LiteralPath -Acl (New-ExactDirectoryAcl -InheritanceFlags $InheritanceFlags -PropagationFlags $PropagationFlags)
}

function Add-ExtraRule {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][string]$Sid,
        [Parameter(Mandatory = $true)][System.Security.AccessControl.AccessControlType]$Type
    )
    $acl = Get-AccessOnlyAcl -LiteralPath $LiteralPath
    $acl.SetAccessRuleProtection($true, $true)
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
        (New-Object System.Security.Principal.SecurityIdentifier($Sid)),
        [System.Security.AccessControl.FileSystemRights]::ReadAndExecute,
        [System.Security.AccessControl.InheritanceFlags]::None,
        [System.Security.AccessControl.PropagationFlags]::None,
        $Type
    )))
    Set-AccessOnlyAcl -LiteralPath $LiteralPath -Acl $acl
}

function Get-SecuritySnapshot {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)
    $item = Get-Item -LiteralPath $LiteralPath -Force
    $acl = Get-AccessOnlyAcl -LiteralPath $LiteralPath
    ([ordered]@{
        Identity = [SecureRootTestNative]::Identity($LiteralPath)
        Attributes = [int]$item.Attributes
        CreationUtcTicks = $item.CreationTimeUtc.Ticks
        LastWriteUtcTicks = $item.LastWriteTimeUtc.Ticks
        OwnerAndDacl = $acl.GetSecurityDescriptorSddlForm([System.Security.AccessControl.AccessControlSections]::Access -bor [System.Security.AccessControl.AccessControlSections]::Owner)
        Children = if ($item.PSIsContainer) { (@(Get-ChildItem -LiteralPath $LiteralPath -Force | Select-Object -ExpandProperty Name | Sort-Object) -join "|") } else { "" }
        Sha256 = if ($item.PSIsContainer) { "" } else { (Get-FileHash -LiteralPath $LiteralPath -Algorithm SHA256).Hash }
    } | ConvertTo-Json -Compress)
}

function Get-BaseBoundarySnapshot {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)
    $item = Get-Item -LiteralPath $LiteralPath -Force
    $acl = Get-AccessOnlyAcl -LiteralPath $LiteralPath
    ([ordered]@{
        Identity = [SecureRootTestNative]::Identity($LiteralPath)
        Attributes = [int]$item.Attributes
        OwnerAndDacl = $acl.GetSecurityDescriptorSddlForm([System.Security.AccessControl.AccessControlSections]::Access -bor [System.Security.AccessControl.AccessControlSections]::Owner)
        Children = (@(Get-ChildItem -LiteralPath $LiteralPath -Force | Select-Object -ExpandProperty Name | Sort-Object) -join "|")
    } | ConvertTo-Json -Compress)
}

function Remove-PrefixedFixtures {
    foreach ($entry in @(Get-ChildItem -LiteralPath $script:AllowedBase -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name.StartsWith($script:Prefix, [System.StringComparison]::OrdinalIgnoreCase) })) {
        [SecureRootTestNative]::SafeDeleteTree($entry.FullName)
    }
}

function New-ProtectedRoot {
    param([Parameter(Mandatory = $true)][string]$Suffix)
    $root = New-TestPath -Suffix $Suffix
    $result = Invoke-SecureRootScript -Arguments @("-Path", $root)
    if ($result.ExitCode -ne 0) { throw "Root creation failed: $($result.Output)" }
    Write-Output -NoEnumerate $root
}

function Wait-TreeQuiescent {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)
    $watcher = New-Object System.IO.FileSystemWatcher($LiteralPath)
    try {
        $watcher.IncludeSubdirectories = $true
        $watcher.NotifyFilter = [System.IO.NotifyFilters]::FileName -bor [System.IO.NotifyFilters]::DirectoryName -bor [System.IO.NotifyFilters]::Attributes -bor [System.IO.NotifyFilters]::Security -bor [System.IO.NotifyFilters]::Size -bor [System.IO.NotifyFilters]::LastWrite
        $watcher.EnableRaisingEvents = $true
        $quietSince = [DateTime]::UtcNow
        $minimumObservationEnd = $quietSince.AddSeconds(5)
        $deadline = $quietSince.AddSeconds(12)
        do {
            $change = $watcher.WaitForChanged([System.IO.WatcherChangeTypes]::All, 250)
            if (-not $change.TimedOut) { $quietSince = [DateTime]::UtcNow }
            if ([DateTime]::UtcNow -ge $deadline) { throw "Test tree did not become quiescent before the single VerifyTree gate." }
        } while ([DateTime]::UtcNow -lt $minimumObservationEnd -or ([DateTime]::UtcNow - $quietSince).TotalMilliseconds -lt 1500)
    }
    finally { $watcher.Dispose() }
}

function Initialize-TreeVerificationMetadata {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)
    foreach ($entry in @($LiteralPath) + @(Get-ChildItem -LiteralPath $LiteralPath -Force -Recurse | Select-Object -ExpandProperty FullName)) {
        [SecureRootTestNative]::TouchForVerification($entry)
        $null = Get-AccessOnlyAcl -LiteralPath $entry
        $null = (Get-Item -LiteralPath $entry -Force).Attributes
    }
}

Describe "secure_backup_root.ps1 security boundary" {
    BeforeAll {
        (New-Object System.IO.DriveInfo("D:\")).DriveFormat | Should Be "NTFS"
        Add-Type -TypeDefinition $nativeSource
        Test-Path -LiteralPath $script:RealRoot | Should Be $false
        Test-Path -LiteralPath $script:SharedBase -PathType Container | Should Be $true
        $script:SharedBaseBefore = Get-BaseBoundarySnapshot -LiteralPath $script:SharedBase
        Test-Path -LiteralPath $script:TestBase | Should Be $false
        New-Item -ItemType Directory -Path $script:TestBase | Out-Null
        Set-AccessOnlyAcl -LiteralPath $script:TestBase -Acl (New-ExactDirectoryAcl)
        $script:TestBaseIdentity = [SecureRootTestNative]::Identity($script:TestBase)
        (Get-Item -LiteralPath $script:TestBase -Force).Attributes.HasFlag([System.IO.FileAttributes]::Encrypted) | Should Be $true
        @(Get-ChildItem -LiteralPath $script:TestBase -Force).Count | Should Be 0
    }

    AfterEach {
        Test-Path -LiteralPath $script:RealRoot | Should Be $false
        Remove-PrefixedFixtures
        @(Get-ChildItem -LiteralPath $script:AllowedBase -Force | Where-Object { $_.Name.StartsWith($script:Prefix, [System.StringComparison]::OrdinalIgnoreCase) }).Count | Should Be 0
    }

    AfterAll {
        if (Test-Path -LiteralPath $script:TestBase) { [SecureRootTestNative]::SafeDeleteTree($script:TestBase) }
        Test-Path -LiteralPath $script:TestBase | Should Be $false
        (Get-BaseBoundarySnapshot -LiteralPath $script:SharedBase) | Should Be $script:SharedBaseBefore
        Test-Path -LiteralPath $script:RealRoot | Should Be $false
    }

    It "does not harden a mismatched allowed base while unrelated content exists" {
        Ensure-MismatchedBaseAcl
        $sentinel = New-TestPath -Suffix "base-unrelated"
        [System.IO.File]::WriteAllText($sentinel, "fixture")
        $beforeBase = Get-SecuritySnapshot -LiteralPath $script:AllowedBase
        $beforeFile = Get-SecuritySnapshot -LiteralPath $sentinel
        $root = New-TestPath -Suffix "base-refusal-root"
        $result = Invoke-SecureRootScript -Arguments @("-Path", $root)
        $result.ExitCode | Should Not Be 0
        $result.Output | Should Match "allowed backup base must already be protected and EFS-encrypted"
        (Get-SecuritySnapshot -LiteralPath $script:AllowedBase) | Should Be $beforeBase
        (Get-SecuritySnapshot -LiteralPath $sentinel) | Should Be $beforeFile
        Test-Path -LiteralPath $root | Should Be $false
    }

    It "accepts an already protected EFS test base without changing its boundary metadata" {
        Set-AccessOnlyAcl -LiteralPath $script:TestBase -Acl (New-ExactDirectoryAcl)
        $beforeIdentity = [SecureRootTestNative]::Identity($script:TestBase)
        $beforeAttributes = [int](Get-Item -LiteralPath $script:TestBase -Force).Attributes
        $beforeSecurity = (Get-AccessOnlyAcl -LiteralPath $script:TestBase).GetSecurityDescriptorSddlForm([System.Security.AccessControl.AccessControlSections]::Access -bor [System.Security.AccessControl.AccessControlSections]::Owner)
        $root = New-TestPath -Suffix "base-hardening-root"
        $result = Invoke-SecureRootScript -Arguments @("-Path", $root)
        $result.ExitCode | Should Be 0
        $baseAcl = Get-AccessOnlyAcl -LiteralPath $script:AllowedBase
        $baseAcl.AreAccessRulesProtected | Should Be $true
        @($baseAcl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])).Count | Should Be 2
        (Get-Item -LiteralPath $script:AllowedBase -Force).Attributes.HasFlag([System.IO.FileAttributes]::Encrypted) | Should Be $true
        [SecureRootTestNative]::Identity($script:TestBase) | Should Be $beforeIdentity
        [int](Get-Item -LiteralPath $script:TestBase -Force).Attributes | Should Be $beforeAttributes
        $baseAcl.GetSecurityDescriptorSddlForm([System.Security.AccessControl.AccessControlSections]::Access -bor [System.Security.AccessControl.AccessControlSections]::Owner) | Should Be $beforeSecurity
    }

    It "bootstraps EFS in explicit maintenance mode and freezes the registered base identity" {
        Set-AccessOnlyAcl -LiteralPath $script:TestBase -Acl (New-ExactDirectoryAcl)
        $beforeIdentity = [SecureRootTestNative]::Identity($script:TestBase)
        [System.IO.File]::Decrypt($script:TestBase)
        (Get-Item -LiteralPath $script:TestBase -Force).Attributes.HasFlag([System.IO.FileAttributes]::Encrypted) | Should Be $false
        $result = Invoke-SecureRootScript -Arguments @("-BootstrapBase")
        if ($result.ExitCode -ne 0) { throw "BootstrapBase failed: $($result.Output)" }
        $report = $result.Output | ConvertFrom-Json
        $report.Mode | Should Be "BootstrapBase"
        $report.RootIdentity | Should Be $beforeIdentity
        [SecureRootTestNative]::Identity($script:TestBase) | Should Be $beforeIdentity
        (Get-Item -LiteralPath $script:TestBase -Force).Attributes.HasFlag([System.IO.FileAttributes]::Encrypted) | Should Be $true
        @(Get-ChildItem -LiteralPath $script:TestBase -Force).Count | Should Be 0
    }

    It "rejects ADS drive-relative UNC extended and nested destinations" {
        $leaf = "{0}-path" -f $script:Prefix
        $testBaseLeaf = [System.IO.Path]::GetFileName($script:TestBase)
        $cases = @(
            "$($script:TestBase)\${leaf}:stream",
            ("D:" + $script:TestBase.Substring(3) + "\$leaf"),
            "\\localhost\D`$\Codex-Migration-Backup\$testBaseLeaf\$leaf",
            "\\?\$($script:TestBase)\$leaf",
            (Join-Path (Join-Path $script:TestBase "parent") $leaf),
            (Join-Path $script:SharedBase $leaf)
        )
        foreach ($candidate in $cases) {
            $result = Invoke-SecureRootScript -Arguments @("-Path", $candidate)
            $result.ExitCode | Should Not Be 0
            $result.Output | Should Match "absolute direct child|outside the allowed backup base|alternate data stream|path's format is not supported"
        }
    }

    It "rejects a reparse root and leaves the target metadata unchanged" {
        $target = New-TestPath -Suffix "root-link-target"
        $root = New-TestPath -Suffix "root-link"
        New-Item -ItemType Directory -Path $target | Out-Null
        $marker = Join-Path $target "marker.txt"
        [System.IO.File]::WriteAllText($marker, "fixture")
        $beforeTarget = Get-SecuritySnapshot -LiteralPath $target
        $beforeMarker = Get-SecuritySnapshot -LiteralPath $marker
        New-Item -ItemType Junction -Path $root -Target $target | Out-Null
        $result = Invoke-SecureRootScript -Arguments @("-Path", $root)
        $result.ExitCode | Should Not Be 0
        $result.Output | Should Match "reparse point"
        (Get-SecuritySnapshot -LiteralPath $target) | Should Be $beforeTarget
        (Get-SecuritySnapshot -LiteralPath $marker) | Should Be $beforeMarker
    }

    It "fixture cleanup removes a junction object without traversing its target" {
        $target = New-TestPath -Suffix "cleanup-link-target"
        $link = New-TestPath -Suffix "cleanup-link"
        New-Item -ItemType Directory -Path $target | Out-Null
        $marker = Join-Path $target "marker.txt"
        [System.IO.File]::WriteAllText($marker, "fixture")
        $beforeTarget = Get-SecuritySnapshot -LiteralPath $target
        $beforeMarker = Get-SecuritySnapshot -LiteralPath $marker
        New-Item -ItemType Junction -Path $link -Target $target | Out-Null
        [SecureRootTestNative]::SafeDeleteTree($link)
        Test-Path -LiteralPath $link | Should Be $false
        (Get-SecuritySnapshot -LiteralPath $target) | Should Be $beforeTarget
        (Get-SecuritySnapshot -LiteralPath $marker) | Should Be $beforeMarker
    }

    It "rejects an unsafe existing empty root without changing its metadata" {
        $root = New-TestPath -Suffix "unsafe-empty"
        New-Item -ItemType Directory -Path $root | Out-Null
        $before = Get-SecuritySnapshot -LiteralPath $root
        $result = Invoke-SecureRootScript -Arguments @("-Path", $root)
        $result.ExitCode | Should Not Be 0
        $result.Output | Should Match "existing root is not already protected"
        (Get-SecuritySnapshot -LiteralPath $root) | Should Be $before
    }

    It "rejects a nonempty root without changing content owner DACL attributes timestamps or entries" {
        $root = New-TestPath -Suffix "nonempty"
        New-Item -ItemType Directory -Path $root | Out-Null
        $sentinel = Join-Path $root "keep.txt"
        [System.IO.File]::WriteAllText($sentinel, "fixture")
        $beforeRoot = Get-SecuritySnapshot -LiteralPath $root
        $beforeFile = Get-SecuritySnapshot -LiteralPath $sentinel
        $result = Invoke-SecureRootScript -Arguments @("-Path", $root)
        $result.ExitCode | Should Not Be 0
        $result.Output | Should Match "must be empty"
        (Get-SecuritySnapshot -LiteralPath $root) | Should Be $beforeRoot
        (Get-SecuritySnapshot -LiteralPath $sentinel) | Should Be $beforeFile
    }

    foreach ($shape in @(
        @{ Name = "None"; Inheritance = [System.Security.AccessControl.InheritanceFlags]::None; Propagation = [System.Security.AccessControl.PropagationFlags]::None },
        @{ Name = "ContainerOnly"; Inheritance = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit; Propagation = [System.Security.AccessControl.PropagationFlags]::None },
        @{ Name = "ObjectOnly"; Inheritance = [System.Security.AccessControl.InheritanceFlags]::ObjectInherit; Propagation = [System.Security.AccessControl.PropagationFlags]::None },
        @{ Name = "NoPropagate"; Inheritance = ([System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit); Propagation = [System.Security.AccessControl.PropagationFlags]::NoPropagateInherit }
    )) {
        It "VerifyTree rejects root ACE propagation shape $($shape.Name)" {
            $root = New-ProtectedRoot -Suffix ("flags-" + $shape.Name)
            Set-RootAccessShape -LiteralPath $root -InheritanceFlags $shape.Inheritance -PropagationFlags $shape.Propagation
            $result = Invoke-SecureRootScript -Arguments @("-VerifyTree", $root)
            $result.ExitCode | Should Not Be 0
            $result.Output | Should Match "InheritanceFlags|PropagationFlags"
        }
    }

    It "idempotently accepts an existing empty root only when it is already exact and encrypted" {
        $root = New-ProtectedRoot -Suffix "idempotent"
        $before = Get-SecuritySnapshot -LiteralPath $root
        $result = Invoke-SecureRootScript -Arguments @("-Path", $root)
        $result.ExitCode | Should Be 0
        ($result.Output | ConvertFrom-Json).Mode | Should Be "Create"
        (Get-SecuritySnapshot -LiteralPath $root) | Should Be $before
    }

    It "reports exact root ACE fields and verifies a two-level descendant tree" {
        $root = New-ProtectedRoot -Suffix "two-level"
        $child = Join-Path $root "child"
        $grandchild = Join-Path $child "grandchild"
        $file = Join-Path $grandchild "payload.bin"
        New-Item -ItemType Directory -Path $grandchild -Force | Out-Null
        [System.IO.File]::WriteAllBytes($file, [guid]::NewGuid().ToByteArray())
        # Wait only for the known fixture writes to quiesce. Production is invoked exactly once.
        Initialize-TreeVerificationMetadata -LiteralPath $root
        Wait-TreeQuiescent -LiteralPath $root
        $result = Invoke-SecureRootScript -Arguments @("-VerifyTree", $root)
        if ($result.ExitCode -ne 0) { throw "Single VerifyTree invocation failed: $($result.Output)" }
        $result.ExitCode | Should Be 0
        $report = $result.Output | ConvertFrom-Json
        $report.FileCount | Should Be 1
        $report.DirectoryCount | Should Be 2
        @($report.Access | Where-Object { $_.InheritanceFlags -ne "ContainerInherit, ObjectInherit" -or $_.PropagationFlags -ne "None" -or $_.IsInherited }).Count | Should Be 0
        [string]::IsNullOrWhiteSpace($report.RootIdentity) | Should Be $false
        [string]::IsNullOrWhiteSpace($report.TreeSnapshotHash) | Should Be $false
    }

    It "VerifyTree rejects an extra broad Allow ACE on a grandchild directory" {
        $root = New-ProtectedRoot -Suffix "grandchild-extra"
        $grandchild = Join-Path (Join-Path $root "child") "grandchild"
        New-Item -ItemType Directory -Path $grandchild -Force | Out-Null
        Add-ExtraRule -LiteralPath $grandchild -Sid "S-1-5-11" -Type Allow
        $result = Invoke-SecureRootScript -Arguments @("-VerifyTree", $root)
        $result.ExitCode | Should Not Be 0
        $result.Output | Should Match "DACL"
    }

    It "VerifyTree rejects a Deny ACE on a descendant file" {
        $root = New-ProtectedRoot -Suffix "file-deny"
        $file = Join-Path (Join-Path $root "child") "payload.bin"
        New-Item -ItemType Directory -Path (Split-Path -Parent $file) | Out-Null
        [System.IO.File]::WriteAllBytes($file, [guid]::NewGuid().ToByteArray())
        Add-ExtraRule -LiteralPath $file -Sid $script:LocalServiceSid -Type Deny
        $result = Invoke-SecureRootScript -Arguments @("-VerifyTree", $root)
        $result.ExitCode | Should Not Be 0
        $result.Output | Should Match "DACL"
    }

    It "VerifyTree rejects a wide inherited ACL on a depth-two descendant" {
        $root = New-ProtectedRoot -Suffix "depth-wide"
        $grandchild = Join-Path (Join-Path $root "child") "grandchild"
        New-Item -ItemType Directory -Path $grandchild -Force | Out-Null
        Add-ExtraRule -LiteralPath $grandchild -Sid "S-1-5-32-545" -Type Allow
        $result = Invoke-SecureRootScript -Arguments @("-VerifyTree", $root)
        $result.ExitCode | Should Not Be 0
        $result.Output | Should Match "DACL"
    }

    It "VerifyTree rejects a mechanically injected descendant owner mismatch at depth two" {
        $root = New-ProtectedRoot -Suffix "owner-mismatch"
        $grandchild = Join-Path (Join-Path $root "child") "grandchild"
        New-Item -ItemType Directory -Path $grandchild -Force | Out-Null
        $result = Invoke-SecureRootScript -Arguments @("-VerifyTree", $root, "-TestFault", "ExpectOwnerMismatch")
        $result.ExitCode | Should Not Be 0
        $result.Output | Should Match "Owner SID mismatch"
    }

    It "VerifyTree rejects a descendant junction without accessing its target" {
        $root = New-ProtectedRoot -Suffix "tree-junction"
        $target = New-TestPath -Suffix "tree-junction-target"
        New-Item -ItemType Directory -Path $target | Out-Null
        $marker = Join-Path $target "marker.txt"
        [System.IO.File]::WriteAllText($marker, "fixture")
        $beforeTarget = Get-SecuritySnapshot -LiteralPath $target
        $beforeMarker = Get-SecuritySnapshot -LiteralPath $marker
        New-Item -ItemType Junction -Path (Join-Path $root "junction") -Target $target | Out-Null
        $result = Invoke-SecureRootScript -Arguments @("-VerifyTree", $root)
        $result.ExitCode | Should Not Be 0
        $result.Output | Should Match "reparse point"
        $result.Output | Should Not Match "Access.*denied"
        (Get-SecuritySnapshot -LiteralPath $target) | Should Be $beforeTarget
        (Get-SecuritySnapshot -LiteralPath $marker) | Should Be $beforeMarker
    }

    It "VerifyTree rejects an entry added between its two bound snapshots" {
        $root = New-ProtectedRoot -Suffix "tree-mutation"
        New-Item -ItemType Directory -Path (Join-Path $root "child") | Out-Null
        $result = Invoke-SecureRootScript -Arguments @("-VerifyTree", $root, "-TestFault", "VerifyTreeMutation")
        $result.ExitCode | Should Not Be 0
        $result.Output | Should Match "tree changed during verification|directory change watcher"
    }

    It "rejects an existing root while a writer or deleter handle is outstanding" {
        $root = New-TestPath -Suffix "writer-held"
        New-Item -ItemType Directory -Path $root | Out-Null
        $writer = [SecureRootTestNative]::HoldWriter($root)
        try { $result = Invoke-SecureRootScript -Arguments @("-Path", $root) }
        finally { $writer.Dispose() }
        $result.ExitCode | Should Not Be 0
        $result.Output | Should Match "concurrent writer|sharing violation"
    }

    It "holds an identity-bound root handle that prevents replacement during create" {
        $root = New-TestPath -Suffix "root-race"
        $ready = "$root.race-ready"
        $release = "$root.race-release"
        $stdout = New-TestPath -Suffix "race-stdout"
        $stderr = New-TestPath -Suffix "race-stderr"
        $process = Start-Process -FilePath powershell.exe -ArgumentList @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", $script:SecureRootScript, "-Path", $root, "-TestBase", $script:TestBase, "-TestBaseCapability", $script:TestBaseCapability, "-TestBaseIdentity", $script:TestBaseIdentity, "-TestFault", "PauseAfterRootLock") -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru -WindowStyle Hidden
        # Preserve the native process handle so Windows PowerShell 5.1 can report ExitCode after exit.
        $null = $process.Handle
        try {
            $deadline = [DateTime]::UtcNow.AddSeconds(20)
            while (-not (Test-Path -LiteralPath $ready) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 50 }
            Test-Path -LiteralPath $ready | Should Be $true
            $beforeIdentity = [SecureRootTestNative]::Identity($root)
            { [System.IO.Directory]::Move($root, "$root-swapped") } | Should Throw
            [System.IO.File]::WriteAllText($release, "release")
            $process.WaitForExit(30000) | Should Be $true
            $process.WaitForExit()
            $process.Refresh()
            $process.ExitCode | Should Be 0
            [SecureRootTestNative]::Identity($root) | Should Be $beforeIdentity
        }
        finally {
            if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit() }
            $process.Dispose()
        }
    }

    It "fails create when a child is inserted while the root anchor is held" {
        $root = New-TestPath -Suffix "root-child-race"
        $ready = "$root.race-ready"
        $release = "$root.race-release"
        $stdout = New-TestPath -Suffix "child-race-stdout"
        $stderr = New-TestPath -Suffix "child-race-stderr"
        $process = Start-Process -FilePath powershell.exe -ArgumentList @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", $script:SecureRootScript, "-Path", $root, "-TestBase", $script:TestBase, "-TestBaseCapability", $script:TestBaseCapability, "-TestBaseIdentity", $script:TestBaseIdentity, "-TestFault", "PauseAfterRootLock") -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru -WindowStyle Hidden
        $null = $process.Handle
        try {
            $deadline = [DateTime]::UtcNow.AddSeconds(20)
            while (-not (Test-Path -LiteralPath $ready) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 50 }
            Test-Path -LiteralPath $ready | Should Be $true
            [System.IO.File]::WriteAllBytes((Join-Path $root "attacker.bin"), [guid]::NewGuid().ToByteArray())
            [System.IO.File]::WriteAllText($release, "release")
            $process.WaitForExit(30000) | Should Be $true
            $process.WaitForExit()
            $process.Refresh()
            $process.ExitCode | Should Not Be 0
            ((Get-Content -LiteralPath $stderr -Raw) + (Get-Content -LiteralPath $stdout -Raw)) | Should Match "must remain empty|directory change watcher"
        }
        finally {
            if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit() }
            $process.Dispose()
        }
    }

    It "keeps the initial root anchor and watcher through the final success decision" {
        $root = New-ProtectedRoot -Suffix "final-boundary-race"
        $ready = "$root.final-ready"; $release = "$root.final-release"
        $stdout = New-TestPath -Suffix "final-race-stdout"; $stderr = New-TestPath -Suffix "final-race-stderr"
        $process = Start-Process -FilePath powershell.exe -ArgumentList @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", $script:SecureRootScript, "-VerifyTree", $root, "-TestBase", $script:TestBase, "-TestBaseCapability", $script:TestBaseCapability, "-TestBaseIdentity", $script:TestBaseIdentity, "-TestFault", "PauseAfterFinalVerification") -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru -WindowStyle Hidden
        $null = $process.Handle
        try {
            $deadline = [DateTime]::UtcNow.AddSeconds(20)
            while (-not (Test-Path -LiteralPath $ready) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 50 }
            Test-Path -LiteralPath $ready | Should Be $true
            $transient = Join-Path $root "transient.bin"
            [System.IO.File]::WriteAllBytes($transient, [guid]::NewGuid().ToByteArray())
            [System.IO.File]::Delete($transient)
            [System.IO.File]::WriteAllText($release, "release")
            $process.WaitForExit(30000) | Should Be $true
            $process.WaitForExit(); $process.Refresh()
            $process.ExitCode | Should Not Be 0
            ((Get-Content -LiteralPath $stderr -Raw) + (Get-Content -LiteralPath $stdout -Raw)) | Should Match "directory change watcher"
            @(Get-ChildItem -LiteralPath $root -Force).Count | Should Be 0
        }
        finally {
            if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit() }
            $process.Dispose()
        }
    }

    It "does not depend on mutable SystemRoot after native initialization" {
        $root = New-TestPath -Suffix "system-directory"
        $result = Invoke-SecureRootScript -Arguments @("-Path", $root, "-TestFault", "MutableSystemRoot")
        $result.ExitCode | Should Be 0
        ($result.Output | ConvertFrom-Json).FileCount | Should Be 0
    }

    It "survives repeated watcher lifecycle and constructor failure cleanup" {
        $root = New-TestPath -Suffix "watcher-lifecycle"
        $result = Invoke-SecureRootScript -Arguments @("-Path", $root, "-TestFault", "WatcherLifecycleStress")
        $result.ExitCode | Should Be 0
        $report = $result.Output | ConvertFrom-Json
        $report.FileCount | Should Be 0
        $report.DirectoryCount | Should Be 0
    }

    It "accepts only an isolated direct-child test base override" {
        $root = New-TestPath -Suffix "base-override"
        $result = Invoke-SecureRootScript -Arguments @("-Path", $root)
        $result.ExitCode | Should Be 0
        ($result.Output | ConvertFrom-Json).FileCount | Should Be 0
        foreach ($invalid in @($script:SharedBase, $script:RealRoot, (Join-Path $script:TestBase "nested"), (Join-Path $script:SharedBase ("ordinary-test-base-{0}" -f $PID)))) {
            $invalidResult = Invoke-SecureRootScript -Arguments @("-Path", $root, "-TestBase", $invalid)
            $invalidResult.ExitCode | Should Not Be 0
        }
        (Invoke-SecureRootScript -Arguments @("-Path", $root, "-TestBaseCapability", ([guid]::NewGuid().ToString("N")))).ExitCode | Should Not Be 0
        (Invoke-SecureRootScript -Arguments @("-Path", $root, "-TestBaseIdentity", "00000000:0000000000000000")).ExitCode | Should Not Be 0
    }

    It "rejects a prefix-only pre-existing test base and uses the one-byte FILE_DISPOSITION_INFO ABI" {
        [SecureRootTestNative]::FileDispositionInfoSize() | Should Be 1
        $prefixOnly = Join-Path $script:SharedBase (".secure-root-tests-shared-{0}" -f $PID)
        New-Item -ItemType Directory -Path $prefixOnly | Out-Null
        try {
            Set-AccessOnlyAcl -LiteralPath $prefixOnly -Acl (New-ExactDirectoryAcl)
            $root = Join-Path $prefixOnly ("{0}-prefix-only" -f $script:Prefix)
            $result = Invoke-SecureRootScript -Arguments @("-Path", $root, "-TestBase", $prefixOnly)
            $result.ExitCode | Should Not Be 0
            Test-Path -LiteralPath $root | Should Be $false
        }
        finally { if (Test-Path -LiteralPath $prefixOnly) { [SecureRootTestNative]::SafeDeleteTree($prefixOnly) } }
    }

    foreach ($fault in @(
        @{ Name = "CipherFailure"; Message = "EFS enablement failed" },
        @{ Name = "CipherTimeout"; Message = "timed out" },
        @{ Name = "ProbeAfterHandleFailure"; Message = "immediately after handle acquisition" },
        @{ Name = "ProbeWriteFailure"; Message = "probe write failed" },
        @{ Name = "ProbeDeleteFailure"; Message = "probe deletion failed" }
    )) {
        It "fails closed for restricted fault $($fault.Name) and leaves no root content" {
            $root = New-TestPath -Suffix ("fault-" + $fault.Name)
            $result = Invoke-SecureRootScript -Arguments @("-Path", $root, "-TestFault", $fault.Name)
            $result.ExitCode | Should Not Be 0
            $result.Output | Should Match $fault.Message
            if (Test-Path -LiteralPath $root) { @(Get-ChildItem -LiteralPath $root -Force).Count | Should Be 0 }
        }
    }

    It "reports an explicit cleanup failure when the bound probe cannot be deleted" {
        $root = New-TestPath -Suffix "probe-cleanup-failure"
        $result = Invoke-SecureRootScript -Arguments @("-Path", $root, "-TestFault", "ProbeCleanupFailure")
        $result.ExitCode | Should Not Be 0
        $result.Output | Should Match "CLEANUP FAILURE"
        Test-Path -LiteralPath $root | Should Be $true
        @(Get-ChildItem -LiteralPath $root -Force).Count | Should Be 1
    }

    It "continues later probe and root cleanup after an earlier cleanup step fails" {
        $root = New-TestPath -Suffix "probe-cleanup-aggregation"
        $result = Invoke-SecureRootScript -Arguments @("-Path", $root, "-TestFault", "ProbeCleanupAggregation")
        $result.ExitCode | Should Not Be 0
        $result.Output | Should Match "CLEANUP FAILURE.*first cleanup step failure"
        Test-Path -LiteralPath $root | Should Be $false
    }

    It "does not expose the former arbitrary CipherExecutable production seam" {
        $root = New-TestPath -Suffix "no-command-seam"
        $fake = New-TestPath -Suffix "fake-cipher.cmd"
        $marker = New-TestPath -Suffix "command-marker"
        Set-Content -LiteralPath $fake -Encoding Ascii -Value "@echo executed>$marker"
        $result = Invoke-SecureRootScript -Arguments @("-Path", $root, "-CipherExecutable", $fake)
        $result.ExitCode | Should Not Be 0
        Test-Path -LiteralPath $marker | Should Be $false
        Test-Path -LiteralPath $root | Should Be $false
    }
}
