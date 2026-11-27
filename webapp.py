"""FastAPI WebApp control server for Monster Galaxy Auto-Farm."""

from contextlib import asynccontextmanager
import os

from fastapi import Body, FastAPI, Response
from fastapi.responses import FileResponse
import uvicorn

from engine import FarmEngine

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML_PATH = os.path.join(PROJECT_ROOT, "web", "index.html")

_engine: FarmEngine | None = None


def get_engine() -> FarmEngine:
    global _engine
    if _engine is None:
        _engine = FarmEngine()
    return _engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_engine()
    try:
        yield
    finally:
        if _engine is not None:
            _engine.shutdown()


app = FastAPI(title="Monster Galaxy Auto-Farm", lifespan=lifespan)


@app.get("/")
def index():
    return FileResponse(INDEX_HTML_PATH)


@app.get("/api/status")
def api_status():
    return get_engine().get_status()


@app.get("/api/preview.jpg")
def api_preview():
    jpeg = get_engine().get_preview_jpeg()
    if jpeg is None:
        return Response(status_code=204)
    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/browser/open")
def api_browser_open():
    get_engine().open_browser()
    return {"ok": True}


@app.post("/api/browser/close")
def api_browser_close():
    get_engine().close_browser()
    return {"ok": True}


@app.post("/api/bot/start")
def api_bot_start():
    get_engine().start()
    return {"ok": True}


@app.post("/api/bot/pause")
def api_bot_pause():
    get_engine().pause()
    return {"ok": True}


@app.post("/api/bot/stop")
def api_bot_stop():
    get_engine().stop()
    return {"ok": True}


@app.post("/api/preview/refresh")
def api_preview_refresh():
    get_engine().refresh_preview()
    return {"ok": True}


@app.post("/api/alert/dismiss")
def api_alert_dismiss():
    get_engine().dismiss_legendary_alert()
    return {"ok": True}


@app.post("/api/settings")
def api_settings(payload: dict = Body(...)):
    saved = get_engine().update_settings(payload)
    return {"ok": True, "settings": saved}


if __name__ == "__main__":
    print("Monster Galaxy Auto-Farm WebApp running at http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000)
