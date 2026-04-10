## Context

The `cached-tts` change introduced `CachedTTS` (two-tier cache: Redis index + filesystem audio) and `LocalCacheStore`. Both modules are fully implemented but only verified via a live agent boot. The existing specs define ~25 scenarios across `tts-caching` and `tts-cache-observability` — most of these can be tested without a live LiveKit room by mocking Redis and TTS providers.

## Goals / Non-Goals

**Goals:**
- Cover all spec scenarios that don't require a live LiveKit room
- Test pure functions (normalization, key building, serialization) directly
- Test CachedTTS behavior (hit/miss/fallback/error/kill-switch) via mocked Redis + mock TTS
- Test LocalCacheStore with real filesystem I/O (using `tmp_path`)
- Test orphan cleanup logic
- Test observability (counters, summary, logging)
- Run with `uv run pytest` — no LiveKit credentials or running Redis needed

**Non-Goals:**
- End-to-end testing with a real LiveKit room or real TTS providers
- Testing the `StreamAdapter` integration (framework-level, not our code)
- Performance/latency benchmarking
- Testing `adapter_factory.build_tts()` (requires LiveKit API keys)

## Decisions

### 1. Mock TTS providers instead of using real ones

**Choice:** Create a `MockTTS(TTS)` fixture that returns pre-built `SynthesizedAudio` frames from `synthesize()`. A `FailingTTS` variant raises on `synthesize()`.

**Why:** Real TTS providers need API keys and network access. Mocking lets us control exactly what audio is returned and when errors occur, making tests deterministic.

### 2. Mock Redis with `fakeredis`

**Choice:** Use `fakeredis.aioredis` as a drop-in replacement for `redis.asyncio`.

**Why:** `fakeredis` implements the Redis protocol in-memory — no Docker needed, supports `GET`/`SET`/`DEL`/`EXISTS`/`KEYS` with TTL. Tests run in CI without Redis. Alternative: `unittest.mock.AsyncMock` — rejected because it doesn't enforce Redis semantics (e.g. TTL, missing keys return None).

### 3. LocalCacheStore tested with real filesystem via `tmp_path`

**Choice:** Use pytest's `tmp_path` fixture for `LocalCacheStore` tests — real file I/O against a temp directory.

**Why:** Mocking `aiofiles` adds complexity without testing the actual file-path derivation logic. `tmp_path` is auto-cleaned and fast.

### 4. Test structure: one file per module

**Choice:**
- `tests/test_cache_store.py` — LocalCacheStore unit tests
- `tests/test_cached_tts.py` — CachedTTS integration tests with mocked Redis/TTS
- `tests/conftest.py` — shared fixtures (MockTTS, fakeredis, tmp store)

**Why:** Keeps tests focused and easy to run selectively (`pytest tests/test_cache_store.py`).

### 5. pytest-asyncio in auto mode

**Choice:** `asyncio_mode = "auto"` in `pyproject.toml` so all `async def test_*` functions are collected automatically.

**Why:** Avoids needing `@pytest.mark.asyncio` on every test.

## Risks / Trade-offs

- **[fakeredis may not cover all Redis behaviors]** → We only use GET/SET/DEL/EXISTS — well-supported by fakeredis. If we hit gaps, fall back to `AsyncMock`.
- **[MockTTS may diverge from real TTS interface]** → MockTTS subclasses the actual `TTS` base class and implements `synthesize()` returning real `ChunkedStream`/`SynthesizedAudio` objects, keeping the contract tight.
- **[CachedTTS reads env vars in `__init__`]** → Tests use `monkeypatch.setenv` to control env vars per test.