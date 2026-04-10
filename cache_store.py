from __future__ import annotations

import os
from typing import Protocol

import aiofiles
import aiofiles.os


class CacheStore(Protocol):
    async def get(self, key: str) -> bytes | None: ...
    async def put(self, key: str, data: bytes, ttl_days: int) -> None: ...
    async def delete(self, key: str) -> None: ...
    def path_for_key(self, key: str) -> str: ...


class LocalCacheStore:
    """Stores cached audio as msgpack files on the local filesystem.

    Key format expected: ``tts:{ver}:{model}:{voice}:{sr}:{hash}``
    Maps to: ``{base_dir}/{ver}/{model}/{voice}/{sr}/{hash}.msgpack``
    """

    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir

    def path_for_key(self, key: str) -> str:
        # key = tts:{ver}:{model}:{voice}:{sr}:{hash}
        parts = key.split(":")
        # parts[0] = "tts", parts[1] = ver, parts[2] = model (provider/name),
        # parts[3] = voice, parts[4] = sr, parts[5] = hash
        ver = parts[1]
        model = parts[2]  # e.g. "cartesia/sonic-3"
        voice = parts[3]
        sr = parts[4]
        sha = parts[5]
        return os.path.join(self._base_dir, ver, model, voice, sr, f"{sha}.msgpack")

    async def get(self, key: str) -> bytes | None:
        path = self.path_for_key(key)
        try:
            async with aiofiles.open(path, "rb") as f:
                return await f.read()
        except FileNotFoundError:
            return None

    async def put(self, key: str, data: bytes, ttl_days: int) -> None:
        path = self.path_for_key(key)
        await aiofiles.os.makedirs(os.path.dirname(path), exist_ok=True)
        async with aiofiles.open(path, "wb") as f:
            await f.write(data)

    async def delete(self, key: str) -> None:
        path = self.path_for_key(key)
        try:
            await aiofiles.os.remove(path)
        except FileNotFoundError:
            pass