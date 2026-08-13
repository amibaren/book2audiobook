"""扫描版 PDF 全本 OCR:逐页渲染 -> 识别 -> 写 p####.txt,支持断点续跑。

用法:
  python scripts/ocr_pdf.py <input.pdf> <out_dir> [start_page] [end_page]

说明:
  - 输出 out_dir 下 p0000.txt ~ p0321.txt(每页一行一段文本)
  - 已存在的页跳过(断点续跑);随时 Ctrl+C 中断后重跑即可续
  - 引擎:rapidocr_onnxruntime(PaddleOCR 模型的 onnx 版,CPU 即可)
    首次运行自动下载模型;如网络受限,先设 HTTP(S)_PROXY 再跑
"""
import pymupdf, os, sys, time
from rapidocr_onnxruntime import RapidOCR


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    pdf = sys.argv[1]
    out_dir = sys.argv[2]
    start = max(0, int(sys.argv[3]) if len(sys.argv) > 3 else 0)
    os.makedirs(out_dir, exist_ok=True)

    ocr = RapidOCR()
    doc = pymupdf.open(pdf)
    total = doc.page_count
    end = min(total, int(sys.argv[4]) if len(sys.argv) > 4 else total)
    if start >= end:
        print(f'无效页码范围: start={start}, end={end}(共 {total} 页)', file=sys.stderr)
        return 1
    t0 = time.time()
    for i in range(start, end):
        outfile = os.path.join(out_dir, f'p{i:04d}.txt')
        if os.path.exists(outfile):
            continue
        pix = doc[i].get_pixmap(dpi=200)
        tmp = os.path.join(out_dir, f'_tmp{i}.png')
        pix.save(tmp)
        try:
            res, _ = ocr(tmp)
            lines = [t for _, t, _ in res] if res else []
            with open(outfile, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        if i % 10 == 0 or i == end - 1:
            el = time.time() - t0
            print(f'[{time.strftime("%H:%M:%S")}] page {i + 1}/{total}  elapsed {el:.0f}s', flush=True)
    print('OCR_ALL_DONE', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
