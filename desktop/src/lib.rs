use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::webview::NewWindowResponse;
use tauri::{AppHandle, Emitter, Manager, Url, WebviewUrl, WebviewWindowBuilder};

const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: u16 = 8080;
const BACKEND_URL: &str = "http://localhost:8080";
const HEALTH_TIMEOUT_SECS: u64 = 30;

/// 页面级兜底脚本：即使以后前端误写 `target="_blank"` 或调用 `window.open`，
/// 也只在当前 webview 内核内跳转，不向 WebView2 发起新窗口请求
/// （未被拦截的新窗口请求在 Windows 上会被系统默认浏览器接管）。
/// 与 Rust 侧的 `on_new_window` 拒绝策略形成双保险。
const IN_APP_NAVIGATION_GUARD: &str = r#"
(function () {
  function isAppUrl(h) {
    try {
      var u = new URL(h, window.location.href);
      if (u.protocol !== 'http:' && u.protocol !== 'https:') return false;
      return u.hostname === 'localhost' ||
             u.hostname === '127.0.0.1' ||
             u.hostname === '[::1]' ||
             u.hostname === 'tauri.localhost';
    } catch (_) { return false; }
  }
  try {
    window.open = function (url) {
      if (url && isAppUrl(url)) window.location.href = String(url);
      return null;
    };
  } catch (_) {}
  document.addEventListener('click', function (event) {
    var el = event.target;
    if (!el || !el.closest) return;
    var anchor = el.closest('a[target="_blank"], a[target="_new"]');
    if (!anchor || !anchor.href) return;
    event.preventDefault();
    event.stopPropagation();
    if (isAppUrl(anchor.href)) window.location.href = anchor.href;
  }, true);
})();
"#;

/// 桌面壳导航白名单：只允许 Tauri 内置兜底页与本机 Go 后端源。
///
/// - `tauri://`：后端未就绪时加载的桌面壳内置 index.html（macOS/Linux 协议）；
/// - `http://tauri.localhost`：同一兜底页在 WebView2 下的虚拟主机映射；
/// - `http(s)://localhost|127.0.0.1:8080`：Go 后端内嵌工作台。
///
/// 返回 `false` 会直接取消导航：外站既不会在应用窗口里打开，也不会被甩给
/// 系统浏览器。
fn is_allowed_navigation(url: &Url) -> bool {
    match url.scheme() {
        "tauri" => true,
        "http" | "https" => match url.host_str() {
            Some("tauri.localhost") => true,
            Some("localhost" | "127.0.0.1" | "[::1]") => {
                url.port().map(|p| p == BACKEND_PORT).unwrap_or(false)
            }
            _ => false,
        },
        _ => false,
    }
}

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

    let mut candidates: Vec<PathBuf> = Vec::new();

    // --- Dev paths (relative to CWD which is desktop/ during `tauri dev`) ---
    candidates.push(PathBuf::from(format!("../api/{}", go_name)));
    candidates.push(PathBuf::from(format!("api/{}", go_name)));
    candidates.push(PathBuf::from(format!("../dist/{}", desk_name)));
    candidates.push(PathBuf::from(format!("dist/{}", desk_name)));

    // --- Production: Tauri resource directory ---
    //
    // `scripts/build_desktop_bundle.py` 装配 desktop/bundle/，tauri.conf.json 的
    // `resources: {"bundle/": ""}` 把内容**扁平**落到 $RESOURCE，故首选
    // <res>/live2d-api.exe。`bin/` 与 `resources/` 是历史/兜底布局：
    // 旧配置用数组形式 `["bin/*"]` 会把文件放进 <res>/bin/ 子目录。
    if let Ok(resource_dir) = app.path().resource_dir() {
        candidates.push(resource_dir.join(go_name));
        candidates.push(resource_dir.join("bin").join(go_name));
        candidates.push(resource_dir.join("resources").join(go_name));
    }

    // --- Production fallback: next to the main executable ---
    if let Ok(exe_path) = std::env::current_exe() {
        if let Some(exe_dir) = exe_path.parent() {
            candidates.push(exe_dir.join(go_name));
            candidates.push(exe_dir.join("bin").join(go_name));
        }
    }

    candidates.into_iter().find(|p| p.exists())
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
                // 三层防护，保证桌面壳在任何操作下都不会拉起系统浏览器：
                // 1) 导航白名单：非本机后端 / 非内置兜底页的跳转一律取消；
                // 2) 新窗口请求一律拒绝（window.open、target=_blank、中键等）；
                // 3) 页面脚本兜底：把新窗口意图改写成当前内核内跳转。
                .on_navigation(is_allowed_navigation)
                .on_new_window(|_url, _features| NewWindowResponse::Deny)
                .initialization_script(IN_APP_NAVIGATION_GUARD)
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
