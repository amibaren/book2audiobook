"""TTS2.0 (seed-tts-2.0) 合成器: openspeech HTTP 单向流式,断点续传。

用法:
  python scripts/synth_tts2.py <book.json> [--staging <dir>] [--chapters 1,3-5]
        [--speaker zh_female_wenroushunv_uranus_bigtts] [--max-chars 900] [--force]

说明:
  - 读取 book.json,tts_friendly + 分块,逐块调 openspeech /api/v3/tts/unidirectional
  - 输出 staging/<pos:04d>.mp3(全局顺序编号,与 CLI package 约定一致)
  - 已存在的块自动跳过(断点续传);--force 重合成
  - 音色默认温柔淑女 2.0(zh_female_wenroushunv_uranus_bigtts),可选其他 uranus 2.0 音色
  - 鉴权用 ini 的 openspeech_api_key / openspeech_base_url(见 book2audio.ini)
"""
import argparse
import base64
import gzip
import json
import sys
import time
from pathlib import Path

# CLI 源码: 优先发布包内 src/, 回退本机 skill 目录
_SRC = Path(__file__).resolve().parent.parent / 'src'
if not (_SRC / 'book2audio' / '__init__.py').exists():
    _SRC = Path(r'C:\Users\ALIENWARE\AppData\Roaming\reasonix\skills\book2audiobook\src')
sys.path.insert(0, str(_SRC))
from book2audio.chunk import tts_friendly, split_chunks
from book2audio.config import load_config
import requests

CFG_INI = str(Path(__file__).resolve().parent.parent / 'book2audio.ini')
PROXY = 'http://127.0.0.1:1088'


def parse_ranges(spec):
    out = set()
    for part in spec.split(','):
        part = part.strip()
        if '-' in part:
            a, b = part.split('-', 1)
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out


def synth_chunk(text: str, out: Path, key: str, base_url: str, speaker: str,
                resource: str, retries: int = 3) -> bool:
    body = {'req_params': {'text': text, 'speaker': speaker,
                           'audio_params': {'format': 'mp3', 'sample_rate': 24000}}}
    proxies = {'http': PROXY, 'https': PROXY}
    headers = {'X-Api-Key': key, 'X-Api-Resource-Id': resource,
               'Content-Type': 'application/json', 'Connection': 'keep-alive'}
    url = base_url.rstrip('/') + '/unidirectional'
    last = ''
    for attempt in range(retries):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=300, proxies=proxies, stream=True)
            if r.status_code != 200:
                last = f'HTTP {r.status_code} {r.text[:200]}'
                r.close()
                time.sleep(2)
                continue
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
                code = j.get('code')
                if code == 20000000:            # 结束帧
                    finished = True
                elif code not in (0, 20000001, None):
                    err = f'code={code} {j.get("message", "")[:100]}'
                b64 = j.get('data')
                if b64:
                    try:
                        parts.append(base64.b64decode(b64))
                    except Exception:
                        pass
            r.close()
            if err:
                last = err
                time.sleep(2)
                continue
            if not finished:
                # 流被截断(无结束帧): 不写文件,重试
                last = f'stream ended without terminal frame ({len(parts)} audio parts)'
                time.sleep(2)
                continue
            audio = b''.join(parts)
            if len(audio) < 1000:
                last = f'audio too small: {len(audio)}B'
                time.sleep(2)
                continue
            # 原子写入: 先写临时文件再替换,避免中断留下半个 mp3 被续传跳过
            tmp = out.with_suffix('.mp3.tmp')
            tmp.write_bytes(audio)
            tmp.replace(out)
            return True
        except Exception as e:
            last = str(e)
            time.sleep(2)
    print(f'  [FAIL] {out.name}: {last}')
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('book_json')
    ap.add_argument('--staging', default=None)
    ap.add_argument('--chapters', default=None)
    ap.add_argument('--speaker', default='zh_female_wenroushunv_uranus_bigtts')
    ap.add_argument('--max-chars', type=int, default=900)
    ap.add_argument('--force', action='store_true')
    args = ap.parse_args()

    cfg = load_config(CFG_INI, {})
    key = cfg.get('openspeech_api_key')
    if not key:
        print('缺少 openspeech_api_key(检查 book2audio.ini)')
        return 1
    base_url = cfg.get('openspeech_base_url') or 'https://openspeech.bytedance.com/api/v3/tts'
    resource = cfg.get('openspeech_resource_id') or 'seed-tts-2.0'

    book_path = Path(args.book_json)
    book = json.loads(book_path.read_text(encoding='utf-8'))
    staging = Path(args.staging) if args.staging else book_path.parent / 'staging'
    staging.mkdir(parents=True, exist_ok=True)

    wanted = parse_ranges(args.chapters) if args.chapters else None
    # 计算每个章节的全局块起点(基于全书顺序,保证局部合成编号与 package 全局一致)
    offsets: dict[int, int] = {}
    pos = 0
    for ch in book['chapters']:
        offsets[ch['index']] = pos
        text = tts_friendly(ch['text'])
        pos += len(split_chunks(text, max_chars=args.max_chars))

    flat = []
    for ch in book['chapters']:
        if wanted is not None and ch['index'] not in wanted:
            continue
        text = tts_friendly(ch['text'])
        chunks = split_chunks(text, max_chars=args.max_chars)
        ch['chunks'] = chunks
        for i, c in enumerate(chunks, 1):
            flat.append((offsets[ch['index']] + i, ch['index'], i, c))
    book_path.write_text(json.dumps(book, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'共 {len(flat)} 块 (每块 ≤{args.max_chars} 字, 音色: {args.speaker})')

    done, skipped, failed = 0, 0, 0
    t0 = time.time()
    for pos, cidx, i, text in flat:
        mp3 = staging / f'{pos:04d}.mp3'
        if mp3.exists() and not args.force:
            skipped += 1
            continue
        ok = synth_chunk(text, mp3, key, base_url, args.speaker, resource)
        if ok:
            done += 1
        else:
            failed += 1
        if (done + failed) % 10 == 0:
            el = time.time() - t0
            print(f'[{time.strftime("%H:%M:%S")}] 完成 {done}, 跳过 {skipped}, 失败 {failed}, 用时 {el:.0f}s', flush=True)
    el = time.time() - t0
    print(f'完成: 新增 {done}, 跳过 {skipped}, 失败 {failed}, 总用时 {el:.0f}s → {staging}')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
