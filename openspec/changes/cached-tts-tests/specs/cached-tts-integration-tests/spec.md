## ADDED Requirements

### Requirement: CachedTTS reports non-streaming capabilities
The test suite SHALL verify that `CachedTTS.capabilities.streaming` is `False`.

#### Scenario: Streaming is false
- **WHEN** a `CachedTTS` instance is created
- **THEN** `capabilities.streaming` is `False`

### Requirement: Cache miss calls primary and writes to cache
The test suite SHALL verify the full miss path: primary is called, audio is returned, file is written to disk, and Redis key is set.

#### Scenario: Miss path end-to-end
- **WHEN** `_try_cache_read` returns `None` (no Redis key) and the mock primary succeeds
- **THEN** the primary's `synthesize()` is called
- **AND** after completion, the store contains the audio file and Redis contains the key pointing to the file path

### Requirement: Cache hit replays from disk without calling providers
The test suite SHALL verify that on a cache hit, no TTS provider is called and audio is replayed from the store.

#### Scenario: Hit path skips providers
- **WHEN** Redis contains a key and the store contains the matching audio file
- **THEN** `_try_cache_read` returns a `CachedChunkedStream` with the correct frames
- **AND** the primary's `synthesize()` is never called

#### Scenario: Hit preserves frame boundaries
- **WHEN** cached audio with 3 chunks is replayed
- **THEN** 3 frames are yielded with matching byte content

### Requirement: Stale Redis key handled gracefully
The test suite SHALL verify that when Redis has a key but the file is missing, it's treated as a miss.

#### Scenario: File missing triggers stale key deletion
- **WHEN** Redis contains a key but the store returns `None` for that key
- **THEN** `_try_cache_read` returns `None`
- **AND** the stale Redis key is deleted

### Requirement: Fallback chain without caching
The test suite SHALL verify that when the primary fails, fallbacks are tried, and fallback audio is NOT cached.

#### Scenario: Primary fails, fallback succeeds, no cache write
- **WHEN** the primary raises an exception and the fallback succeeds
- **THEN** audio from the fallback is returned
- **AND** no file is written to the store and no Redis key is set

#### Scenario: All providers fail raises last exception
- **WHEN** both primary and all fallbacks raise exceptions
- **THEN** the last exception is raised to the caller

### Requirement: Redis errors are non-fatal
The test suite SHALL verify that Redis failures don't propagate.

#### Scenario: Redis GET failure falls through to live synthesis
- **WHEN** Redis raises `ConnectionError` on GET
- **THEN** `_try_cache_read` returns `None` (treated as miss)

#### Scenario: Redis SET failure does not affect caller
- **WHEN** `_write_cache` is called and Redis raises on SET
- **THEN** the error is logged but no exception propagates

### Requirement: Store errors are non-fatal
The test suite SHALL verify that CacheStore failures don't propagate.

#### Scenario: Store read failure falls through to live synthesis
- **WHEN** Redis returns a key but `store.get()` raises an I/O error
- **THEN** `_try_cache_read` returns `None`

### Requirement: Kill switch disables all caching
The test suite SHALL verify that `CACHED_TTS_ENABLED=false` bypasses Redis and store entirely.

#### Scenario: Kill switch uses passthrough
- **WHEN** `CachedTTS` is created with `CACHED_TTS_ENABLED=false`
- **THEN** `synthesize()` returns a `_PassthroughChunkedStream` (no Redis, no store interaction)

### Requirement: Orphan cleanup deletes unreferenced files
The test suite SHALL verify that `cleanup_orphans` removes files not referenced by Redis keys.

#### Scenario: Orphaned file deleted
- **WHEN** a `.msgpack` file exists on disk but its derived key does not exist in Redis
- **THEN** `cleanup_orphans` deletes the file

#### Scenario: Active file preserved
- **WHEN** a `.msgpack` file exists on disk and its derived key exists in Redis
- **THEN** `cleanup_orphans` does not delete the file

### Requirement: Counters and summary
The test suite SHALL verify that hit/miss counters increment correctly and `get_summary()` formats properly.

#### Scenario: Counters after hits and misses
- **WHEN** `_try_cache_read` produces 2 hits and the dispatcher produces 3 misses
- **THEN** `_total` is 5, `_hits` is 2, `_misses` is 3

#### Scenario: Summary with zero activity
- **WHEN** `get_summary()` is called with no activity
- **THEN** the string contains `total=0 hits=0 misses=0 hit_rate=0.0%`

#### Scenario: Summary with activity
- **WHEN** `get_summary()` is called after 3 hits and 7 misses
- **THEN** the string contains `total=10 hits=3 misses=7 hit_rate=30.0%`

### Requirement: Cache logging
The test suite SHALL verify that INFO logs are emitted on hit and miss with correct fields.

#### Scenario: Hit log contains cache_hit=true
- **WHEN** a cache hit occurs
- **THEN** an INFO log containing `cache_hit=true` and the sentence text is emitted

#### Scenario: Miss log contains cache_hit=false
- **WHEN** a cache miss occurs
- **THEN** an INFO log containing `cache_hit=false` and the sentence text is emitted

### Requirement: Write-back logging
The test suite SHALL verify that write-back success/failure produces the correct log levels.

#### Scenario: Successful write-back logs at DEBUG
- **WHEN** `_write_cache` succeeds
- **THEN** a DEBUG log containing `write-back success` is emitted

#### Scenario: Failed write-back logs at WARNING
- **WHEN** `_write_cache` fails (Redis or store error)
- **THEN** a WARNING log containing `write-back failed` is emitted