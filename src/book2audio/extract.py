"""格式提取:epub / pdf / mobi / azw3 → BookDoc。"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .models import BookDoc, Chapter

SUPPORTED_EXTS = {".epub", ".pdf", ".mobi", ".azw3", ".txt"}

# ---------------------------------------------------------------- 通用清洗

_PAGE_NUM_LINE = re.compile(r"^\s*\d{1,4}\s*$")


def clean_whitespace(text: str) -> str:
    """合并多余空行、去除行首尾空白与孤立页码行。"""
    lines = []
    blank = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            blank += 1
            if blank <= 1:
                lines.append("")
            continue
        blank = 0
        if _PAGE_NUM_LINE.match(line) and len(line) <= 4:
            continue
        lines.append(line)
    out = "\n".join(lines)
    return out.strip()


def remove_footnotes(text: str) -> str:
    """移除常见脚注/尾注标记,如 [12]、〔12〕、(注 12)、[^12]。"""
    text = re.sub(r"\[\^?\d+\]", "", text)
    text = re.sub(r"〔\d+〕", "", text)
    text = re.sub(r"（注\s*\d+）", "", text)
    text = re.sub(r"\(注\s*\d+\)", "", text)
    return text


# ---------------------------------------------------------------- epub

_NS_CNT = "{urn:oasis:names:tc:opendocument:xmlns:container}"
_NS_OPF = "{http://www.idpf.org/2007/opf}"
_NS_DC = "{http://purl.org/dc/elements/1.1/}"


def _epub_rootfile(zf: zipfile.ZipFile) -> str:
    data = zf.read("META-INF/container.xml")
    root = ET.fromstring(data)
    node = root.find(f".//{_NS_CNT}rootfile")
    if node is None:
        raise ValueError("EPUB container.xml 中找不到 rootfile")
    return node.get("full-path")


def _epub_cover(zf: zipfile.ZipFile, opf_root: ET.Element, opf_dir: str) -> bytes | None:
    """从 OPF 找封面:meta[name=cover] → manifest → href;回退:manifest 中首个 image。"""
    manifest = opf_root.find(f"{_NS_OPF}manifest")
    if manifest is None:
        return None
    cover_id = None
    for meta in opf_root.findall(f"{_NS_OPF}metadata/{_NS_OPF}meta"):
        if meta.get("name") == "cover":
            cover_id = meta.get("content")
            break
    items = {it.get("id"): it for it in manifest.findall(f"{_NS_OPF}item")}
    href = None
    if cover_id and cover_id in items:
        href = items[cover_id].get("href")
    if href is None:
        for it in manifest.findall(f"{_NS_OPF}item"):
            mt = (it.get("media-type") or "").lower()
            if mt.startswith("image/"):
                href = it.get("href")
                break
    if href is None:
        return None
    # 相对 opf 目录拼接,不 resolve(避免 Windows 绝对路径进入 zip 名)
    rel = str(Path(opf_dir) / href).replace("\\", "/")
    rel = rel.lstrip("./")
    try:
        return zf.read(rel)
    except KeyError:
        return None


def _html_to_text(html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav"]):
        tag.decompose()
    for tag in soup.find_all(["sup", "a"]):
        tag.decompose()  # 去掉上标与链接(脚注)
    text = soup.get_text("\n")
    return clean_whitespace(remove_footnotes(text))


def _extract_epub(path: Path) -> BookDoc:
    with zipfile.ZipFile(path) as zf:
        opf_path = _epub_rootfile(zf)
        opf_dir = str(Path(opf_path).parent)
        opf_root = ET.fromstring(zf.read(opf_path))
        metadata = opf_root.find(f"{_NS_OPF}metadata")
        title = author = ""
        if metadata is not None:
            n = metadata.find(f"{_NS_DC}title")
            title = (n.text or "").strip() if n is not None else ""
            n = metadata.find(f"{_NS_DC}creator")
            author = (n.text or "").strip() if n is not None else ""
        cover = _epub_cover(zf, opf_root, opf_dir)

        manifest = opf_root.find(f"{_NS_OPF}manifest")
        spine = opf_root.find(f"{_NS_OPF}spine")
        items = {it.get("id"): it for it in manifest.findall(f"{_NS_OPF}item")} if manifest is not None else {}
        order: list[str] = []
        if spine is not None:
            order = [r.get("idref") for r in spine.findall(f"{_NS_OPF}itemref")]
        if not order:
            order = list(items.keys())

        chapters: list[Chapter] = []
        idx = 0
        for idref in order:
            it = items.get(idref)
            if it is None:
                continue
            mt = (it.get("media-type") or "").lower()
            if not mt.endswith(("html", "xml")):
                continue
            rel = str(Path(opf_dir) / it.get("href")).replace("\\", "/").lstrip("./")
            try:
                raw = zf.read(rel).decode("utf-8", errors="replace")
            except KeyError:
                continue
            text = _html_to_text(raw)
            if not text:
                continue
            # 一个 spine 文件常含多个 <h1>/<h2>,按标题再切
            parts = _split_by_headings(text)
            for title_text, body in parts:
                idx += 1
                chapters.append(Chapter(index=idx, title=title_text or f"第 {idx} 章", text=body))

    if not chapters:
        raise ValueError("EPUB 中没有提取到任何正文章节")
    if not title:
        title = path.stem
    return BookDoc(title=title, author=author, cover=cover, source_format="epub", chapters=chapters)


_HEADING_RE = re.compile(r"^\s*(第\s*[0-9一二三四五六七八九十百千零]+\s*[章节卷部篇]|Chapter\s+\d+|附录|前言|序|后记)\s*[:：、.．\-—]?\s*\S.*$")


def _split_by_headings(text: str) -> list[tuple[str, str]]:
    """按常见中文章节标题把一段文本切成 (标题, 正文) 列表。"""
    lines = text.splitlines()
    parts: list[tuple[str, str]] = []
    cur_title, cur_body = "", []
    for line in lines:
        if _HEADING_RE.match(line) and len(line) <= 60:
            if cur_title or cur_body:
                parts.append((cur_title, "\n".join(cur_body).strip()))
            cur_title, cur_body = line.strip(), []
        else:
            cur_body.append(line)
    if cur_title or cur_body:
        parts.append((cur_title, "\n".join(cur_body).strip()))
    return [(t, b) for t, b in parts if b]


# ---------------------------------------------------------------- pdf

_CHAPTER_HEADING_RE = re.compile(
    r"^\s*(第\s*[0-9一二三四五六七八九十百千零]+\s*[章节卷部篇][^\n]{0,40}|"
    r"Chapter\s+\d+[^\n]{0,40}|"
    r"#{1,3}\s+[^\n]{0,40})\s*$"
)


def _extract_pdf(path: Path) -> BookDoc:
    import pymupdf

    doc = pymupdf.open(path)
    pages = [page.get_text() for page in doc]
    doc.close()
    meaningful = [t for t in pages if len(t.strip()) > 20]
    if meaningful and len(meaningful) < max(2, int(len(pages) * 0.5)):
        print(f"[warn] 检测到大量空白页({len(meaningful)}/{len(pages)} 有文本),"
              f"可能为扫描版 PDF,需要先 OCR 才能提取文字")
    text = "\n\n".join(pages)
    text = clean_whitespace(remove_footnotes(text))
    if len(text) < 50:
        raise ValueError("PDF 几乎没有可提取的文本,疑似扫描版,请先 OCR(如 PaddleOCR)再处理")

    chapters: list[Chapter] = []
    idx, cur_title, cur_body = 0, "", []
    for line in text.splitlines():
        if _CHAPTER_HEADING_RE.match(line):
            if cur_title or cur_body:
                idx += 1
                chapters.append(Chapter(index=idx, title=cur_title or f"第 {idx} 章", text="\n".join(cur_body).strip()))
            cur_title, cur_body = line.strip(), []
        else:
            cur_body.append(line)
    if cur_title or cur_body:
        idx += 1
        chapters.append(Chapter(index=idx, title=cur_title or f"第 {idx} 章", text="\n".join(cur_body).strip()))

    if len(chapters) == 1 and len(chapters[0].text) > 6000:
        print(f"[warn] 未能按章节标题切分({len(chapters[0].text)} 字),已作为单章处理,"
              f"后续可用 --max-chars 控制 TTS 分块粒度")
    if not chapters or all(not c.text for c in chapters):
        raise ValueError("PDF 未提取到正文")
    return BookDoc(title=path.stem, source_format="pdf", chapters=chapters)


# ---------------------------------------------------------------- mobi / azw3

def _find_calibre() -> str | None:
    exe = shutil.which("ebook-convert")
    if exe:
        return exe
    # 常见安装位置
    candidates = [
        r"C:\Program Files\Calibre2\ebook-convert.exe",
        r"C:\Program Files (x86)\Calibre2\ebook-convert.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None


def _extract_mobi_azw3(path: Path) -> BookDoc:
    calibre = _find_calibre()
    if calibre is None:
        raise RuntimeError(
            f"处理 {path.suffix} 需要 Calibre(转换到 EPUB)。请安装后重试,"
            f"或先用 Calibre 手动把文件转成 EPUB。安装: https://calibre-ebook.com/download"
        )
    with tempfile.TemporaryDirectory(prefix="b2a_calibre_") as tmp:
        out_epub = Path(tmp) / "book.epub"
        proc = subprocess.run(
            [calibre, str(path), str(out_epub)],
            capture_output=True, text=True, timeout=600, check=False,
        )
        if proc.returncode != 0 or not out_epub.exists():
            raise RuntimeError(f"Calibre 转换失败: {proc.stderr[-500:]}")
        book = _extract_epub(out_epub)
    book.source_format = path.suffix.lstrip(".")
    if not book.title or book.title == "book":
        book.title = path.stem
    return book


# ---------------------------------------------------------------- txt

def _extract_txt(path: Path) -> BookDoc:
    text = path.read_text(encoding="utf-8", errors="replace")
    text = clean_whitespace(remove_footnotes(text))
    chapters: list[Chapter] = []
    idx, cur_title, cur_body = 0, "", []
    for line in text.splitlines():
        if _CHAPTER_HEADING_RE.match(line):
            if cur_title or cur_body:
                idx += 1
                chapters.append(Chapter(index=idx, title=cur_title or f"第 {idx} 章", text="\n".join(cur_body).strip()))
            cur_title, cur_body = line.strip(), []
        else:
            cur_body.append(line)
    if cur_title or cur_body:
        idx += 1
        chapters.append(Chapter(index=idx, title=cur_title or f"第 {idx} 章", text="\n".join(cur_body).strip()))
    if not chapters:
        chapters = [Chapter(index=1, title=path.stem, text=text)]
    return BookDoc(title=path.stem, source_format="txt", chapters=chapters)


# ---------------------------------------------------------------- 入口

def extract(path: str | Path) -> BookDoc:
    """按扩展名分派提取。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {p}")
    ext = p.suffix.lower()
    if ext not in SUPPORTED_EXTS:
        raise ValueError(f"不支持的格式 {ext},支持: {sorted(SUPPORTED_EXTS)}")
    if ext == ".epub":
        return _extract_epub(p)
    if ext == ".pdf":
        return _extract_pdf(p)
    if ext in (".mobi", ".azw3"):
        return _extract_mobi_azw3(p)
    return _extract_txt(p)
