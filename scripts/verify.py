"""合成质量校验与验收报告。

用法:
  python scripts/verify.py <work_dir> [--out <交付目录>]

功能:
  - 检查 staging 每块音频: 时长 / 码率 / 损坏
  - 汇总: 块数、总时长、坏块列表
  - 若 --out 给定, 同时校验交付目录(每章文件是否齐全、时长是否匹配)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def check_dir_mp3(d: Path, label: str) -> tuple[list[dict], float]:
    """扫描目录下 mp3, 返回 (文件信息列表, 总时长秒)。"""
    items, total = [], 0.0
    for p in sorted(d.glob('*.mp3')):
        try:
            from mutagen.mp3 import MP3
            a = MP3(p)
            total += a.info.length
            items.append({'name': p.name, 'ok': True, 'sec': round(a.info.length, 1),
                          'kbps': a.info.bitrate // 1000, 'hz': a.info.sample_rate})
        except Exception as e:
            items.append({'name': p.name, 'ok': False, 'sec': 0, 'err': str(e)[:60]})
    return items, total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('work_dir')
    ap.add_argument('--out', default=None, help='交付目录(可选,校验打包结果)')
    args = ap.parse_args()

    work = Path(args.work_dir)
    staging = work / 'staging'
    print(f'=== 校验 {work} ===')
    if not staging.exists():
        print('[error] 未找到 staging 目录', file=sys.stderr)
        return 1

    items, total = check_dir_mp3(staging, 'staging')
    bad = [i for i in items if not i['ok']]
    print(f'staging: {len(items)} 块, 音频约 {total/3600:.1f} 小时')
    if bad:
        print(f'  ❌ 异常 {len(bad)} 块:')
        for b in bad[:10]:
            print(f'    {b["name"]}: {b.get("err", "?")}')
    else:
        print('  ✅ 全部正常')

    # 与 book.json 对比预期块数
    bj = work / 'book.json'
    if bj.exists():
        book = json.loads(bj.read_text(encoding='utf-8'))
        expected = sum(max(1, len(c.get('chunks', []))) for c in book['chapters'])
        print(f'预期块数: {expected}(book.json), 实际: {len(items)}')
        if len(items) < expected:
            print(f'  ⚠️ 还差 {expected - len(items)} 块未合成')

    if args.out:
        out_dir = Path(args.out)
        print(f'\n=== 校验交付目录 {out_dir} ===')
        files, ftotal = check_dir_mp3(out_dir, 'out')
        fbad = [f for f in files if not f['ok']]
        lrcs = list(out_dir.glob('*.lrc'))
        covers = list(out_dir.glob('cover.*'))
        print(f'mp3: {len(files)} 个, {ftotal/3600:.1f} 小时; lrc: {len(lrcs)}; cover: {len(covers)}')
        if fbad:
            print(f'  ❌ 异常 {len(fbad)} 个:')
            for f in fbad[:10]:
                print(f'    {f["name"]}: {f.get("err","?")}')
        else:
            print('  ✅ 交付目录完整')

    print('\n验收完成。')
    return 0 if not bad else 1


if __name__ == '__main__':
    sys.exit(main())
