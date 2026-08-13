"""核心数据模型。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Chapter:
    """一章书:清洗后的正文 + 按 TTS 长度切好的分块。"""

    index: int                      # 1-based 章节序号(决定最终音轨顺序)
    title: str                      # 章节标题(用于文件名与 ID3)
    text: str                       # 清洗 + TTS 友好化后的正文
    chunks: list[str] = field(default_factory=list)   # 单个 TTS 请求的文本块

    @property
    def char_count(self) -> int:
        return len(self.text)


@dataclass
class BookDoc:
    """整本书提取结果。"""

    title: str
    author: str = ""
    cover: bytes | None = None      # 封面图片字节(JPEG/PNG)
    source_format: str = ""         # epub / pdf / mobi / azw3
    chapters: list[Chapter] = field(default_factory=list)

    @property
    def total_chars(self) -> int:
        return sum(c.char_count for c in self.chapters)
