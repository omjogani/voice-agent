from cached_tts import _deserialize_audio, _serialize_audio


class TestSerialization:
    def test_round_trip(self):
        chunks = [b"chunk1", b"chunk2", b"chunk3"]
        data = _serialize_audio(
            chunks=chunks,
            sample_rate=24000,
            num_channels=1,
            samples_per_channel=4800,
            provider="cartesia/sonic-3",
        )
        meta = _deserialize_audio(data)
        assert meta["sample_rate"] == 24000
        assert meta["num_channels"] == 1
        assert meta["samples_per_channel"] == 4800
        assert meta["provider"] == "cartesia/sonic-3"
        assert len(meta["chunks"]) == 3
        assert meta["chunks"][0] == b"chunk1"
        assert meta["chunks"][2] == b"chunk3"
        assert "created_at" in meta

    def test_chunk_boundaries_preserved(self):
        chunks = [b"a" * 100, b"b" * 200, b"c" * 50]
        data = _serialize_audio(
            chunks=chunks, sample_rate=24000, num_channels=1,
            samples_per_channel=100, provider="p",
        )
        meta = _deserialize_audio(data)
        assert [len(c) for c in meta["chunks"]] == [100, 200, 50]
