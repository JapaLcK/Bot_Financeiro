"""PR C2 (Metas): "minhas metas" no WhatsApp, leitura determinística. K = quem tem a chave
`dashboard_v2_enabled` (P1-b), N = quem não tem. O pytest NÃO fala com o modelo (tier 3 e envio falsos):
prova roteamento, estado de pendência e texto, não a interpretação real.
Controles (cada um injetado e restaurado, ver o relato do PR): `_pede_metas` sempre False -> `test_k_*` e as
conversas de K; sem `_metas_ligadas` no gate -> `test_n_*`/`test_conversa_c_*`; `metas.list` fora do `ABANDONA` ->
`test_conversa_a_*`; sem o veto de catálogo -> `test_conversa_b_*`; sem `wrap_wa_markup`/sem corte -> `test_mensagem_*`;
`ler` do vizinho -> `test_isolamento_*`; `metas.list` no `_INTENTS_DO_ROTEADOR` -> `test_com_a_flag_*`.
"""
from __future__ import annotations

import re

import pytest

import core.handle_incoming as hi
import core.intent_classifier as ic
import db
from conftest import usuario_pagante
from core.handlers.launches import _WPP_MAX_CHARS, _wpp_len
from core.intent_classifier import IntentResult, classify
from core.services import metas_conversa as mc
from core.types import IncomingMessage
from db import metas as dbmetas
from tests._patrimonio_helpers import conexao, posicao
from tests._pendencia_credito_helpers import AVISO, arma_installment, diga as diga_cartao, novo_uid
from tests.test_ai_chat_metas import FIX, meta_de, nova

FRASES_K = [
    "metas", "minhas metas", "ver metas", "ver minhas metas", "listar metas", "lista minhas metas", "Minhas Metas?",
    "como estão minhas metas", "como andam as metas", "como vão as minhas metas?", "quanto falta para as metas",
]
# O que o classificador dizia na `main` (436f7d32, `allow_ai=False`) e tem de continuar dizendo para
# TODOS: "meta"/"reserva" sozinhos podem ser nome de caixinha, e o resto é saque, depósito ou caixinha.
COMO_ANTES = {
    "meta": ("out_of_scope", 0.0), "reserva": ("out_of_scope", 0.0), "meta 2028": ("out_of_scope", 0.0),
    "quanto falta pra minha meta 2028": ("out_of_scope", 0.0),
    "reserva de emergencia 5000": ("out_of_scope", 0.0),
    "saquei 200 da reserva de emergência": ("funds.withdraw", 0.95),
    "saquei 200 da reserva": ("funds.withdraw", 0.95),
    "guardei 100 na meta 2028": ("out_of_scope", 0.0),
    "guardei 100 na reserva": ("out_of_scope", 0.0),
    "criar caixinha reserva": ("pockets.create", 0.95),
    "excluir caixinha metas": ("pockets.delete", 0.95),
    "apagar caixinha meta": ("pockets.delete", 0.95),
    "deposita 200 na caixinha metas": ("pockets.deposit", 0.95),
    "retirei 100 da caixinha reserva": ("pockets.withdraw", 0.95),
    "minhas caixinhas": ("pockets.list", 1.0), "caixinhas": ("pockets.list", 1.0),
    "saldo": ("balance.check", 1.0), "gastei 50 no mercado": ("launches.add", 0.95),
}  # "apaga a meta Casa nova" e "criar meta viagem" saíram: para K viraram `metas.escrita` (C4, tests/test_wa_metas_escrita.py)


@pytest.fixture
def chave(monkeypatch):
    """`chave(*uids)`: só estes têm a chave do /painel novo."""
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")

    def _liga(*uids):
        monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", ",".join(map(str, uids)))
    _liga()
    return _liga


@pytest.fixture
def tier3(monkeypatch):
    """O tier 3 (LLM) falso: `fala` é o que ele devolve; `chamadas` conta as idas a ele."""
    est = {"fala": IntentResult(intent="out_of_scope", confidence=0.0), "chamadas": []}

    def falso(text, user_id=None):
        est["chamadas"].append(text)
        return est["fala"]
    monkeypatch.setattr(ic, "_classify_with_ai", falso)
    return est


def diga(uid: int, *textos: str) -> list[str]:
    saidas = []
    for t in textos:
        outs = hi.handle_incoming(IncomingMessage(platform="whatsapp", user_id=uid, text=t, message_id="1",
                                                  attachments=[], external_id=str(uid), raw={}))
        saidas.append(" ".join(o.text or "" for o in (outs or [])))
    return saidas


def com_meta(chave, nome="Viagem A", alvo="1000", saldo="300"):
    k = usuario_pagante(plan="pro_max")
    chave(k)
    nova(k, nome, alvo, saldo)
    return k


def sem_pendencia(uid):
    return db.get_pending_action(uid) is None


# ── classificador (allow_ai=False): K × N ───────────────────────────────────────────────────────

@pytest.mark.parametrize("frase", FRASES_K)
def test_k_as_frases_de_metas_dao_metas_list(chave, frase):
    k = usuario_pagante(plan="pro_max")
    chave(k)
    r = classify(frase, user_id=k, allow_ai=False)
    assert (r.intent, r.confidence) == ("metas.list", 1.0)


@pytest.mark.parametrize("frase", FRASES_K)
def test_n_as_mesmas_frases_dao_o_que_davam_antes(chave, frase):
    """N: com `user_id` (conta sem a chave) e sem ele, o resultado é o de antes das metas."""
    n = usuario_pagante(plan="pro_max")
    chave()
    sem = classify(frase, allow_ai=False)  # sem `user_id` a regra nem existe
    assert sem.intent != "metas.list" and classify(frase, user_id=n, allow_ai=False) == sem


@pytest.mark.parametrize("frase,esperado", COMO_ANTES.items())
def test_o_resto_do_corpus_continua_como_na_main_para_k_e_n(chave, frase, esperado):
    k, n = usuario_pagante(plan="pro_max"), usuario_pagante(plan="pro_max")
    chave(k)
    for quem in (None, n, k):
        r = classify(frase, user_id=quem, allow_ai=False)
        assert (r.intent, r.confidence) == esperado, (frase, quem)


def test_falha_da_chave_vira_o_comportamento_de_antes(chave, monkeypatch):
    k = usuario_pagante(plan="pro_max")
    chave(k)

    def quebra(_uid):
        raise RuntimeError("banco fora")
    monkeypatch.setattr(mc, "metas_ligadas", quebra)
    assert classify("minhas metas", user_id=k, allow_ai=False).intent == "out_of_scope"


# ── a conversa (handle_incoming, banco real) ────────────────────────────────────────────────────

def test_conversa_k_minhas_metas_sem_llm_e_sem_ia(chave, tier3, monkeypatch):
    """(d) o caminho não depende do LLM: nem o tier 3 nem a IA conversacional são chamados."""
    k = com_meta(chave)
    monkeypatch.setattr(hi, "_resposta_da_ia", lambda *a, **kw: pytest.fail("a IA não devia ser chamada"))
    (r,) = diga(k, "minhas metas")
    assert tier3["chamadas"] == []
    assert r.startswith("🎯 *Suas metas*") and "*Viagem A*" in r
    assert "R$ 300,00 de R$ 1.000,00 (30%), faltam R$ 700,00." in r and r.endswith(mc.ONDE_CRIAR)
    assert "**" not in r and not any(l.startswith("#") for l in r.splitlines())


def test_conversa_a_minhas_metas_abandona_a_pergunta_e_o_resto_segue(chave, tier3):
    """(a) "saquei 200" pergunta de qual caixinha; "minhas metas" é outro comando: lista e abandona.
    Depois um gasto comum segue normal."""
    k = com_meta(chave)
    r0, r1 = diga(k, "saquei 200", "minhas metas")
    assert "retirar de qual" in r0
    assert "Suas metas" in r1 and "Não achei" not in r1 and sem_pendencia(k)
    r2, r3 = diga(k, "gastei 50 no mercado", "dinheiro")  # K tem banco: a conversa normal pergunta a forma
    assert "dinheiro vivo ou" in r2 and "Suas metas" not in r2 + r3
    assert [float(x["valor"]) for x in db.list_launches(k, limit=5) if x["tipo"] == "despesa"] == [50.0]


def test_conversa_b_caixinha_chamada_metas_vence_o_comando_na_pergunta_de_nome(chave, tier3):
    """(b) pergunta de nome aberta + "metas": é a caixinha (veto de catálogo), não o comando."""
    k = com_meta(chave)
    db.add_launch_and_update_balance(k, "receita", 500.0, alvo="salário", nota="setup",
                                     categoria="salário", is_internal_movement=False)
    db.create_pocket(k, "metas")
    db.pocket_deposit_from_account(k, "metas", 300.0)
    r0, r1 = diga(k, "saquei 200", "metas")
    assert "retirar de qual" in r0 and "Suas metas" not in r1
    assert {p["name"]: float(p["balance"]) for p in db.list_pockets(k)}["metas"] == 100.0


def test_conversa_b_sem_pergunta_aberta_o_comando_lista_as_metas(chave, tier3):
    """O oposto de (b): sem pendência, "minhas metas" lista, mesmo com caixinha chamada "metas"."""
    k = com_meta(chave)
    db.create_pocket(k, "metas")
    (r,) = diga(k, "minhas metas")
    assert "Suas metas" in r and "*Viagem A*" in r


def test_conversa_c_n_recebe_o_que_recebia_e_a_chave_e_o_unico_motivo(chave, tier3, monkeypatch):
    """(c) N: "minhas metas" cai no tier 3 como antes (aqui o LLM falso diz pockets.list) e lista caixinhas.
    O controle: o mesmo N com as regras de metas desligadas recebe a MESMA resposta; K, não."""
    n = usuario_pagante(plan="pro_max")
    k = com_meta(chave)
    tier3["fala"] = IntentResult(intent="pockets.list", confidence=0.9)
    db.create_pocket(n, "viagem")
    (com,) = diga(n, "minhas metas")
    assert tier3["chamadas"] == ["minhas metas"] and "Suas metas" not in com and "viagem" in com
    monkeypatch.setattr(ic, "_pede_metas", lambda norm: False)
    assert diga(n, "minhas metas") == [com]
    assert "Suas metas" not in diga(k, "minhas metas")[0]  # sem a regra nem o K recebe metas
    monkeypatch.undo()


def test_conversa_c_n_com_pendencia_nao_muda(chave, tier3, monkeypatch):
    """N com "saquei 200" aberto: a resposta "minhas metas" tem o mesmo desfecho com e sem as regras."""
    n = usuario_pagante(plan="pro_max")
    chave()
    ligado = diga(n, "saquei 200", "minhas metas")
    monkeypatch.setattr(ic, "_pede_metas", lambda norm: False)
    n2 = usuario_pagante(plan="pro_max")
    assert diga(n2, "saquei 200", "minhas metas") == ligado


def test_conversa_cartao_k_minhas_metas_abandona_a_pergunta_do_parcelamento(chave, tier3):
    """As cinco pendências de cartão aceitam QUALQUER texto como resposta; `metas.list` está no
    `_ABANDONA_CREDITO` (herdado do `ABANDONA`), então "minhas metas" não vira descrição de parcelamento."""
    k = novo_uid()
    chave(k)
    nova(k, "Viagem A", "1000", "300")
    arma_installment(k)
    r = diga_cartao(k, "minhas metas")
    assert AVISO in r and "Suas metas" in r and sem_pendencia(k)
    assert "Parcelamento" not in r


def test_conversa_cartao_n_nao_muda(chave, tier3, monkeypatch):
    """N: a mesma pergunta de parcelamento tem o mesmo desfecho com e sem as regras de metas."""
    n1, n2 = novo_uid(), novo_uid()
    chave()
    arma_installment(n1)
    sem_codigo = lambda t: re.sub(r"PC[0-9A-F]{8}", "PC", t)  # noqa: E731  (código do parcelamento é aleatório)
    ligado = sem_codigo(diga_cartao(n1, "minhas metas"))
    monkeypatch.setattr(ic, "_pede_metas", lambda norm: False)
    arma_installment(n2)
    assert sem_codigo(diga_cartao(n2, "minhas metas")) == ligado and AVISO not in ligado and "Parcelamento" in ligado


def test_conversa_e_sem_metas_diz_onde_criar(chave, tier3):
    k = usuario_pagante(plan="pro_max")
    chave(k)
    (r,) = diga(k, "minhas metas")
    assert r == f"Você ainda não tem metas. {mc.ONDE_CRIAR}"


def test_conversa_f_mais_de_dez_metas(chave, tier3):
    k = usuario_pagante(plan="pro_max")
    chave(k)
    cid = conexao(k, "item-f", banco="Nubank")
    posicao(cid, "p1", "300")
    for i in range(12):
        dbmetas.criar(k, {"nome": f"Meta {i:02d}", "tipo": "meta", "alvo": "1000", "meses": None, "data_alvo": None,
                          **dbmetas.resolver_fonte(k, "investido", None, None)})
    (r,) = diga(k, "minhas metas")
    assert r.count("R$ 300,00 de R$ 1.000,00") <= 10 and "e mais 2 no painel" in r
    assert _wpp_len(r) <= _WPP_MAX_CHARS


def test_com_a_flag_ia_primeiro_o_roteador_nao_intercepta_metas(chave, tier3, monkeypatch):
    """`metas.list` não está em `_INTENTS_DO_ROTEADOR` (como `investments.list`): com a flag a IA atende
    (`list_metas` dá o mesmo texto)."""
    from core.services import wa_ia_primeiro as w
    k = usuario_pagante(plan="pro_max")
    chave(k)
    assert not w.fica_no_roteador(k, "minhas metas")
    assert w.fica_no_roteador(k, "bom dia")  # positivo: a função não manda tudo à IA


def test_hallucinacao_do_tier3_para_n_cai_em_caixinhas(chave, tier3):
    """Se o LLM do tier 3 devolver `metas.list` a quem não tem a chave, vale a lista de caixinhas."""
    n = usuario_pagante(plan="pro_max")
    chave()
    db.create_pocket(n, "viagem")
    tier3["fala"] = IntentResult(intent="metas.list", confidence=0.9)
    (r,) = diga(n, "zzz qwerty")
    assert "Suas metas" not in r and "viagem" in r


# ── isolamento ──────────────────────────────────────────────────────────────────────────────────

def test_isolamento_a_nao_ve_meta_de_b_pelo_whatsapp(chave, tier3):
    a, b = usuario_pagante(plan="pro_max"), usuario_pagante(plan="pro_max")
    chave(a, b)
    nova(a, "Casa", "1000", "300")
    nova(b, "Casa", "9999", "7777")  # mesmo nome, outro dinheiro
    ra, rb = diga(a, "minhas metas")[0], diga(b, "minhas metas")[0]
    assert "R$ 1.000,00" in ra and "9.999" not in ra and "7.777" not in ra
    assert "R$ 9.999,00" in rb and "1.000,00" not in rb


# ── a mensagem: markup, tamanho, erro ───────────────────────────────────────────────────────────

def _falso(monkeypatch, *metas):
    monkeypatch.setattr(mc, "ler", lambda uid: {"metas": list(metas), "custo_mensal": FIX["varios"]["custo_mensal"]})


@pytest.mark.parametrize("nome,linha", [
    ("a*b", "a*b"), ("a_b_ c", "a_b_ c"), ("x~y~", "x~y~"), ("cr`ase`", "cr`ase`"),  # marcação do usuário: sem negrito do bot
    ("li\nnha *2*", "li nha *2*"),  # quebra de linha não abre linha nova
    ("Viagem ✈️🇯🇵", "*Viagem ✈️🇯🇵*"), ("meta_casa_nova", "*meta_casa_nova*"), ("com\x00nul", "*comnul*"),
])
def test_mensagem_nome_do_usuario_nao_quebra_a_formatacao(monkeypatch, nome, linha):
    _falso(monkeypatch, {**meta_de("Viagem pro Japão"), "nome": nome})
    r = mc.texto_whatsapp(1)
    assert r.split("\n\n")[1].split("\n")[0] == linha and "\x00" not in r
    assert "**" not in r and "\n#" not in r


def _mais_longa():
    """A meta das fixtures cujo texto (resumo + avisos) é o maior."""
    base = FIX["varios"]
    def tam(m):
        (i,) = mc.para_conversa({"metas": [m], "custo_mensal": base["custo_mensal"]})["metas"]
        return len(i["resumo"]) + sum(map(len, i["avisos"]))
    return max(base["metas"], key=tam)


def test_mensagem_o_pior_caso_cabe_nos_4096_e_anuncia_o_que_cortou(monkeypatch):
    nome = "🇯🇵" * 30  # 60 caracteres, 4 unidades UTF-16 cada letra de bandeira
    # a mais longa das fixtures com 10 metas dá 4031 (cabe, sem folga); com todos os avisos que só acompanham o
    # número, o corte entra
    avisos = ["outra_moeda", "banco_desatualizado", "moeda_presumida", "conta_fora_do_ultimo_sync", "saldo_ausente"]
    _falso(monkeypatch, *[{**_mais_longa(), "nome": nome, "motivos": avisos} for _ in range(10)])
    r = mc.texto_whatsapp(1)
    mostradas = r.count(nome)
    assert _wpp_len(r) <= _WPP_MAX_CHARS and 1 <= mostradas < 10, (_wpp_len(r), mostradas)  # cortou de verdade
    assert f"e mais {10 - mostradas} no painel" in r and r.endswith(mc.ONDE_CRIAR)


def test_mensagem_poucas_metas_cabem_inteiras(monkeypatch):
    _falso(monkeypatch, *[{**meta_de("Viagem pro Japão"), "nome": f"M{i}"} for i in range(3)])
    r = mc.texto_whatsapp(1)
    assert all(f"*M{i}*" in r for i in range(3)) and "e mais" not in r and _wpp_len(r) <= _WPP_MAX_CHARS


def test_mensagem_falha_da_leitura_vira_texto_humano(monkeypatch):
    def quebra(uid):
        raise RuntimeError("banco fora: traceback secreto")
    monkeypatch.setattr(mc, "ler", quebra)
    r = mc.texto_whatsapp(1)
    assert r == "Não consegui ler suas metas agora. Tenta de novo daqui a pouco." and "traceback" not in r


def test_mensagem_e_o_resumo_da_conversa_sem_recalcular(chave):
    k = com_meta(chave)
    assert mc.para_conversa(mc.ler(k))["metas"][0]["resumo"] in mc.texto_whatsapp(k)


# ── DASHBOARD_V2_TODOS abre as metas para quem está fora da lista ────────────

def test_todos_conversa_gastei_e_minhas_metas_respondem_metas_fora_da_lista(chave, tier3, monkeypatch):
    """Pela conversa: com a env, K fora da lista recebe a resposta de metas; sem ela, a de hoje (N).
    Negativo: tirar o ramo da env em `dashboard_v2_enabled` deixa o primeiro `assert` vermelho."""
    chave()
    u = usuario_pagante(plan="pro_max")
    nova(u, "Viagem A", "1000", "300")
    diga(u, "gastei 50 no mercado")
    (sem,) = diga(u, "minhas metas")
    assert "Suas metas" not in sem
    monkeypatch.setenv("DASHBOARD_V2_TODOS", "1")
    (com,) = diga(u, "minhas metas")
    assert "🎯 *Suas metas*" in com and "*Viagem A*" in com  # "Cancelei a pergunta anterior" pode abrir
