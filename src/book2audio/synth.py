"""TTS 合成引擎:豆包语音 TTS 大模型(主) / seed-audio(增强) / edge-tts(兜底) / dry(联调)。"""
from __future__ import annotations

import base64
import time
from pathlib import Path

import requests

from .config import Config

MP3_HEADER = b"\xff\xfb\x90\x00"  # MPEG1 Layer III, 128kbps, 44.1kHz


def _write_dry_mp3(path: Path, seconds: float = 1.0) -> None:
    """写一个最小合法静音 MP3(约 26ms/帧),用于无 key/无网时联调链路。"""
    frame = MP3_HEADER + b"\x00" * (417 - 4)   # MPEG1 L3 128kbps 44.1kHz 无 padding: 帧长 417
    n = max(1, int(seconds / 0.026))
    path.write_bytes(frame * n)


class Provider:
    name = "base"

    def synthesize(self, text: str, out: Path, **kw) -> Path:
        raise NotImplementedError


class VolcArkProvider(Provider):
    """火山方舟 豆包语音 TTS 大模型(主引擎)。"""

    name = "volc_ark"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def synthesize(self, text: str, out: Path, **kw) -> Path:
        if not self.cfg.ark_api_key:
            raise RuntimeError(
                "未配置火山方舟 API Key。设置环境变量 ARK_API_KEY=xxx "
                "(控制台 https://console.volcengine.com/ark 创建),或在 ini 配置 [book2audio] ark_api_key。"
            )
        url = self.cfg.ark_base_url.rstrip("/") + "/tts"
        body: dict = {
            "model": self.cfg.tts_model,
            "text": text,
            "voice": kw.get("voice") or self.cfg.voice,
            "response_format": "mp3",
            "speed": float(kw.get("speed") or self.cfg.speed),
        }
        if text.lstrip().startswith("<speak"):
            body["ssml"] = True  # 若服务端不识别,会 4xx,此时去掉该字段重试
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {self.cfg.ark_api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=300,
        )
        if resp.status_code == 400 and "ssml" in body:
            body.pop("ssml")
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {self.cfg.ark_api_key}", "Content-Type": "application/json"},
                json=body,
                timeout=300,
            )
        if resp.status_code != 200:
            raise RuntimeError(f"火山 TTS 失败 HTTP {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        audio = _extract_b64_audio(data)
        if not audio:
            raise RuntimeError(f"火山 TTS 响应中未找到音频数据: {str(data)[:500]}")
        out.write_bytes(base64.b64decode(audio))
        return out


def _extract_b64_audio(data: dict) -> str | None:
    """容错解析几种常见的 TTS 返回结构。"""
    d = data.get("data")
    if isinstance(d, list) and d and isinstance(d[0], dict):
        for k in ("audio", "base64", "data"):
            if d[0].get(k):
                return d[0][k]
    if isinstance(d, dict):
        for k in ("audio", "base64", "data"):
            if d.get(k):
                return d[k]
    for k in ("audio", "base64", "data"):
        if isinstance(data.get(k), str):
            return data[k]
    return None


class EdgeProvider(Provider):
    """edge-tts 兜底(免费,需联网)。"""

    name = "edge"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def synthesize(self, text: str, out: Path, **kw) -> Path:
        try:
            import edge_tts
        except ImportError:
            raise RuntimeError("未安装 edge-tts: pip install edge-tts(或安装 book2audio[edge])") from None
        voice = kw.get("voice") or self.cfg.edge_voice
        proxy = kw.get("proxy") or self.cfg.proxy or None
        # 切到 3000 字以内(edge-tts 单次限制)
        t = text
        if len(t) > 3000:
            t = t[:3000]
        comm = edge_tts.Communicate(t, voice, proxy=proxy)
        import asyncio

        asyncio.run(comm.save(str(out)))
        return out


class SeedAudioProvider(Provider):
    """doubao-seed-audio-1.0 增强(异步任务,单次 ≤120s)。

    ⚠️ 火山方舟该接口的端点/参数随控制台版本可能调整;若调用失败,
       请以 https://docs.volcengine.com/docs/6561/2550782 为准微调 _build_body。
    """

    name = "seed_audio"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def synthesize(self, text: str, out: Path, **kw) -> Path:
        if not self.cfg.ark_api_key:
            raise RuntimeError("未配置火山方舟 API Key(ARK_API_KEY)")
        base = self.cfg.ark_base_url.rstrip("/")
        # prompt 支持自然语言语气描述,如 "用温和的语气朗读:..."
        prompt = kw.get("prompt") or f"自然朗读以下内容:{text}"
        body = self._build_body(prompt, voice=kw.get("voice") or self.cfg.voice)
        headers = {"Authorization": f"Bearer {self.cfg.ark_api_key}", "Content-Type": "application/json"}
        resp = requests.post(f"{base}/contents/generations/tasks", headers=headers, json=body, timeout=120)
        if resp.status_code != 200:
            # 扁平参数回退(部分网关实现)
            flat = {
                "model": self.cfg.seed_audio_model,
                "prompt": prompt,
                "audio_references": [self.cfg.voice],
                "speech_rate": self.cfg.seed_audio_speech_rate,
                "format": "mp3",
            }
            resp = requests.post(f"{base}/contents/generations/tasks", headers=headers, json=flat, timeout=120)
        if resp.status_code != 200:
            raise RuntimeError(f"seed-audio 任务提交失败 HTTP {resp.status_code}: {resp.text[:500]}")
        task_id = resp.json().get("id")
        if not task_id:
            raise RuntimeError(f"seed-audio 未返回任务 id: {resp.text[:300]}")
        url = self._poll(f"{base}/contents/generations/tasks/{task_id}", headers)
        audio = requests.get(url, timeout=300).content
        out.write_bytes(audio)
        return out

    def _build_body(self, prompt: str, voice: str) -> dict:
        # 方舟原生形态:content 消息数组 + generation_params
        return {
            "model": self.cfg.seed_audio_model,
            "content": [{"type": "text", "text": prompt}],
            "generation_params": {
                "audio_references": [voice],
                "speech_rate": self.cfg.seed_audio_speech_rate,
                "format": "mp3",
            },
        }

    def _poll(self, url: str, headers: dict, timeout_s: float = 600) -> str:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            r = requests.get(url, headers=headers, timeout=60)
            if r.status_code != 200:
                raise RuntimeError(f"seed-audio 任务查询失败 HTTP {r.status_code}: {r.text[:300]}")
            j = r.json()
            status = str(j.get("status", j.get("state", ""))).upper()
            if status in ("SUCCEEDED", "SUCCESS", "COMPLETED"):
                audio_url = j.get("audio_url") or j.get("url")
                if not audio_url:
                    # content 数组形态
                    for item in j.get("content", []) or []:
                        if isinstance(item, dict) and item.get("audio_url"):
                            audio_url = item["audio_url"]
                            break
                if not audio_url:
                    raise RuntimeError(f"seed-audio 成功但未找到音频地址: {str(j)[:300]}")
                return audio_url
            if status in ("FAILED", "ERROR", "CANCELLED"):
                raise RuntimeError(f"seed-audio 任务失败: {str(j)[:300]}")
            time.sleep(5)
        raise TimeoutError("seed-audio 任务超时")


class DryProvider(Provider):
    """联调用:写最小静音 MP3,验证链路而不真正合成。"""

    name = "dry"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def synthesize(self, text: str, out: Path, **kw) -> Path:
        _write_dry_mp3(out, seconds=1.0)
        return out


def get_provider(name: str, cfg: Config) -> Provider:
    providers = {
        "volc_ark": VolcArkProvider,
        "edge": EdgeProvider,
        "seed_audio": SeedAudioProvider,
        "dry": DryProvider,
    }
    if name not in providers:
        raise ValueError(f"未知 provider {name},可选: {sorted(providers)}")
    return providers[name](cfg)
