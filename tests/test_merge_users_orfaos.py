"""#635 — depois da junção a conta de origem não pode sobrar.

Antes: `merge_users` movia só o financeiro e deixava a linha `users` da origem
com a conversa da IA, logs, push, agentes... `delete_user_data(destino)` não os
alcançava, e o id canônico do WhatsApp é determinístico (sha256 do número): o
mesmo número, reciclado, voltava ao id da origem e via as `ai_messages` da
pessoa anterior. Decisão do dono (2026-09-26): mover conversa, logs, dinheiro e
OF terminal; o resto some pelas FKs quando a linha `users` da origem é apagada.
"""
import uuid

import pytest

import db
from adapters.whatsapp import wa_runtime as wr
from adapters.whatsapp.wa_parse import InboundMessage
from db import attempt_whatsapp_phone_link, get_conn
from db.ai_chat import append_message, set_pending_action
from db.privacy import delete_user_data
from db.users import _DESTINO_VENCE, _MOVIDAS, _OUTRAS_COLUNAS, get_or_create_canonical_user
from tests._billing_grants_helpers import garantir_system_event_logs
from tests._dreno_pix_helpers import nova_cobranca
from tests._helpers_pii import insert_auth_account_pii
from tests.conftest import promote_to_pro


def _sql(q, a=()):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(q, a)
        r = cur.fetchall() if cur.description else None
        conn.commit()
    return r


def _n(q, a=()):
    return _sql(f"select count(*) as n from {q}", a)[0]["n"]


def _tabelas_user_id():
    """Toda tabela com user_id (lida do banco, não de lista fixa)."""
    return [r["table_name"] for r in _sql(
        "select c.table_name from information_schema.columns c join information_schema.tables t"
        " on t.table_name = c.table_name and t.table_schema = 'public' and t.table_type = 'BASE TABLE'"
        " where c.table_schema = 'public' and c.column_name = 'user_id'")]


def _sobra(uid):
    out = {t: _n(f"{t} where user_id = %s", (uid,)) for t in _tabelas_user_id()}
    out["pii_access_log(subject)"] = _n("pii_access_log where subject_user_id = %s", (uid,))
    out["users"] = _n("users where id = %s", (uid,))
    return {k: v for k, v in out.items() if v}


def _fone():
    return f"55119{uuid.uuid4().int % 100_000_000:08d}"


def _conta_site(uid, fone):
    with get_conn() as conn, conn.cursor() as cur:
        insert_auth_account_pii(cur, uid, f"s-{uuid.uuid4().hex[:8]}@e.com", phone=fone)
        conn.commit()


def _semeia_origem(o):
    db.add_launch_and_update_balance(o, "despesa", 50, None, "mercado")
    append_message(o, "user", "quanto gastei com mercado?")
    append_message(o, "assistant", "R$ 50,00")
    set_pending_action(o, "add_launch", {"v": 1}, "da origem")
    _sql("insert into ai_fallback_log(user_id, question) values (%s, 'x')", (o,))
    _sql("insert into ai_proactive_cache(user_id, kind, payload) values (%s, 'k', '{}')", (o,))
    _sql("insert into system_event_logs(level, event_type, message, user_id) values ('info','t','m',%s)", (o,))
    _sql("insert into audit_events(user_id, event) values (%s, 'da_origem')", (o,))
    _sql("insert into pii_access_log(purpose, actor, subject_user_id, field)"
         " values ('t', 'system', %s, 'phone')", (o,))
    _sql("insert into budget_alert_sent(user_id, categoria, ym, threshold) values"
         " (%s, 'mercado', '2026-09', 80), (%s, 'lazer', '2026-09', 80)", (o, o))
    a = _sql("insert into agents(user_id, kind) values (%s, 'teste') returning id", (o,))[0]["id"]
    _sql("insert into agent_events(agent_id, user_id, kind, dedupe_key) values (%s, %s, 'k', 'd')", (a, o))
    _sql("insert into push_tokens(user_id, token, platform, environment) values (%s, %s, 'ios', 'production')",
         (o, uuid.uuid4().hex))
    _sql("insert into checkout_funnel_events(user_id, session_id, kind) values (%s, 's', 'started')", (o,))
    _sql("insert into financial_spaces(user_id, name) values (%s, 'Espaço da origem')", (o,))


def _origem_do_whatsapp(destino):
    garantir_system_event_logs()
    fone = _fone()
    origem = get_or_create_canonical_user("whatsapp", fone)
    _conta_site(destino, fone)
    return fone, origem


def test_origem_some_e_numero_reciclado_nao_ve_a_conversa_anterior(user_id):
    fone, origem = _origem_do_whatsapp(user_id)
    _semeia_origem(origem)

    r = attempt_whatsapp_phone_link(fone, current_user_id=origem)

    assert r["status"] == "linked" and r["user_id"] == user_id
    assert _sobra(origem) == {}, "a origem ficou com dado"
    assert _sql("select tool_name from ai_pending_actions where user_id = %s",
                (user_id,))[0]["tool_name"] == "add_launch", "sem colisão, a pendência da IA vem junto"
    delete_user_data(user_id)
    volta = get_or_create_canonical_user("whatsapp", fone)
    assert volta == origem, "pré-condição: o id canônico é determinístico"
    assert _n("ai_messages where user_id = %s", (volta,)) == 0


def test_positivo_destino_mantem_o_seu_e_recebe_o_da_origem(user_id):
    fone, origem = _origem_do_whatsapp(user_id)
    _semeia_origem(origem)
    append_message(user_id, "user", "do destino")
    set_pending_action(user_id, "do_destino", {}, "do destino")
    _sql("insert into budget_alert_sent(user_id, categoria, ym, threshold) values (%s, 'mercado', '2026-09', 80)",
         (user_id,))
    _sql("insert into audit_events(user_id, event) values (%s, 'do_destino')", (user_id,))

    assert attempt_whatsapp_phone_link(fone, current_user_id=origem)["status"] == "linked"

    msgs = [r["content"] for r in _sql("select content from ai_messages where user_id = %s", (user_id,))]
    assert sorted(msgs) == sorted(["do destino", "quanto gastei com mercado?", "R$ 50,00"])
    assert _sql("select tool_name from ai_pending_actions where user_id = %s",
                (user_id,))[0]["tool_name"] == "do_destino"
    assert _n("budget_alert_sent where user_id = %s", (user_id,)) == 2
    assert {r["event"] for r in _sql("select event from audit_events where user_id = %s", (user_id,))} \
        == {"da_origem", "do_destino"}
    assert _n("pii_access_log where subject_user_id = %s", (user_id,)) == 1
    assert _n("system_event_logs where user_id = %s and event_type = 't'", (user_id,)) == 1
    assert _n("launches where user_id = %s", (user_id,)) == 1
    assert _n("auth_accounts where user_id = %s", (user_id,)) == 1


# As tuplas do `merge_users`: uma tabela nova nelas entra aqui sozinha e o teste
# abaixo exige que ela seja semeada.
_A_MOVER = (*((t, "user_id") for t in (*_MOVIDAS, *(t for t, _ in _DESTINO_VENCE))), *_OUTRAS_COLUNAS)
# O que a semeadura põe na origem e NÃO chega como a mesma linha: apagado pela
# decisão do dono (#635); `accounts`, somado ao saldo do destino; `user_identities`,
# que chega mas o `bind_identity` recifra (`external_id_enc` com nonce novo).
_NAO_CHEGA = {"ai_proactive_cache", "agents", "agent_events", "push_tokens", "checkout_funnel_events",
              "accounts", "user_identities",
              # apagada de propósito (dono, 2026-09-26, #635); decidir mover quando a
              # Fase 2 expuser espaços. O teste abaixo afirma que ela não chega.
              "financial_spaces"}


def _linhas(table, col, uid):
    """As linhas do uid, sem a coluna do dono: a mesma linha antes e depois do update."""
    return {r["j"] for r in _sql(f"select (to_jsonb(t) - %s)::text as j from {table} t where {col} = %s",
                                 (col, uid))}


def test_positivo_cada_tabela_movida_chega_ao_destino(user_id):
    """Chegou, não só saiu: "a origem ficou vazia" também passa quando o CASCADE
    apaga a linha em vez de movê-la (foi assim que o ai_fallback_log se perdia)."""
    fone, origem = _origem_do_whatsapp(user_id)
    _semeia_origem(origem)
    nova_cobranca(origem)
    _grant_vencido(origem)
    af = _sql("insert into affiliates(user_id, code) values (%s, %s) returning id",
              (origem, uuid.uuid4().hex[:10]))[0]["id"]
    for q in (
        "insert into affiliate_referrals(affiliate_id, referred_user_id) values ({af}, %s)",
        "insert into affiliate_commissions(affiliate_id, referred_user_id, stripe_invoice_id, invoice_amount_cents,"
        " amount_cents, available_at) values ({af}, %s, '" + uuid.uuid4().hex + "', 1000, 100, now())",
        "insert into prospect_referrals(code, referred_user_id) values ('c', %s)",
        "insert into auth_login_events(user_id, success) values (%s, true)",
        "insert into open_finance_connections(user_id, provider, provider_item_id, status, institution_id,"
        " institution_name) values (%s, 'pluggy', '" + uuid.uuid4().hex + "', 'DELETED', 'i', 'Banco')",
        "insert into open_finance_item_registry(user_id, origin) values (%s, 't')",
        "insert into user_category_rules(user_id, keyword, category) values (%s, 'padaria', 'mercado')",
        "insert into pending_actions(user_id, action_type, payload, expires_at) values (%s, 't', '{{}}', now())",
        "insert into user_categories(user_id, name) values (%s, 'da origem')",
        "insert into category_budgets(user_id, categoria, budget) values (%s, 'mercado', 100)",
        "insert into household_budget_config(user_id, bucket, pct) values (%s, 'metas', 10)",
        "insert into household_budget_income(user_id, month, amount) values (%s, '2026-09', 10)",
        "insert into daily_report_prefs(user_id) values (%s) on conflict do nothing",
        "insert into recurring_suggestion_dismissed(user_id, merchant_key, amount) values (%s, 'm', 1)",
        "insert into subscription_marks(user_id, merchant_key, status) values (%s, 'netflix', 'ignorar')",
    ):
        _sql(q.format(af=af), (origem,))
    # Do banco, não só das tuplas, mas só confere tabela que tem linha: tabela nova
    # com FK para users só é vista se for semeada em `_semeia_origem` (ou aqui).
    fks = {(r["t"], r["c"]) for r in _sql(
        "select conrelid::regclass::text as t, a.attname as c from pg_constraint k"
        " join pg_attribute a on a.attrelid = k.conrelid and a.attnum = any(k.conkey)"
        " where k.contype = 'f' and k.confrelid = 'users'::regclass")}
    todas = {*_A_MOVER, *((t, c) for t, c in {*fks, *((t, "user_id") for t in _tabelas_user_id())}
                          if t not in _NAO_CHEGA)}
    antes = {tc: v for tc in todas if (v := _linhas(*tc, origem))}
    assert [tc for tc in _A_MOVER if tc not in antes] == [], "semeie a tabela na origem"
    espaco = "financial_spaces where user_id = %s and name = 'Espaço da origem'"
    assert _n(espaco, (origem,)) == 1

    assert attempt_whatsapp_phone_link(fone, current_user_id=origem)["status"] == "linked"

    faltam = [f"{t}.{c}" for (t, c), v in antes.items() if not v <= _linhas(t, c, user_id)]
    assert faltam == [], "a linha da origem não chegou ao destino"
    assert (_n(espaco, (origem,)), _n(espaco, (user_id,))) == (0, 0), \
        "financial_spaces é apagada por decisão do dono (#635); movê-la pede nova decisão"


def _grant_vencido(uid):
    _sql("insert into plan_grants(user_id, source, external_ref, plan_stored, starts_at, ends_at)"
         " values (%s, 'admin', %s, 'pro', now() - interval '60 days', now() - interval '30 days')",
         (uid, f"admin:{uuid.uuid4().hex}"))


def test_dinheiro_pix_e_grant_vao_para_o_destino(user_id):
    fone, origem = _origem_do_whatsapp(user_id)
    nova_cobranca(origem)
    _grant_vencido(origem)

    assert attempt_whatsapp_phone_link(fone, current_user_id=origem)["status"] == "linked"

    assert _n("pix_charges where user_id = %s and status = 'pending'", (user_id,)) == 1
    assert _n("plan_grants where user_id = %s", (user_id,)) == 1
    assert _n("users where id = %s", (origem,)) == 0


def test_pix_aberto_nos_dois_lados_recusa_e_nada_e_apagado(user_id):
    fone, origem = _origem_do_whatsapp(user_id)
    _semeia_origem(origem)
    nova_cobranca(origem)
    nova_cobranca(user_id)
    sem_log = lambda uid: {k: v for k, v in _sobra(uid).items() if k != "system_event_logs"}  # noqa: E731
    antes = (_sobra(origem), sem_log(user_id))

    r = attempt_whatsapp_phone_link(fone, current_user_id=origem)

    assert r == {"status": "merge_conflict", "wa_phone": fone}
    assert (_sobra(origem), sem_log(user_id)) == antes, "a recusa grava só o log dela no destino"
    assert _sobra(origem)["users"] == 1


def test_comissao_de_afiliado_da_origem_passa_ao_destino(user_id):
    fone, origem = _origem_do_whatsapp(user_id)
    dono = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(dono)
    try:
        af = _sql("insert into affiliates(user_id, code) values (%s, %s) returning id",
                  (dono, uuid.uuid4().hex[:10]))[0]["id"]
        _sql("insert into affiliate_referrals(affiliate_id, referred_user_id) values (%s, %s)", (af, origem))
        _sql("insert into affiliate_commissions(affiliate_id, referred_user_id, stripe_invoice_id,"
             " invoice_amount_cents, amount_cents, available_at) values (%s, %s, %s, 1000, 100, now())", (af, origem, uuid.uuid4().hex))

        assert attempt_whatsapp_phone_link(fone, current_user_id=origem)["status"] == "linked"

        assert _n("affiliate_commissions where affiliate_id = %s and referred_user_id = %s", (af, user_id)) == 1
        assert _n("affiliate_referrals where affiliate_id = %s and referred_user_id = %s", (af, user_id)) == 1
    finally:
        _sql("delete from users where id = %s", (dono,))


@pytest.mark.parametrize("dono_e_origem", [True, False], ids=["origem_dona", "destino_dono"])
def test_afiliado_de_um_lado_indicou_o_outro_recusa_e_nada_muda(user_id, dono_e_origem):
    """Juntos, dono e indicado viram o mesmo usuário: a 1ª fatura paga seria
    comissão para si mesmo (`record_commission_for_invoice` não rechecaria)."""
    fone, origem = _origem_do_whatsapp(user_id)
    dono, indicado = (origem, user_id) if dono_e_origem else (user_id, origem)
    af = _sql("insert into affiliates(user_id, code) values (%s, %s) returning id",
              (dono, uuid.uuid4().hex[:10]))[0]["id"]
    _sql("insert into affiliate_referrals(affiliate_id, referred_user_id) values (%s, %s)", (af, indicado))

    r = attempt_whatsapp_phone_link(fone, current_user_id=origem)

    assert r == {"status": "merge_conflict", "wa_phone": fone}
    assert _n("users where id = %s", (origem,)) == 1
    assert _n("affiliates where id = %s and user_id = %s", (af, dono)) == 1
    assert _n("affiliate_referrals where affiliate_id = %s and referred_user_id = %s", (af, indicado)) == 1


def test_login_da_origem_vai_inteiro_quando_o_destino_nao_tem(user_id):
    """Conta, Google e MFA juntos: antes o login migrava e o MFA ficava para trás."""
    origem = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(origem)
    with get_conn() as conn, conn.cursor() as cur:
        insert_auth_account_pii(cur, origem, f"o-{uuid.uuid4().hex[:8]}@e.com")
        conn.commit()
    _sql("insert into user_mfa(user_id, secret_encrypted, enabled) values (%s, 'x', true)", (origem,))
    _sql("insert into user_mfa_backup_codes(user_id, code_hash) values (%s, 'h')", (origem,))
    _sql("insert into auth_identities(user_id, provider, provider_sub) values (%s, 'google', %s)",
         (origem, uuid.uuid4().hex))

    db.merge_users(origem, user_id)

    for t in ("auth_accounts", "user_mfa", "user_mfa_backup_codes", "auth_identities"):
        assert _n(f"{t} where user_id = %s", (user_id,)) == 1, t
    assert _sql("select enabled from user_mfa where user_id = %s", (user_id,))[0]["enabled"] is True


def test_conversa_gastei_e_saldo_com_juncao_no_turno(monkeypatch):
    """O auto-link REAL, pelo `process_message`: a junção acontece no 1º turno.

    O plano pago é do DESTINO: na origem, `_origem_presa` recusaria a junção.
    O destino pago passa o paywall porque o turno já roda como ele. O destino
    fica na faixa do canônico: o núcleo comprime id acima dela (e perderia o plano)."""
    user_id = int(uuid.uuid4().int % 2_000_000_000) + 1
    db.ensure_user(user_id)  # órfão limpo pelo `_auto_cleanup_orphan_users`
    fone, origem = _origem_do_whatsapp(user_id)
    append_message(origem, "user", "conversa antiga")
    promote_to_pro(user_id)
    enviadas = []
    for nome in ("send_text", "send_interactive_buttons", "send_interactive_list"):
        monkeypatch.setattr(wr, nome, lambda *a, **k: enviadas.append(k.get("body") or str(a)))
    monkeypatch.setattr(wr, "send_typing_indicator", lambda *a, **k: None)
    monkeypatch.setattr(wr, "send_welcome", lambda *a, **k: enviadas.append("WELCOME"))

    for texto in ("gastei 50 no mercado", "saldo"):
        n = len(enviadas)
        wr.process_message(InboundMessage(wa_id=fone, text=texto, timestamp="1", attachments=[],
                                          raw={"id": f"wamid.{uuid.uuid4().hex}", "type": "text"}))
        assert len(enviadas) > n, f"{texto!r} ficou sem resposta"

    assert not any(wr._PROCESSING_FAILURE_MESSAGE in m for m in enviadas), enviadas
    assert _n("launches where user_id = %s", (user_id,)) == 1
    assert any("-50,00" in m or "- R$ 50,00" in m or "-R$ 50,00" in m for m in enviadas[1:]), enviadas
    assert _sobra(origem) == {}, "o turno recriou a origem"
    assert _n("ai_messages where user_id = %s and content = 'conversa antiga'", (user_id,)) == 1


def test_lancamento_num_espaco_da_origem_recusa_e_nada_muda(user_id):
    """`fk_launches_space` é composta (user_id, space_id): o lançamento movido
    apontaria para o espaço da origem. Vira `merge_conflict`, não erro cru."""
    fone, origem = _origem_do_whatsapp(user_id)
    db.add_launch_and_update_balance(origem, "despesa", 50, None, "mercado")
    esp = _sql("insert into financial_spaces(user_id, name) values (%s, 'Casa') returning id", (origem,))[0]["id"]
    _sql("update launches set space_id = %s where user_id = %s", (esp, origem))

    r = attempt_whatsapp_phone_link(fone, current_user_id=origem)

    assert r == {"status": "merge_conflict", "wa_phone": fone}
    assert _n("users where id = %s", (origem,)) == 1
    assert _n("launches where user_id = %s and space_id = %s", (origem, esp)) == 1
    assert _n("launches where user_id = %s", (user_id,)) == 0
