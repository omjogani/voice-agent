import logging

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RoomInputOptions,
    AgentServer,
)
from livekit.plugins import noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.agents import llm, stt, tts, inference
from livekit.agents import AgentStateChangedEvent, MetricsCollectedEvent, metrics
import time

logger = logging.getLogger(__name__)

load_dotenv()


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=("You are an upbeat, slightly sarcastic voice AI for tech support"
                          "Help the caller fix issues without rambling, and keep replies under 3 sentences."),
        )


server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: JobContext):
    session = AgentSession(
        stt=stt.FallbackAdapter([
            inference.STT.from_model_string("deepgram/nova-3"),
            inference.STT.from_model_string("assemblyai/universal-streaming:en"),
        ]),
        llm=llm.FallbackAdapter([
            inference.LLM.from_model_string("openai/gpt-4.1-mini"),
            inference.LLM.from_model_string("google/gemini-2.5-flash"),
        ]),
        tts=tts.FallbackAdapter([
            inference.TTS.from_model_string("cartesia/sonic-3:f31cc6a7-c1e8-4764-980c-60a361443dd1"),
            inference.TTS.from_model_string("inworld/inworld-tts-1")
        ]),
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
        preemptive_generation=True,  # starts thinking alongside the user speaking
    )

    usage_collector = metrics.UsageCollector()
    last_eou_metrics: metrics.EOUMetrics | None = None

    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent):
        nonlocal last_eou_metrics
        if ev.metrics.type == "eou_metrics":
            last_eou_metrics = ev.metrics

        metrics.log_metrics(ev.metrics)
        usage_collector.collect(ev.metrics)

    async def log_usage():
        summary = usage_collector.get_summary()
        logger.info("Usage Summary: %s", summary)

    ctx.add_shutdown_callback(log_usage)

    @session.on("agent_state_changed")
    def _on_agent_state_change(ev: AgentStateChangedEvent):
        if ev.new_state == "speaking":
            if last_eou_metrics:
                elapsed = time.time() - last_eou_metrics.timestamp
                logger.info(f"Time to first audio: {elapsed:.3f}s")

    await session.start(
        agent=Assistant(),
        room=ctx.room,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    await ctx.connect()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    agents.cli.run_app(server)
