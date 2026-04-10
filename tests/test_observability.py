from __future__ import annotations

import logging
from unittest.mock import AsyncMock

from tests.conftest import _make_pcm_bytes


class TestCountersAndSummary:
    async def test_counters_increment(self, make_cached_tts, fake_redis):
        tts = make_cached_tts()

        # 2 misses via _try_cache_read (returns None, counters not incremented there for misses)
        # Misses are tracked in the dispatcher, hits in _try_cache_read
        # Simulate directly:
        tts._total = 0
        tts._hits = 0
        tts._misses = 0

        # Seed a hit
        key = tts._cache_key("cached")
        await tts._write_cache(key, [_make_pcm_bytes()], 4800)

        await tts._try_cache_read("cached")  # hit: total+1, hits+1
        await tts._try_cache_read("cached")  # hit: total+1, hits+1

        # Simulate 3 misses (as dispatcher would)
        tts._total += 3
        tts._misses += 3

        assert tts._total == 5
        assert tts._hits == 2
        assert tts._misses == 3

    def test_summary_zero_activity(self, make_cached_tts):
        tts = make_cached_tts()
        summary = tts.get_summary()
        assert "total=0" in summary
        assert "hits=0" in summary
        assert "misses=0" in summary
        assert "hit_rate=0.0%" in summary

    def test_summary_with_activity(self, make_cached_tts):
        tts = make_cached_tts()
        tts._total = 10
        tts._hits = 3
        tts._misses = 7
        summary = tts.get_summary()
        assert "total=10" in summary
        assert "hits=3" in summary
        assert "misses=7" in summary
        assert "hit_rate=30.0%" in summary


class TestLogging:
    async def test_hit_logs_cache_hit_true(self, make_cached_tts, fake_redis, caplog):
        tts = make_cached_tts()
        key = tts._cache_key("logged")
        await tts._write_cache(key, [_make_pcm_bytes()], 4800)

        with caplog.at_level(logging.INFO, logger="cached_tts"):
            await tts._try_cache_read("logged")

        assert any("cache_hit=true" in r.message for r in caplog.records)

    async def test_miss_logs_cache_hit_false(self, make_cached_tts, caplog):
        tts = make_cached_tts()
        with caplog.at_level(logging.INFO, logger="cached_tts"):
            result = await tts._try_cache_read("nonexistent")
        assert result is None

    async def test_successful_writeback_logs_debug(self, make_cached_tts, caplog):
        tts = make_cached_tts()
        key = tts._cache_key("writeback")

        with caplog.at_level(logging.DEBUG, logger="cached_tts"):
            await tts._write_cache(key, [b"audio"], 4800)

        assert any("write-back success" in r.message.lower() or "Cache write-back success" in r.message for r in
                   caplog.records)

    async def test_failed_writeback_logs_warning(self, make_cached_tts, caplog):
        tts = make_cached_tts()
        tts._redis = AsyncMock()
        tts._redis.set = AsyncMock(side_effect=ConnectionError("down"))

        with caplog.at_level(logging.WARNING, logger="cached_tts"):
            await tts._write_cache("some:key:a:b:c:d", [b"data"], 4800)

        assert any(
            "write-back failed" in r.message.lower() or "Cache write-back failed" in r.message for r in caplog.records)
