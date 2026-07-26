//! Headless verification hooks. Compiled only with `--features test-hooks`,
//! so nothing here exists in a shipping build.

use serde_json::json;
use tauri::{AppHandle, Emitter};

/// If CLEARWAVE_TEST_DROP is set to a comma-separated list of paths, emit the
/// same `tauri://drag-*` sequence the OS produces when files are dropped on
/// the window. This exercises the real front-end drag-drop handler without
/// needing a desktop session to drive a physical drag.
pub fn maybe_inject_drop(app: AppHandle) {
    let Ok(list) = std::env::var("CLEARWAVE_TEST_DROP") else {
        return;
    };
    let paths: Vec<String> = list
        .split(',')
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect();
    if paths.is_empty() {
        return;
    }

    std::thread::spawn(move || {
        // Give the webview time to register its listeners.
        std::thread::sleep(std::time::Duration::from_millis(2500));
        let position = json!({ "x": 400, "y": 300 });

        let _ = app.emit(
            "tauri://drag-enter",
            json!({ "paths": paths, "position": position }),
        );
        std::thread::sleep(std::time::Duration::from_millis(150));
        let _ = app.emit("tauri://drag-over", json!({ "position": position }));
        std::thread::sleep(std::time::Duration::from_millis(150));
        let _ = app.emit(
            "tauri://drag-drop",
            json!({ "paths": paths, "position": position }),
        );
        eprintln!("[test-hooks] injected drag-drop of {} path(s)", paths.len());
    });
}
