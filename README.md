# book2audio

把电子书(PDF / EPUB / MOBI / AZW3)转成**适合 Navidrome 入库的有声读物**:
每章一个音频文件 + 同名 LRC 文本 + 封面 + ID3 标签,拷贝进 Navidrome 的
`MusicFolder` 后由 Navidrome 自动扫描入库,即可在车上用 Symfonium / Finamp 等客户端收听。

## 📦 安装(支持自然语言/URL 方式)

对任意 AI 助手说一句话即可安装本 skill:

> **参考 https://raw.githubusercontent.com/amibaren/book2audiobook/main/SKILL.md,安装该 skill**

或手动安装:见 [install.md](install.md)(一键 `install.ps1` + 配 key)。

> 本文档为实测后的使用说明。推荐主引擎为**火山语音技术 TTS2.0**(seed-tts-2.0,声音自然、
> 速度快);edge-tts 作为免费兜底。接入细节与踩坑记录见 [docs/火山语音TTS接入.md](docs/火山语音TTS接入.md)。

## 快速开始(标准流程)

```powershell
# 1. 安装(独立 venv)
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[edge]" -i https://pypi.tuna.tsinghua.edu.cn/simple
# 扫描版 PDF 还需 OCR 引擎:
.venv\Scripts\python -m pip install rapidocr_onnxruntime

# 2. 配置密钥(book2audio.ini,见下)
#    主引擎需 openspeech_api_key(语音技术控制台)

# 3. 文字版电子书:一条龙
book2audio all 我的书.epub --config book2audio.ini --out output
#    dry 联调(不真正合成): 加 --provider dry
```

### 扫描版 PDF(无文字层,先 OCR)

```powershell
# ① 全本 OCR(支持断点续跑;约 5 秒/页)
python scripts/ocr_pdf.py 书.pdf .tmp_ocr/fulltext
# ② 构造 book.json(自动识别章节标题,提取封面)
python scripts/build_book.py .tmp_ocr/fulltext .output.work "书名" "作者" --pdf 书.pdf
# ③ 分块 + TTS2.0 合成(断点续传,温柔淑女 2.0 音色)
python scripts/synth_tts2.py .output.work/book.json
# ④ 打包(Navidrome 交付目录)
book2audio package .output.work/book.json --config book2audio.ini --out output
```

> 关于 `book.json` 路径:`book2audio package` 期望的中间文件位于
> `<out>/.<书名>.work/book.json`,因此统一用 `.output.work/` 作为工作目录即可。
> 如果自行换目录,注意 `synth_tts2.py --staging` 与 `package` 的 staging 目录要一致。

## 产物结构

```
output/我的书/
  01-第一章 出发.mp3
  01-第一章 出发.mp3.lrc
  02-第二章 山路.mp3
  02-第二章 山路.mp3.lrc
  cover.jpg
```

## 子命令

| 命令                  | 作用                                                   |
| ------------------- | ---------------------------------------------------- |
| `extract 书`         | 提取为 `book.json`(章节/封面/元数据;支持 epub/pdf/mobi/azw3/txt) |
| `chunk book.json`   | TTS 友好化 + 按 `--max-chars` 分块(可选 LLM 情感标注)            |
| `synth book.json`   | 逐块合成(`--chapters 1,3-5` 只合成指定章节;已生成自动跳过)             |
| `package book.json` | 命名 / ID3 / LRC / 封面,打包为交付目录                          |
| `all 书`             | 一条龙                                                  |
| `info book.json`    | 查看章节概况                                               |

辅助脚本(`scripts/`):

| 脚本              | 作用                                       |
| --------------- | ---------------------------------------- |
| `convert.py`    | **自然语言统一入口**——一条命令完成提取/OCR/合成/打包(见 `docs/自然语言使用.md`) |
| `ocr_pdf.py`    | 扫描版 PDF 全本 OCR(rapidocr,断点续跑)            |
| `build_book.py` | OCR 文本 → book.json(自动定位章节/封面)            |
| `synth_tts2.py` | TTS2.0 合成器(主引擎,断点续传)                     |
| `synth_seed.py` | seed-audio-1.0 合成器(生成式音频,备用)             |

### 自然语言使用(推荐)

向 LLM/agent 说人话即可,由它翻译成 `scripts/convert.py` 参数。常用示例:

```powershell
# "把这本书转成有声书"(自动识别文字版/扫描版)
python scripts/convert.py 书.pdf --title 不平等的童年 --author "安妮特·拉鲁 著"

# "只合成第三章到第五章,用男声"
python scripts/convert.py 书.pdf --title 不平等的童年 --chapters 3-5 --voice 男声

# "先合成第一章试听"
python scripts/convert.py 书.pdf --title 不平等的童年 --chapters 1 --skip-package

# "接着上次继续合成"(断点续传)
python scripts/convert.py .output.work
```

完整映射表与音色别名见 [docs/自然语言使用.md](docs/自然语言使用.md)。

## 配置

优先级:命令行 > 环境变量 > ini(`--config xxx.ini`) > 默认。

| 环境变量                     | 说明                                               | 默认                                            |
| ------------------------ | ------------------------------------------------ | --------------------------------------------- |
| `OPENSPEECH_API_KEY`     | 语音技术控制台 API Key(TTS2.0/seed-audio 主引擎必需)         | -                                             |
| `OPENSPEECH_BASE_URL`    | openspeech 基础 URL                                | `https://openspeech.bytedance.com/api/v3/tts` |
| `OPENSPEECH_RESOURCE_ID` | 资源 id                                            | `seed-tts-2.0`                                |
| `ARK_API_KEY`            | 方舟 API Key(仅 LLM 情感标注用)                          | -                                             |
| `B2A_TTS_MODEL`          | 方舟豆包 TTS 模型 id(方舟体系)                             | `doubao-tts`                                  |
| `B2A_VOICE`              | 音色 id                                            | `zh_female_cancan_mars_bigtts`                |
| `B2A_PROVIDER`           | 默认引擎: `volc_ark` / `edge` / `seed_audio` / `dry` | `volc_ark`                                    |
| `B2A_MAX_CHARS`          | 单块最大字符数                                          | `1500`                                        |
| `B2A_LLM_MODEL`          | 填模型 id 后启用 LLM 情感标注(如 `doubao-seed-1-6-xxx`)     | 空(不启用)                                        |
| `B2A_PROXY`              | 外部请求代理(edge-tts 等)                               | 空                                             |

ini 示例(完整模板见 `book2audio.ini.example`):

```ini
[book2audio]
; 语音技术(主引擎,必填)——控制台 console.volcengine.com 搜索"语音技术"
openspeech_api_key = xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
openspeech_base_url = https://openspeech.bytedance.com/api/v3/tts
openspeech_resource_id = seed-tts-2.0

; 方舟(仅 LLM 情感标注,可选)
ark_api_key = xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

; 音色(seed-tts-2.0 用 uranus 2.0 系列,如温柔淑女)
voice = zh_female_wenroushunv_uranus_bigtts
```

## 格式支持

| 输入          | 方案                                 | 依赖                   |
| ----------- | ---------------------------------- | -------------------- |
| EPUB        | 直接解析(zip + 章节标题切分)                 | 无                    |
| PDF(文字版)    | PyMuPDF 提取文本;章节标题识别                | 无                    |
| PDF(扫描版)    | `scripts/ocr_pdf.py` 先 OCR         | rapidocr_onnxruntime |
| MOBI / AZW3 | Calibre `ebook-convert` 转 EPUB 后解析 | 需安装 Calibre          |
| TXT         | 直接读取                               | 无                    |

- **扫描版 PDF**:提取时自动检测(文本页占比过低)并提示先 OCR;OCR 用 rapidocr(PaddleOCR 的 onnx 版,CPU 即可)。
- **Calibre**:`winget install calibre.calibre` 或官网下载。

## TTS 引擎与语气控制

- **火山语音技术 TTS2.0(推荐主引擎)**:`seed-tts-2.0` 资源 + `uranus` 2.0 音色(如温柔淑女
  `zh_female_wenroushunv_uranus_bigtts`)。HTTP 单向流式接口,约 4-6 秒合成一块(900 字 ≈ 4 分钟音频),
  全书(29 万字)约 1.5-2 小时。音色统一稳定,语气自然。
- **seed-audio-1.0(生成式音频)**:`--provider seed_audio` 或 `scripts/synth_seed.py`。适合带音效/多角色
  的段落;⚠️ 必须传 `references.speaker` 固定音色,否则每次生成随机音色(男女声交替)。
- **方舟 volc_ark(doubao-tts)**:方舟体系的 TTS,需在方舟控制台开通豆包语音 TTS 模型;
  与 openspeech 是两套独立服务、独立 key。
- **edge-tts(兜底)**:免费、无需 Key,自然度略低,适合先跑通体验。
- **LLM 情感标注**:`chunk --llm-model <方舟对话模型>` 让 LLM 为每章生成 SSML,实现"语气随上下文调整";
  需方舟侧开通对应对话模型(如 `doubao-seed-evolving`,走 Responses API)。

## Navidrome 对接

Navidrome 没有上传 API(设计如此),靠扫描 `MusicFolder`:

1. 把 `output/<书名>/` 整个目录拷入 Navidrome 的 MusicFolder(推荐用 multi-library 单独建"有声书"库);
2. 等定时扫描(默认约 1 分钟)或手动触发扫描;
3. 客户端建议固定用 Symfonium(Android,支持 LRC/多库/下载)或 Finamp(iOS);出发前把书下载到本地。

## 常见问题

- **mobi/azw3 报错**:先装 Calibre。
- **PDF 没有文字**:扫描版,先 OCR(见上)。
- **TTS2.0 返回 55000000 resource mismatch**:音色必须是 uranus 2.0 系列(`*_uranus_bigtts`),
  不能用 1.0 的 `*_mars_bigtts`;且资源 id 需为 `seed-tts-2.0`(非 `seed-tts-2.0-standard`)。
- **TTS2.0 返回 403 not granted**:该资源未开通,去语音技术控制台开通。
- **seed-audio 男女声交替**:请求漏了 `references.speaker`,补上固定音色。
- **火山 TTS 4xx**:模型 id / 音色名需在对应控制台开通并核对。
- **音质**:edge-tts 输出 48kbps;TTS2.0/seed-audio 默认 mp3(采样率 24000 或 48000)。
