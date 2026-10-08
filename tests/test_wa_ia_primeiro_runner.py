"""`chat(..., ia_primeiro=True)`: None só quando nenhuma escrita foi tentada.

Runner de verdade com o modelo falso (`tests/_ia_falsa_helpers.py`); U2 e o
teste das duas mensagens rodam a conversa pelo `handle_incoming`.
"""
from __future__ import annotations

import dataclasses
import itertools
from types import SimpleNamespace

import pytest

import db
from core.services.ai_chat import runner
from core.services.ai_chat import tools as ai_tools
from db.ai_chat import get_usage_this_month
from tests._fusao_of_helpers import uid_pro  # noqa: F401 (fixture)
from tests._ia_falsa_helpers import (
    chamada, com_tools, lancamento, lancamentos, liga_flag, openai_falso, texto,
)
from tests._pendencia_credito_helpers import diga
from tests.conftest import promote_to_pro


@pytest.fixture
def uid(user_id):
    return promote_to_pro(user_id)


def _historico(uid):
    return runner.trim_history_for_openai(db.ai_get_recent_messages(uid, limit=50))


def test_u1_create_falha_devolve_none_reembolsa_e_fecha_o_turno(uid, monkeypatch):
    clientes = openai_falso(monkeypatch, RuntimeError("modelo fora"))
    r = runner.chat(uid, "qual meu saldo?", monthly_limit=10, platform="whatsapp", ia_primeiro=True)
    assert r is None
    assert clientes == [{"api_key": "test-only", "timeout": 8.0, "max_retries": 0}]
    assert get_usage_this_month(uid) == 0
    h = db.ai_get_recent_messages(uid)
    assert [m["role"] for m in h] == ["user", "system"]
    assert h[-1]["content"] == runner._ATENDIDA_FORA
    assert lancamentos(uid) == []


def test_u5_mesma_falha_sem_ia_primeiro_e_error_msg(uid, monkeypatch):
    clientes = openai_falso(monkeypatch, RuntimeError("modelo fora"))
    r = runner.chat(uid, "qual meu saldo?", monthly_limit=10, platform="whatsapp")
    assert r == runner.ERROR_MSG
    assert clientes[0]["timeout"] == runner.OPENAI_TIMEOUT
    assert db.ai_get_recent_messages(uid)[-1]["content"] == runner.ERROR_MSG


def test_u3_prazo_estourado_devolve_none(uid, monkeypatch):
    # prazo = 0 + 15; 1ª volta em 1 (ok, roda a leitura); 2ª em 100 (estourou).
    # Troca o `time` só do runner: o `time.monotonic` global é do processo.
    relogio = itertools.chain([0.0, 1.0], itertools.repeat(100.0))
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: next(relogio)))
    openai_falso(monkeypatch, com_tools(chamada("get_period_summary", {})),
                 texto("🐷 resposta depois do prazo"))
    r = runner.chat(uid, "quanto entrou?", monthly_limit=10, platform="whatsapp", ia_primeiro=True)
    assert r is None
    assert get_usage_this_month(uid) == 0
    assert db.ai_get_recent_messages(uid)[-1]["role"] == "system"


def test_u4_dois_add_launch_na_rodada_desiste_sem_tool_calls_orfao(uid, monkeypatch):
    openai_falso(monkeypatch, com_tools(
        chamada("add_launch", {"tipo": "despesa", "valor": 50, "alvo": "ifood"}, "a"),
        chamada("add_launch", {"tipo": "despesa", "valor": 30, "alvo": "uber"}, "b"),
    ))
    r = runner.chat(uid, "50 ifood 30 uber", monthly_limit=10, platform="whatsapp", ia_primeiro=True)
    assert r is None
    assert lancamentos(uid) == []
    h = _historico(uid)
    assert [m["role"] for m in h] == ["user", "system"]
    assert not any(m.get("tool_calls") for m in db.ai_get_recent_messages(uid, limit=50))


def test_u6_cota_no_limite_devolve_none(uid, monkeypatch):
    openai_falso(monkeypatch)  # roteiro vazio: se chamar, levanta
    assert runner.chat(uid, "qto sobrou", monthly_limit=0, platform="whatsapp", ia_primeiro=True) is None
    assert runner.chat(uid, "qto sobrou", monthly_limit=0, platform="whatsapp") == \
        runner.LIMIT_MSG_TEMPLATE.format(limit=0)


def test_u2_escrita_e_depois_falha_nao_volta_ao_roteador(uid_pro, monkeypatch):
    liga_flag(monkeypatch)
    real = ai_tools.get_tool("add_launch")

    def grava_e_levanta(user_id, args):
        real.execute(user_id, args)
        raise RuntimeError("falha depois do commit")

    quebrada = dataclasses.replace(real, execute=grava_e_levanta)
    monkeypatch.setattr(runner, "get_tool", lambda n: quebrada if n == "add_launch" else ai_tools.get_tool(n))
    openai_falso(monkeypatch, lancamento(50))
    r = diga(uid_pro, "gastei 50 no mercado")
    assert runner.ERROR_MSG in r
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 50.0}]


def test_turno_fechado_impede_a_ia_de_refazer_o_pedido_pendurado(uid_pro, monkeypatch):
    """1ª mensagem: a IA cai e o roteador grava. 2ª: a IA "repete" qualquer
    mensagem do usuário que ficou sem resposta no histórico."""
    liga_flag(monkeypatch)

    def modelo(messages):
        sem_resposta = [m for i, m in enumerate(messages[:-1])
                        if m["role"] == "user" and messages[i + 1]["role"] == "user"]
        if sem_resposta:
            return lancamento(50)
        return texto("🐷 Sobrou bastante.")

    openai_falso(monkeypatch, RuntimeError("modelo fora"), modelo)
    diga(uid_pro, "gastei 50 no mercado")
    assert len(lancamentos(uid_pro)) == 1
    r = diga(uid_pro, "qto sobrou esse mes")
    assert "Sobrou bastante" in r, r
    assert db.ai_get_pending_action(uid_pro) is None
    assert len(lancamentos(uid_pro)) == 1


# ── Pendência armada no turno e escondida pela resposta final ────────────────
# Rodada 1: a IA arma `delete_launch` (pede confirmação). Rodada 2 sai por um
# caminho que NÃO é a pergunta dela. A pendência tem de morrer: senão o "sim"
# seguinte apaga um lançamento que o usuário nem viu ser proposto.

_TIRA = "tira o lançamento errado, foram 2 cafés de 8 cada"
_MODOS = {"ia_primeiro": "", "normal": "piggy "}   # prefixo = chat sem ia_primeiro


def _apaga_o_1():
    return com_tools(chamada("delete_launch", {"launch_id": "1"}, "d"))


@pytest.fixture
def com_lancamento_1(uid_pro, monkeypatch):
    from tests._ia_falsa_helpers import desliga_flag
    desliga_flag(monkeypatch)
    diga(uid_pro, "gastei 25 no mercado")          # roteador: lançamento #1
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 25.0}]
    liga_flag(monkeypatch)
    return uid_pro


def _sim_nao_apaga(uid):
    assert db.ai_get_pending_action(uid) is None
    diga(uid, "sim")
    assert lancamentos(uid) == [{"tipo": "despesa", "valor": 25.0}]


@pytest.mark.parametrize("modo", list(_MODOS))
def test_p1_um_por_vez_depois_de_armar_cancela(com_lancamento_1, monkeypatch, modo):
    uid = com_lancamento_1
    openai_falso(monkeypatch, _apaga_o_1(), com_tools(
        chamada("add_launch", {"tipo": "despesa", "valor": 50, "alvo": "mercado"}, "a"),
        chamada("add_launch", {"tipo": "despesa", "valor": 30, "alvo": "uber"}, "b"),
    ))
    r = diga(uid, _MODOS[modo] + _TIRA)
    assert runner._UM_POR_VEZ in r, r
    _sim_nao_apaga(uid)


@pytest.mark.parametrize("modo", list(_MODOS))
def test_p2_erro_depois_de_armar_cancela(com_lancamento_1, monkeypatch, modo):
    uid = com_lancamento_1
    openai_falso(monkeypatch, _apaga_o_1(), RuntimeError("modelo caiu"))
    r = diga(uid, _MODOS[modo] + _TIRA)
    assert runner.ERROR_MSG in r, r
    _sim_nao_apaga(uid)


def test_p2b_max_tool_loops_depois_de_armar_cancela(com_lancamento_1, monkeypatch):
    uid = com_lancamento_1
    monkeypatch.setattr(runner, "MAX_TOOL_LOOPS", 2)
    openai_falso(monkeypatch, _apaga_o_1(), com_tools(chamada("get_period_summary", {}, "s")))
    r = diga(uid, "piggy " + _TIRA)
    assert runner.ERROR_MSG in r, r
    _sim_nao_apaga(uid)


def test_p2c_write_direto_depois_de_armar_cancela(com_lancamento_1, monkeypatch):
    """Resposta final = resultado de um add_launch executado: não é a pergunta."""
    uid = com_lancamento_1
    from tests._ia_falsa_helpers import desliga_flag
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, _apaga_o_1(), lancamento(8, alvo="café"))
    r = diga(uid, "piggy " + _TIRA)
    assert "Despesa registrada" in r, r
    assert db.ai_get_pending_action(uid) is None
    diga(uid, "sim")
    assert lancamentos(uid) == [{"tipo": "despesa", "valor": 25.0},
                                {"tipo": "despesa", "valor": 8.0}]


def test_p3_prazo_depois_de_armar_cancela(com_lancamento_1, monkeypatch):
    uid = com_lancamento_1

    def estoura_na_2a_resposta(messages):
        # A rodada 1 armou o delete dentro do prazo; a 2ª resposta chega fora.
        monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: 1e12))
        return com_tools(chamada("get_period_summary", {}, "s"))

    openai_falso(monkeypatch, _apaga_o_1(), estoura_na_2a_resposta,
                 texto("🐷 não devia chegar aqui"))
    r = diga(uid, _TIRA)
    assert runner.ERROR_MSG in r, r
    _sim_nao_apaga(uid)


@pytest.mark.parametrize("modo", list(_MODOS))
def test_p4_texto_do_modelo_depois_de_armar_mantem(com_lancamento_1, monkeypatch, modo):
    uid = com_lancamento_1
    openai_falso(monkeypatch, _apaga_o_1(), texto("🐷 Confirma apagar o #1?"))
    r = diga(uid, _MODOS[modo] + _TIRA)
    assert "Confirma apagar" in r, r
    assert db.ai_get_pending_action(uid)["tool_name"] == "delete_launch"
    diga(uid, "sim")
    assert lancamentos(uid) == []


def test_p5_pendencia_que_nao_e_deste_turno_fica(com_lancamento_1, monkeypatch):
    """Outra janela (o app) arma a pendência durante o turno, e o turno sai com
    ERROR_MSG sem ter armado nada: a dela não é deste turno e fica."""
    uid = com_lancamento_1

    def outra_janela_arma_e_cai(messages):
        db.ai_set_pending_action(uid, "delete_launch", {"launch_id": "1"}, "apagar o lançamento #1")
        raise RuntimeError("modelo caiu")

    openai_falso(monkeypatch, outra_janela_arma_e_cai)
    r = diga(uid, "piggy qual meu saldo?")
    assert runner.ERROR_MSG in r, r
    assert db.ai_get_pending_action(uid)["tool_name"] == "delete_launch"


def test_p5b_rearmada_por_outra_janela_nao_e_cancelada(com_lancamento_1, monkeypatch):
    """O turno armou, outra janela re-armou por cima (created_at novo) e o
    turno caiu: o CAS não apaga a pendência da outra janela."""
    uid = com_lancamento_1

    def outra_janela_rearma_e_cai(messages):
        db.ai_set_pending_action(uid, "delete_launch", {"launch_id": "1"}, "apagar o lançamento #1")
        raise RuntimeError("modelo caiu")

    openai_falso(monkeypatch, _apaga_o_1(), outra_janela_rearma_e_cai)
    r = diga(uid, "piggy " + _TIRA)
    assert runner.ERROR_MSG in r, r
    assert db.ai_get_pending_action(uid)["tool_name"] == "delete_launch"


def test_p6_confirmacao_e_delete_na_mesma_rodada_nada_roda(com_lancamento_1, monkeypatch):
    """add_launch incerto + delete_launch na mesma rodada: nada roda nem fica
    armado (pré-varredura), e o "sim" seguinte não apaga nem grava."""
    uid = com_lancamento_1
    openai_falso(monkeypatch, com_tools(
        chamada("add_launch", {"tipo": "despesa", "valor": 500, "alvo": "mercado"}, "a"),
        chamada("delete_launch", {"launch_id": "1"}, "d"),
    ))
    r = diga(uid, "piggy gastei 50 no mercado")
    assert runner._UM_POR_VEZ in r, r
    _sim_nao_apaga(uid)


def test_p7_corrida_entre_gravar_e_reler_nao_apaga_a_outra_janela(com_lancamento_1, monkeypatch):
    """Logo depois de o turno gravar, outra janela re-arma (created_at novo).
    O token do turno é o da linha que ELE gravou: o cancelamento perde o CAS
    e a confirmação da outra janela fica."""
    uid = com_lancamento_1
    original = db.ai_set_pending_action

    def corrida(user_id, name, args, summary):
        meu = original(user_id, name, args, summary)
        original(user_id, "delete_launch", {"launch_id": "1"}, "outra janela")
        return meu

    monkeypatch.setattr(db, "ai_set_pending_action", corrida)
    openai_falso(monkeypatch, _apaga_o_1(), RuntimeError("modelo caiu"))
    r = diga(uid, "piggy " + _TIRA)
    assert runner.ERROR_MSG in r, r
    assert db.ai_get_pending_action(uid)["summary"] == "outra janela"


def test_set_pending_action_devolve_a_linha_como_get(user_id):
    gravada = db.ai_set_pending_action(user_id, "delete_launch", {"launch_id": "1"}, "apagar #1")
    assert gravada == db.ai_get_pending_action(user_id)
    assert gravada["created_at"].tzinfo is not None and gravada["tool_args"] == {"launch_id": "1"}


# ── Confirmação + outra escrita na mesma rodada: nada roda ────────────────

def _boletos(uid):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) n from bill_instances where user_id=%s", (uid,))
        n = cur.fetchone()["n"]
        conn.commit()
    return n


_BOLETO = chamada("add_boleto", {"name": "IPTU", "amount": 300, "days": 10}, "b")


def _lanca(valor):
    return chamada("add_launch", {"tipo": "despesa", "valor": valor, "alvo": "mercado"}, "a")


@pytest.mark.parametrize("modo", list(_MODOS))
@pytest.mark.parametrize("ordem", ["boleto-antes", "boleto-depois"])
def test_confirmacao_e_outra_escrita_na_mesma_rodada_nada_roda(uid_pro, monkeypatch, ordem, modo):
    liga_flag(monkeypatch)
    rodada = [_BOLETO, _lanca(500)] if ordem == "boleto-antes" else [_lanca(500), _BOLETO]
    openai_falso(monkeypatch, com_tools(*rodada))     # usuário disse 50: incerto
    r = diga(uid_pro, _MODOS[modo] + "gastei 50 no mercado")
    assert runner._UM_POR_VEZ in r, r
    assert _boletos(uid_pro) == 0
    assert lancamentos(uid_pro) == []
    assert db.ai_get_pending_action(uid_pro) is None
    h = db.ai_get_recent_messages(uid_pro, limit=50)
    assert not any(m.get("tool_calls") for m in h), h
    assert runner.trim_history_for_openai(h) == h


def test_add_launch_certo_e_outra_escrita_rodam_como_hoje(uid_pro, monkeypatch):
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, com_tools(_lanca(50), _BOLETO))
    r = diga(uid_pro, "gastei 50 no mercado")
    assert "Só confirmando" not in r, r
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 50.0}]
    assert _boletos(uid_pro) == 1


@pytest.mark.parametrize("leitura_antes", [True, False], ids=["leitura-antes", "leitura-depois"])
def test_confirmacao_com_leitura_na_rodada(uid_pro, monkeypatch, leitura_antes):
    """Leitura não conta como escrita: a rodada segue e a resposta é o
    `_CONFIRMA`, com a pendência viva. A leitura que vem antes roda; a que vem
    depois do `_CONFIRMA` não (corte, rede de segurança)."""
    liga_flag(monkeypatch)
    real = ai_tools.get_tool("get_period_summary")
    rodou = []

    def conta(user_id, args):
        rodou.append(1)
        return real.execute(user_id, args)

    contada = dataclasses.replace(real, execute=conta)
    monkeypatch.setattr(runner, "get_tool",
                        lambda n: contada if n == "get_period_summary" else ai_tools.get_tool(n))
    leitura = chamada("get_period_summary", {}, "s")
    rodada = [leitura, _lanca(500)] if leitura_antes else [_lanca(500), leitura]
    openai_falso(monkeypatch, com_tools(*rodada))
    r = diga(uid_pro, "gastei 50 no mercado")
    assert "Só confirmando" in r, r
    assert db.ai_get_pending_action(uid_pro)["tool_name"] == "add_launch"
    assert rodou == ([1] if leitura_antes else [])
    h = db.ai_get_recent_messages(uid_pro, limit=50)
    assert runner.trim_history_for_openai(h) == h


# ── requires_confirmation + outra escrita: vale em todos os canais ──────────

@pytest.mark.parametrize("flag,platform", [
    (True, "whatsapp"), (False, "whatsapp"), (False, "dashboard"), (True, "dashboard"),
])
def test_apagar_e_lancar_na_mesma_rodada_nada_roda(com_lancamento_1, monkeypatch, flag, platform):
    from tests._ia_falsa_helpers import desliga_flag
    uid = com_lancamento_1
    if not flag:
        desliga_flag(monkeypatch)
    openai_falso(monkeypatch, com_tools(
        chamada("add_launch", {"tipo": "despesa", "valor": 50, "alvo": "ifood"}, "a"),
        chamada("delete_launch", {"launch_id": "1"}, "b"),
    ))
    r = runner.chat(uid, "gasta 50 no ifood e apaga o lançamento #1",
                    monthly_limit=10, platform=platform)
    assert r == runner._UM_POR_VEZ, r
    assert lancamentos(uid) == [{"tipo": "despesa", "valor": 25.0}]
    assert db.ai_get_pending_action(uid) is None
    h = db.ai_get_recent_messages(uid, limit=50)
    assert not any(m.get("tool_calls") for m in h), h
    assert runner.trim_history_for_openai(h) == h


def test_apagar_sozinho_no_dashboard_pergunta_como_hoje(com_lancamento_1, monkeypatch):
    uid = com_lancamento_1
    openai_falso(monkeypatch, _apaga_o_1(), texto("🐷 Confirma apagar o #1?"))
    r = runner.chat(uid, "apaga o lançamento #1", monthly_limit=10, platform="dashboard")
    assert "Confirma apagar" in r, r
    assert db.ai_get_pending_action(uid)["tool_name"] == "delete_launch"


def test_apagar_orcamento_e_lancar_na_mesma_rodada_nada_roda(com_lancamento_1, monkeypatch):
    uid = com_lancamento_1
    openai_falso(monkeypatch, com_tools(
        chamada("delete_budget", {"categoria": "alimentação"}, "o"),
        chamada("add_launch", {"tipo": "despesa", "valor": 50, "alvo": "mercado"}, "a"),
    ))
    r = diga(uid, "gastei 50 no mercado")
    assert runner._UM_POR_VEZ in r, r
    assert lancamentos(uid) == [{"tipo": "despesa", "valor": 25.0}]
    assert db.ai_get_pending_action(uid) is None


# ── set_budget arma a pergunta dentro do execute: conta sempre ──────────────

def _orcamento(valor):
    return chamada("set_budget", {"categoria": "mercado", "budget": valor}, "o")


@pytest.mark.parametrize("ja_tem_orcamento", [True, False], ids=["atualiza", "cria"])
def test_set_budget_e_lancamento_na_mesma_rodada_nada_roda(uid_pro, monkeypatch, ja_tem_orcamento):
    """Atualizando, o set_budget arma "atualizar orçamento?" dentro do execute;
    criando, não armaria — conta igual (lado conservador, sem ler o banco)."""
    liga_flag(monkeypatch)
    if ja_tem_orcamento:
        db.upsert_budget(uid_pro, "mercado", 300)
    openai_falso(monkeypatch, com_tools(_orcamento(800), _lanca(50)))
    r = diga(uid_pro, "gastei 50 no mercado")
    assert runner._UM_POR_VEZ in r, r
    assert lancamentos(uid_pro) == []
    assert db.ai_get_pending_action(uid_pro) is None
    orc = db.get_budget(uid_pro, "mercado")
    assert (orc["budget"] if orc else None) == (300.0 if ja_tem_orcamento else None)


def test_pagar_conta_e_lancamento_na_mesma_rodada_nada_roda(uid_pro, monkeypatch):
    """mark_bill_paid pode armar "quanto veio?" (conta variável) dentro do
    execute: conta sempre, mesmo numa conta de valor fixo."""
    from datetime import timedelta
    from db.bills import create_boleto, get_bill
    from utils_date import today_tz
    liga_flag(monkeypatch)
    conta = create_boleto(uid_pro, "IPTU", 300, today_tz() + timedelta(days=5))
    openai_falso(monkeypatch, com_tools(
        chamada("mark_bill_paid", {"bill_id": int(conta["id"]), "forma_pagamento": "dinheiro"}, "p"),
        _lanca(50)))
    r = diga(uid_pro, "gastei 50 no mercado")
    assert runner._UM_POR_VEZ in r, r
    assert lancamentos(uid_pro) == []
    assert get_bill(uid_pro, int(conta["id"]))["status"] != "paid"


def test_set_budget_sozinho_atualizando_pergunta_como_hoje(uid_pro, monkeypatch):
    liga_flag(monkeypatch)
    db.upsert_budget(uid_pro, "mercado", 300)
    openai_falso(monkeypatch, com_tools(_orcamento(800)))
    r = diga(uid_pro, "piggy muda o orçamento de mercado pra 800")
    assert "Atualizar pra R$ 800.00?" in r, r
    assert db.ai_get_pending_action(uid_pro)["tool_name"] == "set_budget"
    diga(uid_pro, "sim")
    assert db.get_budget(uid_pro, "mercado")["budget"] == 800.0


# ── Prazo do turno: a chamada não passa dele, nem a resposta atrasada roda ──

def _relogio(monkeypatch, *instantes):
    """`time` só do runner: devolve os instantes em ordem e repete o último."""
    fila = list(instantes)
    monkeypatch.setattr(runner, "time", SimpleNamespace(
        monotonic=lambda: fila.pop(0) if len(fila) > 1 else fila[0]))


def test_resposta_depois_do_prazo_nao_despacha_escrita(uid_pro, monkeypatch):
    liga_flag(monkeypatch)
    # prazo = 0 + 15; topo da volta em 1; timeout calculado em 1; resposta em 20.
    _relogio(monkeypatch, 0.0, 1.0, 1.0, 20.0)
    openai_falso(monkeypatch, lancamento(50))
    r = runner.chat(uid_pro, "gastei 50 no mercado", monthly_limit=10,
                    platform="whatsapp", ia_primeiro=True)
    assert r is None
    assert lancamentos(uid_pro) == []
    h = db.ai_get_recent_messages(uid_pro, limit=50)
    assert not any(m.get("tool_calls") for m in h), h
    assert runner.trim_history_for_openai(h) == h


def test_timeout_da_chamada_cabe_no_prazo_e_dentro_dele_roda(uid_pro, monkeypatch):
    liga_flag(monkeypatch)
    # prazo = 15; topo em 10; timeout = min(8, 15 - 10) = 5; resposta em 11.
    _relogio(monkeypatch, 0.0, 10.0, 10.0, 11.0)
    clientes = openai_falso(monkeypatch, lancamento(50))
    r = runner.chat(uid_pro, "gastei 50 no mercado", monthly_limit=10,
                    platform="whatsapp", ia_primeiro=True)
    assert clientes.creates[0]["timeout"] == 5.0
    assert "Só confirmando" not in r, r
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 50.0}]


def test_fora_do_ia_primeiro_a_chamada_nao_ganha_timeout_proprio(uid_pro, monkeypatch):
    clientes = openai_falso(monkeypatch, texto("🐷 oi"))
    runner.chat(uid_pro, "oi", monthly_limit=10, platform="dashboard")
    assert "timeout" not in clientes.creates[0]
