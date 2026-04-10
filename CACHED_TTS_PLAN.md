# CachedTTS — Design Plan

Goal: cut TTS spend by caching synthesized audio for frequently-repeated sentences in Redis, with LFU eviction. `CachedTTS` owns the fallback chain directly (no `FallbackAdapter`), so it knows which provider served each request and caches only primary-provider audio.

---

## 1. Where it plugs in — the StreamAdapter trick

**Key finding:** `AgentSession` always calls `.stream()` on the TTS (see `voice/agent.py:480`). However, when `TTS.capabilities.streaming=False`, the session auto-wraps it in `StreamAdapter` (`voice/agent.py:473-477`). The `StreamAdapter`:
1. Tokenizes incoming LLM text tokens into **complete sentences** (via `blingfire.SentenceTokenizer`)
2. Calls `synthesize(sentence)` per sentence (`stream_adapter.py:131`)

So: **`CachedTTS` reports `streaming=False`**. The framework sentence-splits for us. Each sentence hits `CachedTTS.synthesize()` — our cache entry point. No need to implement `stream()` caching.

`build_tts()` returns `CachedTTS(primary=cartesia, fallbacks=[inworld])`. `agent.py` is untouched.

```
AgentSession
  └─ StreamAdapter (auto-wrapped because streaming=False)
       └─ sentence tokenizer splits LLM output into sentences
            └─ CachedTTS.synthesize(sentence)
                 ├─ Redis GET (primary's key)
                 │    ├─ hit  → replay cached audio
                 │    └─ miss → try primary
                 │               ├─ ok  → stream to caller + async cache write
                 │               └─ fail → try fallbacks (no cache write)
                 └─ (never called via stream() directly)
```

---

## 2. Cache key

```
tts:{key_version}:{provider}:{model}:{voice_id}:{sample_rate}:{sha1(normalized_text)}
```

- `provider/model/voice_id` — audio from Cartesia != Inworld. Key pins the voice so cache lookups always use the primary's identity. On hit, we know it's the right voice.
- `sample_rate` — guard against format drift.
- `key_version` — from env (`CACHED_TTS_KEY_VERSION`), bump to invalidate all cached audio.
- `normalized_text` — lowercased, collapsed whitespace, trimmed, **punctuation stripped**. "Got it." and "Got it!" produce the same key. Prosody loss from punctuation is negligible for short common phrases. `StreamAdapter` already handles sentence segmentation upstream, so each `synthesize()` call receives a single sentence.

Hashing: sha1 (not security-sensitive). Keeps keys bounded.

---

## 3. Value format

Store raw PCM bytes + metadata via msgpack.

```
msgpack({
  "sample_rate": int,
  "num_channels": int,
  "samples_per_channel": int,   # per chunk
  "chunks": [bytes, bytes, ...], # list of raw PCM chunks preserving original frame boundaries
  "provider": str,
  "created_at": float,
})
```

On hit, rehydrate into `rtc.AudioFrame` instances and push through a `ChunkedStream` we control.

Chunk list (not single blob) — preserves original frame boundaries so downstream consumers see the same frame sizes they'd get from a live synthesis. No re-chunking needed.

**No compression in v1.** PCM compresses well but adds encode/decode cost. Revisit if Redis memory becomes the bottleneck.

---

## 4. Cache policy — what to cache, what to skip

**Cache everything from the primary provider.** No text-length filter, no content heuristics. Redis LFU evicts low-frequency entries naturally — that's the whole point of LFU. Artificial filters are redundant complexity.

Only rule: **primary-only**. If Cartesia fails and Inworld serves the request, don't cache it. Inworld audio under a Cartesia cache key would cause voice mixing.

---

## 5. LFU eviction — Redis-native

Redis's built-in `maxmemory-policy allkeys-lfu`. No app-side bookkeeping.

Redis config (applied via `redis.conf` in Docker):
```
maxmemory 512mb
maxmemory-policy allkeys-lfu
lfu-log-factor 10
lfu-decay-time 4320       # 3 days in minutes — decays cold keys so stale entries get evicted
```

Redis runs in Docker (`docker-compose.yml`), dedicated DB 1 (`SELECT 1`). Prod: ElastiCache (latency target: <20ms p99 GET).

**TTL:** 30-day safety TTL on every key so entries can't live forever if we bump voices. LFU is the primary eviction driver; TTL is the floor.

---

## 6. The miss path — tee the stream without doubling latency

On a cache miss, we must NOT buffer full audio before returning the first frame — that would destroy TTFA (`register_metrics` in `agent.py:60` tracks this).

Approach: wrap the inner `ChunkedStream` so every `SynthesizedAudio` frame yielded to the consumer is **also** appended to an in-memory buffer. When the stream ends cleanly (`is_final` seen, no errors), fire-and-forget `asyncio.create_task` to serialize + `SET` in Redis. Consumer sees zero added latency.

Never write to cache when:
- Inner stream raised an exception partway
- Fallback provider served the request (not primary)
- Partial stream (no `is_final` frame)

Redis write errors are swallowed and logged — caching is best-effort, never blocks the call path.

---

## 7. Hit path — replay via ChunkedStream subclass

Subclass `ChunkedStream` to replay cached audio. Implement `_run` to push cached frames into `_event_ch`. This integrates with the existing metrics/tracing plumbing.

**Metrics on cache hits:** Log `cache_hit=true`, text, and key prefix on every hit. For TTS metrics, emit with a `cached` label so hit rate is visible in the existing `MetricsCollectedEvent` flow. This keeps observability consistent — `register_metrics` in `agent.py` sees both cached and uncached events without changes.

**Pacing:** Yield all chunks instantly. The audio output layer paces playback itself. Instant yield = minimal TTFA on cache hits.

---

## 8. Streaming API — not cached

`CachedTTS` reports `capabilities.streaming=False`. The framework never calls `stream()` on us directly — it wraps us in `StreamAdapter` which calls `synthesize()` per sentence. No streaming cache logic needed.

If `stream()` is ever called directly (shouldn't happen, but defensive): delegate to primary with manual fallback, no caching.

---

## 9. Fallback chain — owned by CachedTTS

`CachedTTS` holds `primary` + `fallbacks` list, replacing `FallbackAdapter` for TTS. Cache key is built from primary's identity. On miss:
1. Try `primary.synthesize(text)` — if it succeeds, tee the stream and cache.
2. If primary fails, try each fallback in order — serve audio but don't cache.

`build_tts()` stays short and inline per `CLAUDE.md` convention:

```python
def build_tts() -> CachedTTS:
    return CachedTTS(
        primary=inference.TTS.from_model_string("cartesia/sonic-3:f31cc..."),
        fallbacks=[inference.TTS.from_model_string("inworld/inworld-tts-1")],
    )
```

---

## 10. Observability

Log on every `synthesize()` call:
- `cache_hit` (bool)
- `key` (short prefix for debugging)
- `text` (the sentence)
- On miss: whether write-back succeeded

Counters visible in shutdown summary (alongside existing `UsageCollector`):
- Total calls, hits, misses, hit rate %
- Estimated $ saved = hits x avg_chars x provider per-char rate

---

## 11. Config surface

Env vars (add to `.env.example`):
```
REDIS_URL=redis://localhost:6379/1
CACHED_TTS_ENABLED=true
CACHED_TTS_TTL_DAYS=30
CACHED_TTS_KEY_VERSION=v1
```

`CACHED_TTS_ENABLED=false` → `CachedTTS` becomes pure pass-through (try primary, fallback on failure, no Redis interaction). Kill switch for incidents.

---

## 12. Docker setup

`docker-compose.yml` with Redis, custom config for LFU:

```yaml
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data
      - ./redis.conf:/usr/local/etc/redis/redis.conf
    command: redis-server /usr/local/etc/redis/redis.conf

volumes:
  redis-data:
```

`redis.conf`:
```
maxmemory 512mb
maxmemory-policy allkeys-lfu
lfu-log-factor 10
lfu-decay-time 4320
```

---

## 13. Build order

1. `docker-compose.yml` + `redis.conf` — get Redis running locally.
2. Skeleton `CachedTTS(TTS)` with `streaming=False`, owns primary+fallbacks, pure pass-through. Wire into `build_tts()`. Verify agent still runs.
3. Redis client (`redis.asyncio`) + key building + msgpack serialization helpers.
4. Read path: Redis GET → hit replay via `ChunkedStream` subclass.
5. Write path: tee inner stream on miss, async SET on clean completion.
6. Observability: hit/miss counters, logging, shutdown summary.
7. Verify: compare TTFA on hit vs miss, confirm no regression on miss path.