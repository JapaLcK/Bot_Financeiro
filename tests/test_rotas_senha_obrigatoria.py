"""Tabela de TODAS as rotas × a perna da senha (PR 4 do funil v3).

Conta paga sem senha e sem Google/Apple (`db.conta_sem_credencial`) não lê nem
grava dado: 403 `password_required` (`shared.exigir_credencial`). Esta tabela é
a única lista de quem bloqueia e quem libera; ela sai do `app.routes` (com o
sub-app da /api/v2 e o /ws), não de memória.

- `bloqueia`: toda rota de dado, e as de vínculo (link-code, push, afiliado);
- `libera`: autenticada, mas é por ela que a pessoa sai do bloqueio (ou exige
  senha por conta própria);
- `publica`: sem sessão de usuário (webhook, login, página pública, admin tem
  sessão própria e fica em `libera`).

Três testes: (1) o conjunto de rotas é o da tabela — rota nova sem linha ou
linha órfã fica vermelha; (2) estrutural, por AST: cada `bloqueia` chama o gate
com a perna (ou `exigir_credencial`/`conta_sem_credencial`), direto ou numa
dependência, e o /contact chama com `exige_credencial=False`; (3) comportamental,
no Postgres: todo GET/DELETE `bloqueia` responde 403 `password_required` para a
conta sem credencial, e as saídas não.

CEGUEIRA DECLARADA: rota com corpo Pydantic obrigatório só tem a prova
estrutural (o 422 vem antes do gate); chamada presente mas DEPOIS de um efeito
colateral não é pega por nenhum dos dois.
"""
import ast
import inspect
import re
import textwrap

import pytest

from test_pix_rota_registrada import _andar_nas_rotas
from test_senha_obrigatoria import CSRF, conta_paga_sem_credencial, env  # noqa: F401 (env é autouse)

B, L, P = "bloqueia", "libera", "publica"
_DADOS = (B, "dado do usuário (authorize_dashboard_access)")
_SAIDA = (L, "saída do bloqueio")

TABELA = {
    # ── WhatsApp e webhooks ──
    ("GET", "/wa/webhook"): (P, "webhook Meta"), ("POST", "/wa/webhook"): (P, "webhook Meta"),
    ("GET", "/webhook"): (P, "webhook Meta"), ("POST", "/webhook"): (P, "webhook Meta"),
    ("POST", "/billing/webhook"): (P, "webhook Stripe"),
    ("POST", "/billing/asaas/webhook"): (P, "webhook Asaas"),
    ("POST", "/open-finance/pluggy/webhook"): (P, "webhook Pluggy"),
    ("POST", "/xquiz/webhook"): (P, "webhook XQuiz"),
    ("POST", "/api/prospect/status"): (P, "lead engine, X-Prospect-Key"),
    # ── /auth ──
    ("GET", "/auth/validate"): _SAIDA, ("GET", "/auth/dashboard-profile"): _SAIDA,
    ("POST", "/auth/register"): (P, "login"), ("POST", "/auth/verify-email"): (P, "login"),
    ("POST", "/auth/login"): (P, "login"), ("POST", "/auth/logout"): _SAIDA,
    ("POST", "/auth/refresh"): _SAIDA, ("POST", "/auth/forgot-password"): (P, "recuperação"),
    ("POST", "/auth/reset-password"): (P, "recuperação: é aqui que a senha nasce"),
    ("POST", "/auth/link-code"): (B, "ligaria o WhatsApp antes de provar o e-mail"),
    ("GET", "/auth/me"): (L, "diz ao front que precisa criar a senha"),
    ("GET", "/auth/mfa/status"): _SAIDA, ("POST", "/auth/mfa/onboarding-seen"): _SAIDA,
    ("POST", "/auth/mfa/setup"): (L, "exige senha (reauth)"),
    ("POST", "/auth/mfa/enable"): (L, "só depois do setup"),
    ("POST", "/auth/mfa/disable"): (L, "exige senha (reauth)"),
    ("POST", "/auth/mfa/regenerate-backup-codes"): (L, "exige senha (reauth)"),
    ("POST", "/auth/mfa/verify-login"): (P, "login"),
    ("POST", "/auth/account/export"): (L, "exige senha → 409"),
    ("GET", "/auth/account/export/download/{token}"): (P, "token de uso único (#687, fora)"),
    ("DELETE", "/auth/account"): (L, "exige senha → 409"),
    ("POST", "/auth/dashboard-token"): _SAIDA, ("POST", "/auth/dashboard-link"): _SAIDA,
    ("GET", "/auth/google/start"): (P, "login"), ("GET", "/auth/google/callback"): (P, "login"),
    ("POST", "/auth/google/exchange"): (P, "login"),
    ("GET", "/auth/google/pending/{token}"): (P, "login"),
    ("POST", "/auth/google/complete-signup"): (P, "login"),
    ("POST", "/auth/apple/exchange"): (P, "login"),
    ("POST", "/auth/apple/complete-signup"): (P, "login"),
    ("POST", "/auth/quiz/resend"): (P, "funil"),
    ("POST", "/auth/quiz/conta"): (L, "cria a conta do quiz"),
    # ── /billing e /conta ──
    ("GET", "/billing/plans-config"): (L, "compra"), ("POST", "/billing/create-checkout"): (L, "compra"),
    ("POST", "/billing/select-free"): (L, "410"),
    ("GET", "/billing/subscription"): (B, "dado da assinatura"),
    ("POST", "/billing/change-plan"): (B, "mexe na assinatura"),
    ("POST", "/billing/cancel-change"): (B, "mexe na assinatura"),
    ("POST", "/billing/portal"): (B, "faturas e cartão no Stripe"),
    ("GET", "/conta"): (B, "atalho do portal → 302 /home"),
    ("POST", "/billing/pix/checkout"): (L, "compra"),
    ("GET", "/billing/pix/{public_token}"): (L, "compra"),
    # ── IA ──
    ("POST", "/ai/chat"): (B, "require_pro_feature"), ("GET", "/ai/messages"): (B, "require_pro_feature"),
    # ── links e utilidades públicas ──
    ("GET", "/d/{code}"): (P, "magic link"), ("GET", "/r/{code}"): (P, "link de afiliado"),
    ("GET", "/i/{code}"): (P, "link de prospect"), ("GET", "/wa"): (P, "abre o WhatsApp"),
    ("POST", "/contact"): (P, "formulário"), ("GET", "/health"): (P, "health"),
    ("GET", "/unsubscribe"): (P, "descadastro"), ("POST", "/unsubscribe"): (P, "descadastro"),
    ("GET", "/api/commands-catalog"): (P, "catálogo"), ("GET", "/api/blog/news"): (P, "blog"),
    # ── afiliados, push, onboarding ──
    ("GET", "/api/affiliate/me"): (B, "decisão do dono"), ("POST", "/api/affiliate/payout"): (B, "decisão do dono"),
    ("POST", "/api/push/register"): (B, "decisão do dono"),
    ("POST", "/api/push/unregister"): (L, "baixa, não vínculo"),
    ("GET", "/onboarding/state"): (L, "estado do wizard"), ("POST", "/onboarding/state"): (L, "estado do wizard"),
    # ── /settings ──
    ("POST", "/settings/reset"): (L, "exige senha (reset_user_data)"),
    ("GET", "/settings/{user_id}/security"): _SAIDA,
    ("PATCH", "/settings/{user_id}/security/contact"): (L, "corrigir o e-mail é a saída"),
    ("POST", "/settings/{user_id}/password-reset"): (L, "manda o link de criar a senha"),
    ("GET", "/settings/{user_id}/activity"): _DADOS,
    ("GET", "/settings/{user_id}/sessions"): _SAIDA,
    ("DELETE", "/settings/{user_id}/sessions/{jti}"): _SAIDA,
    ("DELETE", "/settings/{user_id}/sessions"): _SAIDA,
    ("GET", "/settings/{user_id}/notifications"): _DADOS,
    ("PATCH", "/settings/{user_id}/notifications"): _DADOS,
    # ── /api/v2 e WS ──
    ("GET", "/api/v2/me"): (B, "usuario_atual"), ("GET", "/api/v2/eventos"): (B, "usuario_atual"),
    ("GET", "/api/v2/assinaturas"): (B, "usuario_atual"),
    ("POST", "/api/v2/assinaturas/marca"): (B, "usuario_atual"),
    ("GET", "/api/v2/perfil"): (B, "usuario_atual"), ("PUT", "/api/v2/perfil"): (B, "usuario_atual"),
    ("GET", "/api/v2/contas"): (B, "usuario_atual"),
    ("GET", "/api/v2/resumo-do-mes"): (B, "usuario_atual"),
    ("WS", "/ws/{user_id}"): (B, "close 4403"),
    # ── HTML autenticado ──
    ("GET", "/app"): (L, "casca; o overlay sobe"), ("GET", "/home"): (L, "casca; o overlay sobe"),
    ("GET", "/settings"): (L, "saída; sem overlay"),
    ("GET", "/painel"): (L, "resíduo declarado"), ("GET", "/onboarding"): (L, "resíduo declarado"),
    ("GET", "/changelog"): (L, "conteúdo público do Pro"), ("GET", "/blog/{slug}"): (L, "conteúdo"),
}

_HTML_PUBLICO = (
    "/", "/login", "/cadastro", "/q", "/recuperar-senha", "/suporte/contato", "/privacy",
    "/termos", "/blog", "/whatsapp", "/funcionalidades", "/comandos", "/comandos-app",
    "/agents", "/como-funciona", "/precos", "/continuar-compra", "/suporte",
    "/reset-password", "/redefinir-senha", "/completar-cadastro",
    "/.well-known/apple-app-site-association",
)
TABELA.update({("GET", p): (P, "página pública") for p in _HTML_PUBLICO})

# Rotas de dados: todas as que passam por `authorize_dashboard_access` inline.
_ROTAS_DE_DADOS = """
GET /data/{user_id}|GET /history/{user_id}|GET /expenses/daily/{user_id}|POST /launches/{user_id}
PATCH /launches/{user_id}/{launch_id}|DELETE /launches/{user_id}/{launch_id}
PATCH /credit-transactions/{user_id}/{tx_id}|DELETE /credit-transactions/{user_id}/{tx_id}
POST /pockets/{user_id}|PATCH /pockets/{user_id}/{pocket_id}/meta|GET /goals/{user_id}/status
DELETE /pockets/{user_id}/{pocket_name:path}|POST /pockets/{user_id}/{pocket_name:path}/deposit
POST /pockets/{user_id}/{pocket_name:path}/withdraw|GET /pockets/{user_id}/{pocket_name:path}/history
POST /cards/{user_id}|GET /cards/{user_id}/summary|PATCH /cards/{user_id}/reorder
PATCH /cards/{user_id}/{card_id}|GET /cards/{user_id}/{card_id}/delete-impact|DELETE /cards/{user_id}/{card_id}
GET /installments/{user_id}/list|GET /installments/{user_id}/{group_id}/delete-impact
POST /installments/{user_id}/{group_id}/anticipate|DELETE /installments/{user_id}/{group_id}
PATCH /installments/{user_id}/{group_id}|GET /bills/{user_id}|GET /bills/{user_id}/{bill_id}
POST /bills/{user_id}/{bill_id}/pay|GET /analytics/{user_id}/kpis|GET /analytics/{user_id}/evolution
GET /analytics/{user_id}/categories|GET /analytics/{user_id}/weekday-pattern
GET /analytics/{user_id}/top-merchants|GET /insights/{user_id}/current|GET /analytics/{user_id}/patterns
GET /agents/{user_id}|POST /agents/{user_id}/{kind}/activate|POST /agents/{user_id}/{kind}/pause
POST /agents/{user_id}/{kind}/email|GET /agents/{user_id}/feed|POST /agents/{user_id}/feed/seen
POST /agents/{user_id}/{kind}/chat|GET /categories/{user_id}/launches|POST /simulator/{user_id}
GET /debug/ai/{user_id}/payload|GET /history/{user_id}/list|GET /history/{user_id}/quick-stats
POST /ofx/import/{user_id}|POST /export/{user_id}|GET /budgets/{user_id}|POST /budgets/{user_id}
DELETE /budgets/{user_id}/{categoria}|GET /budgets/{user_id}/status|GET /household-budget/{user_id}/status
PUT /household-budget/{user_id}/config|PUT /household-budget/{user_id}/income|GET /categories/{user_id}
POST /categories/{user_id}|PATCH /categories/{user_id}/{cat_id}|POST /categories/{user_id}/{cat_id}/archive
POST /categories/{user_id}/{cat_id}/unarchive|DELETE /categories/{user_id}/{cat_id}
GET /account/{user_id}/setup-status|POST /account/{user_id}/initial-balance
POST /account/{user_id}/adjust-balance|GET /recurring-bills/{user_id}
POST /recurring-bills/{user_id}/{bill_id}/pay|GET /recurring-bills/{user_id}/projection|GET /forecast/{user_id}
POST /recurring-bills/{user_id}|PATCH /recurring-bills/{user_id}/{bill_id}|DELETE /recurring-bills/{user_id}/{bill_id}
GET /recurring-expenses/{user_id}|POST /recurring-expenses/{user_id}|PATCH /recurring-expenses/{user_id}/{rec_id}
POST /recurring-expenses/{user_id}/charges/{charge_id}/ack|DELETE /recurring-expenses/{user_id}/{rec_id}
GET /recurring-incomes/{user_id}|POST /recurring-incomes/{user_id}|PATCH /recurring-incomes/{user_id}/{inc_id}
POST /recurring-incomes/{user_id}/credits/{credit_id}/ack|DELETE /recurring-incomes/{user_id}/{inc_id}
GET /investments/{user_id}/rates|POST /investments/{user_id}|POST /investments/{user_id}/deposit
POST /investments/{user_id}/withdraw|DELETE /investments/{user_id}/{name:path}
GET /open-finance/{user_id}|GET /open-finance/{user_id}/connectors|GET /open-finance/{user_id}/caixinhas
POST /open-finance/{user_id}/caixinhas/bind|POST /open-finance/{user_id}/connect-token
POST /open-finance/{user_id}/pluggy-item|POST /open-finance/{user_id}/sync|POST /open-finance/{user_id}/refresh
POST /open-finance/{user_id}/mock-connect|DELETE /open-finance/{user_id}|GET /open-finance/{user_id}/movements
POST /open-finance/{user_id}/movements/confirm|GET /open-finance/{user_id}/reconciliations
POST /open-finance/{user_id}/reconciliations/{of_tx_id}/{action}
GET /open-finance/{user_id}/cash-transfers|POST /open-finance/{user_id}/cash-transfers/{link_id}/{action}
"""
for _linha in re.split(r"[|\n]", _ROTAS_DE_DADOS.strip()):
    _m, _p = _linha.split(" ", 1)
    TABELA[(_m, _p)] = _DADOS

_ADMIN = (L, "admin: sessão própria (_get_current_admin)")
_SUFIXOS_DE_ASSET = (".js", ".css", ".png", ".json", ".txt", ".xml", ".html")


def _eh_asset(path: str) -> bool:
    return path.endswith(_SUFIXOS_DE_ASSET) or path.startswith(("/fonts/", "/brand/"))


def _classe(metodo: str, path: str):
    if (metodo, path) in TABELA:
        return TABELA[(metodo, path)]
    if path.startswith("/admin"):
        return _ADMIN
    if metodo == "GET" and _eh_asset(path):
        return (P, "asset")
    return None


def _rotas():
    """(método, path, rota) de todo o app, com o /api/v2 e o /ws."""
    import frontend.finance_bot_websocket_custom as dashboard
    for path, rota in _andar_nas_rotas(dashboard.app.routes, com_rota=True):
        if not getattr(rota, "endpoint", None):
            continue  # o Mount da /api/v2: as filhas vêm pela descida
        metodos = getattr(rota, "methods", None) or {"WS"}
        for m in sorted(metodos - {"HEAD"}):
            yield m, path, rota


# ── 1: a tabela cobre o app, e só o app ─────────────────────────────────────

def test_toda_rota_tem_linha_e_nenhuma_linha_e_orfa():
    vistas = {(m, p) for m, p, _ in _rotas()}
    sem_linha = sorted(k for k in vistas if _classe(*k) is None)
    orfas = sorted(set(TABELA) - vistas)
    assert not sem_linha, f"rota nova sem classificação na TABELA: {sem_linha}"
    assert not orfas, f"linha da TABELA sem rota: {orfas}"
    assert ("WS", "/ws/{user_id}") in vistas and ("GET", "/api/v2/me") in vistas, "a descida quebrou"


# ── 2: estrutural, por AST ──────────────────────────────────────────────────

_GATES = {"authorize_dashboard_access", "_authorize_dashboard_access", "_enforce_subscription_gate"}
_DIRETAS = {"exigir_credencial", "_exigir_credencial", "conta_sem_credencial"}
_ISENTOS = ("/billing", "/auth", "/conta")  # shared._GATE_EXEMPT_PREFIXES: o gate retorna antes


def _chamadas(fn):
    """(nome, {kwarg: valor literal}) de cada Call no corpo de `fn`."""
    arvore = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    for no in ast.walk(arvore):
        if isinstance(no, ast.Call):
            f = no.func
            nome = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
            kws = {k.arg: getattr(k.value, "value", None) for k in no.keywords}
            yield nome, kws
            for a in no.args:  # `asyncio.to_thread(_exigir_credencial, uid)`
                ref = a.id if isinstance(a, ast.Name) else getattr(a, "attr", None)
                if ref in _DIRETAS:
                    yield ref, {}


def _dependentes(dep):
    yield dep
    for sub in dep.dependencies:
        yield from _dependentes(sub)


def _funcoes_da_rota(rota):
    yield rota.endpoint
    dep = getattr(rota, "dependant", None)
    for d in (_dependentes(dep) if dep else ()):
        if inspect.isroutine(d.call) and d.call is not rota.endpoint:
            yield d.call  # instância (HTTPBearer) não tem fonte a ler


def _prova(path: str, rota) -> bool:
    for fn in _funcoes_da_rota(rota):
        for nome, kws in _chamadas(fn):
            if nome in _DIRETAS:
                return True
            if (nome in _GATES and not path.startswith(_ISENTOS)
                    and kws.get("exige_credencial") is not False and kws.get("exige_direito") is not False):
                return True
    return False


def test_cada_rota_que_bloqueia_tem_a_perna_da_senha():
    sem_prova = sorted(f"{m} {p}" for m, p, r in _rotas()
                       if (_classe(m, p) or (None,))[0] == B and not _prova(p, r))
    assert not sem_prova, f"rota 'bloqueia' sem a perna da senha: {sem_prova}"


def test_o_gate_central_e_quem_o_repassa_carregam_a_perna():
    """Sem isto, tirar o `exigir_credencial` de dentro do gate deixaria o teste
    acima verde: as rotas continuam chamando o gate, que não checa mais nada."""
    from frontend.routes import shared
    assert "exigir_credencial" in {n for n, _ in _chamadas(shared._enforce_subscription_gate)}
    repasse = [k for n, k in _chamadas(shared.authorize_dashboard_access) if n == "_enforce_subscription_gate"]
    assert repasse and all("exige_credencial" in k for k in repasse), repasse


def test_o_contact_pula_so_a_perna_da_senha():
    rota = next(r for m, p, r in _rotas() if (m, p) == ("PATCH", "/settings/{user_id}/security/contact"))
    chamadas = [k for n, k in _chamadas(rota.endpoint) if n == "authorize_dashboard_access"]
    assert chamadas == [{"exige_credencial": False}], chamadas


# ── 3: comportamental, no Postgres ──────────────────────────────────────────

def _url(path: str, uid: int) -> str:
    return re.sub(r"\{[^}]+\}", lambda m: str(uid) if m.group(0) == "{user_id}" else "1", path)


def _codigo(r) -> str | None:
    try:
        corpo = r.json()
    except ValueError:
        return None
    detalhe = corpo.get("detail") if isinstance(corpo, dict) else None
    if isinstance(detalhe, dict):
        return detalhe.get("error")
    erro = corpo.get("error") if isinstance(corpo, dict) else None  # envelope da /api/v2
    return erro.get("code") if isinstance(erro, dict) else None


def _query_obrigatoria(rota) -> dict:
    """Valor de mentira para cada query obrigatória: sem ele o 422 vem antes do gate."""
    import datetime as _dt
    return {q.alias: ("2026-01-01" if q.field_info.annotation is _dt.date else "1")
            for d in _dependentes(rota.dependant) for q in d.query_params if q.field_info.is_required()}


def _sem_corpo_obrigatorio(rota) -> bool:
    return not any(f.field_info.is_required() for f in rota.dependant.body_params)


def test_toda_leitura_e_exclusao_que_bloqueia_responde_password_required(monkeypatch):
    uid, _, client = conta_paga_sem_credencial()
    monkeypatch.setattr("core.services.plan_service.dashboard_v2_enabled", lambda *a, **k: True)
    monkeypatch.setenv("DEBUG_AI_ROUTES", "1")  # desligada, a rota de debug dá 404 antes do gate
    alvos = sorted(((m, p, r) for m, p, r in _rotas()
                    if m in ("GET", "DELETE") and (_classe(m, p) or (None,))[0] == B
                    and p != "/conta" and _sem_corpo_obrigatorio(r)), key=lambda t: t[:2])
    assert len(alvos) > 40, alvos  # a varredura achou as rotas de dados
    errados = []
    for m, p, rota in alvos:
        r = client.request(m, _url(p, uid), params=_query_obrigatoria(rota), headers={"x-csrf-token": CSRF})
        if r.status_code != 403 or _codigo(r) != "password_required":
            errados.append((m, p, r.status_code, r.text[:120]))
    assert not errados, errados


@pytest.mark.parametrize("metodo,path", [
    ("GET", "/auth/me"), ("GET", "/auth/validate"), ("GET", "/settings/{user_id}/security"),
    ("GET", "/settings/{user_id}/sessions"), ("POST", "/auth/dashboard-token"),
    ("POST", "/auth/refresh"), ("POST", "/auth/logout"),
])
def test_as_saidas_nao_respondem_password_required(metodo, path):
    uid, _, client = conta_paga_sem_credencial()
    r = client.request(metodo, _url(path, uid), headers={"x-csrf-token": CSRF})
    assert r.status_code < 400 and _codigo(r) != "password_required", (r.status_code, r.text[:200])
