from livekit.agents import inference, llm, stt, tts


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


def build_tts() -> tts.FallbackAdapter:
    return tts.FallbackAdapter([
        inference.TTS.from_model_string("cartesia/sonic-3:f31cc6a7-c1e8-4764-980c-60a361443dd1"),
        inference.TTS.from_model_string("inworld/inworld-tts-1"),
    ])