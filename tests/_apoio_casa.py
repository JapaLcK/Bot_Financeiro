"""Apoio dos testes da casa (PR 5): chamadas curtas, ref do item e a linha crua no banco."""
from db.connection import get_conn
from test_api_v2_familia_dados import erro  # noqa: F401  (reexporta)


def meta(w, quem, nome="Viagem", alvo="1000.00", **extra):
    r = w.chamar(quem, "POST", "/familia/casa/metas", json={"nome": nome, "alvo": alvo, **extra})
    assert r.status_code == 201, r.text
    return next(m["ref"] for m in r.json()["metas"] if m["nome"] == nome)


def orc(w, quem, categoria="Mercado", limite="500.00"):
    r = w.chamar(quem, "POST", "/familia/casa/orcamentos", json={"categoria": categoria, "limite": limite})
    assert r.status_code == 201, r.text
    return next(o["ref"] for o in r.json()["orcamentos"] if o["categoria"] == categoria)


def casa(w, quem):
    r = w.chamar(quem, "GET", "/familia/casa")
    assert r.status_code == 200, r.text
    return r.json()


def item(w, quem, chave, ref):
    return next((i for i in casa(w, quem)[chave] if i["ref"] == ref), None)


def linha(tabela, ref):
    assert tabela in ("family_goals", "family_budgets")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"select * from {tabela} where id = %s", (int(ref[1:]),))
        return cur.fetchone()
