"""A regra "CDB é caixinha só em par banco × emissor conhecido" (db.open_finance).

A Pluggy não manda campo que separe caixinha de CDB comum. Casar emissor com o
banco conectado (a regra anterior) transformava o CDB comum do Inter, emitido
pelo próprio Inter, em caixinha read-only — e ele sumia de `list_of_fixed_income`.
Agora o automático só aceita o que está em `_CAIXINHA_CDB`; o resto vai para a
tela de vínculo manual.
"""
import db
from db.open_finance import _e_caixinha
from test_of_caixinha_autoimport import (
    NU_ISSUER, _nubank_raws, _pockets, _save, _seed_connection,
)


def _cdb(issuer, banco, raw=None):
    return {"name": "CDB", "type": "FIXED_INCOME", "subtype": "CDB",
            "raw": {"issuer": issuer} if raw is None else raw, "institution_name": banco}


NAO_E_CAIXINHA = [
    ("BANCO INTER S.A.", "Banco Inter"),       # o apontamento: CDB comum do próprio banco
    ("BANCO INTER S.A.", "Inter"),
    ("BANCO C6 S.A.", "C6 Bank"),
    ("BANCO BTG PACTUAL S.A.", "BTG Pactual"),
    ("NU INVEST CORRETORA DE VALORES S.A.", "Nubank"),   # corretora do grupo
    ("NU INVESTIMENTOS S.A. - CORRETORA DE TÍTULOS E VALORES MOBILIÁRIOS", "Nubank"),
    ("BANCO INTER S.A.", "Nubank"),
    (NU_ISSUER, "Banco Inter"),                # emissor certo, banco errado
    (NU_ISSUER, "Nubank Empresas"),            # nome exato: PJ não herda
    (None, "Nubank"), ("", "Nubank"), (NU_ISSUER, None),
]


def test_so_par_conhecido_vira_caixinha():
    assert _e_caixinha(_cdb(NU_ISSUER, "Nubank"))
    assert _e_caixinha(_cdb("  nu financeira s.a.  - SCFI", " nubank "))
    assert [p for p in NAO_E_CAIXINHA if _e_caixinha(_cdb(*p))] == []
    assert not _e_caixinha(_cdb(None, "Nubank", raw="sem-issuer"))


def test_cdb_comum_do_banco_conectado_segue_investimento(user_id):
    """Apontamento P1 do Codex no #384, no caminho do sync: cliente do Inter com
    CDB emitido pelo Inter não perde o investimento para uma caixinha read-only."""
    from db.rv import list_of_fixed_income
    conn_id = _seed_connection(user_id, institution="Banco Inter", item="cx-inter")
    _save(conn_id, [{"id": "inter-cdb", "name": "CDB Banco Inter", "number": None,
                     "issuer": "BANCO INTER S.A.", "type": "FIXED_INCOME",
                     "subtype": "CDB", "balance": 7000.0}])
    res = db.sync_open_finance_caixinhas(conn_id, user_id)

    assert res["caixinhas_created"] == 0
    assert _pockets(user_id) == {}
    assert [float(r["balance"]) for r in list_of_fixed_income(user_id)] == [7000.0]
    assert "CDB Banco Inter" in {c["name"] for c in db.list_caixinha_candidates(user_id)}


def test_corretora_irma_nao_vira_caixinha_no_sync(user_id):
    """NU INVEST numa conexão Nubank: papel de terceiro, fica investimento — e as
    10 do dono continuam entrando ao lado dele."""
    from db.rv import list_of_fixed_income
    conn_id = _seed_connection(user_id, institution="Nubank", item="cx-nuinvest")
    nu_invest = {"id": "xp1", "name": "CDB NU INVEST", "number": None,
                 "issuer": "NU INVEST CORRETORA DE VALORES S.A.",
                 "type": "FIXED_INCOME", "subtype": "CDB", "balance": 5000.0}
    _save(conn_id, _nubank_raws() + [nu_invest])
    res = db.sync_open_finance_caixinhas(conn_id, user_id)

    assert res["caixinhas_created"] == 10
    assert len(_pockets(user_id)) == 10
    assert [float(r["balance"]) for r in list_of_fixed_income(user_id)] == [5000.0]


def test_raw_escalar_nao_derruba_a_tela_de_vinculo(user_id):
    """`raw` é jsonb: escalar ali fazia `.get` explodir, e em
    `list_caixinha_candidates` o erro saía como 500 na tela do Banqueiro."""
    from db import get_conn
    conn_id = _seed_connection(user_id, institution="Nubank")
    _save(conn_id, [{"id": "cx-raw", "name": "CDB Qualquer", "type": "FIXED_INCOME",
                     "subtype": "CDB", "balance": 120.0}])
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update open_finance_investments set raw = %s::jsonb "
                        "where connection_id = %s", ('"sem-issuer"', conn_id))
        conn.commit()

    assert db.sync_open_finance_caixinhas(conn_id, user_id)["caixinhas_created"] == 0
    nomes = {c["name"] for c in db.list_caixinha_candidates(user_id)}
    assert "CDB Qualquer" in nomes     # a tela abre, e ainda oferece o papel
