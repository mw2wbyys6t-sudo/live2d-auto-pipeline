use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Emitter, Manager, WebviewUrl, WebviewWindowBuilder};

const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: u16 = 8080;
const BACKEND_URL: &str = "http://localhost:8080";
const HEALTH_TIMEOUT_SECS: u64 = 30;

struct AppState {
    backend: Mutex<Option<Child>>,
}

fn backend_addr() -> String {
    format!("{}:{}", BACKEND_HOST, BACKEND_PORT)
}

fn is_backend_online() -> bool {
    TcpStream::connect(backend_addr()).is_ok()
}

fn go_binary_name() -> &'static str {
    if cfg!(windows) {
        "live2d-api.exe"
    } else {
        "live2d-api"
    }
}

fn desktop_binary_name() -> &'static str {
    if cfg!(windows) {
        "Live2DMasterAgent.exe"
    } else {
        "Live2DMasterAgent"
    }
}

/// Search for the Go backend binary in order of likelihood.
///
/// Development mode (running `cargo tauri dev` from `desktop/`):
///   1. ../api/live2d-api(.exe)       — freshly built Go binary
///   2. ../dist/Live2DMasterAgent(.exe) — full desktop build
///
/// Production mode (installed via NSIS / running the bundled exe):
///   3. <resource_dir>/live2d-api(.exe) — Tauri bundles `desktop/bin/*` here
///   4. <exe_dir>/live2d-api(.exe)      — fallback: sibling of the main exe
fn find_go_binary(app: &AppHandle) -> Option<PathBuf> {
    let go_name = go_binary_name();
    let desk_name = desktop_binary_name();

    // --- Dev paths (relative to CWD which is desktop/ during `tauri dev`) ---
    let dev_candidates: Vec<PathBuf> = vec![
        PathBuf::from(format!("../api/{}", go_name)),
        PathBuf::from(format!("api/{}", go_name)),
        PathBuf::from(format!("../dist/{}", desk_name)),
        PathBuf::from(format!("dist/{}", desk_name)),
    ];
    for candidate in &dev_candidates {
        if candidate.exists() {
            return Some(candidate.clone());
        }
    }

    // --- Production: Tauri resource directory ---
    if let Ok(resource_dir) = app.path().resource_dir() {
        let p = resource_dir.join(go_name);
        if p.exists() {
            return Some(p);
        }
    }

    // --- Production fallback: next to the main executable ---
    if let Ok(exe_path) = std::env::current_exe() {
        if let Some(exe_dir) = exe_path.parent() {
            let p = exe_dir.join(go_name);
            if p.exists() {
                return Some(p);
            }
        }
    }

    None
}

fn spawn_backend(app: &AppHandle) -> Option<Child> {
    let binary = find_go_binary(app)?;
    // 桌面形态的后端只服务本机 webview：默认显式绑回环地址。
    // Go 二进制自身默认 0.0.0.0，若不传参会把生图/文件/桌宠 API 暴露到整个局域网。
    // 排障确需局域网访问时，可设环境变量 LIVE2D_DESKTOP_BACKEND_HOST=0.0.0.0 覆盖。
    let host = std::env::var("LIVE2D_DESKTOP_BACKEND_HOST")
        .ok()
        .filter(|v| !v.trim().is_empty())
        .unwrap_or_else(|| BACKEND_HOST.to_string());
    Command::new(&binary)
        .arg("-host")
        .arg(host)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .ok()
}

fn wait_for_backend(timeout_secs: u64) -> bool {
    let start = Instant::now();
    let timeout = Duration::from_secs(timeout_secs);
    let addr = backend_addr();

    while start.elapsed() < timeout {
        if TcpStream::connect(&addr).is_ok() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(300));
    }
    false
}

fn kill_backend(state: &AppState) {
    let mut guard = state.backend.lock().unwrap();
    if let Some(child) = guard.as_mut() {
        let _ = child.kill();
        let _ = child.wait();
    }
    *guard = None;
}

#[tauri::command]
fn check_backend() -> bool {
    is_backend_online()
}

pub fn run() {
    let already_running = is_backend_online();

    tauri::Builder::default()
        .manage(AppState {
            backend: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![check_backend])
        .setup(move |app| {
            let backend_ok = if already_running {
                true
            } else {
                let child = spawn_backend(app.handle());
                if child.is_some() {
                    wait_for_backend(HEALTH_TIMEOUT_SECS);
                }
                let state: tauri::State<AppState> = app.state();
                *state.backend.lock().unwrap() = child;
                is_backend_online()
            };

            let window = if backend_ok {
                WebviewWindowBuilder::new(
                    app,
                    "main",
                    WebviewUrl::External(BACKEND_URL.parse().unwrap()),
                )
            } else {
                WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
            };

            window
                .title("Live2D Master Agent")
                .inner_size(1280.0, 800.0)
                .min_inner_size(800.0, 600.0)
                .center()
                .build()?;

            if !backend_ok {
                let _ = app.emit("backend-error", serde_json::json!({
                    "message": "Go API backend not found.\n\nDevelopment:\n  cd api && go build -o live2d-api.exe .\n\nRelease:\n  Run scripts\\build_tauri_release.bat"
                }));
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                if window.label() == "main" {
                    let state: tauri::State<AppState> = window.app_handle().state();
                    kill_backend(&state);
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error building tauri application")
        .run(|app_handle, event| {
            if let tauri::RunEvent::Exit = event {
                let state: tauri::State<AppState> = app_handle.state();
                kill_backend(&state);
            }
        });
}
