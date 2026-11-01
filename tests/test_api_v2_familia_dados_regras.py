"""Plano Família, PR 4 (passada 3): duas regras que o plano fixou e a suíte principal não travava.

(1) orçamento só no mês corrente; (2) decisão 1: titular coberto mas com os membros suspensos ainda lê (e grava log).
Reutiliza o mundo `w` de test_api_v2_familia_dados (nos dois prefixos).
"""
import db
from db import family_grants as fg
from db.connection import get_conn
from test_api_v2_familia_dados import _log, t_mes, w  # noqa: F401  (w é fixture)
from utils_date import add_months, now_tz


def test_orcamento_so_no_mes_corrente_e_null_no_mes_passado(w):
    db.upsert_budget(w.ma1, "mercado", 200)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update family_members set joined_at = now() - interval '90 days' where user_id = %s", (w.ma1,))
        conn.commit()
    atual = w.ler(w.ta, w.ma1)
    assert atual.status_code == 200, atual.text
    orc = atual.json()["orcamentos"]
    assert orc and [float(i["teto"]) for i in orc["itens"]] == [200.0]
    item = orc["itens"][0]
    assert item["categoria"].lower() == "mercado" and float(item["gasto"]) == 50.0 and float(item["restante"]) == 150.0
    assert float(orc["total"]["teto"]) == 200.0 and float(orc["total"]["gasto"]) == 50.0
    ano, m = add_months(now_tz().year, now_tz().month, -1)
    passado = w.ler(w.ta, w.ma1, mes=f"{ano:04d}-{m:02d}")
    assert passado.status_code == 200, passado.text
    assert passado.json()["orcamentos"] is None
    assert len(_log(w.ma1)) == 2


def test_decisao1_titular_coberto_mas_suspenso_le_e_grava_log(w):
    fg.set_suspended(w.ta, True)
    assert fg.is_suspended(w.ta)
    for rec in ("agregados", "lancamentos"):
        r = w.ler(w.ta, w.ma1, rec)
        assert r.status_code == 200, (rec, r.text)
    assert [x["recurso"] for x in _log(w.ma1)] == [f"agregados:{t_mes()}", f"lancamentos:{t_mes()}"]
