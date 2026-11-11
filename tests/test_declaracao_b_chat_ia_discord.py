"""PR-B (b) (ADR 0006): a declaração pelo chat IA no Discord (P7). A IA usa o snowflake da mensagem (`dc:`); o roteador
do Discord continua sem chave e responde T18 (D5=A). Pelo `handle_incoming`, com banco real e o modelo falso.
Controles negativos: os mesmos do WhatsApp (o passo 1 e o passo 2 da `reentrega`), e `abre_turno` sem o `dc` (T18).
"""
from __future__ import annotations

import random

import core.handle_incoming as hi
from core.handlers import declaracao_banco_textos as tx
from core.services.ai_chat.tools import declaracao_ia as di
from tests._chat_ia_apoio import (CONFIRMA, FRASE, ana_ia, banco, dc, declaracoes, ia, livro,  # noqa: F401
                                  pendencia_ia, t3, uid_pro, valores)
from tests._ia_falsa_helpers import desliga_flag


def snowflake() -> str:
    return str(random.randrange(10**17, 10**19))


def test_a_ia_no_discord_arma_confirma_e_a_reentrega_e_t3(ana_ia, monkeypatch):
    desliga_flag(monkeypatch)  # o WA_IA_PRIMEIRO é só do WhatsApp; no Discord a IA entra pelo prefixo
    reg = ia(monkeypatch, banco())
    m1, s1 = snowflake(), snowflake()
    assert dc(ana_ia, "piggy " + FRASE, m1) == CONFIRMA
    assert dc(ana_ia, "piggy " + FRASE, m1) == CONFIRMA  # reentrega antes do "sim"
    assert "registrada pelo banco" in dc(ana_ia, "sim", s1)
    assert dc(ana_ia, "piggy " + FRASE, m1) == t3(1)
    assert dc(ana_ia, "sim", s1) == t3(1)
    assert valores(ana_ia) == [50.0] and len(reg.creates) == 1


def test_sim_repetido_no_discord_nao_consome_a_pendencia_nova(ana_ia, monkeypatch):
    desliga_flag(monkeypatch)
    reg = ia(monkeypatch, banco(), banco(30, alvo="padaria"))
    s1 = snowflake()
    dc(ana_ia, "piggy " + FRASE, snowflake())
    dc(ana_ia, "sim", s1)
    dc(ana_ia, "piggy gastei 30 na padaria no pix", snowflake())
    viva = pendencia_ia(ana_ia)["created_at"]
    assert dc(ana_ia, "sim", s1) == t3(1)
    assert pendencia_ia(ana_ia)["created_at"] == viva and valores(ana_ia) == [50.0] and len(reg.creates) == 2


def test_o_roteador_do_discord_segue_sem_chave(ana_ia, monkeypatch):
    desliga_flag(monkeypatch)
    ia(monkeypatch)
    assert dc(ana_ia, FRASE, snowflake()) == tx.SEM_ID
    assert declaracoes(ana_ia) == [] and livro(ana_ia) == []


def test_id_do_discord_que_nao_e_snowflake_nao_declara_pela_ia(ana_ia, monkeypatch):
    desliga_flag(monkeypatch)
    ia(monkeypatch, banco())
    assert dc(ana_ia, "piggy " + FRASE, "abc") == tx.SEM_ID
    assert pendencia_ia(ana_ia) is None and livro(ana_ia) == []


def test_o_piggy_sim_ja_visto_nao_consome_a_declaracao_viva(ana_ia, monkeypatch):
    """O "piggy sim" já visto volta com a declaração viva: a reentrega o reconhece pelo id já registrado no livro
    (desde a rodada 9, já na 1ª chamada, a do `handle_incoming`, embora o texto tenha o prefixo). O texto neutro (D-a),
    e a pendência fica intacta."""
    from tests._ia_falsa_helpers import texto
    desliga_flag(monkeypatch)
    ia(monkeypatch, texto("Não tem nada para confirmar."), banco())
    s0 = snowflake()
    dc(ana_ia, "piggy sim", s0)  # sem pendência: vai à IA e fica anotado no livro como visto
    assert dc(ana_ia, "piggy " + FRASE, snowflake()) == CONFIRMA
    viva = pendencia_ia(ana_ia)["created_at"]
    assert dc(ana_ia, "piggy sim", s0) == tx.SIM_JA_RECEBIDO
    assert pendencia_ia(ana_ia)["created_at"] == viva and valores(ana_ia) == []
    assert "registrada pelo banco" in dc(ana_ia, "sim", snowflake()) and valores(ana_ia) == [50.0]


def test_o_piggy_nao_ja_visto_mostra_a_confirmacao_com_o_negrito_do_discord(ana_ia, monkeypatch):
    """O "piggy não" já visto, com uma declaração viva, recebe a confirmação viva (4A) com o negrito do Discord: `*x*`,
    nunca o `**x**` do widget. Desde a rodada 9 quem responde é a 1ª chamada (a do `handle_incoming`, pelo id já
    registrado no livro); a plataforma que o runner passa só conta na corrida, medida no teste abaixo (M11R)."""
    desliga_flag(monkeypatch)
    ia(monkeypatch, *[banco() for _ in range(3)])
    assert dc(ana_ia, "piggy " + FRASE, snowflake()) == CONFIRMA
    n1 = snowflake()
    assert "não fiz nada" in dc(ana_ia, "piggy não", n1)
    assert dc(ana_ia, "piggy " + FRASE, snowflake()) == CONFIRMA  # W2 viva
    viva = pendencia_ia(ana_ia)["created_at"]
    r = dc(ana_ia, "piggy não", n1)  # N1 reentregue: 4A
    assert r == CONFIRMA and "**" not in r
    assert pendencia_ia(ana_ia)["created_at"] == viva and valores(ana_ia) == []


def test_a_corrida_da_mensagem_que_arma_responde_no_runner_sem_o_negrito_do_site(ana_ia, monkeypatch):
    """M11R: duas entregas da mesma mensagem, intercaladas como numa corrida real. A 2ª passa pelo 1º gancho (nada armado),
    a 1ª roda inteira e só então a 2ª chega ao `na_chegada` do runner, que responde com o `platform` do canal."""
    desliga_flag(monkeypatch)
    ia(monkeypatch, *[banco() for _ in range(6)])
    m1 = snowflake()
    gancho, na_chegada, primeira, respostas = hi._reentrega_da_declaracao, di.na_chegada, [], []

    def fura_a_fila(uid, texto, platform, msg):
        r = gancho(uid, texto, platform, msg)
        if r is None and not primeira:
            primeira.append(None)
            primeira[0] = dc(ana_ia, "piggy " + FRASE, m1)
        return r

    def anota(uid, texto, platform):
        r = na_chegada(uid, texto, platform)
        respostas.append((platform, r))
        return r
    monkeypatch.setattr(hi, "_reentrega_da_declaracao", fura_a_fila)
    monkeypatch.setattr(di, "na_chegada", anota)
    assert dc(ana_ia, "piggy " + FRASE, m1) == CONFIRMA and primeira == [CONFIRMA]
    assert respostas[-1] == ("discord", CONFIRMA)  # quem respondeu foi o runner
    assert valores(ana_ia) == []
