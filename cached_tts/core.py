from __future__ import annotations

import logging
import os
import redis.asyncio as aioredis
from livekit import rtc
from livekit.agents.tts import TTS, ChunkedStream, TTSCapabilities
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions
from livekit.agents.utils import shortuuid

from cache_store import CacheStore

from .keys import _build_cache_key
from .serialization import _deserialize_audio, _serialize_audio
from .streams import CachedChunkedStream, _CachedSynthesizeDispatcher, _PassthroughChunkedStream

logger = logging.getLogger(__name__)


def _parse_model_string(model_string: str) -> tuple[str, str]:
    """Split 'cartesia/sonic-3:voice-id' into (model, voice)."""
    if ":" in model_string:
        idx = model_string.rfind(":")
        return model_string[:idx], model_string[idx + 1:]
    return model_string, ""


def _key_from_filepath(filepath: str, storage_dir: str) -> str:
    """Reconstruct a cache key from a stored file's relative path.

    Path layout: {storage_dir}/{ver}/{provider}/{model}/{voice}/{sr}/{hash}.msgpack
    Key layout:  tts:{ver}:{provider}/{model}:{voice}:{sr}:{hash}
    """
    rel = os.path.relpath(filepath, storage_dir)
    parts = rel.replace(os.sep, "/").split("/")
    ver, provider, model, voice, sr = parts[0], parts[1], parts[2], parts[3], parts[4]
    sha = parts[5].replace(".msgpack", "")
    return f"tts:{ver}:{provider}/{model}:{voice}:{sr}:{sha}"


class CachedTTS(TTS):
    def __init__(
            self,
            *,
            primary: TTS,
            fallbacks: list[TTS],
            store: CacheStore,
            model_string: str,
    ) -> None:
        model_part, voice = _parse_model_string(model_string)

        super().__init__(
            capabilities=TTSCapabilities(streaming=False),
            sample_rate=primary.sample_rate,
            num_channels=primary.num_channels,
        )
        self._primary = primary
        self._fallbacks = fallbacks
        self._store = store
        self._model_part = model_part
        self._voice = voice

        self._enabled = os.getenv("CACHED_TTS_ENABLED", "true").lower() == "true"
        self._key_version = os.getenv("CACHED_TTS_KEY_VERSION", "v1")
        self._ttl_days = int(os.getenv("CACHED_TTS_TTL_DAYS", "30"))
        self._redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/1")
        self._redis: aioredis.Redis | None = None

        self._total = 0
        self._hits = 0
        self._misses = 0

    def synthesize(
            self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> ChunkedStream:
        if not self._enabled:
            return self._synthesize_passthrough(text, conn_options=conn_options)

        return _CachedSynthesizeDispatcher(
            tts=self, input_text=text, conn_options=conn_options,
        )

    def stream(self, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS):
        return self._primary.stream(conn_options=conn_options)

    def _cache_key(self, text: str) -> str:
        return _build_cache_key(
            self._key_version, self._model_part, self._voice,
            self._primary.sample_rate, text,
        )

    async def _try_cache_read(self, text: str) -> CachedChunkedStream | None:
        """Look up text in Redis + filesystem. Returns a stream on hit, None on miss."""
        key = self._cache_key(text)

        file_path = await self._redis_get(key)
        if file_path is None:
            return None

        data = await self._store_read(key)
        if data is None:
            await self._delete_stale_key(key)
            return None

        frames = self._deserialize_frames(key, data)
        if frames is None:
            return None

        self._total += 1
        self._hits += 1
        logger.info("cache_hit=true key=%s text=%r", key[:40], text)

        return CachedChunkedStream(
            tts=self, input_text=text, frames=frames, request_id=shortuuid(),
        )

    async def _write_cache(
            self, key: str, chunks: list[bytes], samples_per_channel: int,
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
            await r.set(key, self._store.path_for_key(key), ex=self._ttl_days * 86400)
            logger.debug("Cache write-back success key=%s", short_key)
        except Exception:
            logger.warning("Cache write-back failed key=%s", short_key, exc_info=True)

    # -- Orphan cleanup --------------------------------------------------------

    async def cleanup_orphans(self) -> None:
        """Delete cached files on disk that are no longer referenced in Redis."""
        import glob as globmod

        storage_dir = os.getenv("CACHED_TTS_STORAGE_DIR", ".tts_cache")
        files = globmod.glob(os.path.join(storage_dir, "**", "*.msgpack"), recursive=True)
        if not files:
            return

        try:
            r = await self._get_redis()
        except Exception:
            logger.warning("Cannot connect to Redis for orphan cleanup", exc_info=True)
            return

        deleted = 0
        for fpath in files:
            try:
                key = _key_from_filepath(fpath, storage_dir)
                if not await r.exists(key):
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

    def _synthesize_passthrough(
            self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> ChunkedStream:
        return _PassthroughChunkedStream(
            tts=self, primary=self._primary, fallbacks=self._fallbacks,
            input_text=text, conn_options=conn_options,
        )

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    async def _redis_get(self, key: str) -> str | None:
        try:
            r = await self._get_redis()
            return await r.get(key)
        except Exception:
            logger.warning("Redis GET failed for key=%s", key[:40], exc_info=True)
            return None

    async def _store_read(self, key: str) -> bytes | None:
        try:
            return await self._store.get(key)
        except Exception:
            logger.warning("File read failed for key=%s", key[:40], exc_info=True)
            return None

    async def _delete_stale_key(self, key: str) -> None:
        logger.info("Stale Redis key (file missing), deleting key=%s", key[:40])
        try:
            r = await self._get_redis()
            await r.delete(key)
        except Exception:
            logger.warning("Failed to delete stale Redis key=%s", key[:40], exc_info=True)

    def _deserialize_frames(self, key: str, data: bytes) -> list[rtc.AudioFrame] | None:
        try:
            meta = _deserialize_audio(data)
        except Exception:
            logger.warning("Deserialization failed for key=%s", key[:40], exc_info=True)
            return None

        return [
            rtc.AudioFrame(
                data=chunk,
                sample_rate=meta["sample_rate"],
                num_channels=meta["num_channels"],
                samples_per_channel=meta["samples_per_channel"],
            )
            for chunk in meta["chunks"]
        ]
