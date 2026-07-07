use std::sync::atomic::{AtomicBool, Ordering};
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager,
};

// Whether the avatar (video) window passes mouse events through to whatever is
// behind it (so it floats over the desktop without blocking clicks).
static CLICK_THROUGH: AtomicBool = AtomicBool::new(false);

// The two floating windows this app manages.
const WINDOWS: [&str; 2] = ["video", "chat"];

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

            // ── tray menu: the only chrome on these frameless windows ──
            let toggle = MenuItem::with_id(app, "toggle", "Avatar click-through: off", true, None::<&str>)?;
            let vis = MenuItem::with_id(app, "vis", "Hide avatar window", true, None::<&str>)?;
            let front = MenuItem::with_id(app, "front", "Bring both to front", true, None::<&str>)?;
            let reload = MenuItem::with_id(app, "reload", "Reload", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&toggle, &vis, &front, &reload, &quit])?;
            let vis_label = vis.clone();

            let toggle_label = toggle.clone();

            let _tray = TrayIconBuilder::with_id("tray")
                .icon(app.default_window_icon().unwrap().clone())
                .tooltip("Ghost Vessel — right-click for menu")
                .menu(&menu)
                .on_menu_event(move |app, event| match event.id.as_ref() {
                    // click-through only on the avatar (video) window — the chat needs clicks
                    "toggle" => {
                        if let Some(w) = app.get_webview_window("video") {
                            let on = !CLICK_THROUGH.load(Ordering::Relaxed);
                            CLICK_THROUGH.store(on, Ordering::Relaxed);
                            let _ = w.set_ignore_cursor_events(on);
                            let _ = toggle_label.set_text(if on {
                                "Avatar click-through: on"
                            } else {
                                "Avatar click-through: off"
                            });
                        }
                    }
                    // show/hide the avatar (video) window
                    "vis" => {
                        if let Some(w) = app.get_webview_window("video") {
                            let hidden = !w.is_visible().unwrap_or(true);
                            if hidden {
                                let _ = w.show();
                                let _ = w.set_focus();
                                let _ = vis_label.set_text("Hide avatar window");
                            } else {
                                let _ = w.hide();
                                let _ = vis_label.set_text("Show avatar window");
                            }
                        }
                    }
                    "front" => {
                        // undo click-through and raise both, so the user can grab them again
                        CLICK_THROUGH.store(false, Ordering::Relaxed);
                        let _ = toggle_label.set_text("Avatar click-through: off");
                        let _ = vis_label.set_text("Hide avatar window");
                        for label in WINDOWS {
                            if let Some(w) = app.get_webview_window(label) {
                                let _ = w.set_ignore_cursor_events(false);
                                let _ = w.show();
                                let _ = w.set_focus();
                            }
                        }
                    }
                    "reload" => {
                        for label in WINDOWS {
                            if let Some(w) = app.get_webview_window(label) {
                                let _ = w.eval("location.reload()");
                            }
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
