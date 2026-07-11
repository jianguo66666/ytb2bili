"""yt-dlp 封装：下载 YouTube 视频并输出结构化信息。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from . import deps
from .config import Config
from .result import Ytb2biliError

# YouTube 需要解 JS "n challenge" 才能拿到视频格式；让 yt-dlp 下载官方 EJS 求解脚本
# （首次下载后缓存）。需要本机有 JS 运行时（deno 或 node）。
EJS_FLAGS = ["--remote-components", "ejs:github"]


def _ffmpeg_flags() -> list[str]:
    loc = deps.ffmpeg_location()
    return ["--ffmpeg-location", loc] if loc else []


def _youtube_cookie_args(cfg: Config, browser: str | None) -> list[str]:
    """决定给 yt-dlp 的 cookie 参数：优先用 cookie 文件（不弹钥匙串），否则读浏览器。"""
    yc = getattr(cfg, "youtube_cookies", "") or ""
    if yc and Path(yc).expanduser().exists():
        return ["--cookies", str(Path(yc).expanduser())]
    b = browser if browser is not None else cfg.cookies_from_browser
    if b:
        return ["--cookies-from-browser", b]
    return []


def export_cookies(cfg: Config, browser: str, out_file: str | None = None) -> dict:
    """从浏览器导出 YouTube cookie 到 Netscape 文件，供后续 --cookies 复用。

    只需触发一次钥匙串授权，之后下载不再读浏览器、不再弹窗。
    """
    deps.ensure_ytdlp()
    from .config import DATA_DIR
    dest = Path(out_file).expanduser() if out_file else (DATA_DIR / "youtube_cookies.txt")
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = deps.ytdlp_cmd() + [
        "--cookies-from-browser", browser,
        "--cookies", str(dest),
        "--skip-download", "--no-warnings",
        "--playlist-items", "0",
        "https://www.youtube.com/",
    ]
    # yt-dlp 会在退出时把 cookie jar 写入 --cookies 文件；即便目标页无可提取内容，
    # 只要成功读到浏览器 cookie 就会落盘，因此不强制要求退出码为 0。
    subprocess.run(cmd, text=True, capture_output=True, env=deps.subprocess_env())
    if not dest.exists() or dest.stat().st_size < 100:
        # 回退：用一个真实视频页重试一次
        cmd2 = deps.ytdlp_cmd() + [
            "--cookies-from-browser", browser, "--cookies", str(dest),
            "--skip-download", "--no-warnings",
            "https://www.youtube.com/watch?v=2WJ_4pxB8jc",
        ]
        try:
            subprocess.run(cmd2, text=True, capture_output=True, check=True)
        except subprocess.CalledProcessError as e:
            raise Ytb2biliError(
                "export_cookies_failed", _clean_ytdlp_error(e.stderr or e.stdout),
                browser=browser,
            )
    if not dest.exists():
        raise Ytb2biliError("export_cookies_failed", f"未能生成 cookie 文件: {dest}")
    n = sum(1 for _ in dest.open(encoding="utf-8", errors="ignore"))
    return {"youtube_cookies": str(dest), "lines": n, "browser": browser}


def probe(url: str, cookies_from_browser: str = "") -> dict:
    """只取元数据不下载。返回 title/duration/uploader/分辨率/license 等。"""
    cmd = deps.ytdlp_cmd() + ["--no-warnings", "--dump-single-json", "--no-playlist"] + EJS_FLAGS
    if cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    cmd.append(url)
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.PIPE,
                                      env=deps.subprocess_env())
    except subprocess.CalledProcessError as e:
        raise Ytb2biliError("probe_failed", _clean_ytdlp_error(e.stderr), url=url)
    info = json.loads(out)
    return _summarize(info)


def download(
    url: str,
    cfg: Config,
    output_dir: str | None = None,
    quality: int | None = None,
    cookies_from_browser: str | None = None,
    progress: bool = True,
) -> dict:
    """下载最高不超过 quality 的画质，合并为 mp4，并抓取封面 + info.json。

    返回 {video, cover, info_json, title, duration, width, height, source_url, id}。
    """
    deps.ensure_ytdlp()
    outdir = Path(output_dir or cfg.download_dir).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)
    q = quality or cfg.quality
    cookie_args = _youtube_cookie_args(cfg, cookies_from_browser)

    fmt = (
        f"bestvideo[height<={q}][ext=mp4]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={q}]+bestaudio/best[height<={q}]/best"
    )
    outtmpl = str(outdir / "%(id)s.%(ext)s")
    cmd = deps.ytdlp_cmd() + [
        "--no-playlist",
        "-f", fmt,
        "--merge-output-format", "mp4",
        "--write-thumbnail", "--convert-thumbnails", "jpg",
        "--write-info-json",
        "--embed-metadata",
        "-o", outtmpl,
        "--print", "after_move:%(id)s",
    ] + EJS_FLAGS + _ffmpeg_flags() + cookie_args
    if not progress:
        cmd.append("--no-progress")
    cmd.append(url)

    try:
        proc = subprocess.run(
            cmd, text=True, capture_output=True, check=True, env=deps.subprocess_env()
        )
    except subprocess.CalledProcessError as e:
        raise Ytb2biliError(
            "download_failed", _clean_ytdlp_error(e.stderr or e.stdout), url=url
        )

    vid = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout.strip() else None
    if not vid:
        # 回退：从 info.json 找 id
        raise Ytb2biliError("download_failed", "未能确定下载文件 id", url=url, stderr=proc.stderr[-800:])

    video = outdir / f"{vid}.mp4"
    cover = _first_existing([outdir / f"{vid}.jpg", outdir / f"{vid}.webp", outdir / f"{vid}.png"])
    info_json = outdir / f"{vid}.info.json"
    meta = {}
    if info_json.exists():
        try:
            meta = _summarize(json.loads(info_json.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass

    if not video.exists():
        raise Ytb2biliError("download_failed", f"下载完成但找不到 {video}", url=url)

    return {
        "id": vid,
        "video": str(video),
        "cover": str(cover) if cover else None,
        "info_json": str(info_json) if info_json.exists() else None,
        "source_url": meta.get("source_url") or url,
        "title": meta.get("title"),
        "duration": meta.get("duration"),
        "duration_string": meta.get("duration_string"),
        "uploader": meta.get("uploader"),
        "width": meta.get("width"),
        "height": meta.get("height"),
        "size_bytes": video.stat().st_size,
        "output_dir": str(outdir),
    }


def _summarize(info: dict) -> dict:
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "duration": info.get("duration"),
        "duration_string": info.get("duration_string"),
        "width": info.get("width"),
        "height": info.get("height"),
        "license": info.get("license"),
        "source_url": info.get("webpage_url"),
        "description": (info.get("description") or "")[:500],
        "thumbnail": info.get("thumbnail"),
    }


def _first_existing(paths: list[Path]) -> Path | None:
    for p in paths:
        if p.exists():
            return p
    return None


def _clean_ytdlp_error(stderr: str | None) -> str:
    if not stderr:
        return "yt-dlp 下载失败（无错误输出）"
    lines = [l for l in stderr.splitlines() if l.strip().startswith("ERROR")]
    if lines:
        return lines[-1].strip()
    return stderr.strip().splitlines()[-1][:400]
