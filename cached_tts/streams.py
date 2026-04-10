from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from livekit import rtc
from livekit.agents.tts import TTS, ChunkedStream
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions
from livekit.agents.utils import shortuuid

if TYPE_CHECKING:
    from .core import CachedTTS

logger = logging.getLogger(__name__)


class CachedChunkedStream(ChunkedStream):
    """Replays cached audio frames from disk."""

    def __init__(
        self,
        *,
        tts: TTS,
        input_text: str,
        frames: list[rtc.AudioFrame],
        request_id: str,
    ) -> None:
        super().__init__(
            tts=tts,
            input_text=input_text,
            conn_options=DEFAULT_API_CONNECT_OPTIONS,
        )
        self._frames = frames
        self._request_id = request_id

    async def _run(self, output_emitter: Any) -> None:
        output_emitter.initialize(
            request_id=self._request_id,
            sample_rate=self._tts.sample_rate,
            num_channels=self._tts.num_channels,
            mime_type="audio/pcm",
            stream=False,
        )
        for frame in self._frames:
            output_emitter.push(frame.data.tobytes())
        output_emitter.flush()


class _CachedSynthesizeDispatcher(ChunkedStream):
    """Dispatches to cache hit or miss path."""

    def __init__(
        self,
        *,
        tts: CachedTTS,
        input_text: str,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._cached_tts = tts
        self._conn_options = conn_options

    async def _run(self, output_emitter: Any) -> None:
        text = self._input_text
        tts = self._cached_tts

        # Try cache read
        cached = await tts._try_cache_read(text)
        if cached is not None:
            # Replay cached frames
            output_emitter.initialize(
                request_id=shortuuid(),
                sample_rate=tts.sample_rate,
                num_channels=tts.num_channels,
                mime_type="audio/pcm",
                stream=False,
            )
            for frame in cached._frames:
                output_emitter.push(frame.data.tobytes())
            output_emitter.flush()
            await cached.aclose()
            return

        # Cache miss — try primary, tee for write-back
        tts._total += 1
        tts._misses += 1
        key = tts._cache_key(text)
        short_key = key[:40]
        logger.info("cache_hit=false key=%s text=%r", short_key, text)

        last_exc: Exception | None = None

        # Try primary
        try:
            inner = self._cached_tts._primary.synthesize(text, conn_options=self._conn_options)
            output_emitter.initialize(
                request_id=shortuuid(),
                sample_rate=tts.sample_rate,
                num_channels=tts.num_channels,
                mime_type="audio/pcm",
                stream=False,
            )
            chunks: list[bytes] = []
            samples_per_chunk = 0
            async for ev in inner:
                raw = ev.frame.data.tobytes()
                chunks.append(raw)
                samples_per_chunk = ev.frame.samples_per_channel
                output_emitter.push(raw)
            output_emitter.flush()

            # Primary succeeded — async cache write
            if chunks:
                asyncio.create_task(tts._write_cache(key, chunks, samples_per_chunk))
            return
        except Exception as e:
            last_exc = e

        # Try fallbacks (no caching)
        for fb in self._cached_tts._fallbacks:
            try:
                inner = fb.synthesize(text, conn_options=self._conn_options)
                output_emitter.initialize(
                    request_id=shortuuid(),
                    sample_rate=tts.sample_rate,
                    num_channels=tts.num_channels,
                    mime_type="audio/pcm",
                    stream=False,
                )
                async for ev in inner:
                    output_emitter.push(ev.frame.data.tobytes())
                output_emitter.flush()
                return
            except Exception as e:
                last_exc = e
                continue

        if last_exc is not None:
            raise last_exc


class _PassthroughChunkedStream(ChunkedStream):
    """Simple primary + fallback without caching."""

    def __init__(
        self,
        *,
        tts: TTS,
        primary: TTS,
        fallbacks: list[TTS],
        input_text: str,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._primary = primary
        self._fallbacks = fallbacks

    async def _run(self, output_emitter: Any) -> None:
        last_exc: Exception | None = None

        for provider in [self._primary, *self._fallbacks]:
            try:
                inner = provider.synthesize(self._input_text, conn_options=self._conn_options)
                output_emitter.initialize(
                    request_id=shortuuid(),
                    sample_rate=self._tts.sample_rate,
                    num_channels=self._tts.num_channels,
                    mime_type="audio/pcm",
                    stream=False,
                )
                async for ev in inner:
                    output_emitter.push(ev.frame.data.tobytes())
                output_emitter.flush()
                return
            except Exception as e:
                last_exc = e
                continue

        if last_exc is not None:
            raise last_exc