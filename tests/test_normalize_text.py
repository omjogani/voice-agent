from cached_tts import _normalize_text


class TestNormalizeText:
    def test_lowercase_and_trim(self):
        assert _normalize_text("  Hello World  ") == "hello world"

    def test_punctuation_stripped(self):
        assert _normalize_text("Got it!") == _normalize_text("Got it.")
        assert _normalize_text("Got it!") == "got it"

    def test_whitespace_collapsed(self):
        assert _normalize_text("hello   world") == "hello world"
