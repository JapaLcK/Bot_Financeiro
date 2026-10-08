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

    def arma_e_estoura(messages):
        monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: 1e12))
        return _apaga_o_1()

    openai_falso(monkeypatch, arma_e_estoura, texto("🐷 não devia chegar aqui"))
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


def test_p6_confirma_sobrescrita_na_mesma_rodada_cancela(com_lancamento_1, monkeypatch):
    """`_CONFIRMA` do add_launch vence como resposta, mas o delete_launch da
    mesma rodada sobrescreve a pendência: a resposta não é a pergunta dela."""
    uid = com_lancamento_1
    openai_falso(monkeypatch, com_tools(
        chamada("add_launch", {"tipo": "despesa", "valor": 500, "alvo": "mercado"}, "a"),
        chamada("delete_launch", {"launch_id": "1"}, "d"),
    ))
    r = diga(uid, "piggy gastei 50 no mercado")
    assert "Só confirmando" in r, r
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
