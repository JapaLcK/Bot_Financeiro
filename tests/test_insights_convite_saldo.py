"""PL-06, convite "Informe seu saldo" (`wallet_balance_unknown`), PR 1: plano em `.time-dev/pl06/plano-convite.md`.

Decisões do dono (2026-11-13): confirmar zero ou o mesmo valor conta como informado e grava
`accounts.saldo_confirmado_em`, sem lançamento (P9 b); o convite só sai quando a previsão do mês a partir do zero fecha
negativa (P11 b); dispensado, volta no mês seguinte (P12 a); textos da P14; fora do e-mail (info). Postgres real; relógio
do detector em 20/10/2026 10:00, menos na conversa (C13), que roda no relógio real.

Controles negativos (mutação numa cópia da árvore; o log mora no relato do PR): sem o ramo `if not informou` → C1
(critical de R$ 30); convite antes do `fecha >= 0` → C2; sem `marcar_saldo_confirmado` no `adjust-balance` → C6, C12;
no `initial-balance` → C7, C9; reset sem limpar a marca → C8; apagar tudo sem limpar → C17; limpar depois do laço → C19; 2ª condição de `saldo_informado` sem `user_id = %s` → C9;
sem o tipo em `RECURSO_POR_TIPO` → C10; `info` no envio por e-mail → C16; leitura sem o filtro de dispensados →
C11, C11b; mês da chave em UTC → C11b. Positivos: C5, C6/C7 (nenhuma linha nova no
extrato), C10 (Plus e Pro legado veem), C16 (o warning da mesma cena vai).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from decimal import Decimal as D

import psycopg
import pytest

import db
from _cards_apoio import _http
from _ia_stream_helpers import executar, openai_falso, rodada
from conftest import promote_to_pro, usuario_pagante
from core import ai_patterns
from core.services import insights_detectores as detectores
from core.services.insights_contrato import periodo_mes
from core.services.insights_notificacao import a_enviar
from db.carteira_qualidade import saldo_informado
from db.privacy import reset_user_data
from db.recurring import create_recurring_expense
from test_api_app_sessao import completo
from test_insights_contrato import _usuario, llm  # noqa: F401
from test_insights_estado import _put
from test_insights_month_projection import AGOSTO, ANTIGO, _aluguel, _diga, _post, _relogio
from tests._patrimonio_helpers import conexao, conta, horas_atras
from utils_date import _tz, day_tz, now_tz

TITULO = "Quanto você tem na Carteira hoje?"
TEXTO = ("Informe o saldo de hoje da Carteira para melhorar a previsão do mês. Sem esse saldo, a previsão usa zero "
         "como ponto de partida.")
CHAVE = "wallet_balance_unknown:carteira:2026-10"


def _feed(uid):
    """O que o usuário recebe: recalcula e lê pela rota."""
    ai_patterns.generate_ai_insights(uid, force=True)
    r = _http(uid).get(f"/insights/{uid}/current")
    assert r.status_code == 200, r.text
    return [i for i in r.json()["insights"] if i["type"] in ("wallet_balance_unknown", "month_projection")]


def _tipos(uid):
    return [(i["type"], i["severity"]) for i in _feed(uid)]


def _lancamentos(uid):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from launches where user_id = %s", (uid,))
        return cur.fetchone()["n"]


def _informou(uid):
    with db.get_conn() as conn, conn.cursor() as cur:
        return saldo_informado(cur, uid)


def _ana(monkeypatch, plano="pro"):
    """Plus (v2 `pro`) que nunca informou o saldo: gastou 30 pelo WhatsApp e tem o aluguel de 1.000."""
    _relogio(monkeypatch)
    uid = _usuario(plano)
    assert "30" in _diga(uid, "gastei 30 na padaria")
    _aluguel(uid)
    return uid


# ── C1 a C4: quando o convite sai ────────────────────────────────────────────────

def test_c1_quem_nunca_informou_recebe_o_convite_e_nao_o_alarme(llm, monkeypatch):
    uid = _ana(monkeypatch)
    (it,) = _feed(uid)
    assert (it["key"], it["type"], it["severity"], it["icon"]) == (CHAVE, "wallet_balance_unknown", "info", "💰")
    assert (it["title"], it["message"]) == (TITULO, TEXTO)
    assert (it["action_label"], it["action_view"]) == ("Informar saldo", "carteira")
    e = it["explicacao"]
    assert (e["tipo"], e["familia"], e["fonte"]["base"], e["periodo"]["inicio"]) == (
        "wallet_balance_unknown", "projecao", "previsao", "2026-10-01")
    assert (e["referencia"], e["atual"], e["diferenca"], e["amostra"]) == (
        {"estatistica": "saldo_nao_informado"}, {}, None, None)
    assert not {"impacto", "acoes"} & set(it)
    assert llm == []


def test_c2_quem_fecha_o_mes_positivo_a_partir_do_zero_nao_recebe(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro")
    db.add_launch_and_update_balance(uid, "receita", 2000, "Salário", None, "salario", ANTIGO)
    db.add_launch_and_update_balance(uid, "despesa", 30, "Padaria", None, "padaria", ANTIGO)
    _aluguel(uid)  # 1.970 − 1.000 = +970
    assert _tipos(uid) == []


def test_c3_com_banco_conectado_nao_recebe(llm, monkeypatch):
    uid = _ana(monkeypatch)
    conta(conexao(uid, "item-convite", sync=horas_atras(1)), "acc-convite", D("10"))
    assert _tipos(uid) == []


def test_c4_quem_importou_extrato_nao_recebe(llm, monkeypatch):
    """Informar não destravaria: o lançamento importado não é verificável (`carteira_nao_confirmada`)."""
    from ofx_import import import_ofx_bytes
    from test_category_normalization import _OFX_BANCO
    _relogio(monkeypatch)
    uid = _usuario("pro")
    import_ofx_bytes(uid, _OFX_BANCO.format(acct=uid % 100000).encode(), "extrato.ofx")
    _aluguel(uid)
    assert _tipos(uid) == []


# ── C5 a C8: informar faz o convite sumir; zero e "o mesmo valor" contam (P9) ─────

def test_c5_ajustar_o_saldo_troca_o_convite_pelo_aviso(llm, monkeypatch):
    uid = _ana(monkeypatch)
    assert _tipos(uid) == [("wallet_balance_unknown", "info")]
    assert _post(uid, "adjust-balance", {"target_balance": 300})["delta"] == 330
    (it,) = _feed(uid)
    assert (it["type"], it["severity"], it["explicacao"]["atual"]["valor"]) == ("month_projection", "warning", -700.0)


@pytest.mark.parametrize("rota,corpo", [("adjust-balance", {"target_balance": 0}),  # C6
                                        ("initial-balance", {"amount": 0})])        # C7
def test_c6_c7_confirmar_zero_conta_como_informado_sem_lancamento(llm, monkeypatch, rota, corpo):
    _relogio(monkeypatch)
    uid = _usuario("pro")  # Carla: só o aluguel, Carteira 0
    _aluguel(uid)
    assert _tipos(uid) == [("wallet_balance_unknown", "info")]
    r = _post(uid, rota, corpo)
    assert r["launch_id"] is None and r.get("delta", 0.0) == 0.0
    assert _lancamentos(uid) == 0 and db.get_balance(uid) == 0  # positivo: o extrato não ganha linha
    assert _informou(uid) is True
    (it,) = _feed(uid)
    assert (it["type"], it["severity"], it["explicacao"]["atual"]["valor"]) == ("month_projection", "warning", -1000.0)


def test_c8_recomecar_do_zero_apaga_a_marca(llm, monkeypatch):
    from db.users import _hash_password
    _relogio(monkeypatch)
    uid = _usuario("pro")
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update auth_accounts set password_hash = %s where user_id = %s", (_hash_password("senha-c8"), uid))
    _post(uid, "initial-balance", {"amount": 0})
    assert _informou(uid) is True
    reset_user_data(uid, "senha-c8")
    assert _informou(uid) is False
    assert "30" in _diga(uid, "gastei 30 na padaria")
    _aluguel(uid)
    assert _tipos(uid) == [("wallet_balance_unknown", "info")]  # sem o critical de R$ 30


def _apagar_tudo(uid):
    """O "apagar todos os lançamentos" do chat: pendência armada e o "sim", que executa a tool antes do LLM."""
    from core.services.ai_chat import chat
    assert db.ai_set_pending_action(uid, "delete_all_launches", {}, "apagar TODOS os seus lançamentos")
    return chat(uid, "sim", monthly_limit=1000, platform="whatsapp")


def test_c17_apagar_tudo_pelo_chat_apaga_a_marca(llm, monkeypatch):
    """Confirmou 0, informou 500 e apagou tudo: a Carteira volta a 0 sem saldo informado, como a de quem só lançou."""
    _relogio(monkeypatch)
    a, b = _usuario("pro"), _usuario("pro")
    for uid in (a, b):
        _post(uid, "initial-balance", {"amount": 0})
    _post(a, "adjust-balance", {"target_balance": 500})
    assert "apaguei" in _apagar_tudo(a).lower()
    assert db.get_balance(a) == 0 and _informou(a) is False
    assert _informou(b) is True  # isolamento: a marca de B fica
    assert "30" in _diga(a, "gastei 30 na padaria")
    _aluguel(a)
    assert _tipos(a) == [("wallet_balance_unknown", "info")]  # sem o critical de R$ 30
    _post(a, "adjust-balance", {"target_balance": -30})  # positivo: confirmar depois volta a marcar
    assert _informou(a) is True and _tipos(a) == [("month_projection", "critical")]


def test_c18_apagar_tudo_de_quem_nunca_informou_nao_marca(llm, monkeypatch):
    uid = _ana(monkeypatch)
    assert "apaguei" in _apagar_tudo(uid).lower()
    assert _informou(uid) is False and _tipos(uid) == [("wallet_balance_unknown", "info")]


class _Queda(BaseException):
    """O processo caindo no meio do laço: `BaseException` passa pelo `except Exception` de cada lançamento."""


def test_c19_queda_no_meio_do_apagar_tudo_nao_deixa_a_marca_velha(llm, monkeypatch):
    """A marca sai ANTES do laço: parar no meio devolve o convite, e não o critical de uma confirmação que já não vale.
    Pela tool do chat (`_apagar_tudo`): a queda atravessa o runner. Positivo: C17, sem a queda."""
    from db import accounts as accounts_mod
    uid = _usuario("pro")
    _post(uid, "initial-balance", {"amount": 0})
    for v in (10, 20, 30):
        db.add_launch_and_update_balance(uid, "despesa", v, "padaria", None, "padaria", ANTIGO)
    assert _informou(uid) is True

    real, chamadas = accounts_mod.delete_launch_and_rollback, []

    def cai_no_segundo(u, lid, **kw):
        chamadas.append(lid)
        if len(chamadas) == 2:
            raise _Queda()
        return real(u, lid, **kw)

    monkeypatch.setattr(accounts_mod, "delete_launch_and_rollback", cai_no_segundo)
    with pytest.raises(_Queda):
        _apagar_tudo(uid)
    assert len(chamadas) == 2 and _lancamentos(uid) == 2  # parou mesmo no meio: 1 apagado, 2 ficaram
    assert _informou(uid) is False


# ── C9 a C12: isolamento, planos, dispensa, aviso de escrita ─────────────────────

def test_c9_a_marca_e_de_cada_usuario(llm, monkeypatch):
    _relogio(monkeypatch)
    a, b = _usuario("pro"), _usuario("pro")
    _post(a, "initial-balance", {"amount": 0})
    for uid in (a, b):
        _aluguel(uid)
    assert _tipos(a) == [("month_projection", "warning")] and _tipos(b) == [("wallet_balance_unknown", "info")]


def test_c10_planos_na_geracao_e_na_leitura(llm, monkeypatch):
    _relogio(monkeypatch)
    essencial = _usuario("essencial")
    assert _http(essencial).get(f"/insights/{essencial}/current").status_code == 403
    assert _tipos(_ana(monkeypatch, "pro")) == [("wallet_balance_unknown", "info")]  # positivo: Plus
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    gratis, pro = promote_to_pro(usuario_pagante("pro"), "free"), completo(usuario_pagante("pro"))
    for uid in (gratis, pro):
        _aluguel(uid)
    assert [i["type"] for i in ai_patterns.generate_ai_insights(gratis, force=True)] == []
    assert [i["type"] for i in ai_patterns.generate_ai_insights(pro, force=True)] == ["wallet_balance_unknown"]
    monkeypatch.setattr("db.compute_active_insights", lambda *a: pytest.fail("recalculou"))
    promote_to_pro(pro, "free")  # cache quente: some na leitura
    assert ai_patterns.ler_insights(pro) == []


def test_c11_dispensado_some_no_mes_e_volta_no_seguinte(llm, monkeypatch):
    _relogio(monkeypatch)
    uid = _usuario("pro")
    _aluguel(uid)
    assert [i["key"] for i in _feed(uid)] == [CHAVE]
    assert _put(uid, chave=CHAVE, dispensado=True, severidade="info").status_code == 200
    assert _tipos(uid) == []
    _relogio(monkeypatch, datetime(2026, 11, 1, 10, tzinfo=_tz()))
    assert [i["key"] for i in _feed(uid)] == ["wallet_balance_unknown:carteira:2026-11"]


def test_c11b_dispensado_vale_o_mes_inteiro_no_fuso_do_app(llm, monkeypatch):
    """O X do painel antigo grava aqui (exceção à D6, P2 do Codex no #1598): não volta no dia seguinte nem na última
    noite do mês, quando em UTC já é o mês seguinte; volta na 1ª hora do mês seguinte no fuso do app. Positivo: quem
    não dispensou vê o convite nos mesmos instantes (sem isso, "não volta" passaria num convite que nem sairia)."""
    _relogio(monkeypatch)
    uid, outro = _usuario("pro"), _usuario("pro")
    for u in (uid, outro):
        # Carteira em -30 por um gasto antigo: a conta a partir do zero fecha negativa em qualquer dia do mês.
        db.add_launch_and_update_balance(u, "despesa", 30, "Padaria", None, "padaria", ANTIGO)
        assert [i["key"] for i in _feed(u)] == [CHAVE]
    assert _put(uid, chave=CHAVE, dispensado=True, severidade="info").status_code == 200
    ultima_noite = datetime(2026, 10, 31, 22, tzinfo=_tz())
    assert ultima_noite.astimezone(timezone.utc).month == 11  # o instante discrimina o fuso
    for agora in (datetime(2026, 10, 21, 10, tzinfo=_tz()), ultima_noite):
        _relogio(monkeypatch, agora)
        assert _tipos(uid) == [], agora
        assert [i["key"] for i in _feed(outro)] == [CHAVE], agora
    _relogio(monkeypatch, datetime(2026, 11, 1, 0, 30, tzinfo=_tz()))
    assert [i["key"] for i in _feed(uid)] == ["wallet_balance_unknown:carteira:2026-11"]


def test_c12_confirmar_o_mesmo_valor_avisa_o_pb_escrita(llm, monkeypatch):
    """Sem escrita não há aviso, e sem aviso o cache de insights não cai (D13): o convite ficaria até o TTL."""
    import os

    from test_api_v2_perfil import cliente
    uid = _usuario("pro")
    c, h = cliente(uid)
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as ouvinte:
        ouvinte.execute("listen pb_escrita")
        r = c.post(f"/account/{uid}/adjust-balance", json={"target_balance": 0}, headers=h)
        assert r.status_code == 200 and r.json()["launch_id"] is None
        fim, avisos = time.monotonic() + 5, []
        while str(uid) not in avisos and time.monotonic() < fim:
            avisos += [n.payload for n in ouvinte.notifies(timeout=0.5, stop_after=1)]
    assert str(uid) in avisos


# ── C13: a conversa ──────────────────────────────────────────────────────────────

def test_c13_conversa_convite_no_card_e_o_aviso_depois_de_informar(llm, monkeypatch):
    """Pelo `handle_incoming` e pelo runner do chat, no relógio real. O NLP e o LLM são os dos testes: não provam a
    interpretação real das mensagens."""
    uid = _usuario("pro")
    per = periodo_mes(day_tz(now_tz()))
    assert _diga(uid, "quanto eu tenho?")
    assert "30" in _diga(uid, "gastei 30 na padaria")
    create_recurring_expense(uid, "Aluguel", 1000, "moradia", per["fim"].day, "account", start_date=AGOSTO)
    ai_patterns.generate_ai_insights(uid)
    openai_falso(monkeypatch, rodada(None, ("show_card", {"card": "piggy"})), rodada("Olha o que eu notei."))
    _, ev, _ = executar(uid, "o que você notou?")
    (bloco,) = [e["bloco"] for e in ev if e["tipo"] == "bloco"]
    assert [i["title"] for i in bloco["abas"][0]["recursos"]["insights"]["insights"]] == [TITULO]
    _post(uid, "adjust-balance", {"target_balance": 500})
    itens = ai_patterns.generate_ai_insights(uid, force=True)
    assert [i["type"] for i in itens if i["type"] == "wallet_balance_unknown"] == []
    (it,) = [i for i in itens if i["type"] == "month_projection"]
    assert "R$ 500,00 no negativo" in it["message"]
    assert llm == []


# ── C14 e C16: o item não vaza o zero-base e não vai por e-mail ──────────────────

def _chaves(o):
    if isinstance(o, dict):
        return set(o) | {k for v in o.values() for k in _chaves(v)}
    return {k for v in o for k in _chaves(v)} if isinstance(o, list) else set()


def test_c14_o_convite_nao_leva_numero_nenhum(llm, monkeypatch):
    uid = _ana(monkeypatch)
    (it,) = detectores.detect_month_projection(uid)
    json.dumps(it)  # serializável como está
    assert not any(ch.isdigit() for ch in it["title"] + it["message"])
    assert not {k for k in _chaves(it["explicacao"]) if k == "valor" or k.endswith("_valor")}
    assert len(it["title"]) <= 140 and len(it["message"]) <= 240 and it["message"] == TEXTO


def test_c16_o_convite_nao_vai_por_email_e_o_warning_da_mesma_cena_vai(llm, monkeypatch):
    uid = _ana(monkeypatch)
    (convite,) = detectores.detect_month_projection(uid)
    _post(uid, "adjust-balance", {"target_balance": 300})
    (aviso,) = detectores.detect_month_projection(uid)
    estado = {"dispensado_em": None, "dispensado_severidade": None, "dispensado_valor": None,
              "notificado_em": None, "notificado_severidade": None}
    assert a_enviar([convite], {convite["key"]: estado}, set()) == ([], [])
    assert a_enviar([aviso], {aviso["key"]: estado}, set()) == ([aviso], [])  # positivo
