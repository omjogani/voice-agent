## 1. Test Infrastructure

- [x] 1.1 Add `pytest`, `pytest-asyncio`, and `fakeredis` as dev dependencies via `uv add --dev`
- [x] 1.2 Add `[tool.pytest.ini_options]` to `pyproject.toml` with `asyncio_mode = "auto"` and `testpaths = ["tests"]`
- [x] 1.3 Create `tests/__init__.py`
- [x] 1.4 Create `tests/conftest.py` with shared fixtures: `MockTTS`, `FailingTTS`, `fake_redis`, `tmp_store` (LocalCacheStore using `tmp_path`), and `make_cached_tts` factory

## 2. Unit Tests — Pure Functions

- [x] 2.1 Create `tests/test_cache_store.py` with tests for `LocalCacheStore`: put/get round-trip, get missing returns None, delete removes file, path_for_key derivation
- [x] 2.2 Create `tests/test_cached_tts.py` with tests for `_normalize_text`: lowercase+trim, punctuation stripping, whitespace collapsing
- [x] 2.3 Add tests for `_build_cache_key`: correct format, same text different punctuation produces same key, different voice produces different key
- [x] 2.4 Add tests for `_serialize_audio` / `_deserialize_audio` round-trip: all fields preserved, chunk boundaries preserved

## 3. Integration Tests — Cache Hit/Miss Flows

- [x] 3.1 Add test: `CachedTTS.capabilities.streaming` is `False`
- [x] 3.2 Add test: cache miss calls primary, writes file to store, sets Redis key with file path
- [x] 3.3 Add test: cache hit reads from store, replays frames, does not call primary
- [x] 3.4 Add test: cache hit preserves frame boundaries (3 chunks in → 3 frames out)
- [x] 3.5 Add test: stale Redis key (file missing) triggers key deletion and falls through to miss

## 4. Integration Tests — Fallback Chain

- [x] 4.1 Add test: primary fails, fallback succeeds, audio returned, no cache write
- [x] 4.2 Add test: all providers fail, last exception raised

## 5. Integration Tests — Error Resilience

- [x] 5.1 Add test: Redis GET failure returns None from `_try_cache_read`
- [x] 5.2 Add test: Redis SET failure in `_write_cache` logs warning, no exception
- [x] 5.3 Add test: store `get()` raises I/O error, `_try_cache_read` returns None

## 6. Integration Tests — Kill Switch

- [x] 6.1 Add test: `CACHED_TTS_ENABLED=false` returns passthrough (no Redis, no store)

## 7. Integration Tests — Orphan Cleanup

- [x] 7.1 Add test: orphaned file (no Redis key) is deleted by `cleanup_orphans`
- [x] 7.2 Add test: active file (Redis key exists) is preserved by `cleanup_orphans`

## 8. Integration Tests — Observability

- [x] 8.1 Add test: counters increment correctly after hits and misses
- [x] 8.2 Add test: `get_summary()` with zero activity returns correct string
- [x] 8.3 Add test: `get_summary()` with activity returns correct hit rate
- [x] 8.4 Add test: cache hit emits INFO log with `cache_hit=true`
- [x] 8.5 Add test: cache miss emits INFO log with `cache_hit=false`
- [x] 8.6 Add test: successful `_write_cache` emits DEBUG log
- [x] 8.7 Add test: failed `_write_cache` emits WARNING log

## 9. Verify

- [x] 9.1 Run `uv run pytest -v` and confirm all tests pass