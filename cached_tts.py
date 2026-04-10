from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from collections.abc import AsyncIterator
from typing import Any

import msgpack
import redis.asyncio as aioredis
from livekit import rtc
from livekit.agents.tts import TTS, ChunkedStream, SynthesizedAudio, TTSCapabilities
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions
from livekit.agents.utils import aio, shortuuid

from cache_store import CacheStore

logger = logging.getLogger(__name__)

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


def _serialize_audio(
    chunks: list[bytes],
    sample_rate: int,
    num_channels: int,
    samples_per_channel: int,
    provider: str,
) -> bytes:
    return msgpack.packb({
        "sample_rate": sample_rate,
        "num_channels": num_channels,
        "samples_per_channel": samples_per_channel,
        "chunks": chunks,
        "provider": provider,
        "created_at": time.time(),
    })


def _deserialize_audio(data: bytes) -> dict[str, Any]:
    return msgpack.unpackb(data, raw=False)


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


class _TeeChunkedStream(ChunkedStream):
    """Wraps an inner ChunkedStream, teeing frames to a buffer for cache write-back."""

    def __init__(
        self,
        *,
        tts: CachedTTS,
        inner: ChunkedStream,
        cache_key: str,
        input_text: str,
    ) -> None:
        super().__init__(
            tts=tts,
            input_text=input_text,
            conn_options=DEFAULT_API_CONNECT_OPTIONS,
        )
        self._inner = inner
        self._cache_key = cache_key
        self._cached_tts = tts

    async def _run(self, output_emitter: Any) -> None:
        output_emitter.initialize(
            request_id=shortuuid(),
            sample_rate=self._tts.sample_rate,
            num_channels=self._tts.num_channels,
            mime_type="audio/pcm",
            stream=False,
        )
        chunks: list[bytes] = []
        samples_per_chunk = 0
        clean = False

        try:
            async for ev in self._inner:
                raw = ev.frame.data.tobytes()
                chunks.append(raw)
                samples_per_chunk = ev.frame.samples_per_channel
                output_emitter.push(raw)
                if ev.is_final:
                    clean = True
            if chunks and not clean:
                clean = True
        except Exception:
            clean = False
            raise
        finally:
            output_emitter.flush()
            if clean and chunks:
                asyncio.create_task(
                    self._cached_tts._write_cache(
                        self._cache_key,
                        chunks,
                        samples_per_chunk,
                    )
                )


class CachedTTS(TTS):
    def __init__(
        self,
        *,
        primary: TTS,
        fallbacks: list[TTS],
        store: CacheStore,
        model_string: str,
    ) -> None:
        # Parse model string to extract voice
        voice: str = ""
        if ":" in model_string:
            idx = model_string.rfind(":")
            voice = model_string[idx + 1 :]
            model_part = model_string[:idx]
        else:
            model_part = model_string

        super().__init__(
            capabilities=TTSCapabilities(streaming=False),
            sample_rate=primary.sample_rate,
            num_channels=primary.num_channels,
        )
        self._primary = primary
        self._fallbacks = fallbacks
        self._store = store
        self._model_part = model_part  # e.g. "cartesia/sonic-3"
        self._voice = voice  # e.g. "f31cc6a7-..."

        self._enabled = os.getenv("CACHED_TTS_ENABLED", "true").lower() == "true"
        self._key_version = os.getenv("CACHED_TTS_KEY_VERSION", "v1")
        self._ttl_days = int(os.getenv("CACHED_TTS_TTL_DAYS", "30"))
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/1")

        self._redis: aioredis.Redis | None = None
        self._redis_url = redis_url

        # Counters
        self._total = 0
        self._hits = 0
        self._misses = 0

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    def _cache_key(self, text: str) -> str:
        return _build_cache_key(
            self._key_version,
            self._model_part,
            self._voice,
            self._primary.sample_rate,
            text,
        )

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> ChunkedStream:
        if not self._enabled:
            return self._synthesize_passthrough(text, conn_options=conn_options)

        return _CachedSynthesizeDispatcher(
            tts=self,
            input_text=text,
            conn_options=conn_options,
        )

    def _synthesize_passthrough(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> ChunkedStream:
        """Try primary, then fallbacks. No caching."""
        return _PassthroughChunkedStream(
            tts=self,
            primary=self._primary,
            fallbacks=self._fallbacks,
            input_text=text,
            conn_options=conn_options,
        )

    def stream(self, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS):
        # Defensive: delegate to primary, no caching
        return self._primary.stream(conn_options=conn_options)

    async def _try_cache_read(self, text: str) -> ChunkedStream | None:
        """Attempt Redis lookup + file read. Returns CachedChunkedStream on hit, None on miss."""
        key = self._cache_key(text)
        short_key = key[:40]

        try:
            r = await self._get_redis()
            file_path = await r.get(key)
        except Exception:
            logger.warning("Redis GET failed for key=%s", short_key, exc_info=True)
            return None

        if file_path is None:
            return None

        # Redis hit — try reading the file
        try:
            data = await self._store.get(key)
        except Exception:
            logger.warning("File read failed for key=%s", short_key, exc_info=True)
            return None

        if data is None:
            # Orphaned Redis key — file missing on disk
            logger.info("Stale Redis key (file missing), deleting key=%s", short_key)
            try:
                r = await self._get_redis()
                await r.delete(key)
            except Exception:
                logger.warning("Failed to delete stale Redis key=%s", short_key, exc_info=True)
            return None

        # Deserialize and build frames
        try:
            meta = _deserialize_audio(data)
        except Exception:
            logger.warning("Deserialization failed for key=%s", short_key, exc_info=True)
            return None

        frames: list[rtc.AudioFrame] = []
        for chunk in meta["chunks"]:
            frame = rtc.AudioFrame(
                data=chunk,
                sample_rate=meta["sample_rate"],
                num_channels=meta["num_channels"],
                samples_per_channel=meta["samples_per_channel"],
            )
            frames.append(frame)

        self._total += 1
        self._hits += 1
        logger.info("cache_hit=true key=%s text=%r", short_key, text)

        return CachedChunkedStream(
            tts=self,
            input_text=text,
            frames=frames,
            request_id=shortuuid(),
        )

    async def _write_cache(
        self,
        key: str,
        chunks: list[bytes],
        samples_per_channel: int,
    ) -> None:
        short_key = key[:40]
        try:
            data = _serialize_audio(
                chunks=chunks,
                sample_rate=self._primary.sample_rate,
                num_channels=self._primary.num_channels,
                samples_per_channel=samples_per_channel,
                provider=self._model_part,
            )
            await self._store.put(key, data, self._ttl_days)

            r = await self._get_redis()
            file_path = self._store.path_for_key(key)
            await r.set(key, file_path, ex=self._ttl_days * 86400)
            logger.debug("Cache write-back success key=%s path=%s", short_key, file_path)
        except Exception:
            logger.warning("Cache write-back failed key=%s", short_key, exc_info=True)

    async def cleanup_orphans(self) -> None:
        """Scan storage directory for files not referenced in Redis. Delete orphans."""
        import glob as globmod

        storage_dir = os.getenv("CACHED_TTS_STORAGE_DIR", ".tts_cache")
        pattern = os.path.join(storage_dir, "**", "*.msgpack")
        files = globmod.glob(pattern, recursive=True)

        if not files:
            return

        try:
            r = await self._get_redis()
        except Exception:
            logger.warning("Cannot connect to Redis for orphan cleanup", exc_info=True)
            return

        deleted = 0
        for fpath in files:
            # Reconstruct key from file path to check Redis
            # Instead, scan Redis for the value (file path). For efficiency,
            # we check if any key maps to this path using a reverse lookup.
            # But that's expensive. Instead, just check if the file's mtime is
            # older than TTL — simpler heuristic.
            # Better approach: derive the key from the path structure
            try:
                rel = os.path.relpath(fpath, storage_dir)
                # rel = v1/cartesia/sonic-3/voice/48000/hash.msgpack
                parts = rel.replace(os.sep, "/").split("/")
                # Reconstruct: tts:{ver}:{model_provider}/{model_name}:{voice}:{sr}:{hash}
                ver = parts[0]
                model = f"{parts[1]}/{parts[2]}"
                voice = parts[3]
                sr = parts[4]
                sha = parts[5].replace(".msgpack", "")
                key = f"tts:{ver}:{model}:{voice}:{sr}:{sha}"

                exists = await r.exists(key)
                if not exists:
                    os.remove(fpath)
                    deleted += 1
            except Exception:
                continue

        if deleted:
            logger.info("Orphan cleanup: deleted %d files", deleted)

    def get_summary(self) -> str:
        hit_rate = (self._hits / self._total * 100) if self._total > 0 else 0.0
        return (
            f"CachedTTS: total={self._total} hits={self._hits} "
            f"misses={self._misses} hit_rate={hit_rate:.1f}%"
        )

    async def aclose(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
        await self._primary.aclose()
        for fb in self._fallbacks:
            await fb.aclose()


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