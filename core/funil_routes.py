"""Rotas do painel de funil: `GET /admin/funil` (HTML), `GET /admin/api/funil` (JSON) e
`GET /admin/api/funil/fonte/{nome}` (uma fonte externa por chamada; `core/funil_fontes.py`).

Mesmo controle de acesso do `/admin` POR CONSTRUÇÃO: reusa `get_current_admin` e a
mesma lógica de redirecionamento, sem auth nova e sem mexer no cookie (Path=/admin).
Só GET, sem escrita (por isso sem CSRF). Os nomes privados vêm de `admin_dashboard`
de propósito, para não duplicar a regra de sessão.
"""
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials

from core.admin_dashboard import _bearer, _json_safe, _resolve_admin_username, get_current_admin
from core.funil_dashboard import fetch_funil
from core.funil_fontes import MODULOS, obter

_NO_STORE = {"Cache-Control": "no-store"}


def register_funil_routes(app: FastAPI, frontend_dir: Path, jwt_secret: str) -> None:
    async def _get_current_admin(
        request: Request,
        creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> str:
        return await get_current_admin(jwt_secret, request, creds)

    @app.get("/admin/funil")
    async def serve_funil(request: Request):
        try:
            _resolve_admin_username(jwt_secret, request)
        except HTTPException:
            return RedirectResponse("/admin/login", status_code=303)
        return FileResponse(frontend_dir / "funil.html", headers=_NO_STORE)

    @app.get("/admin/api/funil")
    async def funil_api(_username: str = Depends(_get_current_admin)):
        return JSONResponse(_json_safe(await fetch_funil()), headers=_NO_STORE)

    @app.get("/admin/api/funil/fonte/{nome}")
    async def funil_fonte_api(nome: str, _username: str = Depends(_get_current_admin)):
        if nome not in MODULOS:  # nome vem da URL: só entra o que está no registro fixo
            return JSONResponse({"detail": "Fonte desconhecida"}, status_code=404, headers=_NO_STORE)
        return JSONResponse(_json_safe(await obter(nome)), headers=_NO_STORE)
