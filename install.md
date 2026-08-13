# book2audiobook 安装说明

## 方式一:自然语言安装(推荐,给 AI 助手用)

直接把下面这句话发给你的 AI 助手(Reasonix / Claude Code / Cursor 等支持 skill 安装的 Agent):

> 参考 https://<你的托管地址>/book2audiobook/SKILL.md,安装该 skill

AI 助手会:下载 SKILL.md 与配套文件 → 安装到 skills 目录 → 运行 install.ps1 完成环境配置。

> 托管地址可以是:
> - GitHub 仓库 raw 链接(推荐,如 `https://raw.githubusercontent.com/<user>/<repo>/main/book2audiobook/SKILL.md`)
> - 内网 HTTP 服务器(如 `http://192.168.x.x/book2audiobook/SKILL.md`)
> - 本机路径(`file:///D:/AI Projects/book2audio/release/book2audiobook/SKILL.md`)

## 方式二:手动安装

### 1. 复制 skill 到 skills 目录

```powershell
# 找到你的 AI 助手的 skills 目录(以 Reasonix 为例)
$skills = "$env:APPDATA\reasonix\skills"
Copy-Item -Recurse release\book2audiobook "$skills\"
# → $skills\book2audiobook\SKILL.md
```

### 2. 安装 Python 依赖

```powershell
cd <skill目录>\book2audiobook
python -m venv .venv
.venv\Scripts\python -m pip install -e ".\src\[edge]" -i https://pypi.tuna.tsinghua.edu.cn/simple
.venv\Scripts\python -m pip install rapidocr_onnxruntime -i https://pypi.tuna.tsinghua.edu.cn/simple
```

或直接运行 `install.ps1`(自动完成 检测→venv→依赖→ini→自检):

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

### 3. 配置密钥

1. 打开火山语音技术控制台(console.volcengine.com 搜索"语音技术"),开通 **豆包语音合成大模型2.0**(seed-tts-2.0);
2. 创建 API Key;
3. 复制 `book2audio.ini.example` 为 `book2audio.ini`,填入 `openspeech_api_key`。

验证:`python scripts/convert.py --init` 应输出"配置就绪"。

## 使用

```powershell
# 整本书转有声书(自动识别文字版/扫描版,自动 OCR)
python scripts/convert.py 书.pdf --title 书名 --author 作者

# 特定章节 + 换音色
python scripts/convert.py 书.pdf --title 书名 --chapters 3-5 --voice 男声

# 第一章试听(自动拼接+播放)
python scripts/convert.py 书.pdf --title 书名 --chapters 1 --skip-package --preview 4

# 断点续传
python scripts/convert.py .output.work
```

详细用法见 `docs/自然语言使用.md`,引擎/接入细节见 `docs/火山语音TTS接入.md`。

## 目录结构

```
book2audiobook/
├── SKILL.md              # skill 定义(给 AI 助手的说明)
├── install.ps1           # Windows 一键安装
├── book2audio.ini.example# 配置模板
├── README.md             # 使用说明
├── docs/                 # 自然语言使用 + 火山 TTS 接入实战
├── scripts/              # convert.py 统一入口 + ocr/build/synth/verify 工具
└── src/                  # book2audio CLI 源码(pip install -e)
```
