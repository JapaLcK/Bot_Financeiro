"""Guard ASGI da sessão de LEITOR (credencial de visualização).

Roda ANTES de qualquer outro middleware e envolve também os sub-apps montados
(`/api/v2`, `/api/app`). Sem banco: lê a marca assinada `ro` dos tokens da
requisição (Bearer e cookies `dashboard_token`/`auth_token`). Se QUALQUER token
válido a tiver, a requisição é de leitor:

* método fora de GET/HEAD/OPTIONS  -> 403 `somente_leitura` (salvo `ESCRITA_PERMITIDA`);
* GET/HEAD de rota em `NEGADO_LEITOR` -> 403 `somente_leitura`.

A validade da sessão (versão da credencial, revogação) NÃO é checada aqui: é do
resolvedor (`resolve_dashboard_user_id`) e do SQL de `get_active_session`.
O WebSocket é do handler `/ws/{user_id}` (`TIPOS_WS_LEITOR`).
"""
import re

from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse

from frontend.routes.shared import (
    AUTH_COOKIE_NAME,
    DASHBOARD_COOKIE_NAME,
    extract_bearer_token,
    payload_de_sessao,
)

METODOS_SEGUROS = frozenset({"GET", "HEAD", "OPTIONS"})

# Única exceção de escrita: renovar, sair e entrar de novo com a credencial de leitura.
ESCRITA_PERMITIDA = frozenset({
    ("POST", "/auth/refresh"),
    ("POST", "/auth/logout"),
    ("POST", "/auth/leitura/login"),
})

# Tipos de mensagem que o loop do `/ws/{user_id}` trata hoje; todos só leem.
# Qualquer outro tipo fecha o socket do leitor com 4403.
TIPOS_WS_LEITOR = frozenset({"refresh", "get_month", "get_history", "ping"})

# GET que o leitor NÃO faz. Constante única; cada item tem a razão ao lado.
NEGADO_LEITOR = tuple(re.compile(p) for p in (
    # efeito colateral / sessão
    r"^/d/",                              # consome o código do magic link e abre sessão de dono
    r"^/auth/google/",                    # start/callback/pending: emitem sessão e cookie
    r"^/auth/account/export/download/",   # ZIP com TODOS os dados e PII da conta
    r"^/conta$",                          # abre o portal de cobrança da Stripe
    r"^/billing/",                        # assinatura, Pix e planos: cobrança é do dono
    r"^/unsubscribe$",                    # o GET aplica o descadastro
    r"^/retomar",                         # marca clique e planta cookie de comprador
    r"^/r/", r"^/i/", r"^/t/",            # planta cookie de atribuição / carimba clique
    r"^/recurring-bills/[^/]+/?$",        # o GET materializa as contas do próximo ciclo (escreve)
    # sensível / pessoal do dono
    r"^/settings/",                       # sessões, atividade, contato e notificações
    r"^/auth/mfa/",                       # estado do MFA
    r"^/auth/dashboard-profile$",         # perfil/nome do dono
    r"^/api/(v2|app)/perfil$",            # perfil do quiz do dono
    r"^/open-finance/",                   # conexão/consentimento e extrato bruto do banco (contraparte, CPF/CNPJ sem categoria para mascarar)
    r"^/pockets/",                        # histórico da caixinha: nota livre sem categoria para mascarar
    r"^/agents/",                         # feed e Xerife: texto gerado e lançamentos esperados citam nomes; UI de agentes fechada ao leitor
    r"^/api/affiliate/",                  # afiliado: chave Pix e comissões
    r"^/analytics/[^/]+/top-merchants$",  # ranking por contraparte (nome de funcionário/condômino), sem categoria para mascarar
    r"^/(cards|installments)/[^/]+/[^/]+/delete-impact$",  # confirmação de exclusão: o leitor não exclui; traz a nota da compra
    r"^/ai/messages$",                    # histórico da conversa privada do dono com a IA
    r"^/debug/", r"^/admin",              # operação interna
    r"^/api/(v2|app)/familia",            # Plano Família: decisão do dono
    r"^/api/(v2|app)/conta/leitura$",     # a credencial de leitura é gestão do dono
))


def e_leitor(conn: HTTPConnection) -> bool:
    tokens = (extract_bearer_token(conn), conn.cookies.get(DASHBOARD_COOKIE_NAME), conn.cookies.get(AUTH_COOKIE_NAME))
    return any(
        (p := payload_de_sessao((t or "").strip())) and p.get("ro")
        for t in tokens if t
    )


class LeituraGuardMiddleware:
    def __init__(self, app, cabecalhos: dict[str, str] | None = None):
        self.app = app
        self.cabecalhos = cabecalhos or {}  # security headers: o 403 sai fora dos outros middlewares

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not e_leitor(HTTPConnection(scope)):
            return await self.app(scope, receive, send)
        metodo, path = scope["method"], scope["path"]
        if metodo in METODOS_SEGUROS:
            negado = any(p.search(path) for p in NEGADO_LEITOR)
        else:
            negado = (metodo, path) not in ESCRITA_PERMITIDA
        if not negado:
            return await self.app(scope, receive, send)
        resp = JSONResponse({"error": "somente_leitura"}, status_code=403,
                            headers={**self.cabecalhos, "Cache-Control": "no-store"})
        await resp(scope, receive, send)
