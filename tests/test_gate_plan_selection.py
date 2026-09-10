"""Gate server-side de escolha de plano (frontend/routes/shared.gate_plan_selection).

Enforcement REAL do funil de cadastro: antes de servir o HTML do dashboard
(home/app/settings), o servidor manda pra /precos quem ainda não escolheu um
plano. Os redirects em JS são só UX; este gate é o que não dá pra burlar.

Convenção de monkeypatch (ver docstring de shared): patchar
`frontend.routes.shared.<nome>` e `core.services.plan_service.needs_plan_selection`.
"""

import pytest
from fastapi import HTTPException
from fastapi.responses import RedirectResponse

import core.services.plan_service as plan_service
import frontend.routes.shared as shared


class _Req:
    """Request falso: só o que gate_plan_selection/_resolve_page_user_id tocam."""
    def __init__(self, ua="", query=None):
        self.headers = {"user-agent": ua}
        self.cookies = {}
        # dict tem .get() como o QueryParams real — suficiente pro gate.
        self.query_params = query or {}


def _patch(monkeypatch, *, token="tok", payload=None, needs=True, session=None,
           dashboard_uid=None, acesso=True):
    monkeypatch.setattr(shared, "get_auth_token_from_request", lambda req, creds: token)
    monkeypatch.setattr(shared, "decode_jwt", lambda t: payload)
    monkeypatch.setattr(shared, "get_active_session", lambda jti: session)
    monkeypatch.setattr(plan_service, "needs_plan_selection", lambda uid: needs)
    # `acesso` é a SEGUNDA perna do gate, que chegou com o corte do Grátis. O
    # default True preserva a intenção dos casos que já existiam ("o que se mede
    # aqui é a escolha de plano"); quem mede o corte passa acesso=False.
    monkeypatch.setattr(plan_service, "has_app_access", lambda uid: acesso)

    # Fallback do dashboard_token (12h): resolve o user OU levanta 401.
    def _fake_dash(req):
        if dashboard_uid is None:
            raise HTTPException(status_code=401, detail="x")
        return dashboard_uid
    monkeypatch.setattr(shared, "resolve_dashboard_user_id", _fake_dash)


def test_cadastro_sem_plano_redireciona_pra_precos(monkeypatch):
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=True)
    out = shared.gate_plan_selection(_Req())
    assert isinstance(out, RedirectResponse)
    assert out.headers["location"] == "/precos?escolha=1"


def test_plano_escolhido_serve_a_pagina(monkeypatch):
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=False)
    assert shared.gate_plan_selection(_Req()) is None


def test_deslogado_nao_forca_precos(monkeypatch):
    # Sem token válido em nenhum cookie: deixa o HTML carregar (ele vai pro login).
    _patch(monkeypatch, token=None, payload=None, needs=True, dashboard_uid=None)
    assert shared.gate_plan_selection(_Req()) is None


def test_retorno_do_checkout_sucesso_nao_forca_precos(monkeypatch):
    # ?upgrade=success = webhook em trânsito; quem acabou de pagar NÃO pode ser
    # jogado pra /precos antes do mark_plan_selected do webhook cair. A tela de
    # confirmação em /home espera e libera (fail-open).
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=True)
    assert shared.gate_plan_selection(_Req(query={"upgrade": "success"})) is None
    # Sem o param, o gate segue redirecionando quem não escolheu plano
    assert isinstance(shared.gate_plan_selection(_Req()), RedirectResponse)


def test_retorno_cancelado_ainda_forca_precos(monkeypatch):
    # ?upgrade=cancelled (abandono) NÃO é exceção — segue pro /precos escolher.
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=True)
    out = shared.gate_plan_selection(_Req(query={"upgrade": "cancelled"}))
    assert isinstance(out, RedirectResponse)
    assert out.headers["location"] == "/precos?escolha=1"


def test_ua_de_app_nao_isenta_o_gate(monkeypatch):
    """Mandar "PigBankApp" no User-Agent NÃO pula o gate de escolha de plano.

    A isenção existia pela diretriz 3.1.1 da App Store e era decidida por
    substring de header — escolhido pelo cliente. Qualquer conta web entrava no
    dashboard sem plano só forjando o UA, e depois que o Grátis saiu da /precos
    isso passou a valer dinheiro. Controle negativo: repor
    `if _is_pigbank_app(request): return None` no topo de gate_plan_selection
    faz este caso voltar a receber None.
    """
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=True)
    out = shared.gate_plan_selection(_Req(ua="Mozilla/5.0 PigBankApp/1.2"))
    assert isinstance(out, RedirectResponse)
    assert out.headers["location"] == "/precos?escolha=1"


def test_ua_de_app_nao_impede_quem_ja_escolheu(monkeypatch):
    # Controle positivo do par acima: sem o gate pendente, o UA de app segue
    # entrando normalmente. Sem isto, o teste anterior passaria num gate que
    # recusa todo mundo.
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=False)
    assert shared.gate_plan_selection(_Req(ua="Mozilla/5.0 PigBankApp/1.2")) is None


def test_dashboard_token_vale_mesmo_com_access_expirado(monkeypatch):
    # auth_token expirado (decode_jwt → None) mas dashboard_token (12h) válido:
    # tem que resolver o user e AINDA aplicar o gate (bug pego pelo Codex).
    _patch(monkeypatch, token=None, payload=None, needs=True, dashboard_uid=7)
    out = shared.gate_plan_selection(_Req())
    assert isinstance(out, RedirectResponse)
    assert out.headers["location"] == "/precos?escolha=1"


def test_jti_invalido_cai_no_dashboard_token(monkeypatch):
    # auth_token com jti mas sessão revogada → tenta o dashboard_token; se ele
    # também não vale, trata como deslogado (serve, sem forçar /precos).
    _patch(monkeypatch, payload={"type": "auth", "sub": "7", "jti": "x"},
           needs=True, session=None, dashboard_uid=None)
    assert shared.gate_plan_selection(_Req()) is None


# ── Detecção da origem do cadastro (signup_source_from_request) ──────────────

def test_signup_source_web_por_padrao():
    assert shared.signup_source_from_request(_Req(ua="Mozilla/5.0")) == "web"


def test_signup_source_app_pela_ua():
    assert shared.signup_source_from_request(
        _Req(ua="Mozilla/5.0 PigBankApp/1.2")) == "app"


def test_signup_source_google_web_e_app():
    assert shared.signup_source_from_request(
        _Req(ua="Mozilla/5.0"), google=True) == "google"
    assert shared.signup_source_from_request(
        _Req(ua="PigBankApp/1.0"), google=True) == "google_app"


# ── A janela entre as escritas do webhook ────────────────────────────────────
# O webhook de checkout.session.completed grava em passos separados
# (finance_bot_websocket_custom.py ~4245): update_user_plan/set_payment_status
# primeiro, mark_plan_selected depois. A pergunta é se existe uma janela em que
# o usuário já tem plano pago e o gate AINDA barra — que faria a tela de
# "confirmando pagamento" fechar cedo e o /data devolver 402.
#
# Não existe, e o motivo é estrutural: needs_plan_selection NÃO lê uma flag
# independente. Ele deriva das MESMAS colunas que update_user_plan acabou de
# escrever (plan + plan_expires_at), e trata assinatura paga vigente como
# escolha implícita — antes de olhar plan_selected_at. Os testes abaixo fixam
# esse invariante nos dois sentidos.

class TestJanelaEntreEscritasDoWebhook:
    @pytest.fixture(autouse=True)
    def _v2_ligado(self, monkeypatch):
        monkeypatch.setattr(plan_service, "plans_v2_enabled", lambda: True)

    @staticmethod
    def _user(plan, *, plan_selected_at, expires="2099-01-01T00:00:00+00:00"):
        from datetime import datetime
        return {
            "plan": plan,
            "plan_selected_at": plan_selected_at,
            "plan_expires_at": datetime.fromisoformat(expires) if expires else None,
        }

    def test_plano_pago_gravado_e_plan_selected_ainda_nao(self):
        """A janela exata que preocupa: update_user_plan já commitou, o
        mark_plan_selected ainda não. O gate NÃO barra — logo não há 402."""
        u = self._user("plus", plan_selected_at=None)
        assert plan_service.needs_plan_selection(1, u) is False

    def test_mesmo_sem_plan_selected_o_trial_ja_vale(self):
        """Trial nasce como assinatura vigente do plano escolhido: mesma
        janela, mesmo resultado."""
        u = self._user("pro", plan_selected_at=None)
        assert plan_service.needs_plan_selection(1, u) is False

    def test_cadastro_novo_sem_plano_nenhum_continua_barrado(self):
        """O outro sentido: sem o passo do webhook, o gate tem de barrar —
        senão este teste passaria por vacuidade."""
        u = self._user("free", plan_selected_at=None, expires=None)
        assert plan_service.needs_plan_selection(1, u) is True

    def test_plano_pago_expirado_nao_conta_como_escolha(self):
        """Assinatura vencida não é escolha implícita: volta a barrar."""
        u = self._user("plus", plan_selected_at=None, expires="2020-01-01T00:00:00+00:00")
        assert plan_service.needs_plan_selection(1, u) is True


class _DataReq:
    """Request falso pro _enforce_subscription_gate: ele lê url.path e o UA."""
    def __init__(self, ua="", path="/data/7"):
        self.headers = {"user-agent": ua}

        class _U:
            pass
        self.url = _U()
        self.url.path = path


@pytest.mark.parametrize("ua", ["Mozilla/5.0", "Mozilla/5.0 PigBankApp/1.2"])
def test_backstop_402_nao_isenta_ua_de_app(monkeypatch, ua):
    """O 402 das rotas de dados nega com e sem UA de app.

    Era o terceiro ponto que isentava o app por header (`not
    _is_pigbank_app(request) and needs_plan_selection(...)`): sem o gate de
    página, uma conta sem plano batia direto em /data/{id} forjando o UA e lia
    os dados. Controle negativo: repor o `not _is_pigbank_app(request) and`
    faz o caso do UA de app parar de levantar.
    """
    monkeypatch.setattr(plan_service, "needs_plan_selection", lambda uid: True)
    monkeypatch.setattr(plan_service, "has_app_access", lambda uid: True)
    with pytest.raises(HTTPException) as exc:
        shared._enforce_subscription_gate(_DataReq(ua=ua), 7)
    assert exc.value.status_code == 402
    assert exc.value.detail["error"] == "plan_selection_required"


@pytest.mark.parametrize("ua", ["Mozilla/5.0", "Mozilla/5.0 PigBankApp/1.2"])
def test_backstop_402_libera_quem_ja_escolheu(monkeypatch, ua):
    # Controle positivo do par acima, nos dois UAs: sem gate pendente e com
    # acesso, a rota passa. Sem isto o par passaria num backstop que nega tudo.
    monkeypatch.setattr(plan_service, "needs_plan_selection", lambda uid: False)
    monkeypatch.setattr(plan_service, "has_app_access", lambda uid: True)
    assert shared._enforce_subscription_gate(_DataReq(ua=ua), 7) is None


def test_backstop_402_ainda_isenta_as_rotas_que_resolvem_o_gate(monkeypatch):
    # /billing e /auth continuam isentos por PREFIXO (não por UA): são eles que
    # fecham o gate. Gatear isto trancaria a saída.
    monkeypatch.setattr(plan_service, "needs_plan_selection", lambda uid: True)
    monkeypatch.setattr(plan_service, "has_app_access", lambda uid: True)
    assert shared._enforce_subscription_gate(
        _DataReq(path="/billing/create-checkout"), 7) is None


# ── a perna do CORTE DO GRÁTIS ──────────────────────────────────────────────
#
# CONTROLES DECLARADOS (`docs/controles_declarados.md`)
# Negativo: em `frontend/routes/shared.gate_plan_selection`, troque
# `has_app_access(user_id)` por `True` dentro do `if`. VERMELHO:
#   `test_sem_direito_vigente_tambem_vai_pra_precos`
# Direção: falso positivo de acesso — o HTML do dashboard é servido a quem foi
# cortado, e este gate é o ÚNICO enforcement de HTML ("o enforcement REAL").
#
# **NÃO use a variante "apague o bloco do `if`"**, e a proibição é medida: o
# `except` daqui é FAIL-OPEN e devolve `None`, então apagar texto serve a página
# por OUTRO motivo (a expressão quebrada cai no `except`) e o leitor conclui o
# oposto do que o controle afirma — é a patologia "injeção que apaga texto" do
# `docs/controles_declarados.md`. Troque o VALOR, não apague o pedaço.
#
# Positivos (VERDES sob a injeção acima): `test_pagante_recebe_a_pagina`,
# `test_deslogado_nao_forca_precos`, `test_upgrade_success_continua_isento`.


def test_sem_direito_vigente_tambem_vai_pra_precos(monkeypatch):
    """Já escolheu plano (needs=False) mas não tem direito hoje: cortado.

    Mesmo destino da outra perna, de propósito — quem separa as duas mensagens
    é o `/auth/me` na própria /precos, nunca o marcador da URL."""
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=False, acesso=False)
    out = shared.gate_plan_selection(_Req())
    assert isinstance(out, RedirectResponse)
    assert out.headers["location"] == "/precos?escolha=1"


def test_pagante_recebe_a_pagina(monkeypatch):
    """POSITIVO: sem gate pendente e com direito vigente, o HTML é servido.
    Sem ele o grupo passaria num gate que recusa todo mundo."""
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=False, acesso=True)
    assert shared.gate_plan_selection(_Req()) is None


def test_upgrade_success_continua_isento(monkeypatch):
    """POSITIVO: quem ACABOU de pagar não pode ser jogado de volta pra /precos
    enquanto o webhook está em trânsito — a isenção roda ANTES das duas pernas."""
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=True, acesso=False)
    assert shared.gate_plan_selection(_Req(query={"upgrade": "success"})) is None


def test_erro_no_veredito_de_acesso_serve_a_pagina(monkeypatch):
    """"Não sei" é fail-open AQUI: `has_app_access` LEVANTA em vez de devolver
    False, e este gate engole. Um soluço de banco não pode trancar a base
    pagante fora do próprio produto — o backstop de dados (402) e o redirect em
    JS seguem valendo como rede de segurança."""
    def _falha(uid):
        raise RuntimeError("pool esgotado")

    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=False)
    monkeypatch.setattr(plan_service, "has_app_access", _falha)
    assert shared.gate_plan_selection(_Req()) is None


# ── /settings é a SAÍDA DE EMERGÊNCIA: isenta da perna do DIREITO ────────────
#
# Decisão do dono. Medido: `grep -rln "auth/account" frontend/` acha
# `settings.html` e mais nada — a UI de exportar os dados e excluir a conta mora
# só ali. Os endpoints `/auth/*` continuam isentos por prefixo, mas sem a página
# não sobra porta para alcançá-los.
#
# CONTROLE DECLARADO (`docs/controles_declarados.md`) — troque o VALOR, não
# apague o bloco: em `frontend/routes/static_pages.serve_settings`, troque
# `gate_plan_selection(request, exige_direito=False)` por
# `gate_plan_selection(request)`. VERMELHO:
#   `test_settings_nao_tranca_quem_perdeu_o_direito`
# Direção: falso NEGATIVO de acesso — quem foi cortado perde a única porta para
# exportar os dados e excluir a própria conta.
#
# Positivo do PAR, e ele fica VERDE sob essa injeção (é o que o torna positivo):
#   `test_settings_ainda_barra_quem_nunca_escolheu_plano`


def test_settings_nao_tranca_quem_perdeu_o_direito(monkeypatch):
    """Cortado alcança /settings: 200 com o HTML, não 302 pra /precos."""
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=False, acesso=False)
    assert shared.gate_plan_selection(_Req(), exige_direito=False) is None


def test_settings_ainda_barra_quem_nunca_escolheu_plano(monkeypatch):
    """A perna da ESCOLHA sobrevive à isenção — ela não foi desligada junto.

    Sem este caso, `exige_direito=False` poderia ter aberto a página inteira e o
    teste de cima ficaria verde do mesmo jeito."""
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=True, acesso=True)
    out = shared.gate_plan_selection(_Req(), exige_direito=False)
    assert isinstance(out, RedirectResponse)
    assert out.headers["location"] == "/precos?escolha=1"


def test_isencao_do_direito_nem_consulta_o_veredito(monkeypatch):
    """`exige_direito=False` não paga o SELECT do direito — a perna nem roda."""
    def _explode(uid):
        raise AssertionError("consultou o direito numa rota isenta dele")

    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=False)
    monkeypatch.setattr(plan_service, "has_app_access", _explode)
    assert shared.gate_plan_selection(_Req(), exige_direito=False) is None


def test_as_outras_paginas_continuam_exigindo_o_direito(monkeypatch):
    """A isenção é de UMA rota, não do gate. O default é `exige_direito=True`, e
    é ele que /app, /home e /onboarding usam."""
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=False, acesso=False)
    out = shared.gate_plan_selection(_Req())
    assert isinstance(out, RedirectResponse)


def test_so_o_settings_e_isento_do_direito():
    """Quem é isento é UMA rota — enumerado no arquivo, não confiado à memória.

    Uma segunda rota com `exige_direito=False` abriria o corte por uma porta que
    ninguém declarou. Cita o PREDICADO (`exige_direito=False`), que é a coisa
    que a isenção É — nome local e forma de bloco envelhecem
    (`docs/controles_declarados.md`).
    """
    import re
    from pathlib import Path

    fonte = (Path(__file__).resolve().parent.parent
             / "frontend" / "routes" / "static_pages.py").read_text(encoding="utf-8")
    isentos = re.findall(r"async def serve_(\w+)\(request[^)]*\):(?:(?!async def).)*?"
                         r"gate_plan_selection\(request,\s*exige_direito=False\)",
                         fonte, re.S)
    assert isentos == ["settings"], isentos
