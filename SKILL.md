---
name: book2audiobook
description: '把 PDF/EPUB/MOBI/AZW3 电子书转成 Navidrome 入库的有声读物(自然语言驱动: CLI book2audio + scripts/convert.py 统一入口)'
---

# book2audiobook — 电子书 → 有声读物(Navidrome 入库)

## 何时用
用户有一本电子书(pdf / epub / mobi / azw3 / txt),希望转成 TTS 有声读物,放入自部署的 Navidrome 收听(车上/通勤场景)。

## 核心入口(优先使用)
**`scripts/convert.py`** 是自然语言统一入口:一条命令完成 提取→(OCR)→分块→合成→打包,支持章节筛选/换音色/试听/部署。用户说人话,agent 按下表翻译:

| 用户说 | 命令 |
|---|---|
| "把这本书转成有声书" | `python scripts/convert.py <书> --title <书名> --author <作者>` |
| "只合成第3-5章" | `... --chapters 3-5` |
| "用男声" | `... --voice 男声` |
| "先合成第一章试听" | `... --chapters 1 --skip-package --preview 4` |
| "接着上次继续" | `python scripts/convert.py <work目录>` |
| "换音色对比" | `python scripts/convert.py --voice-demo` |
| "配置检查" | `python scripts/convert.py --init` |

完整映射表见 **`docs/自然语言使用.md`**;音色别名(温柔淑女/男声/Vivi/高冷御姐...)见 convert.py 的 `VOICE_ALIAS`。

## 前置(首次使用)
- 一键安装(Windows):运行 `install.ps1`(检测 Python→建 venv→装依赖→生成 ini→自检)。
- 手动安装:
  ```
  python -m venv .venv
  .venv\Scripts\python -m pip install -e ".[edge]"          # Windows
  .venv\Scripts\python -m pip install rapidocr_onnxruntime  # 扫描版 PDF 用
  ```
  pip 慢可加 `-i https://pypi.tuna.tsinghua.edu.cn/simple`。
- 密钥(主引擎 TTS2.0 必需):`book2audio.ini` 里填 `openspeech_api_key`
  (火山语音技术控制台创建;与方舟 ARK_API_KEY 是两套独立 key,勿混用)。
- 格式依赖:mobi / azw3 需 Calibre;扫描版 PDF 自动走 OCR。

## TTS 引擎
- **TTS2.0(推荐主引擎)**:`seed-tts-2.0` 资源 + uranus 2.0 音色(温柔淑女等),速度约 4-6 秒/块,全书 1.5-2 小时。
- `edge`(兜底):免费、无需 Key,自然度略低。
- `seed`(生成式):慢 8-10 倍,仅特殊段落用。
- LLM 情感标注:方舟 `doubao-seed-evolving` 走 Responses API(见 docs/火山语音TTS接入.md)。

## 工作流(标准)
1. 确认书文件存在、格式、大小;扫描版 PDF 需 OCR(convert.py 自动)。
2. 合成前建议先试听:`--chapters 1 --skip-package --preview 4`(自动拼接前 4 块并播放)。
3. 真实合成(整本或指定章节,断点续传):
   ```
   python scripts/convert.py <书> --title <书名> --author <作者> --voice 温柔淑女
   ```
4. 汇报交付目录 `<输出目录>/<书名>/`(mp3 + lrc + cover),提醒用户拷入 Navidrome MusicFolder(建议单独"有声书"库),自动扫描入库。

## 注意
- 不修改原书文件;产物只在输出目录。
- 版权:仅限用户自购书籍的个人收听用途。
- 局部合成(未合成全书)不会打包,脚本会提示。
- 音色必须 uranus 2.0 系列(`*_uranus_bigtts`);mars 1.0 音色报 resource mismatch。
- 错误码速查:55000000 音色不匹配 / 45000010 key 无效 / 45000030 未开通(见 docs/火山语音TTS接入.md §9)。
