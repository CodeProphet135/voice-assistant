from pathlib import Path

from fastapi import FastAPI, WebSocket
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


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    # Imported inside the function to avoid any risk of a circular import
    # between session.py's agent/protocol imports and this module.
    from voice_assistant.session import Session

    await websocket.accept()
    session = Session(websocket)
    await session.run()


# NOTE: this route MUST be declared before the static mount below — FastAPI
# matches explicit routes before falling through to a mount, but keeping the
# ordering explicit here avoids ever re-shadowing /ws if the mount changes.

_static_dir = Path(__file__).parent.parent.parent / "static"
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")
