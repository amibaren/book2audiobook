"""配置:环境变量 + ini 文件,优先级 CLI > 环境变量 > ini > 默认。"""
from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"


@dataclass
class Config:
    provider: str = "volc_ark"          # volc_ark | edge | seed_audio | dry
    # 火山方舟(主引擎,豆包语音 TTS 大模型)
    ark_api_key: str = ""
    ark_base_url: str = DEFAULT_ARK_BASE
    tts_model: str = "doubao-tts"       # 在方舟控制台开通后填实际 model id(如 doubao-tts-1-xxx 或 seed-tts-eval-zh-800k)
    voice: str = "zh_female_cancan_mars_bigtts"   # 豆包大模型音色,控制台音色列表可查
    speed: float = 1.0
    # seed-audio 增强(小说对白/特殊情绪段)
    seed_audio_model: str = "doubao-seed-audio-1-0"
    seed_audio_speech_rate: float = 1.0
    # edge-tts 兜底
    edge_voice: str = "zh-CN-XiaoxiaoNeural"
    proxy: str = ""                     # edge-tts 等外部请求代理,如 http://127.0.0.1:1088
    # 分块与 LLM 标注
    max_chars: int = 1500
    llm_model: str = ""                 # 留空则不启用情感标注;填模型 id(如 doubao-seed-1-6-xxx)后启用
    # 输出
    out_dir: str = "output"
    force: bool = False
    verbose: bool = False

    _extra: dict = field(default_factory=dict, repr=False)

    def get(self, key: str, default=None):
        return self._extra.get(key, default)


def load_config(ini_path: str | Path | None = None, overrides: dict | None = None) -> Config:
    """从默认值 + ini + 环境变量 + overrides(CLI) 依次覆盖。"""
    cfg = Config()

    parser = configparser.ConfigParser()
    if ini_path and Path(ini_path).exists():
        parser.read(ini_path, encoding="utf-8")
        if parser.has_section("book2audio"):
            for k, v in parser.items("book2audio"):
                if hasattr(cfg, k):
                    setattr(cfg, k, _coerce(getattr(cfg, k), v))
                else:
                    cfg._extra[k] = v

    env_map = {
        "ARK_API_KEY": "ark_api_key",
        "ARK_BASE_URL": "ark_base_url",
        "B2A_PROVIDER": "provider",
        "B2A_TTS_MODEL": "tts_model",
        "B2A_VOICE": "voice",
        "B2A_SPEED": "speed",
        "B2A_SEED_MODEL": "seed_audio_model",
        "B2A_EDGE_VOICE": "edge_voice",
        "B2A_PROXY": "proxy",
        "B2A_MAX_CHARS": "max_chars",
        "B2A_LLM_MODEL": "llm_model",
        "B2A_OUT": "out_dir",
    }
    for env, attr in env_map.items():
        val = os.environ.get(env)
        if val is not None:
            setattr(cfg, attr, _coerce(getattr(cfg, attr), val))

    if overrides:
        for k, v in overrides.items():
            if v is not None and hasattr(cfg, k):
                setattr(cfg, k, _coerce(getattr(cfg, k), v))
            elif v is not None:
                cfg._extra[k] = v
    return cfg


def _coerce(current, value: str):
    if isinstance(current, bool):
        return str(value).lower() in ("1", "true", "yes", "on")
    if isinstance(current, int):
        return int(value)
    if isinstance(current, float):
        return float(value)
    return value
