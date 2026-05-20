// Learn more about Tauri commands at https://tauri.app/develop/calling-rust/
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
};

mod data;

#[tauri::command]
fn greet(name: &str) -> String {
    format!("Hello, {}! You've been greeted from Rust!", name)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default();

    // Single-instance lock: registered FIRST so a second launch is caught
    // before any other plugin or setup runs (Tauri plugin ordering requirement).
    // When a second instance attempts to launch, this closure receives the
    // args + cwd of that second instance; we do nothing for now (placeholder).
    // TODO (#416): bring the existing window to the foreground here.
    #[cfg(any(target_os = "macos", windows, target_os = "linux"))]
    let builder = builder.plugin(tauri_plugin_single_instance::init(
        |_app, _args, _cwd| {
            // Focus-on-relaunch is Phase 2 UI work (#416).
            // For now the second launch is silently rejected.
        },
    ));

    builder
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            greet,
            data::commands::read_jobs,
            data::commands::read_roster,
            data::commands::read_lanes,
            data::commands::read_jobs_by_lane,
        ])
        .setup(|app| {
            // System tray: "Quit Mercury" menu item.
            // Uses the app's default window icon (bundled in src-tauri/icons/).
            // show_menu_on_left_click keeps the UX simple for the MVP.
            let icon = app
                .default_window_icon()
                .ok_or("default_window_icon missing from tauri.conf.json bundle.icon[]")?
                .clone();
            let quit_item =
                MenuItem::with_id(app, "quit", "Quit Mercury", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&quit_item])?;
            let _tray = TrayIconBuilder::new()
                .icon(icon)
                .menu(&menu)
                .tooltip("Mercury")
                .show_menu_on_left_click(true)
                .on_menu_event(|app, event| {
                    if event.id.as_ref() == "quit" {
                        app.exit(0);
                    }
                })
                .build(app)?;

            // Spawn the read-side file-watcher (#414). Non-fatal if it fails —
            // commands still work, just no live "data-changed" events.
            if let Err(e) = data::watcher::start(app.handle().clone()) {
                eprintln!(
                    "[mercury-gui] data watcher start failed: {}",
                    data::redact_home(&e.to_string())
                );
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
