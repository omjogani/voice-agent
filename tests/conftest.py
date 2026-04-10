from __future__ import annotations

import os
import sys
from typing import Any
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest

# Ensure project root is on sys.path so `cache_store` / `cached_tts` resolve
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from livekit import rtc
from livekit.agents.tts import TTS, ChunkedStream, SynthesizedAudio, TTSCapabilities
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions
from livekit.agents.utils import shortuuid

from cache_store import LocalCacheStore
from cached_tts import CachedTTS


# ---------------------------------------------------------------------------
# Mock TTS helpers
# ---------------------------------------------------------------------------

SAMPLE_RATE = 24000
NUM_CHANNELS = 1
SAMPLES_PER_CHANNEL = 4800  # 200ms at 24kHz


def _make_pcm_bytes(value: int = 0, size: int = SAMPLES_PER_CHANNEL * 2) -> bytes:
    """Create dummy PCM bytes (16-bit mono)."""
    return bytes([value % 256]) * size


class _MockChunkedStream(ChunkedStream):
    """ChunkedStream that yields pre-built frames."""

    def __init__(self, *, tts: TTS, input_text: str, chunks: list[bytes]) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=DEFAULT_API_CONNECT_OPTIONS)
        self._chunks = chunks

    async def _run(self, output_emitter: Any) -> None:
        output_emitter.initialize(
            request_id=shortuuid(),
            sample_rate=self._tts.sample_rate,
            num_channels=self._tts.num_channels,
            mime_type="audio/pcm",
            stream=False,
        )
        for chunk in self._chunks:
            output_emitter.push(chunk)
        output_emitter.flush()


class MockTTS(TTS):
    """TTS that returns deterministic audio. Tracks call count."""

    def __init__(self, chunks: list[bytes] | None = None) -> None:
        super().__init__(
            capabilities=TTSCapabilities(streaming=False),
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
        )
        self._chunks = chunks or [_make_pcm_bytes(1), _make_pcm_bytes(2), _make_pcm_bytes(3)]
        self.synthesize_count = 0

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> ChunkedStream:
        self.synthesize_count += 1
        return _MockChunkedStream(tts=self, input_text=text, chunks=self._chunks)

    async def aclose(self) -> None:
        pass


class FailingTTS(TTS):
    """TTS that always raises."""

    def __init__(self, error: Exception | None = None) -> None:
        super().__init__(
            capabilities=TTSCapabilities(streaming=False),
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
        )
        self._error = error or RuntimeError("TTS provider failed")

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> ChunkedStream:
        raise self._error

    async def aclose(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_redis():
    """In-memory fake Redis instance."""
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def tmp_store(tmp_path):
    """LocalCacheStore backed by a temp directory."""
    return LocalCacheStore(str(tmp_path))


@pytest.fixture
def make_cached_tts(tmp_path, fake_redis, monkeypatch):
    """Factory that creates a CachedTTS with mocked Redis and temp store."""

    def _factory(
        *,
        primary: TTS | None = None,
        fallbacks: list[TTS] | None = None,
        enabled: bool = True,
        model_string: str = "cartesia/sonic-3:voice123",
    ) -> CachedTTS:
        monkeypatch.setenv("CACHED_TTS_ENABLED", str(enabled).lower())
        monkeypatch.setenv("CACHED_TTS_KEY_VERSION", "v1")
        monkeypatch.setenv("CACHED_TTS_TTL_DAYS", "30")
        monkeypatch.setenv("CACHED_TTS_STORAGE_DIR", str(tmp_path))
        monkeypatch.setenv("REDIS_URL", "redis://fake")

        tts = CachedTTS(
            primary=primary or MockTTS(),
            fallbacks=fallbacks if fallbacks is not None else [],
            store=LocalCacheStore(str(tmp_path)),
            model_string=model_string,
        )
        # Inject fake redis so it doesn't try to connect to a real one
        tts._redis = fake_redis
        return tts

    return _factory