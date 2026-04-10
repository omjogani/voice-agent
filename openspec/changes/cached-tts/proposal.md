## Why

TTS synthesis is the largest per-request cost in the voice agent pipeline. Many conversational responses are repeated frequently ("Got it", "Sure, one moment", acknowledgements, greetings). Caching synthesized audio for these repeated sentences in Redis with LFU eviction eliminates redundant TTS API calls, cutting spend proportional to the hit rate while maintaining the same voice quality and latency characteristics.

## What Changes

- Introduce `CachedTTS`, a new TTS wrapper that owns the fallback chain directly (replacing `FallbackAdapter` for TTS) and adds a two-tier cache: Redis as the key index, local filesystem for audio storage
- Redis stores cache key → file path mappings; audio chunks are stored as msgpack files on disk under `.tts_cache/`
- On cache hit: Redis lookup returns file path, read audio from disk, replay frames instantly (near-zero TTFA)
- On cache miss: tee the live synthesis stream to the caller, write audio file to disk, then SET the file path in Redis — zero added latency on the miss path
- Only cache audio from the primary provider to prevent voice mixing across fallback providers
- Redis runs locally via Docker Compose with LFU eviction policy managing the index; configurable via env vars with a kill switch (`CACHED_TTS_ENABLED=false`)
- Periodic cleanup handles orphaned files (Redis evicted key but file remains on disk)
- Storage abstraction (`CacheStore` protocol) enables future swap to S3 for production
- Add hit/miss counters and estimated cost savings to the shutdown summary alongside existing `UsageCollector`

## Capabilities

### New Capabilities
- `tts-caching`: Two-tier TTS cache (Redis index + filesystem audio storage) with LFU eviction, primary-only caching, stream-tee write-back, and storage abstraction for S3 migration
- `tts-cache-observability`: Hit/miss logging per request, counters, and cost-savings summary at shutdown

### Modified Capabilities
<!-- No existing spec-level behavior changes. The TTS fallback chain moves from FallbackAdapter
     into CachedTTS but the external contract (build_tts() returns a TTS) is unchanged. -->

## Impact

- **Code**: `adapter_factory.py` — `build_tts()` returns `CachedTTS(...)` instead of `FallbackAdapter`. New file `cached_tts.py` for the `CachedTTS` class.
- **Dependencies**: `redis[hiredis]` (async Redis client), `msgpack` (serialization), `aiofiles` (async file I/O) added to `pyproject.toml`
- **Infrastructure**: New `docker-compose.yml` + `redis.conf` for local Redis with LFU config; `.tts_cache/` directory for audio file storage
- **Config**: New env vars (`REDIS_URL`, `CACHED_TTS_ENABLED`, `CACHED_TTS_TTL_DAYS`, `CACHED_TTS_KEY_VERSION`, `CACHED_TTS_STORAGE_DIR`) added to `.env.example`
- **Metrics**: Existing `register_metrics` in `agent.py` continues to work unchanged; `CachedTTS` adds its own cache-specific counters