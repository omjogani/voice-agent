import os

from livekit.agents import inference, llm, stt, tts

from cache_store import LocalCacheStore
from cached_tts import CachedTTS


def build_stt() -> stt.FallbackAdapter:
    return stt.FallbackAdapter([
        inference.STT.from_model_string("deepgram/nova-3"),
        inference.STT.from_model_string("assemblyai/universal-streaming:en"),
    ])


def build_llm() -> llm.FallbackAdapter:
    return llm.FallbackAdapter([
        inference.LLM.from_model_string("openai/gpt-4.1-mini"),
        inference.LLM.from_model_string("google/gemini-2.5-flash"),
    ])


def build_tts() -> CachedTTS:
    storage_dir = os.getenv("CACHED_TTS_STORAGE_DIR", ".tts_cache")
    return CachedTTS(
        primary=inference.TTS.from_model_string("cartesia/sonic-3:f31cc6a7-c1e8-4764-980c-60a361443dd1"),
        fallbacks=[inference.TTS.from_model_string("inworld/inworld-tts-1")],
        store=LocalCacheStore(storage_dir),
        model_string="cartesia/sonic-3:f31cc6a7-c1e8-4764-980c-60a361443dd1",
    )