"""Plano Família, PR 4 (passada 3): duas regras que o plano fixou e a suíte principal não travava.

(1) orçamento só no mês corrente; (2) decisão 1: titular coberto mas com os membros suspensos ainda lê (e grava log).
Reutiliza o mundo `w` de test_api_v2_familia_dados (nos dois prefixos).
"""
import base64
import json

import pytest

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


def _pagina1(w, monkeypatch):
    """(proximo entregue ao titular, proximo CRU que a consulta devolveu)"""
    cru = []
    orig = db.lancamentos_leitura.pagina
    monkeypatch.setattr(db.lancamentos_leitura, "pagina", lambda *a, **k: (cru.append(orig(*a, **k)), cru[-1])[1])
    r = w.ler(w.ta, w.ma1, "lancamentos", limite=2)
    assert r.status_code == 200 and r.json()["proximo"], r.text
    return r.json()["proximo"], cru[0]["proximo"]


def test_cursor_do_titular_nao_expoe_instante_tabela_nem_id(w, monkeypatch):
    p, cru = _pagina1(w, monkeypatch)
    dia, instante, tabela, ident = json.loads(base64.urlsafe_b64decode(cru))
    assert p != cru
    try:
        decodificado = base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)).decode("latin-1")
    except ValueError:
        decodificado = ""
    for texto in (p, decodificado):
        assert instante not in texto and dia not in texto and f'"{tabela}"' not in texto
    with pytest.raises(ValueError):
        json.loads(base64.urlsafe_b64decode(p))


def test_cursor_recusado_422_sem_log_e_sem_consulta(w, monkeypatch):
    p, cru = _pagina1(w, monkeypatch)
    w.conceder(w.ma2, "aggregates", "transactions")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update family_members set joined_at = now() - interval '90 days' where user_id = %s", (w.ma1,))
        conn.commit()
    ano, m = add_months(now_tz().year, now_tz().month, -1)
    antes = len(_log(w.ma1)) + len(_log(w.ma2))
    consultas = []
    monkeypatch.setattr(db.lancamentos_leitura, "pagina", lambda *a, **k: (consultas.append(1), {"itens": [], "proximo": None})[1])
    ruim = p[:-4] + ("AAAA" if p[-4:] != "AAAA" else "BBBB")
    for alvo, cursor, mes in ((w.ma1, cru, t_mes()), (w.ma1, ruim, t_mes()), (w.ma1, p, f"{ano:04d}-{m:02d}"),
                              (w.ma2, p, t_mes())):  # cru antigo, adulterado, outro mês, outro membro
        r = w.ler(w.ta, alvo, "lancamentos", cursor=cursor, mes=mes)
        assert r.status_code == 422 and "cursor" in r.text, r.text
    assert consultas == [] and len(_log(w.ma1)) + len(_log(w.ma2)) == antes
    # positivo: o mesmo token, no contexto certo, continua valendo (volta a consultar)
    assert w.ler(w.ta, w.ma1, "lancamentos", cursor=p).status_code == 200 and consultas == [1]
