"""PR-A2b-1 (ADR 0006): trocar, desfazer e escolha só manual, pelas rotas do /app com Postgres real.

Toda asserção é o retrato inteiro (`tests/_retrato_declaracao.py`). As conversas passam pelas rotas
`/open-finance/{uid}/movements*` e pelo sync/import reais.

Controles (CLAUDE.md §3; formato de `docs/controles_declarados.md`):
- G1: tirar `or (e_fluxo(d) and d["manual_only"])` da condição de bind do reconcile -> VERMELHO
  `test_c1_exemplo_de_referencia` (passo 3 religa T1). Positivo: `test_g1_o_mesmo_sync_casa_a_declaracao_normal`.
- G2: trocar `method == "manual" and e_fluxo(declaration)` por `False` no `_bind` -> VERMELHO
  `test_c2_primeira_escolha_ja_e_so_manual`. Positivo: `test_g2_sem_escolha_manual_religa_sozinha`.
- G5: no ramo `replace_transaction_id`, tirar `d["matched_transaction_id"] != replace_transaction_id` -> VERMELHO
  `test_g5_g7_cas_do_trocar_e_do_desfazer`.
- G6: `conn.commit()` logo depois do `devolve_sombra` do ramo `replace` -> VERMELHO
  `test_g6_trocar_e_atomico`.
- G7: no `undo_bank_movement`, tirar `d["matched_transaction_id"] != transaction_id` -> VERMELHO
  `test_g5_g7_cas_do_trocar_e_do_desfazer`.
- G10: tirar `ReconciliationConflict` do `except (ValueError, ReconciliationConflict)` da rota -> VERMELHO
  `test_g10_conflito_vira_409`.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import db.bank_movements as movements
import frontend.finance_bot_websocket_custom as dashboard
from conftest import promote_to_pro
from db.open_finance import pause_open_finance_connection
from tests._patrimonio_helpers import q
from tests._retrato_declaracao import BASE, casada, decl, em, retrato, sombra, solta, t, tid
from tests.test_bank_movements import _bank
from tests.test_declaracao_fluxo_pausa_reconexao import _reconecta, _retoma
from tests.test_declaracao_fluxo_reconcilia import declara, sincroniza

H = {dashboard.CSRF_HEADER_NAME: "a2b-test"}
T1 = t("T1", -50, 0, "SUPERMERCADO XYZ")
T2 = t("T2", -50, 1, "PIX MARIA")


def cliente(uid):
    client = TestClient(dashboard.app, raise_server_exceptions=False)
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(uid, "a2b@test.com"))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(uid, hours=1))
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "a2b-test")
    return client


def confirma(c, uid, lid, ptid, troca=None):
    body = {"launch_id": lid, "transaction_id": tid(uid, ptid)}
    if troca:
        body["replace_transaction_id"] = tid(uid, troca)
    return c.post(f"/open-finance/{uid}/movements/confirm", json=body, headers=H)


def desfaz(c, uid, lid, ptid):
    return c.post(f"/open-finance/{uid}/movements/undo", json={"launch_id": lid, "transaction_id": tid(uid, ptid)},
                  headers=H)


def ana(uid):
    """Carteira 1000, banco conectado, "mercado" R$ 50,00 declarado em BASE e o sync de T1 e T2."""
    promote_to_pro(uid)
    q("update accounts set balance=1000 where user_id=%s returning 1", (uid,))
    cid, _ = _bank(uid)
    d = declara(uid, criado_em=em(0))
    sincroniza(cid, uid, T1, T2)
    return cid, d, cliente(uid)


def R(L, T, D, saiu, C=1000.0):
    return {"L": sorted(L), "T": sorted(T), "D": sorted(D), "C": C, "saiu": saiu}


def test_c1_exemplo_de_referencia(user_id):
    cid, d, c = ana(user_id)
    passo1 = R([decl(0), sombra("T2", 50.0, 1)], [casada("T1", -50.0, 0), solta("T2", -50.0, 1)],
               [("decl mercado", -50.0, "T1", "automatic", False, False)], 100.0)
    assert retrato(user_id) == passo1

    r = desfaz(c, user_id, d, "T1")
    assert r.status_code == 200 and r.json() == {"ok": True, "changed": True}, r.text
    passo2 = R([decl(0), sombra("T1", 50.0, 0), sombra("T2", 50.0, 1)],
               [solta("T1", -50.0, 0), solta("T2", -50.0, 1)],
               [("decl mercado", -50.0, None, None, True, False)], 150.0)
    assert retrato(user_id) == passo2

    sincroniza(cid, user_id, T1, T2)  # G1: a declaração só manual não religa
    assert retrato(user_id) == passo2

    assert confirma(c, user_id, d, "T2").status_code == 200
    assert retrato(user_id) == R([decl(1), sombra("T1", 50.0, 0)], [solta("T1", -50.0, 0), casada("T2", -50.0, 1)],
                                 [("decl mercado", -50.0, "T2", "manual", True, False)], 100.0)

    assert confirma(c, user_id, d, "T1", troca="T2").status_code == 200
    assert retrato(user_id) == R([decl(0), sombra("T2", 50.0, 1)], [casada("T1", -50.0, 0), solta("T2", -50.0, 1)],
                                 [("decl mercado", -50.0, "T1", "manual", True, False)], 100.0)

    t1_corrigida = t("T1", -60, 0, "SUPERMERCADO XYZ")
    sincroniza(cid, user_id, t1_corrigida, T2)  # fora de max(R$ 5; 10%): o par cai e T1 ganha sombra na mesma passada
    passo6 = R([decl(0), sombra("T1", 60.0, 0), sombra("T2", 50.0, 1)],
               [solta("T1", -60.0, 0), solta("T2", -50.0, 1)],
               [("decl mercado", -50.0, None, None, True, True)], 160.0)
    assert retrato(user_id) == passo6

    sincroniza(cid, user_id, t1_corrigida, T2, t("T3", -50, 2, "MERCADO BOM PRECO"))
    assert retrato(user_id) == R(passo6["L"] + [sombra("T3", 50.0, 2)], passo6["T"] + [solta("T3", -50.0, 2)],
                                 passo6["D"], 210.0)
    lista = c.get(f"/open-finance/{user_id}/movements").json()["movements"]
    assert [m["launch_id"] for m in lista] == [d]
    assert [x["description"] for x in lista[0]["candidates"]] == ["PIX MARIA", "MERCADO BOM PRECO"]
    assert lista[0]["fluxo"] and lista[0]["manual_only"] and lista[0]["match"] is None


def test_c2_primeira_escolha_ja_e_so_manual(user_id):
    promote_to_pro(user_id)
    q("update accounts set balance=1000 where user_id=%s returning 1", (user_id,))
    cid, _ = _bank(user_id)
    c = cliente(user_id)
    d = declara(user_id, criado_em=em(0))
    t4 = t("T4", -50, 0, "FARMACIA")
    sincroniza(cid, user_id, t4)  # sem evidência: não casa sozinha
    assert retrato(user_id) == R([decl(0), sombra("T4", 50.0, 0)], [solta("T4", -50.0, 0)],
                                 [("decl mercado", -50.0, None, None, False, False)], 100.0)
    assert confirma(c, user_id, d, "T4").status_code == 200
    assert retrato(user_id) == R([decl(0)], [casada("T4", -50.0, 0)],
                                 [("decl mercado", -50.0, "T4", "manual", True, False)], 50.0)
    t4_70 = t("T4", -70, 0, "FARMACIA")
    sincroniza(cid, user_id, t4_70)
    sincroniza(cid, user_id, t4_70, t("T5", -50, 0, "SUPERMERCADO XYZ"))  # G2: T5 tem evidência e não liga
    assert retrato(user_id) == R([decl(0), sombra("T4", 70.0, 0), sombra("T5", 50.0, 0)],
                                 [solta("T4", -70.0, 0), solta("T5", -50.0, 0)],
                                 [("decl mercado", -50.0, None, None, True, True)], 170.0)


def test_g2_sem_escolha_manual_religa_sozinha(user_id):
    cid, d, _ = ana(user_id)
    t1_60 = t("T1", -60, 0, "SUPERMERCADO XYZ")
    sincroniza(cid, user_id, t1_60, T2, t("T6", -50, 0, "SUPERMERCADO ABC"))
    assert retrato(user_id) == R(
        [decl(0), sombra("T1", 60.0, 0), sombra("T2", 50.0, 1)],
        [solta("T1", -60.0, 0), solta("T2", -50.0, 1), casada("T6", -50.0, 0)],
        [("decl mercado", -50.0, "T6", "automatic", False, False)], 160.0)


def test_g1_o_mesmo_sync_casa_a_declaracao_normal(user_id):
    cid, d, c = ana(user_id)
    assert desfaz(c, user_id, d, "T1").status_code == 200
    declara(user_id, criado_em=em(0), alvo="farmacia", categoria="saude")
    sincroniza(cid, user_id, T1, T2, t("F1", -50, 0, "FARMACIA SAO JOAO"))
    assert retrato(user_id) == R(
        [decl(0), decl(0, alvo="farmacia"), sombra("T1", 50.0, 0), sombra("T2", 50.0, 1)],
        [solta("T1", -50.0, 0), solta("T2", -50.0, 1), casada("F1", -50.0, 0, alvo="farmacia")],
        [("decl farmacia", -50.0, "F1", "automatic", False, False),
         ("decl mercado", -50.0, None, None, True, False)], 200.0)


@pytest.mark.parametrize("via", ["reconexao", "pausa"])
def test_c3_trocar_depois_reconexao_ou_pausa(user_id, via):
    """Medido: nos dois caminhos o par escolhido cai (ids novos na reconexão; conta fora das ativas na
    pausa) e a declaração vai para "a conferir" só manual, com as transações como sugestão (DP-1 = a)."""
    cid, d, c = ana(user_id)
    assert confirma(c, user_id, d, "T2", troca="T1").status_code == 200
    if via == "reconexao":
        _reconecta(user_id, cid, T1, T2)
    else:
        pause_open_finance_connection(cid)
        assert retrato(user_id) == R(  # pausada: T2 sem linha; a declaração a conferir representa o débito
            [decl(0), sombra("T1", 50.0, 0)], [solta("T1", -50.0, 0), ("T2", -50.0, 1, None, None, None)],
            [("decl mercado", -50.0, None, None, True, True)], 100.0)
        _retoma(cid)
        sincroniza(cid, user_id, T1, T2)
    assert retrato(user_id) == R([decl(0), sombra("T1", 50.0, 0), sombra("T2", 50.0, 1)],
                                 [solta("T1", -50.0, 0), solta("T2", -50.0, 1)],
                                 [("decl mercado", -50.0, None, None, True, True)], 150.0)
    [m] = c.get(f"/open-finance/{user_id}/movements?launch_id={d}").json()["movements"]
    assert m["match"] is None and [x["description"] for x in m["candidates"]] == ["SUPERMERCADO XYZ", "PIX MARIA"]


def test_c4_congelada_desfaz_e_troca(user_id):
    promote_to_pro(user_id)
    q("update accounts set balance=1000 where user_id=%s returning 1", (user_id,))
    cid, _ = _bank(user_id)
    c = cliente(user_id)
    # Dias k e k+1, além de FLUXO_CONGELA_DIAS e no MESMO mês: com -50/-49 fixos, uma vez por mês o -49 caía
    # no dia 1 e o "saiu" do mês da declaração perdia a linha que a troca levou para o mês seguinte.
    k = -51 if (BASE + timedelta(days=-49)).day == 1 else -50
    d = declara(user_id, criado_em=em(k))
    a, b = t("A", -50, k, "SUPERMERCADO XYZ"), t("B", -50, k + 1, "PIX MARIA")
    sincroniza(cid, user_id, a, b)
    mes = BASE + timedelta(days=k)
    assert q("select matched_transaction_id from bank_movement_declarations where launch_id=%s", (d,))[
        "matched_transaction_id"] == tid(user_id, "A")
    sincroniza(cid, user_id, a, b)  # agora congelada: o sync não mexe nela
    [m] = c.get(f"/open-finance/{user_id}/movements?launch_id={d}").json()["movements"]
    assert m["match"]["description"] == "SUPERMERCADO XYZ" and [x["description"] for x in m["candidates"]] == ["PIX MARIA"]
    assert confirma(c, user_id, d, "B", troca="A").status_code == 200
    assert retrato(user_id, mes) == R([decl(k + 1), sombra("A", 50.0, k)], [solta("A", -50.0, k), casada("B", -50.0, k + 1)],
                                      [("decl mercado", -50.0, "B", "manual", True, False)], 100.0)
    assert desfaz(c, user_id, d, "B").status_code == 200
    assert retrato(user_id, mes) == R([decl(k), sombra("A", 50.0, k), sombra("B", 50.0, k + 1)],
                                      [solta("A", -50.0, k), solta("B", -50.0, k + 1)],
                                      [("decl mercado", -50.0, None, None, True, False)], 150.0)


def test_c5_trocar_e_apagar(user_id):
    cid, d, c = ana(user_id)
    assert confirma(c, user_id, d, "T2", troca="T1").status_code == 200
    r = c.delete(f"/launches/{user_id}/{d}", headers=H)
    assert r.status_code == 200, r.text
    assert retrato(user_id) == R([sombra("T1", 50.0, 0), sombra("T2", 50.0, 1)],
                                 [solta("T1", -50.0, 0), solta("T2", -50.0, 1)], [], 100.0)
    sincroniza(cid, user_id, T1, T2)  # o import seguinte não duplica a sombra devolvida
    assert retrato(user_id)["L"] == [sombra("T1", 50.0, 0), sombra("T2", 50.0, 1)]


def test_g5_g7_cas_do_trocar_e_do_desfazer(user_id):
    cid, d, c = ana(user_id)
    sincroniza(cid, user_id, T1, T2, t("T3", -50, 2, "PIX JOAO"))
    assert confirma(c, user_id, d, "T2", troca="T1").status_code == 200  # a aba 1 troca
    vencedora = retrato(user_id)
    assert vencedora["D"] == [("decl mercado", -50.0, "T2", "manual", True, False)]
    assert confirma(c, user_id, d, "T3", troca="T1").status_code == 409  # a aba 2 viu T1
    assert desfaz(c, user_id, d, "T1").status_code == 409
    assert retrato(user_id) == vencedora
    assert confirma(c, user_id, d, "T3", troca="T2").status_code == 200  # com o par certo, troca
    assert desfaz(c, user_id, d, "T3").status_code == 200  # e desfaz
    assert retrato(user_id)["D"] == [("decl mercado", -50.0, None, None, True, False)]


def test_g6_trocar_e_atomico(user_id, monkeypatch):
    cid, d, c = ana(user_id)
    antes = retrato(user_id)

    def quebra(*a, **k):
        raise RuntimeError("bind falhou depois da sombra")
    original = movements._bind
    monkeypatch.setattr(movements, "_bind", quebra)
    assert confirma(c, user_id, d, "T2", troca="T1").status_code == 500
    assert retrato(user_id) == antes
    monkeypatch.setattr(movements, "_bind", original)
    assert confirma(c, user_id, d, "T2", troca="T1").status_code == 200
    assert retrato(user_id)["D"] == [("decl mercado", -50.0, "T2", "manual", True, False)]


def test_g10_conflito_vira_409(user_id, monkeypatch):
    import db.open_finance as of
    cid, d, c = ana(user_id)
    antes = retrato(user_id)
    original = of._insert_of_shadow
    monkeypatch.setattr(of, "_insert_of_shadow", lambda *a, **k: (None, False))
    assert desfaz(c, user_id, d, "T1").status_code == 409
    assert confirma(c, user_id, d, "T2", troca="T1").status_code == 409
    assert retrato(user_id) == antes
    monkeypatch.setattr(of, "_insert_of_shadow", original)
    assert desfaz(c, user_id, d, "T1").status_code == 200
