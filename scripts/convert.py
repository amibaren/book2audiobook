"""book2audio 自然语言统一入口:一条命令完成 提取/分块/合成/打包。

这是给 LLM/agent 使用的入口 —— 把用户的自然语言请求翻译成这里的一个参数组合:

  python scripts/convert.py <书或工作目录> [选项]

核心选项:
  --title <书名> --author <作者>      # 不填则自动识别(PDF 元数据/首页文本/文件名)
  --chapters 1-12 | 1,3,5 | 12       # 只合成指定章节(默认全部)
  --voice 温柔淑女|Vivi|反卷青年       # 音色别名(见 VOICE_ALIAS)
  --engine tts2|seed|edge            # 合成引擎(默认 tts2)
  --max-chars 900                    # 单块最大字数
  --out output                       # 交付目录(默认 output)
  --work <目录>                      # 工作目录(默认 .output.work;多书用 work/<书名>)
  --force                            # 重新合成已存在的块
  --skip-package                     # 只合成不打包
  --preview N                        # 合成后拼接前 N 块为试听并自动播放
  --deploy <NavidromeMusicFolder>    # 打包后拷入 Navidrome
  --voice-demo                       # 快速合成同一句话多个音色对比(不播放)
  --init                             # 首次配置向导(检查 key/代理/依赖)

自动判断输入类型:
  - 文字版 PDF/EPUB/TXT/MOBI → 直接提取
  - 扫描版 PDF(无文本层) → 自动 OCR + build_book
  - 已有 work 目录(含 book.json) → 断点续传

返回值: 0 成功; 非 0 失败。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
PY = sys.executable
DEFAULT_WORK = ROOT / '.output.work'
# CLI 源码定位: 优先发布包内的 src/(随 skill 一起分发), 回退到本机 skill 目录
_LOCAL_SRC = SCRIPTS.parent / 'src'
if not (_LOCAL_SRC / 'book2audio' / '__init__.py').exists():
    _LOCAL_SRC = Path(r'C:\Users\ALIENWARE\AppData\Roaming\reasonix\skills\book2audiobook\src')
SKILL_SRC = _LOCAL_SRC

# ---------------------------------------------------------------- CLI 定位

def _cli_module_args() -> list[str]:
    """返回 ['-m', 'book2audio.cli'] 或 skill venv 的 console script。"""
    try:
        import book2audio  # noqa: F401
        return ['-m', 'book2audio.cli']
    except ImportError:
        pass
    exe = SKILL_SRC / '.venv' / 'Scripts' / 'book2audio.exe'
    if exe.exists():
        return [str(exe)]
    return ['-m', 'book2audio.cli']


CLI = _cli_module_args()

# ---------------------------------------------------------------- 音色

VOICE_ALIAS = {
    '温柔淑女': 'zh_female_wenroushunv_uranus_bigtts',
    '淑女': 'zh_female_wenroushunv_uranus_bigtts',
    'vivi': 'zh_female_vv_uranus_bigtts',
    '邻家女孩': 'zh_female_linjianvhai_uranus_bigtts',
    '甜美小源': 'zh_female_tianmeixiaoyuan_uranus_bigtts',
    '高冷御姐': 'zh_female_gaolengyujie_uranus_bigtts',
    '儿童绘本': 'zh_female_xiaoxue_uranus_bigtts',
    '反卷青年': 'zh_male_fanjuanqingnian_uranus_bigtts',
    '男声': 'zh_male_fanjuanqingnian_uranus_bigtts',
    '爽快思思': 'zh_female_shuangkuaisisi_uranus_bigtts',
    '温柔小哥': 'zh_male_wenrouxiaoge_uranus_bigtts',
}


def resolve_voice(name: str) -> str:
    if not name:
        return VOICE_ALIAS['温柔淑女']
    return VOICE_ALIAS.get(name.strip(), name.strip())


# ---------------------------------------------------------------- 元数据自动识别

def detect_title_author(inp: Path, pdf: str | None = None, ocr_first_pages: list[str] | None = None,
                        given_title: str | None = None, given_author: str = '') -> tuple[str, str]:
    """自动识别书名/作者。优先级: 参数 > PDF 元数据 > OCR 首页文本 > 文件名。"""
    title, author = given_title or '', given_author or ''
    # 1. PDF 元数据
    if not title or not author:
        try:
            import pymupdf
            doc = pymupdf.open(str(pdf or inp))
            meta = doc.metadata or {}
            doc.close()
            if not title and meta.get('title'):
                title = meta['title'].strip()
            if not author and meta.get('author'):
                author = meta['author'].strip()
        except Exception:
            pass
    # 2. OCR 首页文本(封面/版权页, 常见模式: 《书名》 或 书名 独立行)
    if (not title or not author) and ocr_first_pages:
        joined = '\n'.join(ocr_first_pages[:3])
        m = re.search(r'《([^》]{2,40})》', joined)
        if not title and m:
            title = m.group(1)
        # 常见作者模式: [美] 某某 著 / 某某 著 / 某某著
        m2 = re.search(r'[\[\（(]?[美英日法德]?[\]）)]?\s*[\u4e00-\u9fff·]{2,20}?\s*著', joined)
        if not author and m2:
            author = m2.group(0).strip(' 著()（）')
    # 3. 文件名
    if not title:
        title = inp.stem if not inp.is_dir() else inp.name
    return title.strip() or '未命名书籍', author.strip()


# ---------------------------------------------------------------- 错误翻译

ERR_TRANSLATE = [
    (r'55000000.*resource ID is mismatched', '音色与资源不匹配: 这本书需要 2.0(uranus)音色,1.0(mars)音色不可用。请换 --voice(如 温柔淑女)重试。'),
    (r'45000010.*Invalid X-Api-Key', 'API Key 无效: 请检查 book2audio.ini 里 openspeech_api_key 是否正确,或重新在语音技术控制台创建。'),
    (r'45000030.*not granted', '该服务未开通: 请到火山语音技术控制台开通 豆包语音合成大模型2.0(seed-tts-2.0)。'),
    (r'45001115.*speaker.*not found', '音色不存在: 该音色 id 不在账号音色库,请换一个 uranus 2.0 音色(如 --voice 温柔淑女)。'),
    (r'HTTP 401', '鉴权失败: openspeech_api_key 无效或已过期,请检查配置。'),
    (r'HTTP 403', '没有权限: 资源未开通或 key 无权访问,请检查开通状态。'),
    (r'HTTP 404', '接口或资源不存在: 请确认已开通语音合成服务,或稍后重试。'),
    (r'HTTP 429|45000292', '并发超限: 请求太密集,脚本会自动重试,请耐心等待。'),
]


def translate_error(text: str) -> str:
    for pat, msg in ERR_TRANSLATE:
        if re.search(pat, text, re.I):
            return msg
    return ''


def run(cmd: list[str]) -> int:
    print(f'>>> {" ".join(str(c) for c in cmd)}', flush=True)
    return subprocess.call(cmd, cwd=str(ROOT))


# ---------------------------------------------------------------- 试听

def id3_size(b: bytes) -> int:
    if b[:3] == b'ID3':
        sz = b[6] & 0x7f
        for x in b[7:10]:
            sz = (sz << 7) | (x & 0x7f)
        return 10 + sz
    return 0


def make_preview(staging: Path, out_file: Path, n: int = 4) -> str:
    """拼接前 n 块为试听 mp3,返回文件路径。"""
    mp3s = sorted(staging.glob('*.mp3'))[:n]
    if not mp3s:
        return ''
    parts = []
    for p in mp3s:
        data = p.read_bytes()
        parts.append(data[id3_size(data):])
    out_file.write_bytes(b''.join(parts))
    return str(out_file)


def open_player(path: str) -> None:
    """跨平台打开系统默认播放器。"""
    if sys.platform == 'win32':
        os.startfile(path)  # noqa: S606  # 本机默认播放器,路径由脚本生成
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', path])
    else:
        subprocess.Popen(['xdg-open', path])


# ---------------------------------------------------------------- 配置向导

def cmd_init() -> int:
    print('=== book2audio 配置检查 ===')
    ok = True
    # ini
    ini = ROOT / 'book2audio.ini'
    if not ini.exists():
        print('  ❌ 缺少 book2audio.ini(复制 book2audio.ini.example 并填 key)')
        ok = False
    else:
        print(f'  ✅ 找到配置 {ini}')
    # key
    try:
        from book2audio.config import load_config
        cfg = load_config(str(ini), {})
    except Exception:
        cfg = None
    key = (cfg.get('openspeech_api_key') if cfg else '') or os.environ.get('OPENSPEECH_API_KEY', '')
    if key:
        print('  ✅ openspeech_api_key 已配置')
    else:
        print('  ❌ openspeech_api_key 未配置 → 语音技术控制台创建后填入 ini')
        ok = False
    # 代理
    if os.environ.get('HTTP_PROXY') or os.environ.get('HTTPS_PROXY'):
        print('  ✅ 代理已设置')
    else:
        print('  ⚠️ 未检测到代理环境变量;若网络受限需走代理,脚本内置了 127.0.0.1:1088')
    # 依赖
    for mod in ('pymupdf', 'mutagen', 'requests'):
        try:
            __import__('pymupdf' if mod == 'pymupdf' else mod)
            print(f'  ✅ 依赖 {mod} 可用')
        except ImportError:
            print(f'  ❌ 缺少依赖 {mod} → 运行 install.ps1 或 pip install')
            ok = False
    print()
    print('配置就绪!' if ok else '配置未完成,请按上面提示处理。')
    return 0 if ok else 1


# ---------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('input', nargs='?', default=None, help='电子书文件,或已存在的 work 目录')
    ap.add_argument('--title', default=None)
    ap.add_argument('--author', default='')
    ap.add_argument('--chapters', default=None, help='章节范围 1-12 / 1,3,5 / 12')
    ap.add_argument('--voice', default='温柔淑女')
    ap.add_argument('--engine', default='tts2', choices=['tts2', 'seed', 'edge'])
    ap.add_argument('--max-chars', type=int, default=900)
    ap.add_argument('--out', default='output')
    ap.add_argument('--work', default=None, help='工作目录(默认 .output.work;多书用 work/<书名>)')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--skip-package', action='store_true')
    ap.add_argument('--preview', type=int, default=0, metavar='N', help='合成后拼接前 N 块为试听并自动播放')
    ap.add_argument('--deploy', default=None, metavar='DIR', help='打包后拷入 Navidrome MusicFolder')
    ap.add_argument('--voice-demo', action='store_true', help='合成同一句话多个音色到 output/voice_demo/ 供对比')
    ap.add_argument('--init', action='store_true', help='首次配置向导')
    args = ap.parse_args()

    if args.init:
        return cmd_init()

    if args.voice_demo:
        return voice_demo()

    if not args.input:
        print('[error] 需要提供电子书路径或 work 目录(--init 可先做配置检查)', file=sys.stderr)
        return 1

    inp = Path(args.input)
    work = Path(args.work) if args.work else DEFAULT_WORK

    # ---- 阶段 1: 提取(自动判断输入类型) ----
    ocr_first_pages: list[str] = []
    if inp.is_dir():
        book_json = inp / 'book.json'
        if book_json.exists():
            work = inp
            print(f'使用已有工作目录: {work}(断点续传)')
        else:
            print(f'[error] {inp} 不是有效的 work 目录(缺 book.json)', file=sys.stderr)
            return 1
    else:
        ext = inp.suffix.lower()
        if ext in ('.epub', '.txt', '.mobi', '.azw3'):
            if run([PY] + CLI + ['extract', str(inp), '--out', args.out]) != 0:
                return 1
        elif ext == '.pdf':
            try:
                import pymupdf
                doc = pymupdf.open(str(inp))
                sample = sum(len(doc[i].get_text().strip()) for i in range(min(10, doc.page_count)))
                doc.close()
            except Exception:
                sample = 0
            if sample < 50:
                ocr_dir = ROOT / '.tmp_ocr' / 'fulltext'
                print(f'检测到扫描版 PDF,开始 OCR(输出到 {ocr_dir})...')
                if run([PY, str(SCRIPTS / 'ocr_pdf.py'), str(inp), str(ocr_dir)]) != 0:
                    return 1
                ocr_first_pages = [(ocr_dir / f'p{i:04d}.txt').read_text(encoding='utf-8')
                                   for i in range(min(3, len(list(ocr_dir.glob('p*.txt')))))
                                   if (ocr_dir / f'p{i:04d}.txt').exists()]
            else:
                if run([PY] + CLI + ['extract', str(inp), '--out', args.out]) != 0:
                    return 1
        else:
            print(f'[error] 不支持的格式: {ext}(支持 pdf/epub/txt/mobi/azw3)', file=sys.stderr)
            return 1

    # ---- 阶段 1.5: 书名/作者自动识别(仅新提取时) ----
    book_json = work / 'book.json'
    if not book_json.exists():
        # 提取流程把 book.json 写到了 CLI 约定位置(output/.书名.work),这里重新定位
        cand = list(Path(args.out).glob('.*.work/book.json'))
        if cand:
            book_json = cand[0]
            work = book_json.parent
    if not book_json.exists():
        print(f'[error] 提取后未生成 book.json(查找过 {work} 与 output/.*.work)', file=sys.stderr)
        return 1

    book = json.loads(book_json.read_text(encoding='utf-8'))
    # 自动识别书名作者(若 book.json 里没有,且用户没给)
    if not args.title or not args.author:
        t, a = detect_title_author(inp, pdf=str(inp) if inp.suffix.lower() == '.pdf' else None,
                                   ocr_first_pages=ocr_first_pages,
                                   given_title=args.title, given_author=args.author)
        if not book.get('title') or book.get('title') == inp.stem:
            book['title'] = t
        if not book.get('author'):
            book['author'] = a
        book_json.write_text(json.dumps(book, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'书名: {book.get("title")}  作者: {book.get("author") or "未知"}  章节: {len(book["chapters"])}')

    # ---- 阶段 2: 章节筛选 ----
    total = len(book['chapters'])
    if args.chapters:
        try:
            for part in args.chapters.split(','):
                if '-' in part:
                    a, b = part.split('-', 1)
                    assert 1 <= int(a) <= int(b) <= total
                else:
                    assert 1 <= int(part) <= total
        except Exception:
            print(f'[error] 章节范围无效: {args.chapters}(全书 1-{total})', file=sys.stderr)
            return 1

    # ---- 阶段 3: 合成 ----
    speaker = resolve_voice(args.voice)
    print(f'音色: {args.voice} → {speaker}')
    synth_rc = 0
    if args.engine == 'tts2':
        cmd = [PY, str(SCRIPTS / 'synth_tts2.py'), str(book_json),
               '--speaker', speaker, '--max-chars', str(args.max_chars)]
        if args.chapters:
            cmd += ['--chapters', args.chapters]
        if args.force:
            cmd += ['--force']
        synth_rc = run(cmd)
    elif args.engine == 'seed':
        print('[warn] seed 引擎较慢(约 12-16h/29万字),建议 tts2')
        cmd = [PY, str(SCRIPTS / 'synth_seed.py'), str(book_json), '--speaker', speaker]
        if args.chapters:
            cmd += ['--chapters', args.chapters]
        if args.force:
            cmd += ['--force']
        synth_rc = run(cmd)
    else:
        cmd = [PY] + CLI + ['synth', str(book_json), '--provider', 'edge', '--out', args.out]
        if args.chapters:
            cmd += ['--chapters', args.chapters]
        synth_rc = run(cmd)
    if synth_rc != 0:
        print('[error] 合成失败,请查看上方日志;常见原因与解决办法:')
        print('  ' + translate_error('合成失败') + ' 或见 docs/火山语音TTS接入.md', file=sys.stderr)
        return 1

    # ---- 阶段 4: 试听(局部合成时) ----
    if args.preview > 0:
        staging = work / 'staging'
        preview = ROOT / args.out / 'preview.mp3'
        made = make_preview(staging, preview, args.preview)
        if made:
            print(f'🎧 试听文件: {made}')
            try:
                open_player(made)
            except Exception:
                pass

    # ---- 阶段 5: 打包 + 验收 ----
    if not args.skip_package:
        total_chunks = sum(max(1, len(c.get('chunks', []))) for c in book['chapters'])
        staged = len(list((work / 'staging').glob('*.mp3'))) if (work / 'staging').exists() else 0
        if staged < total_chunks:
            print(f'[warn] 仅 {staged}/{total_chunks} 块已合成,跳过打包(用 --skip-package 试听,'
                  f'或先合成全书再打包)', file=sys.stderr)
            return 0
        import base64 as _b64
        try:
            from book2audio.models import BookDoc, Chapter
            from book2audio.package import package as do_package
        except ImportError:
            sys.path.insert(0, str(SKILL_SRC))
            from book2audio.models import BookDoc, Chapter
            from book2audio.package import package as do_package
        try:
            chapters = [Chapter(index=c['index'], title=c['title'], text=c.get('text', ''),
                                chunks=c.get('chunks', [])) for c in book['chapters']]
            bdoc = BookDoc(title=book.get('title', inp.stem), author=book.get('author', ''),
                           cover=_b64.b64decode(book['cover']) if book.get('cover') else None,
                           chapters=chapters)
            out_dir = Path(args.out) / bdoc.title
            files = do_package(bdoc, out_dir, work / 'staging')
            # 验收报告: 校验交付目录完整性
            try:
                subprocess.call([PY, str(SCRIPTS / 'verify.py'), str(work), '--out', str(out_dir)],
                                cwd=str(ROOT))
            except Exception:
                pass
            print(f'\n✅ 完成: {out_dir}/ 共 {len(files)} 个文件,可拷入 Navidrome MusicFolder')
            if args.deploy:
                import shutil
                dst = Path(args.deploy) / bdoc.title
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(out_dir, dst)
                print(f'📦 已部署到 Navidrome: {dst}(约 1 分钟后自动扫描入库)')
        except Exception as e:
            print(f'[error] 打包失败: {e}', file=sys.stderr)
            return 1
    return 0


def voice_demo() -> int:
    """合成同一句话多个音色,输出到 output/voice_demo/,供用户对比选择。"""
    cfg = _load_cfg()
    key = cfg.get('openspeech_api_key') if cfg else ''
    if not key:
        print('[error] 缺少 openspeech_api_key,先运行 --init 或配置 ini', file=sys.stderr)
        return 1
    text = '这是一段试听语音。在一个晚春的下午,孩子们在花园里奔跑嬉戏,笑声回荡。'
    out_dir = ROOT / 'output' / 'voice_demo'
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f'合成 {len(VOICE_ALIAS)} 个音色到 {out_dir} ...')
    import base64, gzip
    import requests
    proxies = {'http': 'http://127.0.0.1:1088', 'https': 'http://127.0.0.1:1088'}
    base_url = cfg.get('openspeech_base_url') or 'https://openspeech.bytedance.com/api/v3/tts'
    for name, speaker in VOICE_ALIAS.items():
        body = {'req_params': {'text': text, 'speaker': speaker,
                               'audio_params': {'format': 'mp3', 'sample_rate': 24000}}}
        try:
            r = requests.post(base_url.rstrip('/') + '/unidirectional',
                              headers={'X-Api-Key': key, 'X-Api-Resource-Id': 'seed-tts-2.0',
                                       'Content-Type': 'application/json', 'Connection': 'keep-alive'},
                              json=body, timeout=120, proxies=proxies, stream=True)
            parts, err, finished = [], None, False
            for raw_line in r.iter_lines():
                if not raw_line:
                    continue
                data = raw_line
                if raw_line[:2] == b'\x1f\x8b':
                    try:
                        data = gzip.decompress(raw_line)
                    except Exception:
                        pass
                try:
                    j = json.loads(data.decode('utf-8', 'replace'))
                except Exception:
                    continue
                if j.get('code') == 20000000:
                    finished = True
                elif j.get('code') not in (0, 20000001, None):
                    err = f'code={j.get("code")} {j.get("message","")[:80]}'
                b64 = j.get('data')
                if b64:
                    parts.append(base64.b64decode(b64))
            r.close()
            if parts and finished:
                fn = out_dir / f'{name}.mp3'
                fn.write_bytes(b''.join(parts))
                print(f'  ✅ {name} → {fn.name}')
            else:
                print(f'  ❌ {name}: {err or "无音频"}')
        except Exception as e:
            print(f'  ❌ {name}: {e}')
    print(f'\n对比试听完成,请打开 {out_dir} 试听,然后把喜欢的音色名告诉我(如 --voice 高冷御姐)')
    return 0


def _load_cfg():
    try:
        from book2audio.config import load_config
        ini = ROOT / 'book2audio.ini'
        if ini.exists():
            return load_config(str(ini), {})
    except Exception:
        pass
    return None


if __name__ == '__main__':
    sys.exit(main())
