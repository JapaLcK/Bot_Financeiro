"""Ingestão do `/bills` da Pluggy em `of_card_bills` — banco real, Pluggy por monkeypatch.

Nada lê a tabela ainda: estes testes medem só que ela é gravada certo, que a falha
dela não derruba o sync, e que o dono é sempre conta -> conexão.

CONTROLES NEGATIVOS (rodados na entrega, ver o relato):
  • try/except por conta da leitura tirado do sync -> 2 vermelho;
  • try/except da gravação tirado do sync -> 3 vermelho;
  • `connection_id` tirado do select do upsert -> 4 vermelho.
  Paginador (pluggy_bills.py): cada cláusula do `if` de metadata e o raise do teto têm um
  caso parametrizado em `test_paginador_levanta_se_incoerente`.
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

import core.services.pluggy_bills as pb
import core.services.pluggy_sync as ps
import db
from core.services.pluggy import PluggyApiError
from db.of_card_bills import salvar_faturas_do_banco
from db.privacy import build_user_export_zip
from tests._fusao_of_helpers import uid_pro  # noqa: F401 (fixture)
from tests._of_cash_helpers import conecta, q
from tests.test_of_cartao_sinal import BANCO, CARTAO, ITEM, A, pluggy_tx

CONTA = CARTAO["id"]


def fatura(ident="bill-1", total="150.00", venc="2026-10-28T00:00:00.000Z", **extra):
    return {"id": ident, "dueDate": venc, "billClosingDate": "2026-09-03T00:00:00.000Z",
            "totalAmount": total, "totalAmountCurrencyCode": "BRL", **extra}


@pytest.fixture
def rodar(monkeypatch):
    """`rodar(uid, faturas)` = um `sync_pluggy_item` inteiro. `faturas` pode ser uma
    exceção (a Pluggy falha em `/bills`). `chamadas` guarda as contas pedidas."""
    estado = {"faturas": [], "chamadas": []}

    def bills(conta, key=None, **kw):
        estado["chamadas"].append(conta)
        if isinstance(estado["faturas"], Exception):
            raise estado["faturas"]
        return estado["faturas"]

    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ps, "get_pluggy_item", lambda i, k=None: {**ITEM, "id": i})
    monkeypatch.setattr(ps, "list_pluggy_accounts", lambda i, k=None: [BANCO, CARTAO])
    monkeypatch.setattr(ps, "list_pluggy_transactions",
                        lambda acc, k=None, **kw: [pluggy_tx(*A)] if acc == CONTA else [])
    monkeypatch.setattr(ps, "list_pluggy_investments", lambda i, k=None: [])
    monkeypatch.setattr(ps, "list_pluggy_recurring_payments", lambda i, k=None: [])
    monkeypatch.setattr(ps, "list_pluggy_bills", bills)

    def _rodar(uid, faturas):
        item = f"item-bills-{uid}"
        if not q("select 1 from open_finance_connections where user_id=%s and provider_item_id=%s",
                 (uid, item), True):
            conecta(uid, item)
        estado["faturas"] = faturas
        return ps.sync_pluggy_item(item)
    _rodar.chamadas = estado["chamadas"]
    return _rodar


def gravadas(uid):
    """[(conta da Pluggy, id da fatura, total)] do usuário, pelo join conta -> conexão."""
    return [(r["pa"], r["pb"], r["t"]) for r in q(
        "select a.provider_account_id pa, b.provider_bill_id pb, b.total_amount t from of_card_bills b "
        "join open_finance_accounts a on a.id=b.account_id join open_finance_connections c "
        "on c.id=a.connection_id where c.user_id=%s order by b.provider_bill_id", (uid,), True)]


def carimbado(uid):
    return q("select last_sync_at from open_finance_connections where user_id=%s", (uid,), True)[0]["last_sync_at"]


def test_grava_so_do_cartao_e_o_segundo_sync_atualiza(uid_pro, rodar):
    rodar(uid_pro, [fatura("b1"), fatura("b2", "80.50")])
    assert gravadas(uid_pro) == [(CONTA, "b1", 150), (CONTA, "b2", 80.5)]
    assert rodar.chamadas == [CONTA]            # a conta BANK não vai ao /bills
    row = q("select b.due_date, b.closing_date, b.currency from of_card_bills b join open_finance_accounts a on a.id=b.account_id "
            "join open_finance_connections c on c.id=a.connection_id where c.user_id=%s "
            "and b.provider_bill_id='b1'", (uid_pro,), True)[0]
    assert (str(row["due_date"]), str(row["closing_date"]), row["currency"]) == ("2026-10-28", "2026-09-03", "BRL")

    rodar(uid_pro, [fatura("b1", "175.00"), fatura("b2", "80.50")])
    assert gravadas(uid_pro) == [(CONTA, "b1", 175), (CONTA, "b2", 80.5)]


def test_falha_do_bills_na_conta_nao_derruba_o_sync_nem_apaga(uid_pro, rodar):
    rodar(uid_pro, [fatura("b1")])
    r = rodar(uid_pro, PluggyApiError("429"))
    assert r["ok"] is True and carimbado(uid_pro) is not None
    assert gravadas(uid_pro) == [(CONTA, "b1", 150)]
    assert q("select count(*) n from open_finance_transactions t join open_finance_accounts a "
             "on a.id=t.account_id join open_finance_connections c on c.id=a.connection_id "
             "where c.user_id=%s", (uid_pro,), True)[0]["n"] == 1


def test_falha_ao_gravar_as_faturas_nao_impede_o_carimbo(uid_pro, rodar, monkeypatch):
    def quebra(*a, **k):
        raise RuntimeError("banco")
    monkeypatch.setattr(ps, "salvar_faturas_do_banco", quebra)
    r = rodar(uid_pro, [fatura("b1")])
    assert r["ok"] is True and carimbado(uid_pro) is not None
    assert gravadas(uid_pro) == []


def test_isolamento_mesmo_id_em_dois_usuarios(uid_pro, rodar):
    outro = uid_pro + 1
    db.ensure_user(outro)
    rodar(uid_pro, [fatura("b1", "10")])
    rodar(outro, [fatura("b1", "99")])         # mesmo provider_account_id e mesmo provider_bill_id
    assert gravadas(uid_pro) == [(CONTA, "b1", 10)]
    assert gravadas(outro) == [(CONTA, "b1", 99)]
    dados = json.loads(zipfile.ZipFile(io.BytesIO(build_user_export_zip(uid_pro))).read("dados.json"))["dados"]
    assert [float(f["total_amount"]) for f in dados["faturas_cartao_open_finance"]] == [10]

    db.disconnect_open_finance_connection(uid_pro)
    assert gravadas(uid_pro) == [] and gravadas(outro) == [(CONTA, "b1", 99)]
    # o reset entra pelo `_OF_JOINS` de tests/test_account_reset.py (a conexão cai em cascata)


def _conta(conn, *ids):
    db.save_open_finance_sync(conn, [{"provider_account_id": i, "name": "C", "type": "CREDIT", "currency": "BRL",
                                      "balance": 0, "raw": {}, "transactions": []} for i in ids])


def test_fatura_invalida_e_pulada_e_as_outras_gravam(uid_pro, capsys):
    conn = conecta(uid_pro, "item-val")
    _conta(conn, CONTA, "acc-outra")
    ruins = [{"dueDate": "2026-10-28", "totalAmount": 1, "segredo": "CPF123"}, fatura("sem-venc", venc=None),
             fatura("venc-ruim", venc="31/10/2026"), fatura("nan", total="NaN"), "lixo"]
    salvar_faturas_do_banco(conn, {CONTA: ruins + [fatura("ok")]})
    assert gravadas(uid_pro) == [(CONTA, "ok", 150)]
    # conta só com lixo (formato mudou): pulada, a outra conta do item grava
    salvar_faturas_do_banco(conn, {CONTA: ruins, "acc-outra": [fatura("o2", "7")]})
    saida = capsys.readouterr().out                  # o log nomeia a conta e a contagem, nunca o raw
    assert f"[of_card_bills] conta={CONTA} sem nenhuma fatura válida" in saida and "CPF123" not in saida
    salvar_faturas_do_banco(conn, {CONTA: []})  # vazio lido é vazio
    assert sorted(gravadas(uid_pro)) == [(CONTA, "ok", 150), ("acc-outra", "o2", 7)]


def test_duas_contas_credit_uma_falha_a_outra_grava_e_a_falha_nao_perde(uid_pro, rodar, monkeypatch):
    outra = {**CARTAO, "id": "acc-outra"}
    monkeypatch.setattr(ps, "list_pluggy_accounts", lambda i, k=None: [CARTAO, outra])
    rodar(uid_pro, [fatura("velha")])                       # as duas contas leem "velha"
    assert sorted(gravadas(uid_pro)) == [(CONTA, "velha", 150), ("acc-outra", "velha", 150)]

    def bills(conta, key=None, **kw):
        if conta == CONTA:
            raise PluggyApiError("429")
        return [fatura("nova", "9")]
    monkeypatch.setattr(ps, "list_pluggy_bills", bills)
    rodar(uid_pro, None)
    assert sorted(gravadas(uid_pro)) == [(CONTA, "velha", 150), ("acc-outra", "nova", 9), ("acc-outra", "velha", 150)]


def test_sync_passa_o_heartbeat_ao_ler_as_faturas(uid_pro, rodar, monkeypatch):
    eventos = []
    monkeypatch.setattr(ps, "_hold_aggregate_emails", lambda *a: eventos.append("hb"))

    def bills(conta, key=None, on_page=None):
        eventos.append("bills")
        on_page()
        on_page()
        return []
    monkeypatch.setattr(ps, "list_pluggy_bills", bills)
    rodar(uid_pro, None)
    i = eventos.index("bills")
    assert eventos[i + 1:i + 3] == ["hb", "hb"]


def _paginas(monkeypatch, resposta):
    """Pluggy honesta: ecoa o `page` pedido; `resposta(page)` dá o resto (e pode sobrescrever `page`)."""
    pedidos = []

    def get(path, key, params=None):
        pedidos.append(params["page"])
        return {"page": params["page"], **resposta(params["page"])}
    monkeypatch.setattr(pb, "_pluggy_get", get)
    return pedidos


def test_paginador_le_todas_as_paginas_e_chama_o_heartbeat_por_pagina(monkeypatch):
    pedidos = _paginas(monkeypatch, lambda p: {"totalPages": 2, "results": [{"id": "ab"[p - 1]}]})
    eventos, get = [], pb._pluggy_get
    monkeypatch.setattr(pb, "_pluggy_get", lambda *a, **k: (eventos.append("get"), get(*a, **k))[1])
    assert pb.list_pluggy_bills("x", "k", on_page=lambda: eventos.append("hb")) == [{"id": "a"}, {"id": "b"}]
    assert pedidos == [1, 2] and eventos == ["hb", "get", "hb", "get"]   # heartbeat ANTES de cada página
    _paginas(monkeypatch, lambda p: {"totalPages": 0, "results": []})
    assert pb.list_pluggy_bills("x", "k") == []             # conta sem faturas não é erro


def test_heartbeat_que_levanta_nao_interrompe_a_leitura(monkeypatch):
    _paginas(monkeypatch, lambda p: {"totalPages": 1, "results": [{"id": "a"}]})
    def quebra():
        raise RuntimeError("heartbeat")             # não ZeroDivisionError: o `except` tem de ser amplo
    assert pb.list_pluggy_bills("x", "k", on_page=quebra) == [{"id": "a"}]


@pytest.mark.parametrize("resposta, max_pages", [
    (lambda p: {"totalPages": 99, "results": [{"id": p}]}, 3),                       # teto: levanta, não trunca
    (lambda p: {"totalPages": 2 if p == 1 else 3, "results": [{"id": p}]}, 20),      # o total muda no meio
    (lambda p: {"totalPages": 0, "results": [{"id": p}]}, 20),                       # resultado além do total
    (lambda p: {"totalPages": -1, "results": []}, 20),                               # total negativo
    (lambda p: {"results": []}, 20),                                                 # sem totalPages
    (lambda p: {"page": 7, "totalPages": 2, "results": []}, 20),                     # eco de outra página
    (lambda p: {"total": 2, "totalPages": 1, "results": [{"id": p}]}, 20),           # lista parcial como completa
    (lambda p: {"total": 2 if p == 1 else 3, "totalPages": 2, "results": [{"id": p}]}, 20),  # total muda no meio
    (lambda p: {"total": "x", "totalPages": 1, "results": []}, 20),                  # total inválido
    (lambda p: {"total": -1, "totalPages": 1, "results": []}, 20),                   # total negativo
    (lambda p: {"total": 1, "totalPages": 1, "results": [{"id": 1}, {"id": 2}]}, 20),  # a mais que o total
])
def test_paginador_levanta_se_incoerente(monkeypatch, resposta, max_pages):
    _paginas(monkeypatch, resposta)
    with pytest.raises(PluggyApiError):
        pb.list_pluggy_bills("x", "k", max_pages=max_pages)


def test_paginador_com_total_que_bate_e_sem_total_passa(monkeypatch):
    _paginas(monkeypatch, lambda p: {"total": 2, "totalPages": 2, "results": [{"id": p}]})
    assert pb.list_pluggy_bills("x", "k") == [{"id": 1}, {"id": 2}]
    _paginas(monkeypatch, lambda p: {"totalPages": 2, "results": [{"id": p}]})      # sem total: como antes
    assert pb.list_pluggy_bills("x", "k") == [{"id": 1}, {"id": 2}]
