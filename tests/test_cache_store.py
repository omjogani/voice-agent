from cache_store import LocalCacheStore

KEY = "tts:v1:cartesia/sonic-3:voiceid:24000:abc123"
DATA = b"fake-audio-data"


async def test_put_then_get(tmp_path):
    store = LocalCacheStore(str(tmp_path))
    await store.put(KEY, DATA, ttl_days=30)
    result = await store.get(KEY)
    assert result == DATA


async def test_get_missing_returns_none(tmp_path):
    store = LocalCacheStore(str(tmp_path))
    result = await store.get(KEY)
    assert result is None


async def test_delete_removes_file(tmp_path):
    store = LocalCacheStore(str(tmp_path))
    await store.put(KEY, DATA, ttl_days=30)
    await store.delete(KEY)
    result = await store.get(KEY)
    assert result is None


async def test_path_for_key(tmp_path):
    store = LocalCacheStore(str(tmp_path))
    path = store.path_for_key(KEY)
    expected_suffix = "v1/cartesia/sonic-3/voiceid/24000/abc123.msgpack"
    assert path.endswith(expected_suffix)
    assert path.startswith(str(tmp_path))
