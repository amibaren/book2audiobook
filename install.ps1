# book2audio 一键安装脚本(Windows)
# 用法: 右键"使用 PowerShell 运行",或:
#   powershell -ExecutionPolicy Bypass -File install.ps1
# 功能: 检测 Python → 建 venv → 装依赖 → 生成 ini 模板 → 自检

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ROOT

Write-Host "`n=== book2audio 一键安装 ===" -ForegroundColor Cyan

# ---------- 1. 检测 Python ----------
$py = $null
foreach ($cand in @("python", "py -3", "python3")) {
    try {
        $v = & $cand --version 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) {
            $py = $cand
            Write-Host "  [OK] Python: $v" -ForegroundColor Green
            break
        }
    } catch {}
}
if (-not $py) {
    Write-Host "  [ERR] 未找到 Python。请先安装 Python 3.10+:" -ForegroundColor Red
    Write-Host "        https://www.python.org/downloads/ (勾选 Add to PATH)"
    exit 1
}

# ---------- 2. 创建 venv ----------
$venv = Join-Path $ROOT ".venv"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    Write-Host "  创建虚拟环境 .venv ..."
    & $py -m venv $venv
    if ($LASTEXITCODE -ne 0) { Write-Host "  [ERR] venv 创建失败" -ForegroundColor Red; exit 1 }
} else {
    Write-Host "  [OK] 虚拟环境已存在" -ForegroundColor Green
}
$vp = Join-Path $venv "Scripts\python.exe"
$vppip = Join-Path $venv "Scripts\python.exe"

# ---------- 3. 安装依赖 ----------
Write-Host "  安装依赖(可加 -i https://pypi.tuna.tsinghua.edu.cn/simple 加速)..."
& $vppip -m pip install --upgrade pip -q
& $vppip -m pip install -e ".[edge]" -q
& $vppip -m pip install rapidocr_onnxruntime -q
if ($LASTEXITCODE -ne 0) { Write-Host "  [ERR] 依赖安装失败,请检查网络" -ForegroundColor Red; exit 1 }
Write-Host "  [OK] 依赖安装完成" -ForegroundColor Green

# ---------- 4. 生成 ini 模板 ----------
$ini = Join-Path $ROOT "book2audio.ini"
if (-not (Test-Path $ini)) {
    Copy-Item "book2audio.ini.example" $ini
    Write-Host "  已生成 book2audio.ini(请填入 openspeech_api_key)" -ForegroundColor Yellow
} else {
    Write-Host "  [OK] book2audio.ini 已存在" -ForegroundColor Green
}

# ---------- 5. 自检 ----------
Write-Host "`n=== 自检 ==="
& $vp scripts\convert.py --init
if ($LASTEXITCODE -ne 0) {
    Write-Host "`n配置未完成: 请编辑 book2audio.ini 填入 openspeech_api_key(语音技术控制台创建)" -ForegroundColor Yellow
} else {
    Write-Host "`n✅ 安装完成!用法:" -ForegroundColor Green
}
Write-Host @"

  把电子书转成有声书(自动识别文字版/扫描版):
    python scripts\convert.py 书.pdf

  只合成特定章节:
    python scripts\convert.py 书.pdf --chapters 3-5

  首次配置检查:
    python scripts\convert.py --init

  详细说明见 docs\自然语言使用.md 和 README.md
"@
