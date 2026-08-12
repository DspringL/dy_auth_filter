// 抖音带货达人系统 — Tauri 主程序
// 职责：管理 Python 后端进程的生命周期

use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::{AppHandle, Emitter, Manager, State};

/// 全局后端进程状态
struct BackendProcess(Mutex<Option<Child>>);

// ── 日志辅助 ──────────────────────────────────────────────────────────────────

fn chrono_now() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let h = (secs % 86400) / 3600;
    let m = (secs % 3600) / 60;
    let s = secs % 60;
    format!("{:02}:{:02}:{:02}", h, m, s)
}

fn append_log(path: &str, msg: &str) -> std::io::Result<()> {
    use std::io::Write;
    let mut f = std::fs::OpenOptions::new()
        .create(true).append(true).open(path)?;
    f.write_all(msg.as_bytes())?;
    Ok(())
}

/// 启动 Python 后端服务
/// 优先使用打包的 sidecar 可执行文件，开发模式下退回 uvicorn
fn start_backend(app: &AppHandle) -> std::io::Result<Child> {
    let resource_path = app.path().resource_dir().unwrap_or_default();

    // Tauri 打包后 sidecar 与主程序同目录（Contents/MacOS/backend-server）
    let exe_dir = std::env::current_exe()
        .unwrap_or_default()
        .parent()
        .unwrap_or(std::path::Path::new("."))
        .to_path_buf();

    // 候选 sidecar 路径（按优先级）
    let candidates = vec![
        exe_dir.join("backend-server"),                              // 打包版：Contents/MacOS/
        resource_path.join("binaries").join(get_sidecar_name()),    // 旧路径
        resource_path.join("binaries").join("backend-server"),      // 无 triple
    ];

    let log_path = "/tmp/douyinkol_startup.log";
    let _ = std::fs::write(log_path, format!(
        "[{ts}] === 应用启动 ===\n\
         [{ts}] exe_dir:      {exe}\n\
         [{ts}] resource_dir: {res}\n",
        ts  = chrono_now(),
        exe = exe_dir.display(),
        res = resource_path.display(),
    ));

    let mut cands_log = String::new();
    for c in &candidates {
        cands_log.push_str(&format!("  {} exists={}\n", c.display(), c.exists()));
    }
    let _ = append_log(log_path, &format!(
        "[{}] sidecar 候选:\n{}", chrono_now(), cands_log
    ));

    let sidecar_path = candidates.into_iter().find(|p| p.exists());

    if let Some(path) = sidecar_path {
        let data_dir = app.path()
            .app_data_dir()
            .unwrap_or(resource_path.clone());

        let _ = append_log(log_path, &format!(
            "[{}] 模式: sidecar\n[{}] sidecar: {}\n[{}] data_dir: {}\n",
            chrono_now(), chrono_now(), path.display(), chrono_now(), data_dir.display()
        ));

        Command::new(&path)
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

        let _ = append_log("/tmp/douyinkol_startup.log", &format!(
            "[{}] 模式: dev uvicorn\n[{}] project_root: {}\n[{}] python: {}\n",
            chrono_now(), chrono_now(), project_root.display(), chrono_now(), python_cmd
        ));

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

/// Tauri 命令：写启动日志到 /tmp（前端调用）
#[tauri::command]
fn write_startup_log(content: String) {
    let _ = append_log("/tmp/douyinkol_startup.log", &content);
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
                    let pid = child.id();
                    println!("后端已启动，PID: {}", pid);
                    let _ = append_log("/tmp/douyinkol_startup.log", &format!(
                        "[{}] ✅ sidecar 启动成功 PID={}\n", chrono_now(), pid
                    ));
                    *state.0.lock().unwrap() = Some(child);
                }
                Err(e) => {
                    eprintln!("后端启动失败: {e}");
                    let _ = append_log("/tmp/douyinkol_startup.log", &format!(
                        "[{}] ❌ sidecar 启动失败: {}\n", chrono_now(), e
                    ));
                }
            }

        // 等待后端就绪后通知前端（另起线程轮询，不阻塞 UI）
            let handle = app_handle.clone();
            std::thread::spawn(move || {
                let log = "/tmp/douyinkol_startup.log";
                let _ = append_log(log, &format!("[{}] 开始轮询后端 HTTP...\n", chrono_now()));
                for i in 0..60 {
                    std::thread::sleep(std::time::Duration::from_millis(1000));
                    match reqwest::blocking::get("http://127.0.0.1:8000/") {
                        Ok(resp) => {
                            let status = resp.status();
                            let _ = append_log(log, &format!(
                                "[{}] 轮询 #{}: HTTP {}\n", chrono_now(), i+1, status
                            ));
                            if status.is_success() {
                                let _ = append_log(log, &format!(
                                    "[{}] ✅ 后端就绪！emit backend-ready\n", chrono_now()
                                ));
                                println!("后端已就绪");
                                let _ = handle.emit("backend-ready", ());
                                break;
                            }
                        }
                        Err(e) => {
                            let _ = append_log(log, &format!(
                                "[{}] 轮询 #{}: 连接失败 {}\n", chrono_now(), i+1, e
                            ));
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
        .invoke_handler(tauri::generate_handler![check_backend, restart_backend, write_startup_log])
        .run(tauri::generate_context!())
        .expect("Tauri 应用运行失败");
}
