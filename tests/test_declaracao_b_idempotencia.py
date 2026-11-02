"""PR-B (ADR 0006), G-idempotência: a chave identifica a AÇÃO (a mensagem, o wamid), nunca o conteúdo.

Mesmo wamid duas vezes = UMA declaração (a segunda diz "já registrei"); duas mensagens iguais com wamids diferentes =
DUAS (nenhum dedupe por valor, descrição ou dia); sem wamid utilizável não declara. O `_SEEN` do runtime (memória,
180 s) NÃO está no caminho: estes testes chamam `handle_incoming` direto, que é o que vale depois do TTL ou de um deploy.

Controles negativos: o escritor ignorando `chave_acao` (sem `external_id` e sem a checagem) deixa vermelhos R2, R5 e a
fila (R6 e R7, duas declarações num turno, deixaram de existir com o E1 da #1310: um gasto por mensagem); um dedupe por conteúdo no lugar da chave deixa vermelho `test_mesmo_texto_wamids_diferentes_sao_duas`
(R3, o controle exigido pela ADR). Positivos: R3 e a primeira entrega de cada caso.
"""
from __future__ import annotations

import uuid

import pytest

import db
from conftest import promote_to_pro
from core.handlers import declaracao_banco_textos as tx
from core.types import IncomingMessage
from tests._declaracao_b import (ana, carteira, declaracoes, fala, liga, manda_wamid, n_lancamentos,  # noqa: F401
                                 pendencia, q, sem_pergunta)
from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401
from tests.test_bank_movements import _bank
import core.handle_incoming as hi

FRASE = "gastei 50 no mercado no pix"


def _t3(seq):
    return tx.REPETIDA.format(seq=seq)


def test_mesmo_wamid_uma_declaracao(ana, liga):
    w = "wamid.R2-" + uuid.uuid4().hex
    assert "registrada pelo banco" in manda_wamid(ana, FRASE, w)[0]
    db.clear_pending_action(ana)
    assert manda_wamid(ana, FRASE, w) == [_t3(1)]
    assert len(declaracoes(ana)) == 1 and carteira(ana) == 200.0
    assert pendencia(ana) is None  # a reentrega não arma "categoria errada?", oferta nem nudge


def test_mesmo_texto_wamids_diferentes_sao_duas(ana, liga):
    """R3: duas compras iguais são dois gastos (a ADR proíbe suprimir por conteúdo)."""
    manda_wamid(ana, FRASE)
    manda_wamid(ana, FRASE)
    assert len(declaracoes(ana)) == 2 and carteira(ana) == 200.0


def test_resposta_reentregue_nao_grava_duas_vezes(ana, liga):
    manda_wamid(ana, "gastei 50 no mercado")
    w = "wamid.R5-" + uuid.uuid4().hex
    assert "registrada pelo banco" in manda_wamid(ana, "banco", w)[0]
    r = manda_wamid(ana, "banco", w)
    assert len(declaracoes(ana)) == 1 and "registrada pelo banco" not in r[0], r


def test_duas_frases_recusam_sem_consumir_chave(ana, liga):
    """E1 (#1310): dois gastos numa mensagem não declaram (T16), e a reentrega do mesmo wamid recusa igual. A ordem `<n>` da
    chave segue no código, mas com um gasto por mensagem ela é sempre 0 (o cenário "frase 0 gravou, frase 1 estourou"
    deixou de existir)."""
    w = "wamid.R6-" + uuid.uuid4().hex
    for _ in range(2):
        assert "um gasto por mensagem" in manda_wamid(ana, "gastei 50 no mercado e 30 na padaria no pix", w)[0]
    assert declaracoes(ana) == []


def test_fila_reentregue_devolve_o_item_em_vez_de_perde_lo(ana, liga, monkeypatch):
    """A reentrega da RESPOSTA de valor tentaria gastar o item seguinte da fila com a chave do anterior: vira T3 e o
    item volta, em vez de sumir."""
    fala(monkeypatch, ana, "paguei o aluguel e paguei a academia no pix")
    assert pendencia(ana) == "multi_launch_values"
    w = "wamid.fila-" + uuid.uuid4().hex
    r = manda_wamid(ana, "1500", w)[0]
    assert "registrada pelo banco" in r and "academia" in r
    r = manda_wamid(ana, "1500", w)[0]
    assert "já tinha registrado" in r and "academia" in r, r
    assert len(declaracoes(ana)) == 1
    assert "registrada pelo banco" in manda_wamid(ana, "200")[0]
    assert sorted(float(d["valor"]) for d in declaracoes(ana)) == [200.0, 1500.0]


def _com_id(uid, texto, message_id, platform="whatsapp"):
    out = hi.handle_incoming(IncomingMessage(platform=platform, user_id=uid, text=texto, message_id=message_id,
                                             attachments=[], external_id=str(uid), raw={}))
    return out[0].text


@pytest.mark.parametrize("message_id,platform", [
    (None, "whatsapp"), ("", "whatsapp"), ("1", "whatsapp"), ("1761500000", "whatsapp"),  # fallback por timestamp
    ("wamid.abc", "discord"),
])
def test_sem_wamid_utilizavel_nao_declara(ana, liga, message_id, platform):
    """R11: nunca timestamp nem hash do corpo como chave (duas mensagens no mesmo segundo se suprimiriam)."""
    assert _com_id(ana, FRASE, message_id, platform) == tx.SEM_GARANTIA
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 0


def test_sem_wamid_na_fila_o_item_volta(ana, liga, monkeypatch):
    fala(monkeypatch, ana, "paguei o aluguel e paguei a academia no pix")
    r = _com_id(ana, "1500", "1761500000")
    assert tx.SEM_GARANTIA in r and "aluguel" in r, r
    assert declaracoes(ana) == []
    fila = db.get_pending_action(ana)["payload"]["queue"]
    assert [i["desc"].split()[0] for i in fila] == ["aluguel", "academia"]
    assert "registrada pelo banco" in manda_wamid(ana, "1500")[0]


def test_consulta_do_banco_caindo_na_fila_devolve_o_item(ana, liga, monkeypatch):
    fala(monkeypatch, ana, "paguei o aluguel e paguei a academia no pix")

    def cai(_uid):
        raise RuntimeError("DB caiu")
    monkeypatch.setattr(db, "has_open_finance_connections", cai)
    assert tx.SEM_GARANTIA in manda_wamid(ana, "1500")[0]
    assert len(db.get_pending_action(ana)["payload"]["queue"]) == 2 and declaracoes(ana) == []


def test_o_wamid_cru_nunca_vai_para_a_coluna_nem_para_o_log(ana, liga, caplog):
    caplog.set_level("DEBUG")
    w = "wamid.SEGREDO-" + uuid.uuid4().hex
    manda_wamid(ana, FRASE, w)
    manda_wamid(ana, FRASE, w)  # a reentrega loga `declaracao_repetida`
    (d,) = declaracoes(ana)
    assert w not in d["external_id"] and "SEGREDO" not in d["external_id"]
    assert "declaracao_repetida" in caplog.text and "SEGREDO" not in caplog.text


def test_isolamento_a_mesma_chave_em_dois_usuarios_sao_dois_lancamentos(ana, liga):
    outro = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(outro)
    promote_to_pro(outro)
    _bank(outro)
    w = "wamid.iso-" + uuid.uuid4().hex
    manda_wamid(ana, FRASE, w)
    assert "registrada pelo banco" in manda_wamid(outro, FRASE, w)[0]
    assert len(declaracoes(ana)) == 1 and len(declaracoes(outro)) == 1
    assert declaracoes(ana)[0]["external_id"] == declaracoes(outro)[0]["external_id"]


def test_depois_de_foi_em_dinheiro_a_reentrega_nao_recria(ana, liga):
    """A declaração convertida CONSERVA o `external_id` (a linha é reaproveitada): a reentrega do wamid original
    não cria um segundo gasto."""
    from db.bank_dinheiro import foi_em_dinheiro
    w = "wamid.conv-" + uuid.uuid4().hex
    manda_wamid(ana, FRASE, w)
    foi_em_dinheiro(ana, declaracoes(ana)[0]["id"])
    assert carteira(ana) == 150.0 and n_lancamentos(ana) == 1
    assert "já tinha registrado" in manda_wamid(ana, FRASE, w)[0]
    assert n_lancamentos(ana) == 1 and carteira(ana) == 150.0


def test_apagar_libera_a_chave_limite_declarado(ana, liga):
    """R12/DP-7: apagar a declaração leva a chave junto; uma reentrega DEPOIS do apagar recria (a Meta só reentrega
    sem 200, e o webhook responde 200 antes de processar). Documenta o limite aceito pelo dono."""
    w = "wamid.r12-" + uuid.uuid4().hex
    manda_wamid(ana, FRASE, w)
    db.delete_launch_and_rollback(ana, declaracoes(ana)[0]["id"])
    assert declaracoes(ana) == []
    assert "registrada pelo banco" in manda_wamid(ana, FRASE, w)[0]
    assert len(declaracoes(ana)) == 1


# ── o escritor ──────────────────────────────────────────────────────────────

def _escreve(uid, chave, **kw):
    return db.add_launch_and_update_balance(uid, "despesa", 50, "mercado", "n", "mercado", None,
                                            declaracao_banco=True, chave_acao=chave, **kw)


def test_escritor_repetida_nao_grava_nem_mexe_no_saldo(ana):
    _escreve(ana, "wa:x:0")
    antes = (n_lancamentos(ana), carteira(ana))
    with pytest.raises(db.bank_declaracoes.DeclaracaoRepetida) as e:
        _escreve(ana, "wa:x:0")
    assert e.value.user_seq == 1 and (n_lancamentos(ana), carteira(ana)) == antes
    assert q("select count(*) n from bank_movement_declarations where user_id=%s", (ana,))["n"] == 1
    _escreve(ana, "wa:x:1")  # outra ordem no turno = outra ação


def test_escritor_indice_unico_e_a_segunda_defesa(ana, monkeypatch):
    """Se o `select` sob o mutex não achar (o mutex mudou um dia), o índice único pega e vira o mesmo erro."""
    _escreve(ana, "wa:y:0")
    import db.accounts as acc
    real, vezes = acc.chave_existente, []
    monkeypatch.setattr(acc, "chave_existente",
                        lambda cur, uid, chave: real(cur, uid, chave) if vezes else vezes.append(1))  # cega só a 1ª
    with pytest.raises(db.bank_declaracoes.DeclaracaoRepetida):
        _escreve(ana, "wa:y:0")
    assert n_lancamentos(ana) == 1


def test_violacao_de_outra_constraint_nao_vira_ja_registrei(ana, monkeypatch):
    """B1-13: só o índice da chave de ação vira `DeclaracaoRepetida`; a chave existe, mas a violação é de OUTRO índice."""
    from types import SimpleNamespace
    from psycopg.errors import UniqueViolation
    import db.accounts as acc

    class Outra(UniqueViolation):
        diag = SimpleNamespace(constraint_name="uq_launches_user_seq")
    _escreve(ana, "wa:o:0")
    real, vezes = acc.chave_existente, []

    def cega_e_estoura(cur, uid, chave):
        if not vezes:
            vezes.append(1)
            raise Outra("duplicate key")
        return real(cur, uid, chave)
    monkeypatch.setattr(acc, "chave_existente", cega_e_estoura)
    with pytest.raises(Outra):
        _escreve(ana, "wa:o:0")
    assert n_lancamentos(ana) == 1


def test_chave_sem_declaracao_e_recusada(ana):
    with pytest.raises(ValueError, match="DECLARACAO_ARGS_INCOMPATIVEIS"):
        db.add_launch_and_update_balance(ana, "despesa", 50, "m", "n", "mercado", chave_acao="wa:z:0")
    assert n_lancamentos(ana) == 0


def test_carteira_sem_chave_continua_gravando_varias_iguais(ana):
    for _ in range(2):
        db.add_launch_and_update_balance(ana, "despesa", 50, "mercado", "n", "mercado")
    assert n_lancamentos(ana) == 2 and carteira(ana) == 100.0


def test_pelo_runtime_do_whatsapp_na_thread_do_worker(ana, liga, monkeypatch):
    """P3: `process_message` (o que o worker chama em `asyncio.to_thread`) lê o wamid de `raw["id"]`, o `handle_incoming`
    abre o turno na PRÓPRIA thread (nada a herdar) e a reentrega depois do TTL do `_SEEN` vira "já registrei".
    O que isto não prova: o formato do id que a Meta manda (o prefixo `wamid.` é premissa do plano)."""
    import asyncio
    import adapters.whatsapp.wa_runtime as wr
    from adapters.whatsapp.wa_parse import InboundMessage

    respostas = []
    for nome, f in {
        "get_or_create_canonical_user": lambda p, e: ana,
        "attempt_whatsapp_phone_link": lambda wa_id, current_user_id=None: {"status": "already_linked", "user_id": ana},
        "log_system_event_sync": lambda *a, **k: None,
        "send_typing_indicator": lambda *a, **k: None,
        "_seen_recent": lambda m: False,  # passou o TTL de 180 s (ou o processo reiniciou)
        "_send_reply": lambda to, body, *a, **k: respostas.append(body),
        "_send_reply_with_optional_buttons": lambda to, body, user_id=None: respostas.append(body),
        "_bloqueado_pelo_corte": lambda *a: False,
    }.items():
        monkeypatch.setattr(wr, nome, f)
    wamid = "wamid.HBgLNTUxMTk5OTk5ODg4FQIAEhgg" + uuid.uuid4().hex
    msg = InboundMessage(wa_id="5511999998888", text=FRASE, timestamp="1761500000", attachments=[],
                         raw={"id": wamid, "type": "text"})
    asyncio.run(asyncio.to_thread(wr.process_message, msg))
    asyncio.run(asyncio.to_thread(wr.process_message, msg))
    assert "registrada pelo banco" in respostas[0] and "já tinha registrado" in respostas[1], respostas
    assert len(declaracoes(ana)) == 1 and wamid not in declaracoes(ana)[0]["external_id"]
