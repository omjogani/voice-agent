from __future__ import annotations

import logging
import os
from unittest.mock import AsyncMock

import pytest

from tests.conftest import FailingTTS, MockTTS, _make_pcm_bytes


class TestCapabilities:
    def test_streaming_is_false(self, make_cached_tts):
        tts = make_cached_tts()
        assert tts.capabilities.streaming is False


class TestCacheMiss:
    async def test_miss_calls_primary_and_writes_cache(self, make_cached_tts, fake_redis):
        primary = MockTTS()
        tts = make_cached_tts(primary=primary)

        # Trigger miss path directly
        result = await tts._try_cache_read("hello world")
        assert result is None  # no cache entry yet

        # Now simulate what the dispatcher does on miss: call _write_cache
        key = tts._cache_key("hello world")
        chunks = [_make_pcm_bytes(1)]
        await tts._write_cache(key, chunks, 4800)

        # Verify Redis has the key
        file_path = await fake_redis.get(key)
        assert file_path is not None

        # Verify store has the file
        data = await tts._store.get(key)
        assert data is not None


class TestCacheHit:
    async def test_hit_returns_frames_without_calling_primary(self, make_cached_tts, fake_redis):
        primary = MockTTS()
        tts = make_cached_tts(primary=primary)

        # Seed the cache
        key = tts._cache_key("hello")
        chunks = [_make_pcm_bytes(1), _make_pcm_bytes(2), _make_pcm_bytes(3)]
        await tts._write_cache(key, chunks, 4800)

        # Now try cache read
        result = await tts._try_cache_read("hello")
        assert result is not None
        assert len(result._frames) == 3
        assert primary.synthesize_count == 0  # primary was never called

    async def test_hit_preserves_frame_boundaries(self, make_cached_tts, fake_redis):
        primary = MockTTS()
        tts = make_cached_tts(primary=primary)

        # Each chunk must be >= samples_per_channel * num_channels * 2 bytes (int16)
        # Use samples_per_channel that matches each chunk size: size / 2 (mono int16)
        chunks = [b"\x00" * 200, b"\x00" * 400, b"\x00" * 100]
        key = tts._cache_key("test")
        # samples_per_channel = smallest_chunk / 2 = 50
        await tts._write_cache(key, chunks, 50)

        result = await tts._try_cache_read("test")
        assert result is not None
        frame_sizes = [len(f.data.tobytes()) for f in result._frames]
        assert frame_sizes == [200, 400, 100]


class TestStaleKey:
    async def test_stale_redis_key_deleted(self, make_cached_tts, fake_redis):
        tts = make_cached_tts()
        key = tts._cache_key("stale")

        # Set a Redis key pointing to a non-existent file
        await fake_redis.set(key, "/nonexistent/path.msgpack")

        result = await tts._try_cache_read("stale")
        assert result is None

        # Redis key should have been deleted
        val = await fake_redis.get(key)
        assert val is None


class TestFallbackChain:
    async def test_primary_fails_fallback_succeeds_no_cache(self, make_cached_tts, fake_redis):
        primary = FailingTTS()
        fallback = MockTTS()
        tts = make_cached_tts(primary=primary, fallbacks=[fallback])

        # The passthrough path should work
        stream = tts._synthesize_passthrough("hello")
        frames = []
        async for ev in stream:
            frames.append(ev.frame)
        assert len(frames) > 0
        assert fallback.synthesize_count == 1

        # No cache write should have happened
        key = tts._cache_key("hello")
        val = await fake_redis.get(key)
        assert val is None

    async def test_all_fail_raises(self, make_cached_tts):
        primary = FailingTTS(RuntimeError("primary down"))
        fallback = FailingTTS(RuntimeError("fallback down"))
        tts = make_cached_tts(primary=primary, fallbacks=[fallback])

        stream = tts._synthesize_passthrough("hello")
        with pytest.raises(RuntimeError, match="fallback down"):
            async for _ in stream:
                pass


class TestRedisErrors:
    async def test_redis_get_failure_returns_none(self, make_cached_tts):
        tts = make_cached_tts()
        # Make Redis raise on GET
        tts._redis = AsyncMock()
        tts._redis.get = AsyncMock(side_effect=ConnectionError("Redis down"))

        result = await tts._try_cache_read("hello")
        assert result is None

    async def test_redis_set_failure_logs_warning(self, make_cached_tts, caplog):
        tts = make_cached_tts()
        # Make Redis raise on SET
        tts._redis = AsyncMock()
        tts._redis.set = AsyncMock(side_effect=ConnectionError("Redis down"))

        with caplog.at_level(logging.WARNING, logger="cached_tts"):
            await tts._write_cache("some:key:a:b:c:d", [b"data"], 4800)

        assert any("write-back failed" in r.message for r in caplog.records)


class TestStoreErrors:
    async def test_store_read_failure_returns_none(self, make_cached_tts, fake_redis):
        tts = make_cached_tts()
        key = tts._cache_key("hello")
        # Set Redis key so we enter the file-read branch
        await fake_redis.set(key, "/some/path")

        # Make store raise on get
        tts._store = AsyncMock()
        tts._store.get = AsyncMock(side_effect=IOError("disk error"))

        result = await tts._try_cache_read("hello")
        assert result is None


class TestKillSwitch:
    async def test_disabled_returns_passthrough(self, make_cached_tts):
        tts = make_cached_tts(enabled=False)
        stream = tts.synthesize("hello")
        # Should be a _PassthroughChunkedStream, not _CachedSynthesizeDispatcher
        from cached_tts import _PassthroughChunkedStream
        assert isinstance(stream, _PassthroughChunkedStream)
        await stream.aclose()


class TestOrphanCleanup:
    async def test_orphaned_file_deleted(self, make_cached_tts, fake_redis, tmp_path, monkeypatch):
        monkeypatch.setenv("CACHED_TTS_STORAGE_DIR", str(tmp_path))
        tts = make_cached_tts()

        # Write a cache entry then remove the Redis key to create an orphan
        key = tts._cache_key("orphan")
        await tts._write_cache(key, [b"audio"], 4800)
        file_path = tts._store.path_for_key(key)
        assert os.path.exists(file_path)

        # Delete the Redis key to simulate eviction
        await fake_redis.delete(key)

        await tts.cleanup_orphans()
        assert not os.path.exists(file_path)

    async def test_active_file_preserved(self, make_cached_tts, fake_redis, tmp_path, monkeypatch):
        monkeypatch.setenv("CACHED_TTS_STORAGE_DIR", str(tmp_path))
        tts = make_cached_tts()

        key = tts._cache_key("active")
        await tts._write_cache(key, [b"audio"], 4800)
        file_path = tts._store.path_for_key(key)
        assert os.path.exists(file_path)

        # Key still in Redis
        await tts.cleanup_orphans()
        assert os.path.exists(file_path)
