"""Plano Família, PR 3 — API de grupo e convites (E1-E9), Postgres real, HTTP, nos dois prefixos.

Mundo: família A (TA, MA1, MA2; capacidade 4), família B (TB, MB1) e U, usuário solto sem plano.
Controles negativos (um a um, resto do grupo verde; restaurado por backup + cmp) estão na tabela do relato do PR;
os positivos do grupo são os testes `test_*_aceita*`, `test_titular_remove_*`, `test_membro_sai_*`, `test_dissolucao_*`.
"""
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de, sql
from _billing_grants_helpers import conta
from api.v2.erros import ErroV2
from api.v2.familia import Convite, Dissolucao, VisaoMembro, VisaoTitular
from api.v2.me import Me
from core.services.plan_service import get_plan_tier
from db import family as fam
from db.connection import get_conn
from db.plan_grants import list_grants, upsert_grant
from test_api_app_sessao import completo

_V = [1_800_000_000]


def _agora():
    return datetime.now(timezone.utc)


def _usuario(plano="free", nome=None) -> int:
    uid = int(uuid.uuid4().int % 900_000_000) + 1
    db.ensure_user(uid)
    if plano == "free":
        conta(uid, "free", None, "")
    else:
        conta(uid, plano, _agora() + timedelta(days=30), "active")
        _V[0] += 1
        upsert_grant(uid, "stripe", f"sub_{uid}", plano, _agora() - timedelta(days=1),
                     _agora() + timedelta(days=30), _V[0])
    if nome:
        sql("update auth_accounts set display_name = %s, display_name_enc = null where user_id = %s", nome, uid)
    return completo(uid)


def _titular(nome) -> int:
    uid = _usuario("pro_max", nome)
    fam.create_group(uid)
    return uid


def _entra(owner, nome=None) -> int:
    m = _usuario("essencial", nome)
    fam.accept_invite(fam.create_invite(owner), m)
    return m


def _ref(uid) -> str:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from family_members where user_id = %s and left_at is null", (uid,))
        return str(cur.fetchone()["id"])


def _ativo(uid) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select 1 from family_members where user_id = %s and left_at is null", (uid,))
        return cur.fetchone() is not None


def _grants_family(uid, status="active"):
    return [g for g in list_grants(uid) if g["source"] == "family" and g["status"] == status]


@pytest.fixture(params=["/api/v2", "/api/app"])
def w(request, monkeypatch):
    monkeypatch.setenv("DASHBOARD_V2_TODOS", "1")
    monkeypatch.delenv("AGENTS_UI_ENABLED", raising=False)
    ta = _titular("Ana Souza Lima")
    # capacidade 4 (base 3 + 1 vaga): MA1, MA2 e uma vaga livre para o aceite
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from family_groups where owner_user_id = %s", (ta,))
        gid = cur.fetchone()["id"]
        cur.execute("insert into family_seat_purchases (group_id, source, external_ref, amount_cents, status)"
                    " values (%s, 'pix', %s, 1000, 'paid')", (gid, uuid.uuid4().hex))
        conn.commit()
    ma1, ma2 = _entra(ta, "Maria Aparecida Costa"), _entra(ta, "Joao Pedro")
    tb = _titular("Bia Barros")
    mb1 = _entra(tb, "Caio Barros")
    u = _usuario("free")
    clientes, corpos = {}, []

    def chamar(uid, metodo, caminho, **kw):
        c = clientes.get(uid)
        if c is None:
            c = clientes[uid] = TestClient(dashboard.app)
            c.headers["Authorization"] = f"Bearer {sessao_de(uid)['access']}"
        c.cookies.clear()
        if metodo != "GET":  # sem cookie, o CSRF só se isenta com corpo application/json (mesmo sem corpo)
            kw.setdefault("headers", {"content-type": "application/json"})
        r = c.request(metodo, request.param + caminho, **kw)
        corpos.append(r.text)
        return r

    return SimpleNamespace(p=request.param, ta=ta, ma1=ma1, ma2=ma2, tb=tb, mb1=mb1, u=u, chamar=chamar,
                           corpos=corpos)


def erro(r, status, code):
    assert r.status_code == status, r.text
    ErroV2.model_validate(r.json())
    assert r.json()["error"]["code"] == code, r.text


def convite(w, owner) -> str:
    r = w.chamar(owner, "POST", "/familia/convites")
    assert r.status_code == 201, r.text
    return Convite.model_validate(r.json()).token


def aceitar(w, uid, token):
    return w.chamar(uid, "POST", "/familia/convites/aceitar", json={"token": token})


# ── Isolamento ───────────────────────────────────────────────────────────────

def test_i1_membro_ve_so_o_papel(w):
    r = w.chamar(w.ma1, "GET", "/familia")
    assert r.status_code == 200 and r.json() == {"papel": "membro"}
    VisaoMembro.model_validate(r.json())


def test_i2_titular_ve_so_os_seus_com_chaves_exatas(w):
    r = w.chamar(w.ta, "GET", "/familia")
    assert r.status_code == 200, r.text
    j = r.json()
    VisaoTitular.model_validate(j)
    assert set(j) == {"papel", "capacidade", "membros", "convites_ativos"}
    assert j["papel"] == "titular" and j["capacidade"] == 4
    assert all(set(m) == {"ref", "nome", "status"} for m in j["membros"])
    # só os membros de A, só o primeiro nome
    assert [(m["ref"], m["nome"], m["status"]) for m in j["membros"]] == [
        (_ref(w.ma1), "Maria", "ativo"), (_ref(w.ma2), "Joao", "ativo")]
    assert "Costa" not in r.text and _ref(w.mb1) not in [m["ref"] for m in j["membros"]]


def test_fora_de_familia_e_404_e_o_titular_de_b_nao_ve_a(w):
    erro(w.chamar(w.u, "GET", "/familia"), 404, "not_found")  # free sem família: sem gate de plano, só não há o que ver
    erro(w.chamar(w.u, "POST", "/familia"), 402, "plan_selection_required")  # o resto segue no gate normal
    erro(w.chamar(_usuario("essencial"), "GET", "/familia"), 404, "not_found")
    j = w.chamar(w.tb, "GET", "/familia").json()
    assert [m["nome"] for m in j["membros"]] == ["Caio"]


def test_i3_titular_nao_remove_membro_de_outro_grupo(w):
    de_b = w.chamar(w.ta, "DELETE", f"/familia/membros/{_ref(w.mb1)}")
    inexistente = w.chamar(w.ta, "DELETE", "/familia/membros/999999999")
    erro(de_b, 404, "not_found")
    assert (de_b.status_code, de_b.json()) == (inexistente.status_code, inexistente.json())
    assert _ativo(w.mb1) and _grants_family(w.mb1)


def test_resolver_de_ref_so_alcanca_membro_ativo_do_proprio_grupo(w):
    """Camada própria: o db também filtra por grupo ao remover, então o HTTP sozinho não discrimina o resolver."""
    from db import family_views as views
    assert views.member_user_id(w.ta, _ref(w.ma1)) == w.ma1  # positivo
    assert views.member_user_id(w.ta, _ref(w.mb1)) is None
    assert views.member_user_id(w.tb, _ref(w.ma1)) is None
    fam.remove_member(w.ta, w.ma2)
    assert views.member_user_id(w.ta, _ref(w.ma1)) == w.ma1
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from family_members where user_id = %s", (w.ma2,))
        assert views.member_user_id(w.ta, str(cur.fetchone()["id"])) is None  # quem já saiu


@pytest.mark.parametrize("ref", ["abc", "-1", "1e3", "0x10", "9" * 40, " "])
def test_ref_malformado_e_404_igual(w, ref):
    erro(w.chamar(w.ta, "DELETE", f"/familia/membros/{ref}"), 404, "not_found")


def test_i4_membro_nao_remove_ninguem(w):
    erro(w.chamar(w.ma1, "DELETE", f"/familia/membros/{_ref(w.ma2)}"), 404, "not_found")
    assert _ativo(w.ma2)


def test_titular_nao_remove_a_si_mesmo(w):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from family_members where user_id = %s and left_at is null", (w.ta,))
        meu = str(cur.fetchone()["id"])
    erro(w.chamar(w.ta, "DELETE", f"/familia/membros/{meu}"), 404, "not_found")
    assert _ativo(w.ta)


def test_i5_token_de_a_entra_u_em_a_e_tb_recebe_already_in_family(w):
    t = convite(w, w.ta)
    erro(aceitar(w, w.tb, t), 409, "already_in_family")
    r = aceitar(w, w.u, t)  # usuário free: sem 402
    assert r.status_code == 200 and r.json() == {"papel": "membro"}
    VisaoMembro.model_validate(r.json())
    assert _ativo(w.u) and _ref(w.u) in [m["ref"] for m in w.chamar(w.ta, "GET", "/familia").json()["membros"]]


def test_i6_token_invalido_tem_um_so_corpo(w):
    usado = convite(w, w.ta)
    assert aceitar(w, w.u, usado).status_code == 200
    revogado = convite(w, w.tb)
    assert w.chamar(w.tb, "DELETE", "/familia/convites", json={"token": revogado}).status_code == 204
    expirado = convite(w, w.tb)
    sql("update family_invites set expires_at = now() - interval '1 second' where token_hash = %s",
        fam.hash_token(expirado))
    novo = _usuario("free")
    respostas = [aceitar(w, novo, t) for t in (usado, revogado, expirado, "token-que-nunca-existiu")]
    for r in respostas:
        erro(r, 404, "invite_invalid")
    assert len({(r.status_code, r.text) for r in respostas}) == 1


def test_i6_token_morto_com_titular_sem_cobertura_tambem_e_404_igual(w):
    """Token morto não pode revelar (402) que o titular está sem cobertura."""
    usado = convite(w, w.ta)
    assert aceitar(w, w.u, usado).status_code == 200
    revogado = convite(w, w.tb)
    assert w.chamar(w.tb, "DELETE", "/familia/convites", json={"token": revogado}).status_code == 204
    expirado = convite(w, w.tb)
    sql("update family_invites set expires_at = now() - interval '1 second' where token_hash = %s",
        fam.hash_token(expirado))
    for titular in (w.ta, w.tb):
        conta(titular, "free", None, "")  # titulares sem cobertura
    novo = _usuario("free")
    respostas = [aceitar(w, novo, t) for t in (usado, revogado, expirado, "token-que-nunca-existiu")]
    for r in respostas:
        erro(r, 404, "invite_invalid")
    assert len({(r.status_code, r.text) for r in respostas}) == 1


def test_i6_dissolvido_tambem_e_invite_invalid(w):
    t = convite(w, w.tb)
    assert fam.dissolve_group(w.tb)
    erro(aceitar(w, w.u, t), 404, "invite_invalid")


def test_i7_sair_derruba_o_tier_na_hora_e_segundo_sair_404(w):
    assert w.chamar(w.ma1, "GET", "/me").json()["plan_tier"] == "pro"
    assert w.chamar(w.ma1, "GET", "/me").json()["familia"] == {"papel": "membro"}
    r = w.chamar(w.ma1, "POST", "/familia/sair")
    assert r.status_code == 204 and r.content == b""
    me = w.chamar(w.ma1, "GET", "/me")
    Me.model_validate(me.json())
    assert me.json()["familia"] is None and me.json()["plan_tier"] == "essencial"
    assert get_plan_tier(w.ma1) == "essencial" and not _grants_family(w.ma1)
    erro(w.chamar(w.ma1, "POST", "/familia/sair"), 404, "not_found")
    assert _ativo(w.ma2) and _grants_family(w.ma2)  # o outro membro não foi tocado


def test_titular_nao_sai(w):
    erro(w.chamar(w.ta, "POST", "/familia/sair"), 409, "owner_cannot_leave")
    assert _ativo(w.ta)


def test_i8_dissolver_a_nao_toca_b(w):
    d = w.chamar(w.ta, "GET", "/familia/dissolucao").json()
    assert w.chamar(w.ta, "POST", "/familia/dissolucao", json={"confirmacao": d["confirmacao"]}).status_code == 204
    assert fam.get_group(w.ta) is None and not _ativo(w.ma1) and not _grants_family(w.ma1)
    assert fam.get_group(w.tb) and _ativo(w.mb1) and _grants_family(w.mb1)


def test_i9_membro_recebe_404_em_e3_e8_e9(w):
    erro(w.chamar(w.ma1, "POST", "/familia/convites"), 404, "not_found")
    erro(w.chamar(w.ma1, "DELETE", "/familia/convites", json={"token": "x"}), 404, "not_found")
    erro(w.chamar(w.ma1, "GET", "/familia/dissolucao"), 404, "not_found")
    erro(w.chamar(w.ma1, "POST", "/familia/dissolucao", json={"confirmacao": "x"}), 404, "not_found")
    assert fam.get_group(w.ta)


def test_i10_nenhuma_resposta_vaza_id_alheio_telefone_ou_email(w):
    sql("update auth_accounts set phone_e164 = '+5511999990000' where user_id = %s", w.ma1)
    t = convite(w, w.ta)
    w.chamar(w.ta, "GET", "/familia")
    w.chamar(w.ta, "GET", "/familia/dissolucao")
    w.chamar(w.ma1, "GET", "/familia")
    w.chamar(w.tb, "DELETE", f"/familia/membros/{_ref(w.ma1)}")
    aceitar(w, w.u, t)
    w.chamar(w.u, "GET", "/me")
    texto = "\n".join(w.corpos)
    for uid in (w.ta, w.ma1, w.ma2, w.tb, w.mb1, w.u):
        assert re.search(rf"(?<!\d){uid}(?!\d)", texto) is None, uid
    assert "@" not in texto and "5511999990000" not in texto and "t.local" not in texto


# ── Escrita: positivos, convite, dissolução, cobertura ──────────────────────

def test_titular_remove_membro_e_ele_perde_o_acesso(w):
    r = w.chamar(w.ta, "DELETE", f"/familia/membros/{_ref(w.ma1)}")
    assert r.status_code == 204
    assert not _ativo(w.ma1) and not _grants_family(w.ma1) and get_plan_tier(w.ma1) == "essencial"
    assert w.chamar(w.ta, "GET", "/familia").json()["membros"][0]["nome"] == "Joao"
    erro(w.chamar(w.ta, "DELETE", f"/familia/membros/{_ref(w.ma2)}x"), 404, "not_found")


def test_convite_traz_link_com_fragmento_e_conta_como_ativo(w):
    r = w.chamar(w.ta, "POST", "/familia/convites")
    c = Convite.model_validate(r.json())
    assert r.status_code == 201 and c.link.endswith(f"/familia/entrar#t={c.token}") and c.link.startswith("https://")
    assert datetime.fromisoformat(c.expira_em.isoformat()) > _agora() + timedelta(days=6)
    assert w.chamar(w.ta, "GET", "/familia").json()["convites_ativos"] == 1
    # o token cru não volta em nenhuma leitura
    assert c.token not in w.chamar(w.ta, "GET", "/familia").text
    assert c.token not in w.chamar(w.ta, "GET", "/familia/dissolucao").text
    assert w.chamar(w.ta, "DELETE", "/familia/convites", json={"token": c.token}).status_code == 204
    erro(w.chamar(w.ta, "DELETE", "/familia/convites", json={"token": c.token}), 404, "not_found")
    assert w.chamar(w.ta, "GET", "/familia").json()["convites_ativos"] == 0


def test_titular_de_b_nao_revoga_convite_de_a(w):
    t = convite(w, w.ta)
    erro(w.chamar(w.tb, "DELETE", "/familia/convites", json={"token": t}), 404, "not_found")
    assert aceitar(w, w.u, t).status_code == 200


def test_convite_sem_vaga_e_409_full(w):
    convite_de_a = convite(w, w.ta)
    assert aceitar(w, w.u, convite_de_a).status_code == 200  # 4 de 4
    erro(w.chamar(w.ta, "POST", "/familia/convites"), 409, "full")
    # B tem capacidade 3 e 2 pessoas: ainda emite
    assert w.chamar(w.tb, "POST", "/familia/convites").status_code == 201


def test_dissolucao_com_nonce_certo_apaga(w):
    d = w.chamar(w.ta, "GET", "/familia/dissolucao")
    j = Dissolucao.model_validate(d.json())
    assert (j.membros, j.convites, j.consentimentos) == (2, 0, 0)
    r = w.chamar(w.ta, "POST", "/familia/dissolucao", json={"confirmacao": j.confirmacao})
    assert r.status_code == 204
    me = w.chamar(w.ma1, "GET", "/me").json()  # D3: o membro só descobre pelo app
    assert me["familia"] is None and me["plan_tier"] == "essencial"
    erro(w.chamar(w.ta, "GET", "/familia/dissolucao"), 404, "not_found")


@pytest.mark.parametrize("nonce", ["", "qualquer-coisa", "0" * 64, "açaí"])
def test_dissolucao_com_nonce_errado_e_409_e_nao_apaga(w, nonce):
    erro(w.chamar(w.ta, "POST", "/familia/dissolucao", json={"confirmacao": nonce}), 409, "confirmation_mismatch")
    assert fam.get_group(w.ta) and _ativo(w.ma1)


def test_nonce_de_outro_titular_ou_velho_nao_vale(w):
    de_b = w.chamar(w.tb, "GET", "/familia/dissolucao").json()["confirmacao"]
    erro(w.chamar(w.ta, "POST", "/familia/dissolucao", json={"confirmacao": de_b}), 409, "confirmation_mismatch")
    velho = w.chamar(w.ta, "GET", "/familia/dissolucao").json()["confirmacao"]
    convite(w, w.ta)  # mudou a contagem de convites: o nonce antigo morre
    erro(w.chamar(w.ta, "POST", "/familia/dissolucao", json={"confirmacao": velho}), 409, "confirmation_mismatch")
    assert fam.get_group(w.ta)


def test_criar_grupo_exige_cobertura(w):
    sem = _usuario("free")
    erro(w.chamar(sem, "POST", "/familia"), 402, "plan_selection_required")  # free cai no gate normal
    # pago mas sem cobertura repassável (vitalício: expires nulo): passa o gate, cai na regra da família
    vitalicio = _usuario("pro_max")
    sql("update auth_accounts set plan_expires_at = null where user_id = %s", vitalicio)
    erro(w.chamar(vitalicio, "POST", "/familia"), 402, "family_coverage_required")
    novo = _usuario("pro_max")
    r = w.chamar(novo, "POST", "/familia")
    assert r.status_code == 201
    assert VisaoTitular.model_validate(r.json()).membros == []
    erro(w.chamar(novo, "POST", "/familia"), 409, "already_in_family")
    erro(w.chamar(w.ta, "POST", "/familia"), 409, "already_in_family")


# ── Limites ──────────────────────────────────────────────────────────────────

def test_11o_aceite_leva_429_no_envelope(w):
    for _ in range(10):
        erro(aceitar(w, w.u, "nao-existe"), 404, "invite_invalid")
    r = aceitar(w, w.u, "nao-existe")
    erro(r, 429, "rate_limited")
    assert int(r.headers["retry-after"]) > 0
    # o limite é por usuário: outro não é afetado
    erro(aceitar(w, _usuario("free"), "nao-existe"), 404, "invite_invalid")


def test_21a_emissao_leva_429(w):
    sql("update family_groups set tier = tier where owner_user_id = %s", w.tb)  # B: 2 de 3, sempre com vaga
    for _ in range(20):
        assert w.chamar(w.tb, "POST", "/familia/convites").status_code == 201
    erro(w.chamar(w.tb, "POST", "/familia/convites"), 429, "rate_limited")


def test_aceite_fora_da_allowlist_da_v2(w, monkeypatch):
    monkeypatch.delenv("DASHBOARD_V2_TODOS")
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", "")
    t = convite_direto(w.ta)
    r = aceitar(w, w.u, t)
    if w.p == "/api/v2":
        erro(r, 404, "dashboard_v2_disabled")
    else:  # o app não usa a allowlist (como usuario_do_app)
        assert r.status_code == 200


def convite_direto(owner) -> str:
    return fam.create_invite(owner)


def test_sem_credencial_401(w):
    r = TestClient(dashboard.app).post(w.p + "/familia/convites/aceitar", json={"token": "x"})
    erro(r, 401, "unauthenticated")


# ── Corrida ──────────────────────────────────────────────────────────────────

def test_dois_aceites_na_ultima_vaga_um_200_e_um_409(w):
    u2 = _usuario("free")
    t1, t2 = fam.create_invite(w.ta), fam.create_invite(w.ta)
    w.chamar(w.u, "GET", "/me")  # cria os clientes (sessão) antes das threads
    w.chamar(u2, "GET", "/me")
    barreira, saidas = threading.Barrier(2), {}

    def correr(uid, t):
        barreira.wait()
        saidas[uid] = aceitar(w, uid, t)

    ts = [threading.Thread(target=correr, args=(w.u, t1)), threading.Thread(target=correr, args=(u2, t2))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(r.status_code for r in saidas.values()) == [200, 409]
    perdeu = next(r for r in saidas.values() if r.status_code == 409)
    erro(perdeu, 409, "full")
    assert _ativo(w.u) != _ativo(u2)


# ── Rodada do Tester: sem plano, cobertura, credencial, nonce ────────────────

def _free_membro_de_grupo_suspenso(w):
    """u (free) aceita o convite de B; depois B perde a cobertura e o grupo fica suspenso: u sem plano."""
    from core.services.family_access import revoke_members
    assert aceitar(w, w.u, fam.create_invite(w.tb)).status_code == 200
    revoke_members(w.tb, "teste")
    erro(w.chamar(w.u, "GET", "/resumo-do-mes"), 402, "plan_selection_required")  # o gate de plano segue valendo no resto


def test_membro_sem_plano_ve_a_familia_sai_e_entra_em_outra(w):
    _free_membro_de_grupo_suspenso(w)
    r = w.chamar(w.u, "GET", "/familia")
    assert r.status_code == 200 and r.json() == {"papel": "membro"}
    assert w.chamar(w.u, "POST", "/familia/sair").status_code == 204
    assert aceitar(w, w.u, fam.create_invite(w.ta)).status_code == 200
    assert _ativo(w.u)


def test_convite_com_titular_sem_cobertura_ou_grupo_suspenso_e_402(w):
    from core.services.family_access import revoke_members
    assert w.chamar(w.tb, "POST", "/familia/convites").status_code == 201  # positivo: com cobertura emite
    revoke_members(w.ta, "teste")  # grupo suspenso
    erro(w.chamar(w.ta, "POST", "/familia/convites"), 402, "family_coverage_required")
    sql("update auth_accounts set plan_expires_at = null where user_id = %s", w.tb)  # vitalício: sem cobertura
    erro(w.chamar(w.tb, "POST", "/familia/convites"), 402, "family_coverage_required")


def test_aceite_exige_credencial_e_com_senha_aceita(w):
    import db_support
    t = fam.create_invite(w.ta)
    sql("update auth_accounts set password_hash = null where user_id = %s", w.u)
    db_support.invalidate_auth_user_cache(w.u)
    assert db.conta_sem_credencial(w.u)
    erro(aceitar(w, w.u, t), 403, "password_required")
    erro(w.chamar(w.u, "GET", "/familia"), 403, "password_required")
    assert not _ativo(w.u)
    sql("update auth_accounts set password_hash = 'x' where user_id = %s", w.u)
    db_support.invalidate_auth_user_cache(w.u)
    assert aceitar(w, w.u, t).status_code == 200


def test_nome_ilegivel_de_um_membro_nao_derruba_a_tela_do_titular(w, monkeypatch):
    real = db.get_auth_user

    def quebra(uid, *a, **k):
        if uid == w.ma1:
            raise ValueError("nao decifra")
        return real(uid, *a, **k)

    monkeypatch.setattr(db, "get_auth_user", quebra)
    for r in (w.chamar(w.ta, "GET", "/familia"), w.chamar(w.ta, "POST", "/familia/convites")):
        assert r.status_code in (200, 201), r.text
    nomes = sorted(m["nome"] for m in w.chamar(w.ta, "GET", "/familia").json()["membros"])
    assert nomes == ["", "Joao"]


def test_nonce_nao_vale_se_trocou_um_membro_por_outro(w):
    velho = w.chamar(w.ta, "GET", "/familia/dissolucao").json()["confirmacao"]
    fam.remove_member(w.ta, w.ma1)
    _entra(w.ta, "Novo Membro")  # mesmas contagens, outro membro
    erro(w.chamar(w.ta, "POST", "/familia/dissolucao", json={"confirmacao": velho}), 409, "confirmation_mismatch")
    assert fam.get_group(w.ta)


def test_nonce_de_grupo_dissolvido_nao_vale_no_grupo_recriado(w):
    novo = _titular("Nova Titular")  # grupo vazio: contagens 0/0/0 antes e depois
    velho = w.chamar(novo, "GET", "/familia/dissolucao").json()["confirmacao"]
    assert fam.dissolve_group(novo)
    fam.create_group(novo)
    erro(w.chamar(novo, "POST", "/familia/dissolucao", json={"confirmacao": velho}), 409, "confirmation_mismatch")
    assert fam.get_group(novo)


def test_aceite_sem_cobertura_do_titular_e_402_e_nao_consome_o_convite(w):
    t = convite(w, w.ta)
    conta(w.ta, "free", None, "")  # o titular perdeu a cobertura depois de emitir o convite
    erro(aceitar(w, w.u, t), 402, "family_coverage_required")
    assert not _ativo(w.u)
    conta(w.ta, "pro_max", _agora() + timedelta(days=30), "active")  # voltou a pagar: o mesmo convite vale
    assert aceitar(w, w.u, t).status_code == 200
    assert _ativo(w.u)


def test_dissolucao_confere_o_nonce_sob_o_lock_do_grupo(w):
    """Um aceite que commita entre a leitura do nonce e a dissolução não pode ser apagado sem ter sido visto."""
    nonce = w.chamar(w.ta, "GET", "/familia/dissolucao").json()["confirmacao"]
    res = []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from family_groups where owner_user_id = %s for update", (w.ta,))
        gid = cur.fetchone()["id"]
        t = threading.Thread(target=lambda: res.append(
            w.chamar(w.ta, "POST", "/familia/dissolucao", json={"confirmacao": nonce})))
        t.start()
        t.join(1.0)
        assert t.is_alive(), "a dissolução não esperou o lock do grupo"
        fam._insert_member(cur, gid, w.u, "member")  # o aceite que entra no meio
        conn.commit()
    t.join(10)
    erro(res[0], 409, "confirmation_mismatch")
    assert fam.get_group(w.ta) and _ativo(w.u)


def test_conta_agendada_para_exclusao_nao_aceita_nem_sai(w):
    t = fam.create_invite(w.ta)
    sql("update auth_accounts set deletion_status = 'scheduled', deletion_scheduled_for = now() + interval '7 days' where user_id in (%s, %s)", w.u, w.ma1)
    assert aceitar(w, w.u, t).status_code == 403
    assert w.chamar(w.ma1, "POST", "/familia/sair").status_code == 403
    assert _ativo(w.ma1) and not _ativo(w.u)
