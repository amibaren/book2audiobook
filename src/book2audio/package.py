"""打包:命名 / ID3 标签 / LRC 歌词 / 封面,输出可直接拷入 Navidrome MusicFolder 的目录。"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from mutagen.id3 import APIC, TALB, TCON, TIT2, TPE1, TPOS, TRCK, ID3NoHeaderError
from mutagen.mp3 import MP3

from .models import BookDoc

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(title: str, max_len: int = 60) -> str:
    s = _ILLEGAL.sub("_", title).strip(" .")
    s = re.sub(r"\s+", " ", s)
    return s[:max_len] or "untitled"


def track_basename(global_idx: int, safe_title: str) -> str:
    """Navidrome 按文件名排序 → 前导序号决定音轨顺序。"""
    return f"{global_idx:02d}-{safe_title}"


def write_lrc(path: Path, book: BookDoc, title: str, artist: str) -> None:
    """无时间戳 LRC:车载/客户端可显示本章全文。"""
    lines = [
        f"[ti:{title}]",
        f"[ar:{artist or book.author}]",
        f"[al:{book.title}]",
        "",
    ]
    # 按段落,每段一行(文本较长也不拆行,播放器可滚动)
    for para in [p.strip() for p in re.split(r"\n+", _chapter_text_for(title, book)) if p.strip()]:
        lines.append(para)
    path.write_text("\n".join(lines), encoding="utf-8")


def _chapter_text_for(title: str, book: BookDoc) -> str:
    for ch in book.chapters:
        if ch.title == title:
            return ch.text
    return ""


def write_id3(audio: Path, *, title: str, artist: str, album: str, track: int, total: int, cover: bytes | None) -> None:
    """写入 ID3 标签;封面内嵌 APIC。"""
    audio = Path(audio)
    try:
        mp3 = MP3(audio)
    except ID3NoHeaderError:
        mp3 = MP3()
    try:
        mp3.add_tags()
    except Exception:
        pass
    mp3.tags.add(TIT2(encoding=3, text=title))
    if artist:
        mp3.tags.add(TPE1(encoding=3, text=artist))
    if album:
        mp3.tags.add(TALB(encoding=3, text=album))
    mp3.tags.add(TRCK(encoding=3, text=f"{track}/{total}"))
    mp3.tags.add(TCON(encoding=3, text="Spoken Word"))
    mp3.tags.add(TPOS(encoding=3, text="1/1"))
    if cover:
        mime = "image/jpeg"
        if cover[:8] == b"\x89PNG\r\n\x1a\n":
            mime = "image/png"
        mp3.tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover))
    mp3.save(audio)


def package(book: BookDoc, out_dir: Path, staging_dir: Path) -> list[Path]:
    """把 staging 里平铺的块音频(0001.mp3...)按章节组织打包,返回文件清单。

    目录结构(直接拷入 Navidrome MusicFolder 即可,扫描后入库):
      <书名>/
        01-第一章标题.mp3           (单块章节)
        01-第一章标题.mp3.lrc
        ...                        (多块章节: 01-001-标题.mp3)
        cover.jpg
    """
    out_dir = Path(out_dir)
    staging_dir = Path(staging_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    flat = sorted(staging_dir.glob("*.mp3"))
    total_chunks = sum(max(1, len(c.chunks)) for c in book.chapters)
    if len(flat) < total_chunks:
        raise RuntimeError(f"staging 音频不足: 需要 {total_chunks} 块,实际 {len(flat)} 个。先运行 synth")

    files: list[Path] = []
    global_idx = 0
    for ch in book.chapters:
        safe = safe_filename(ch.title)
        chunks = ch.chunks or [ch.text]
        for i in range(1, len(chunks) + 1):
            global_idx += 1
            if len(chunks) == 1:
                base = track_basename(global_idx, safe)
            else:
                base = track_basename(global_idx, f"{global_idx - len(chunks) + i:03d}-{safe}")
            src = flat[global_idx - 1]
            dst = out_dir / f"{base}.mp3"
            shutil.copy2(src, dst)
            write_id3(dst, title=ch.title, artist=book.author, album=book.title,
                      track=global_idx, total=total_chunks, cover=book.cover)
            write_lrc(out_dir / f"{base}.mp3.lrc", book, ch.title, book.author)
            files.append(dst)

    if book.cover:
        mime = "image/png" if book.cover[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
        cover_path = out_dir / f"cover.{'png' if 'png' in mime else 'jpg'}"
        cover_path.write_bytes(book.cover)
        files.append(cover_path)
    return files
