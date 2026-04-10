from __future__ import annotations

import hashlib
import re

_PUNCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_text(text: str) -> str:
    text = text.lower().strip()
    text = _PUNCTUATION_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def _build_cache_key(
    key_version: str, model: str, voice: str, sample_rate: int, text: str
) -> str:
    normalized = _normalize_text(text)
    sha = hashlib.sha1(normalized.encode()).hexdigest()
    return f"tts:{key_version}:{model}:{voice}:{sample_rate}:{sha}"