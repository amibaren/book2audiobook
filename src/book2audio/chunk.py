"""TTS 友好化 + 分块 + 可选 LLM 情感标注(语气随上下文调整的核心)。"""
from __future__ import annotations

import re

import requests

from .models import BookDoc, Chapter

# 句子结束标点(用于切分长段)
_SENT_END = re.compile(r"(?<=[。！？!?；;…])")
_SPLIT_CJK = re.compile(r"(?<=[\u4e00-\u9fff])")


def tts_friendly(text: str) -> str:
    """规则级 TTS 友好化:不改变文字内容,只做朗读友好处理。

    现代大模型 TTS(豆包/edge-tts)已能自然朗读数字、英文、标点,因此这里
    只做保守处理,避免误伤。多音字/专名读音交给 TTS 或 LLM 标注阶段。
    """
    t = text
    t = t.replace("\u3000", " ")            # 全角空格
    t = re.sub(r"[ \t]+", " ", t)           # 合并空白
    t = re.sub(r"\n{3,}", "\n\n", t)        # 压缩空行
    # 中文与英文/数字之间补一个空格,朗读更自然
    t = re.sub(r"(?<=[\u4e00-\u9fff])(?=[A-Za-z0-9])", " ", t)
    t = re.sub(r"(?<=[A-Za-z0-9])(?=[\u4e00-\u9fff])", " ", t)
    return t.strip()


def split_chunks(text: str, max_chars: int = 1500) -> list[str]:
    """按段落累积切块,尽量在句末断开;单段超长时按句子切。"""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    paras = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for para in paras:
        if size + len(para) + 1 <= max_chars:
            buf.append(para)
            size += len(para) + 1
            continue
        # 当前段落放不下:先结算缓冲区
        if buf:
            chunks.append("\n".join(buf))
            buf, size = [], 0
        # 段落本身超长:按句切
        if len(para) > max_chars:
            for piece in _split_long(para, max_chars):
                chunks.append(piece)
        else:
            buf.append(para)
            size = len(para) + 1
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def _split_long(para: str, max_chars: int) -> list[str]:
    """按句末标点把长段切成若干不超过 max_chars 的片段。"""
    sentences = [s for s in _SENT_END.split(para) if s.strip()]
    pieces: list[str] = []
    buf, size = "", 0
    for s in sentences:
        if size + len(s) <= max_chars:
            buf += s
            size += len(s)
        else:
            if buf:
                pieces.append(buf)
            # 单句仍超长:硬切(仅中文字符边界,避免切断多字节)
            buf, size = "", 0
            while len(s) > max_chars:
                cut = max_chars
                while cut > 0 and not _SPLIT_CJK.match(s[cut - 1 : cut]):
                    cut -= 1
                pieces.append(s[:cut])
                s = s[cut:]
            buf = s
            size = len(s)
    if buf:
        pieces.append(buf)
    return pieces


# ---------------------------------------------------------------- 可选 LLM 情感标注

_ANNOTATE_PROMPT = """你是有声书朗读导演。请把下面的书摘文本改写成火山语音合成大模型可用的 SSML,
在保留全部文字内容不变的前提下:
- 用 <break time="300ms"/> 控制句间停顿(省略号/破折号处可加长)
- 用 <prosody rate="0.95" pitch="+2%">…</prosody> 控制局部语速与音高
- 用 <mstts:express-as style="gentle|calm|angry|excited|sad|neutral">…</mstts:express-as>
  给整段标注一种情感基调;若文本含明显对话或情绪起伏,允许对不同句子用不同 style
- 不要修改、增删任何文字;只输出一个 <speak>...</speak>,不要解释

文本:
{text}
"""


def annotate_with_llm(chapter: Chapter, *, endpoint: str, api_key: str, model: str, proxy: str = "") -> str:
    """让 LLM 为整章生成 SSML 情感标注;任何失败都降级为原文本。"""
    try:
        proxies = {"http": proxy, "https": proxy} if proxy else None
        resp = requests.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": _ANNOTATE_PROMPT.format(text=chapter.text[:3000])}],
                "temperature": 0.3,
            },
            timeout=120,
            proxies=proxies,
        )
        resp.raise_for_status()
        out = resp.json()["choices"][0]["message"]["content"]
        out = out.strip()
        if not (out.startswith("<speak") and out.endswith("</speak>")):
            # 有时模型会包 markdown 代码块
            m = re.search(r"<speak.*?</speak>", out, flags=re.DOTALL)
            if not m:
                raise ValueError("LLM 未返回合法 SSML")
            out = m.group(0)
        return out
    except Exception as e:
        print(f"[warn] LLM 情感标注失败,使用原文本: {e}")
        return chapter.text


def chunk_book(book: BookDoc, max_chars: int = 1500, *, llm: dict | None = None) -> None:
    """对每章做 TTS 友好化 + 分块;llm 形如 {endpoint, api_key, model, proxy} 时做情感标注。"""
    for ch in book.chapters:
        ch.text = tts_friendly(ch.text)
        if llm:
            ch.text = annotate_with_llm(ch, **llm)
        ch.chunks = split_chunks(ch.text, max_chars=max_chars)
