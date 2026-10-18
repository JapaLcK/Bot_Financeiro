"""Tela do dono para ler o 👍/👎 do chat do Piggy: `GET /admin/feedback` (HTML) e
`GET /admin/api/feedback` (JSON).

Mesmo controle de acesso do `/admin` e do funil, por construção (`get_current_admin`,
sem auth nova). Só GET, sem escrita. A saída identifica a mensagem só por data e id:
nunca `user_id`, nome ou e-mail. Cada página de 👎 (conteúdo de mensagens de usuários) vai para
`pii_access_log`, uma linha por usuário; o resumo semanal é agregado e NÃO é auditado, e com
`PII_AUDIT_DISABLED=1` nada é auditado.
"""
import asyncio
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials
from psycopg import connect as _conecta

import core.admin_dashboard as admin_dashboard
from core.admin_dashboard import _bearer, _json_safe, _resolve_admin_username, get_current_admin
from core.crypto import PiiAccessContext, _ctx_to_row
from core.system_event_log import statement_timeout_options
from db.ai_feedback import MOTIVOS, listar_negativos, resumo_semanal

_NO_STORE = {"Cache-Control": "no-store"}


class AuditoriaFalhou(Exception):
    """A leitura entre usuários não pôde ser registrada em `pii_access_log`."""


def _audita(ctxs: list[PiiAccessContext]) -> None:
    """Grava a auditoria da página ANTES de ela sair: FALHA FECHADA, ao contrário de
    `core.crypto._record_access`/`pii_audit_batch` (que engolem o erro, e seguem como
    estão para os outros chamadores). Atômico (uma transação): se qualquer linha falhar (FK de
    usuário apagado no meio, por exemplo), nenhuma é gravada e a rota dá 503.
    `PII_AUDIT_DISABLED=1` continua desligando a auditoria: política global do ambiente,
    a mesma do resto do código (a suíte roda assim). O formato da linha é o de `_ctx_to_row`."""
    if not ctxs or os.getenv("PII_AUDIT_DISABLED", "").strip() in {"1", "true", "True"}:
        return
    try:
        # Conexão própria, como `log_system_event`: `statement_timeout` de
        # `statement_timeout_options()` (env SYSTEM_EVENT_LOG_TIMEOUT_MS) e connect de 2 s; estourar vira 503.
        with _conecta(os.environ["DATABASE_URL"], connect_timeout=2,
                      options=statement_timeout_options()) as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    "insert into pii_access_log (purpose, actor, subject_user_id, field, endpoint, extra)"
                    " values (%s, %s, %s, %s, %s, %s)",
                    [_ctx_to_row(c) for c in ctxs],
                )
            conn.commit()
    except Exception as exc:
        raise AuditoriaFalhou(type(exc).__name__) from exc


def _coleta(limit: int, offset: int, username: str) -> dict:
    # O resumo é da tela inteira, não de cada página: "Carregar mais" (offset > 0) não o pede.
    resumo = resumo_semanal() if offset == 0 else None
    pagina = listar_negativos(limit, offset)
    ids_por_usuario: dict[int, list[int]] = {}
    for it in pagina["itens"]:
        ids_por_usuario.setdefault(it.pop("user_id"), []).append(it["id"])
    _audita([
        PiiAccessContext(
            purpose="render_admin_ai_feedback", actor=f"admin:{username}",
            subject_user_id=uid, field="ai_messages", endpoint="/admin/api/feedback",
            extra={"message_ids": ids},
        ) for uid, ids in ids_por_usuario.items()
    ])
    return {"motivos": MOTIVOS, "semanas": resumo, **pagina}


def register_feedback_routes(app: FastAPI, frontend_dir: Path, jwt_secret: str) -> None:
    async def _get_current_admin(
        request: Request,
        creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> str:
        return await get_current_admin(jwt_secret, request, creds)

    @app.get("/admin/feedback")
    async def serve_feedback(request: Request):
        try:
            _resolve_admin_username(jwt_secret, request)
        except HTTPException:
            return RedirectResponse("/admin/login", status_code=303)
        return FileResponse(frontend_dir / "admin-feedback.html", headers=_NO_STORE)

    @app.get("/admin/api/feedback")
    async def feedback_api(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0, le=1_000_000),  # além disso o bigint do Postgres estoura (500)
        username: str = Depends(_get_current_admin),
    ):
        try:
            dados = await asyncio.to_thread(_coleta, limit, offset, username)
        except AuditoriaFalhou as exc:  # sem auditoria, sem dados
            await admin_dashboard.log_system_event(
                "error", "admin_feedback_auditoria", "Leitura do feedback sem auditoria: 503",
                source="admin_feedback", details={"erro": str(exc), "limit": limit, "offset": offset},
            )
            return JSONResponse({"detail": "Auditoria indisponível. Tente de novo."},
                                status_code=503, headers=_NO_STORE)
        return JSONResponse(_json_safe(dados), headers=_NO_STORE)
