[CmdletBinding(DefaultParameterSetName = "Create")]
param(
    [Parameter(Mandatory = $true, ParameterSetName = "Create")]
    [string]$Path,

    [Parameter(Mandatory = $true, ParameterSetName = "VerifyTree")]
    [Alias("verify-tree")]
    [string]$VerifyTree,

    [Parameter(Mandatory = $true, ParameterSetName = "BootstrapBase")]
    [switch]$BootstrapBase,

    [Parameter(ParameterSetName = "BootstrapBase")]
    [switch]$MaintenanceWindowConfirmed,

    [Parameter(ParameterSetName = "Create")]
    [Parameter(ParameterSetName = "VerifyTree")]
    [ValidateSet("None", "CipherFailure", "CipherTimeout", "ProbeAfterHandleFailure", "ProbeWriteFailure", "ProbeDeleteFailure", "ProbeCleanupFailure", "ProbeCleanupAggregation", "VerifyTreeMutation", "ExpectOwnerMismatch", "PauseAfterRootLock", "PauseAfterFinalVerification", "WatcherLifecycleStress", "MutableSystemRoot")]
    [string]$TestFault = "None",

    [Parameter(ParameterSetName = "Create")]
    [Parameter(ParameterSetName = "VerifyTree")]
    [Parameter(ParameterSetName = "BootstrapBase")]
    [string]$TestBase,

    [Parameter(ParameterSetName = "Create")]
    [Parameter(ParameterSetName = "VerifyTree")]
    [Parameter(ParameterSetName = "BootstrapBase")]
    [string]$TestBaseCapability,

    [Parameter(ParameterSetName = "Create")]
    [Parameter(ParameterSetName = "VerifyTree")]
    [Parameter(ParameterSetName = "BootstrapBase")]
    [string]$TestBaseIdentity
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:SharedBase = [System.IO.Path]::GetFullPath("D:\Codex-Migration-Backup").TrimEnd("\")
$script:RealTarget = [System.IO.Path]::GetFullPath("D:\Codex-Migration-Backup\2026-08-15-mercury-sot").TrimEnd("\")
$script:AllowedBase = $script:SharedBase
$script:CurrentUserSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$script:SystemSid = "S-1-5-18"
$script:ExpectedInheritance = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
$script:ExpectedPropagation = [System.Security.AccessControl.PropagationFlags]::None

$nativeSource = @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

namespace Mercury.SecureBackup {
    [StructLayout(LayoutKind.Sequential)] internal struct FILETIME { public uint Low; public uint High; }
    [StructLayout(LayoutKind.Sequential)] internal struct BY_HANDLE_FILE_INFORMATION {
        public uint Attributes; public FILETIME Creation; public FILETIME LastAccess; public FILETIME LastWrite;
        public uint VolumeSerial; public uint SizeHigh; public uint SizeLow; public uint Links;
        public uint FileIndexHigh; public uint FileIndexLow;
    }
    [StructLayout(LayoutKind.Sequential, Pack = 1)] internal struct FILE_DISPOSITION_INFO {
        public byte DeleteFile;
    }
    [StructLayout(LayoutKind.Sequential)] internal struct SECURITY_ATTRIBUTES {
        public int Length; public IntPtr SecurityDescriptor; [MarshalAs(UnmanagedType.Bool)] public bool InheritHandle;
    }
    [StructLayout(LayoutKind.Sequential)] internal struct OVERLAPPED {
        public UIntPtr Internal; public UIntPtr InternalHigh; public uint Offset; public uint OffsetHigh; public IntPtr Event;
    }

    public sealed class BoundPathHandle : IDisposable {
        internal SafeFileHandle Handle;
        public string Path { get; private set; }
        public string Identity { get; private set; }
        public uint Attributes { get; private set; }
        public string FinalPath { get; private set; }
        internal BoundPathHandle(string path, SafeFileHandle handle, string identity, uint attributes, string finalPath) {
            Path = path; Handle = handle; Identity = identity; Attributes = attributes; FinalPath = finalPath;
        }
        public string CurrentIdentity() { return Native.IdentityOfHandle(Handle); }
        public uint CurrentAttributes() { return Native.AttributesOfHandle(Handle); }
        public void Dispose() { if (Handle != null) { Handle.Dispose(); Handle = null; } }
    }

    public sealed class DirectoryChangeGuard : IDisposable {
        private SafeFileHandle handle;
        private IntPtr buffer;
        private IntPtr eventHandle;
        private IntPtr overlapped;
        private bool finished;
        private bool ioStarted;
        private bool disposed;
        private const uint ERROR_IO_PENDING = 997;
        private const uint ERROR_OPERATION_ABORTED = 995;
        private const uint ERROR_NOT_FOUND = 1168;
        private const uint WAIT_OBJECT_0 = 0;
        private const uint WAIT_TIMEOUT = 258;

        internal DirectoryChangeGuard(string path, int faultStep) {
            try {
                handle = NativeMethods.CreateFileW(path, NativeMethods.FILE_LIST_DIRECTORY,
                    NativeMethods.FILE_SHARE_READ | NativeMethods.FILE_SHARE_WRITE | NativeMethods.FILE_SHARE_DELETE,
                    IntPtr.Zero, NativeMethods.OPEN_EXISTING,
                    NativeMethods.FILE_FLAG_BACKUP_SEMANTICS | NativeMethods.FILE_FLAG_OPEN_REPARSE_POINT | NativeMethods.FILE_FLAG_OVERLAPPED,
                    IntPtr.Zero);
                if (handle.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error(), "directory change watcher open failed");
                InjectSetupFault(faultStep, 1);
                eventHandle = NativeMethods.CreateEventW(IntPtr.Zero, true, false, null);
                if (eventHandle == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(), "directory change watcher event creation failed");
                InjectSetupFault(faultStep, 2);
                buffer = Marshal.AllocHGlobal(65536);
                InjectSetupFault(faultStep, 3);
                overlapped = Marshal.AllocHGlobal(Marshal.SizeOf(typeof(OVERLAPPED)));
                Marshal.StructureToPtr(new OVERLAPPED { Event = eventHandle }, overlapped, false);
                InjectSetupFault(faultStep, 4);
                StartIo();
                InjectSetupFault(faultStep, 5);
            }
            catch (Exception setupError) {
                try { ReleaseResources(true); }
                catch (Exception cleanupError) { throw new AggregateException("watcher setup and cleanup both failed", setupError, cleanupError); }
                throw;
            }
        }

        private static void InjectSetupFault(int requested, int current) {
            if (requested == current) throw new IOException("restricted watcher setup fault at step " + current.ToString());
        }

        private void StartIo() {
            if (!NativeMethods.ResetEvent(eventHandle)) throw new Win32Exception(Marshal.GetLastWin32Error(), "directory change watcher event reset failed");
            Marshal.StructureToPtr(new OVERLAPPED { Event = eventHandle }, overlapped, false);
            uint ignored;
            bool started = NativeMethods.ReadDirectoryChangesW(handle, buffer, 65536, true,
                0x00000001 | 0x00000002 | 0x00000004 | 0x00000008 | 0x00000010 | 0x00000040 | 0x00000100,
                out ignored, overlapped, IntPtr.Zero);
            int error = Marshal.GetLastWin32Error();
            if (!started && error != ERROR_IO_PENDING) throw new Win32Exception(error, "directory change watcher setup failed");
            ioStarted = true;
            finished = false;
        }

        private void ValidateProbeBatch(uint bytes, string expectedName, ref bool added, ref bool removed) {
            if (bytes == 0) throw new IOException("directory change watcher overflowed");
            int offset = 0;
            while (true) {
                if (offset < 0 || offset + 12 > bytes) throw new IOException("malformed directory notification");
                int next = Marshal.ReadInt32(buffer, offset);
                int action = Marshal.ReadInt32(buffer, offset + 4);
                int nameBytes = Marshal.ReadInt32(buffer, offset + 8);
                if (nameBytes < 0 || (nameBytes & 1) != 0 || offset + 12 + nameBytes > bytes) throw new IOException("malformed directory notification");
                string name = Marshal.PtrToStringUni(new IntPtr(buffer.ToInt64() + offset + 12), nameBytes / 2);
                if (!String.Equals(name, expectedName, StringComparison.OrdinalIgnoreCase) || (action != 1 && action != 2 && action != 3))
                    throw new IOException("directory change watcher detected a non-probe change: action=" + action.ToString() + ", relativePath=" + name);
                if (action == 1) added = true;
                if (action == 2) removed = true;
                if (next == 0) break;
                if (next < 12 || offset + next >= bytes) throw new IOException("malformed directory notification chain");
                offset += next;
            }
        }

        public void AcknowledgeProbe(string expectedName) {
            if (finished || !ioStarted) throw new InvalidOperationException("directory change watcher is not active");
            DateTime deadline = DateTime.UtcNow.AddSeconds(10);
            bool added = false; bool removed = false;
            while (!removed) {
                if (DateTime.UtcNow >= deadline) throw new IOException("timed out waiting for probe notifications");
                uint remaining = (uint)Math.Max(1, (deadline - DateTime.UtcNow).TotalMilliseconds);
                uint wait = NativeMethods.WaitForSingleObject(eventHandle, remaining);
                if (wait == WAIT_TIMEOUT) throw new IOException("timed out waiting for probe notifications");
                if (wait != WAIT_OBJECT_0) throw new Win32Exception(Marshal.GetLastWin32Error(), "probe notification wait failed");
                uint bytes;
                if (!NativeMethods.GetOverlappedResult(handle, overlapped, out bytes, false))
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "probe notification completion failed");
                ioStarted = false;
                ValidateProbeBatch(bytes, expectedName, ref added, ref removed);
                StartIo();
            }
            if (!added) throw new IOException("probe notification sequence did not include creation");
        }

        public void AssertNoChange() {
            if (finished) throw new InvalidOperationException("directory change watcher already consumed");
            uint wait = NativeMethods.WaitForSingleObject(eventHandle, 0);
            if (wait == WAIT_OBJECT_0) {
                uint bytes;
                if (!NativeMethods.GetOverlappedResult(handle, overlapped, out bytes, false))
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "directory change watcher completion failed");
                finished = true;
                ioStarted = false;
                if (bytes == 0) throw new IOException("directory change watcher overflowed");
                throw new IOException("directory change watcher detected a concurrent tree change: " + DescribeFirstChange(bytes));
            }
            if (wait != WAIT_TIMEOUT) throw new Win32Exception(Marshal.GetLastWin32Error(), "directory change watcher wait failed");
            if (!NativeMethods.CancelIoEx(handle, overlapped)) {
                int cancelError = Marshal.GetLastWin32Error();
                if (cancelError != 1168) throw new Win32Exception(cancelError, "directory change watcher cancellation failed");
            }
            uint finalBytes;
            bool completed = NativeMethods.GetOverlappedResult(handle, overlapped, out finalBytes, true);
            int error = Marshal.GetLastWin32Error();
            finished = true;
            ioStarted = false;
            if (completed) {
                if (finalBytes == 0) throw new IOException("directory change watcher overflowed");
                throw new IOException("directory change watcher detected a concurrent tree change: " + DescribeFirstChange(finalBytes));
            }
            if (error != ERROR_OPERATION_ABORTED) throw new Win32Exception(error, "directory change watcher finalization failed");
        }

        private string DescribeFirstChange(uint bytes) {
            if (bytes < 12) return "malformed notification";
            int action = Marshal.ReadInt32(buffer, 4);
            int nameBytes = Marshal.ReadInt32(buffer, 8);
            if (nameBytes < 0 || 12 + nameBytes > bytes) return "malformed notification";
            string name = Marshal.PtrToStringUni(new IntPtr(buffer.ToInt64() + 12), nameBytes / 2);
            return "action=" + action.ToString() + ", relativePath=" + name;
        }

        private void CompletePendingIo() {
            if (!ioStarted || finished || handle == null || handle.IsInvalid || overlapped == IntPtr.Zero) return;
            int cancelError = 0;
            if (!NativeMethods.CancelIoEx(handle, overlapped)) {
                cancelError = Marshal.GetLastWin32Error();
            }
            uint bytes;
            bool completed = NativeMethods.GetOverlappedResult(handle, overlapped, out bytes, true);
            int completionError = Marshal.GetLastWin32Error();
            ioStarted = false;
            finished = true;
            if (!completed && completionError != (int)ERROR_OPERATION_ABORTED)
                throw new Win32Exception(completionError, "directory change watcher disposal completion failed");
            if (cancelError != 0 && cancelError != (int)ERROR_NOT_FOUND && !completed)
                throw new Win32Exception(cancelError, "directory change watcher disposal cancellation failed");
        }

        private void ReleaseResources(bool reportErrors) {
            Exception completionError = null;
            try { CompletePendingIo(); }
            catch (Exception error) { completionError = error; }
            if (overlapped != IntPtr.Zero) { Marshal.FreeHGlobal(overlapped); overlapped = IntPtr.Zero; }
            if (buffer != IntPtr.Zero) { Marshal.FreeHGlobal(buffer); buffer = IntPtr.Zero; }
            if (eventHandle != IntPtr.Zero) {
                if (!NativeMethods.CloseHandle(eventHandle) && completionError == null)
                    completionError = new Win32Exception(Marshal.GetLastWin32Error(), "directory change watcher event cleanup failed");
                eventHandle = IntPtr.Zero;
            }
            if (handle != null) { handle.Dispose(); handle = null; }
            if (reportErrors && completionError != null) throw completionError;
        }

        public void Dispose() {
            if (disposed) return;
            disposed = true;
            ReleaseResources(true);
        }
    }

    internal static class NativeMethods {
        internal const uint DELETE = 0x00010000;
        internal const uint READ_CONTROL = 0x00020000;
        internal const uint GENERIC_READ = 0x80000000;
        internal const uint GENERIC_WRITE = 0x40000000;
        internal const uint FILE_READ_ATTRIBUTES = 0x00000080;
        internal const uint FILE_LIST_DIRECTORY = 0x00000001;
        internal const uint FILE_SHARE_READ = 0x00000001;
        internal const uint FILE_SHARE_WRITE = 0x00000002;
        internal const uint FILE_SHARE_DELETE = 0x00000004;
        internal const uint CREATE_NEW = 1;
        internal const uint OPEN_EXISTING = 3;
        internal const uint FILE_ATTRIBUTE_TEMPORARY = 0x00000100;
        internal const uint FILE_FLAG_DELETE_ON_CLOSE = 0x04000000;
        internal const uint FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000;
        internal const uint FILE_FLAG_BACKUP_SEMANTICS = 0x02000000;
        internal const uint FILE_FLAG_OVERLAPPED = 0x40000000;
        internal const uint FILE_FLAG_WRITE_THROUGH = 0x80000000;
        internal const int FileDispositionInfo = 4;

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        internal static extern SafeFileHandle CreateFileW(string name, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        internal static extern bool CreateDirectoryW(string path, ref SECURITY_ATTRIBUTES security);
        [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        internal static extern bool EncryptFileW(string path);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool GetFileInformationByHandle(SafeFileHandle handle, out BY_HANDLE_FILE_INFORMATION info);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        internal static extern uint GetFinalPathNameByHandleW(SafeFileHandle handle, StringBuilder path, uint length, uint flags);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool SetFileInformationByHandle(SafeFileHandle handle, int infoClass, ref FILE_DISPOSITION_INFO info, uint size);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        internal static extern IntPtr CreateEventW(IntPtr attributes, bool manualReset, bool initialState, string name);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool ReadDirectoryChangesW(SafeFileHandle directory, IntPtr buffer, uint length, bool subtree, uint filter, out uint bytes, IntPtr overlapped, IntPtr completion);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool GetOverlappedResult(SafeFileHandle handle, IntPtr overlapped, out uint bytes, bool wait);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool CancelIoEx(SafeFileHandle handle, IntPtr overlapped);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool ResetEvent(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool CloseHandle(IntPtr handle);
    }

    public static class Native {
        private static BY_HANDLE_FILE_INFORMATION Info(SafeFileHandle handle) {
            BY_HANDLE_FILE_INFORMATION info;
            if (!NativeMethods.GetFileInformationByHandle(handle, out info)) throw new Win32Exception(Marshal.GetLastWin32Error(), "GetFileInformationByHandle failed");
            return info;
        }
        private static string Id(BY_HANDLE_FILE_INFORMATION info) {
            return info.VolumeSerial.ToString("X8") + ":" + info.FileIndexHigh.ToString("X8") + info.FileIndexLow.ToString("X8");
        }
        private static string FinalPath(SafeFileHandle handle) {
            StringBuilder builder = new StringBuilder(1024);
            uint length = NativeMethods.GetFinalPathNameByHandleW(handle, builder, (uint)builder.Capacity, 0);
            if (length == 0 || length >= builder.Capacity) throw new Win32Exception(Marshal.GetLastWin32Error(), "GetFinalPathNameByHandle failed");
            return builder.ToString();
        }
        public static BoundPathHandle OpenBound(string path) {
            SafeFileHandle handle = NativeMethods.CreateFileW(path,
                NativeMethods.FILE_READ_ATTRIBUTES | NativeMethods.READ_CONTROL,
                NativeMethods.FILE_SHARE_READ | NativeMethods.FILE_SHARE_DELETE,
                IntPtr.Zero, NativeMethods.OPEN_EXISTING,
                NativeMethods.FILE_FLAG_OPEN_REPARSE_POINT | NativeMethods.FILE_FLAG_BACKUP_SEMANTICS,
                IntPtr.Zero);
            if (handle.IsInvalid) {
                int error = Marshal.GetLastWin32Error(); handle.Dispose();
                throw new Win32Exception(error, error == 32 ? "concurrent writer or deleter detected (sharing violation)" : "reparse-safe handle open failed");
            }
            BY_HANDLE_FILE_INFORMATION info = Info(handle);
            return new BoundPathHandle(path, handle, Id(info), info.Attributes, FinalPath(handle));
        }
        public static BoundPathHandle OpenDirectoryAnchor(string path) {
            SafeFileHandle handle = NativeMethods.CreateFileW(path,
                NativeMethods.FILE_LIST_DIRECTORY | NativeMethods.FILE_READ_ATTRIBUTES | NativeMethods.READ_CONTROL | NativeMethods.DELETE,
                NativeMethods.FILE_SHARE_READ,
                IntPtr.Zero, NativeMethods.OPEN_EXISTING,
                NativeMethods.FILE_FLAG_OPEN_REPARSE_POINT | NativeMethods.FILE_FLAG_BACKUP_SEMANTICS,
                IntPtr.Zero);
            if (handle.IsInvalid) {
                int error = Marshal.GetLastWin32Error(); handle.Dispose();
                throw new Win32Exception(error, error == 32 ? "concurrent writer or deleter detected (sharing violation)" : "identity-bound directory anchor open failed");
            }
            BY_HANDLE_FILE_INFORMATION info = Info(handle);
            if ((info.Attributes & 0x10) == 0) { handle.Dispose(); throw new IOException("directory anchor target is not a directory"); }
            return new BoundPathHandle(path, handle, Id(info), info.Attributes, FinalPath(handle));
        }
        public static BoundPathHandle OpenExclusiveBound(string path) {
            SafeFileHandle handle = NativeMethods.CreateFileW(path,
                NativeMethods.FILE_READ_ATTRIBUTES | NativeMethods.READ_CONTROL | NativeMethods.DELETE,
                NativeMethods.FILE_SHARE_READ,
                IntPtr.Zero, NativeMethods.OPEN_EXISTING,
                NativeMethods.FILE_FLAG_OPEN_REPARSE_POINT | NativeMethods.FILE_FLAG_BACKUP_SEMANTICS,
                IntPtr.Zero);
            if (handle.IsInvalid) {
                int error = Marshal.GetLastWin32Error(); handle.Dispose();
                throw new Win32Exception(error, error == 32 ? "concurrent writer or deleter detected (sharing violation)" : "identity-bound exclusive handle open failed");
            }
            BY_HANDLE_FILE_INFORMATION info = Info(handle);
            return new BoundPathHandle(path, handle, Id(info), info.Attributes, FinalPath(handle));
        }
        public static string IdentityOfHandle(SafeFileHandle handle) { return Id(Info(handle)); }
        public static uint AttributesOfHandle(SafeFileHandle handle) { return Info(handle).Attributes; }
        public static SafeFileHandle CreateProbe(string path, bool deleteOnClose) {
            uint flags = NativeMethods.FILE_ATTRIBUTE_TEMPORARY | NativeMethods.FILE_FLAG_OPEN_REPARSE_POINT | NativeMethods.FILE_FLAG_WRITE_THROUGH;
            if (deleteOnClose) flags |= NativeMethods.FILE_FLAG_DELETE_ON_CLOSE;
            SafeFileHandle handle = NativeMethods.CreateFileW(path,
                NativeMethods.GENERIC_READ | NativeMethods.GENERIC_WRITE | NativeMethods.DELETE | NativeMethods.READ_CONTROL | NativeMethods.FILE_READ_ATTRIBUTES,
                NativeMethods.FILE_SHARE_READ, IntPtr.Zero, NativeMethods.CREATE_NEW, flags, IntPtr.Zero);
            if (handle.IsInvalid) { int error = Marshal.GetLastWin32Error(); handle.Dispose(); throw new Win32Exception(error, "CREATE_NEW probe open failed"); }
            return handle;
        }
        public static void MarkDelete(SafeFileHandle handle) {
            FILE_DISPOSITION_INFO disposition = new FILE_DISPOSITION_INFO { DeleteFile = 1 };
            if (!NativeMethods.SetFileInformationByHandle(handle, NativeMethods.FileDispositionInfo, ref disposition, (uint)Marshal.SizeOf(typeof(FILE_DISPOSITION_INFO))))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "identity-bound delete failed");
        }
        public static void DeleteByIdentity(string path, string identity) {
            SafeFileHandle handle = NativeMethods.CreateFileW(path,
                NativeMethods.DELETE | NativeMethods.READ_CONTROL | NativeMethods.FILE_READ_ATTRIBUTES,
                NativeMethods.FILE_SHARE_READ, IntPtr.Zero, NativeMethods.OPEN_EXISTING,
                NativeMethods.FILE_FLAG_OPEN_REPARSE_POINT | NativeMethods.FILE_FLAG_BACKUP_SEMANTICS, IntPtr.Zero);
            if (handle.IsInvalid) { int error = Marshal.GetLastWin32Error(); handle.Dispose(); throw new Win32Exception(error, "identity-bound cleanup open failed"); }
            using (handle) {
                if (IdentityOfHandle(handle) != identity) throw new IOException("cleanup identity mismatch");
                MarkDelete(handle);
            }
            if (File.Exists(path) || Directory.Exists(path)) throw new IOException("identity-bound cleanup did not remove the path");
        }
        public static void CreateDirectorySecure(string path, byte[] descriptor) {
            GCHandle pinned = GCHandle.Alloc(descriptor, GCHandleType.Pinned);
            try {
                SECURITY_ATTRIBUTES security = new SECURITY_ATTRIBUTES {
                    Length = Marshal.SizeOf(typeof(SECURITY_ATTRIBUTES)), SecurityDescriptor = pinned.AddrOfPinnedObject(), InheritHandle = false
                };
                if (!NativeMethods.CreateDirectoryW(path, ref security)) throw new Win32Exception(Marshal.GetLastWin32Error(), "atomic secure directory creation failed");
            }
            finally { pinned.Free(); }
        }
        public static void EncryptPathForMaintenance(string path) {
            if (!NativeMethods.EncryptFileW(path)) throw new Win32Exception(Marshal.GetLastWin32Error(), "EncryptFileW maintenance bootstrap failed");
        }
        public static DirectoryChangeGuard WatchDirectory(string path) { return new DirectoryChangeGuard(path, 0); }
        public static int FileDispositionInfoSize() { return Marshal.SizeOf(typeof(FILE_DISPOSITION_INFO)); }
        public static void StressWatcherLifecycle(string path) {
            int before = System.Diagnostics.Process.GetCurrentProcess().HandleCount;
            for (int iteration = 0; iteration < 100; iteration++) {
                using (DirectoryChangeGuard guard = new DirectoryChangeGuard(path, 0)) { guard.AssertNoChange(); }
            }
            for (int iteration = 0; iteration < 20; iteration++) {
                for (int step = 1; step <= 5; step++) {
                    bool failed = false;
                    try { using (DirectoryChangeGuard ignored = new DirectoryChangeGuard(path, step)) { } }
                    catch (IOException) { failed = true; }
                    if (!failed) throw new InvalidOperationException("watcher setup fault was not observed");
                }
            }
            GC.Collect(); GC.WaitForPendingFinalizers(); GC.Collect();
            int after = System.Diagnostics.Process.GetCurrentProcess().HandleCount;
            if (after > before + 4) throw new IOException("watcher lifecycle leaked native handles: before=" + before.ToString() + ", after=" + after.ToString());
        }
    }
}
'@

if (-not ("Mercury.SecureBackup.Native" -as [type])) {
    Add-Type -TypeDefinition $nativeSource
}
if ([Mercury.SecureBackup.Native]::FileDispositionInfoSize() -ne 1) { throw "FILE_DISPOSITION_INFO ABI mismatch: expected one-byte BOOLEAN." }

function Set-AllowedBase {
    if ([string]::IsNullOrWhiteSpace($TestBase)) {
        $script:AllowedBase = $script:SharedBase
        return
    }
    if ($TestBase.StartsWith("\\\\") -or $TestBase.StartsWith("//") -or $TestBase.StartsWith("\\?\") -or $TestBase.StartsWith("\\.") -or $TestBase -notmatch '^[dD]:\\') {
        throw "TestBase must be an absolute direct .secure-root-tests-* child of the shared backup base."
    }
    if ($TestBase.Substring(2).Contains(":")) { throw "TestBase alternate data streams are not allowed." }
    $full = [System.IO.Path]::GetFullPath($TestBase).TrimEnd("\")
    $parent = [System.IO.Path]::GetDirectoryName($full)
    $leaf = [System.IO.Path]::GetFileName($full)
    $match = [regex]::Match($leaf, '^\.secure-root-tests-base-([0-9]+)-([0-9a-f]{32})$', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    if (-not $parent.Equals($script:SharedBase, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not $match.Success -or
        $full.Equals($script:RealTarget, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "TestBase must use exact .secure-root-tests-base-<pid>-<guid32> syntax as a direct child and cannot overlap the real target."
    }
    if ([string]::IsNullOrWhiteSpace($TestBaseCapability) -or -not $match.Groups[2].Value.Equals($TestBaseCapability, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "TestBase capability does not match the registered suite nonce."
    }
    if (-not [System.IO.Directory]::Exists($full)) { throw "TestBase must already exist as an isolated test fixture." }
    if ([string]::IsNullOrWhiteSpace($TestBaseIdentity)) { throw "TestBase registered identity is required." }
    $observedIdentity = $null
    $registration = Open-IdentityBoundDirectory -LiteralPath $full
    try { $observedIdentity = $registration.Identity }
    finally { $registration.Dispose() }
    if ($observedIdentity -ne $TestBaseIdentity) { throw "TestBase identity does not match its absent-before/create-once registration." }
    $script:AllowedBase = $full
}

function Get-ValidatedDestination {
    param([Parameter(Mandatory = $true)][string]$Candidate)

    if ($Candidate.StartsWith("\\\\") -or $Candidate.StartsWith("//") -or $Candidate.StartsWith("\\?\") -or $Candidate.StartsWith("\\.\")) {
        throw "Path must be an absolute direct child of the allowed backup base; UNC and extended paths are outside the allowed backup base."
    }
    if ($Candidate -notmatch '^[dD]:\\') {
        throw "Path must be an absolute direct child of the allowed backup base."
    }
    if ($Candidate.Substring(2).Contains(":")) {
        throw "Alternate data stream paths are not allowed."
    }

    try { $full = [System.IO.Path]::GetFullPath($Candidate).TrimEnd("\") }
    catch { throw "Path's format is not supported: $($_.Exception.Message)" }
    $parent = [System.IO.Path]::GetDirectoryName($full)
    if (-not $parent -or -not $parent.Equals($script:AllowedBase, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path must be an absolute direct child of the allowed backup base."
    }
    if ([System.IO.Path]::GetFileName($full) -in @(".", "..")) { throw "Path must be an absolute direct child of the allowed backup base." }
    return $full
}

function Assert-TestFaultScope {
    param([string]$Destination)
    if ($TestFault -eq "None") { return }
    $leaf = [System.IO.Path]::GetFileName($Destination)
    if (-not $leaf.StartsWith(".secure-root-tests-", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "TestFault is restricted to unique .secure-root-tests-* destinations."
    }
}

function New-ExactDirectorySecurity {
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner((New-Object System.Security.Principal.SecurityIdentifier($script:CurrentUserSid)))
    foreach ($sid in @($script:CurrentUserSid, $script:SystemSid)) {
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            (New-Object System.Security.Principal.SecurityIdentifier($sid)),
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            $script:ExpectedInheritance,
            $script:ExpectedPropagation,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        $acl.AddAccessRule($rule)
    }
    return $acl
}

function Get-PathAcl {
    param([string]$LiteralPath, [bool]$Directory)
    $sections = [System.Security.AccessControl.AccessControlSections]::Owner -bor [System.Security.AccessControl.AccessControlSections]::Access
    return (Get-Item -LiteralPath $LiteralPath -Force).GetAccessControl($sections)
}

function Get-SidValue {
    param($IdentityReference)
    if ($IdentityReference -is [System.Security.Principal.SecurityIdentifier]) { return $IdentityReference.Value }
    if ($IdentityReference -is [string]) {
        if ($IdentityReference.StartsWith("S-1-", [System.StringComparison]::OrdinalIgnoreCase)) { return $IdentityReference }
        return (New-Object System.Security.Principal.NTAccount($IdentityReference)).Translate([System.Security.Principal.SecurityIdentifier]).Value
    }
    return $IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
}

function Assert-ExactSecurity {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][bool]$Directory,
        [switch]$Root,
        [string]$ExpectedOwnerSid = $script:CurrentUserSid
    )

    $acl = Get-PathAcl -LiteralPath $LiteralPath -Directory $Directory
    $owner = Get-SidValue $acl.Owner
    if ($owner -ne $ExpectedOwnerSid) { throw "Owner SID mismatch at '$LiteralPath': expected '$ExpectedOwnerSid', observed '$owner'." }
    if ($Root -and -not $acl.AreAccessRulesProtected) { throw "DACL is not protected at '$LiteralPath'." }
    $rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
    if ($rules.Count -ne 2) { throw "DACL must contain exactly two ACEs at '$LiteralPath'." }

    $seen = @{}
    $report = @()
    foreach ($rule in $rules) {
        $sid = Get-SidValue $rule.IdentityReference
        if ($sid -notin @($script:CurrentUserSid, $script:SystemSid) -or $seen.ContainsKey($sid)) {
            throw "DACL contains an extra, duplicate, or broad ACE at '$LiteralPath'."
        }
        $seen[$sid] = $true
        if ($rule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow) {
            throw "DACL contains a Deny ACE at '$LiteralPath'."
        }
        if ($rule.FileSystemRights -ne [System.Security.AccessControl.FileSystemRights]::FullControl) {
            throw "DACL ACE is not exact FullControl at '$LiteralPath'."
        }
        $wantedInheritance = if ($Directory) { $script:ExpectedInheritance } else { [System.Security.AccessControl.InheritanceFlags]::None }
        if ($rule.InheritanceFlags -ne $wantedInheritance) {
            throw "DACL InheritanceFlags mismatch at '$LiteralPath': '$($rule.InheritanceFlags)'."
        }
        if ($rule.PropagationFlags -ne $script:ExpectedPropagation) {
            throw "DACL PropagationFlags mismatch at '$LiteralPath': '$($rule.PropagationFlags)'."
        }
        if ($Root -and $rule.IsInherited) { throw "Root DACL ACE must be non-inherited at '$LiteralPath'." }
        $report += [ordered]@{
            Sid = $sid
            Type = $rule.AccessControlType.ToString()
            Rights = $rule.FileSystemRights.ToString()
            IsInherited = [bool]$rule.IsInherited
            InheritanceFlags = $rule.InheritanceFlags.ToString()
            PropagationFlags = $rule.PropagationFlags.ToString()
        }
    }
    return [pscustomobject]@{ OwnerSid = $owner; Access = @($report) }
}

function Convert-FinalPath {
    param([string]$FinalPath)
    if ($FinalPath.StartsWith("\\?\UNC\", [System.StringComparison]::OrdinalIgnoreCase)) { return "\\" + $FinalPath.Substring(8) }
    if ($FinalPath.StartsWith("\\?\", [System.StringComparison]::OrdinalIgnoreCase)) { return $FinalPath.Substring(4) }
    return $FinalPath
}

function Open-IdentityBoundPath {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)
    $bound = [Mercury.SecureBackup.Native]::OpenBound($LiteralPath)
    try {
        if (($bound.Attributes -band 0x400) -ne 0) { throw "Reparse point rejected at '$LiteralPath'." }
        $resolved = (Convert-FinalPath $bound.FinalPath).TrimEnd("\")
        $expected = [System.IO.Path]::GetFullPath($LiteralPath).TrimEnd("\")
        if (-not $resolved.Equals($expected, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Identity-bound final path mismatch at '$LiteralPath'."
        }
        return $bound
    }
    catch { $bound.Dispose(); throw }
}

function Open-IdentityBoundDirectory {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)
    $bound = [Mercury.SecureBackup.Native]::OpenDirectoryAnchor($LiteralPath)
    try {
        if (($bound.Attributes -band 0x400) -ne 0) { throw "Reparse point rejected at '$LiteralPath'." }
        $resolved = (Convert-FinalPath $bound.FinalPath).TrimEnd("\")
        $expected = [System.IO.Path]::GetFullPath($LiteralPath).TrimEnd("\")
        if (-not $resolved.Equals($expected, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Identity-bound final path mismatch at '$LiteralPath'." }
        return $bound
    }
    catch { $bound.Dispose(); throw }
}

function Open-IdentityBoundExclusivePath {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)
    $bound = [Mercury.SecureBackup.Native]::OpenExclusiveBound($LiteralPath)
    try {
        if (($bound.Attributes -band 0x400) -ne 0) { throw "Reparse point rejected at '$LiteralPath'." }
        $resolved = (Convert-FinalPath $bound.FinalPath).TrimEnd("\")
        $expected = [System.IO.Path]::GetFullPath($LiteralPath).TrimEnd("\")
        if (-not $resolved.Equals($expected, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Identity-bound final path mismatch at '$LiteralPath'." }
        return $bound
    }
    catch { $bound.Dispose(); throw }
}

function Assert-IdentityStable {
    param([string]$LiteralPath, [string]$Identity)
    $again = Open-IdentityBoundPath -LiteralPath $LiteralPath
    try {
        if ($again.Identity -ne $Identity) { throw "Object identity changed at '$LiteralPath'." }
    }
    finally { $again.Dispose() }
}

function Assert-Encrypted {
    param([string]$LiteralPath)
    $item = Get-Item -LiteralPath $LiteralPath -Force
    if (-not $item.Attributes.HasFlag([System.IO.FileAttributes]::Encrypted)) {
        throw "EFS verification failed: '$LiteralPath' is not EFS-encrypted."
    }
}

function Test-DirectoryEmpty {
    param([string]$LiteralPath)
    return @([System.IO.Directory]::EnumerateFileSystemEntries($LiteralPath)).Count -eq 0
}

function Ensure-AllowedBase {
    if (-not [System.IO.Directory]::Exists($script:AllowedBase)) {
        throw "The allowed backup base must already exist, be protected, and be EFS-encrypted."
    }
    $drive = New-Object System.IO.DriveInfo(([System.IO.Path]::GetPathRoot($script:AllowedBase)))
    if ($drive.DriveFormat -ne "NTFS") { throw "The allowed backup base must be on NTFS." }

    $base = Open-IdentityBoundDirectory -LiteralPath $script:AllowedBase
    try {
        try {
            Assert-ExactSecurity -LiteralPath $script:AllowedBase -Directory $true -Root | Out-Null
            Assert-Encrypted -LiteralPath $script:AllowedBase
            Assert-IdentityStable -LiteralPath $script:AllowedBase -Identity $base.Identity
        }
        catch { throw "The allowed backup base must already be protected and EFS-encrypted; preserved without mutation. $($_.Exception.Message)" }
        return $base
    }
    catch { $base.Dispose(); throw }
}

function Invoke-Probe {
    param([string]$RootPath)
    $probeName = ".efs-probe-{0}.bin" -f [guid]::NewGuid().ToString("N")
    $probePath = Join-Path $RootPath $probeName
    $deleteOnClose = $TestFault -ne "ProbeCleanupFailure"
    $handle = [Mercury.SecureBackup.Native]::CreateProbe($probePath, $deleteOnClose)
    $stream = $null; $rng = $null; $payload = $null; $readback = $null
    try {
        if ($TestFault -eq "ProbeAfterHandleFailure") { throw "Probe failed immediately after handle acquisition (restricted test fault)." }
        $identity = [Mercury.SecureBackup.Native]::IdentityOfHandle($handle)
        $payload = New-Object byte[] 32
        $readback = New-Object byte[] 32
        $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        $rng.GetBytes($payload)
        $stream = New-Object System.IO.FileStream($handle, [System.IO.FileAccess]::ReadWrite)
        if ($TestFault -eq "ProbeWriteFailure") {
            $stream.Write($payload, 0, 1); $stream.Flush($true)
            throw "Probe write failed after a partial write (restricted test fault)."
        }
        $stream.Write($payload, 0, $payload.Length)
        $stream.Flush($true)
        $stream.Position = 0
        if ($stream.Read($readback, 0, $readback.Length) -ne $readback.Length) { throw "Probe readback was incomplete." }
        for ($index = 0; $index -lt $payload.Length; $index++) {
            if ($payload[$index] -ne $readback[$index]) { throw "Probe readback did not match the written bytes." }
        }
        if ([Mercury.SecureBackup.Native]::IdentityOfHandle($handle) -ne $identity) { throw "Probe handle identity changed." }
        Assert-Encrypted -LiteralPath $probePath
        Assert-ExactSecurity -LiteralPath $probePath -Directory $false | Out-Null
        $pathBound = Open-IdentityBoundPath -LiteralPath $probePath
        try { if ($pathBound.Identity -ne $identity) { throw "Probe path identity does not match its creation handle." } }
        finally { $pathBound.Dispose() }
        if ($TestFault -eq "ProbeDeleteFailure") { throw "Probe deletion failed (restricted test fault)." }
        if ($TestFault -eq "ProbeCleanupFailure") { throw "CLEANUP FAILURE: restricted probe cleanup failure left the identity-bound probe for inspection." }
        [Mercury.SecureBackup.Native]::MarkDelete($handle)
    }
    finally {
        $cleanupErrors = New-Object 'System.Collections.Generic.List[string]'
        foreach ($cleanup in @(
            { if ($TestFault -eq "ProbeCleanupAggregation") { throw "restricted first cleanup step failure" } },
            { if ($stream) { $stream.Dispose() } },
            { if ($rng) { $rng.Dispose() } },
            { if ($payload) { [Array]::Clear($payload, 0, $payload.Length) } },
            { if ($readback) { [Array]::Clear($readback, 0, $readback.Length) } },
            { if ($handle -and -not $handle.IsClosed) { $handle.Dispose() } }
        )) {
            try { & $cleanup }
            catch { $cleanupErrors.Add($_.Exception.Message) }
        }
        if ($cleanupErrors.Count -gt 0) { throw "CLEANUP FAILURE: $($cleanupErrors -join '; ')" }
    }
    if ([System.IO.File]::Exists($probePath)) { throw "CLEANUP FAILURE: probe remains after identity-bound deletion." }
    return $probeName
}

function Sort-OrdinalStrings {
    param([object[]]$Values)
    $copy = [string[]]@($Values | ForEach-Object { [string]$_ })
    [Array]::Sort($copy, [System.StringComparer]::Ordinal)
    return ,$copy
}

function Get-CanonicalTreePass {
    param(
        [string]$RootPath,
        [System.Collections.Generic.List[Mercury.SecureBackup.BoundPathHandle]]$Handles,
        [Mercury.SecureBackup.BoundPathHandle]$RootHandle,
        [switch]$HoldHandles,
        [switch]$InjectOwnerMismatch
    )
    $queue = New-Object System.Collections.Queue
    $queue.Enqueue([pscustomobject]@{ Path = $RootPath; Relative = "."; Depth = 0 })
    $records = @()
    $files = 0; $directories = 0
    while ($queue.Count -gt 0) {
        $entry = $queue.Dequeue()
        $borrowedRoot = $entry.Depth -eq 0 -and $null -ne $RootHandle
        $bound = if ($borrowedRoot) { $RootHandle } elseif ($HoldHandles) { Open-IdentityBoundExclusivePath -LiteralPath $entry.Path } else { Open-IdentityBoundPath -LiteralPath $entry.Path }
        $keep = $false
        try {
            $isDirectory = ($bound.Attributes -band 0x10) -ne 0
            $expectedOwner = if ($InjectOwnerMismatch -and $entry.Depth -ge 2) { $script:SystemSid } else { $script:CurrentUserSid }
            $security = Assert-ExactSecurity -LiteralPath $entry.Path -Directory $isDirectory -Root:($entry.Depth -eq 0) -ExpectedOwnerSid $expectedOwner
            Assert-Encrypted -LiteralPath $entry.Path
            $children = @()
            if ($isDirectory) {
                if ($entry.Depth -gt 0) { $directories++ }
                $childPaths = Sort-OrdinalStrings @([System.IO.Directory]::EnumerateFileSystemEntries($entry.Path))
                foreach ($childPath in $childPaths) {
                    $name = [System.IO.Path]::GetFileName($childPath)
                    $relative = if ($entry.Relative -eq ".") { $name } else { "$($entry.Relative)\$name" }
                    $children += $name
                    $queue.Enqueue([pscustomobject]@{ Path = $childPath; Relative = $relative; Depth = $entry.Depth + 1 })
                }
            }
            else { $files++ }
            $aceLines = foreach ($ace in @($security.Access)) {
                "{0}|{1}|{2}|{3}|{4}|{5}" -f $ace.Sid, $ace.Type, $ace.Rights, ([bool]$ace.IsInherited), $ace.InheritanceFlags, $ace.PropagationFlags
            }
            $record = [ordered]@{
                RelativePath = $entry.Relative
                Identity = $bound.Identity
                Attributes = [uint32]$bound.Attributes
                OwnerSid = $security.OwnerSid
                Access = @(Sort-OrdinalStrings $aceLines)
                Children = @(Sort-OrdinalStrings $children)
            }
            $records += ($record | ConvertTo-Json -Depth 5 -Compress)
            if ($HoldHandles -and -not $borrowedRoot) { $Handles.Add($bound); $keep = $true }
        }
        finally { if (-not $borrowedRoot -and -not $keep) { $bound.Dispose() } }
    }
    [pscustomobject]@{ Lines = @(Sort-OrdinalStrings $records); FileCount = $files; DirectoryCount = $directories }
}

function Get-TreeHash {
    param([string[]]$Lines)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes(($Lines -join "`n"))
        try { return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "") }
        finally { [Array]::Clear($bytes, 0, $bytes.Length) }
    }
    finally { $sha.Dispose() }
}

function Test-SecureTree {
    param([string]$RootPath, [string]$Mode, [Mercury.SecureBackup.BoundPathHandle]$RootHandle, [Mercury.SecureBackup.DirectoryChangeGuard]$Watcher)
    $held = New-Object 'System.Collections.Generic.List[Mercury.SecureBackup.BoundPathHandle]'
    try {
        $initialIdentity = $RootHandle.Identity
        $first = Get-CanonicalTreePass -RootPath $RootPath -Handles $held -RootHandle $RootHandle -HoldHandles -InjectOwnerMismatch:($TestFault -eq "ExpectOwnerMismatch")
        if ($TestFault -eq "VerifyTreeMutation") {
            [System.IO.File]::WriteAllText((Join-Path $RootPath ".secure-root-tests-mutation"), "mutation")
        }
        $unused = New-Object 'System.Collections.Generic.List[Mercury.SecureBackup.BoundPathHandle]'
        $second = Get-CanonicalTreePass -RootPath $RootPath -Handles $unused -RootHandle $RootHandle
        if (($first.Lines -join "`n") -ne ($second.Lines -join "`n")) { throw "Tree changed during verification." }
        $rootSecurity = Assert-ExactSecurity -LiteralPath $RootPath -Directory $true -Root
        if ($RootHandle.CurrentIdentity() -ne $initialIdentity) { throw "Root identity changed during verification." }
        $report = [ordered]@{
            Mode = $Mode
            Path = $RootPath
            OwnerSid = $rootSecurity.OwnerSid
            Access = @($rootSecurity.Access)
            Encrypted = $true
            FileCount = $first.FileCount
            DirectoryCount = $first.DirectoryCount
            RootIdentity = $initialIdentity
            TreeSnapshotHash = Get-TreeHash $first.Lines
        }
        if ($TestFault -eq "PauseAfterFinalVerification") {
            $ready = "$RootPath.final-ready"; $release = "$RootPath.final-release"
            [System.IO.File]::WriteAllText($ready, "ready")
            $deadline = [DateTime]::UtcNow.AddSeconds(20)
            while (-not [System.IO.File]::Exists($release) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 25 }
            if (-not [System.IO.File]::Exists($release)) { throw "Timed out waiting for restricted final-verification release." }
            [System.IO.File]::Delete($release); [System.IO.File]::Delete($ready)
        }
        $Watcher.AssertNoChange()
        return $report
    }
    finally {
        $cleanupErrors = New-Object 'System.Collections.Generic.List[string]'
        foreach ($handle in $held) { try { $handle.Dispose() } catch { $cleanupErrors.Add($_.Exception.Message) } }
        if ($cleanupErrors.Count -gt 0) { throw "CLEANUP FAILURE: $($cleanupErrors -join '; ')" }
    }
}

function Remove-CreatedRoot {
    param([string]$RootPath, [string]$Identity)
    [Mercury.SecureBackup.Native]::DeleteByIdentity($RootPath, $Identity)
}

function Invoke-BaseBootstrap {
    param([string]$BasePath)
    $anchor = $null; $watcher = $null; $createdBase = $false; $initialIdentity = $null; $failure = $null
    try {
        $drive = New-Object System.IO.DriveInfo(([System.IO.Path]::GetPathRoot($BasePath)))
        if ($drive.DriveFormat -ne "NTFS") { throw "Bootstrap base must be on NTFS." }
        if (-not [System.IO.Directory]::Exists($BasePath)) {
            if (-not $MaintenanceWindowConfirmed) { throw "Bootstrap mutation requires an explicit offline maintenance window because the immediate parent may grant DELETE_CHILD." }
            [Mercury.SecureBackup.Native]::CreateDirectorySecure($BasePath, (New-ExactDirectorySecurity).GetSecurityDescriptorBinaryForm())
            $createdBase = $true
        }
        $anchor = Open-IdentityBoundDirectory -LiteralPath $BasePath
        $initialIdentity = $anchor.Identity
        Assert-ExactSecurity -LiteralPath $BasePath -Directory $true -Root | Out-Null
        if (-not (Test-DirectoryEmpty $BasePath)) { throw "Bootstrap base must be empty." }
        $encrypted = ((Get-Item -LiteralPath $BasePath -Force).Attributes -band [System.IO.FileAttributes]::Encrypted) -ne 0
        if (-not $encrypted) {
            $parentProtected = $false
            $parent = [System.IO.Path]::GetDirectoryName($BasePath)
            try { Assert-ExactSecurity -LiteralPath $parent -Directory $true -Root | Out-Null; $parentProtected = $true } catch { $parentProtected = $false }
            if (-not $parentProtected -and -not $MaintenanceWindowConfirmed) {
                throw "Bootstrap encryption requires an offline maintenance window unless the immediate parent denies untrusted DELETE_CHILD."
            }
            $anchor.Dispose(); $anchor = $null
            [Mercury.SecureBackup.Native]::EncryptPathForMaintenance($BasePath)
            $anchor = Open-IdentityBoundDirectory -LiteralPath $BasePath
            if ($anchor.Identity -ne $initialIdentity) { throw "Bootstrap base identity changed across EncryptFileW." }
        }
        Assert-ExactSecurity -LiteralPath $BasePath -Directory $true -Root | Out-Null
        Assert-Encrypted -LiteralPath $BasePath
        if (-not (Test-DirectoryEmpty $BasePath)) { throw "Bootstrap base must remain empty." }
        if ($anchor.CurrentIdentity() -ne $initialIdentity) { throw "Bootstrap base identity changed before probe verification." }
        $watcher = [Mercury.SecureBackup.Native]::WatchDirectory($BasePath)
        $probeName = Invoke-Probe -RootPath $BasePath
        $watcher.AcknowledgeProbe($probeName)
        $report = Test-SecureTree -RootPath $BasePath -Mode "BootstrapBase" -RootHandle $anchor -Watcher $watcher
        if ($report.FileCount -ne 0 -or $report.DirectoryCount -ne 0) { throw "Bootstrap base must be empty at final verification." }
        return $report
    }
    catch { $failure = $_.Exception.Message; throw }
    finally {
        $cleanupErrors = New-Object 'System.Collections.Generic.List[string]'
        foreach ($resource in @($watcher, $anchor)) { if ($resource) { try { $resource.Dispose() } catch { $cleanupErrors.Add($_.Exception.Message) } } }
        if ($failure -and $createdBase -and [System.IO.Directory]::Exists($BasePath)) {
            try {
                if (-not $initialIdentity) {
                    $cleanupAnchor = Open-IdentityBoundDirectory -LiteralPath $BasePath
                    try { $initialIdentity = $cleanupAnchor.Identity } finally { $cleanupAnchor.Dispose() }
                }
                if (-not (Test-DirectoryEmpty $BasePath)) { throw "created bootstrap base is nonempty" }
                Remove-CreatedRoot -RootPath $BasePath -Identity $initialIdentity
            }
            catch { $cleanupErrors.Add($_.Exception.Message) }
        }
        if ($cleanupErrors.Count -gt 0) { throw "CLEANUP FAILURE: $($cleanupErrors -join '; '); original failure: $failure" }
    }
}

Set-AllowedBase
$bootstrapReport = $null
if ($PSCmdlet.ParameterSetName -eq "BootstrapBase") {
    try {
        $bootstrapReport = Invoke-BaseBootstrap -BasePath $script:AllowedBase
        $bootstrapReport | ConvertTo-Json -Depth 6 -Compress
        exit 0
    }
    catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
}
$destination = if ($PSCmdlet.ParameterSetName -eq "VerifyTree") { Get-ValidatedDestination $VerifyTree } else { Get-ValidatedDestination $Path }
Assert-TestFaultScope -Destination $destination
$baseHandle = $null
$rootHandle = $null
$created = $false
$createdIdentity = $null
$primaryFailure = $null
$creationWatcher = $null
$report = $null
$succeeded = $false
try {
    $baseHandle = Ensure-AllowedBase
    if ($PSCmdlet.ParameterSetName -eq "VerifyTree") {
        $rootHandle = Open-IdentityBoundDirectory -LiteralPath $destination
        $createdIdentity = $rootHandle.Identity
        $creationWatcher = [Mercury.SecureBackup.Native]::WatchDirectory($destination)
        $report = Test-SecureTree -RootPath $destination -Mode "VerifyTree" -RootHandle $rootHandle -Watcher $creationWatcher
        $succeeded = $true
    }
    elseif ([System.IO.Directory]::Exists($destination) -or [System.IO.File]::Exists($destination)) {
        $rootHandle = Open-IdentityBoundDirectory -LiteralPath $destination
        $createdIdentity = $rootHandle.Identity
        $creationWatcher = [Mercury.SecureBackup.Native]::WatchDirectory($destination)
        if (($rootHandle.Attributes -band 0x10) -eq 0) { throw "Existing root is not a directory." }
        if (-not (Test-DirectoryEmpty $destination)) { throw "Existing root must be empty." }
        try {
            Assert-ExactSecurity -LiteralPath $destination -Directory $true -Root | Out-Null
            Assert-Encrypted -LiteralPath $destination
            Assert-IdentityStable -LiteralPath $destination -Identity $rootHandle.Identity
        }
        catch { throw "Existing root is not already protected; preserved without mutation. $($_.Exception.Message)" }
        $report = Test-SecureTree -RootPath $destination -Mode "Create" -RootHandle $rootHandle -Watcher $creationWatcher
        if ($report.FileCount -ne 0 -or $report.DirectoryCount -ne 0) { throw "Create mode root must remain empty." }
        $succeeded = $true
    }
    else {
        [Mercury.SecureBackup.Native]::CreateDirectorySecure($destination, (New-ExactDirectorySecurity).GetSecurityDescriptorBinaryForm())
        $created = $true
        $rootHandle = Open-IdentityBoundDirectory -LiteralPath $destination
        $createdIdentity = $rootHandle.Identity
        $creationWatcher = [Mercury.SecureBackup.Native]::WatchDirectory($destination)
        Assert-ExactSecurity -LiteralPath $destination -Directory $true -Root | Out-Null

        if ($TestFault -eq "PauseAfterRootLock") {
            $ready = "$destination.race-ready"; $release = "$destination.race-release"
            [System.IO.File]::WriteAllText($ready, "ready")
            $deadline = [DateTime]::UtcNow.AddSeconds(20)
            while (-not [System.IO.File]::Exists($release) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 25 }
            if (-not [System.IO.File]::Exists($release)) { throw "Timed out waiting for restricted race test release." }
            [System.IO.File]::Delete($release); [System.IO.File]::Delete($ready)
        }

        if ($TestFault -eq "CipherFailure") { throw "EFS enablement failed (restricted inherited-EFS verification fault)." }
        if ($TestFault -eq "CipherTimeout") { throw "EFS enablement timed out (restricted inherited-EFS verification fault)." }
        if ($TestFault -eq "MutableSystemRoot") {
            $savedSystemRoot = $env:SystemRoot
            try {
                $env:SystemRoot = "D:\.secure-root-tests-not-the-system-root"
                $rootAttributes = [System.IO.File]::GetAttributes($destination)
                if (-not $rootAttributes.HasFlag([System.IO.FileAttributes]::Encrypted)) { throw "Inherited EFS verification failed while SystemRoot was modified." }
            }
            finally { $env:SystemRoot = $savedSystemRoot }
        }
        else { Assert-Encrypted -LiteralPath $destination }
        if ($rootHandle.CurrentIdentity() -ne $createdIdentity) { throw "Root identity changed before the probe." }
        if (-not (Test-DirectoryEmpty $destination)) { throw "Create mode root must remain empty before the probe." }
        if ($TestFault -eq "WatcherLifecycleStress") { [Mercury.SecureBackup.Native]::StressWatcherLifecycle($destination) }
        $probeName = Invoke-Probe -RootPath $destination
        $creationWatcher.AcknowledgeProbe($probeName)
        if (-not (Test-DirectoryEmpty $destination)) { throw "Create mode root must remain empty after the probe." }
        if ($rootHandle.CurrentIdentity() -ne $createdIdentity) { throw "Root identity changed after the probe." }
        $report = Test-SecureTree -RootPath $destination -Mode "Create" -RootHandle $rootHandle -Watcher $creationWatcher
        if ($report.FileCount -ne 0 -or $report.DirectoryCount -ne 0) { throw "Create mode root must remain empty at final verification." }
        $succeeded = $true
    }
}
catch {
    $primaryFailure = $_.Exception.Message
}
finally {
    $cleanupErrors = New-Object 'System.Collections.Generic.List[string]'
    foreach ($resourceName in @("creationWatcher", "rootHandle")) {
        $resource = Get-Variable -Name $resourceName -ValueOnly
        if ($resource) { try { $resource.Dispose() } catch { $cleanupErrors.Add("$resourceName cleanup: $($_.Exception.Message)") }; Set-Variable -Name $resourceName -Value $null }
    }
    if (-not $succeeded -and $created -and $createdIdentity -and [System.IO.Directory]::Exists($destination)) {
        try {
            if (-not (Test-DirectoryEmpty $destination)) { throw "created root is not empty after failure" }
            Remove-CreatedRoot -RootPath $destination -Identity $createdIdentity
        }
        catch { $cleanupErrors.Add("root cleanup: $($_.Exception.Message)") }
    }
    if ($baseHandle) { try { $baseHandle.Dispose() } catch { $cleanupErrors.Add("baseHandle cleanup: $($_.Exception.Message)") }; $baseHandle = $null }
    if ($cleanupErrors.Count -gt 0) {
        $cleanupMessage = "CLEANUP FAILURE: $($cleanupErrors -join '; ')"
        if ($primaryFailure) { $primaryFailure = "$cleanupMessage; original failure: $primaryFailure" } else { $primaryFailure = $cleanupMessage; $succeeded = $false }
    }
}

if (-not $succeeded) { [Console]::Error.WriteLine($primaryFailure); exit 1 }
$report | ConvertTo-Json -Depth 6 -Compress
exit 0
