import hashlib

from cached_tts import _build_cache_key


class TestBuildCacheKey:
    def test_key_format(self):
        key = _build_cache_key("v1", "cartesia/sonic-3", "voice123", 24000, "hello")
        sha = hashlib.sha1("hello".encode()).hexdigest()
        assert key == f"tts:v1:cartesia/sonic-3:voice123:24000:{sha}"

    def test_same_text_different_punctuation(self):
        k1 = _build_cache_key("v1", "m", "v", 24000, "Got it.")
        k2 = _build_cache_key("v1", "m", "v", 24000, "Got it!")
        assert k1 == k2

    def test_different_voice_different_key(self):
        k1 = _build_cache_key("v1", "m", "abc", 24000, "hello")
        k2 = _build_cache_key("v1", "m", "xyz", 24000, "hello")
        assert k1 != k2
