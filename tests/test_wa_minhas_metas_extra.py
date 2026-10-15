"""PR C2 (Metas), passada 1 do Tester: o que o primeiro arquivo (`test_wa_minhas_metas.py`, no teto de 350
linhas) não cobre. Controles (injetados e restaurados): `_METAS_PERGUNTA`/`_METAS_EXATAS` com "reserva" e o
singular de volta -> `test_singular_*`; sem o casamento do resolvedor no veto -> `test_pendencia_*`; sem o `try`
da chave no `_execute` -> `test_chave_*`; sem o `user_id` na porta 4 -> `test_porta4_*`; `user_id` de volta no
`isencao_do_barrado` -> `test_barrado_*`; `blocos.pop(0)` -> `test_corte_*`.
"""
from __future__ import annotations

import uuid

import pytest

import adapters.whatsapp.wa_runtime as wr
import core.handle_incoming as hi
import db
from conftest import usuario_pagante
from core.handlers.launches import _WPP_MAX_CHARS, _wpp_len
from core.intent_classifier import classify
from core.intent_router import abandona_pergunta_de_valor
from core.services import metas_conversa as mc, plan_service
from tests.test_ai_chat_metas import nova
from tests.test_wa_minhas_metas import (  # noqa: F401  (fixtures importadas)
    _falso, _mais_longa, chave, com_meta, diga, sem_pendencia, tier3)

# Na `main` e hoje: nada disto é `metas.list`. Podem ser caixinha ou investimento, e o singular pede UMA meta.
SINGULARES = [
    "minha reserva", "reserva de emergencia", "reserva de emergência", "minha reserva de emergencia",
    "como está a reserva", "como anda a reserva de emergência", "quanto falta pra minha reserva",
    "quanto falta pra minha meta", "como está a meta Casa nova", "como anda a meta",
]


def com_caixinha(chave, nome, saldo="300"):
    k = usuario_pagante(plan="pro_max")
    chave(k)
    db.add_launch_and_update_balance(k, "receita", 1000.0, alvo="salário", nota="s", categoria="salário",
                                     is_internal_movement=False)
    db.create_pocket(k, nome)
    db.pocket_deposit_from_account(k, nome, float(saldo))
    return k


def saldo_de(k, nome):
    return {p["name"]: float(p["balance"]) for p in db.list_pockets(k)}[nome]


# ── achado 2: só o plural inequívoco é `metas.list` ─────────────────────────────────────────────

@pytest.mark.parametrize("frase", SINGULARES)
def test_singular_e_reserva_nao_sao_metas_list_nem_para_k(chave, frase):
    k, n = usuario_pagante(plan="pro_max"), usuario_pagante(plan="pro_max")
    chave(k)
    for quem in (None, n, k):
        r = classify(frase, user_id=quem, allow_ai=False)
        assert (r.intent, r.confidence) == ("out_of_scope", 0.0), (frase, quem)


def test_singular_k_com_caixinha_chamada_reserva_segue_para_a_ia(chave, tier3, monkeypatch):
    """K sem metas e com a caixinha "Reserva de emergência": antes do conserto ouvia "Você ainda não tem metas"."""
    k = com_caixinha(chave, "Reserva de emergência")
    ia = []
    monkeypatch.setattr(hi, "_resposta_da_ia", lambda uid, text, *a, **kw: ia.append(text) or "IA responde")
    r1, r2 = diga(k, "minha reserva de emergência", "como está a meta Casa nova")
    assert ia == ["minha reserva de emergência", "como está a meta Casa nova"]
    assert r1 == r2 == "IA responde" and "ainda não tem metas" not in r1 + r2


# ── pendência de nome: só o NOME EXATO escolhe a caixinha (decisão do dono, passada 2) ──────────
# Comando de LEITURA não mexe em dinheiro para K. O veto de catálogo é a igualdade de `eh_nome_do_catalogo`
# (caixa, acento, espaços e pontuação final), a mesma de antes das metas; contenção ("listar metas") lista.

@pytest.mark.parametrize("pergunta,resposta", [
    ("saquei 200", "ver minhas metas"), ("saquei 200", "como estão minhas metas"), ("saquei 200", "listar metas"),
    ("saquei 200", "quanto falta pras metas"), ("esvaziar caixinha", "listar metas"),
    ("esvaziar caixinha", "lista minhas metas"), ("esvaziar caixinha", "ver minhas metas"),
])
def test_pendencia_frase_de_leitura_lista_as_metas_e_nao_move_dinheiro(chave, tier3, pergunta, resposta):
    k = com_caixinha(chave, "metas")
    nova(k, "Viagem A", "1000", "300")
    r0, r1 = diga(k, pergunta, resposta)
    assert "qual" in r0.lower() and "Suas metas" in r1 and sem_pendencia(k)
    assert saldo_de(k, "metas") == 300.0


@pytest.mark.parametrize("pergunta,resposta,esperado", [
    ("saquei 200", "metas", 100.0), ("saquei 200", " Métas?! ", 100.0), ("esvaziar caixinha", "METAS", 0.0),
])
def test_pendencia_nome_exato_escolhe_a_caixinha(chave, tier3, pergunta, resposta, esperado):
    """Controle positivo: o nome exato (mesma normalização do `eh_nome_do_catalogo`) continua sendo a caixinha."""
    k = com_caixinha(chave, "metas")
    nova(k, "Viagem A", "1000", "300")
    r0, r1 = diga(k, pergunta, resposta)
    assert "qual" in r0.lower() and "Suas metas" not in r1 and saldo_de(k, "metas") == esperado


@pytest.mark.parametrize("nome", ["metas", "minhas metas", "metas 2028", "reserva e metas"])
@pytest.mark.parametrize("pergunta", ["saquei 200", "esvaziar caixinha"])
@pytest.mark.parametrize("resposta", ["listar metas", "minhas metas", "ver minhas metas"])
def test_pendencia_matriz_so_move_quando_a_resposta_e_o_nome_exato(chave, tier3, nome, pergunta, resposta):
    k = com_caixinha(chave, nome)
    nova(k, "Viagem A", "1000", "300")
    diga(k, pergunta, resposta)
    assert (saldo_de(k, nome) < 300.0) == (resposta == nome), (nome, pergunta, resposta, saldo_de(k, nome))


def test_pendencia_investimento_chamado_metas_resposta_ver_metas_lista(chave, tier3):
    """Investimento manual legado (criado antes da chave: depois dela criar e aportar são recusados)."""
    k = usuario_pagante(plan="pro_max")
    db.add_launch_and_update_balance(k, "receita", 1000.0, alvo="salário", nota="s", categoria="salário",
                                     is_internal_movement=False)
    for nome in ("Metas", "Outro"):
        db.create_investment(k, nome, 1.0, "monthly")
        db.investment_deposit_from_account(k, nome, 300.0)
    chave(k)
    nova(k, "Viagem A", "1000", "300")
    r0, r1 = diga(k, "resgatei 100", "listar metas")
    assert "qual" in r0.lower() and "Suas metas" in r1 and sem_pendencia(k)
    assert sorted(float(i["balance"]) for i in db.list_investments(k, include_lots=False)) == [300.0, 300.0]


def test_pendencia_de_origem_do_deposito_em_caixinha_e_abandonada_sem_mover_dinheiro(chave, tier3):
    """`funding_source_choice` de K existe (depósito em CAIXINHA não é bloqueado; só o aporte em investimento
    manual é): a frase de leitura a abandona como "minhas caixinhas" abandona para N, e nada anda."""
    k = com_caixinha(chave, "metas")
    db.set_pending_action(k, "funding_source_choice", {
        "amount": 500.0, "retomar": {"fluxo": "pocket_deposit", "name": "metas", "text": "coloquei 500 em metas"},
        "fontes": [{"kind": "account", "of_account_id": None, "label": "Carteira", "balance": 900.0}]})
    antes = float(db.get_balance(k))
    (r,) = diga(k, "listar metas")
    assert "ainda não tem metas" in r and sem_pendencia(k)
    assert saldo_de(k, "metas") == 300.0 and float(db.get_balance(k)) == antes


def test_pendencia_sem_caixinha_de_mesmo_nome_o_comando_abandona_e_lista(chave, tier3):
    """Positivo: sem caixinha de mesmo nome o comando abandona a pergunta, como sempre."""
    k = com_caixinha(chave, "viagem")
    nova(k, "Viagem A", "1000", "300")
    r0, r1 = diga(k, "saquei 200", "ver minhas metas")
    assert "retirar de qual" in r0 and "Suas metas" in r1 and sem_pendencia(k) and saldo_de(k, "viagem") == 300.0


# ── achado 3: a pendência "✅ Já paguei" (porta 4) vê `metas.list` como vê `pockets.list` ───────

@pytest.fixture
def porta4(monkeypatch, chave, tier3):
    k = usuario_pagante(plan="pro_max")
    chave(k)
    respostas, estado = [], {"vivo": True}
    pend = {"action_type": "bill_pay_amount", "payload": {"bill_id": 7, "name": "luz"}}
    monkeypatch.setattr("core.handlers.forma_pagamento.regra_ativa", lambda u: False)
    for nome, f in {
        "get_or_create_canonical_user": lambda p, e: k,
        "attempt_whatsapp_phone_link": lambda wa_id, current_user_id=None: {"status": "already_linked", "user_id": k},
        "log_system_event_sync": lambda *a, **kw: None, "send_typing_indicator": lambda *a, **kw: None,
        "_seen_recent": lambda m: False, "_send_reply": lambda to, body: respostas.append(body),
        "_send_reply_with_optional_buttons": lambda to, body, user_id=None: respostas.append(body),
        "get_pending_action": lambda u: pend if estado["vivo"] else None, "_bloqueado_pelo_corte": lambda *a: False,
        "consume_pending_action": lambda u, p: estado.update(vivo=False) or True,
    }.items():
        monkeypatch.setattr(wr, nome, f)

    def manda(texto):
        from adapters.whatsapp.wa_parse import InboundMessage
        estado["vivo"] = True
        respostas.clear()
        wr.process_message(InboundMessage(wa_id="5511999998888", text=texto, timestamp="2", attachments=[],
                                          raw={"id": uuid.uuid4().hex, "type": "text"}))
        return list(respostas), estado["vivo"]
    return k, manda


def test_porta4_k_minhas_metas_abandona_a_pergunta_como_minhas_caixinhas(porta4):
    k, manda = porta4
    (rc, vivo_c), (rm, vivo_m) = manda("minhas caixinhas"), manda("minhas metas")
    assert vivo_c is False and vivo_m is False and "Não peguei o valor" not in " ".join(rm)


def test_porta4_oraculo_so_enxerga_metas_com_user_id_e_n_fica_igual(chave):
    k, n = usuario_pagante(plan="pro_max"), usuario_pagante(plan="pro_max")
    chave(k)
    assert abandona_pergunta_de_valor("minhas metas", k) is True
    assert abandona_pergunta_de_valor("minhas metas", n) is False and abandona_pergunta_de_valor("minhas metas") is False
    assert abandona_pergunta_de_valor("132", k) is False and abandona_pergunta_de_valor("minhas caixinhas", n) is True


def test_portas_1_e_3_abandonam_sozinhas_com_minhas_metas(chave, tier3):
    """Limite declarado: as portas 1 (conta variável) e 3 (fila multi) não consultam o oráculo com `user_id`."""
    k = usuario_pagante(plan="pro_max")
    chave(k)
    db.set_pending_action(k, "bill_amount_expected", {"bill_name": "Luz", "bill_id": 1})
    assert "ainda não tem metas" in diga(k, "minhas metas")[0] and sem_pendencia(k)
    db.add_launch_and_update_balance(k, "receita", 1000.0, alvo="salário", nota="s", categoria="salário",
                                     is_internal_movement=False)
    diga(k, "gastei 50 no mercado e paguei o aluguel")
    assert "ainda não tem metas" in diga(k, "minhas metas")[0] and sem_pendencia(k)


# ── achado 4: a chave que quebra só na 2ª leitura ──────────────────────────────────────────────

def test_chave_que_quebra_so_no_execute_cai_na_lista_de_caixinhas(chave, tier3, monkeypatch):
    k = com_meta(chave)
    db.create_pocket(k, "viagem")
    real, n = plan_service.dashboard_v2_enabled, {"c": 0}

    def flaky(uid, email=None):
        n["c"] += 1
        if n["c"] >= 2:
            raise RuntimeError("banco fora")
        return real(uid, email)
    monkeypatch.setattr(plan_service, "dashboard_v2_enabled", flaky)
    (r,) = diga(k, "minhas metas")
    assert n["c"] >= 2 and "erro interno" not in r.lower() and "Suas metas" not in r and "viagem" in r


# ── achado 6: lacunas de teste ─────────────────────────────────────────────────────────────────

def test_barrado_o_gate_do_paywall_nao_le_a_conta_para_classificar(chave, monkeypatch):
    """`isencao_do_barrado` promete "sem DB": `classify` sem `user_id` nunca chega a `metas_ligadas`."""
    k = usuario_pagante(plan="pro_max")
    chave(k)
    lidas = []
    monkeypatch.setattr(mc, "metas_ligadas", lambda uid: lidas.append(uid) or False)
    assert hi.isencao_do_barrado("minhas metas", k)[1].intent != "metas.list"
    assert lidas == []


def test_corte_pelo_tamanho_fica_com_as_primeiras_metas_e_anuncia_as_ultimas(monkeypatch):
    avisos = ["outra_moeda", "banco_desatualizado", "moeda_presumida", "conta_fora_do_ultimo_sync", "saldo_ausente"]
    _falso(monkeypatch, *[{**_mais_longa(), "nome": f"M{i}" + "🇯🇵" * 30, "motivos": avisos} for i in range(10)])
    r = mc.texto_whatsapp(1)
    ficaram = [i for i in range(10) if f"M{i}" in r]
    assert 1 <= len(ficaram) < 10 and _wpp_len(r) <= _WPP_MAX_CHARS
    assert ficaram == list(range(len(ficaram))) and f"e mais {10 - len(ficaram)} no painel" in r
