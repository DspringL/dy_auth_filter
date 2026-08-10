# =============================================================================
#  抖音带货达人系统 — 一键启动脚本（Windows PowerShell）
#  用法：.\start.ps1 [-NoFrontend] [-NoBrowser] [-SkipUpdate]
# =============================================================================
param(
    [switch]$NoFrontend,
    [switch]$NoBrowser,
    [switch]$SkipUpdate
)

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ROOT

# ── 颜色输出 ──────────────────────────────────────────────────────────────────
function Info    { param($msg) Write-Host "[INFO]  $msg"  -ForegroundColor Cyan }
function Ok      { param($msg) Write-Host "[OK]    $msg"  -ForegroundColor Green }
function Warn    { param($msg) Write-Host "[WARN]  $msg"  -ForegroundColor Yellow }
function Err     { param($msg) Write-Host "[ERROR] $msg"  -ForegroundColor Red; exit 1 }
function Step    { param($msg) Write-Host "`n>>> $msg"    -ForegroundColor White -BackgroundColor DarkBlue }

Write-Host ""
Write-Host "╔══════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║   抖音带货达人自动化系统 — 一键启动      ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ═════════════════════════════════════════════════════════════════════════════
# 1. 检测系统依赖
# ═════════════════════════════════════════════════════════════════════════════
Step "检测系统环境"

# Python
$pyCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]; $minor = [int]$Matches[2]
            if ($major -ge 3 -and $minor -ge 10) {
                Ok "Python $major.$minor ✓"
                $pyCmd = $cmd; break
            }
        }
    } catch {}
}
if (-not $pyCmd) { Err "需要 Python 3.10+，请先安装：https://www.python.org/downloads/" }

# Node.js（前端需要）
if (-not $NoFrontend) {
    try {
        $nodeVer = (& node --version 2>&1) -replace "v", ""
        $nodeMajor = [int]($nodeVer.Split(".")[0])
        if ($nodeMajor -ge 18) { Ok "Node.js v$nodeVer ✓" }
        else { Warn "Node.js $nodeVer 版本偏低，建议升级到 18+" }
        $npmVer = & npm --version 2>&1
        Ok "npm $npmVer ✓"
    } catch {
        Warn "未找到 Node.js，将跳过前端。请安装：https://nodejs.org"
        $NoFrontend = $true
    }
}

# ═════════════════════════════════════════════════════════════════════════════
# 2. Python 虚拟环境
# ═════════════════════════════════════════════════════════════════════════════
Step "配置 Python 虚拟环境"

$venvDir = Join-Path $ROOT ".venv"
if (-not (Test-Path $venvDir)) {
    Info "创建虚拟环境 .venv ..."
    & $pyCmd -m venv $venvDir
    Ok "虚拟环境已创建"
} else {
    Ok "虚拟环境已存在，跳过创建"
}

$venvPy = Join-Path $venvDir "Scripts\python.exe"
if (-not (Test-Path $venvPy)) { Err "虚拟环境损坏，请删除 .venv 目录后重试" }

# ═════════════════════════════════════════════════════════════════════════════
# 3. 安装 Python 依赖
# ═════════════════════════════════════════════════════════════════════════════
Step "安装 Python 依赖"

$pipStamp = Join-Path $venvDir ".pip_installed"

if (-not $SkipUpdate -or -not (Test-Path $pipStamp)) {
    Info "升级 pip..."
    & $venvPy -m pip install -q --upgrade pip
    Info "安装 requirements.txt ..."
    & $venvPy -m pip install -q -r (Join-Path $ROOT "requirements.txt")
    Get-Date | Out-File $pipStamp -Encoding utf8
    Ok "Python 依赖安装完成"
} else {
    Ok "Python 依赖已安装（跳过更新）"
}

# 验证关键包
foreach ($pkg in @("fastapi", "uvicorn", "cloakbrowser", "sqlalchemy")) {
    $res = & $venvPy -c "import $pkg" 2>&1
    if ($LASTEXITCODE -eq 0) { Ok "  $pkg ✓" }
    else { Warn "  $pkg 导入失败，尝试重新安装..."; & $venvPy -m pip install -q -r (Join-Path $ROOT "requirements.txt"); break }
}

# ═════════════════════════════════════════════════════════════════════════════
# 4. CloakBrowser 二进制
# ═════════════════════════════════════════════════════════════════════════════
if (-not $NoBrowser) {
    Step "检测 CloakBrowser 二进制"
    $cloakCache = Join-Path $env:USERPROFILE ".cloakbrowser"
    $chromePaths = Get-ChildItem $cloakCache -Filter "chrome.exe" -Recurse -ErrorAction SilentlyContinue
    if ($chromePaths.Count -gt 0) {
        Ok "CloakBrowser 二进制已存在，跳过下载"
    } else {
        Info "下载 CloakBrowser Chromium 二进制（约 200MB）..."
        try {
            & $venvPy -m cloakbrowser install
            Ok "CloakBrowser 安装完成"
        } catch {
            Warn "下载失败，可能需要访问 cloakbrowser.dev。使用 -NoBrowser 跳过"
        }
    }
}

# ═════════════════════════════════════════════════════════════════════════════
# 5. 前端依赖
# ═════════════════════════════════════════════════════════════════════════════
if (-not $NoFrontend) {
    Step "安装前端依赖"
    $frontendDir = Join-Path $ROOT "frontend"
    $nodeModules  = Join-Path $frontendDir "node_modules"
    if (-not $SkipUpdate -or -not (Test-Path $nodeModules)) {
        Info "运行 npm install ..."
        Push-Location $frontendDir
        & npm install --prefer-offline 2>&1 | Select-Object -Last 5
        Pop-Location
        Ok "前端依赖安装完成"
    } else {
        Ok "node_modules 已存在，跳过"
    }
}

# ═════════════════════════════════════════════════════════════════════════════
# 6. 初始化目录
# ═════════════════════════════════════════════════════════════════════════════
Step "初始化数据目录"
foreach ($d in @("data", "logs", "data\browser_profile")) {
    $p = Join-Path $ROOT $d
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p | Out-Null }
}
Ok "data/ 和 logs/ 目录就绪"

# ═════════════════════════════════════════════════════════════════════════════
# 7. 启动服务
# ═════════════════════════════════════════════════════════════════════════════
Step "启动服务"

$uvicorn  = Join-Path $venvDir "Scripts\uvicorn.exe"
$backendLog  = Join-Path $ROOT "logs\backend.log"
$frontendLog = Join-Path $ROOT "logs\frontend.log"

# 启动后端
Info "启动后端（日志 → logs\backend.log）"
$env:PYTHONPATH = $ROOT
$backendProc = Start-Process -FilePath $uvicorn `
    -ArgumentList "backend.main:app","--host","127.0.0.1","--port","8000","--reload","--loop","asyncio" `
    -WorkingDirectory $ROOT `
    -RedirectStandardOutput $backendLog `
    -RedirectStandardError  "$ROOT\logs\backend_err.log" `
    -PassThru -WindowStyle Hidden

# 等待后端就绪
Info "等待后端就绪..."
$ready = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/" -TimeoutSec 2 -ErrorAction Stop
        if ($r.StatusCode -eq 200) { Ok "后端已就绪 → http://127.0.0.1:8000"; $ready = $true; break }
    } catch {}
}
if (-not $ready) { Warn "后端启动超时，查看日志：logs\backend.log" }

# 启动前端
if (-not $NoFrontend) {
    Info "启动前端（日志 → logs\frontend.log）"
    $frontendProc = Start-Process -FilePath "npm" `
        -ArgumentList "run","dev","--","--port","5173" `
        -WorkingDirectory (Join-Path $ROOT "frontend") `
        -RedirectStandardOutput $frontendLog `
        -RedirectStandardError  "$ROOT\logs\frontend_err.log" `
        -PassThru -WindowStyle Hidden

    Info "等待前端就绪..."
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 1
        try {
            $r = Invoke-WebRequest -Uri "http://localhost:5173/" -TimeoutSec 2 -ErrorAction Stop
            if ($r.StatusCode -eq 200) { Ok "前端已就绪 → http://localhost:5173"; break }
        } catch {}
    }

    # 自动打开浏览器
    Start-Sleep -Seconds 1
    Start-Process "http://localhost:5173"
}

# ═════════════════════════════════════════════════════════════════════════════
# 8. 汇总
# ═════════════════════════════════════════════════════════════════════════════
Write-Host ""
Write-Host "╔══════════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║              🚀 所有服务已启动                  ║" -ForegroundColor Green
if (-not $NoFrontend) {
Write-Host "║  管理后台  →  http://localhost:5173              ║" -ForegroundColor Green }
Write-Host "║  后端 API  →  http://127.0.0.1:8000             ║" -ForegroundColor Green
Write-Host "║  API 文档  →  http://127.0.0.1:8000/docs        ║" -ForegroundColor Green
Write-Host "║  关闭服务：关闭此窗口或按 Ctrl+C                ║" -ForegroundColor Green
Write-Host "╚══════════════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""

# 实时跟踪后端日志
Info "实时后端日志（Ctrl+C 退出）："
Write-Host "──────────────────────────────────────────────────"

# 注册退出清理
Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action {
    if ($backendProc  -and -not $backendProc.HasExited)  { $backendProc.Kill()  }
    if ($frontendProc -and -not $frontendProc.HasExited) { $frontendProc.Kill() }
    Write-Host "`n所有服务已关闭。" -ForegroundColor Yellow
} | Out-Null

# tail 后端日志
Get-Content $backendLog -Wait -Tail 20
