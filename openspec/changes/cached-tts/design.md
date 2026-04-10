## Context

The voice agent uses a TTS fallback chain (Cartesia Sonic-3 primary, Inworld fallback) via `FallbackAdapter` in `adapter_factory.py`. Every sentence synthesized costs a TTS API call, even for frequently repeated phrases. The `AgentSession` auto-wraps non-streaming TTS in a `StreamAdapter` that sentence-tokenizes LLM output and calls `synthesize()` per sentence — this is our natural cache entry point.

A two-tier cache separates concerns: Redis as a fast key index with native LFU eviction, and the local filesystem for audio blob storage. Redis values are lightweight file paths, keeping Redis memory usage minimal. The filesystem tier is abstracted behind a `CacheStore` protocol so it can be swapped to S3 for production.

## Goals / Non-Goals

**Goals:**
- Reduce TTS API spend proportional to cache hit rate
- Zero added latency on cache misses (stream-tee approach)
- Near-zero TTFA on cache hits (instant frame replay from disk)
- Primary-only caching to prevent voice mixing across providers
- Kill switch for incidents (`CACHED_TTS_ENABLED=false`)
- Storage abstraction enabling local filesystem now, S3 later
- Observability: per-request hit/miss logging + shutdown summary with cost estimate

**Non-Goals:**
- Compression of cached PCM (revisit if disk space becomes a bottleneck)
- Caching at the `stream()` level (framework calls `synthesize()` via `StreamAdapter`)
- Cache invalidation API or admin tooling
- Multi-voice support within a single session (one voice config per deployment)
- Caching fallback provider audio
- S3 implementation (future work — only the protocol is defined now)

## Decisions

### 1. Two-tier cache: Redis index + filesystem audio storage

**Choice:** Redis stores `cache_key → file_path` mappings. Audio blobs live on disk as msgpack files under `.tts_cache/`. A `CacheStore` protocol abstracts the storage tier.

**Why:** Audio chunks are large (50-200KB per sentence). Storing them in Redis wastes expensive memory on bulk data. Redis excels at fast key lookups and LFU eviction — let it manage *what* is cached. The filesystem handles *storing* the audio cheaply. The `CacheStore` protocol (`get`/`put`) makes swapping to S3 a single implementation change.

**Alternative considered:** Audio blobs directly in Redis values — rejected because it bloats Redis memory and the 512MB limit would hold far fewer entries. Also considered pure filesystem with no Redis — rejected because it loses native LFU eviction.

### 2. Filesystem folder structure mirrors the cache key

**Choice:**
```
.tts_cache/
  v1/                                    ← key_version
    cartesia/sonic-3/f31cc.../48000/     ← provider/model/voice_id/sample_rate
      a3f2c8...msgpack                   ← sha1(normalized_text).msgpack
```

**Why:** Segments match the cache key hierarchy, making it inspectable (browse by provider/voice), easy to invalidate (new version = new top-level dir), and S3-ready (same path becomes an S3 object key). The sha1 filename keeps names bounded and filesystem-safe.

### 3. CachedTTS owns the fallback chain (replaces FallbackAdapter for TTS)

**Choice:** `CachedTTS` holds `primary` + `fallbacks` list directly instead of wrapping a `FallbackAdapter`.

**Why:** The cache key must be built from the primary provider's identity (provider, model, voice_id, sample_rate). `FallbackAdapter` is opaque — it doesn't expose which provider served a request. Owning the chain lets `CachedTTS` know exactly which provider is being tried and cache only primary responses.

**Alternative considered:** Wrap `FallbackAdapter` and inspect internal state — rejected because it couples to `FallbackAdapter` internals and doesn't reliably distinguish primary vs fallback responses.

### 4. Report `streaming=False` to leverage StreamAdapter sentence tokenization

**Choice:** `CachedTTS.capabilities.streaming = False`.

**Why:** When `streaming=False`, `AgentSession` auto-wraps in `StreamAdapter`, which sentence-tokenizes LLM output and calls `synthesize(sentence)` per sentence. This gives us complete sentences as cache keys for free — no custom tokenization needed.

**Alternative considered:** Implement `stream()` with partial-text caching — rejected due to complexity of matching partial token sequences and no clear benefit.

### 5. Cache key includes provider/model/voice/sample_rate + sha1(normalized_text)

**Choice:** `tts:{key_version}:{provider}:{model}:{voice_id}:{sample_rate}:{sha1(normalized_text)}`

**Why:** Pins cached audio to a specific voice configuration. Normalized text (lowercase, collapsed whitespace, punctuation stripped) means "Got it." and "Got it!" share a key — prosody difference is negligible for short phrases. SHA1 keeps keys bounded; not security-sensitive.

**Alternative considered:** Include punctuation in key — rejected because it fragments the cache for no audible difference.

### 6. Audio files stored as msgpack (no compression)

**Choice:** msgpack-serialized struct with `chunks: [bytes, ...]` preserving original frame boundaries, plus metadata (sample_rate, num_channels, samples_per_channel, provider, created_at). Written to disk via `aiofiles`.

**Why:** Preserving chunk boundaries means downstream consumers see the same frame sizes as live synthesis — no re-chunking needed. msgpack is fast and compact for binary data. No compression in v1 to avoid encode/decode latency.

### 7. Stream-tee on cache miss (zero added latency)

**Choice:** Wrap the inner `ChunkedStream` so every frame yielded to the consumer is also appended to an in-memory buffer. On clean stream completion, fire-and-forget `asyncio.create_task` to write the audio file to disk and SET the file path in Redis.

**Why:** Buffering the full response before returning would destroy TTFA. The tee approach lets the consumer receive frames at the same speed as a non-cached path. Disk writes and Redis SETs are async and best-effort — failures are logged, never block.

### 8. Redis with native LFU eviction for the index

**Choice:** `maxmemory-policy allkeys-lfu` with 512MB limit, `lfu-decay-time 4320` (3 days), 30-day safety TTL per key. Redis values are file paths (~100 bytes each), so 512MB holds millions of index entries.

**Why:** LFU is ideal — frequently used phrases stay indexed, rare ones get evicted naturally. No app-side bookkeeping. The 30-day TTL is a safety net for voice changes, not the primary eviction driver.

### 9. Orphaned file cleanup

**Choice:** When Redis evicts a key (LFU or TTL), the audio file remains on disk. A periodic cleanup task scans `.tts_cache/` for files not referenced by any Redis key and deletes them. Runs on agent startup and optionally on a configurable interval.

**Why:** Redis LFU eviction is fire-and-forget — there's no eviction callback. Orphaned files waste disk but not Redis memory. Startup cleanup is simple and catches drift. The reverse case (Redis has key, file missing) is handled as a cache miss — delete the stale Redis key and fall through to live synthesis.

### 10. CacheStore protocol for storage abstraction

**Choice:**
```python
class CacheStore(Protocol):
    async def get(self, key: str) -> bytes | None: ...
    async def put(self, key: str, data: bytes, ttl_days: int) -> None: ...
```

`LocalCacheStore` implements this with `aiofiles`, deriving the file path from the key. Future `S3CacheStore` uses `aiobotocore` with the same key-as-object-path pattern.

**Why:** Keeps `CachedTTS` decoupled from the storage backend. Swapping local → S3 requires only a new `CacheStore` implementation and a config change.

## Risks / Trade-offs

- **[Stale audio after voice change]** → Bump `CACHED_TTS_KEY_VERSION` env var to invalidate all cached audio. The 30-day TTL also provides a natural floor.
- **[Redis unavailability]** → `CachedTTS` degrades to pure pass-through (try primary, fallback chain). Redis errors are caught and logged, never propagated. `CACHED_TTS_ENABLED=false` is the manual kill switch.
- **[Orphaned files accumulate on disk]** → Startup cleanup + optional periodic scan. Disk is cheap; orphans are bounded by the rate of Redis evictions.
- **[File missing but Redis key exists]** → Treat as cache miss, delete stale Redis key, proceed to live synthesis. Self-healing.
- **[Disk I/O latency on cache hit]** → Local SSD reads for ~100KB files are sub-millisecond. Still far faster than a TTS API call (~200-500ms). For S3, latency is higher but acceptable vs. synthesis cost.
- **[Punctuation stripping reduces prosody accuracy]** → Acceptable trade-off for short conversational phrases. If problematic, revisit normalization rules.
- **[msgpack schema evolution]** → Adding fields to the cached struct is forward-compatible with msgpack. Removing/changing fields requires a key version bump.