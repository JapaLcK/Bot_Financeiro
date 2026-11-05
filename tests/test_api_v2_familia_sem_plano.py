"""Plano Família: quem está sem plano não vê nem concede, só sai, revoga e aceita convite. Postgres real, HTTP, nos dois prefixos.

Fechadas (usuario_atual, 402): GET /familia, POST /familia/permissoes, GET /familia/historico, GET /familia/casa.
Abertas (usuario_logado): POST /familia/sair, DELETE /familia/permissoes/{escopo}, POST /familia/convites/aceitar.
O fixture `w` e os helpers vêm de test_api_v2_familia (mundo: família A, família B, U free solto).
Controles negativos (relato do PR): uma rota fechada de volta a `usuario_logado` deixa o caso dela vermelho;
o DELETE em `usuario_atual` deixa `test_sem_plano_revoga_sai_e_aceita_sem_tocar_nos_outros_grupos` vermelho.
"""
import pytest

from _billing_grants_helpers import conta
from api.v2.familia_casa import Casa
from db import family as fam
from db import family_consents as fc
from db.connection import get_conn
from test_api_v2_familia import (  # noqa: F401 (w é fixture)
    _ativo, _free_membro_de_grupo_suspenso, _usuario, aceitar, erro, w)

FECHADAS = [("GET", "/familia", None), ("POST", "/familia/permissoes", {"escopo": "agregados"}),
            ("GET", "/familia/historico", None), ("GET", "/familia/casa", None)]
IDS = [f"{m} {p}" for m, p, _ in FECHADAS]
CODIGOS = ("plan_selection_required", "subscription_required")


def _chama(w, uid, rota):
    metodo, caminho, corpo = rota
    return w.chamar(uid, metodo, caminho, **({"json": corpo} if corpo else {}))


def _consentimentos():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select member_user_id, scope, revoked_at is not null as revogado from family_consents order by id")
        return [tuple(r.values()) for r in cur.fetchall()]


def _titular_sem_plano(w):
    conta(w.ta, "free", None, "")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update plan_grants set status = 'revoked' where user_id = %s and source <> 'family'", (w.ta,))
        conn.commit()


@pytest.mark.parametrize("rota", FECHADAS, ids=IDS)
def test_rota_fechada_da_402_para_membro_e_titular_sem_plano(w, rota):
    _free_membro_de_grupo_suspenso(w)  # w.u: free, membro de B suspenso
    _titular_sem_plano(w)
    solto = _usuario("free")
    for quem in (w.u, w.ta, solto):
        r = _chama(w, quem, rota)
        assert r.status_code == 402, (quem, r.text)
        assert r.json()["error"]["code"] in CODIGOS and set(r.json()) == {"error"}
        assert set(r.json()["error"]) == {"code", "message"}
    # a recusa não depende do grupo: o corpo do membro e o de quem nunca teve família são o mesmo
    assert _chama(w, w.u, rota).json() == _chama(w, solto, rota).json()


def test_recusa_do_conceder_nao_grava_nada(w):
    _free_membro_de_grupo_suspenso(w)
    antes = _consentimentos()
    erro(_chama(w, w.u, FECHADAS[1]), 402, "plan_selection_required")
    assert _consentimentos() == antes and not any(m == w.u for m, _, _ in antes)


def test_sem_plano_revoga_sai_e_aceita_sem_tocar_nos_outros_grupos(w):
    assert aceitar(w, w.u, fam.create_invite(w.tb)).status_code == 200
    for membro in (w.u, w.ma1, w.mb1):
        fc.grant_consent(membro, "aggregates")
        fc.grant_consent(membro, "transactions")
    from core.services.family_access import revoke_members
    revoke_members(w.tb, "teste")
    erro(w.chamar(w.u, "GET", "/resumo-do-mes"), 402, "plan_selection_required")  # u está sem plano
    outros = [c for c in _consentimentos() if c[0] != w.u]
    r = w.chamar(w.u, "DELETE", "/familia/permissoes/agregados")  # revogar agregados leva lancamentos junto
    assert r.status_code == 200 and r.json() == {"permissoes": []}, r.text
    assert sorted((s, rev) for m, s, rev in _consentimentos() if m == w.u) == [("aggregates", True), ("transactions", True)]
    assert [c for c in _consentimentos() if c[0] != w.u] == outros  # MA1 e MB1 seguem com o que liberaram
    assert w.chamar(w.u, "POST", "/familia/sair").status_code == 204
    assert not _ativo(w.u) and _ativo(w.ma1) and _ativo(w.mb1)
    assert aceitar(w, w.u, fam.create_invite(w.ta)).status_code == 200  # free solto aceita convite
    assert _ativo(w.u)


def test_com_plano_os_corpos_nao_mudam(w):
    assert w.chamar(w.ma1, "GET", "/familia").json() == {"papel": "membro", "permissoes": []}
    r = w.chamar(w.ma1, "POST", "/familia/permissoes", json={"escopo": "agregados"})
    assert r.status_code == 200 and r.json() == {"permissoes": ["agregados"]}
    assert w.chamar(w.ma1, "GET", "/familia/historico").json() == {"itens": [], "proximo": None}
    for quem in (w.ma1, w.ta):
        r = w.chamar(quem, "GET", "/familia/casa")
        assert r.status_code == 200, r.text
        assert Casa.model_validate(r.json()).metas == []
    j = w.chamar(w.ta, "GET", "/familia").json()
    assert set(j) == {"papel", "capacidade", "membros", "convites_ativos"}


def test_membro_com_plano_proprio_em_grupo_suspenso_segue_como_hoje(w):
    """Decisão do dono (P2 = A): vale o plano da PESSOA. `_cobertura` do titular não entra nestas rotas."""
    from core.services.family_access import revoke_members
    assert w.chamar(w.ma1, "GET", "/familia").status_code == 200
    revoke_members(w.ta, "teste")  # grupo A suspenso: ma1 perde só o grant da família, o plano individual fica
    assert w.chamar(w.ma1, "GET", "/resumo-do-mes").status_code == 200  # tem plano
    assert w.chamar(w.ma1, "GET", "/familia").json() == {"papel": "membro", "permissoes": []}
    r = w.chamar(w.ma1, "POST", "/familia/permissoes", json={"escopo": "agregados"})
    assert r.status_code == 200 and r.json() == {"permissoes": ["agregados"]}
    assert w.chamar(w.ma1, "GET", "/familia/historico").status_code == 200
    assert w.chamar(w.ma1, "GET", "/familia/casa").status_code == 200
