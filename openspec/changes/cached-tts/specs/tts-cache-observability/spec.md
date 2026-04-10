## ADDED Requirements

### Requirement: Per-request cache logging
`CachedTTS` SHALL log on every `synthesize()` call: `cache_hit` (bool), a short key prefix (for debugging), and the sentence text.

#### Scenario: Cache hit is logged
- **WHEN** a cache hit occurs
- **THEN** an INFO log is emitted with `cache_hit=true`, the key prefix, and the sentence text

#### Scenario: Cache miss is logged
- **WHEN** a cache miss occurs
- **THEN** an INFO log is emitted with `cache_hit=false`, the key prefix, and the sentence text

### Requirement: Write-back result logging
On cache miss, after the async write-back completes, `CachedTTS` SHALL log whether the Redis SET succeeded or failed.

#### Scenario: Successful write-back logged
- **WHEN** a cache miss write-back to Redis succeeds
- **THEN** a DEBUG log is emitted confirming the cache write with the key

#### Scenario: Failed write-back logged
- **WHEN** a cache miss write-back to Redis fails
- **THEN** a WARNING log is emitted with the error details

### Requirement: Shutdown summary with cache statistics
`CachedTTS` SHALL expose a method to produce a summary containing: total calls, hits, misses, hit rate percentage, and estimated cost savings (hits x avg characters x provider per-character rate). This summary SHALL be logged at shutdown alongside the existing `UsageCollector` summary.

#### Scenario: Shutdown logs cache stats
- **WHEN** the agent shuts down
- **THEN** the cache summary (total, hits, misses, hit rate %, estimated savings) is logged

#### Scenario: No cache activity
- **WHEN** the agent shuts down with zero `synthesize()` calls
- **THEN** the summary reports 0 total calls and 0% hit rate