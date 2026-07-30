"""
mock_dashboard.py — harness LOCAL pra ver o dashboard sem backend/prod.

Serve frontend/ e finge as respostas mínimas de auth pra furar o gate,
para conferir marca/fonte/recolor/empty-states com ZERO acesso ao banco.
Os cards de saldo vêm por WebSocket no app real — aqui o WS só aceita a
conexão (chrome renderiza; dados ficam vazios). Estender com dados fake
quando precisar validar os gráficos.

Uso:  python scripts/mock_dashboard.py      → http://localhost:8010/
Nada aqui toca .env, banco ou a app real.
"""
from __future__ import annotations

import mimetypes
import pathlib

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect

FRONTEND = pathlib.Path(__file__).resolve().parent.parent / "frontend"
app = FastAPI()


@app.get("/auth/validate")
async def validate():
    return {"user_id": 1}


@app.get("/auth/me")
async def me():
    return {"app_access": True, "plan": "pro", "is_pro": True,
            "last_payment_status": "active"}


@app.get("/auth/dashboard-profile")
async def profile():
    return {"user_id": 1, "plan": "pro", "display_name": "Piggy Preview",
            "is_affiliate": False}


@app.websocket("/ws/{uid}")
async def ws(sock: WebSocket, uid: str):
    await sock.accept()
    try:
        while True:
            await sock.receive_text()
    except WebSocketDisconnect:
        return


def _dashboard_html_cachebusted() -> str:
    """Injeta ?v=<mtime> nos assets pra o navegador nunca servir JS/CSS velho."""
    import re
    html = (FRONTEND / "dashboard.html").read_text(encoding="utf-8")
    assets = ("dashboard.js", "dashboard.css", "dashboard-mobile.css",
              "brand.css", "modals.js", "dashboard-chat.js")
    for a in assets:
        try:
            v = int((FRONTEND / a).stat().st_mtime)
        except OSError:
            continue
        html = re.sub(rf'(/{re.escape(a)})(["\?])', rf'\1?v={v}\2', html)
    return html


@app.api_route("/{full_path:path}", methods=["GET", "POST"])
async def catch_all(full_path: str):
    if not full_path or full_path in ("app", "dashboard", "dashboard.html"):
        from fastapi.responses import HTMLResponse
        return HTMLResponse(_dashboard_html_cachebusted(),
                            headers={"Cache-Control": "no-store"})
    candidate = (FRONTEND / full_path).resolve()
    if FRONTEND.resolve() in candidate.parents and candidate.is_file():
        media = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        return FileResponse(candidate, media_type=media,
                            headers={"Cache-Control": "no-store"})
    return JSONResponse({"data": [], "items": []})


if __name__ == "__main__":
    print("Mock dashboard: http://localhost:8010/  (fonte/marca/recolor — sem prod)")
    uvicorn.run(app, host="127.0.0.1", port=8010, log_level="warning")
