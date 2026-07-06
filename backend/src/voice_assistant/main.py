from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

from voice_assistant.telemetry import configure_telemetry

configure_telemetry()

app = FastAPI(title="Voice Assistant")

FastAPIInstrumentor.instrument_app(app)
HTTPXClientInstrumentor().instrument()


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


# The WebSocket endpoint (/ws) is added in Phase 1.

_static_dir = Path(__file__).parent.parent.parent / "static"
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")
