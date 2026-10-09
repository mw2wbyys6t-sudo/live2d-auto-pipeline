use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Emitter, Manager, WebviewUrl, WebviewWindowBuilder};

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

fn find_go_binary() -> Option<PathBuf> {
    let exe_name = if cfg!(windows) {
        "live2d-api.exe"
    } else {
        "live2d-api"
    };

    let desktop_name = if cfg!(windows) {
        "Live2DMasterAgent.exe"
    } else {
        "Live2DMasterAgent"
    };

    let candidates: Vec<PathBuf> = [
        format!("../api/{}", exe_name),
        format!("api/{}", exe_name),
        format!("../dist/{}", desktop_name),
        format!("dist/{}", desktop_name),
    ]
    .iter()
    .map(|s| PathBuf::from(s))
    .collect();

    for candidate in &candidates {
        if candidate.exists() {
            return Some(candidate.clone());
        }
    }
    None
}

fn spawn_backend() -> Option<Child> {
    let binary = find_go_binary()?;
    Command::new(&binary)
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

    let backend = if already_running {
        None
    } else {
        let child = spawn_backend();
        if child.is_some() {
            wait_for_backend(HEALTH_TIMEOUT_SECS);
        }
        child
    };

    let backend_ok = is_backend_online();

    tauri::Builder::default()
        .manage(AppState {
            backend: Mutex::new(backend),
        })
        .invoke_handler(tauri::generate_handler![check_backend])
        .setup(move |app| {
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
                    "message": "Go API backend not found. Please build it first:\n  cd api && go build -o live2d-api.exe ."
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
