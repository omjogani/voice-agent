## ADDED Requirements

### Requirement: CachedTTS reports non-streaming capabilities
The `CachedTTS` class SHALL report `capabilities.streaming = False` so that the framework wraps it in `StreamAdapter`, which sentence-tokenizes LLM output and calls `synthesize()` per sentence.

#### Scenario: Framework wraps CachedTTS in StreamAdapter
- **WHEN** `AgentSession` receives a `CachedTTS` instance as the TTS
- **THEN** the session auto-wraps it in `StreamAdapter` because `streaming=False`

### Requirement: CachedTTS owns the fallback chain
`CachedTTS` SHALL accept a `primary` TTS and a `fallbacks` list. It SHALL try the primary first, and on failure try each fallback in order. It SHALL NOT use `FallbackAdapter`.

#### Scenario: Primary succeeds
- **WHEN** `synthesize()` is called and the primary provider succeeds
- **THEN** the primary's audio stream is returned to the caller

#### Scenario: Primary fails, fallback succeeds
- **WHEN** `synthesize()` is called and the primary provider raises an exception
- **THEN** `CachedTTS` SHALL try each fallback in order and return the first successful result

#### Scenario: All providers fail
- **WHEN** `synthesize()` is called and both primary and all fallbacks fail
- **THEN** `CachedTTS` SHALL raise the last exception

### Requirement: Cache key construction
The cache key SHALL be `tts:{key_version}:{provider}:{model}:{voice_id}:{sample_rate}:{sha1(normalized_text)}` where normalized text is lowercased, whitespace-collapsed, trimmed, and punctuation-stripped.

#### Scenario: Identical text with different punctuation produces same key
- **WHEN** `synthesize("Got it.")` and `synthesize("Got it!")` are called
- **THEN** both produce the same cache key (punctuation is stripped before hashing)

#### Scenario: Different voice configs produce different keys
- **WHEN** text is synthesized with different provider/model/voice combinations
- **THEN** each combination produces a distinct cache key

### Requirement: Two-tier cache lookup (Redis index + filesystem audio)
On `synthesize()`, `CachedTTS` SHALL first query Redis for the cache key. Redis values are file paths pointing to msgpack audio files on disk. On a Redis hit, `CachedTTS` SHALL read the audio file from the `CacheStore` and replay the frames. If the file is missing (orphaned Redis key), it SHALL treat it as a cache miss, delete the stale Redis key, and proceed to live synthesis.

#### Scenario: Cache hit — Redis key exists and file is present
- **WHEN** `synthesize()` is called, Redis returns a file path, and the file exists on disk
- **THEN** the audio is read from disk, deserialized from msgpack, and replayed as `rtc.AudioFrame` instances

#### Scenario: Cache hit — Redis key exists but file is missing
- **WHEN** `synthesize()` is called, Redis returns a file path, but the file does not exist
- **THEN** `CachedTTS` SHALL delete the stale Redis key and proceed to live synthesis as a cache miss

#### Scenario: Cached audio preserves frame boundaries
- **WHEN** cached audio is replayed from disk
- **THEN** the number and size of audio frames match those originally produced by the primary provider

### Requirement: Cache miss writes audio to disk and file path to Redis
On a cache miss where the primary provider succeeds, `CachedTTS` SHALL tee the audio stream: every frame yielded to the caller is also buffered in memory. On clean completion (all frames received, no errors), the buffered audio SHALL be serialized via msgpack, written to disk via `CacheStore.put()`, and the resulting file path SHALL be SET in Redis with a TTL — all asynchronously via `asyncio.create_task`.

#### Scenario: Cache miss from primary streams without added latency
- **WHEN** `synthesize()` is called for an uncached sentence and the primary succeeds
- **THEN** audio frames are yielded to the caller as they arrive from the primary (no buffering delay)
- **AND** after the stream completes, the audio file is written to disk and its path is stored in Redis

#### Scenario: Partial or errored stream is not cached
- **WHEN** the primary provider's stream raises an exception or ends without a final frame
- **THEN** no file is written to disk and no Redis key is set

### Requirement: Only primary provider audio is cached
`CachedTTS` SHALL NOT write to the cache (neither disk nor Redis) when audio is served by a fallback provider.

#### Scenario: Fallback serves audio
- **WHEN** the primary fails and a fallback provider serves the request
- **THEN** the audio is returned to the caller but no file is written and no Redis key is set

### Requirement: Redis errors are non-fatal
All Redis operations (GET, SET, DEL) SHALL be wrapped in try/except. Redis failures SHALL be logged and SHALL NOT propagate to the caller. On Redis failure, `CachedTTS` SHALL fall through to live synthesis.

#### Scenario: Redis is unavailable on read
- **WHEN** Redis GET fails (connection error, timeout)
- **THEN** `CachedTTS` proceeds to live synthesis as if it were a cache miss

#### Scenario: Redis is unavailable on write
- **WHEN** the async Redis SET fails after a successful synthesis and file write
- **THEN** the error is logged and the caller is unaffected

### Requirement: Filesystem errors are non-fatal
All `CacheStore` operations (read, write) SHALL be wrapped in try/except. File I/O failures SHALL be logged and SHALL NOT propagate to the caller.

#### Scenario: Disk read fails on cache hit
- **WHEN** Redis returns a file path but reading the file raises an I/O error
- **THEN** `CachedTTS` treats it as a cache miss and proceeds to live synthesis

#### Scenario: Disk write fails on cache miss
- **WHEN** writing the audio file to disk fails after a successful synthesis
- **THEN** the error is logged, no Redis key is set, and the caller is unaffected

### Requirement: TTL on Redis index entries
Every Redis SET SHALL include an expiry of `CACHED_TTS_TTL_DAYS` days (default 30). LFU eviction is the primary eviction driver; TTL is a safety net.

#### Scenario: Redis entry expires after TTL
- **WHEN** a Redis key has not been accessed for longer than the configured TTL
- **THEN** Redis removes the key via expiry (audio file becomes orphaned, cleaned up later)

### Requirement: Orphaned file cleanup
`CachedTTS` SHALL provide a cleanup mechanism that scans the `.tts_cache/` directory for audio files not referenced by any Redis key and deletes them. This SHALL run on agent startup.

#### Scenario: Orphaned files removed on startup
- **WHEN** the agent starts and `.tts_cache/` contains files whose cache keys no longer exist in Redis
- **THEN** those files are deleted from disk

#### Scenario: Active files preserved during cleanup
- **WHEN** cleanup runs and a file's cache key still exists in Redis
- **THEN** the file is not deleted

### Requirement: CacheStore protocol for storage abstraction
Audio file storage SHALL be abstracted behind a `CacheStore` protocol with `get(key) -> bytes | None` and `put(key, data, ttl_days) -> None` methods. `LocalCacheStore` SHALL implement this using `aiofiles` with the folder structure `.tts_cache/{key_version}/{provider}/{model}/{voice_id}/{sample_rate}/{sha1}.msgpack`.

#### Scenario: LocalCacheStore writes and reads a file
- **WHEN** `put(key, data, ttl_days)` is called followed by `get(key)`
- **THEN** the returned bytes match the original data

#### Scenario: LocalCacheStore returns None for missing key
- **WHEN** `get(key)` is called for a key that has not been written
- **THEN** `None` is returned

### Requirement: Kill switch disables caching
When `CACHED_TTS_ENABLED=false`, `CachedTTS` SHALL skip all Redis and filesystem interactions and behave as a pure pass-through with primary + fallback chain.

#### Scenario: Caching disabled via env var
- **WHEN** `CACHED_TTS_ENABLED` is set to `false`
- **THEN** `synthesize()` calls go directly to the primary/fallback chain with no Redis, no disk I/O

### Requirement: build_tts returns CachedTTS
`adapter_factory.build_tts()` SHALL return a `CachedTTS` instance with inline `from_model_string` calls, replacing the current `FallbackAdapter` return.

#### Scenario: build_tts wiring
- **WHEN** `build_tts()` is called
- **THEN** it returns `CachedTTS(primary=..., fallbacks=[...])` with the same model strings as before