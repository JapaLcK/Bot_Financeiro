"""Plano Família, PR 1 — schema e db/family.py contra Postgres real (sem mock de db)."""
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import db
from db import family as fam
from db import get_conn
from db.schema import init_db
from _billing_grants_helpers import conta


def _u() -> int:
    uid = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(uid)
    conta(uid, "pro_max", datetime.now(timezone.utc) + timedelta(days=30), "active")  # titular precisa de cobertura p/ o aceite
    return uid


def _sql(q, args=(), fetch=False):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(q, args)
        rows = cur.fetchall() if fetch else None
        conn.commit()
        return rows


def _vaga(gid, owner, ref=None, status="paid", valid_until=None):
    _sql("insert into family_seat_purchases (group_id, source, external_ref, amount_cents, status, valid_until)"
         " values (%s,'pix',%s,1000,%s,%s)", (gid, ref or uuid.uuid4().hex, status, valid_until))


def _entra(owner, u=None):
    """Único caminho de membro: convite aceito."""
    u = u or _u()
    fam.accept_invite(fam.create_invite(owner), u)
    return u


def _casa(n_membros=0):
    owner = _u()
    gid = fam.create_group(owner)
    membros = []
    for _ in range(n_membros):
        m = _u()
        _entra(owner, m)
        membros.append(m)
    return owner, gid, membros


def test_schema_aplica_duas_vezes():
    init_db()
    init_db()


def test_uma_familia_ativa_por_pessoa_e_volta_apos_sair():
    a, _, (m,) = _casa(1)
    b = _u()
    gb = fam.create_group(b)
    with pytest.raises(fam.FamilyError, match="already_in_family"):
        _entra(b, m)  # negativo: m está ativo na família de a
    fam.remove_member(a, m, "left")
    _entra(b, m)  # positivo: após left_at entra em outra
    assert [x["user_id"] for x in fam.list_members(b)] == [b, m]
    row = _sql("select left_at, left_reason from family_members where user_id=%s and group_id<>%s", (m, gb), fetch=True)
    assert row[0]["left_at"] is not None and row[0]["left_reason"] == "left"


def test_indice_unico_parcial_segura_mesmo_sem_a_checagem_do_app():
    a, _, (m,) = _casa(1)
    b = _u()
    gb = fam.create_group(b)
    with pytest.raises(Exception, match="uq_family_members_active_user"):
        _sql("insert into family_members (group_id, user_id, role) values (%s,%s,'member')", (gb, m))


def test_convite_valido_aceito_e_uso_unico():
    a, gid, _ = _casa()
    tok = fam.create_invite(a)
    assert tok not in str(_sql("select * from family_invites where group_id=%s", (gid,), fetch=True))  # só o hash
    m = _u()
    assert fam.accept_invite(tok, m) == gid
    with pytest.raises(fam.FamilyError, match="invite_invalid"):
        fam.accept_invite(tok, _u())  # usado


def test_convite_expirado_revogado_e_desconhecido_recusados():
    a, gid, _ = _casa()
    exp = fam.create_invite(a)
    _sql("update family_invites set expires_at = now() - interval '1 second' where token_hash=%s", (fam.hash_token(exp),))
    rev = fam.create_invite(a)
    assert fam.revoke_invite(a, rev)
    for t in (exp, rev, "nao-existe"):
        with pytest.raises(fam.FamilyError, match="invite_invalid"):
            fam.accept_invite(t, _u())
    assert len(fam.list_members(a)) == 1


def test_convite_dura_7_dias():
    a, gid, _ = _casa()
    fam.create_invite(a)
    r = _sql("select expires_at - now() as d from family_invites where group_id=%s", (gid,), fetch=True)[0]["d"]
    assert 6 < r.days + r.seconds / 86400 <= 7


def test_teto_base_vaga_paga_e_maximo_6():
    a, gid, _ = _casa(2)  # titular + 2 = 3 (base)
    with pytest.raises(fam.FamilyError, match="full"):
        _entra(a)  # 4º sem vaga paga
    _vaga(gid, a)
    _entra(a)  # 4º com 1 vaga
    for _ in range(2):
        _vaga(gid, a)
    _vaga(gid, a, status="pending")
    _vaga(gid, a, status="canceled")
    _vaga(gid, a, valid_until="2000-01-01")  # vencida
    _entra(a)
    _entra(a)  # 6
    assert fam.seat_capacity(a) == 6
    _vaga(gid, a)  # 4ª extra paga: ainda assim teto 6
    assert fam.seat_capacity(a) == 6
    with pytest.raises(fam.FamilyError, match="full"):
        _entra(a)  # 7º
    tok = fam.create_invite(a)
    with pytest.raises(fam.FamilyError, match="full"):
        fam.accept_invite(tok, _u())
    # convite recusado por teto não fica queimado
    assert _sql("select used_at from family_invites where token_hash=%s", (fam.hash_token(tok),), fetch=True)[0]["used_at"] is None


def test_dois_aceites_concorrentes_na_ultima_vaga_so_um_entra():
    a, gid, _ = _casa(1)  # 2 de 3: resta 1 vaga
    t1, t2 = fam.create_invite(a), fam.create_invite(a)
    barreira = threading.Barrier(2)
    res = []

    def go(tok):
        u = _u()
        barreira.wait()
        try:
            fam.accept_invite(tok, u)
            res.append("ok")
        except fam.FamilyError as e:
            res.append(e.args[0])

    ths = [threading.Thread(target=go, args=(t,)) for t in (t1, t2)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    assert sorted(res) == ["full", "ok"]
    assert len(fam.list_members(a)) == 3


def test_consentimento_transactions_exige_aggregates_e_revogacao_em_cascata():
    a, gid, (m,) = _casa(1)
    with pytest.raises(fam.FamilyError, match="aggregates_required"):
        fam.grant_consent(m, "transactions")
    with pytest.raises(fam.FamilyError, match="bad_scope"):
        fam.grant_consent(m, "tudo")
    fam.grant_consent(m, "aggregates")
    fam.grant_consent(m, "aggregates")  # idempotente
    fam.grant_consent(m, "transactions")
    assert [(c["member_user_id"], c["scope"]) for c in fam.list_consents(a)] == [(m, "aggregates"), (m, "transactions")]
    fam.revoke_consent(m, "aggregates")
    assert fam.list_consents(a) == []
    with pytest.raises(fam.FamilyError, match="aggregates_required"):
        fam.grant_consent(m, "transactions")


def test_consentimento_e_unidirecional_membro_para_titular():
    a, gid, (m,) = _casa(1)
    with pytest.raises(fam.FamilyError, match="not_member"):
        fam.grant_consent(a, "aggregates")  # titular não consente "para" o membro
    with pytest.raises(Exception):  # nem por SQL: viewer = member
        _sql("insert into family_consents (group_id, member_user_id, viewer_user_id, scope) values (%s,%s,%s,'aggregates')", (gid, m, m))
    fam.grant_consent(m, "aggregates")
    row = _sql("select viewer_user_id from family_consents where group_id=%s", (gid,), fetch=True)[0]
    assert row["viewer_user_id"] == a


def test_saida_revoga_consentimentos_e_item_da_casa_fica():
    a, gid, (m,) = _casa(1)
    fam.grant_consent(m, "aggregates")
    _sql("insert into family_goals (group_id, created_by_user_id, nome, alvo_cents) values (%s,%s,'viagem',1000)", (gid, m))
    fam.remove_member(a, m)
    assert fam.list_consents(a) == []
    assert _sql("select count(*) n from family_goals where group_id=%s", (gid,), fetch=True)[0]["n"] == 1
    with pytest.raises(fam.FamilyError, match="not_member"):
        fam.remove_member(a, a)  # titular não sai por aqui


def test_excluir_conta_do_criador_anula_created_by_e_mantem_item():
    a, gid, (m,) = _casa(1)
    _sql("insert into family_budgets (group_id, created_by_user_id, categoria, limite_cents) values (%s,%s,'mercado',5000)", (gid, m))
    _sql("delete from users where id=%s", (m,))
    r = _sql("select created_by_user_id from family_budgets where group_id=%s", (gid,), fetch=True)
    assert r == [{"created_by_user_id": None}]


def test_dissolucao_apaga_casa_e_mantem_log_com_group_null():
    a, gid, (m,) = _casa(1)
    tok = fam.create_invite(a)
    fam.grant_consent(m, "aggregates")
    _vaga(gid, a)
    _sql("insert into family_budgets (group_id, created_by_user_id, categoria, limite_cents) values (%s,%s,'x',1)", (gid, a))
    _sql("insert into family_goals (group_id, created_by_user_id, nome, alvo_cents) values (%s,%s,'x',1)", (gid, a))
    fam.log_read(gid, a, m, "aggregates", "resumo_mes")
    assert fam.dissolve_group(a) is True
    for t in ("family_groups", "family_members", "family_invites", "family_consents",
              "family_seat_purchases", "family_budgets", "family_goals"):
        col = "id" if t == "family_groups" else "group_id"
        assert _sql(f"select count(*) n from {t} where {col}=%s", (gid,), fetch=True)[0]["n"] == 0, t
    log = _sql("select group_id, viewer_user_id, recurso from family_access_log where member_user_id=%s", (m,), fetch=True)
    assert log == [{"group_id": None, "viewer_user_id": a, "recurso": "resumo_mes"}]
    assert fam.dissolve_group(a) is False
    fam.create_group(a)  # titular e membro podem formar nova família
    fam.create_group(m)


def test_isolamento_funcoes_do_titular_a_nao_veem_grupo_b():
    a, ga, (ma,) = _casa(1)
    b, gb, (mb,) = _casa(1)
    for m in (ma, mb):
        fam.grant_consent(m, "aggregates")
    ta = fam.create_invite(a)
    assert {x["user_id"] for x in fam.list_members(a)} == {a, ma}
    assert {c["member_user_id"] for c in fam.list_consents(a)} == {ma}
    assert fam.get_group(a)["id"] == ga
    with pytest.raises(fam.FamilyError, match="not_member"):
        fam.remove_member(a, mb)  # membro de B
    assert {x["user_id"] for x in fam.list_members(b)} == {b, mb}
    assert fam.revoke_invite(b, ta) is False  # convite de A
    assert fam.revoke_invite(a, ta) is True
    assert fam.seat_capacity(b) == 3
    with pytest.raises(fam.FamilyError, match="no_group"):
        _entra(ma)  # membro não é titular


def test_so_vagas_invalidas_nao_ampliam_a_capacidade():
    a, gid, _ = _casa(2)
    _vaga(gid, a, status="pending")
    _vaga(gid, a, status="refunded")
    _vaga(gid, a, status="canceled")
    _vaga(gid, a, valid_until="2000-01-01")  # vencida
    assert fam.seat_capacity(a) == 3
    with pytest.raises(fam.FamilyError, match="full"):
        _entra(a)


def test_erros_traduzidos_e_convite_dissolvido():
    a, gid, _ = _casa(0)
    tok = fam.create_invite(a)
    with pytest.raises(fam.FamilyError, match="user_not_found"):
        fam.accept_invite(tok, 999_999_999_999)
    assert _sql("select used_at from family_invites where token_hash=%s", (fam.hash_token(tok),), fetch=True)[0]["used_at"] is None
    fam.dissolve_group(a)
    with pytest.raises(fam.FamilyError, match="invite_invalid"):
        fam.accept_invite(tok, _u())
    # mesmo usuário aceitando em dois grupos ao mesmo tempo: um entra, o outro already_in_family
    b, _, _ = _casa(0)
    c, _, _ = _casa(0)
    u = _u()
    tb, tc = fam.create_invite(b), fam.create_invite(c)
    barreira, res = threading.Barrier(2), []

    def go(t):
        barreira.wait()
        try:
            fam.accept_invite(t, u)
            res.append("ok")
        except fam.FamilyError as e:
            res.append(e.args[0])

    ths = [threading.Thread(target=go, args=(t,)) for t in (tb, tc)]
    [t.start() for t in ths]
    [t.join(20) for t in ths]
    assert sorted(res) == ["already_in_family", "ok"]


def test_log_read_so_com_titular_membro_e_consentimento_ativos():
    a, gid, (m,) = _casa(1)
    outro, gid2, (m2,) = _casa(1)
    n = lambda: _sql("select count(*) n from family_access_log where group_id in (%s,%s)", (gid, gid2), fetch=True)[0]["n"]
    with pytest.raises(fam.FamilyError, match="no_consent"):
        fam.log_read(gid, a, m, "aggregates", "r")  # sem consentimento
    fam.grant_consent(m, "aggregates")
    with pytest.raises(fam.FamilyError, match="no_consent"):
        fam.log_read(gid, a, m, "transactions", "r")  # escopo não concedido
    with pytest.raises(fam.FamilyError, match="not_owner"):
        fam.log_read(gid, outro, m, "aggregates", "r")  # viewer de outro grupo
    with pytest.raises(fam.FamilyError, match="not_owner"):
        fam.log_read(gid2, a, m, "aggregates", "r")  # grupo errado
    with pytest.raises(fam.FamilyError, match="no_consent"):
        fam.log_read(gid, a, m2, "aggregates", "r")  # membro de outro grupo
    assert n() == 0
    fam.log_read(gid, a, m, "aggregates", "r")  # positivo
    assert n() == 1
    fam.remove_member(a, m)
    with pytest.raises(fam.FamilyError, match="no_consent"):
        fam.log_read(gid, a, m, "aggregates", "r")  # saiu
    assert n() == 1


def _corrida_com_grupo_travado(owner, fn):
    """Segura o lock do grupo numa conexão, dispara `fn` em outra thread e solta: prova que `fn` espera o lock."""
    res = []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set local lock_timeout = '5s'")
        cur.execute("select id from family_groups where owner_user_id=%s for update", (owner,))

        def go():
            try:
                fn()
                res.append("ok")
            except fam.FamilyError as e:
                res.append(e.args[0])

        t = threading.Thread(target=go)
        t.start()
        t.join(1.0)
        assert t.is_alive(), "a função não esperou o lock do grupo"
        conn.rollback()  # solta o lock
    t.join(10)
    assert not t.is_alive()
    return res[0]


def test_consentimento_serializa_no_lock_do_grupo_sem_deadlock():
    a, gid, (m,) = _casa(1)
    assert _corrida_com_grupo_travado(a, lambda: fam.grant_consent(m, "aggregates")) == "ok"
    assert _corrida_com_grupo_travado(a, lambda: fam.revoke_consent(m, "aggregates")) == "ok"
    assert fam.list_consents(a) == []


def test_corrida_saida_x_consentimento_nao_deixa_consentimento_de_quem_saiu():
    for _ in range(10):
        a, gid, (m,) = _casa(1)
        fam.grant_consent(m, "aggregates")
        barreira, res = threading.Barrier(2), []

        def sair():
            barreira.wait()
            fam.remove_member(a, m)

        def conceder():
            barreira.wait()
            try:
                fam.grant_consent(m, "transactions")
            except fam.FamilyError as e:
                res.append(e.args[0])

        ths = [threading.Thread(target=f) for f in (sair, conceder)]
        [t.start() for t in ths]
        [t.join(20) for t in ths]
        assert not any(t.is_alive() for t in ths)
        assert fam.list_consents(a) == []


def test_corrida_revoga_aggregates_x_concede_transactions_mantem_invariante():
    for _ in range(10):
        a, gid, (m,) = _casa(1)
        fam.grant_consent(m, "aggregates")
        barreira = threading.Barrier(2)

        def rev():
            barreira.wait()
            fam.revoke_consent(m, "aggregates")

        def conc():
            barreira.wait()
            try:
                fam.grant_consent(m, "transactions")
            except fam.FamilyError:
                pass

        ths = [threading.Thread(target=f) for f in (rev, conc)]
        [t.start() for t in ths]
        [t.join(20) for t in ths]
        assert not any(t.is_alive() for t in ths)
        escopos = {c["scope"] for c in fam.list_consents(a)}
        assert "transactions" not in escopos or "aggregates" in escopos


def test_unique_violation_da_corrida_vira_already_in_family():
    b, gb, _ = _casa(0)
    c, _, _ = _casa(0)
    u = _u()
    tc, res = fam.create_invite(c), []
    with get_conn() as conn, conn.cursor() as cur:  # a "outra" transação: inseriu e ainda não commitou
        cur.execute("insert into family_members (group_id, user_id, role) values (%s,%s,'member')", (gb, u))

        def go():
            try:
                fam.accept_invite(tc, u)
            except fam.FamilyError as e:
                res.append(e.args[0])

        t = threading.Thread(target=go)
        t.start()
        t.join(1.0)
        assert t.is_alive()  # bloqueado no índice único
        conn.commit()
    t.join(10)
    assert res == ["already_in_family"]


def _segura_lock_e_mexe(owner, fn, mexe):
    """A segura o lock do grupo; `fn` roda em thread e bloqueia; A executa `mexe(cur)`, commita e libera."""
    res = []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from family_groups where owner_user_id=%s for update", (owner,))

        def go():
            try:
                fn()
                res.append("ok")
            except fam.FamilyError as e:
                res.append(e.args[0])

        t = threading.Thread(target=go)
        t.start()
        t.join(1.0)
        assert t.is_alive(), "a função não esperou o lock do grupo"
        mexe(cur)
        conn.commit()
    t.join(10)
    assert not t.is_alive()
    return res[0]


def test_create_group_owner_inexistente_vira_user_not_found():
    with pytest.raises(fam.FamilyError, match="user_not_found"):
        fam.create_group(999_999_999_998)


def test_vinculo_encerrado_durante_a_espera_do_lock_vira_not_member():
    a, gid, (m,) = _casa(1)
    r = _segura_lock_e_mexe(a, lambda: fam.grant_consent(m, "aggregates"), lambda c: c.execute(
        "update family_members set left_at = now() where user_id=%s", (m,)))
    assert r == "not_member"
    assert _sql("select 1 from family_consents where member_user_id=%s", (m,), fetch=True) == []


def test_grupo_dissolvido_durante_a_espera_do_lock():
    a, gid, _ = _casa(0)
    tok = fam.create_invite(a)
    u = _u()
    assert _segura_lock_e_mexe(a, lambda: fam.accept_invite(tok, u), lambda c: c.execute(
        "delete from family_groups where owner_user_id=%s", (a,))) == "invite_invalid"
    b, _, (m,) = _casa(1)
    assert _segura_lock_e_mexe(b, lambda: fam.grant_consent(m, "aggregates"), lambda c: c.execute(
        "delete from family_groups where owner_user_id=%s", (b,))) == "not_member"


def test_validade_conferida_no_relogio_depois_do_lock():
    """`now()` congela no início da transação: convite e vaga que vencem durante a espera do lock
    têm de valer como vencidos (clock_timestamp)."""
    a, gid, _ = _casa(0)
    tok = fam.create_invite(a)
    _sql("update family_invites set expires_at = clock_timestamp() + interval '0.5 s' where group_id=%s", (gid,))
    assert _segura_lock_e_mexe(a, lambda: fam.accept_invite(tok, _u()), lambda c: None) == "invite_invalid"

    b, gid_b, _ = _casa(2)  # 3 pessoas: base cheia
    _sql("insert into family_seat_purchases (group_id, source, external_ref, amount_cents, status, valid_until)"
         " values (%s,'pix',%s,1000,'paid',clock_timestamp() + interval '0.5 s')", (gid_b, uuid.uuid4().hex))
    tok_b = fam.create_invite(b)
    assert _segura_lock_e_mexe(b, lambda: fam.accept_invite(tok_b, _u()), lambda c: None) == "full"
