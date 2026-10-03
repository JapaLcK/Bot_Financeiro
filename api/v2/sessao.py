"""A dependência única que entrega o usuário a toda rota da /api/v2.

Nenhuma rota da v2 recebe `user_id` de fora: ele sai daqui, da sessão (Bearer
ou cookie `dashboard_token`). O user agent não entra — ele escolhe tela, nunca
concede nem nega. `tests/test_api_v2_rotas.py` reprova rota sem esta
dependência ou com parâmetro de usuário.
"""
from fastapi import HTTPException, Request

from core.services.plan_service import dashboard_v2_enabled
from frontend.routes.shared import (
    _enforce_subscription_gate,
    raise_if_account_scheduled_for_deletion,
    resolve_dashboard_user_id,
)


def usuario_atual(request: Request) -> int:
    # `def` e não `async def`: tudo abaixo é banco síncrono, e o FastAPI roda
    # dependência síncrona no threadpool em vez de travar o event loop.
    uid = resolve_dashboard_user_id(request)
    raise_if_account_scheduled_for_deletion(uid)
    _enforce_subscription_gate(request, uid)
    if not dashboard_v2_enabled(uid):
        # 404 no padrão de `_require_agents_beta`: fora da lista, a v2 não existe.
        raise HTTPException(status_code=404, detail={"error": "dashboard_v2_disabled"})
    return uid
