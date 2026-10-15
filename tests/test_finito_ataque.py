"""ATAQUE do Tester ao CHECK `finito_*` (db/finito_checks.py), mantido como regressão.
Guarda da porta ligada (valor <= VALOR_MAX passa): ataca a SOMA que cruza 1e13 e o que os handlers fazem."""
import logging
from decimal import Decimal

import pytest

import db
from conftest import usuario_pagante
from db import get_conn
from db.finito_checks import MSG_VALOR_WHATSAPP
from tests._patrimonio_helpers import q

NOVE = "9000000000000"  # 9e12: passa em valor_valido (<= 1e13), a soma de dois não


def _sem_linha(texto):
    """Sem o DETAIL com dado. O marcador que o filtro de log deixa no lugar ('omitido') é o esperado."""
    return "Failing row" not in texto.replace("Failing row contains (omitido)", "")


def _linhas(sql, uid):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (uid,))
        return cur.fetchall()


def _diga(uid, texto):
    from core.types import IncomingMessage
    import core.handle_incoming as HI
    msg = IncomingMessage(platform="whatsapp", user_id=uid, text=texto,
                          message_id="x", attachments=[], external_id="e", raw={})
    saida = HI.handle_incoming(msg)
    return saida[0].text if saida else ""


def _investimento_perto_do_teto(uid):
    _l, inv_id, _n = db.create_investment_db(uid, "cdbx", 0.001, "daily")
    q("update investments set balance=9000000000000, last_date='2020-01-02', interest_frozen_at=null where id=%s", (inv_id,))
    q("delete from investment_lots where investment_id=%s", (inv_id,))
    return inv_id


def test_job_de_rendimento_levanta_check_e_vaza_failing_row(caplog, monkeypatch):
    """Saldo legítimo (9e12) que rende e cruza 1e13: o CHECK barra o job; o log não pode vazar a linha."""
    from core.services import investment_scheduler as S
    uid = usuario_pagante()
    _investimento_perto_do_teto(uid)
    import asyncio
    from core.admin_dashboard import ensure_admin_tables
    asyncio.run(ensure_admin_tables())
    monkeypatch.setattr(db, "list_users_with_unfrozen_interest", lambda: [uid])
    with caplog.at_level(logging.DEBUG):
        r = S.accrue_all_users_investments()
    assert r["failed"] == 1, r
    # o evento REAL em system_event_logs (gravador central), não um mock do gravador
    eventos = [f'{l["message"]} {l["details"]}' for l in _linhas(
        "select message, details from system_event_logs where user_id=%s", uid)]
    assert any("investment_accrual" in e or "Falha ao atualizar" in e for e in eventos), eventos
    assert _sem_linha(caplog.text) and all(_sem_linha(e) for e in eventos), (
        "vazou: " + " | ".join(e[:250] for e in eventos if "Failing row" in e))


def test_wa_retirar_da_caixinha_devolve_failing_row_ao_usuario(caplog):
    """`return f"Erro ao retirar: {e}"` (core/handlers/pockets.py) levava o texto do psycopg ao WhatsApp."""
    uid = usuario_pagante()
    _l, pid, _n = db.create_pocket(uid, "viagem")
    q("update pockets set balance=9000000000000 where id=%s", (pid,))
    db.set_balance(uid, Decimal("9000000000000"))
    resp = _diga(uid, f"retirei {NOVE} da caixinha viagem")
    assert "Failing row" not in resp and "violates check" not in resp, resp[:300]


def _lancs(uid):
    return len(_linhas("select 1 from launches where user_id=%s", uid))


_CAB = ("OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nSECURITY:NONE\nENCODING:USASCII\nCHARSET:1252\n"
        "COMPRESSION:NONE\nOLDFILEUID:NONE\nNEWFILEUID:NONE\n\n")


def _ofx_cartao(acct, trnamt):
    return (_CAB + "<OFX><CREDITCARDMSGSRSV1><CCSTMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS>"
            f"<CCSTMTRS><CURDEF>BRL<CCACCTFROM><ACCTID>{acct}</CCACCTFROM><BANKTRANLIST><DTSTART>20260801<DTEND>20260831"
            f"<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260803<TRNAMT>{trnamt}<FITID>{acct}CC1<MEMO>MCDONALDS 1234</STMTTRN>"
            "</BANKTRANLIST><LEDGERBAL><BALAMT>-39.90<DTASOF>20260831</LEDGERBAL></CCSTMTRS></CCSTMTTRNRS>"
            "</CREDITCARDMSGSRSV1></OFX>").encode()


def _ofx_banco(acct, trnamt, ledger="100.00"):
    return (_CAB + "<OFX><BANKMSGSRSV1><STMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS>"
            f"<STMTRS><CURDEF>BRL<BANKACCTFROM><BANKID>0001<ACCTID>{acct}<ACCTTYPE>CHECKING</BANKACCTFROM>"
            f"<BANKTRANLIST><DTSTART>20260801<DTEND>20260831<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260803"
            f"<TRNAMT>{trnamt}<FITID>{acct}TX1<MEMO>MCDONALDS 1234</STMTTRN></BANKTRANLIST>"
            f"<LEDGERBAL><BALAMT>{ledger}<DTASOF>20260831</LEDGERBAL></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>").encode()


def test_ofx_cartao_com_compra_absurda_vira_400_sem_failing_row():
    """A rota devolvia 200 com `Erro ao importar fatura...: {e}` (Failing row dentro)."""
    from test_valor_nao_finito_painel import _cliente
    uid = usuario_pagante()
    db.create_card(uid, "Nubank", closing_day=10, due_day=17)
    c, h = _cliente(uid)
    h.pop("Content-Type")
    r = c.post(f"/ofx/import/{uid}", headers=h, files={
        "file": ("f.ofx", _ofx_cartao(uid, "-100000000000000000000.00"), "application/x-ofx")})
    assert r.status_code == 400 and "Failing row" not in r.text, (r.status_code, r.text[:300])


def test_ofx_banco_com_saldo_final_absurdo_importa_e_depois_falha():
    from core.services.ofx_service import handle_ofx_import
    uid = usuario_pagante()
    try:
        resp = handle_ofx_import(str(uid), _ofx_banco(uid, "-39.90", ledger="99999999999999.00"), "f.ofx")
    except Exception as exc:
        resp = f"EXC {type(exc).__name__}"
    assert not (resp.startswith("EXC") and _lancs(uid) == 1), "erro ao usuario, mas o lancamento FOI gravado"


def test_extrato_pdf_com_saldo_final_absurdo_importa_sem_reconciliar(monkeypatch):
    """Irmão do OFX (`statement_import.py`): o bulk já commitou; `set_balance` do saldo > teto cairia no CHECK."""
    from datetime import date as _date
    import statement_import as si
    uid = usuario_pagante()
    monkeypatch.setattr(si, "_extract_pdf_text", lambda _d: "extrato falso")
    monkeypatch.setattr(si, "parse_pdf_statement_text", lambda _t: [
        {"posted_at": _date.today(), "amount": Decimal("-20"), "memo": "SAQUE"}])
    monkeypatch.setattr(si, "extract_pdf_final_balance", lambda _t: Decimal("99999999999999.00"))
    rep = si.import_statement_bytes(uid, b"%PDF-falso", "extrato.pdf", "pdf")
    assert rep["reconciled"] is False and _lancs(uid) == 1, rep


def test_auto_write_da_ia_nao_loga_failing_row(caplog, monkeypatch):
    """O `logger.exception` do auto-write levava o traceback (com o DETAIL) para o log e o system_event_logs."""
    from types import SimpleNamespace
    from core.services.ai_chat import runner
    uid = usuario_pagante()
    tool = SimpleNamespace(is_write=True, requires_confirmation=False, confirmar_se=None,
                           execute=lambda u, a: q("update accounts set balance=1e20 where user_id=%s", (u,)))
    monkeypatch.setattr(runner, "get_tool", lambda n: tool)
    with caplog.at_level(logging.DEBUG):
        _hist, resposta = runner._dispatch_tool(uid, "qualquer", {})
    assert "erro em auto-write" in caplog.text and "finito_balance" in caplog.text
    assert _sem_linha(caplog.text)
    assert resposta == MSG_VALOR_WHATSAPP, resposta


GRANDE_DIGITOS = "1" + "0" * 20  # 1e20, casa com a gramática do _SQL_PROFIT


def test_sync_de_posicao_com_amountprofit_absurdo_envenena_o_item():
    """`_SQL_PROFIT` copia `raw->>'amountProfit'` para `pockets.of_last_seen_profit` por CAST no SQL:
    a caixinha com lápide é religada a cada sync e o save INTEIRO caía no CHECK, sempre."""
    from db.open_finance import save_open_finance_investments
    from tests._patrimonio_helpers import conexao
    uid = usuario_pagante()
    cid = conexao(uid, f"item-prof-{uid}")
    inv = {"provider_investment_id": "p1", "name": "CDB", "type": "FIXED_INCOME", "currency": "BRL",
           "balance": Decimal("100"), "raw": {"amountProfit": GRANDE_DIGITOS, "balance": 100}}
    save_open_finance_investments(cid, [{**inv, "raw": {"balance": 100}}])  # 1º sync, saudável
    q("insert into pockets (user_id, name, balance, of_tombstone_connection_id, of_tombstone_provider_id) "
      "values (%s, 'cx', 100, %s, 'p1')", (uid, cid))
    try:
        r = save_open_finance_investments(cid, [inv])
        res = f"OK {r}"
    except Exception as exc:
        res = f"EXC {type(exc).__name__}"
    assert not res.startswith("EXC"), res


def _contas_hoje(uid):
    from db.contas_hoje import listar
    with get_conn() as conn, conn.cursor() as cur:
        r = listar(cur, uid)
        conn.rollback()
    return r


def test_saldo_absurdo_da_pluggy_vira_zero_sem_saldo_ausente():
    """`_to_decimal` zera 1e20, mas o `raw` continua finito: a conta aparecia com 0 SEM `saldo_ausente`."""
    from core.services.pluggy_sync import normalize_pluggy_account
    from tests._patrimonio_helpers import conexao
    uid = usuario_pagante()
    cid = conexao(uid, f"item-aus-{uid}")
    db.save_open_finance_sync(cid, [normalize_pluggy_account({"id": i, "type": "BANK", "balance": b,
                                                              "currencyCode": "BRL"})
                                    for i, b in (("nan", "NaN"), ("grande", 1e20))])
    contas = {c["nome"]: c for c in _contas_hoje(uid)["contas"]}
    por_id = {r["provider_account_id"]: r["id"] for r in q(
        "select id, provider_account_id from open_finance_accounts where connection_id=%s", (cid,), fetch=True)}
    cs = {c["id"]: c for c in _contas_hoje(uid)["contas"]}
    nan, grande = cs[por_id["nan"]], cs[por_id["grande"]]
    assert "saldo_ausente" in nan["motivos"]
    assert "saldo_ausente" in grande["motivos"], "saldo 1e20 virou 0 e entrou no total como se fosse leitura valida"


def test_rota_credito_soma_cruza_limite_vira_400_sem_failing_row():
    """Guarda da porta LIGADA; a 2a compra de 9e12 estoura `credit_bills.total` (1.8e13)."""
    from test_valor_nao_finito_painel import _cliente
    uid = usuario_pagante()
    cid = db.create_card(uid, "Nubank", closing_day=10, due_day=17)
    c, h = _cliente(uid)
    corpo = f'{{"tipo":"credito","valor":{NOVE},"alvo":"x","nota":"x","card_id":{cid}}}'
    r1 = c.post(f"/launches/{uid}", headers=h, content=corpo)
    r2 = c.post(f"/launches/{uid}", headers=h, content=corpo)
    assert r1.status_code == 200
    assert r2.status_code == 400 and "Failing row" not in r2.text, r2.text[:200]


def test_rota_credito_parcelado_estoura_check_e_nao_loga_failing_row(caplog):
    """Terceiro parcelado de 9e12 (4,5e12 por fatura) passa de 1e13 em `credit_bills.total`."""
    from test_valor_nao_finito_painel import _cliente
    uid = usuario_pagante()
    cid = db.create_card(uid, "Nubank", closing_day=10, due_day=17)
    c, h = _cliente(uid)
    c.raise_server_exceptions = False
    corpo = f'{{"tipo":"credito","valor":{NOVE},"alvo":"x","nota":"x","card_id":{cid},"parcelas":2}}'
    with caplog.at_level(logging.DEBUG):
        resp = [c.post(f"/launches/{uid}", headers=h, content=corpo) for _ in range(3)]
    assert [r.status_code for r in resp] == [200, 200, 400], [r.status_code for r in resp]
    assert resp[2].json()["detail"] == "Valor inválido: confira o número."
    assert _sem_linha(caplog.text)


def test_rota_investimento_com_check_vira_400_sem_failing_row(monkeypatch):
    """`except Exception as exc: ... detail=str(exc)` de `create_investment_route` devolvia o texto do psycopg."""
    import frontend.finance_bot_websocket_custom as m
    from test_valor_nao_finito_painel import _cliente
    uid = usuario_pagante()
    c, h = _cliente(uid)
    monkeypatch.setattr(m, "create_investment_db",
                        lambda *a, **k: q("update accounts set balance=1e20 where user_id=%s", (uid,)))
    r = c.post(f"/investments/{uid}", headers=h, content='{"name":"cdbx","rate":0.1,"period":"daily"}')
    assert r.status_code == 400 and "Failing row" not in r.text, (r.status_code, r.text[:200])


def test_ia_pendencia_confirmada_estoura_check_e_loga_failing_row(caplog):
    from core.services.ai_chat import runner
    uid = usuario_pagante()
    db.set_balance(uid, Decimal("-9000000000000"))
    pend = {"tool_name": "add_launch", "tool_args": {
        "tipo": "despesa", "valor": 9e12, "alvo": "x", "forma_pagamento": "dinheiro"}}
    with caplog.at_level(logging.DEBUG):
        msg = runner._execute_pending(uid, pend)
    assert _lancs(uid) == 0
    assert _sem_linha(caplog.text), "str(e) do psycopg foi para o log (e para o system_event_logs)"
    assert msg == MSG_VALOR_WHATSAPP, msg  # a mesma frase do WhatsApp, não o "falha do meu lado"


def test_duas_instancias_simultaneas_uma_roda_a_outra_sai_sem_erro():
    import threading
    from db import finito_checks as F
    q("alter table launches drop constraint if exists finito_valor")
    q("alter table accounts drop constraint if exists finito_balance")
    saidas, erros = [], []
    barra = threading.Barrier(4)

    def roda():
        barra.wait()
        try:
            saidas.append(F.ensure_finito_checks_once())
        except Exception as exc:
            erros.append(repr(exc)[:200])

    ts = [threading.Thread(target=roda) for _ in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not erros
    r = F.ensure_finito_checks_once()  # lock solto no fim
    assert r is not None and r["faltando"] == [] and r["falhas"] == [], r
    assert [s for s in saidas if s and s["falhas"]] == []


@pytest.mark.xfail(strict=True, reason="TEÓRICA: o ensure só roda em autocommit (a tarefa de fundo); fora do escopo")
def test_ensure_em_conexao_transacional_com_tabela_travada():
    """`ensure_finito_checks` documenta 'exige AUTOCOMMIT'; sem ele, o 1o lock_timeout aborta a
    transação e o `finally` (set_config) levanta OUTRA exceção por cima da original."""
    import threading
    from db import finito_checks as F
    q("alter table launches drop constraint if exists finito_valor")
    solta, travou = threading.Event(), threading.Event()

    def segura():
        with get_conn() as conn:
            conn.execute("lock table launches in access share mode")
            travou.set(); solta.wait(10)
    t = threading.Thread(target=segura); t.start(); assert travou.wait(5)
    try:
        with get_conn() as conn, conn.cursor() as cur:
            try:
                r = F.ensure_finito_checks(cur)
                res = f"OK {r['falhas']}"
            except Exception as exc:
                res = f"EXC {type(exc).__name__}"
    finally:
        solta.set(); t.join()
    F.ensure_finito_checks_once()
    assert res.startswith("OK")


@pytest.fixture
def check_alheio():
    q("alter table accounts add constraint zz_alheio check (balance <> -50)")
    yield
    q("alter table accounts drop constraint if exists zz_alheio")


def test_monolito_check_alheio_continua_500_sem_failing_row(check_alheio):
    from test_valor_nao_finito_painel import _cliente
    uid = usuario_pagante()
    c, h = _cliente(uid)
    c.raise_server_exceptions = False
    r = c.post(f"/launches/{uid}", headers=h, content='{"tipo":"despesa","valor":50,"alvo":"x","nota":"x"}')
    assert r.status_code == 500 and "Failing row" not in r.text and "Valor inválido" not in r.text


def test_wa_check_alheio_nao_vira_mensagem_de_valor_invalido(check_alheio):
    uid = usuario_pagante()
    resp = _diga(uid, "gastei 50 no mercado")
    assert "Confere o número" not in resp and "Failing row" not in resp


def test_erro_no_meio_do_ensure_solta_o_lock_e_o_lock_timeout(monkeypatch):
    from db import finito_checks as F
    q("alter table launches drop constraint if exists finito_valor")
    def boom(cur, cols):
        raise RuntimeError("pre-scan explodiu")
    monkeypatch.setattr(F, "colunas_fora_do_limite", boom)
    with pytest.raises(RuntimeError):
        F.ensure_finito_checks_once()
    monkeypatch.undo()
    r = F.ensure_finito_checks_once()  # lock de sessão foi solto, nada pela metade
    assert r is not None and r["criadas"] == ["launches.valor"], r
    with get_conn() as conn, conn.cursor() as cur:  # a conexão volta ao pool limpa
        cur.execute("select current_setting('lock_timeout') as v, "
                    "(select count(*) from pg_locks where locktype='advisory' and objid=728531006) as presos")
        row = cur.fetchone()
        assert row["v"] == "0" and row["presos"] == 0, row


@pytest.mark.xfail(strict=True, reason="BAIXA: soma de partes > teto em patrimonio_fotos.total; decisão do dono: fora do escopo")
def test_foto_do_patrimonio_cuja_soma_passa_do_teto_falha_toda_hora():
    """`patrimonio_fotos.total` é a SOMA de partes que cabem cada uma em 1e13: o CHECK barra a
    linha de quem tem 9e12 na carteira + 9e12 em investimento manual (job tenta de novo a cada hora)."""
    from db import patrimonio
    from tests._patrimonio_helpers import investimento_manual
    from datetime import date
    uid = usuario_pagante()
    db.set_balance(uid, Decimal("9000000000000"))
    investimento_manual(uid, "cdb", Decimal("9000000000000"))
    try:
        r = patrimonio.gravar_foto(uid, date.today())
        res = f"OK {r}"
    except Exception as exc:
        res = f"EXC {type(exc).__name__}"
    assert res == "OK True", res


def test_startup_do_app_agenda_o_ensure_do_finito():
    """O processo web de verdade chama a função. Molde de `test_fk_indexes.py`."""
    from _lifespan_probe import sondar
    resultado, diagnostico = sondar({"finito": "db.finito_checks:ensure_finito_checks_once"})
    assert resultado == {"finito": True, "do_disco": []}, diagnostico
