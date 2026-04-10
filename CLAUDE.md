# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Package manager: **uv** (Python >=3.13). A `.env` with `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` is required — see `.env.example`.

- Install deps: `uv sync`
- Run agent in dev (hot reload, connects to LiveKit Cloud): `uv run agent.py dev`
- Run agent in console mode (local mic/speaker, no LiveKit room): `uv run agent.py console`
- Download model weights (Silero VAD, turn detector) once before first run: `uv run agent.py download-files`

The `dev` / `console` / `download-files` subcommands come from `livekit.agents.cli.run_app` — see `livekit-agents` docs for the full list.

## Architecture

This is a single-file LiveKit voice agent split into two modules:

- **`agent.py`** — wires up the `AgentServer` and defines the `entrypoint` decorated with `@server.rtc_session()`. Each job builds a fresh `AgentSession` via `build_session()`, which composes STT + LLM + TTS + Silero VAD + `MultilingualModel` turn detection, with `preemptive_generation=True` so the LLM starts generating while the user is still speaking. `register_metrics` attaches two listeners: one collects usage via `metrics.UsageCollector` (logged on shutdown), the other measures time-to-first-audio by diffing `AgentStateChangedEvent` ("speaking") against the last `EOUMetrics` timestamp. `BVC` noise cancellation is applied on `RoomInputOptions`.

- **`adapter_factory.py`** — isolates model selection. Each of `build_stt` / `build_llm` / `build_tts` returns a `FallbackAdapter` built from `inference.{STT,LLM,TTS}.from_model_string(...)` calls inline. Current chain: Deepgram nova-3 → AssemblyAI (STT), GPT-4.1-mini → Gemini 2.5 Flash (LLM), Cartesia Sonic-3 → Inworld (TTS). **Keep the inline `from_model_string` style** — do not refactor into constant lists + comprehensions.

The `Assistant` class only carries the system prompt (`ASSISTANT_INSTRUCTIONS`); all runtime behavior lives in the session composition.