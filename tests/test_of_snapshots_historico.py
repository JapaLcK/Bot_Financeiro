"""Foto diária por posição do Open Finance (`open_finance_investment_snapshots`).

Pelo caminho real (`sync_pluggy_item` com a Pluggy falsa, Postgres real), no
molde de tests/test_of_connection_state.py, de onde vêm os helpers.

CONTROLES (CLAUDE.md §3), medidos:
  • tirar a chamada de `grava_fotos_posicoes` em `save_open_finance_investments`
    → T1, T2 e T7 vermelhos;
  • tirar o `where` do `on conflict` em db/of_snapshots.py → T3 vermelho;
  • trocar `now.date()` pela data em UTC → T6 vermelho;
  • tirar o SAVEPOINT (`with conn.transaction():`) deixando só o try → T13 vermelho.
POSITIVOS: T3 (a confirmada do dia seguinte sobrescreve a não confirmada), T13
(o espelho atualiza quando só o histórico falha), T15/T14b (quem não pode
gravar não grava, e o T1 prova que quem pode grava).
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from psycopg.types.json import Jsonb

import core.services.pluggy_sync as ps
import db
import db.privacy as privacy
from core.services.pluggy import PluggyApiError
from db.connection import get_conn
from db.of_snapshots import _numero
from db.open_finance import _SQL_PROFIT
from test_of_connection_state import (ITEM_SAUDAVEL, _conexao, _conta_pluggy, _espelho,
                                      _mock_pluggy, _set_estado, _tx_pluggy)
from utils_date import _tz

RELOGIO = {"agora": datetime(2026, 9, 28, 12, 0, tzinfo=_tz())}


class _Relogio(datetime):
    @classmethod
    def now(cls, tz=None):
        return RELOGIO["agora"] if tz is None else RELOGIO["agora"].astimezone(tz)


@pytest.fixture(autouse=True)
def relogio(monkeypatch):
    for modulo in ("core.services.pluggy_sync", "db.open_finance_state", "db.open_finance"):
        monkeypatch.setattr(f"{modulo}.datetime", _Relogio)
    RELOGIO["agora"] = datetime(2026, 9, 28, 12, 0, tzinfo=_tz())


def _em(dia: int, hora: int = 12, minuto: int = 0) -> None:
    RELOGIO["agora"] = datetime(2026, 9, dia, hora, minuto, tzinfo=_tz())


CDB = {"id": "inv-cdb", "name": "CDB Nubank", "type": "FIXED_INCOME", "subtype": "CDB",
       "currencyCode": "BRL", "balance": 1500.25, "amount": 1500.25, "amountOriginal": 1400,
       "quantity": 1, "rate": 100, "rateType": "CDI", "status": "ACTIVE",
       "date": "2026-09-27T03:00:00Z", "lastMonthRate": None}
ACAO = {"id": "inv-acao", "name": "PETR4", "type": "EQUITY", "currencyCode": "BRL",
        "balance": 320.5, "amount": 320.5, "quantity": 10, "status": "TOTAL_WITHDRAWAL",
        "date": "2026-09-26T22:00:00-03:00"}
PARCIAL = {"executionStatus": "PARTIAL_SUCCESS"}


def _sync(monkeypatch, item: str, posicoes, *, item_extra=None, contas=()):
    _mock_pluggy(monkeypatch, item={**ITEM_SAUDAVEL, "id": item, **(item_extra or {})},
                 contas=contas, txs=[_tx_pluggy()] if contas else ())
    monkeypatch.setattr(ps, "list_pluggy_investments", lambda i, k=None: [dict(p) for p in posicoes])
    return ps.sync_pluggy_item(item)


def _fotos(conn_id: int) -> list[dict]:
    with get_conn() as c, c.cursor() as cur:
        cur.execute("select * from open_finance_investment_snapshots where connection_id=%s "
                    "order by provider_investment_id, observed_on", (conn_id,))
        rows = [dict(r) for r in cur.fetchall()]
        c.commit()
    return rows


def _por(conn_id: int, pid: str) -> list[tuple]:
    return [(f["observed_on"].day, f["balance"], f["collection_confirmed"])
            for f in _fotos(conn_id) if f["provider_investment_id"] == pid]


# T1 ─────────────────────────────────────────────────────────────────────────

def test_sync_confirmado_grava_uma_foto_por_posicao(user_id, monkeypatch):
    con = _conexao(user_id, "item-t1")["id"]
    assert _sync(monkeypatch, "item-t1", [CDB, ACAO])["ok"] is True

    fotos = {f["provider_investment_id"]: f for f in _fotos(con)}
    assert set(fotos) == {"inv-cdb", "inv-acao"}
    cdb, acao = fotos["inv-cdb"], fotos["inv-acao"]
    for f in (cdb, acao):
        assert f["observed_on"] == date(2026, 9, 28)
        assert f["observed_at"] == RELOGIO["agora"]
        assert f["collection_confirmed"] is True
    assert (cdb["balance"], cdb["amount"], cdb["amount_original"], cdb["quantity"]) == \
        (Decimal("1500.25"), Decimal("1500.25"), Decimal("1400"), Decimal("1"))
    assert (cdb["contract_rate"], cdb["contract_rate_type"], cdb["status"]) == \
        (Decimal("100"), "CDI", "ACTIVE")
    assert cdb["position_at"] == datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc)
    assert acao["position_at"] == datetime(2026, 9, 27, 1, 0, tzinfo=timezone.utc)
    assert acao["amount_original"] is None and acao["contract_rate"] is None
    assert (cdb["last_month_rate"], cdb["last_twelve_months_rate"], cdb["annual_rate"]) == \
        (None, None, None)


# T2 ─────────────────────────────────────────────────────────────────────────

def test_mesmo_dia_atualiza_e_dia_seguinte_acrescenta(user_id, monkeypatch):
    con = _conexao(user_id, "item-t2")["id"]
    _sync(monkeypatch, "item-t2", [CDB])
    _em(28, 18)
    _sync(monkeypatch, "item-t2", [{**CDB, "balance": 1501}])
    assert _por(con, "inv-cdb") == [(28, Decimal("1501"), True)]
    _em(29, 9)
    _sync(monkeypatch, "item-t2", [{**CDB, "balance": 1502}])
    assert _por(con, "inv-cdb") == [(28, Decimal("1501"), True), (29, Decimal("1502"), True)]


# T3 ─────────── controle negativo: o `where` do conflito ────────────────────

def test_coleta_nao_confirmada_nao_sobrescreve_a_confirmada(user_id, monkeypatch):
    con = _conexao(user_id, "item-t3")["id"]
    _sync(monkeypatch, "item-t3", [{**CDB, "balance": 100}])
    _em(28, 15)
    _sync(monkeypatch, "item-t3", [{**CDB, "balance": 200}], item_extra=PARCIAL)
    assert _por(con, "inv-cdb") == [(28, Decimal("100"), True)]
    assert _fotos(con)[0]["observed_at"].astimezone(_tz()).hour == 12

    # POSITIVO: no dia seguinte, não confirmada grava (com a marca) e é
    # sobrescrita pela não confirmada seguinte e depois pela confirmada.
    _em(29, 8)
    _sync(monkeypatch, "item-t3", [{**CDB, "balance": 300}], item_extra=PARCIAL)
    _em(29, 9)
    _sync(monkeypatch, "item-t3", [{**CDB, "balance": 310}], item_extra=PARCIAL)
    assert _por(con, "inv-cdb")[1] == (29, Decimal("310"), False)
    _em(29, 10)
    _sync(monkeypatch, "item-t3", [{**CDB, "balance": 400}])
    assert _por(con, "inv-cdb")[1] == (29, Decimal("400"), True)


# T4 ─────────────────────────────────────────────────────────────────────────

def test_429_em_investimentos_nao_grava_foto_e_nao_apaga_as_antigas(user_id, monkeypatch):
    con = _conexao(user_id, "item-t4")["id"]
    _sync(monkeypatch, "item-t4", [CDB], contas=[_conta_pluggy()])
    _em(29)
    _mock_pluggy(monkeypatch, item={**ITEM_SAUDAVEL, "id": "item-t4"},
                 contas=[_conta_pluggy()], txs=[_tx_pluggy()])
    monkeypatch.setattr(ps, "list_pluggy_investments", lambda i, k=None: (_ for _ in ()).throw(
        PluggyApiError("rate limit", status_code=429)))

    res = ps.sync_pluggy_item("item-t4")

    assert res["ok"] is True and res["investments_ok"] is False
    assert _espelho(con) == (1, 1), "as contas continuam sincronizadas"
    assert _por(con, "inv-cdb") == [(28, Decimal("1500.25"), True)], "lacuna, não zero"


# T5 ─────────────────────────────────────────────────────────────────────────

_ENTRADAS = ["0.95", "-12.34", "0.000001", "100", "007", "1,2", "abc", "", "NaN", "Infinity",
             "1e5", " 1", "1 ", "1\n", "+1", ".5", "1.", "١٢", 0.95, -3, 10**20, 1e20, 0.000001,
             True, False, None, [1], {"a": 1}]


def test_regra_do_numero_e_a_mesma_do_sql_profit():
    with get_conn() as c, c.cursor() as cur:
        for v in _ENTRADAS:
            cur.execute(f"select {_SQL_PROFIT} as v from (select %s::jsonb as raw) i",
                        (Jsonb({"amountProfit": v}),))
            sql = cur.fetchone()["v"]
            assert _numero(v) == sql, f"divergem em {v!r}: python={_numero(v)!r} sql={sql!r}"
        c.commit()
    for v in (float("nan"), float("inf"), float("-inf")):
        assert _numero(v) is None


def test_campo_ausente_ou_lixo_vira_null_e_nunca_zero(user_id, monkeypatch):
    con = _conexao(user_id, "item-t5")["id"]
    lixo = {"id": "inv-lixo", "type": "FIXED_INCOME", "balance": "0.95", "amount": "-12.34",
            "amountOriginal": "1,2", "quantity": True, "rate": "0.000001", "rateType": 7,
            "lastMonthRate": {"v": 1}, "annualRate": "", "date": "ontem", "status": None}
    vazio = {"id": "inv-vazio", "type": "FIXED_INCOME", "date": "2026-09-20"}
    _sync(monkeypatch, "item-t5", [lixo, vazio])

    f = {x["provider_investment_id"]: x for x in _fotos(con)}
    li, va = f["inv-lixo"], f["inv-vazio"]
    assert (li["balance"], li["amount"], li["contract_rate"]) == \
        (Decimal("0.95"), Decimal("-12.34"), Decimal("0.000001"))
    for col in ("amount_original", "quantity", "contract_rate_type", "last_month_rate",
                "annual_rate", "position_at", "status"):
        assert li[col] is None, col
    assert all(va[c] is None for c in ("balance", "amount", "quantity", "status"))
    assert va["position_at"] == datetime(2026, 9, 20, tzinfo=_tz()), "data pura = meia-noite no fuso do app"


# T6 ─────────── controle negativo: data em UTC ──────────────────────────────

def test_dia_da_foto_e_o_do_fuso_do_app_e_nao_o_utc(user_id, monkeypatch):
    con = _conexao(user_id, "item-t6")["id"]
    _em(28, 23, 30)                                   # 02:30 de 29/09 em UTC
    _sync(monkeypatch, "item-t6", [CDB])
    assert [f["observed_on"] for f in _fotos(con)] == [date(2026, 9, 28)]


# T7 ─────────────────────────────────────────────────────────────────────────

def test_posicao_que_some_e_volta_mantem_o_historico(user_id, monkeypatch):
    con = _conexao(user_id, "item-t7")["id"]
    _sync(monkeypatch, "item-t7", [CDB, ACAO])
    _em(29)
    _sync(monkeypatch, "item-t7", [ACAO])             # leitura confirmada: o CDB some
    with get_conn() as c, c.cursor() as cur:
        cur.execute("select count(*) as n from open_finance_investments "
                    "where connection_id=%s and provider_investment_id='inv-cdb'", (con,))
        assert cur.fetchone()["n"] == 0, "a reconciliação tinha de apagar a posição"
        c.commit()
    _em(30)
    _sync(monkeypatch, "item-t7", [CDB, ACAO])        # e volta com id novo
    assert [d for d, _, _ in _por(con, "inv-cdb")] == [28, 30]
    assert [d for d, _, _ in _por(con, "inv-acao")] == [28, 29, 30]


# T8 ─────────────────────────────────────────────────────────────────────────

def test_reconexao_no_mesmo_item_continua_o_historico(user_id, monkeypatch):
    con = _conexao(user_id, "item-t8")["id"]
    _sync(monkeypatch, "item-t8", [CDB])
    _em(29)
    nova = db.save_pluggy_open_finance_item(
        user_id, {"id": "item-t8", "status": "UPDATED", "connector": {"id": 612, "name": "Nubank"}})
    assert nova["id"] == con
    assert _sync(monkeypatch, "item-t8", [CDB])["ok"] is True
    assert [d for d, _, _ in _por(con, "inv-cdb")] == [28, 29]


# T14 / T14b / T15 ──────────────────────────────────────────────────────────

def test_dois_itens_no_mesmo_dia_nao_colidem(user_id, monkeypatch):
    a = _conexao(user_id, "item-t14a")["id"]
    b = _conexao(user_id, "item-t14b")["id"]
    _sync(monkeypatch, "item-t14a", [CDB])
    _sync(monkeypatch, "item-t14b", [{**CDB, "balance": 7}])
    assert _por(a, "inv-cdb") == [(28, Decimal("1500.25"), True)]
    assert _por(b, "inv-cdb") == [(28, Decimal("7"), True)]


def test_run_de_geracao_velha_nao_grava_foto(user_id, monkeypatch):
    con = _conexao(user_id, "item-velho")["id"]
    _mock_pluggy(monkeypatch, item={**ITEM_SAUDAVEL, "id": "item-velho"})

    def reconecta_no_meio(i, k=None):
        db.save_pluggy_open_finance_item(
            user_id, {"id": "item-velho", "status": "UPDATED",
                      "connector": {"id": 612, "name": "Nubank"}})
        return [dict(CDB)]
    monkeypatch.setattr(ps, "list_pluggy_investments", reconecta_no_meio)

    assert ps.sync_pluggy_item("item-velho")["reason"] == "stale_authorization"
    assert _fotos(con) == []


@pytest.mark.parametrize("status", ["PAUSED", "DELETED"])
def test_conexao_pausada_ou_apagada_nao_grava(user_id, monkeypatch, status):
    con = _conexao(user_id, "item-t15")["id"]
    _set_estado(con, status=status)
    assert _sync(monkeypatch, "item-t15", [CDB])["ok"] is False
    assert _fotos(con) == []


# T13 ─────────── controle negativo: o SAVEPOINT ─────────────────────────────

def test_falha_so_no_historico_nao_congela_o_espelho(user_id, monkeypatch):
    con = _conexao(user_id, "item-t13")["id"]
    monkeypatch.setattr("db.open_finance.grava_fotos_posicoes",
                        lambda cur, *a: cur.execute("select 1/0"))
    res = _sync(monkeypatch, "item-t13", [CDB])
    assert res["ok"] is True and res.get("investments_ok") is not False, res
    with get_conn() as c, c.cursor() as cur:
        cur.execute("select balance from open_finance_investments where connection_id=%s", (con,))
        assert [r["balance"] for r in cur.fetchall()] == [Decimal("1500.25")]
        c.commit()
    assert _fotos(con) == []


# T9 / T11 / T12 — isolamento: desconectar, exclusão, exportação ────────────

def _dois_donos(user_id, monkeypatch):
    vizinho = user_id + 1
    db.ensure_user(vizinho)
    a1 = _conexao(user_id, f"item-{user_id}-a1")["id"]
    a2 = _conexao(user_id, f"item-{user_id}-a2")["id"]
    b1 = _conexao(vizinho, f"item-{user_id}-b1")["id"]
    for item in ("a1", "a2", "b1"):
        _sync(monkeypatch, f"item-{user_id}-{item}", [CDB])
    return vizinho, a1, a2, b1


def test_desconectar_apaga_so_o_historico_da_conexao(user_id, monkeypatch):
    _, a1, a2, b1 = _dois_donos(user_id, monkeypatch)
    assert db.disconnect_open_finance_connection(user_id, a1) == 1
    assert (_fotos(a1), len(_fotos(a2)), len(_fotos(b1))) == ([], 1, 1)


def test_exclusao_da_conta_apaga_o_historico_e_poupa_o_vizinho(user_id, monkeypatch):
    _, a1, a2, b1 = _dois_donos(user_id, monkeypatch)
    privacy.delete_user_data(user_id)
    assert (_fotos(a1), _fotos(a2), len(_fotos(b1))) == ([], [], 1)


def test_exportacao_leva_posicoes_e_historico_so_do_titular(user_id, monkeypatch):
    _, a1, a2, b1 = _dois_donos(user_id, monkeypatch)
    with zipfile.ZipFile(io.BytesIO(privacy.build_user_export_zip(user_id))) as zf:
        dados = json.loads(zf.read("dados.json"))["dados"]
        nomes = set(zf.namelist())
    assert {"csv/investimentos_open_finance.csv", "csv/historico_investimentos_open_finance.csv"} <= nomes
    for chave in ("investimentos_open_finance", "historico_investimentos_open_finance"):
        assert sorted(r["connection_id"] for r in dados[chave]) == sorted([a1, a2]), chave
    assert dados["historico_investimentos_open_finance"][0]["balance"] is not None


# T16 ─────────────────────────────────────────────────────────────────────────

def test_ddl_idempotente_com_pk_natural_e_cascade_pela_conexao():
    db.init_db()
    with get_conn() as c, c.cursor() as cur:
        cur.execute(
            """
            select k.contype, k.confdeltype, k.confrelid::regclass::text as alvo,
                   array(select a.attname::text from unnest(k.conkey) with ordinality u(n, o)
                         join pg_attribute a on a.attrelid = k.conrelid and a.attnum = u.n
                         order by u.o) as cols
              from pg_constraint k
             where k.conrelid = 'open_finance_investment_snapshots'::regclass
            """)
        cons = {r["contype"]: r for r in cur.fetchall()}
        c.commit()
    assert cons["p"]["cols"] == ["connection_id", "provider_investment_id", "observed_on"]
    assert (cons["f"]["cols"], cons["f"]["alvo"], cons["f"]["confdeltype"]) == \
        (["connection_id"], "open_finance_connections", "c")
