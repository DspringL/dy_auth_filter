# -*- coding: utf-8 -*-
"""
build_backend.py
将 FastAPI 后端打包为独立可执行文件（无需 Python 环境即可运行）

用法：
  python build_backend.py                              # 自动检测当前平台 triple
  TARGET_TRIPLE=x86_64-apple-darwin python build_backend.py  # 指定 triple（CI 交叉编译）

输出：frontend/src-tauri/binaries/backend-server-{target_triple}[.exe]
"""
import os
import sys
import subprocess
import platform
import shutil
from pathlib import Path

# Windows CI 上 stdout 默认是 cp1252，强制改为 UTF-8，避免中文打印崩溃
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent
BINARIES_DIR = ROOT / "frontend" / "src-tauri" / "binaries"


def get_target_triple() -> str:
    """
    返回目标平台的 Rust target triple。
    优先读取环境变量 TARGET_TRIPLE（CI 交叉编译时由 workflow 注入），
    其次根据当前运行平台自动推断。
    """
    # CI 注入的目标 triple（用于 macOS 交叉编译 x86_64/aarch64）
    env_triple = os.environ.get("TARGET_TRIPLE", "").strip()
    if env_triple:
        return env_triple

    system = platform.system()
    machine = platform.machine().lower()

    if system == "Darwin":
        return "aarch64-apple-darwin" if machine == "arm64" else "x86_64-apple-darwin"
    elif system == "Windows":
        return "x86_64-pc-windows-msvc"
    else:
        return "x86_64-unknown-linux-gnu"


def build():
    triple = get_target_triple()
    is_windows = platform.system() == "Windows"
    suffix = ".exe" if is_windows else ""
    output_name = f"backend-server-{triple}{suffix}"
    output_path = BINARIES_DIR / output_name

    print(f"[build_backend] target triple : {triple}")
    print(f"[build_backend] output path   : {output_path}")

    BINARIES_DIR.mkdir(parents=True, exist_ok=True)

    # 检查 PyInstaller
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[build_backend] 正在安装 PyInstaller...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)

    # 创建 PyInstaller 入口脚本（仅用于打包，打包后删除）
    entry_script = ROOT / "_backend_entry.py"
    entry_script.write_text(
        '"""PyInstaller entry: start FastAPI backend"""\n'
        "import sys\n"
        "import os\n"
        "from pathlib import Path\n\n"
        "root = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent\n"
        "sys.path.insert(0, str(root))\n"
        "os.chdir(root)\n\n"
        "import uvicorn\n"
        "from backend.main import app\n\n"
        'if __name__ == "__main__":\n'
        '    uvicorn.run(app, host="127.0.0.1", port=8000, loop="asyncio")\n',
        encoding="utf-8",
    )

    # --add-data 路径分隔符：Windows 用 `;`，其他用 `:`
    sep = ";" if is_windows else ":"

    pyinstaller_args = [
        sys.executable, "-m", "PyInstaller",
        str(entry_script),
        "--name", "backend-server",
        "--onefile",
        "--noconfirm",
        "--clean",
        "--distpath", str(BINARIES_DIR),
        "--workpath", str(ROOT / "build" / "pyinstaller_work"),
        "--specpath", str(ROOT / "build"),
        "--add-data", f"{ROOT / 'config'}{sep}config",
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops.asyncio",
        "--hidden-import", "uvicorn.protocols.http.h11_impl",
        "--hidden-import", "uvicorn.protocols.http.httptools_impl",
        "--hidden-import", "uvicorn.protocols.websockets.websockets_impl",
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "uvicorn.lifespan.off",
        "--hidden-import", "aiosqlite",
        "--hidden-import", "sqlalchemy.dialects.sqlite",
        "--collect-all", "cloakbrowser",
        "--collect-all", "playwright",
    ]

    print("[build_backend] 正在编译后端 sidecar（可能需要几分钟）...")
    result = subprocess.run(pyinstaller_args, cwd=str(ROOT))

    # 清理临时入口脚本
    entry_script.unlink(missing_ok=True)

    if result.returncode != 0:
        print("[build_backend] ERROR: PyInstaller 编译失败")
        sys.exit(1)

    # PyInstaller 输出的文件名不含 triple，重命名为 Tauri sidecar 规范格式
    compiled = BINARIES_DIR / f"backend-server{suffix}"
    if compiled.exists():
        shutil.move(str(compiled), str(output_path))
        print(f"[build_backend] OK: {output_path}")
    else:
        print(f"[build_backend] ERROR: 未找到编译产物 {compiled}")
        sys.exit(1)


if __name__ == "__main__":
    build()
