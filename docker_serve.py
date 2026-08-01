"""Dashboard ASGI app: the real Popper API plus the prebuilt React GUI on one port.

The GUI (gui/dist) fetches relative /api/... URLs, so API and static files must share
an origin. The API app registers its /api/* routes first; mounting StaticFiles at "/"
last makes it the fallback for everything else and serves index.html at "/".

ponytail: this thin wrapper exists only so we don't have to run a second proxy or a
node dev server in the container. If the SPA ever needs deep-link/history routing,
swap the StaticFiles mount for a catch-all that returns index.html.

Serve with:  uvicorn docker_serve:app --host 0.0.0.0 --port 8000
Rebuild gui/dist first if it's stale:  cd gui && npm install && npm run build
"""
from pathlib import Path

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from popper.api.main import app

_DIST = Path(__file__).resolve().parent / "gui" / "dist"
if _DIST.is_dir():
    @app.get("/reset-password", include_in_schema=False)
    def reset_password_page():
        return FileResponse(_DIST / "index.html")

    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="gui")
else:  # API still works; UI just won't render until you build gui/dist.
    print(f"[docker_serve] WARNING: {_DIST} missing — API only. Run `npm run build` in gui/.")
