"""#607 — `merge_users` quando as DUAS contas têm dados financeiros.

Antes: UniqueViolation (`uq_launches_user_seq`, `pockets_user_id_name_key`...)
subia do auto-link do WhatsApp, que roda em TODA mensagem, e a pessoa recebia
"Não consegui processar sua mensagem agora" para sempre. Decisão do dono
(2026-09-26): recusar a junção, avisar uma vez na saudação e seguir como a conta
do WhatsApp. Quando só um lado tem dados, a junção passa e move tudo — inclusive
os lotes de caixinha/investimento, que antes ficavam com o user_id da origem.
"""
import uuid

import pytest

import db
from adapters.whatsapp import wa_runtime as wr
from adapters.whatsapp.wa_parse import InboundMessage
from db import attempt_whatsapp_phone_link, ensure_user, get_conn
from tests._helpers_pii import bind_identity_pii, insert_auth_account_pii
from tests.conftest import promote_to_pro

_TABELAS = ("launches", "pockets", "pocket_lots", "investments", "investment_lots",
            "user_category_rules", "pending_actions", "recurring_expenses", "recurring_incomes",
            "bill_instances", "open_finance_connections")


def _sql(q, args=()):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(q, args)
            r = cur.fetchall() if cur.description else None
        conn.commit()
    return r


def _foto(uid):
    """Contagens por tabela + saldo da conta + saldo de cada caixinha."""
    f = {t: _sql(f"select count(*) as n from {t} where user_id=%s", (uid,))[0]["n"] for t in _TABELAS}
    f["saldo"] = db.get_balance(uid)
    f["caixinhas"] = _sql("select name, balance from pockets where user_id=%s order by name", (uid,))
    return f


def _wa_dono_do_numero():
    """(wa_phone, wa_uid): conta só do WhatsApp, dona da identidade do número."""
    wa_phone = f"55119{uuid.uuid4().int % 100_000_000:08d}"
    wa_uid = int(uuid.uuid4().int % 2_000_000_000) + 1  # faixa do canônico: o núcleo comprime acima dela
    ensure_user(wa_uid)
    with get_conn() as conn:
        with conn.cursor() as cur:
            bind_identity_pii(cur, "whatsapp", wa_phone, wa_uid)
        conn.commit()
    return wa_phone, wa_uid


def _conta_site(uid, wa_phone):
    with get_conn() as conn:
        with conn.cursor() as cur:
            insert_auth_account_pii(cur, uid, f"s-{uuid.uuid4().hex[:8]}@e.com", phone=wa_phone)
        conn.commit()


def _com_dados(uid, nome_caixinha="Viagem", valor=1000):
    db.add_launch_and_update_balance(uid, "receita", valor, None, "salario")
    db.create_pocket(uid, nome_caixinha)


def _dono_do_numero(wa_phone):
    return _sql("select user_id from user_identities where provider='whatsapp' and external_id=%s",
                (wa_phone,))[0]["user_id"]


@pytest.mark.parametrize("nome_web", ["Viagem", "viagem"])
def test_whatsapp_com_dados_nos_dois_lados_recusa_sem_mexer_em_nada(user_id, nome_web):
    wa_phone, wa_uid = _wa_dono_do_numero()
    _conta_site(user_id, wa_phone)
    _com_dados(user_id, nome_web)
    _com_dados(wa_uid)
    antes = (_foto(user_id), _foto(wa_uid))

    r = attempt_whatsapp_phone_link(wa_phone, current_user_id=wa_uid)

    assert r == {"status": "merge_conflict", "wa_phone": wa_phone}
    assert (_foto(user_id), _foto(wa_uid)) == antes
    assert _dono_do_numero(wa_phone) == wa_uid, "o número não pode ser religado na recusa"
    st = _sql("select phone_status from auth_accounts where user_id=%s", (user_id,))[0]
    assert st["phone_status"] == "pending", "recusa não confirma o telefone"


def test_conversa_oi_e_saldo_nao_quebram_e_o_aviso_sai_uma_vez(user_id, monkeypatch):
    """O auto-link REAL, pelo `process_message`, com estado real no banco."""
    from tests._billing_grants_helpers import garantir_system_event_logs
    garantir_system_event_logs()  # o "uma vez" do aviso mora nela
    wa_phone, wa_uid = _wa_dono_do_numero()
    _conta_site(user_id, wa_phone)
    _com_dados(user_id)
    _com_dados(wa_uid, valor=250)
    promote_to_pro(wa_uid)  # sem plano o "saldo" para no paywall e não chega ao núcleo
    enviadas = []
    for nome in ("send_text", "send_interactive_buttons", "send_interactive_list"):
        monkeypatch.setattr(wr, nome, lambda *a, **k: enviadas.append(k.get("body") or str(a)))
    monkeypatch.setattr(wr, "send_typing_indicator", lambda *a, **k: None)
    monkeypatch.setattr(wr, "send_welcome", lambda *a, **k: enviadas.append("WELCOME"))

    for texto in ("oi", "saldo", "oi"):
        n = len(enviadas)
        wr.process_message(InboundMessage(wa_id=wa_phone, text=texto, timestamp="1", attachments=[],
                                          raw={"id": f"wamid.{uuid.uuid4().hex}", "type": "text"}))
        assert len(enviadas) > n, f"{texto!r} ficou sem resposta"

    assert not any(wr._PROCESSING_FAILURE_MESSAGE in m for m in enviadas), enviadas
    avisos = [m for m in enviadas if "juntar" in m]
    assert len(avisos) == 1, enviadas
    assert any("R$ 250,00" in m for m in enviadas), "o saldo tem de ser o da conta do WhatsApp"
    assert _dono_do_numero(wa_phone) == wa_uid


def test_positivo_whatsapp_vazio_liga_na_conta_do_site(user_id):
    wa_phone, wa_uid = _wa_dono_do_numero()
    _conta_site(user_id, wa_phone)
    _com_dados(user_id)
    antes = _foto(user_id)

    r = attempt_whatsapp_phone_link(wa_phone, current_user_id=wa_uid)

    assert r["status"] == "linked" and r["user_id"] == user_id
    assert _foto(user_id) == antes
    assert _dono_do_numero(wa_phone) == user_id


def test_positivo_lotes_vao_junto_e_continuam_operaveis_no_destino(user_id):
    wa_phone, wa_uid = _wa_dono_do_numero()
    _conta_site(user_id, wa_phone)
    _com_dados(wa_uid)
    db.pocket_deposit_from_account(wa_uid, "Viagem", 300, "guardar")
    db.create_investment(wa_uid, "cdb", 0.01, "monthly")
    aporte, *_ = db.investment_deposit_from_account(wa_uid, "cdb", 200, "aporte")

    r = attempt_whatsapp_phone_link(wa_phone, current_user_id=wa_uid)

    assert r["status"] == "linked"
    orfaos = _sql("select (select count(*) from pocket_lots where user_id=%s)"
                  " + (select count(*) from investment_lots where user_id=%s) as n", (wa_uid, wa_uid))
    assert orfaos[0]["n"] == 0, "lote ficou com o user_id da origem"
    assert db.get_balance(user_id) == 500
    db.pocket_withdraw_to_account(user_id, "Viagem", 100, "sacar")
    assert db.get_balance(user_id) == 600
    db.delete_launch_and_rollback(user_id, aporte)
    assert db.get_balance(user_id) == 800
    assert _foto(user_id)["investment_lots"] == 0


def test_vincular_com_dados_nos_dois_lados_responde_sem_excecao(user_id):
    from core.handlers.account import vincular

    wa_phone, wa_uid = _wa_dono_do_numero()
    _com_dados(user_id)
    _com_dados(wa_uid)
    antes = (_foto(user_id), _foto(wa_uid))
    code = db.create_link_code(user_id)

    resposta = vincular("whatsapp", wa_phone, code)

    assert "juntar" in resposta and "código" not in resposta.lower(), resposta
    assert (_foto(user_id), _foto(wa_uid)) == antes
    assert _dono_do_numero(wa_phone) == wa_uid


def test_regra_e_pendencia_colidem_e_o_destino_vence(user_id):
    """Só a origem tem regra/pendência — nenhum dado financeiro nela — então passa."""
    origem = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(origem)
    _com_dados(user_id)
    _sql("insert into user_category_rules(user_id, keyword, category) values"
         " (%s,'uber','origem'), (%s,'ifood','alimentacao'), (%s,'uber','destino')",
         (origem, origem, user_id))
    db.set_pending_action(origem, "da_origem", {})
    db.set_pending_action(user_id, "do_destino", {})

    db.merge_users(origem, user_id)

    regras = _sql("select keyword, category from user_category_rules where user_id=%s order by keyword",
                  (user_id,))
    assert [(r["keyword"], r["category"]) for r in regras] == [("ifood", "alimentacao"), ("uber", "destino")]
    assert db.get_pending_action(user_id)["action_type"] == "do_destino"
    assert _foto(origem)["user_category_rules"] == 0


# ── Rodada 2 (#607): o que ficava preso, destino vence, corrida e origem presa ──

_VENCE = "2026-03-10"


def _com_itens_sem_lancamento(uid):
    """Recorrente, receita recorrente, boleto, orçamento, categoria, 50/30/20,
    renda do mês, preferência de relatório e sugestão dispensada."""
    from datetime import date

    from db.bills import create_boleto
    from db.budgets import upsert_budget
    from db.categories import create_user_category
    from db.household_budget import save_config, set_income_override
    from db.recurring import create_recurring_expense, dismiss_recurring_suggestion
    from db.recurring_income import create_recurring_income

    create_recurring_expense(uid, "Aluguel", 900, "moradia", 10, "account", start_date=date(2026, 1, 1))
    create_recurring_income(uid, "Salário", 3000, "salário", 5)
    create_boleto(uid, "Faculdade", 450, _VENCE)
    upsert_budget(uid, "mercado", 600)
    create_user_category(uid, "Aquário")
    save_config(uid, {"custos_fixos": 50, "conforto": 10, "metas": 10, "prazeres": 10,
                      "liberdade_financeira": 10, "conhecimento": 10})
    set_income_override(uid, "2026-09", 3100)
    db.set_daily_report_hour(uid, 7, 30)
    dismiss_recurring_suggestion(uid, "netflix", 39.9)


_MOVIDAS = ("recurring_expenses", "recurring_charges", "recurring_incomes", "recurring_income_credits",
            "bill_instances", "bank_movement_declarations", "category_budgets", "user_categories",
            "household_budget_config", "household_budget_income", "daily_report_prefs",
            "recurring_suggestion_dismissed", "user_category_rules", "pending_actions")


def test_positivo_itens_sem_lancamento_vao_para_a_conta_do_site(user_id):
    from datetime import date

    from db.bills import list_bills
    from db.budgets import list_budgets
    from db.categories import list_custom_category_names
    from db.household_budget import get_config, get_monthly_income
    from db.recurring import find_recurring_candidate, list_due_recurring_expenses, list_recurring_expenses
    from db.recurring_income import list_recurring_incomes

    wa_phone, wa_uid = _wa_dono_do_numero()
    _conta_site(user_id, wa_phone)
    _com_itens_sem_lancamento(wa_uid)

    r = attempt_whatsapp_phone_link(wa_phone, current_user_id=wa_uid)

    assert r["status"] == "linked" and r["user_id"] == user_id
    assert [x["name"] for x in list_recurring_expenses(user_id)] == ["Aluguel"]
    assert [x["name"] for x in list_recurring_incomes(user_id)] == ["Salário"]
    assert [x["name"] for x in list_bills(user_id)] == ["Faculdade"]
    assert [(x["categoria"], float(x["budget"])) for x in list_budgets(user_id)] == [("mercado", 600.0)]
    assert "aquário" in list_custom_category_names(user_id)
    assert get_config(user_id)["custos_fixos"] == 50
    assert get_monthly_income(user_id, "2026-09") == (3100.0, "override")
    prefs = db.get_daily_report_prefs(user_id)
    assert (prefs["hour"], prefs["minute"]) == (7, 30)
    assert find_recurring_candidate(user_id, "netflix", 39.9, current_year=2026, current_month=9) == 0
    devidos = [x for x in list_due_recurring_expenses(today=date(2026, 3, 10)) if x["name"] == "Aluguel"
               and x["user_id"] in (user_id, wa_uid)]
    assert [x["user_id"] for x in devidos] == [user_id], "o cron cobraria na conta que sumiu"
    for t in _MOVIDAS:
        n = _sql(f"select count(*) as n from {t} where user_id=%s", (wa_uid,))[0]["n"]
        assert n == 0, f"{t}: {n} linha(s) presa(s) na origem"


def _ins(table, uid, **cols):
    cols = {"user_id": uid, **cols}
    _sql(f"insert into {table} ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))})",
         tuple(cols.values()))


# (tabela, chave, coluna de valor, valor da origem, valor do destino, linha extra da origem)
_COLISOES = [
    ("user_category_rules", {"keyword": "uber"}, "category", "origem", "destino", {"keyword": "ifood"}),
    ("pending_actions", {}, "action_type", "da_origem", "do_destino", None),
    ("user_categories", {"name": "Pets"}, "emoji", "O", "D", {"name": "Viagem"}),
    ("category_budgets", {"categoria": "mercado"}, "budget", 100, 200, {"categoria": "lazer"}),
    ("household_budget_config", {"bucket": "metas"}, "pct", 10, 20, {"bucket": "conforto"}),
    ("household_budget_income", {"month": "2026-09"}, "amount", 1000, 2000, {"month": "2026-08"}),
    ("daily_report_prefs", {}, "hour", 7, 21, None),
    ("recurring_suggestion_dismissed", {"merchant_key": "netflix", "amount": 39.9}, None, None, None,
     {"merchant_key": "spotify", "amount": 21.9}),
]


@pytest.mark.parametrize("tabela,chave,col,v_origem,v_destino,extra", _COLISOES, ids=[c[0] for c in _COLISOES])
def test_colisao_o_destino_vence_e_o_resto_da_origem_migra(user_id, tabela, chave, col, v_origem,
                                                          v_destino, extra):
    origem = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(origem)
    _com_dados(user_id)
    valor = (lambda v: {col: v}) if col else (lambda v: {})
    if tabela == "pending_actions":
        valor = (lambda v: {"action_type": v, "payload": "{}", "expires_at": "2999-01-01"})
    _ins(tabela, origem, **chave, **valor(v_origem))
    _ins(tabela, user_id, **chave, **valor(v_destino))
    if extra:
        _ins(tabela, origem, **extra, **valor(v_origem))

    db.merge_users(origem, user_id)

    onde = " and ".join(f"{c}=%s" for c in chave) or "true"
    linhas = _sql(f"select * from {tabela} where user_id=%s and {onde}", (user_id, *chave.values()))
    assert len(linhas) == 1
    if col:
        assert str(linhas[0][col]) == str(v_destino) or float(linhas[0][col]) == float(v_destino)
    if extra:
        onde_x = " and ".join(f"{c}=%s" for c in extra)
        assert _sql(f"select count(*) as n from {tabela} where user_id=%s and {onde_x}",
                    (user_id, *extra.values()))[0]["n"] == 1, "a linha não colidente da origem sumiu"
    assert _sql(f"select count(*) as n from {tabela} where user_id=%s", (origem,))[0]["n"] == 0


def _so_recorrente(uid):
    from datetime import date

    from db.recurring import create_recurring_expense
    create_recurring_expense(uid, "Aluguel", 900, "moradia", 10, "account", start_date=date(2026, 1, 1))


def _so_receita(uid):
    from db.recurring_income import create_recurring_income
    create_recurring_income(uid, "Salário", 3000, "salário", 5)


def _so_boleto(uid):
    from db.bills import create_boleto
    create_boleto(uid, "Faculdade", 450, _VENCE)


@pytest.mark.parametrize("semear", [_so_recorrente, _so_receita, _so_boleto],
                         ids=["recorrente", "receita", "boleto"])
def test_so_recorrencia_nos_dois_lados_tambem_recusa(user_id, semear):
    wa_phone, wa_uid = _wa_dono_do_numero()
    _conta_site(user_id, wa_phone)
    semear(user_id)
    semear(wa_uid)
    antes = (_foto(user_id), _foto(wa_uid))

    r = attempt_whatsapp_phone_link(wa_phone, current_user_id=wa_uid)

    assert r == {"status": "merge_conflict", "wa_phone": wa_phone}
    assert (_foto(user_id), _foto(wa_uid)) == antes
    assert _dono_do_numero(wa_phone) == wa_uid


@pytest.mark.parametrize("rota", ["auto_link", "vincular"])
def test_corrida_lancamento_no_destino_depois_da_checagem_vira_recusa(user_id, monkeypatch, rota):
    """Lançamento gravado no destino, por OUTRA conexão, entre a checagem e os
    updates: sem o except, `uq_launches_user_seq` subia como UniqueViolation."""
    import db.users as users

    wa_phone, wa_uid = _wa_dono_do_numero()
    if rota == "auto_link":
        _conta_site(user_id, wa_phone)
    db.add_launch_and_update_balance(wa_uid, "receita", 250, None, "salario")
    antes = _foto(wa_uid)
    real = users._tem_dados_financeiros

    def checa_e_grava_no_destino(cur, uid):
        tem = real(cur, uid)
        if uid == user_id:
            db.add_launch_and_update_balance(user_id, "receita", 80, None, "pix")
        return tem

    monkeypatch.setattr(users, "_tem_dados_financeiros", checa_e_grava_no_destino)

    if rota == "auto_link":
        r = attempt_whatsapp_phone_link(wa_phone, current_user_id=wa_uid)
        assert r == {"status": "merge_conflict", "wa_phone": wa_phone}
    else:
        from core.handlers.account import vincular
        resposta = vincular("whatsapp", wa_phone, db.create_link_code(user_id))
        assert "juntar" in resposta, resposta

    assert _foto(wa_uid) == antes, "nada da origem pode ter se movido"
    assert _foto(user_id)["launches"] == 1, "só o lançamento da corrida no destino"
    assert _dono_do_numero(wa_phone) == wa_uid


def _com_conexao_of(uid, status):
    _ins("open_finance_connections", uid, provider="pluggy",
         provider_item_id=f"item-{uuid.uuid4().hex}", institution_id="201", institution_name="Banco", status=status)


def _com_plano_pago(uid):
    promote_to_pro(uid)


@pytest.mark.parametrize("prender,esperado", [
    (lambda uid: _com_conexao_of(uid, "UPDATED"), "merge_conflict"),
    (_com_plano_pago, "merge_conflict"),
    (lambda uid: _com_conexao_of(uid, "PAUSED"), "linked"),  # trial vencido: o item nem existe mais
], ids=["open_finance_vivo", "plano_pago", "open_finance_pausado"])
def test_origem_com_open_finance_ou_plano_pago_nao_some(user_id, prender, esperado):
    wa_phone, wa_uid = _wa_dono_do_numero()
    _conta_site(user_id, wa_phone)
    with get_conn() as conn:
        with conn.cursor() as cur:
            # como o uid 832398038: a origem tem cadastro web, com OUTRO telefone
            insert_auth_account_pii(cur, wa_uid, f"o-{uuid.uuid4().hex[:8]}@e.com", phone="5511000000000")
        conn.commit()
    prender(wa_uid)
    antes = (_foto(user_id), _foto(wa_uid))

    r = attempt_whatsapp_phone_link(wa_phone, current_user_id=wa_uid)

    assert r["status"] == esperado, r
    if esperado == "merge_conflict":
        assert (_foto(user_id), _foto(wa_uid)) == antes
        assert _dono_do_numero(wa_phone) == wa_uid
    else:
        assert _dono_do_numero(wa_phone) == user_id
