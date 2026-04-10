## ADDED Requirements

### Requirement: Text normalization tests
The test suite SHALL verify that `_normalize_text` lowercases, collapses whitespace, trims, and strips punctuation.

#### Scenario: Lowercase and trim
- **WHEN** `_normalize_text("  Hello World  ")` is called
- **THEN** the result is `"hello world"`

#### Scenario: Punctuation stripped
- **WHEN** `_normalize_text("Got it!")` and `_normalize_text("Got it.")` are called
- **THEN** both return `"got it"`

#### Scenario: Whitespace collapsed
- **WHEN** `_normalize_text("hello   world")` is called
- **THEN** the result is `"hello world"`

### Requirement: Cache key construction tests
The test suite SHALL verify that `_build_cache_key` produces correct and deterministic keys.

#### Scenario: Key format
- **WHEN** `_build_cache_key("v1", "cartesia/sonic-3", "voice123", 24000, "hello")` is called
- **THEN** the result matches `tts:v1:cartesia/sonic-3:voice123:24000:{sha1("hello")}`

#### Scenario: Same text different punctuation produces same key
- **WHEN** keys are built for `"Got it."` and `"Got it!"`
- **THEN** both keys are identical

#### Scenario: Different voice produces different key
- **WHEN** keys are built with `voice="abc"` vs `voice="xyz"` for the same text
- **THEN** the keys differ

### Requirement: Msgpack serialization round-trip tests
The test suite SHALL verify that `_serialize_audio` and `_deserialize_audio` round-trip correctly.

#### Scenario: Round-trip preserves all fields
- **WHEN** audio is serialized with known chunks, sample_rate, num_channels, samples_per_channel, and provider, then deserialized
- **THEN** all fields match the originals

#### Scenario: Chunk boundaries preserved
- **WHEN** 3 chunks of different sizes are serialized and deserialized
- **THEN** the deserialized `chunks` list has 3 entries with matching byte content

### Requirement: LocalCacheStore filesystem tests
The test suite SHALL verify `LocalCacheStore` read/write/delete operations using a temp directory.

#### Scenario: Put then get returns same data
- **WHEN** `put(key, data, ttl_days)` is called, then `get(key)`
- **THEN** the returned bytes match the original data

#### Scenario: Get missing key returns None
- **WHEN** `get(key)` is called for a key that was never written
- **THEN** `None` is returned

#### Scenario: Delete removes the file
- **WHEN** `put(key, data, ttl_days)` then `delete(key)` then `get(key)`
- **THEN** `get` returns `None`

#### Scenario: Path derivation from key
- **WHEN** `path_for_key("tts:v1:cartesia/sonic-3:voiceid:24000:abc123")` is called
- **THEN** the path is `{base_dir}/v1/cartesia/sonic-3/voiceid/24000/abc123.msgpack`