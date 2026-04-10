## 1. Infrastructure Setup

- [x] 1.1 Create `docker-compose.yml` with Redis 7 Alpine service, volume mount for data, and custom config path
- [x] 1.2 Create `redis.conf` with `maxmemory 512mb`, `maxmemory-policy allkeys-lfu`, `lfu-log-factor 10`, `lfu-decay-time 4320`
- [x] 1.3 Add `redis[hiredis]`, `msgpack`, and `aiofiles` dependencies to `pyproject.toml` via `uv add`
- [x] 1.4 Add `REDIS_URL`, `CACHED_TTS_ENABLED`, `CACHED_TTS_TTL_DAYS`, `CACHED_TTS_KEY_VERSION`, `CACHED_TTS_STORAGE_DIR` to `.env.example`
- [x] 1.5 Add `.tts_cache/` to `.gitignore`

## 2. CacheStore Abstraction

- [x] 2.1 Create `cache_store.py` with `CacheStore` protocol defining `get(key) -> bytes | None` and `put(key, data, ttl_days) -> None`
- [x] 2.2 Implement `LocalCacheStore` using `aiofiles`: derive file path from key as `.tts_cache/{key_version}/{provider}/{model}/{voice_id}/{sample_rate}/{sha1}.msgpack`, create directories on write
- [x] 2.3 Implement `LocalCacheStore.delete(key)` for cleanup and stale key handling

## 3. CachedTTS Skeleton

- [x] 3.1 Create `cached_tts.py` with `CachedTTS(TTS)` class: `__init__` accepting `primary`, `fallbacks`, and `CacheStore`; report `capabilities.streaming=False`
- [x] 3.2 Implement pass-through `synthesize()`: try primary, on failure try each fallback in order, raise last exception if all fail
- [x] 3.3 Implement defensive `stream()` override: delegate to primary with manual fallback, no caching
- [x] 3.4 Wire `build_tts()` in `adapter_factory.py` to return `CachedTTS(primary=..., fallbacks=[...], store=LocalCacheStore(...))` with inline `from_model_string` calls
- [x] 3.5 Verify agent still runs with `uv run agent.py dev`

## 4. Redis Client and Key Building

- [x] 4.1 Add async Redis client initialization in `CachedTTS.__init__` using `REDIS_URL` env var, with lazy connection
- [x] 4.2 Implement cache key builder: `tts:{key_version}:{provider}:{model}:{voice_id}:{sample_rate}:{sha1(normalized_text)}` with text normalization (lowercase, collapse whitespace, strip punctuation)
- [x] 4.3 Implement msgpack serialization helper: serialize list of PCM chunks + metadata (sample_rate, num_channels, samples_per_channel, provider, created_at)
- [x] 4.4 Implement msgpack deserialization helper: reconstruct chunk list + metadata from cached bytes

## 5. Cache Read Path (Hit)

- [x] 5.1 Create `CachedChunkedStream(ChunkedStream)` subclass with `_run` method that pushes cached `rtc.AudioFrame` instances into `_event_ch`
- [x] 5.2 In `synthesize()`, perform Redis GET to get file path; on hit, read audio from `CacheStore`, deserialize, return `CachedChunkedStream`
- [x] 5.3 Handle stale Redis key (file missing on disk): delete Redis key, fall through to live synthesis
- [x] 5.4 Wrap Redis GET and file read in try/except; on failure, log warning and fall through to live synthesis

## 6. Cache Write Path (Miss)

- [x] 6.1 Create a stream-tee wrapper that yields each `SynthesizedAudio` frame to the caller while appending to an in-memory buffer
- [x] 6.2 On clean stream completion (final frame seen, no exceptions), fire `asyncio.create_task` to: serialize via msgpack, write file via `CacheStore.put()`, then SET file path in Redis with TTL
- [x] 6.3 Skip cache write when: stream errored, no final frame, or audio was served by a fallback provider
- [x] 6.4 Wrap file write and Redis SET in try/except; on failure, log warning (never block caller)

## 7. Kill Switch

- [x] 7.1 Read `CACHED_TTS_ENABLED` env var (default `true`); when `false`, skip all Redis and filesystem interactions in `synthesize()` and go straight to primary/fallback chain

## 8. Orphaned File Cleanup

- [x] 8.1 Implement startup cleanup: scan `.tts_cache/` for all `.msgpack` files, check each against Redis, delete files whose keys no longer exist
- [x] 8.2 Call cleanup on `CachedTTS` initialization (async, non-blocking)

## 9. Observability

- [x] 9.1 Log on every `synthesize()` call: `cache_hit` (bool), short key prefix, sentence text
- [x] 9.2 Log write-back result: DEBUG on success (file path + Redis key), WARNING on failure
- [x] 9.3 Maintain in-memory counters: total calls, hits, misses
- [x] 9.4 Implement `get_summary()` method returning total, hits, misses, hit rate %, estimated cost savings
- [x] 9.5 Register shutdown callback in `agent.py` to log `CachedTTS.get_summary()` alongside existing `UsageCollector`

## 10. Verification

- [ ] 10.1 Run agent, confirm cache miss path works end-to-end (audio plays, file written to `.tts_cache/`, Redis key set)
- [ ] 10.2 Repeat same sentence, confirm cache hit (no TTS API call, audio replays from disk)
- [ ] 10.3 Compare TTFA on hit vs miss — hit should be near-zero
- [ ] 10.4 Set `CACHED_TTS_ENABLED=false`, confirm no Redis or disk interaction
- [ ] 10.5 Stop Redis, confirm agent degrades gracefully (live synthesis, no crashes)
- [ ] 10.6 Delete a cached file manually, confirm stale Redis key is handled (treated as miss, key cleaned up)