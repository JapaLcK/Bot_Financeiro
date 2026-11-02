"""Plano Família, PR 4: consentimento do membro e leitura do titular, Postgres real, HTTP, nos dois prefixos.

Mundo: família A (TA, MA1, MA2; capacidade 4), família B (TB, MB1), U free solto e U2 pago solto.
MA1 libera agregados e lançamentos; MA2 não libera nada. Os números `t1`..`t18` são os 18 casos do plano.
Controles negativos (um a um, restaurado por backup + cmp) estão na tabela do relato do PR; os positivos do grupo
são t2 (agregados, lançamentos e página 2), t8 (histórico depois de sair) e t17 (mês dentro do permitido).
"""
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de
from api.v2.erros import ErroV2
from api.v2.familia_dados import Agregados, Historico, LancamentosMembro
from db import family as fam
from db import family_consents as fc
from db.connection import get_conn
from test_api_v2_familia import _entra, _ref, _titular, _usuario
from utils_date import add_months, now_tz, today_tz

SEGREDO, NOTA, INTERNO = "zzx-segredo-1", "nota-zzx-privada", "zzx-interno"
PROIBIDAS = {"descricao", "mensagem", "id", "conta_id", "cartao_id", "instituicao", "origem", "dias"}
CHAVES_ITEM = {"data", "tipo", "valor", "moeda", "descricao", "categoria", "parcela"}


def _semeia(uid):
    db.add_launch_and_update_balance(uid, "receita", 1000, "salario", None)
    db.add_launch_and_update_balance(uid, "despesa", 30, SEGREDO, NOTA, "mercado")
    db.add_launch_and_update_balance(uid, "despesa", 20, "padaria", None, "mercado")
    db.add_launch_and_update_balance(uid, "despesa", 500, INTERNO, None, is_internal_movement=True)
    cartao = db.create_card(uid, "Nubank", closing_day=28, due_day=5)
    db.add_credit_purchase(uid, cartao, 80, "cartao-zzx", "nota-cartao", today_tz())


@pytest.fixture(params=["/api/v2", "/api/app"])
def w(request, monkeypatch):
    monkeypatch.setenv("DASHBOARD_V2_TODOS", "1")
    monkeypatch.delenv("AGENTS_UI_ENABLED", raising=False)
    ta = _titular("Ana Souza Lima")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from family_groups where owner_user_id = %s", (ta,))
        cur.execute("insert into family_seat_purchases (group_id, source, external_ref, amount_cents, status)"
                    " values (%s, 'pix', %s, 1000, 'paid')", (cur.fetchone()["id"], uuid.uuid4().hex))
        conn.commit()
    ma1, ma2 = _entra(ta, "Maria Aparecida Costa"), _entra(ta, "Joao Pedro")
    tb = _titular("Bia Barros")
    mb1 = _entra(tb, "Caio Barros")
    u, u2 = _usuario("free"), _usuario("pro_max")
    _semeia(ma1)
    clientes = {}

    def chamar(uid, metodo, caminho, **kw):
        c = clientes.get(uid)
        if c is None:
            c = clientes[uid] = TestClient(dashboard.app)
            c.headers["Authorization"] = f"Bearer {sessao_de(uid)['access']}"
        c.cookies.clear()
        if metodo != "GET":
            kw.setdefault("headers", {"content-type": "application/json"})
        return c.request(metodo, request.param + caminho, **kw)

    ns = SimpleNamespace(p=request.param, ta=ta, ma1=ma1, ma2=ma2, tb=tb, mb1=mb1, u=u, u2=u2, chamar=chamar)
    ns.conceder = lambda uid, *escopos: [fc.grant_consent(uid, e) for e in escopos]
    ns.ler = lambda quem, alvo, rec="agregados", **p: chamar(quem, "GET", f"/familia/membros/{_ref(alvo)}/{rec}", params=p)
    conceder_tudo = ("aggregates", "transactions")
    ns.conceder(ma1, *conceder_tudo)
    return ns


def _log(uid) -> list:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select scope, recurso, group_id from family_access_log where member_user_id = %s order by id", (uid,))
        return cur.fetchall()


def erro(r, status, code):
    assert r.status_code == status, r.text
    ErroV2.model_validate(r.json())
    assert r.json()["error"]["code"] == code, r.text


def chaves(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from chaves(v)
    elif isinstance(x, list):
        for v in x:
            yield from chaves(v)


def t_mes():
    n = now_tz()
    return f"{n.year:04d}-{n.month:02d}"


# ── 1-2: consentimento e leitura legítima ────────────────────────────────────

def test_t1_sem_consentimento_403_sem_dado_e_sem_log(w, monkeypatch):
    chamadas = []
    for mod, nome in ((db.resumo_mes, "resumo_do_mes"), (db.gastos, "ler_mes"), (db.budgets, "get_budgets_status_for_month"),
                      (db.lancamentos_leitura, "pagina")):
        monkeypatch.setattr(mod, nome, lambda *a, _n=nome, **k: chamadas.append(_n))
    for rec in ("agregados", "lancamentos"):
        r = w.ler(w.ta, w.ma2, rec)
        erro(r, 403, "consent_required")
        assert set(r.json()) == {"error"} and SEGREDO not in r.text
    assert _log(w.ma2) == [] and chamadas == []  # a consulta financeira nem roda


def test_t2_titular_le_o_que_o_membro_liberou_e_os_numeros_batem(w, monkeypatch):
    visto = []
    for mod, nome in ((db.resumo_mes, "resumo_do_mes"), (db.gastos, "ler_mes"),
                      (db.budgets, "get_budgets_status_for_month"), (db.lancamentos_leitura, "pagina")):
        monkeypatch.setattr(mod, nome, (lambda orig, n: lambda uid, *a, **k: (visto.append((n, uid)), orig(uid, *a, **k))[1])(
            getattr(mod, nome), nome))
    r = w.ler(w.ta, w.ma1)
    assert r.status_code == 200, r.text
    a = Agregados.model_validate(r.json())
    meu = w.chamar(w.ma1, "GET", "/resumo-do-mes").json()
    assert (a.entrou, a.saiu) == (Decimal(meu["entrou"]), Decimal(meu["saiu"]))
    assert a.entrou > 0 and a.saiu > 0 and a.mes == t_mes()
    assert [x[0] for x in visto if x[0] != "pagina"] and {x[1] for x in visto} == {w.ma1}  # consultas com o uid do MEMBRO
    assert len(_log(w.ma1)) == 1 and _log(w.ma1)[0]["recurso"] == f"agregados:{t_mes()}"
    # lançamentos: página 1 e página 2 pelo cursor, uma linha de log por página
    p1 = w.ler(w.ta, w.ma1, "lancamentos", limite=2)
    assert p1.status_code == 200 and p1.json()["proximo"], p1.text
    p2 = w.ler(w.ta, w.ma1, "lancamentos", limite=2, cursor=p1.json()["proximo"])
    assert p2.status_code == 200 and LancamentosMembro.model_validate(p2.json()).itens
    assert [x for x in visto if x[0] == "pagina" and x[1] != w.ma1] == []
    assert len(_log(w.ma1)) == 3
    assert w.chamar(w.ma1, "GET", "/familia").json()["permissoes"] == ["agregados", "lancamentos"]  # ordem por c.id


# ── 3-7: quem está fora vê 404 igual a ref que não existe ─────────────────────

def test_t3_outro_grupo_404_identico_ao_inexistente(w):
    r = w.chamar(w.tb, "GET", f"/familia/membros/{_ref(w.ma1)}/agregados")
    inexistente = w.chamar(w.tb, "GET", "/familia/membros/987654321/agregados")
    erro(r, 404, "not_found")
    assert r.json() == inexistente.json() and r.status_code == inexistente.status_code
    assert _log(w.ma1) == []


def test_t4_membro_nao_le_membro(w):
    erro(w.ler(w.ma2, w.ma1), 404, "not_found")
    erro(w.ler(w.ma2, w.ma1, "lancamentos"), 404, "not_found")
    assert _log(w.ma1) == []


def test_t5_membro_com_o_proprio_ref_ou_o_do_titular(w):
    erro(w.ler(w.ma1, w.ma1), 404, "not_found")
    r = w.chamar(w.ma1, "GET", f"/familia/membros/{_ref(w.ma2)}/agregados")
    erro(r, 404, "not_found")
    assert _log(w.ma1) == [] and _log(w.ma2) == []


def test_t6_solto_nao_le_ninguem(w):
    assert w.ler(w.u, w.ma1).status_code == 402  # free: o `usuario_atual` recusa antes de qualquer consulta
    erro(w.ler(w.u2, w.ma1), 404, "not_found")  # pago e sem família: o ref não resolve
    assert SEGREDO not in w.ler(w.u2, w.ma1).text and _log(w.ma1) == []


@pytest.mark.parametrize("ref", ["abc", "-1", "9" * 19, "0", "1e3"])
def test_t7_ref_malformado_404(w, ref):
    for rec in ("agregados", "lancamentos"):
        erro(w.chamar(w.ta, "GET", f"/familia/membros/{ref}/{rec}"), 404, "not_found")
    erro(w.chamar(w.ta, "GET", f"/familia/membros/{w.ma1}/agregados"), 404, "not_found")  # user_id no lugar do ref
    assert _log(w.ma1) == []


# ── 8-10: o estado muda entre uma leitura e a outra ───────────────────────────

def test_t8_membro_sai_titular_perde_o_ref_e_o_membro_ve_o_historico(w):
    ref = _ref(w.ma1)
    assert w.ler(w.ta, w.ma1).status_code == 200
    assert w.chamar(w.ma1, "POST", "/familia/sair").status_code == 204
    erro(w.chamar(w.ta, "GET", f"/familia/membros/{ref}/agregados"), 404, "not_found")
    h = w.chamar(w.ma1, "GET", "/familia/historico")
    assert h.status_code == 200, h.text
    j = Historico.model_validate(h.json())
    assert [(i.escopo, i.mes, i.quem) for i in j.itens] == [("agregados", t_mes(), "Ana")]


def test_t9_leu_revogou_leu_de_novo(w):
    assert w.ler(w.ta, w.ma1).status_code == 200
    assert w.chamar(w.ma1, "DELETE", "/familia/permissoes/agregados").status_code == 200
    erro(w.ler(w.ta, w.ma1), 403, "consent_required")
    assert len(_log(w.ma1)) == 1


def test_t10_revogacao_durante_a_leitura_descarta_o_dado(w, monkeypatch):
    def revoga(orig, escopo):
        def f(*a, **k):
            fc.revoke_consent(w.ma1, escopo)
            return orig(*a, **k)
        return f
    monkeypatch.setattr(db.resumo_mes, "resumo_do_mes", revoga(db.resumo_mes.resumo_do_mes, "aggregates"))
    r = w.ler(w.ta, w.ma1)
    erro(r, 403, "consent_required")
    assert "entrou" not in r.text and _log(w.ma1) == []
    monkeypatch.setattr(db.lancamentos_leitura, "pagina", revoga(db.lancamentos_leitura.pagina, "transactions"))
    fc.grant_consent(w.ma1, "aggregates")
    fc.grant_consent(w.ma1, "transactions")
    r = w.ler(w.ta, w.ma1, "lancamentos")
    erro(r, 403, "consent_required")
    assert "itens" not in r.text and _log(w.ma1) == []


def test_t10b_falha_ao_gravar_o_log_nao_entrega_o_dado(w, monkeypatch):
    def quebra(*a, **k):
        raise RuntimeError("log fora do ar")
    monkeypatch.setattr(fc, "log_read", quebra)
    try:
        r = w.ler(w.ta, w.ma1)
    except RuntimeError:
        return  # o cliente de teste relança: o dado não saiu
    assert r.status_code == 500 and "entrou" not in r.text and SEGREDO not in r.text


# ── 11-12: o que sai é só o que a lista branca deixa ──────────────────────────

def test_t11_agregados_sem_descricao_nem_ids(w):
    r = w.ler(w.ta, w.ma1)
    assert r.status_code == 200
    assert not PROIBIDAS & set(chaves(r.json())), set(chaves(r.json()))
    assert SEGREDO not in r.text and NOTA not in r.text and INTERNO not in r.text
    assert r.json()["categorias"], r.text


def test_t12_lancamentos_com_as_chaves_exatas(w):
    itens = []
    cursor = None
    for _ in range(10):
        r = w.ler(w.ta, w.ma1, "lancamentos", limite=2, **({"cursor": cursor} if cursor else {}))
        assert r.status_code == 200, r.text
        itens += r.json()["itens"]
        cursor = r.json()["proximo"]
        if not cursor:
            break
    assert itens and all(set(i) == CHAVES_ITEM for i in itens)
    corpo = str(itens)
    assert SEGREDO in corpo and NOTA not in corpo and INTERNO not in corpo  # descrição liberada; nota e interno, não


# ── 13-14: dependência entre as permissões ────────────────────────────────────

def test_t13_so_agregados_nao_abre_lancamentos_e_lancamentos_sem_agregados_409(w, monkeypatch):
    w.conceder(w.ma2, "aggregates")
    assert w.ler(w.ta, w.ma2).status_code == 200
    chamadas = []
    monkeypatch.setattr(db.lancamentos_leitura, "pagina", lambda *a, **k: chamadas.append("pagina"))
    erro(w.ler(w.ta, w.ma2, "lancamentos"), 403, "consent_required")
    assert chamadas == []  # a consulta financeira do membro nem roda
    r = w.chamar(w.mb1, "POST", "/familia/permissoes", json={"escopo": "lancamentos"})
    erro(r, 409, "aggregates_required")
    assert w.chamar(w.mb1, "POST", "/familia/permissoes", json={"escopo": "x"}).status_code == 422


def test_t14_revogar_agregados_derruba_lancamentos(w):
    r = w.chamar(w.ma1, "DELETE", "/familia/permissoes/agregados")
    assert r.status_code == 200 and r.json() == {"permissoes": []}
    erro(w.ler(w.ta, w.ma1, "lancamentos"), 403, "consent_required")
    ver = w.chamar(w.ta, "GET", "/familia").json()["membros"]
    assert all(m["permissoes"] == [] for m in ver)
    again = w.chamar(w.ma1, "DELETE", "/familia/permissoes/agregados")  # idempotente
    assert again.status_code == 200 and again.json() == {"permissoes": []}
    g = w.chamar(w.ma1, "POST", "/familia/permissoes", json={"escopo": "agregados"})
    assert g.json() == {"permissoes": ["agregados"]}
    assert w.chamar(w.ma1, "GET", "/familia").json()["permissoes"] == ["agregados"]


# ── 15-16: histórico ──────────────────────────────────────────────────────────

def test_t15_historico_so_do_proprio_membro(w):
    w.conceder(w.ma2, "aggregates")
    assert w.ler(w.ta, w.ma2).status_code == 200
    for _ in range(3):
        assert w.ler(w.ta, w.ma1).status_code == 200
    h1 = w.chamar(w.ma1, "GET", "/familia/historico", params={"limite": 2}).json()
    assert len(h1["itens"]) == 2 and h1["proximo"]
    h2 = w.chamar(w.ma1, "GET", "/familia/historico", params={"limite": 2, "antes": h1["proximo"]}).json()
    assert len(h2["itens"]) == 1 and h2["proximo"] is None
    assert len(w.chamar(w.ma2, "GET", "/familia/historico").json()["itens"]) == 1
    assert w.chamar(w.u, "GET", "/familia/historico").json() == {"itens": [], "proximo": None}
    assert w.chamar(w.ma1, "GET", "/familia/historico", params={"antes": "1; drop"}).status_code == 422
    assert w.chamar(w.ma1, "GET", "/familia/historico", params={"limite": 0}).status_code == 422


def test_t16_grupo_dissolvido_404_e_o_historico_fica(w):
    ref = _ref(w.ma1)
    assert w.ler(w.ta, w.ma1).status_code == 200
    previa = w.chamar(w.ta, "GET", "/familia/dissolucao").json()
    r = w.chamar(w.ta, "POST", "/familia/dissolucao", json={"confirmacao": previa["confirmacao"]})
    assert r.status_code == 204, r.text
    erro(w.chamar(w.ta, "GET", f"/familia/membros/{ref}/agregados"), 404, "not_found")
    assert len(w.chamar(w.ma1, "GET", "/familia/historico").json()["itens"]) == 1
    assert _log(w.ma1)[0]["group_id"] is None


# ── 17-18: mês e cache ────────────────────────────────────────────────────────

def test_t17_mes_invalido_futuro_ou_antes_da_entrada_422_sem_log(w):
    for mes in ("2999-01", "2026-13", "abcd", "2026-1"):
        for rec in ("agregados", "lancamentos"):
            assert w.ler(w.ta, w.ma1, rec, mes=mes).status_code == 422, (mes, rec)
    assert w.ler(w.ta, w.ma1, "agregados", mes="2000-01").status_code == 422  # antes de ter entrado na família
    assert w.ler(w.ta, w.ma1, "lancamentos", mes="2000-01").status_code == 422
    assert _log(w.ma1) == []
    with get_conn() as conn, conn.cursor() as cur:  # positivo: entrou há 90 dias, o mês passado já vale
        cur.execute("update family_members set joined_at = now() - interval '90 days' where user_id = %s", (w.ma1,))
        conn.commit()
    ano, m = add_months(now_tz().year, now_tz().month, -1)
    assert w.ler(w.ta, w.ma1, mes=f"{ano:04d}-{m:02d}").status_code == 200
    assert len(_log(w.ma1)) == 1


def test_t18_titular_recebe_no_store(w):
    for rec in ("agregados", "lancamentos"):
        r = w.ler(w.ta, w.ma1, rec)
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-store" and r.headers["pragma"] == "no-cache"
    for r in (w.ler(w.ta, w.ma2), w.chamar(w.ta, "GET", "/familia/membros/99999999/lancamentos")):  # 403 sem consentimento, 404 ref inexistente
        assert r.status_code in (403, 404)
        assert r.headers["cache-control"] == "no-store" and r.headers["pragma"] == "no-cache"
