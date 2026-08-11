// 抖音带货达人系统 — Tauri 主程序
// 职责：管理 Python 后端进程的生命周期

use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::{AppHandle, Emitter, Manager, State};

/// 全局后端进程状态
struct BackendProcess(Mutex<Option<Child>>);

/// 启动 Python 后端服务
/// 优先使用打包的 sidecar 可执行文件，开发模式下退回 uvicorn
fn start_backend(app: &AppHandle) -> std::io::Result<Child> {
    let resource_path = app.path().resource_dir().unwrap_or_default();
    let sidecar_path = resource_path.join("binaries").join(get_sidecar_name());

    if sidecar_path.exists() {
        // 生产模式：运行打包的可执行文件
        // DYAUTH_RESOURCE_DIR 指向 app 数据目录，确保数据库和配置写到正确位置
        let data_dir = app.path()
            .app_data_dir()
            .unwrap_or(resource_path.clone());
        Command::new(sidecar_path)
            .env("DYAUTH_RESOURCE_DIR", data_dir.to_str().unwrap_or("."))
            .spawn()
    } else {
        // 开发模式：从 src-tauri 目录往上找项目根（含 .venv 的目录）
        let src_tauri_dir = std::env::current_dir().unwrap_or_default();
        // current_dir 在 tauri dev 时是 frontend/src-tauri，往上两级是项目根
        let project_root = src_tauri_dir
            .ancestors()
            .find(|p| p.join(".venv").exists())
            .unwrap_or(&src_tauri_dir)
            .to_path_buf();

        // 用 .venv 里的 python3 以 -m uvicorn 方式启动，避免全局 uvicorn shebang 问题
        let python_bin = project_root.join(".venv/bin/python3");
        let python_cmd = if python_bin.exists() {
            python_bin.to_str().unwrap_or("python3").to_string()
        } else {
            "python3".to_string()
        };

        println!("开发模式：project_root={} python={}", project_root.display(), python_cmd);

        Command::new(&python_cmd)
            .current_dir(&project_root)
            .args([
                "-m", "uvicorn",
                "backend.main:app",
                "--host", "127.0.0.1",
                "--port", "8000",
                "--loop", "asyncio",
            ])
            .env("PYTHONPATH", project_root.to_str().unwrap_or("."))
            .spawn()
    }
}

/// 根据当前平台返回 sidecar 可执行文件名
fn get_sidecar_name() -> String {
    #[cfg(target_os = "windows")]
    return "backend-server-x86_64-pc-windows-msvc.exe".to_string();
    #[cfg(target_os = "macos")]
    {
        #[cfg(target_arch = "aarch64")]
        return "backend-server-aarch64-apple-darwin".to_string();
        #[cfg(target_arch = "x86_64")]
        return "backend-server-x86_64-apple-darwin".to_string();
    }
    #[cfg(target_os = "linux")]
    return "backend-server-x86_64-unknown-linux-gnu".to_string();
}

/// Tauri 命令：获取后端状态
#[tauri::command]
fn check_backend(state: State<BackendProcess>) -> bool {
    let guard = state.0.lock().unwrap();
    guard.is_some()
}

/// Tauri 命令：重启后端
#[tauri::command]
fn restart_backend(
    app: AppHandle,
    state: State<'_, BackendProcess>,
) -> Result<String, String> {
    let mut guard = state.0.lock().map_err(|e| e.to_string())?;

    // 停止旧进程
    if let Some(mut child) = guard.take() {
        let _ = child.kill();
        let _ = child.wait();
    }

    // 等待端口释放
    std::thread::sleep(std::time::Duration::from_secs(1));

    // 启动新进程
    match start_backend(&app) {
        Ok(child) => {
            *guard = Some(child);
            Ok("后端已重启".to_string())
        }
        Err(e) => Err(format!("后端启动失败: {e}")),
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .manage(BackendProcess(Mutex::new(None)))
        .setup(|app| {
            // 应用启动时拉起后端
            let app_handle = app.handle().clone();
            let state = app.state::<BackendProcess>();
            match start_backend(&app_handle) {
                Ok(child) => {
                    println!("后端已启动，PID: {}", child.id());
                    *state.0.lock().unwrap() = Some(child);
                }
                Err(e) => {
                    eprintln!("后端启动失败: {e}");
                    // 即使后端启动失败，窗口仍然打开（可手动重试）
                }
            }

        // 等待后端就绪后通知前端（另起线程轮询，不阻塞 UI）
            let handle = app_handle.clone();
            std::thread::spawn(move || {
                for _ in 0..60 {
                    std::thread::sleep(std::time::Duration::from_millis(1000));
                    if let Ok(resp) = reqwest::blocking::get("http://127.0.0.1:8000/") {
                        if resp.status().is_success() {
                            println!("后端已就绪");
                            let _ = handle.emit("backend-ready", ());
                            break;
                        }
                    }
                }
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            // 窗口关闭时终止后端进程
            if let tauri::WindowEvent::Destroyed = event {
                let state = window.state::<BackendProcess>();
                let mut guard = state.0.lock().unwrap();
                if let Some(mut child) = guard.take() {
                    println!("正在关闭后端进程...");
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![check_backend, restart_backend])
        .run(tauri::generate_context!())
        .expect("Tauri 应用运行失败");
}
