"""把 OCR 全本文本按章节锚点切分,构造 book.json(与 CLI book_to_dict 同构)。

用法:
  python scripts/build_book.py <ocr_dir> <out_work_dir> <book_title> <author> \
      --pdf <原始pdf> [--chapters 起始页:标题, 起始页:标题, ...]

说明:
  - ocr_dir: ocr_pdf.py 的输出目录(内含 p0000.txt ~ p0321.txt)
  - 章节边界用"第X章/附录X/注释/参考文献"标题所在页定位(自动扫描,排除目录页)
  - --chapters 可手动覆盖,格式: 起始页:标题(起始页为标题所在 pdf 页索引,0-based)
  - --pdf 用于提取封面图;省略则无封面
  - 输出 <out_work_dir>/book.json(与 book2audio extract 的中间文件同路径同构)
"""
import argparse
import base64
import json
import re
import sys
from pathlib import Path

# CLI 源码: 优先发布包内 src/, 回退本机 skill 目录
_SRC = Path(__file__).resolve().parent.parent / 'src'
if not (_SRC / 'book2audio' / '__init__.py').exists():
    _SRC = Path(r'C:\Users\ALIENWARE\AppData\Roaming\reasonix\skills\book2audiobook\src')
sys.path.insert(0, str(_SRC))
import pymupdf

# 自动识别章节标题: 仅"第X章/附录X/注释/参考文献"(不含"第X部分")
_TITLE_RE = re.compile(
    r'^(第\s*[0-9一二三四五六七八九十百千零]+\s*章|附录[一二三123]|注释|参考文献)'
)
# 页眉: 孤立页码、页码+书名(书名以 "childhoods" 结尾,中英皆可)、纯英文书名行
_PAGEHEAD = re.compile(r'^\s*\d{1,3}\s*(不平等的童年|Unequal\s*Childhoods)\s*$', re.I)
_PAGENUM = re.compile(r'^\s*\d{1,4}\s*$')
# 英文书名独立行页眉(通用)
_EN_TITLE = re.compile(r'^\s*(Unequal\s*Childhoods|Chapter\s*\d+)\s*$', re.I)
# 行尾数字(页眉"章节名+页码"连写的特征,如"第一章协作培养和成就自然成长3")
_TRAILING_NUM = re.compile(r'\d\s*$')


def clean_page(text: str) -> list[str]:
    lines = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if _PAGEHEAD.match(s) or _PAGENUM.match(s) or _EN_TITLE.match(s):
            continue
        lines.append(s)
    return lines


def page_text(ocr_dir: Path, idx: int) -> str:
    p = ocr_dir / f'p{idx:04d}.txt'
    return p.read_text(encoding='utf-8') if p.exists() else ''


def find_chapter_anchors(ocr_dir: Path, total_pages: int) -> list[tuple[int, str]]:
    """扫描每页,找"第X章/附录X/注释/参考文献"整行标题。

    规则:
      - 目录页(一页出现 >=3 个标题)整体跳过
      - 同一主标题(如"附录二")只取首次出现的页;后续页的页眉/引用行忽略
      - 标题页需有正文(>2 行长句)才认定为章节起始页
      - 章节标题页常为"第X章" + 副标题(下一行),合并成完整标题
    """
    per_page: list[tuple[int, str]] = []
    for pg in range(total_pages):
        text = page_text(ocr_dir, pg)
        lines = text.splitlines()
        # 候选标题行: 行首匹配 + 非数字结尾(排除页眉) + 长度 ≤40
        # 且标题行须为短行(标题本身), 排除"第三部分将展现..."这类正文引用
        hits = []
        for ln in lines:
            s = ln.strip()
            if not _TITLE_RE.match(s) or _TRAILING_NUM.search(s) or len(s) > 40:
                continue
            # 标题行应短(≤20 字)或为"第X章+空格+副标题"形式(≤40);
            # 正文引用(如"第三部分将展现...")通常更长且以动词开头,靠长度过滤
            hits.append(s)
        if len(hits) >= 3:          # 目录页
            continue
        if hits:
            body_len = sum(1 for l in lines if len(l.strip()) > 10)
            if body_len >= 3:
                title = hits[0]
                # 纯主标题(如"第一章")时,合并下一行副标题
                if len(title) <= 6 and _TITLE_RE.match(title):
                    idx = next(i for i, l in enumerate(lines) if l.strip() == title)
                    if idx + 1 < len(lines):
                        nxt = lines[idx + 1].strip()
                        # 副标题:非页码/页眉/脚注标记/括号标注/数字结尾,长度 < 40 的短行
                        if (nxt and len(nxt) < 40
                                and not _PAGENUM.match(nxt)
                                and not _PAGEHEAD.match(nxt)
                                and not _TRAILING_NUM.search(nxt)
                                and not re.match(r'^[\[\{（(]?\s*\d+\s*[\]\}）)]?$', nxt)
                                and not nxt.startswith('（') and not nxt.startswith('[')
                                and not nxt.startswith('{')):
                            title = f'{title} {nxt}'
                per_page.append((pg, title))
    # 同主标题只保留首次(主标题 = "第X章"/"附录X"前缀)
    seen: set[str] = set()
    out: list[tuple[int, str]] = []
    for pg, title in per_page:
        m = _TITLE_RE.match(title)
        key = m.group(0) if m else title
        if key not in seen:
            seen.add(key)
            out.append((pg, title))
    return sorted(out, key=lambda x: x[0])


def build():
    ap = argparse.ArgumentParser()
    ap.add_argument('ocr_dir')
    ap.add_argument('out_work_dir')
    ap.add_argument('book_title')
    ap.add_argument('author', nargs='?', default='')
    ap.add_argument('--pdf', default=None, help='原始 PDF(提取封面)')
    ap.add_argument('--chapters', default=None,
                    help='手动指定章节: "11:第一章 xxx,24:第二章 xxx,..."(起始页:标题)')
    args = ap.parse_args()

    ocr_dir = Path(args.ocr_dir)
    work = Path(args.out_work_dir)
    work.mkdir(parents=True, exist_ok=True)

    # 总页数 = ocr 文件数
    pages = sorted(ocr_dir.glob('p*.txt'))
    total = len(pages)

    if args.chapters:
        chapters_raw = [(int(p.split(':', 1)[0]), p.split(':', 1)[1].strip())
                        for p in args.chapters.split(',')]
    else:
        chapters_raw = find_chapter_anchors(ocr_dir, total)
        if not chapters_raw:
            print('未自动识别到章节标题,请用 --chapters 手动指定', file=sys.stderr)
            return 1

    chapters = []
    for i, (start, title) in enumerate(chapters_raw):
        end = chapters_raw[i + 1][0] if i + 1 < len(chapters_raw) else total
        parts = []
        for pg in range(start, end):
            parts.extend(clean_page(page_text(ocr_dir, pg)))
        body = '\n'.join(parts).strip()
        chapters.append({'title': title, 'text': body})

    # 封面
    cover = None
    if args.pdf:
        doc = pymupdf.open(args.pdf)
        if doc.page_count > 0:
            imgs = doc[0].get_images(full=True)
            if imgs:
                xref = imgs[0][0]
                pix = pymupdf.Pixmap(doc, xref)
                if pix.n - pix.alpha >= 4:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                cover = pix.tobytes('png')
        doc.close()

    book = {
        'title': args.book_title,
        'author': args.author,
        'cover': base64.b64encode(cover).decode() if cover else None,
        'source_format': 'pdf(ocr)',
        'chapters': [{'index': i + 1, 'title': c['title'], 'text': c['text'], 'chunks': []}
                     for i, c in enumerate(chapters)],
    }
    total_chars = sum(len(c['text']) for c in chapters)
    print(f'章节数: {len(chapters)}  总字数: {total_chars}')
    for c in chapters:
        print(f'  {c["title"][:30]:<34} {len(c["text"]):>6} 字')
    (work / 'book.json').write_text(json.dumps(book, ensure_ascii=False, indent=1), encoding='utf-8')
    print('saved:', work / 'book.json')
    return 0


if __name__ == '__main__':
    sys.exit(build())
