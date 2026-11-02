"""Agregados da régua de remarketing no painel do funil (estado real no banco, em DELTA)."""
from core.funil_remarketing import fetch_remarketing  # noqa: F401  (a chave sai por /admin/api/funil)
from db import get_conn
from tests.test_funil_dashboard import _evento, _funil, configured_admin, contas  # noqa: F401


def _rm():
    return _funil()["remarketing"]


def _envio(cur, uid, etapa, status, email_ok, wa_ok, clique_dias=None):
    cur.execute(
        "insert into remarketing_envios (user_id, etapa, status, email_ok, whatsapp_ok, clicado_em) "
        "values (%s,%s,%s,%s,%s, case when %s::int is null then null else now() - make_interval(days => %s::int) end)",
        (uid, etapa, status, email_ok, wa_ok, clique_dias, clique_dias))


def test_agregados_por_etapa_e_so_contagens(contas):
    antes = _rm()
    a, b = contas(), contas()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("insert into remarketing_regua (user_id, origem) values (%s,'precos'),(%s,'assinar')", (a, b))
            _envio(cur, a, 1, "enviado", True, False, clique_dias=2)   # clicou e comprou depois
            _envio(cur, b, 1, "enviado", True, True, clique_dias=2)    # clicou, não comprou
            _envio(cur, a, 2, "sem_canal", None, None)
            _envio(cur, b, 2, "enviando", None, None)
        conn.commit()
    _evento(a, "completed", dias_atras=1)  # depois do clique (2 dias atrás)
    try:
        d = _rm()
        e = lambda r, i: r["etapas"][i - 1]
        dif = lambda i, k: e(d, i)[k] - e(antes, i)[k]
        assert d["na_regua"] - antes["na_regua"] == 2
        assert {k: dif(1, k) for k in ("enviados", "email_ok", "whatsapp_falhas", "whatsapp_ok", "cliques", "recuperados")} == {
            "enviados": 2, "email_ok": 2, "whatsapp_falhas": 1, "whatsapp_ok": 1, "cliques": 2, "recuperados": 1}
        assert (dif(2, "sem_canal"), dif(2, "enviando"), dif(3, "enviados")) == (1, 1, 0)
        assert set(e(d, 1)) == {"etapa", "enviados", "sem_canal", "enviando", "email_ok", "email_falhas",
                                "whatsapp_ok", "whatsapp_falhas", "cliques", "recuperados"}
    finally:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from remarketing_envios where user_id = any(%s)", ([a, b],))
                cur.execute("delete from remarketing_regua where user_id = any(%s)", ([a, b],))
            conn.commit()


def test_recuperado_exige_clique_kind_e_compra_depois_e_falha_de_email(contas):
    antes = _rm()
    c, d, e = contas(), contas(), contas()
    with get_conn() as conn:
        with conn.cursor() as cur:
            _envio(cur, c, 3, "enviado", False, None, clique_dias=3)  # falha de e-mail; compra ANTES do clique
            _envio(cur, d, 3, "enviado", None, None, clique_dias=3)   # e-mail NULL; outro kind depois do clique
            _envio(cur, e, 3, "enviado", None, None)                  # compra sem clique
        conn.commit()
    _evento(c, "completed", dias_atras=5)
    _evento(d, "viewed_pricing", dias_atras=1)
    _evento(e, "completed", dias_atras=1)
    try:
        dif = lambda k: _rm()["etapas"][2][k] - antes["etapas"][2][k]
        assert (dif("enviados"), dif("cliques"), dif("recuperados"), dif("email_falhas")) == (3, 2, 0, 1)
    finally:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from remarketing_envios where user_id = any(%s)", ([c, d, e],))
            conn.commit()
