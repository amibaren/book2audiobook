# -*- coding: utf-8 -*-
"""seed-audio-1.0 直出合成器:
1. 读 book.json,tts_friendly + split_chunks(max_chars=450)
2. 逐块调 openspeech /api/v3/tts/create,写 staging/{pos:04d}.mp3(断点续传)
用法: python synth_seed.py [--chapters 1,3-5] [--limit N]
"""
import argparse, base64, json, sys, time
from pathlib import Path

# CLI 源码: 优先发布包内 src/, 回退本机 skill 目录
_SRC = Path(__file__).resolve().parent.parent / 'src'
if not (_SRC / 'book2audio' / '__init__.py').exists():
    _SRC = Path(r'C:\Users\ALIENWARE\AppData\Roaming\reasonix\skills\book2audiobook\src')
sys.path.insert(0, str(_SRC))
from book2audio.chunk import tts_friendly, split_chunks
from book2audio.config import load_config
import requests

CFG_INI = r'D:\AI Projects\book2audio\book2audio.ini'
BOOK = Path(r'D:\AI Projects\book2audio\.output.work\book.json')
STAGING = Path(r'D:\AI Projects\book2audio\.output.work\staging')
MAX_CHARS = 450
PROXY = 'http://127.0.0.1:1088'
SPEAKER = 'zh_female_vv_uranus_bigtts'  # 固定音色,避免每块随机


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


def synth_chunk(text: str, out: Path, key: str, retries: int = 3) -> bool:
    body = {
        'model': 'seed-audio-1.0',
        'text_prompt': text,
        'references': [{'speaker': SPEAKER}],
        'audio_config': {'format': 'mp3', 'sample_rate': 48000, 'pitch_rate': 0, 'speech_rate': 0, 'loudness_rate': 0},
        'watermark': {},
    }
    proxies = {'http': PROXY, 'https': PROXY}
    last = ''
    for attempt in range(retries):
        try:
            r = requests.post('https://openspeech.bytedance.com/api/v3/tts/create',
                              headers={'X-Api-Key': key, 'Content-Type': 'application/json'},
                              json=body, timeout=180, proxies=proxies)
            if r.status_code != 200:
                last = f'HTTP {r.status_code} {r.text[:200]}'
                time.sleep(3)
                continue
            j = r.json()
            audio = j.get('audio')
            if not audio:
                last = f'no audio field: {str(j)[:200]}'
                time.sleep(3)
                continue
            data = base64.b64decode(audio)
            if len(data) < 1000 or data[:3] != b'ID3' and data[:2] != b'\xff\xfb' and data[:2] != b'\xff\xf3':
                last = f'bad audio magic: {data[:8]!r}'
                time.sleep(3)
                continue
            out.write_bytes(data)
            return True
        except Exception as e:
            last = str(e)
            time.sleep(3)
    print(f'  [FAIL] {out.name}: {last}')
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--chapters', default=None)
    ap.add_argument('--limit', type=int, default=0, help='最多合成 N 块(测试用)')
    args = ap.parse_args()

    cfg = load_config(CFG_INI, {})
    key = cfg.get('openspeech_api_key')
    if not key:
        print('缺少 openspeech_api_key'); return 1

    book = json.loads(BOOK.read_text(encoding='utf-8'))
    wanted = parse_ranges(args.chapters) if args.chapters else None
    STAGING.mkdir(parents=True, exist_ok=True)

    # 分块
    flat = []  # (pos, chapter_index, chunk_idx, text)
    pos = 0
    for ch in book['chapters']:
        if wanted is not None and ch['index'] not in wanted:
            continue
        text = tts_friendly(ch['text'])
        chunks = split_chunks(text, max_chars=MAX_CHARS)
        ch['chunks'] = chunks
        for i, c in enumerate(chunks, 1):
            pos += 1
            flat.append((pos, ch['index'], i, c))
    BOOK.write_text(json.dumps(book, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'共 {len(flat)} 块 (每块 ≤{MAX_CHARS} 字)')

    done, skipped, failed = 0, 0, 0
    t0 = time.time()
    for pos, cidx, i, text in flat:
        mp3 = STAGING / f'{pos:04d}.mp3'
        if mp3.exists():
            skipped += 1
            continue
        if args.limit and done >= args.limit:
            print(f'[limit] 达到测试上限 {args.limit},停止')
            break
        ok = synth_chunk(text, mp3, key)
        if ok:
            done += 1
        else:
            failed += 1
        if done % 5 == 0 or done == args.limit:
            el = time.time() - t0
            print(f'[{time.strftime("%H:%M:%S")}] 完成 {done} 块, 跳过 {skipped}, 失败 {failed}, 用时 {el:.0f}s')
    el = time.time() - t0
    print(f'完成: 新增 {done}, 跳过 {skipped}, 失败 {failed}, 总用时 {el:.0f}s → {STAGING}')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
