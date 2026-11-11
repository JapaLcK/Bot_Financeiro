"""Exclusões simultâneas de contas ligadas por indicação (#1414): nunca `DeadlockDetected`.

Postgres real, `delete_user_data` real. Sem sorte e sem sleep de sincronização: duas contas usam
`PausaSql`; três usam um ponto de encontro com o mesmo hook (`_encontro`).

Controles negativos (rodados à mão, §3 do `CLAUDE.md`):
  - tire de `desvincular_afiliado` o bloco que trava os afiliados ligados → deadlock em `test_mutua`,
    `test_so_b_indica_a_com_comissao`, `test_cadeia_3[ciclo-*]` e `test_mutua_mantem_registros_do_1508`
    (a mesma corrida); `test_exclusao_do_indicado_*` e `test_ordem_de_id_deterministica` ficam vermelhos
    porque a pausa não acha a trava.
  - troque `for key share` por `for update` → vermelho em `test_exclusao_do_indicado_nao_segura_o_afiliado`.
  - ordene "o próprio primeiro" (`order by (a.user_id = %(u)s) desc, a.id`) → deadlock em
    `test_ordem_de_id_deterministica`.
Controles positivos: `test_cadeia_3[linha-*]`, a força da trava e os registros do #1508 na corrida.
"""
import threading
import time

import psycopg
import pytest

import db
from db.affiliates import mark_payout_paid, record_commission_for_invoice, record_referral, request_payout, \
    set_affiliate_commission
from db.connection import get_conn
from test_afiliado_area import _afiliado, _comissao, _conferida, _conta
from test_afiliado_pendencia import _acoes, _rev
from tests._pausa_sql import PausaSql, sem_deadlock

APAGA_AFILIADO = lambda q: q.startswith("delete from affiliates where user_id")  # noqa: E731
PRIMEIRA_TRAVA = lambda q: q.startswith("select id from affiliates where id = ")  # noqa: E731


def _indica(quem, indicado, comissao):
    assert record_referral(quem.aff["code"], indicado.uid)
    if comissao:
        linha = record_commission_for_invoice(indicado.uid, f"in_{indicado.uid}_1414", 10_000)
        _conferida(linha["id"])


def _concluidas(*resultados, contas):
    sem_deadlock(*resultados)
    assert all(isinstance(r, dict) and r["deleted"] for r in resultados), resultados
    with get_conn() as conn:
        assert conn.execute("select count(*) as n from users where id = any(%s)",
                            ([c.uid for c in contas],)).fetchone()["n"] == 0


def test_ordem_de_id_deterministica(monkeypatch):
    """A ordem é a do id, não "o próprio primeiro". B criado antes (id menor); A para logo depois da 1ª
    trava de afiliado. Por id: A pega B em KEY SHARE e para, B espera. Próprio primeiro: A pega A FOR
    UPDATE e para, B pega B e pede A, A pede B → deadlock."""
    b = _afiliado()
    a = _afiliado()
    assert b.aff["id"] < a.aff["id"]
    _indica(a, b, False)
    _indica(b, a, False)
    pausa = PausaSql(monkeypatch, "a", PRIMEIRA_TRAVA)
    ra, rb = pausa.roda(lambda: db.delete_user_data(a.uid), lambda: db.delete_user_data(b.uid))
    assert pausa.casou
    _concluidas(ra, rb, contas=(a, b))


@pytest.mark.parametrize("comissao", [False, True], ids=["sem_comissao", "com_comissao"])
def test_mutua(monkeypatch, comissao):
    a, b = _afiliado(), _afiliado()
    _indica(a, b, comissao)
    _indica(b, a, comissao)
    pausa = PausaSql(monkeypatch, "a", APAGA_AFILIADO)
    ra, rb = pausa.roda(lambda: db.delete_user_data(a.uid), lambda: db.delete_user_data(b.uid))
    assert pausa.casou
    _concluidas(ra, rb, contas=(a, b))


def _ordem_dos_triggers():
    with get_conn() as conn:
        return [r["tab"] for r in conn.execute(
            """
            select c.conrelid::regclass::text as tab from pg_trigger t join pg_constraint c on c.oid = t.tgconstraint
             where t.tgrelid = 'users'::regclass and t.tgtype & 8 = 8
               and c.conrelid::regclass::text in ('affiliate_referrals', 'affiliate_commissions')
             order by t.tgname
            """).fetchall()]


def _recria_fk(conn, tabela):
    """Drop + add da FK de `tabela` para `users` com a definição que ela já tem (o padrão de
    `test_init_db_migra_banco_antigo_com_cascade_e_not_null`): o trigger RI ganha oid novo."""
    fk = conn.execute("select conname, pg_get_constraintdef(oid) as d from pg_constraint where contype = 'f'"
                      " and conrelid = %s::regclass and confrelid = 'users'::regclass", (tabela,)).fetchone()
    conn.execute(f'alter table {tabela} drop constraint "{fk["conname"]}", add constraint "{fk["conname"]}" {fk["d"]}')


def _impoe_ordem_dos_triggers():
    """Referral antes da comissão, venha de onde vier o banco (outro teste recria a FK da referral).
    Recria as duas nessa ordem: a comissão fica com oid maior. O nome compara como texto, então se o
    oid ganhou um dígito entre as duas, a 2ª volta recria as duas já com o mesmo número de dígitos."""
    for _ in range(2):
        with get_conn() as conn:
            _recria_fk(conn, "affiliate_referrals")
            _recria_fk(conn, "affiliate_commissions")
            conn.commit()
        if _ordem_dos_triggers() == ["affiliate_referrals", "affiliate_commissions"]:
            return


@pytest.mark.parametrize("a_afiliado", [True, False], ids=["a_afiliado", "a_comum"])
def test_so_b_indica_a_com_comissao(monkeypatch, a_afiliado):
    """Ciclo sem indicação mútua: B trava a comissão dele sobre A e depois apaga `affiliates`(B), com
    cascata na referral; A, no `delete from users`, anula a referral e espera a comissão."""
    _impoe_ordem_dos_triggers()
    assert _ordem_dos_triggers() == ["affiliate_referrals", "affiliate_commissions"], (
        "o set null de users dispara os triggers RI em ordem de nome; com a comissão antes da referral "
        "este ciclo não fecha nem sem o conserto, e o caso não mede nada")
    a = _afiliado() if a_afiliado else _conta()
    b = _afiliado()
    _indica(b, a, True)
    pausa = PausaSql(monkeypatch, "b", lambda q: "from affiliate_commissions where affiliate_id" in q
                     and "for update" in q)
    rb, ra = pausa.roda(lambda: db.delete_user_data(b.uid), lambda: db.delete_user_data(a.uid))
    assert pausa.casou
    _concluidas(ra, rb, contas=(a, b))


def _encontro(monkeypatch, contas, quando):
    """Exclui as contas em paralelo; cada uma para depois do statement `quando` até todas chegarem (ou
    acabarem) ou até as que faltam estarem esperando lock. Com o conserto as outras param em
    `affiliates` e a que chegou segue; sem ele todas chegam e o ciclo fecha. Devolve as saídas e quantas
    exclusões casaram `quando`."""
    n, tl, mx, chegaram, saidas, casaram = len(contas), threading.local(), threading.Lock(), [0], {}, []

    def travadas():
        with get_conn() as conn:
            return conn.execute("select count(*) as n from pg_stat_activity where datname = current_database()"
                                " and wait_event_type = 'Lock'").fetchone()["n"]

    def chegou():
        with mx:
            chegaram[0] += 1
        fim = time.monotonic() + 15
        while time.monotonic() < fim:
            with mx:
                falta = n - chegaram[0]
            if falta == 0 or travadas() >= falta:
                return
            time.sleep(0.05)

    original = psycopg.Cursor.execute

    def execute(cur, query, *a, **k):
        r = original(cur, query, *a, **k)
        if getattr(tl, "ativo", False) and not tl.passou and quando(str(query).lower()):
            tl.passou = True
            casaram.append(True)
            chegou()
        return r
    monkeypatch.setattr(psycopg.Cursor, "execute", execute)

    def exclui(c):
        tl.ativo, tl.passou = True, False
        try:
            saidas[c.uid] = db.delete_user_data(c.uid)
        except Exception as exc:  # noqa: BLE001 — quem julga é o teste
            saidas[c.uid] = exc
        finally:
            if not tl.passou:
                with mx:
                    chegaram[0] += 1
    ts = [threading.Thread(target=exclui, args=(c,)) for c in contas]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    assert not any(t.is_alive() for t in ts), "hang: uma das exclusões não terminou"
    return [saidas[c.uid] for c in contas], len(casaram)


@pytest.mark.parametrize("comissao", [False, True], ids=["sem_comissao", "com_comissao"])
@pytest.mark.parametrize("forma", ["ciclo", "linha"])
def test_cadeia_3(monkeypatch, forma, comissao):
    """A→B→C(→A). No ciclo cada exclusão segura, por cascata, a referral que fez e espera, por set null,
    a que a indicou: deadlock até sem comissão. Na linha não há ciclo: é o controle do caminho comum."""
    a, b, c = _afiliado(), _afiliado(), _afiliado()
    _indica(a, b, comissao)
    _indica(b, c, comissao)
    if forma == "ciclo":
        _indica(c, a, comissao)
    saidas, casaram = _encontro(monkeypatch, [a, b, c], APAGA_AFILIADO)
    assert casaram == 3, "o encontro não casou: o teste não mediu a corrida"
    _concluidas(*saidas, contas=(a, b, c))


def test_exclusao_do_indicado_nao_segura_o_afiliado(monkeypatch):
    """Positivo da força: o indicado U trava o afiliado X só em KEY SHARE. Saque, percentual e comissão
    nova de X seguem com a exclusão de U parada logo depois dessa trava. FOR UPDATE seguraria os três."""
    x = _afiliado()
    u = _comissao(x, 100).indicado
    # Casa a trava de linha seja qual for a força: com `for update` a pausa tem de ver o afiliado travado.
    pausa = PausaSql(monkeypatch, "u", PRIMEIRA_TRAVA)

    def afiliado_vivo():
        return request_payout(x.aff["id"]), set_affiliate_commission(x.aff["id"], 1500), _comissao(x, 50)
    ru, rx = pausa.roda(lambda: db.delete_user_data(u.uid), afiliado_vivo)
    assert pausa.casou
    assert pausa.outro_travou is False, "a exclusão do indicado segurou uma operação do afiliado vivo"
    assert not isinstance(rx, Exception), rx
    assert (rx[0]["amount_cents"], rx[1]) == (10_000, 1000)
    _concluidas(ru, contas=(u,))


def _saques_de(c):
    """Um saque pago e um aberto, cada um com a sua comissão, chave Pix e nota, para os registros do
    #1508 (a exclusão tem de zerar as duas: `test_exclusao_zera_pii_do_saque`)."""
    pago = _comissao(c, 100)
    saque_pago = request_payout(c.aff["id"], "pix-enc-1414")["id"]
    assert mark_payout_paid(saque_pago, f"pago a {c.email}")
    aberto = _comissao(c, 60)
    saque_aberto = request_payout(c.aff["id"], "pix-enc-1414")["id"]
    with get_conn() as conn:
        conn.execute("update affiliate_payouts set note = %s where id = %s", (f"pedido de {c.email}", saque_aberto))
        conn.commit()
    return pago, saque_pago, aberto, saque_aberto


def _saque_db(saque):
    with get_conn() as conn:
        return conn.execute("select * from affiliate_payouts where id = %s", (saque,)).fetchone()


def test_mutua_mantem_registros_do_1508(monkeypatch):
    """Positivo: na corrida de `test_mutua`, cada lado guarda saques e pendências como na exclusão
    isolada (`test_exclusao_mantem_saques`, `test_exclusao_do_afiliado_mantem_recuperacao`)."""
    a, b = _afiliado(), _afiliado()
    registros = {c.aff["code"]: _saques_de(c) for c in (a, b)}
    _indica(a, b, True)
    _indica(b, a, True)
    campos = ("amount_cents", "status", "requested_at", "paid_at")
    antes = {s: _saque_db(s) for _, pago, _, aberto in registros.values() for s in (pago, aberto)}
    assert all(r["pix_key_enc"] and r["note"] for r in antes.values()), "sem chave e nota o zerar não é medido"
    pausa = PausaSql(monkeypatch, "a", APAGA_AFILIADO)
    ra, rb = pausa.roda(lambda: db.delete_user_data(a.uid), lambda: db.delete_user_data(b.uid))
    assert pausa.casou
    _concluidas(ra, rb, contas=(a, b))
    for code, (com_paga, saque_pago, com_aberta, saque_aberto) in registros.items():
        for s in (saque_pago, saque_aberto):
            depois = _saque_db(s)
            assert {k: depois[k] for k in campos} == {k: antes[s][k] for k in campos}
            assert (depois["affiliate_id"], depois["affiliate_code"], depois["pix_key_enc"], depois["note"]) == (
                None, code, None, None)
        paga, aberta = _rev(com_paga.fatura), _rev(com_aberta.fatura)
        assert (paga["status"], paga["paid_cents"], paga["affiliate_code"]) == ("resolved", 10_000, code)
        assert (aberta["commission_cents"], aberta["orphan_payout_id"], aberta["affiliate_code"]) == (
            6_000, saque_aberto, code)
        assert "affiliate_deleted" in _acoes(com_paga.fatura) and "affiliate_deleted" in _acoes(com_aberta.fatura)
