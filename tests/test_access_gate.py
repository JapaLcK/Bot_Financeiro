"""
tests/test_access_gate.py — o VEREDITO de acesso (`plan_service.has_app_access`).

Até o corte do Grátis esta função devolvia `True` incondicional com o v2 ligado.
Ela passou a consultar `tem_direito_hoje`: plano pago vigente **OU** carência de
inadimplência aberta. Este arquivo amarra as duas metades daquele OR e, sobretudo,
a **DIREÇÃO** dele.

CONTROLES DECLARADOS (`docs/controles_declarados.md`)
────────────────────────────────────────────────────
**Negativo 1 — o gate existe?** Em `core/services/plan_service.has_app_access`,
troque `return tem_direito_hoje(user)` por `return True`. VERMELHOS:
  `test_sem_direito_nao_entra[free-sem-nada]`
  `test_sem_direito_nao_entra[pago-vencido]`
  `test_sem_direito_nao_entra[carencia-estourada]`
  `test_so_whatsapp_sem_linha_nao_entra`
  `test_linha_em_mao_nao_consulta_o_banco`  (a metade `user=None`)
Direção: falso positivo de acesso — o corte simplesmente não acontece.

**Negativo 2, o que este arquivo existe para ter** — o status virando
AUTORIDADE. Em `core/services/plan_service.tem_direito_hoje`, troque o `return`
por::

    return (_tem_plano_pago_vigente(user)
            and (user.get("last_payment_status") or "") not in PAST_DUE_PAYMENT_STATUSES)

VERMELHOS (medido 2026-09-10; a instrução anterior nomeava só o primeiro):
  `test_pagante_em_retentativa_de_cobranca_continua_entrando`
  `test_carencia_aberta_concede_acesso`
Direção: falso NEGATIVO de acesso, e é o mais caro dos dois — um cliente
PAGANTE, com `plan_expires_at` no futuro, seria barrado por um ciclo inteiro de
smart retry, e o bot mandaria ASSINAR para quem já assinou. É a célula 29 de
`docs/dunning_estados_eventos.md` amarrada por teste; antes deste arquivo
nenhum teste cobria a direção do OR.

Fora deste arquivo a mesma injeção derruba
`tests/test_corte_do_gratis_no_webhook.py::test_carencia_aberta_mantem_o_acesso_ate_a_janela_fechar`
e `tests/test_relatorios_param_no_corte.py::test_carencia_aberta_continua_no_lote`.

**Positivos, e a classificação anterior estava ERRADA**: `test_carencia_aberta_concede_acesso`
era listado aqui como positivo das DUAS injeções, e ele **tem** que cair na 2ª —
ela apaga o lado direito do OR, que é exatamente o que aquele caso mede.
Positivos de verdade (verdes nas duas): `test_pagante_vigente_entra`,
`test_grandfathered_sem_validade_entra`, `test_freio_de_emergencia_devolve_tudo`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.services import plan_service
from core.services.billing_dunning import PAST_DUE_PAYMENT_STATUSES

AGORA = datetime.now(timezone.utc)
FUTURO = AGORA + timedelta(days=10)
PASSADO = AGORA - timedelta(days=10)


def _user(plan="free", expires=None, past_due_since=None, status=None):
    return {
        "plan": plan,
        "plan_expires_at": expires,
        "plan_selected_at": AGORA,
        "past_due_since": past_due_since,
        "last_payment_status": status,
    }


@pytest.fixture(autouse=True)
def _gate_ligado(monkeypatch):
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setenv("ACCESS_GATE_ENABLED", "1")


def _com_linha(monkeypatch, user):
    """Patcha a BUSCA, não o veredito: o caminho medido tem de ser o inteiro."""
    monkeypatch.setattr(plan_service, "get_auth_user", lambda uid: user)


# ── quem NÃO entra ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("user", [
    pytest.param(_user("free"), id="free-sem-nada"),
    pytest.param(_user("pro", PASSADO), id="pago-vencido"),
    # Relógio velho: a carência de 7 dias já fechou, então o lado direito do OR
    # não concede mais nada. Delta ABSOLUTO (21 d), nunca `DUNNING_GRACE_DAYS+n`
    # — escrito em função da constante, alargar a carência moveria o caso junto.
    pytest.param(_user("pro", PASSADO,
                       past_due_since=AGORA - timedelta(days=21),
                       status="past_due"), id="carencia-estourada"),
])
def test_sem_direito_nao_entra(monkeypatch, user):
    _com_linha(monkeypatch, user)
    assert plan_service.has_app_access(1) is False


def test_so_whatsapp_sem_linha_nao_entra(monkeypatch):
    """Sem linha em `auth_accounts` não há direito — é o corte da população
    só-WhatsApp, decisão registrada do dono."""
    _com_linha(monkeypatch, None)
    assert plan_service.has_app_access(1) is False


# ── quem ENTRA ──────────────────────────────────────────────────────────────

def test_pagante_vigente_entra(monkeypatch):
    _com_linha(monkeypatch, _user("pro", FUTURO, status="active"))
    assert plan_service.has_app_access(1) is True


def test_grandfathered_sem_validade_entra(monkeypatch):
    """`plan_expires_at IS NULL` é VITALÍCIO, não "venceu". Quem reescrever o
    predicado como `plan_expires_at > now()` corta a base inteira dos
    vitalícios — e sem este caso o corte passaria verde."""
    _com_linha(monkeypatch, _user("pro", None))
    assert plan_service.has_app_access(1) is True


def test_carencia_aberta_concede_acesso(monkeypatch):
    """O relógio de inadimplência só CONCEDE: plano vencido + ciclo aberto há
    2 dias ainda entra. Prova o lado DIREITO do OR."""
    _com_linha(monkeypatch, _user("pro", PASSADO,
                                  past_due_since=AGORA - timedelta(days=2),
                                  status="past_due"))
    assert plan_service.has_app_access(1) is True


def test_pagante_em_retentativa_de_cobranca_continua_entrando(monkeypatch):
    """A DIREÇÃO do OR, e o caso que só existe por causa dela.

    `plan='pro'` + `plan_expires_at` no FUTURO + `last_payment_status='past_due'`
    é o smart retry normal da Stripe numa conta que continua paga. A autoridade
    é o DIREITO; o status de cobrança não vota. Lido como autoridade, este
    cliente ficaria barrado por um ciclo inteiro de retentativa — célula 29 de
    `docs/dunning_estados_eventos.md`."""
    assert "past_due" in PAST_DUE_PAYMENT_STATUSES, "pré-condição do caso"
    _com_linha(monkeypatch, _user("pro", FUTURO, status="past_due"))
    assert plan_service.has_app_access(1) is True


# ── o freio ─────────────────────────────────────────────────────────────────

def test_freio_de_emergencia_devolve_tudo(monkeypatch):
    """`ACCESS_GATE_ENABLED=0` desfaz o corte inteiro sem redeploy, e nem chega
    a consultar o banco."""
    monkeypatch.setenv("ACCESS_GATE_ENABLED", "0")

    def _explode(uid):
        raise AssertionError("o freio consultou o banco em vez de sair na hora")

    monkeypatch.setattr(plan_service, "get_auth_user", _explode)
    assert plan_service.has_app_access(1) is True


# ── os três estados: "não sei" nunca vira "não tem" ─────────────────────────

def test_erro_de_banco_sobe_em_vez_de_virar_false(monkeypatch):
    """REGRA DURA. `get_auth_user` LEVANTA em erro (não devolve None), e a
    distinção "não sei" × "não tem" é o que um `except: return False` aqui
    destruiria: um soluço de banco barraria a base pagante inteira e o bot
    mandaria ASSINAR para quem já assinou.

    Cada chamador aplica a própria política sobre a exceção, e elas NÃO são
    todas fail-open: o gate do bot e o `gate_plan_selection` engolem; o backstop
    de dados, o WebSocket e o filtro dos relatórios deixam propagar (o backstop
    vira **500**, não 402 — medido). A tabela dos cinco está na docstring de
    `has_app_access`. O que este teste amarra é só o contrato DAQUI: a exceção
    sobe."""
    def _falha(uid):
        raise RuntimeError("pool esgotado")

    monkeypatch.setattr(plan_service, "get_auth_user", _falha)
    with pytest.raises(RuntimeError):
        plan_service.has_app_access(1)


def test_linha_em_mao_nao_consulta_o_banco(monkeypatch):
    """A sentinela `_UNSET`: `user=<linha>` usa a linha, e `user=None` é a
    RESPOSTA "não existe cadastro web" — nunca "não busquei". É o que permite ao
    gate do bot não pagar o `get_auth_user` (decrypt de PII + `pii_access_log`
    por MENSAGEM)."""
    def _explode(uid):
        raise AssertionError("consultou o banco tendo a linha em mão")

    monkeypatch.setattr(plan_service, "get_auth_user", _explode)
    assert plan_service.has_app_access(1, user=_user("pro", FUTURO)) is True
    assert plan_service.has_app_access(1, user=None) is False


def test_allowlist_legada_nao_isenta_do_corte(monkeypatch):
    """`_ACCESS_ALLOWLIST` é da perna LEGADA e não isenta ninguém no v2.

    Era inócuo enquanto `has_app_access` devolvia True incondicional; com o
    corte, essas contas caem como qualquer outra. Decisão registrada: fica
    assim — quem precisa de liberação usa o grant de admin, que é auditado, tem
    validade e aparece no painel. Este teste existe para que reintroduzir a
    lista no caminho v2 seja uma escolha VISÍVEL, não um `or uid in ...` que
    passa despercebido numa revisão.
    """
    uid = next(iter(plan_service._ACCESS_ALLOWLIST))
    _com_linha(monkeypatch, _user("free"))
    assert plan_service.has_app_access(uid) is False

    # E ela continua valendo onde sempre valeu: a perna legada do paywall.
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    monkeypatch.setenv("PAYWALL_ENABLED", "true")
    assert plan_service.has_app_access(uid) is True
