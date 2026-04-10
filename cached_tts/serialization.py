from __future__ import annotations

import time
from typing import Any

import msgpack


def _serialize_audio(
    chunks: list[bytes],
    sample_rate: int,
    num_channels: int,
    samples_per_channel: int,
    provider: str,
) -> bytes:
    return msgpack.packb({
        "sample_rate": sample_rate,
        "num_channels": num_channels,
        "samples_per_channel": samples_per_channel,
        "chunks": chunks,
        "provider": provider,
        "created_at": time.time(),
    })


def _deserialize_audio(data: bytes) -> dict[str, Any]:
    return msgpack.unpackb(data, raw=False)