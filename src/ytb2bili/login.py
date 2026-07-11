"""扫码登录流程：生成二维码（PNG + 终端 ASCII + URL）并轮询写入 cookies。

设计成对 AI/脚本友好：``login`` 会先输出一条二维码事件（含 PNG 路径、URL、
终端 ASCII），再阻塞轮询，最终输出登录结果。CLI 的 --json 模式下以 NDJSON
（每行一个 JSON）流式输出，非 --json 模式下把二维码打到终端并可自动打开图片。
"""
from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

from . import biliapi
from .config import DATA_DIR, ensure_dirs
from .result import Ytb2biliError


def make_qr_png(url: str, path: Path) -> Path:
    import qrcode

    ensure_dirs()
    img = qrcode.make(url)
    img.save(path)
    return path


def qr_terminal(url: str) -> str:
    """返回可在等宽终端里扫描的二维码 ASCII。"""
    import qrcode

    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.make(fit=True)
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    return buf.getvalue()


def open_image(path: Path) -> bool:
    """尝试用系统默认程序打开图片（仅便利，失败不报错）。"""
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        elif sys.platform.startswith("win"):
            import os
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
        return True
    except Exception:
        return False


def prepare_qr(cookies_file: str) -> dict:
    """申请二维码并落地 PNG，返回二维码相关信息（不含轮询）。"""
    url, auth_code = biliapi.request_qrcode()
    png = DATA_DIR / "login_qr.png"
    try:
        make_qr_png(url, png)
        png_path = str(png)
    except Exception:
        png_path = None
    return {
        "qr_url": url,
        "auth_code": auth_code,
        "qr_png": png_path,
        "qr_terminal": qr_terminal(url),
        "cookies_file": cookies_file,
    }
