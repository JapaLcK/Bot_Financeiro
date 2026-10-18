"""PR C4, conserto estrutural do Tester (BLOQUEIO 1): `create_meta` só GRAVA DIRETO quando a MENSAGEM DO TURNO manda
criar (`pede_criar_meta_ja`, gramática fechada em `core/services/metas_gate.py`). Em qualquer outra (desejo, hipótese, relato, receita que cita meta) o modelo,
mesmo errando e chamando a tool, só consegue armar o "Só confirmando": o "sim" grava 1 vez. Runner de verdade, modelo
FALSO que SEMPRE chama `create_meta` (o pior caso), banco real.
Controles (injetados e restaurados): `confirmar_se` sempre False -> `test_controle_*` e `test_mensagem_ambigua_*`
(grava direto); `pede_criar_meta_ja` sempre False -> `test_comando_explicito_*`; sem o gate da chave no `execute` ->
`test_chave_removida_*`.
"""
from __future__ import annotations

import dataclasses

import pytest

import db
from conftest import usuario_pagante
from core.services.metas_gate import pede_criar_meta_ja
from core.services.ai_chat import runner, tools as ai_tools
from core.services.ai_chat.tools import metas_escrita as me
from db.connection import get_conn
from tests._ia_falsa_helpers import chamada, com_tools, openai_falso, texto
from tests.test_ai_chat_metas_escrita import linhas
from tests.test_wa_minhas_metas import chave  # noqa: F401  (fixture)

VIAGEM = {"nome": "Viagem", "tipo": "meta", "alvo": 5000}
PERGUNTA = '🐷 Só confirmando: registrar *a meta "Viagem": juntar R$ 5.000,00*? Responde *sim* ou *não*.'
AMBIGUAS = ["queria uma meta de 5000 pra viagem", "registrei 100 de receita pra meta 2028", "e se eu criasse uma meta de 5000?",
            "minha amiga criou uma meta de 10 mil", "acho que preciso de uma meta de 5000", "quero uma meta de 5000 pra viagem",
            "registra um depósito de 100 na meta 2028", "não cria nada, mas uma meta de 5000 seria boa", "sim", "cria uma meta se eu conseguir",
            "pode criar uma meta de 5000?", "cria uma meta de 5000 pra viagem, é só um teste", "cria uma meta de 5000 pra viagem mas só amanhã"]
EXPLICITAS = ["cria uma meta de 5000 pra viagem", "Cria uma meta de R$ 5.000 pra viagem!", "pode criar uma meta de 5000 pra viagem",
              "faz uma meta de 5000 pra viagem", "registra uma meta chamada Viagem de 5000", "crie a meta Viagem de 5000"]


@pytest.fixture
def k(chave):
    u = usuario_pagante(plan="pro_max")
    chave(u)
    return u


def fala(uid, msg, *rodadas, monkeypatch):
    openai_falso(monkeypatch, *rodadas)
    return runner.chat(uid, msg, monthly_limit=50, platform="whatsapp")


def modelo_cria(uid, msg, monkeypatch, args=VIAGEM):
    return fala(uid, msg, com_tools(chamada("create_meta", args)), monkeypatch=monkeypatch)


def pendencia(uid):
    return db.ai_get_pending_action(uid)


@pytest.mark.parametrize("msg", AMBIGUAS)
def test_mensagem_ambigua_so_arma_a_pergunta_e_nao_grava(k, monkeypatch, msg):
    assert modelo_cria(k, msg, monkeypatch) == PERGUNTA
    assert linhas(k) == [] and pendencia(k)["tool_name"] == "create_meta"


def test_o_sim_grava_uma_vez_e_o_segundo_sim_nao_cria_outra(k, monkeypatch):
    modelo_cria(k, "queria uma meta de 5000 pra viagem", monkeypatch)
    r = fala(k, "sim", monkeypatch=monkeypatch)
    assert r.startswith('✅ Criei a meta "Viagem": juntar R$ 5.000,00,') and len(linhas(k)) == 1 and pendencia(k) is None
    assert fala(k, "sim", texto("🐷 Sim pra quê?"), monkeypatch=monkeypatch).startswith("🐷 Sim pra quê")
    assert len(linhas(k)) == 1


@pytest.mark.parametrize("resposta", ["não", "não, deixa", "gastei 50 no mercado"])
def test_nao_ou_qualquer_outra_coisa_cancela_e_o_sim_depois_nao_cria(k, monkeypatch, resposta):
    modelo_cria(k, "queria uma meta de 5000 pra viagem", monkeypatch)
    fala(k, resposta, texto("🐷 ok"), monkeypatch=monkeypatch)
    assert pendencia(k) is None
    fala(k, "sim", texto("🐷 Sim pra quê?"), monkeypatch=monkeypatch)
    assert linhas(k) == []


def test_pendencia_vencida_nao_grava(k, monkeypatch):
    modelo_cria(k, "queria uma meta de 5000 pra viagem", monkeypatch)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update ai_pending_actions set created_at = now() - interval '11 minutes' where user_id = %s", (k,))
        conn.commit()
    fala(k, "sim", texto("🐷 Sim pra quê?"), monkeypatch=monkeypatch)
    assert linhas(k) == []


def test_chave_removida_entre_armar_e_o_sim_nao_grava(k, chave, monkeypatch):
    modelo_cria(k, "queria uma meta de 5000 pra viagem", monkeypatch)
    chave()
    assert fala(k, "sim", monkeypatch=monkeypatch) == me.SEM_METAS and linhas(k) == []


@pytest.mark.parametrize("msg", EXPLICITAS)
def test_comando_explicito_grava_direto_sem_pendencia(k, monkeypatch, msg):
    assert modelo_cria(k, msg, monkeypatch).startswith('✅ Criei a meta "Viagem": juntar R$ 5.000,00')
    assert len(linhas(k)) == 1 and pendencia(k) is None


def test_reserva_explicita_grava_direto_e_desejo_de_reserva_pergunta(k, monkeypatch):
    reserva = {"nome": "Reserva", "tipo": "reserva"}
    assert modelo_cria(k, "queria uma reserva de emergência", monkeypatch, reserva) == (
        '🐷 Só confirmando: registrar *a reserva de emergência "Reserva" (6 meses de contas fixas)*? Responde *sim* ou *não*.')
    assert linhas(k) == []
    assert fala(k, "não", monkeypatch=monkeypatch) == "👍 Beleza, não fiz nada."
    r = modelo_cria(k, "cria minha reserva de emergência", monkeypatch, reserva)
    assert r.startswith('✅ Criei sua reserva de emergência "Reserva": 6 meses') and len(linhas(k)) == 1


def test_ambigua_com_pedido_ja_invalido_responde_o_erro_e_nao_pergunta(k, monkeypatch):
    for args, trecho in (({"nome": "Viagem", "tipo": "meta"}, "Meta pede o valor"), ({**VIAGEM, "alvo": 0}, "maior que zero"),
                         ({**VIAGEM, "data_alvo": "2020-01-01"}, "passado"), ({**VIAGEM, "nome": ""}, "o nome")):
        assert trecho in modelo_cria(k, "queria uma meta de 5000 pra viagem", monkeypatch, args)
        assert pendencia(k) is None and linhas(k) == []


def test_ambigua_com_nome_repetido_recusa_na_hora(k, monkeypatch):
    modelo_cria(k, "cria uma meta de 5000 pra viagem", monkeypatch)
    assert modelo_cria(k, "queria uma meta de 5000 pra viagem", monkeypatch).startswith('🐷 Você já tem a meta "Viagem"')
    assert pendencia(k) is None and len(linhas(k)) == 1


def test_controle_sem_o_gancho_a_mensagem_ambigua_grava_direto(k, monkeypatch):
    real = ai_tools.get_tool("create_meta")
    solto = dataclasses.replace(real, confirmar_se=None)
    monkeypatch.setattr(runner, "get_tool", lambda n: solto if n == "create_meta" else ai_tools.get_tool(n))
    modelo_cria(k, "queria uma meta de 5000 pra viagem", monkeypatch)
    assert len(linhas(k)) == 1  # é o que o `confirmar_se` impede


def test_controle_sem_a_regra_do_comando_ate_o_explicito_pergunta(k, monkeypatch):
    monkeypatch.setattr("core.services.metas_gate.pede_criar_meta_ja", lambda t, a=None: False)
    assert modelo_cria(k, "cria uma meta de 5000 pra viagem", monkeypatch) == PERGUNTA and linhas(k) == []


@pytest.mark.parametrize("frase,esperado", [(f, True) for f in EXPLICITAS] + [(f, False) for f in AMBIGUAS] + [
    ("cria minha reserva de emergência", True), ("faz um relatório das metas", False), ("criei uma meta ontem", False),
    ("me ajuda a criar uma meta", False), ("quero criar uma meta de 5000", False), ("como faço pra criar uma meta?", False)])
def test_tabela_do_comando_explicito(frase, esperado):
    assert pede_criar_meta_ja(frase) is esperado, frase


def test_nome_com_nul_na_pergunta_vira_frase_e_nao_estoura(k, monkeypatch):
    """B-1: o JSONB da pendência recusa NUL (`psycopg.DataError` escapava do runner); agora recusa antes de armar."""
    r = modelo_cria(k, "queria uma meta de 5000 pra viagem", monkeypatch, {**VIAGEM, "nome": "Via\x00gem"})
    assert r.startswith("🐷 O nome não pode ter caracteres de controle") and pendencia(k) is None and linhas(k) == []
