"""Plano Família, PR 5: regras 9-12 da casa (prévia de dissolução, conflito de edição, movimentos, corrida com a saída).

Mundo de test_api_v2_familia_dados. Positivos do grupo: a dissolução depois da nova prévia (9), a soma concorrente (11).
"""
import threading

from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de
from _apoio_casa import casa, erro, item, linha, meta, orc
from _espera_lock import _esperar_backend_travado
from db import family as fam
from db.connection import get_conn
from test_api_v2_familia import _ativo
from test_api_v2_familia_dados import w  # noqa: F401  (w é fixture)


def _post_em_thread(w, uid, caminho, corpo, codigos):  # um cliente por thread: o TestClient compartilhado não é thread-safe
    c = TestClient(dashboard.app)
    c.headers["Authorization"] = f"Bearer {sessao_de(uid)['access']}"
    codigos.append(c.post(w.p + caminho, json=corpo).status_code)


def test_t9_previa_conta_a_casa_e_item_novo_entre_previa_e_confirmacao_e_409(w):
    meta(w, w.ma1), meta(w, w.ta, "Outra"), orc(w, w.ma2)
    meta(w, w.tb, "De B")  # não conta
    d = w.chamar(w.ta, "GET", "/familia/dissolucao").json()
    assert (d["metas"], d["orcamentos"]) == (2, 1)
    orc(w, w.ma1, "Luz")  # entre a prévia e a confirmação
    erro(w.chamar(w.ta, "POST", "/familia/dissolucao", json={"confirmacao": d["confirmacao"]}), 409, "confirmation_mismatch")
    assert fam.get_group(w.ta) and len(casa(w, w.ta)["orcamentos"]) == 2
    d2 = w.chamar(w.ta, "GET", "/familia/dissolucao").json()
    assert (d2["metas"], d2["orcamentos"]) == (2, 2)
    assert w.chamar(w.ta, "POST", "/familia/dissolucao", json={"confirmacao": d2["confirmacao"]}).status_code == 204
    assert not fam.get_group(w.ta) and len(casa(w, w.tb)["metas"]) == 1


def test_t10_segunda_edicao_com_versao_velha_e_conflito_e_a_primeira_vale(w):
    ref, o = meta(w, w.ma1, "Reforma"), orc(w, w.ma1, "Luz")
    v = item(w, w.ma1, "metas", ref)["versao"]
    assert item(w, w.ma2, "metas", ref)["versao"] == v == 1
    corpo = lambda nome: {"nome": nome, "alvo": "10", "prazo": None, "versao": v}  # noqa: E731
    assert w.chamar(w.ma1, "PUT", f"/familia/casa/metas/{ref}", json=corpo("de MA1")).status_code == 200
    erro(w.chamar(w.ma2, "PUT", f"/familia/casa/metas/{ref}", json=corpo("de MA2")), 409, "edit_conflict")
    m = item(w, w.ta, "metas", ref)
    assert (m["nome"], m["versao"]) == ("de MA1", 2)
    assert w.chamar(w.ma2, "PUT", f"/familia/casa/metas/{ref}", json={**corpo("de MA2"), "versao": 2}).status_code == 200  # recarregou
    po = {"categoria": "Energia", "limite": "9", "versao": 1}  # o mesmo no orçamento
    assert w.chamar(w.ma1, "PUT", f"/familia/casa/orcamentos/{o}", json=po).status_code == 200
    erro(w.chamar(w.ma2, "PUT", f"/familia/casa/orcamentos/{o}", json={**po, "categoria": "Outra"}), 409, "edit_conflict")
    assert item(w, w.ta, "orcamentos", o)["categoria"] == "Energia"
    assert w.chamar(w.ma1, "PUT", f"/familia/casa/metas/{ref}", json={**corpo("x"), "versao": 0}).status_code == 422  # versao >= 1


def test_t10b_movimento_nao_exige_nem_muda_a_versao(w):
    ref = meta(w, w.ma1, "Reforma")
    assert w.chamar(w.ma2, "POST", f"/familia/casa/metas/{ref}/movimentos", json={"tipo": "somar", "valor": "5"}).status_code == 200
    assert item(w, w.ma1, "metas", ref)["versao"] == 1
    assert w.chamar(w.ma1, "PUT", f"/familia/casa/metas/{ref}", json={"nome": "N", "alvo": "100", "prazo": None, "versao": 1}).status_code == 200
    assert item(w, w.ma1, "metas", ref)["guardado"] == "5.00"  # o PUT não toca no guardado


def test_t11_movimentos_somam_e_subtrair_alem_do_saldo_e_recusado(w):
    ref, o = meta(w, w.ma1, "Reforma", "1000"), orc(w, w.ma1, "Luz", "100")
    for caminho in (f"/familia/casa/metas/{ref}/movimentos", f"/familia/casa/orcamentos/{o}/movimentos"):
        codigos = []
        threads = [threading.Thread(target=_post_em_thread, args=(w, q, caminho, {"tipo": "somar", "valor": "10.10"}, codigos))
                   for q in (w.ta, w.ma1, w.ma2) * 3]
        [t.start() for t in threads], [t.join(20) for t in threads]
        assert codigos == [200] * 9, codigos
    assert item(w, w.ta, "metas", ref)["guardado"] == "90.90" and item(w, w.ta, "orcamentos", o)["gasto"] == "90.90"
    assert item(w, w.ta, "metas", ref)["percentual"] == 9 and item(w, w.ta, "orcamentos", o)["restante"] == "9.10"
    for caminho, chave in ((f"/familia/casa/metas/{ref}/movimentos", "metas"), (f"/familia/casa/orcamentos/{o}/movimentos", "orcamentos")):
        erro(w.chamar(w.ma1, "POST", caminho, json={"tipo": "subtrair", "valor": "90.91"}), 409, "insufficient")
        assert w.chamar(w.ma1, "POST", caminho, json={"tipo": "subtrair", "valor": "90.90"}).status_code == 200  # exato: chega a zero
    assert item(w, w.ta, "metas", ref)["guardado"] == "0.00" and item(w, w.ta, "orcamentos", o)["gasto"] == "0.00"


def test_t11b_gasto_do_orcamento_zera_no_mes_novo(w):
    o = orc(w, w.ma1, "Luz", "100")
    w.chamar(w.ma1, "POST", f"/familia/casa/orcamentos/{o}/movimentos", json={"tipo": "somar", "valor": "40"})
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update family_budgets set gasto_mes = gasto_mes - interval '1 month' where id = %s", (int(o[1:]),))
        conn.commit()
    assert item(w, w.ta, "orcamentos", o)["gasto"] == "0.00"
    erro(w.chamar(w.ta, "POST", f"/familia/casa/orcamentos/{o}/movimentos", json={"tipo": "subtrair", "valor": "1"}), 409, "insufficient")
    w.chamar(w.ta, "POST", f"/familia/casa/orcamentos/{o}/movimentos", json={"tipo": "somar", "valor": "5"})
    assert item(w, w.ta, "orcamentos", o)["gasto"] == "5.00"  # o mês novo parte de zero, sem arrastar os 40


def test_t12_edicao_que_espera_o_lock_e_barrada_se_o_autor_saiu_enquanto_isso(w):
    ref = meta(w, w.ma1, "Reforma")
    antes = linha("family_goals", ref)
    res = []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from family_groups where owner_user_id = %s for update", (w.ta,))
        gid = cur.fetchone()["id"]
        t = threading.Thread(target=lambda: res.append(w.chamar(
            w.ma1, "PUT", f"/familia/casa/metas/{ref}", json={"nome": "tarde", "alvo": "10", "prazo": None, "versao": 1})))
        t.start()
        assert _esperar_backend_travado(10), "o PUT não esperou o lock do grupo"
        fam._remove_locked(cur, gid, w.ma1, "left")  # MA1 sai na conexão que segura o lock
        conn.commit()
    t.join(15)
    erro(res[0], 404, "not_found")
    assert linha("family_goals", ref) == antes and not _ativo(w.ma1)
