"""PR C4 (Metas): `edit_meta`/`delete_meta` pedem confirmação, `create_meta` não, e N não muda.
Runner de verdade com o modelo falso (`tests/_ia_falsa_helpers.py`), banco real. Não prova a interpretação do
modelo (harness real, do Tester).
Controles (injetados e restaurados): sem `arma_pendencia_no_execute` no `create_meta` -> `test_duas_escritas_*`
(2 linhas); sem o gate da chave no `execute` -> `test_chave_removida_*`; `edit_meta`/`delete_meta` fora de
`NAO_FINANCEIRAS_IA` -> `test_pendencia_de_meta_*`; sem `validate` -> `test_nome_que_nao_existe_*`.
"""
from __future__ import annotations

import dataclasses

import pytest

import db
from conftest import usuario_pagante
from core.services.ai_chat import runner, system_prompt as sp, tools as ai_tools
from core.services.ai_chat.tools import get_tool, metas_escrita as me
from core.services.cashflow_snapshot import pendencia_financeira
from db.connection import get_conn
from tests._ia_falsa_helpers import chamada, com_tools, lancamento, lancamentos, openai_falso, texto
from tests.test_ai_chat_metas import nova
from tests.test_ai_chat_metas_escrita import FUTURO, PASSADO, linhas
from tests.test_wa_minhas_metas import chave  # noqa: F401  (fixture)


@pytest.fixture
def k(chave):
    u = usuario_pagante(plan="pro_max")
    chave(u)
    nova(u, "Casa nova", "5000", "300")
    return u


def fala(uid, msg, *rodadas, monkeypatch):
    openai_falso(monkeypatch, *rodadas)
    return runner.chat(uid, msg, monthly_limit=50, platform="whatsapp")


def pede(uid, nome, args, monkeypatch, msg="faz isso"):
    return fala(uid, msg, com_tools(chamada(nome, args)), texto("🐷 Confirma? (sim/não)"), monkeypatch=monkeypatch)


def pendencia(uid):
    return db.ai_get_pending_action(uid)


def alvo(uid):
    return linhas(uid)[0]["alvo"]


def test_editar_pede_confirmacao_nao_escreve_e_o_sim_escreve(k, monkeypatch):
    pede(k, "edit_meta", {"nome": "casa nova", "alvo": 6000, "data_alvo": FUTURO}, monkeypatch)
    p = pendencia(k)
    d = FUTURO
    assert p["tool_name"] == "edit_meta"
    assert p["summary"] == f'mudar a meta "casa nova": alvo para R$ 6.000,00; prazo para {d[8:]}/{d[5:7]}/{d[:4]}'
    assert str(alvo(k)) == "5000.00"
    r = fala(k, "sim", monkeypatch=monkeypatch)
    assert r == f'✅ Meta "Casa nova" atualizada: alvo para R$ 6.000,00; prazo para {d[8:]}/{d[5:7]}/{d[:4]}.'
    assert str(alvo(k)) == "6000.00" and linhas(k)[0]["data_alvo"].isoformat() == FUTURO and pendencia(k) is None


def test_sem_prazo_e_renomear(k, monkeypatch):
    pede(k, "edit_meta", {"nome": "Casa nova", "sem_prazo": True, "novo_nome": "Apê"}, monkeypatch)
    assert pendencia(k)["summary"] == 'mudar a meta "Casa nova": nome para "Apê"; sem prazo'
    assert fala(k, "sim", monkeypatch=monkeypatch) == '✅ Meta "Casa nova" atualizada: nome para "Apê"; sem prazo.'
    assert linhas(k)[0]["nome"] == "Apê" and linhas(k)[0]["data_alvo"] is None


@pytest.mark.parametrize("args,trecho", [
    ({"nome": "Casa nova"}, "Me diz o que mudar"),
    ({"nome": "Casa nova", "meses": 8}, "Meses é só da reserva"),
    ({"nome": "Casa nova", "data_alvo": PASSADO}, "não pode estar no passado"),
    ({"nome": "Casa nova", "data_alvo": FUTURO, "sem_prazo": True}, "não os dois"),
    ({"nome": "Casa nova", "alvo": -1}, "o valor"),
    ({"nome": "Casa nova", "alvo": float("nan")}, "o valor"),
    ({"nome": "Casa nova", "alvo": 1e300}, "o valor"),
    ({"nome": "Casa nova", "novo_nome": "x" * 61}, "o nome"),
    ({"nome": "Casa nova", "data_alvo": "2026-13-40"}, "Data inválida"),
])
def test_edicao_hostil_responde_na_hora_e_nao_arma_nada(k, monkeypatch, args, trecho):
    r = pede(k, "edit_meta", args, monkeypatch)
    assert trecho in r and pendencia(k) is None and str(alvo(k)) == "5000.00", (args, r)


def test_renomear_para_nome_que_ja_existe_e_recusado_sem_armar(k, monkeypatch):
    nova(k, "Viagem", "100", "10")
    for novo in ("viagem", "VIAGEM "):
        r = pede(k, "edit_meta", {"nome": "Casa nova", "novo_nome": novo}, monkeypatch)
        assert "outra meta chamada" in r
        assert pendencia(k) is None
    assert sorted(m["nome"] for m in linhas(k)) == ["Casa nova", "Viagem"]


def test_edicao_com_valor_ou_meses_fora_da_faixa_responde_antes_do_sim(k, monkeypatch):
    """O `validate` confere o que a rota só diria depois do "sim": valor > 0 e meses de 1 a 24."""
    get_tool("create_meta").execute(k, {"nome": "Reserva", "tipo": "reserva"})
    for args, trecho in (({"nome": "Casa nova", "alvo": 0}, "maior que zero"), ({"nome": "Casa nova", "alvo": -3}, "o valor"),
                         ({"nome": "Reserva", "meses": 0}, "Meses de 1 a 24"), ({"nome": "Reserva", "meses": -3}, "Meses de 1 a 24"),
                         ({"nome": "Reserva", "meses": 30}, "Meses de 1 a 24")):
        assert trecho in pede(k, "edit_meta", args, monkeypatch), args
        assert pendencia(k) is None
    pede(k, "edit_meta", {"nome": "Reserva", "meses": 12}, monkeypatch)  # positivo: o limite de cima passa
    assert pendencia(k)["tool_name"] == "edit_meta"


def test_nome_que_nao_existe_nao_arma_e_lista_as_metas(k, monkeypatch):
    r = pede(k, "delete_meta", {"nome": "Xpto"}, monkeypatch)
    assert r == "🐷 Não achei a meta *Xpto*. Suas metas: Casa nova." and pendencia(k) is None
    assert len(linhas(k)) == 1


def test_nome_por_igualdade_e_nao_por_contencao(k, monkeypatch):
    for nome in ("Casa", "nova", "Casa nova 2028"):
        assert "Não achei a meta" in pede(k, "delete_meta", {"nome": nome}, monkeypatch), nome
        assert pendencia(k) is None
    assert len(linhas(k)) == 1


def test_dois_com_o_mesmo_nome_manda_pro_painel(k, monkeypatch):
    nova(k, "casa  NOVA", "100", "10")  # o painel aceita nome repetido
    r = pede(k, "delete_meta", {"nome": "Casa nova"}, monkeypatch)
    assert "mais de uma meta chamada *Casa nova*" in r and "painel" in r and pendencia(k) is None
    assert len(linhas(k)) == 2


def test_apagar_pede_confirmacao_e_o_sim_apaga_so_a_meta(k, monkeypatch):
    nova(k, "Outra", "100", "10")
    pede(k, "delete_meta", {"nome": "casa nova"}, monkeypatch)
    assert pendencia(k)["summary"] == 'apagar a meta "casa nova" (o dinheiro no banco não muda)'
    assert len(linhas(k)) == 2
    assert fala(k, "sim", monkeypatch=monkeypatch) == '✅ Meta "Casa nova" apagada. O dinheiro no banco não mudou.'
    assert [m["nome"] for m in linhas(k)] == ["Outra"] and pendencia(k) is None


def test_nao_cancela_e_um_sim_solto_depois_nao_apaga(k, monkeypatch):
    pede(k, "delete_meta", {"nome": "Casa nova"}, monkeypatch)
    assert fala(k, "não", monkeypatch=monkeypatch) == "👍 Beleza, não fiz nada."
    assert len(linhas(k)) == 1 and pendencia(k) is None
    assert "apagada" not in fala(k, "sim", texto("🐷 Sim pra quê?"), monkeypatch=monkeypatch)
    assert len(linhas(k)) == 1


def test_pendencia_vencida_nao_executa(k, monkeypatch):
    pede(k, "delete_meta", {"nome": "Casa nova"}, monkeypatch)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update ai_pending_actions set created_at = now() - interval '11 minutes' where user_id = %s", (k,))
        conn.commit()
    assert "apagada" not in fala(k, "sim", texto("🐷 Sim pra quê?"), monkeypatch=monkeypatch)
    assert len(linhas(k)) == 1


def test_chave_removida_entre_armar_e_o_sim_nao_apaga(k, chave, monkeypatch):
    pede(k, "delete_meta", {"nome": "Casa nova"}, monkeypatch)
    chave()  # a chave cai
    assert fala(k, "sim", monkeypatch=monkeypatch) == me.SEM_METAS
    assert len(linhas(k)) == 1


def test_meta_renomeada_no_painel_dentro_dos_10_min_vira_nao_achei(k, monkeypatch):
    pede(k, "delete_meta", {"nome": "Casa nova"}, monkeypatch)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update metas set nome = 'Outro nome' where user_id = %s", (k,))
        conn.commit()
    assert fala(k, "sim", monkeypatch=monkeypatch).startswith("🐷 Não achei a meta *Casa nova*")
    assert len(linhas(k)) == 1


# ── criar sozinho, e a rodada com duas escritas ─────────────────────────────────────────────────

def test_criar_sozinho_grava_e_a_resposta_e_a_da_tool(k, monkeypatch):
    r = fala(k, "cria uma meta de 5000 pra viagem", com_tools(chamada("create_meta", {"nome": "Viagem", "tipo": "meta", "alvo": 5000})),
             monkeypatch=monkeypatch)
    assert r.startswith('✅ Criei a meta "Viagem": juntar R$ 5.000,00,') and pendencia(k) is None
    assert [m["nome"] for m in linhas(k)] == ["Casa nova", "Viagem"]


def test_duas_escritas_na_mesma_rodada_nao_rodam_nenhuma(k, monkeypatch):
    dois = com_tools(chamada("create_meta", {"nome": "A", "tipo": "meta", "alvo": 100}, "a"),
                     chamada("create_meta", {"nome": "B", "tipo": "meta", "alvo": 200}, "b"))
    assert fala(k, "cria a meta A de 100 e a meta B de 200", dois, monkeypatch=monkeypatch) == runner._UM_POR_VEZ
    assert [m["nome"] for m in linhas(k)] == ["Casa nova"]
    misto = com_tools(chamada("create_meta", {"nome": "A", "tipo": "meta", "alvo": 100}, "a"),
                      chamada("add_launch", {"tipo": "despesa", "valor": 50, "alvo": "mercado"}, "b"))
    assert fala(k, "cria a meta A de 100 e gastei 50 na meta", misto, monkeypatch=monkeypatch) == runner._UM_POR_VEZ
    assert len(linhas(k)) == 1 and lancamentos(k) == []


def test_controle_sem_arma_pendencia_as_duas_rodam_e_a_resposta_cita_so_uma(k, monkeypatch):
    real = ai_tools.get_tool("create_meta")
    solto = dataclasses.replace(real, arma_pendencia_no_execute=False)
    monkeypatch.setattr(runner, "get_tool", lambda n: solto if n == "create_meta" else ai_tools.get_tool(n))
    dois = com_tools(chamada("create_meta", {"nome": "A", "tipo": "meta", "alvo": 100}, "a"),
                     chamada("create_meta", {"nome": "A", "tipo": "meta", "alvo": 100}, "b"))
    r = fala(k, "cria a meta A de 100", dois, monkeypatch=monkeypatch)  # a frase manda criar: as duas passam pelo gate
    assert len(linhas(k)) == 2 and '"A"' in r  # é o que o campo impede


def test_criar_com_uma_leitura_junto_nao_e_escrita_dupla(k, monkeypatch):
    r = fala(k, "cria a meta A de 100", com_tools(chamada("list_metas", {}, "l"), chamada("create_meta", {"nome": "A", "tipo": "meta", "alvo": 100}, "c")),
             monkeypatch=monkeypatch)
    assert r.startswith("✅ Criei a meta") and len(linhas(k)) == 2


# ── N idêntico à main: o que o modelo vê ─────────────────────────────────────────────────────────

def test_n_nao_ve_as_tools_nem_o_prompt_de_escrita(chave):
    n = usuario_pagante(plan="pro_max")
    chave()
    nomes = {"create_meta", "edit_meta", "delete_meta"}
    for app in (False, True):
        vistos = {s["function"]["name"] for s in ai_tools.schemas_para(n, app)}
        assert not vistos & nomes
        assert "create_meta" not in sp.prompt_do_usuario(False, app) and "edit_meta" not in sp.prompt_do_usuario(False, app)
    assert not nomes & ai_tools.WRITE_TOOL_NAMES and not nomes & {s["function"]["name"] for s in ai_tools.SCHEMAS}
    assert not nomes & {s["function"]["name"] for s in ai_tools.SCHEMAS_APP}


def test_k_ve_as_tres_no_whatsapp_e_no_app_e_o_prompt_diz_que_grava_na_hora(k):
    nomes = {"create_meta", "edit_meta", "delete_meta"}
    for app in (False, True):
        vistos = {s["function"]["name"] for s in ai_tools.schemas_para(k, app)}
        assert nomes <= vistos and ("list_metas" in vistos) is (not app)
        p = sp.prompt_do_usuario(True, app)
        assert "grava na hora (quando ele mandou)" in p and "NUNCA pergunte em texto" in p and "Quer que eu crie" not in p and "Só confirmando" not in p and "AINDA NÃO dá" not in p and "ainda não dá pelo chat" not in p
    assert "`delete_meta`" in sp.prompt_do_usuario(True, True)


def test_pendencia_de_meta_nao_e_financeira_e_a_de_criar_pocket_continua_sendo():
    for nome in ("create_meta", "edit_meta", "delete_meta"):  # create_meta arma "Só confirmando": também não é caixa
        assert not pendencia_financeira(nome, {}, ia=True), nome
    assert pendencia_financeira("create_pocket", {}, ia=True)
