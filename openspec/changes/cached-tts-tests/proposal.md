## Why

The `CachedTTS` and `LocalCacheStore` implementations have no automated tests. The cached-tts change includes 6 manual verification tasks (10.1–10.6) that require a live LiveKit room. Automated pytest tests covering the pure-logic and mocked-I/O paths will catch regressions early and remove the need for manual verification of core behaviors like key construction, serialization, cache hit/miss flows, fallback chains, error resilience, and observability.

## What Changes

- Add `pytest` and `pytest-asyncio` as dev dependencies
- Create a `tests/` directory with pytest test modules covering `cached_tts.py` and `cache_store.py`
- Test all spec scenarios from `tts-caching` and `tts-cache-observability` that can be exercised without a live LiveKit room
- Mock Redis and TTS providers to test cache hit, miss, fallback, error resilience, and kill switch paths

## Capabilities

### New Capabilities
- `cached-tts-unit-tests`: Unit tests for pure functions (text normalization, cache key construction, msgpack serialization/deserialization)
- `cached-tts-integration-tests`: Integration tests for CachedTTS and LocalCacheStore using mocked Redis and mock TTS providers, covering hit/miss flows, fallback chain, error resilience, kill switch, orphan cleanup, and observability

### Modified Capabilities
<!-- No existing spec-level behavior changes. This is a test-only addition. -->

## Impact

- **Code**: New `tests/` directory with test modules; `conftest.py` for shared fixtures
- **Dependencies**: `pytest`, `pytest-asyncio` added as dev dependencies
- **Config**: `pyproject.toml` updated with `[tool.pytest.ini_options]`
- **CI**: Tests can be run with `uv run pytest` — no LiveKit credentials or Redis required (all I/O mocked)