"""Namespace explícito do produto móvel, independente da coorte beta web.

O prefixo é público: não concede privilégio por User-Agent ou header do cliente.
As rotas V2 continuam com sua chave beta original, e as do app reaproveitam os
mesmos contratos, motores e guardas por substituição da dependência de sessão.
"""
from fastapi import HTTPException, Request

from api.v2.app import criar_app
from api.v2.sessao import usuario_assinante, usuario_atual
from db.open_finance_onboarding import get_open_finance_onboarding

from . import mes_detalhes, patrimonio, rendimento


def usuario_do_app(request: Request) -> int:
    uid = usuario_assinante(request)
    if not get_open_finance_onboarding(uid)["completed"]:
        raise HTTPException(status_code=403,
                            detail={"error": "open_finance_onboarding_required"})
    return uid


# O SSE web revalida a chave beta diretamente durante o stream. O app nativo
# revalida por foco/foreground e não oferece esse contrato de eventos web.
app = criar_app("/api/app", com_eventos=False)
app.dependency_overrides[usuario_atual] = usuario_do_app
for modulo in (patrimonio, rendimento, mes_detalhes):
    app.include_router(modulo.router)
