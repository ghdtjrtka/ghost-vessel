use std::sync::atomic::{AtomicBool, Ordering};
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager,
};

// Whether the window currently passes mouse events through to whatever is behind
// it (so the avatar floats over the desktop without blocking clicks).
static CLICK_THROUGH: AtomicBool = AtomicBool::new(false);

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            // ── tray menu: the only chrome on this frameless window ──
            let toggle = MenuItem::with_id(app, "toggle", "Click-through: off", true, None::<&str>)?;
            let front = MenuItem::with_id(app, "front", "Bring to front", true, None::<&str>)?;
            let reload = MenuItem::with_id(app, "reload", "Reload", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&toggle, &front, &reload, &quit])?;

            // clone we can mutate the label on from inside the event closure
            let toggle_label = toggle.clone();

            let _tray = TrayIconBuilder::with_id("tray")
                .icon(app.default_window_icon().unwrap().clone())
                .tooltip("Ghost Vessel — right-click for menu")
                .menu(&menu)
                .on_menu_event(move |app, event| match event.id.as_ref() {
                    "toggle" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let on = !CLICK_THROUGH.load(Ordering::Relaxed);
                            CLICK_THROUGH.store(on, Ordering::Relaxed);
                            let _ = w.set_ignore_cursor_events(on);
                            let _ = toggle_label
                                .set_text(if on { "Click-through: on" } else { "Click-through: off" });
                        }
                    }
                    "front" => {
                        // undo click-through and raise, so the user can grab the window again
                        CLICK_THROUGH.store(false, Ordering::Relaxed);
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.set_ignore_cursor_events(false);
                            let _ = toggle_label.set_text("Click-through: off");
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                    "reload" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.eval("location.reload()");
                        }
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .build(app)?;

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
