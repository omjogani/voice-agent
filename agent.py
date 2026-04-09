import logging
import time

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    AgentStateChangedEvent,
    JobContext,
    MetricsCollectedEvent,
    RoomInputOptions,
    metrics,
)
from livekit.plugins import noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from adapter_factory import build_llm, build_stt, build_tts

load_dotenv()

logger = logging.getLogger(__name__)

ASSISTANT_INSTRUCTIONS = (
    "You are an upbeat, slightly sarcastic voice AI for tech support"
    "Help the caller fix issues without rambling, and keep replies under 3 sentences."
)

class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=ASSISTANT_INSTRUCTIONS)


def build_session() -> AgentSession:
    return AgentSession(
        stt=build_stt(),
        llm=build_llm(),
        tts=build_tts(),
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
        preemptive_generation=True,  # starts thinking alongside the user speaking
    )


def register_metrics(ctx: JobContext, session: AgentSession) -> None:
    usage_collector = metrics.UsageCollector()
    last_eou_metrics: metrics.EOUMetrics | None = None

    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent):
        nonlocal last_eou_metrics
        if ev.metrics.type == "eou_metrics":
            last_eou_metrics = ev.metrics

        metrics.log_metrics(ev.metrics)
        usage_collector.collect(ev.metrics)

    @session.on("agent_state_changed")
    def _on_agent_state_change(ev: AgentStateChangedEvent):
        if ev.new_state == "speaking" and last_eou_metrics:
            elapsed = time.time() - last_eou_metrics.timestamp
            logger.info(f"Time to first audio: {elapsed:.3f}s")

    async def log_usage():
        logger.info("Usage Summary: %s", usage_collector.get_summary())

    ctx.add_shutdown_callback(log_usage)


server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: JobContext):
    session = build_session()
    register_metrics(ctx, session)

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
