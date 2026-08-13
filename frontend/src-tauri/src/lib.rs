// 抖音带货达人系统 — Tauri 主程序
// 职责：管理 Python 后端进程的生命周期

use std::collections::{HashMap, HashSet};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tauri::{AppHandle, Emitter, Manager, State};

/// 全局后端进程状态
#[derive(Default)]
struct BackendState {
    child: Option<Child>,
    last_error: Option<String>,
    shutdown_token: Option<String>,
    browser_profile: Option<PathBuf>,
    shutting_down: bool,
}

struct SpawnedBackend {
    child: Child,
    browser_profile: PathBuf,
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

fn captcha_screenshot_dir(app: &AppHandle) -> PathBuf {
    app.path()
        .download_dir()
        .unwrap_or_else(|_| std::env::temp_dir())
        .join("DouyinKOL验证码")
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
        // CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        command.creation_flags(0x08000200);
    }

    // 后端及它启动的 CloakBrowser 进入独立进程组。正常退出会先通过 HTTP
    // 让 Python 关闭浏览器；若后端卡死，再按进程组兜底清理全部后代。
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }

    command.spawn()
}

fn new_shutdown_token() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    format!("{}-{nanos:x}", std::process::id())
}

fn backend_port_in_use() -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], 8000));
    TcpStream::connect_timeout(&address, Duration::from_millis(150)).is_ok()
}

#[cfg(target_os = "windows")]
fn wait_for_exit(child: &mut Child, timeout: Duration) -> bool {
    let started = std::time::Instant::now();
    while started.elapsed() < timeout {
        match child.try_wait() {
            Ok(Some(_)) => return true,
            Ok(None) => std::thread::sleep(Duration::from_millis(100)),
            Err(_) => return false,
        }
    }
    false
}

#[cfg(unix)]
fn process_exists(pid: u32) -> bool {
    Command::new("/bin/kill")
        .args(["-0", &pid.to_string()])
        .status()
        .is_ok_and(|status| status.success())
}

#[cfg(unix)]
fn descendant_pids(root_pid: u32) -> Vec<u32> {
    let Ok(output) = Command::new("/bin/ps")
        .args(["-axo", "pid=,ppid="])
        .output()
    else {
        return Vec::new();
    };

    let mut children: HashMap<u32, Vec<u32>> = HashMap::new();
    for line in String::from_utf8_lossy(&output.stdout).lines() {
        let mut fields = line.split_whitespace();
        let (Some(pid), Some(ppid)) = (fields.next(), fields.next()) else {
            continue;
        };
        let (Ok(pid), Ok(ppid)) = (pid.parse::<u32>(), ppid.parse::<u32>()) else {
            continue;
        };
        children.entry(ppid).or_default().push(pid);
    }

    let mut descendants = Vec::new();
    let mut seen = HashSet::new();
    let mut pending = vec![root_pid];
    while let Some(parent) = pending.pop() {
        for &pid in children.get(&parent).into_iter().flatten() {
            if seen.insert(pid) {
                descendants.push(pid);
                pending.push(pid);
            }
        }
    }
    descendants
}

#[cfg(unix)]
fn signal_process(pid: u32, signal: &str) {
    let _ = Command::new("/bin/kill")
        .args([signal, &pid.to_string()])
        .status();
}

#[cfg(unix)]
fn lsof_command() -> &'static str {
    #[cfg(target_os = "macos")]
    return "/usr/sbin/lsof";
    #[cfg(not(target_os = "macos"))]
    return "/usr/bin/lsof";
}

#[cfg(unix)]
fn process_parent_and_command(pid: u32) -> Option<(u32, String)> {
    let output = Command::new("/bin/ps")
        .args(["-p", &pid.to_string(), "-o", "ppid=,command="])
        .output()
        .ok()?;
    let line = String::from_utf8_lossy(&output.stdout);
    let mut fields = line.trim().splitn(2, char::is_whitespace);
    let parent = fields.next()?.parse::<u32>().ok()?;
    let command = fields.next().unwrap_or_default().trim().to_string();
    Some((parent, command))
}

#[cfg(unix)]
fn backend_tree_root(mut pid: u32) -> u32 {
    loop {
        let Some((parent, _)) = process_parent_and_command(pid) else {
            return pid;
        };
        let Some((_, parent_command)) = process_parent_and_command(parent) else {
            return pid;
        };
        if is_backend_command(&parent_command) {
            pid = parent;
        } else {
            return pid;
        }
    }
}

#[cfg(unix)]
fn terminate_pid_tree(pid: u32) {
    let descendants = descendant_pids(pid);
    for &descendant in descendants.iter().rev() {
        signal_process(descendant, "-TERM");
    }
    signal_process(pid, "-TERM");

    let started = std::time::Instant::now();
    while started.elapsed() < Duration::from_secs(2)
        && (process_exists(pid) || descendants.iter().any(|&pid| process_exists(pid)))
    {
        std::thread::sleep(Duration::from_millis(100));
    }

    for &descendant in descendants.iter().rev() {
        if process_exists(descendant) {
            signal_process(descendant, "-KILL");
        }
    }
    if process_exists(pid) {
        signal_process(pid, "-KILL");
    }
}

fn is_backend_command(command: &str) -> bool {
    command.contains("backend-server")
        || (command.contains("uvicorn") && command.contains("backend.main:app"))
}

#[cfg(unix)]
fn cleanup_backend_listeners(log_path: &Path) {
    let Ok(output) = Command::new(lsof_command())
        .args(["-nP", "-t", "-iTCP:8000", "-sTCP:LISTEN"])
        .output()
    else {
        return;
    };

    let mut targets = HashSet::new();
    for value in String::from_utf8_lossy(&output.stdout).lines() {
        let Ok(pid) = value.trim().parse::<u32>() else {
            continue;
        };
        let Some((_, command)) = process_parent_and_command(pid) else {
            continue;
        };
        if is_backend_command(&command) {
            let root_pid = backend_tree_root(pid);
            targets.insert(root_pid);
            let _ = append_log(
                log_path,
                &format!(
                    "[{}] 检测到旧后端监听 PID={pid}，根 PID={root_pid}，正在清理\n",
                    chrono_now()
                ),
            );
        }
    }

    for pid in targets {
        terminate_pid_tree(pid);
    }
}

#[cfg(unix)]
fn cleanup_frontend_listeners(log_path: &Path) {
    let Ok(output) = Command::new(lsof_command())
        .args(["-nP", "-t", "-iTCP:5173", "-sTCP:LISTEN"])
        .output()
    else {
        return;
    };

    for value in String::from_utf8_lossy(&output.stdout).lines() {
        let Ok(pid) = value.trim().parse::<u32>() else {
            continue;
        };
        let Some((_, command)) = process_parent_and_command(pid) else {
            continue;
        };
        if command.contains("vite") {
            let _ = append_log(
                log_path,
                &format!("[{}] 检测到旧前端 PID={pid}，正在清理\n", chrono_now()),
            );
            terminate_pid_tree(pid);
        }
    }
}

#[cfg(target_os = "windows")]
fn cleanup_backend_listeners(log_path: &Path) {
    use std::os::windows::process::CommandExt;

    let script = "$targets = Get-NetTCPConnection -LocalPort 8000 -State Listen \
        -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; \
        foreach ($targetPid in $targets) { \
          $process = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $targetPid) \
            -ErrorAction SilentlyContinue; \
          $command = [string]$process.CommandLine; \
          if ($command.Contains('backend-server') -or \
              ($command.Contains('uvicorn') -and $command.Contains('backend.main:app'))) { \
            taskkill /PID $targetPid /T /F | Out-Null \
          } \
        }";
    let status = Command::new("powershell")
        .args(["-NoProfile", "-NonInteractive", "-Command", script])
        .creation_flags(0x08000000)
        .status();
    let _ = append_log(
        log_path,
        &format!("[{}] Windows 旧后端扫描完成：{:?}\n", chrono_now(), status),
    );
}

#[cfg(target_os = "windows")]
fn cleanup_frontend_listeners(log_path: &Path) {
    use std::os::windows::process::CommandExt;

    let script = "$targets = Get-NetTCPConnection -LocalPort 5173 -State Listen \
        -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; \
        foreach ($targetPid in $targets) { \
          $process = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $targetPid) \
            -ErrorAction SilentlyContinue; \
          if ([string]$process.CommandLine -match 'vite') { \
            taskkill /PID $targetPid /T /F | Out-Null \
          } \
        }";
    let status = Command::new("powershell")
        .args(["-NoProfile", "-NonInteractive", "-Command", script])
        .creation_flags(0x08000000)
        .status();
    let _ = append_log(
        log_path,
        &format!("[{}] Windows 旧前端扫描完成：{:?}\n", chrono_now(), status),
    );
}

fn wait_for_backend_port_release(timeout: Duration) -> bool {
    let started = std::time::Instant::now();
    let mut consecutive_free_checks = 0;
    while started.elapsed() < timeout {
        if !backend_port_in_use() {
            consecutive_free_checks += 1;
            if consecutive_free_checks >= 5 {
                return true;
            }
        } else {
            consecutive_free_checks = 0;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    !backend_port_in_use()
}

fn cleanup_previous_instances(app: &AppHandle) -> std::io::Result<()> {
    let log_path = startup_log_path(app);
    cleanup_backend_listeners(&log_path);
    cleanup_frontend_listeners(&log_path);

    if let Ok(data_dir) = app.path().app_data_dir() {
        terminate_profile_browsers(&data_dir.join("data/browser_profile"));
    }

    #[cfg(debug_assertions)]
    {
        let current_dir = std::env::current_dir().unwrap_or_default();
        if let Some(project_root) = current_dir
            .ancestors()
            .find(|path| path.join(".venv").exists())
        {
            terminate_profile_browsers(&project_root.join("data/browser_profile"));
        }
    }

    if wait_for_backend_port_release(Duration::from_secs(4)) {
        Ok(())
    } else {
        let message = "端口 8000 被非本应用进程占用，无法安全启动后端";
        let _ = append_log(&log_path, &format!("[{}] {message}\n", chrono_now()));
        Err(std::io::Error::new(std::io::ErrorKind::AddrInUse, message))
    }
}

fn terminate_process_tree(child: &mut Child) {
    let pid = child.id();

    #[cfg(unix)]
    {
        // Chromium 会建立自己的进程组，因此必须在父进程退出、子进程被
        // init 接管前先快照整棵进程树，并从叶子到根逐一终止。
        let descendants = descendant_pids(pid);
        for &descendant in descendants.iter().rev() {
            signal_process(descendant, "-TERM");
        }
        signal_process(pid, "-TERM");

        let started = std::time::Instant::now();
        while started.elapsed() < Duration::from_secs(2) {
            let root_running = matches!(child.try_wait(), Ok(None));
            let descendants_running = descendants.iter().any(|&pid| process_exists(pid));
            if !root_running && !descendants_running {
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }

        for &descendant in descendants.iter().rev() {
            if process_exists(descendant) {
                signal_process(descendant, "-KILL");
            }
        }
        if matches!(child.try_wait(), Ok(None)) {
            signal_process(pid, "-KILL");
        }
        let _ = child.wait();
    }

    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;

        let _ = Command::new("taskkill")
            .args(["/PID", &pid.to_string(), "/T", "/F"])
            .creation_flags(0x08000000)
            .status();
        if !wait_for_exit(child, Duration::from_secs(2)) {
            let _ = child.kill();
        }
        let _ = child.wait();
    }
}

#[cfg(unix)]
fn terminate_profile_browsers(profile: &Path) {
    let Ok(output) = Command::new("/bin/ps")
        .args(["-axo", "pid=,command="])
        .output()
    else {
        return;
    };
    let needle = format!("--user-data-dir={}", profile.display());
    let mut matches = Vec::new();
    for line in String::from_utf8_lossy(&output.stdout).lines() {
        let line = line.trim_start();
        let Some((pid, command)) = line.split_once(char::is_whitespace) else {
            continue;
        };
        if command.contains(&needle) {
            if let Ok(pid) = pid.parse::<u32>() {
                matches.push(pid);
            }
        }
    }

    for &pid in matches.iter().rev() {
        signal_process(pid, "-TERM");
    }
    std::thread::sleep(Duration::from_millis(200));
    for pid in matches {
        if process_exists(pid) {
            signal_process(pid, "-KILL");
        }
    }
}

#[cfg(target_os = "windows")]
fn terminate_profile_browsers(profile: &Path) {
    use std::os::windows::process::CommandExt;

    // 后端若已先崩溃，taskkill 无法再从父 PID 找到被系统接管的 Chromium；
    // 此时按本应用独占的 Profile 参数精确清理孤儿浏览器进程。
    let script = "$needle = '--user-data-dir=' + $env:DYAUTH_BROWSER_PROFILE_TO_STOP; \
        Get-CimInstance Win32_Process | \
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains($needle) } | \
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }";
    let _ = Command::new("powershell")
        .args(["-NoProfile", "-NonInteractive", "-Command", script])
        .env("DYAUTH_BROWSER_PROFILE_TO_STOP", profile)
        .creation_flags(0x08000000)
        .status();
}

/// 先请求 Python 释放 CloakBrowser，再终止独立进程组，避免遗留任何子进程。
fn stop_backend(app: &AppHandle, final_shutdown: bool) {
    let (mut child, token, browser_profile) = {
        let state = app.state::<BackendProcess>();
        let Ok(mut guard) = state.0.lock() else {
            return;
        };
        if final_shutdown {
            guard.shutting_down = true;
        }
        (
            guard.child.take(),
            guard.shutdown_token.take(),
            guard.browser_profile.take(),
        )
    };

    let log_path = startup_log_path(app);
    let _ = append_log(
        &log_path,
        &format!("[{}] 应用退出：开始关闭浏览器和后端进程树\n", chrono_now()),
    );

    if let (Some(_), Some(token)) = (child.as_ref(), token) {
        let result = reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(8))
            .build()
            .and_then(|client| {
                client
                    .post("http://127.0.0.1:8000/api/system/shutdown")
                    .header("x-dyauth-shutdown-token", token)
                    .send()
            });
        let message = match result {
            Ok(response) if response.status().is_success() => "浏览器已由后端正常关闭".to_string(),
            Ok(response) => format!(
                "后端关停请求返回 HTTP {}，转入进程树清理",
                response.status()
            ),
            Err(error) => format!("后端关停请求失败（{error}），转入进程树清理"),
        };
        let _ = append_log(&log_path, &format!("[{}] {message}\n", chrono_now()));
    }

    if let Some(ref mut child) = child {
        terminate_process_tree(child);
    }
    if let Some(profile) = browser_profile.as_deref() {
        terminate_profile_browsers(profile);
    }
    let _ = append_log(
        &log_path,
        &format!("[{}] 后端及附带进程已全部关闭\n", chrono_now()),
    );
}

fn refresh_backend_state(state: &mut BackendState) -> bool {
    match state.child.as_mut().map(Child::try_wait) {
        Some(Ok(None)) => true,
        Some(Ok(Some(status))) => {
            state.last_error = Some(format!("后端进程已退出（{status}）"));
            state.child = None;
            state.shutdown_token = None;
            false
        }
        Some(Err(error)) => {
            state.last_error = Some(format!("无法读取后端进程状态：{error}"));
            state.child = None;
            state.shutdown_token = None;
            false
        }
        None => false,
    }
}

/// 启动 Python 后端服务
/// 优先使用打包的 sidecar 可执行文件，开发模式下退回 uvicorn
fn start_backend(app: &AppHandle, shutdown_token: &str) -> std::io::Result<SpawnedBackend> {
    let resource_path = app.path().resource_dir().unwrap_or_default();
    let log_path = startup_log_path(app);
    let captcha_dir = captcha_screenshot_dir(app);
    std::fs::create_dir_all(&captcha_dir)?;

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

    let _ = append_log(
        &log_path,
        &format!(
            "\n[{ts}] === 应用启动 ===\n\
         [{ts}] exe_dir:      {exe}\n\
         [{ts}] resource_dir: {res}\n\
         [{ts}] captcha_dir:  {captcha}\n\
         [{ts}] log_path:     {log}\n",
            ts = chrono_now(),
            exe = exe_dir.display(),
            res = resource_path.display(),
            captcha = captcha_dir.display(),
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
        command
            .env("DYAUTH_RESOURCE_DIR", &data_dir)
            .env("DYAUTH_SHUTDOWN_TOKEN", shutdown_token)
            .env("DYAUTH_CAPTCHA_SCREENSHOT_DIR", &captcha_dir);
        let child = spawn_logged(command, &log_path)?;
        Ok(SpawnedBackend {
            child,
            browser_profile: data_dir.join("data/browser_profile"),
        })
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
                .env("PYTHONPATH", &project_root)
                .env("DYAUTH_SHUTDOWN_TOKEN", shutdown_token)
                .env("DYAUTH_CAPTCHA_SCREENSHOT_DIR", &captcha_dir);
            let child = spawn_logged(command, &log_path)?;
            Ok(SpawnedBackend {
                child,
                browser_profile: project_root.join("data/browser_profile"),
            })
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
    stop_backend(&app, false);

    let mut guard = state.0.lock().map_err(|e| e.to_string())?;
    if guard.shutting_down {
        return Err("应用正在退出，不能重启后端".to_string());
    }
    guard.last_error = None;
    drop(guard);

    cleanup_previous_instances(&app).map_err(|error| format!("启动前清理失败：{error}"))?;

    // 持锁完成最终检查和 spawn，关闭事件随后一定能取得并清理新进程。
    let mut guard = state.0.lock().map_err(|e| e.to_string())?;
    if guard.shutting_down {
        return Err("应用正在退出，已取消重启后端".to_string());
    }
    let shutdown_token = new_shutdown_token();
    match start_backend(&app, &shutdown_token) {
        Ok(spawned) => {
            guard.child = Some(spawned.child);
            guard.shutdown_token = Some(shutdown_token);
            guard.browser_profile = Some(spawned.browser_profile);
            guard.last_error = None;
            Ok("后端已重启".to_string())
        }
        Err(error) => {
            let message = format!("后端启动失败：{error}");
            guard.last_error = Some(message.clone());
            Err(message)
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .manage(BackendProcess::default())
        .setup(|app| {
            // 应用启动时拉起后端
            let app_handle = app.handle().clone();
            let state = app.state::<BackendProcess>();
            let shutdown_token = new_shutdown_token();
            let startup = cleanup_previous_instances(&app_handle)
                .and_then(|_| start_backend(&app_handle, &shutdown_token));
            match startup {
                Ok(spawned) => {
                    let pid = spawned.child.id();
                    println!("后端已启动，PID: {}", pid);
                    let log_path = startup_log_path(&app_handle);
                    let _ = append_log(
                        &log_path,
                        &format!("[{}] ✅ sidecar 启动成功 PID={}\n", chrono_now(), pid),
                    );
                    let mut guard = state.0.lock().unwrap();
                    guard.child = Some(spawned.child);
                    guard.shutdown_token = Some(shutdown_token);
                    guard.browser_profile = Some(spawned.browser_profile);
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
            // 无论点关闭按钮还是系统退出，窗口销毁前先做完整资源清理。
            if matches!(
                event,
                tauri::WindowEvent::CloseRequested { .. } | tauri::WindowEvent::Destroyed
            ) {
                println!("正在关闭浏览器和全部后台服务...");
                stop_backend(window.app_handle(), true);
            }
        })
        .invoke_handler(tauri::generate_handler![
            check_backend,
            get_backend_status,
            restart_backend,
            write_startup_log
        ])
        .build(tauri::generate_context!())
        .expect("Tauri 应用构建失败");

    app.run(|app_handle, event| {
        // Command+Q、Dock 菜单退出和系统关机不一定触发窗口 CloseRequested，
        // 因此必须在应用级退出事件再次执行同一套幂等清理。
        if matches!(
            event,
            tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit
        ) {
            stop_backend(app_handle, true);
        }
    });
}
