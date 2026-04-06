#[cfg(target_os = "windows")]
use std::ffi::c_void;
#[cfg(target_os = "windows")]
use std::io;
#[cfg(target_os = "windows")]
use std::mem::{size_of, zeroed};
#[cfg(target_os = "windows")]
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle, RawHandle};
#[cfg(target_os = "windows")]
use tokio::process::Child;
#[cfg(target_os = "windows")]
use windows_sys::Win32::Foundation::HANDLE;
#[cfg(target_os = "windows")]
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};

/// Windows Job Object wrapper that terminates the assigned process tree when the
/// job handle is closed. This protects against orphaned sidecar/CLI children when
/// the GUI or dev terminal is closed abruptly.
///
/// # Known race window
/// The current spawn flow calls [`ProcessJob::assign`] *after* `Command::spawn()`,
/// leaving a brief window where the child process is not yet tracked by the job.
/// An abrupt GUI termination or panic in that window may produce an orphan.
/// The `taskkill /T /F` fallback in `SidecarManager::shutdown` provides
/// best-effort cleanup but does not make the assignment atomic. A proper fix
/// (`CREATE_SUSPENDED` + `ResumeThread`) is tracked in Issue #107; this
/// behaviour is intentionally left as-is for now.
#[cfg(target_os = "windows")]
pub struct ProcessJob {
    handle: OwnedHandle,
}

#[cfg(target_os = "windows")]
impl ProcessJob {
    pub fn new_kill_on_close() -> io::Result<Self> {
        unsafe {
            let raw = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if raw.is_null() {
                return Err(io::Error::last_os_error());
            }

            let handle = OwnedHandle::from_raw_handle(raw as RawHandle);
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = zeroed();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;

            if SetInformationJobObject(
                handle.as_raw_handle() as HANDLE,
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const c_void,
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            ) == 0
            {
                return Err(io::Error::last_os_error());
            }

            Ok(Self { handle })
        }
    }

    pub fn assign(&self, child: &Child) -> io::Result<()> {
        let process_handle = child.raw_handle().ok_or_else(|| {
            io::Error::new(io::ErrorKind::Other, "Child process handle is unavailable")
        })?;

        unsafe {
            if AssignProcessToJobObject(
                self.handle.as_raw_handle() as HANDLE,
                process_handle as HANDLE,
            ) == 0
            {
                return Err(io::Error::last_os_error());
            }
        }

        Ok(())
    }
}
