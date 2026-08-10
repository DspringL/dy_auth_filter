"""
build_backend.py
将 FastAPI 后端打包为独立可执行文件（无需 Python 环境即可运行）
用法：python build_backend.py
输出：frontend/src-tauri/binaries/backend-server-{target_triple}[.exe]
"""
import os
import sys
import subprocess
import platform
import shutil
from pathlib import Path

ROOT = Path(__file__).parent
BINARIES_DIR = ROOT / "frontend" / "src-tauri" / "binaries"


def get_target_triple() -> str:
    """返回当前平台对应的 Rust target triple"""
    system = platform.system()
    machine = platform.machine().lower()

    if system == "Darwin":
        if machine == "arm64":
            return "aarch64-apple-darwin"
        return "x86_64-apple-darwin"
    elif system == "Windows":
        return "x86_64-pc-windows-msvc"
    else:  # Linux
        return "x86_64-unknown-linux-gnu"


def build():
    triple = get_target_triple()
    suffix = ".exe" if platform.system() == "Windows" else ""
    output_name = f"backend-server-{triple}{suffix}"
    output_path = BINARIES_DIR / output_name

    print(f"目标平台：{triple}")
    print(f"输出路径：{output_path}")

    BINARIES_DIR.mkdir(parents=True, exist_ok=True)

    # 检查 PyInstaller
    try:
        import PyInstaller
    except ImportError:
        print("正在安装 PyInstaller...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)

    # 创建 backend 入口脚本（供 PyInstaller 打包用）
    entry_script = ROOT / "_backend_entry.py"
    entry_script.write_text(
        '"""PyInstaller 入口：启动 FastAPI 后端服务"""\n'
        'import sys\n'
        'import os\n'
        'from pathlib import Path\n\n'
        '# 将项目根目录加入 Python 路径\n'
        'root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent\n'
        'sys.path.insert(0, str(root))\n'
        'os.chdir(root)\n\n'
        'import uvicorn\n'
        'from backend.main import app\n\n'
        'if __name__ == "__main__":\n'
        '    uvicorn.run(app, host="127.0.0.1", port=8000, loop="asyncio")\n',
        encoding="utf-8",
    )

    # PyInstaller 参数
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
        # 添加必要的数据文件和模块
        "--add-data", f"{ROOT / 'config'}:config",
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops.asyncio",
        "--hidden-import", "uvicorn.protocols.http.h11_impl",
        "--hidden-import", "uvicorn.protocols.http.httptools_impl",
        "--hidden-import", "uvicorn.protocols.websockets.websockets_impl",
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "uvicorn.lifespan.off",
        "--hidden-import", "aiosqlite",
        "--hidden-import", "sqlalchemy.dialects.sqlite",
        "--hidden-import", "passlib.handlers.bcrypt",
        "--collect-all", "cloakbrowser",
        "--collect-all", "playwright",
    ]

    print("\n正在编译后端（可能需要几分钟）...")
    result = subprocess.run(pyinstaller_args, cwd=str(ROOT))

    # 清理临时文件
    entry_script.unlink(missing_ok=True)

    if result.returncode != 0:
        print("❌ 编译失败")
        sys.exit(1)

    # 重命名为带 triple 的名字（Tauri sidecar 要求）
    compiled = BINARIES_DIR / f"backend-server{suffix}"
    if compiled.exists():
        shutil.move(str(compiled), str(output_path))
        print(f"\n✅ 编译完成：{output_path}")
    else:
        print(f"❌ 未找到编译产物：{compiled}")
        sys.exit(1)


if __name__ == "__main__":
    build()
