// 抖音带货达人系统 — Tauri 主程序
// 职责：管理 Python 后端进程的生命周期

use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use tauri::{AppHandle, Emitter, Manager, State};

/// 全局后端进程状态
#[derive(Default)]
struct BackendState {
    child: Option<Child>,
    last_error: Option<String>,
}

#[derive(Default)]
struct BackendProcess(Mutex<BackendState>);

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct BackendStatus {
    running: bool,
    error: Option<String>,
    log_path: String,
}

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

fn append_log(path: &Path, msg: &str) -> std::io::Result<()> {
    use std::io::Write;
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)?;
    f.write_all(msg.as_bytes())?;
    Ok(())
}

fn startup_log_path(app: &AppHandle) -> PathBuf {
    let log_dir = app
        .path()
        .app_log_dir()
        .or_else(|_| app.path().app_data_dir())
        .unwrap_or_else(|_| std::env::temp_dir());
    let _ = std::fs::create_dir_all(&log_dir);
    log_dir.join("douyinkol_startup.log")
}

fn spawn_logged(mut command: Command, log_path: &Path) -> std::io::Result<Child> {
    let stdout = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_path)?;
    let stderr = stdout.try_clone()?;
    command
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr));

    // 防止 Windows 启动 PyInstaller sidecar 时弹出额外的控制台窗口。
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }

    command.spawn()
}

fn refresh_backend_state(state: &mut BackendState) -> bool {
    match state.child.as_mut().map(Child::try_wait) {
        Some(Ok(None)) => true,
        Some(Ok(Some(status))) => {
            state.last_error = Some(format!("后端进程已退出（{status}）"));
            state.child = None;
            false
        }
        Some(Err(error)) => {
            state.last_error = Some(format!("无法读取后端进程状态：{error}"));
            state.child = None;
            false
        }
        None => false,
    }
}

/// 启动 Python 后端服务
/// 优先使用打包的 sidecar 可执行文件，开发模式下退回 uvicorn
fn start_backend(app: &AppHandle) -> std::io::Result<Child> {
    let resource_path = app.path().resource_dir().unwrap_or_default();
    let log_path = startup_log_path(app);

    // Tauri 打包后 sidecar 与主程序同目录；Windows 文件名带 .exe。
    let exe_dir = std::env::current_exe()
        .unwrap_or_default()
        .parent()
        .unwrap_or(std::path::Path::new("."))
        .to_path_buf();
    let installed_sidecar_name = format!("backend-server{}", std::env::consts::EXE_SUFFIX);

    // 候选 sidecar 路径（按优先级）
    let candidates = vec![
        exe_dir.join(&installed_sidecar_name),
        resource_path.join("binaries").join(get_sidecar_name()),
        resource_path.join("binaries").join(&installed_sidecar_name),
    ];

    let _ = std::fs::write(
        &log_path,
        format!(
            "[{ts}] === 应用启动 ===\n\
         [{ts}] exe_dir:      {exe}\n\
         [{ts}] resource_dir: {res}\n\
         [{ts}] log_path:     {log}\n",
            ts = chrono_now(),
            exe = exe_dir.display(),
            res = resource_path.display(),
            log = log_path.display(),
        ),
    );

    let mut cands_log = String::new();
    for c in &candidates {
        cands_log.push_str(&format!("  {} exists={}\n", c.display(), c.exists()));
    }
    let _ = append_log(
        &log_path,
        &format!("[{}] sidecar 候选:\n{}", chrono_now(), cands_log),
    );

    let sidecar_path = candidates.iter().find(|p| p.exists()).cloned();

    if let Some(path) = sidecar_path {
        let data_dir = app.path().app_data_dir().unwrap_or(resource_path.clone());
        std::fs::create_dir_all(&data_dir)?;

        let _ = append_log(
            &log_path,
            &format!(
                "[{}] 模式: sidecar\n[{}] sidecar: {}\n[{}] data_dir: {}\n",
                chrono_now(),
                chrono_now(),
                path.display(),
                chrono_now(),
                data_dir.display()
            ),
        );

        let mut command = Command::new(&path);
        command.env("DYAUTH_RESOURCE_DIR", &data_dir);
        spawn_logged(command, &log_path)
    } else {
        #[cfg(not(debug_assertions))]
        let searched = candidates
            .iter()
            .map(|path| path.display().to_string())
            .collect::<Vec<_>>()
            .join(", ");

        #[cfg(not(debug_assertions))]
        {
            let message = format!("未找到后端程序；已检查：{searched}");
            let _ = append_log(&log_path, &format!("[{}] ❌ {message}\n", chrono_now()));
            return Err(std::io::Error::new(std::io::ErrorKind::NotFound, message));
        }

        #[cfg(debug_assertions)]
        {
            // 开发模式：从 src-tauri 目录往上找项目根（含 .venv 的目录）
            let src_tauri_dir = std::env::current_dir().unwrap_or_default();
            // current_dir 在 tauri dev 时是 frontend/src-tauri，往上两级是项目根
            let project_root = src_tauri_dir
                .ancestors()
                .find(|p| p.join(".venv").exists())
                .unwrap_or(&src_tauri_dir)
                .to_path_buf();

            // 使用项目虚拟环境；Windows 和 Unix 的虚拟环境目录不同。
            #[cfg(target_os = "windows")]
            let python_bin = project_root.join(".venv/Scripts/python.exe");
            #[cfg(not(target_os = "windows"))]
            let python_bin = project_root.join(".venv/bin/python3");

            #[cfg(target_os = "windows")]
            let fallback_python = PathBuf::from("python");
            #[cfg(not(target_os = "windows"))]
            let fallback_python = PathBuf::from("python3");

            let python_cmd = if python_bin.exists() {
                python_bin
            } else {
                fallback_python
            };

            println!(
                "开发模式：project_root={} python={}",
                project_root.display(),
                python_cmd.display()
            );

            let _ = append_log(
                &log_path,
                &format!(
                    "[{}] 模式: dev uvicorn\n[{}] project_root: {}\n[{}] python: {}\n",
                    chrono_now(),
                    chrono_now(),
                    project_root.display(),
                    chrono_now(),
                    python_cmd.display()
                ),
            );

            let mut command = Command::new(&python_cmd);
            command
                .current_dir(&project_root)
                .args([
                    "-m",
                    "uvicorn",
                    "backend.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8000",
                    "--loop",
                    "asyncio",
                ])
                .env("PYTHONPATH", &project_root);
            spawn_logged(command, &log_path)
        }
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
    let mut guard = state.0.lock().unwrap();
    refresh_backend_state(&mut guard)
}

/// Tauri 命令：返回后端进程、错误和日志路径。
#[tauri::command]
fn get_backend_status(app: AppHandle, state: State<BackendProcess>) -> BackendStatus {
    let mut guard = state.0.lock().unwrap();
    let running = refresh_backend_state(&mut guard);
    BackendStatus {
        running,
        error: guard.last_error.clone(),
        log_path: startup_log_path(&app).display().to_string(),
    }
}

/// Tauri 命令：将前端启动诊断写入系统应用日志目录。
#[tauri::command]
fn write_startup_log(app: AppHandle, content: String) {
    let _ = append_log(&startup_log_path(&app), &content);
}

/// Tauri 命令：重启后端
#[tauri::command]
fn restart_backend(app: AppHandle, state: State<'_, BackendProcess>) -> Result<String, String> {
    {
        let mut guard = state.0.lock().map_err(|e| e.to_string())?;
        guard.last_error = None;
        if let Some(mut child) = guard.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }

    // 等待端口释放
    std::thread::sleep(std::time::Duration::from_secs(1));

    // 启动新进程
    match start_backend(&app) {
        Ok(child) => {
            let mut guard = state.0.lock().map_err(|e| e.to_string())?;
            guard.child = Some(child);
            guard.last_error = None;
            Ok("后端已重启".to_string())
        }
        Err(error) => {
            let message = format!("后端启动失败：{error}");
            if let Ok(mut guard) = state.0.lock() {
                guard.last_error = Some(message.clone());
            }
            Err(message)
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .manage(BackendProcess::default())
        .setup(|app| {
            // 应用启动时拉起后端
            let app_handle = app.handle().clone();
            let state = app.state::<BackendProcess>();
            match start_backend(&app_handle) {
                Ok(child) => {
                    let pid = child.id();
                    println!("后端已启动，PID: {}", pid);
                    let log_path = startup_log_path(&app_handle);
                    let _ = append_log(
                        &log_path,
                        &format!("[{}] ✅ sidecar 启动成功 PID={}\n", chrono_now(), pid),
                    );
                    let mut guard = state.0.lock().unwrap();
                    guard.child = Some(child);
                    guard.last_error = None;
                }
                Err(error) => {
                    let message = format!("后端启动失败：{error}");
                    eprintln!("{message}");
                    let log_path = startup_log_path(&app_handle);
                    let _ = append_log(&log_path, &format!("[{}] ❌ {}\n", chrono_now(), message));
                    state.0.lock().unwrap().last_error = Some(message);
                }
            }

            // 等待后端就绪后通知前端（另起线程轮询，不阻塞 UI）
            let handle = app_handle.clone();
            std::thread::spawn(move || {
                let log = startup_log_path(&handle);
                let _ = append_log(&log, &format!("[{}] 开始轮询后端 HTTP...\n", chrono_now()));
                for i in 0..60 {
                    std::thread::sleep(std::time::Duration::from_millis(1000));

                    let process_error = {
                        let state = handle.state::<BackendProcess>();
                        let mut guard = state.0.lock().unwrap();
                        if refresh_backend_state(&mut guard) {
                            None
                        } else {
                            guard.last_error.clone()
                        }
                    };
                    if let Some(error) = process_error {
                        let _ = append_log(&log, &format!("[{}] ❌ {}\n", chrono_now(), error));
                        let _ = handle.emit("backend-failed", error);
                        break;
                    }

                    match reqwest::blocking::get("http://127.0.0.1:8000/") {
                        Ok(resp) => {
                            let status = resp.status();
                            let _ = append_log(
                                &log,
                                &format!("[{}] 轮询 #{}: HTTP {}\n", chrono_now(), i + 1, status),
                            );
                            if status.is_success() {
                                let _ = append_log(
                                    &log,
                                    &format!(
                                        "[{}] ✅ 后端就绪！emit backend-ready\n",
                                        chrono_now()
                                    ),
                                );
                                println!("后端已就绪");
                                let _ = handle.emit("backend-ready", ());
                                break;
                            }
                        }
                        Err(e) => {
                            let _ = append_log(
                                &log,
                                &format!("[{}] 轮询 #{}: 连接失败 {}\n", chrono_now(), i + 1, e),
                            );
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
                if let Some(mut child) = guard.child.take() {
                    println!("正在关闭后端进程...");
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            check_backend,
            get_backend_status,
            restart_backend,
            write_startup_log
        ])
        .run(tauri::generate_context!())
        .expect("Tauri 应用运行失败");
}
