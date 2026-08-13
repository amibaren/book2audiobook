"""book2audio CLI 入口。

子命令:
  extract  电子书 → BookDoc(保存为 book.json)
  chunk    TTS 友好化 + 分块(更新 book.json)
  synth    逐块 TTS 合成 → staging mp3
  package  打包:命名/ID3/LRC/封面 → 交付目录
  all      一条龙
  info     查看 book.json 概况

典型用法(先 dry 联调,再上火山):
  book2audio all book.epub --provider dry --out output
  book2audio all book.epub --provider volc_ark --voice zh_female_cancan_mars_bigtts --out output
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

from . import __version__
from .chunk import chunk_book
from .config import load_config
from .extract import extract
from .models import BookDoc, Chapter
from .package import package as do_package
from .synth import get_provider

# ---------------------------------------------------------------- 序列化


def book_to_dict(book: BookDoc) -> dict:
    return {
        "title": book.title,
        "author": book.author,
        "cover": base64.b64encode(book.cover).decode() if book.cover else None,
        "source_format": book.source_format,
        "chapters": [
            {"index": c.index, "title": c.title, "text": c.text, "chunks": c.chunks}
            for c in book.chapters
        ],
    }


def book_from_dict(d: dict) -> BookDoc:
    return BookDoc(
        title=d.get("title", ""),
        author=d.get("author", ""),
        cover=base64.b64decode(d["cover"]) if d.get("cover") else None,
        source_format=d.get("source_format", ""),
        chapters=[
            Chapter(index=c["index"], title=c["title"], text=c.get("text", ""), chunks=c.get("chunks", []))
            for c in d.get("chapters", [])
        ],
    )


def load_book(input_path: Path) -> BookDoc:
    if input_path.suffix.lower() == ".json":
        return book_from_dict(json.loads(input_path.read_text(encoding="utf-8")))
    return extract(input_path)


def work_dir(out: Path, title: str) -> Path:
    return out.parent / f".{out.name}.work"


def save_book(book: BookDoc, wd: Path) -> Path:
    p = wd / "book.json"
    p.write_text(json.dumps(book_to_dict(book), ensure_ascii=False, indent=1), encoding="utf-8")
    return p


# ---------------------------------------------------------------- 命令


def cmd_extract(args) -> int:
    cfg = load_config(args.config, {"out_dir": args.out, "force": args.force, "verbose": args.verbose})
    book = extract(args.input)
    out = Path(cfg.out_dir) / book.title
    wd = work_dir(out, book.title)
    wd.mkdir(parents=True, exist_ok=True)
    p = save_book(book, wd)
    print(f"提取完成: {book.source_format} → {book.title}(作者: {book.author or '未知'})")
    print(f"  章节数: {len(book.chapters)}  总字数: {book.total_chars}  封面: {'有' if book.cover else '无'}")
    print(f"  中间文件: {p}")
    return 0


def cmd_chunk(args) -> int:
    cfg = load_config(args.config, {"max_chars": args.max_chars, "llm_model": args.llm_model, "out_dir": args.out})
    input_path = Path(args.input)
    book = load_book(input_path)
    out = Path(cfg.out_dir) / book.title
    wd = work_dir(out, book.title)
    wd.mkdir(parents=True, exist_ok=True)

    llm = None
    if cfg.llm_model:
        llm = {
            "endpoint": cfg.ark_base_url.rstrip("/") + "/chat/completions",
            "api_key": cfg.ark_api_key,
            "model": cfg.llm_model,
            "proxy": cfg.proxy,
        }
    chunk_book(book, max_chars=cfg.max_chars, llm=llm)
    save_book(book, wd)
    total = sum(len(c.chunks) for c in book.chapters)
    print(f"分块完成: {len(book.chapters)} 章 → {total} 个 TTS 块(每块 ≤{cfg.max_chars} 字)"
          f"{'[已做 LLM 情感标注]' if llm else ''}")
    return 0


def cmd_synth(args) -> int:
    cfg = load_config(
        args.config,
        {
            "provider": args.provider,
            "tts_model": args.model,
            "voice": args.voice,
            "speed": args.speed,
            "force": args.force,
            "out_dir": args.out,
            "verbose": args.verbose,
        },
    )
    book = load_book(Path(args.input))
    out = Path(cfg.out_dir) / book.title
    wd = work_dir(out, book.title)
    staging = wd / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    provider = get_provider(cfg.provider, cfg)

    wanted = parse_ranges(args.chapters) if args.chapters else None
    flat: list[tuple[Chapter, int, str]] = []   # (chapter, chunk_idx, text)
    for ch in book.chapters:
        if wanted is not None and ch.index not in wanted:
            continue
        for i, t in enumerate(ch.chunks or [ch.text], start=1):
            flat.append((ch, i, t))
    if not flat:
        print("没有需要合成的分块(先运行 chunk 生成分块)")
        return 1

    done, skipped = 0, 0
    for pos, (ch, i, text) in enumerate(flat, start=1):
        mp3 = staging / f"{pos:04d}.mp3"
        if mp3.exists() and not cfg.force:
            skipped += 1
            continue
        if cfg.verbose:
            print(f"[{pos}/{len(flat)}] 第{ch.index}章 块{i}: {text[:40]}...")
        provider.synthesize(text, mp3, proxy=cfg.proxy)
        done += 1
    print(f"合成完成: 新增 {done} 个, 跳过(已存在) {skipped} 个, 共 {len(flat)} 个 → {staging}")
    return 0


def cmd_package(args) -> int:
    cfg = load_config(args.config, {"out_dir": args.out, "verbose": args.verbose})
    book = load_book(Path(args.input))
    out = Path(cfg.out_dir) / book.title
    wd = work_dir(out, book.title)
    staging = wd / "staging"
    if not staging.exists():
        print("未找到 staging 音频,先运行 synth")
        return 1
    files = do_package(book, out, staging)
    print(f"打包完成 → {out}")
    for f in files:
        print(f"  {f.name}")
    print(f"\n共 {len(files)} 个文件。拷贝整个目录到 Navidrome 的 MusicFolder 后,"
          f"Navidrome 会自动扫描入库(或手动触发扫描)。")
    return 0


def cmd_all(args) -> int:
    cfg = load_config(
        args.config,
        {
            "provider": args.provider,
            "tts_model": args.model,
            "voice": args.voice,
            "speed": args.speed,
            "force": args.force,
            "max_chars": args.max_chars,
            "llm_model": args.llm_model,
            "out_dir": args.out,
            "verbose": args.verbose,
        },
    )
    input_path = Path(args.input)
    if input_path.suffix.lower() == ".json":
        book = load_book(input_path)
        out = Path(cfg.out_dir) / book.title
        wd = work_dir(out, book.title)
        wd.mkdir(parents=True, exist_ok=True)
        save_book(book, wd)
    else:
        book = extract(input_path)
        out = Path(cfg.out_dir) / book.title
        wd = work_dir(out, book.title)
        wd.mkdir(parents=True, exist_ok=True)
        save_book(book, wd)

    llm = None
    if cfg.llm_model:
        llm = {
            "endpoint": cfg.ark_base_url.rstrip("/") + "/chat/completions",
            "api_key": cfg.ark_api_key,
            "model": cfg.llm_model,
            "proxy": cfg.proxy,
        }
    chunk_book(book, max_chars=cfg.max_chars, llm=llm)
    save_book(book, wd)

    staging = wd / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    provider = get_provider(cfg.provider, cfg)
    flat = [(ch, i, t) for ch in book.chapters for i, t in enumerate(ch.chunks or [ch.text], start=1)]
    if not flat:
        print("书中没有可合成的文本,退出")
        return 1
    done, skipped = 0, 0
    for pos, (ch, i, text) in enumerate(flat, start=1):
        mp3 = staging / f"{pos:04d}.mp3"
        if mp3.exists() and not cfg.force:
            skipped += 1
            continue
        if cfg.verbose:
            print(f"[{pos}/{len(flat)}] 第{ch.index}章 块{i}")
        provider.synthesize(text, mp3, proxy=cfg.proxy)
        done += 1

    files = do_package(book, out, staging)
    print(f"全部完成: 合成 {done} 个(跳过 {skipped} 个), 打包 {len(files)} 个文件 → {out}")
    return 0


def cmd_info(args) -> int:
    book = load_book(Path(args.input))
    print(f"书名: {book.title}  作者: {book.author or '未知'}  来源: {book.source_format}")
    print(f"章节: {len(book.chapters)}  总字数: {book.total_chars}  封面: {'有' if book.cover else '无'}")
    for ch in book.chapters:
        print(f"  第{ch.index:>3}章 {ch.title[:30]:<32} {ch.char_count:>6}字 / {len(ch.chunks)}块")
    return 0


# ---------------------------------------------------------------- 参数解析


def parse_ranges(spec: str) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="book2audio",
        description="电子书(PDF/EPUB/MOBI/AZW3)→ 有声读物(Navidrome 入库友好)",
    )
    p.add_argument("--version", action="version", version=f"book2audio {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(parser):
        parser.add_argument("--config", help="ini 配置文件路径")
        parser.add_argument("--out", default=None, help="输出根目录(默认 output)")
        parser.add_argument("--force", action="store_true", help="覆盖已生成的音频")
        parser.add_argument("-v", "--verbose", action="store_true")

    pe = sub.add_parser("extract", help="提取为 book.json")
    common(pe)
    pe.add_argument("input")

    pc = sub.add_parser("chunk", help="TTS 友好化 + 分块")
    common(pc)
    pc.add_argument("input", help="电子书或 book.json")
    pc.add_argument("--max-chars", type=int, default=None, help="单块最大字符数(默认 1500)")
    pc.add_argument("--llm-model", default=None, help="LLM 模型 id(启用情感标注,如 doubao-seed-1-6-xxx)")

    ps = sub.add_parser("synth", help="TTS 合成")
    common(ps)
    ps.add_argument("input", help="book.json")
    ps.add_argument("--provider", choices=["volc_ark", "edge", "seed_audio", "dry"], default=None)
    ps.add_argument("--model", default=None, help="TTS 模型 id(默认取配置)")
    ps.add_argument("--voice", default=None, help="音色(默认取配置)")
    ps.add_argument("--speed", type=float, default=None, help="语速,1.0 为正常")
    ps.add_argument("--chapters", default=None, help="只合成指定章节,如 '1,3-5'")

    pk = sub.add_parser("package", help="打包为交付目录")
    common(pk)
    pk.add_argument("input", help="book.json")

    pa = sub.add_parser("all", help="一条龙")
    common(pa)
    pa.add_argument("input")
    pa.add_argument("--provider", choices=["volc_ark", "edge", "seed_audio", "dry"], default=None)
    pa.add_argument("--model", default=None)
    pa.add_argument("--voice", default=None)
    pa.add_argument("--speed", type=float, default=None)
    pa.add_argument("--max-chars", type=int, default=None)
    pa.add_argument("--llm-model", default=None)

    pi = sub.add_parser("info", help="查看 book.json 概况")
    common(pi)
    pi.add_argument("input")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        handler = {
            "extract": cmd_extract,
            "chunk": cmd_chunk,
            "synth": cmd_synth,
            "package": cmd_package,
            "all": cmd_all,
            "info": cmd_info,
        }[args.cmd]
        return handler(args)
    except Exception as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
