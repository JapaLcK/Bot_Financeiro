"""PR-11 A (#637): a exportação LGPD classifica toda tabela da pessoa e não leva credencial.

T1 completude: toda tabela do fecho de FK até `users` é tabela principal de `EXPORTADAS` ou chave de
   `NAO_EXPORTADAS`; nome sem tabela (órfão) e tabela nas duas listas reprovam.
T2 estrutura: toda SQL filtra por `%(uid)s` e nenhuma usa `%s`; tabela exportada com `select *`/`alias.*`
   não tem coluna com cara de segredo, salvo `COLUNAS_SEM_SEGREDO`.
T3 isolamento: o ZIP de A não tem nada semeado em B.
T4 segredo semeado: o `code` do link mágico, o hash e o cifrado das identidades, o `webhookUrl` com token
   e os hashes e cifrados da conta não aparecem em nenhum membro do ZIP; a linha legítima continua lá.
T5 formato: os membros do ZIP e as colunas de cada dataset são os da `main` de 2026-11-12, salvo `MUDARAM`.

CONTROLES rodados à mão (CLAUDE.md §3), cada um num caso verde:
- T1: tirar "lancamentos" de `EXPORTADAS` no arquivo de produção deixa `test_t1_registro_real` vermelho.
- T4: voltar `sessoes_dashboard` para `select code, ...` e `identidades` para `select *`, ou tirar o
  `pop("webhookUrl")` de `db/privacy_export.py`, deixa `test_t4_segredo_semeado_nao_sai` vermelho.

CLASSES CEGAS: segredo dentro de coluna JSON de tabela `select *` (`raw`, `payload`, `details`) que não seja
o `webhookUrl`; tabela ligada à pessoa por e-mail ou telefone, fora do fecho de FK (#1557); tabela que só
existe em produção (DP9); coluna nomeada nova com segredo (T2 só olha `select *`; T4 só o que semeia).
"""
from __future__ import annotations

import asyncio
import io
import json
import re
import uuid
import zipfile

import pytest

from core.admin_dashboard import ensure_admin_tables
from db import ensure_user, get_conn
from db.privacy_export import build_user_export_zip
from db.privacy_export_conjuntos import COLUNAS_SEM_SEGREDO, EXPORTADAS, NAO_EXPORTADAS

# Isenção nomeada da guarda de órfãos: os 3 datasets legados apontam para tabelas que não existem no
# schema e saem sempre vazios. Tirar daqui (e de EXPORTADAS) quando o dono rodar a Q-DP9.
ORFAS_PENDENTES_DP9 = dict.fromkeys(
    ("user_category_triggers", "user_trigger_candidates", "user_category_feedback"),
    "pendente DP9: existência em produção não confirmada")

_FECHO = """
    with recursive fecho(oid) as (
        select 'public.users'::regclass::oid
        union
        select con.conrelid from pg_constraint con join fecho f on con.confrelid = f.oid where con.contype = 'f'
    )
    select relname from pg_class where oid in (select oid from fecho)
"""
_SEGREDO = re.compile(r"hash|token|secret|_enc$|password|^code$|jti|challenge|webhook")
_ESTRELA = re.compile(r"select\s+(?:\w+\.)?\*", re.I)


@pytest.fixture(scope="module", autouse=True)
def _tabelas_admin():
    asyncio.run(ensure_admin_tables())  # `system_event_logs` só nasce aqui


def _q(sql, params=None, fetch=False):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall() if fetch else None
        conn.commit()
    return rows


def _fecho() -> set[str]:
    return {r["relname"] for r in _q(_FECHO, fetch=True)}


def _nao_classificadas(exportadas, nao_exportadas, fecho) -> tuple[list, list, list]:
    """(do fecho e sem classificação, nomes do registro fora do fecho, nas duas listas)."""
    principais = {tabela for tabela, _ in exportadas.values()}
    return (sorted(fecho - principais - set(nao_exportadas)),
            sorted((principais | set(nao_exportadas)) - fecho - set(ORFAS_PENDENTES_DP9)),
            sorted(principais & set(nao_exportadas)))


def test_t1_registro_real():
    assert _nao_classificadas(EXPORTADAS, NAO_EXPORTADAS, _fecho()) == ([], [], [])


def test_t1_guarda_acusa_falta_orfa_e_duplicada():
    fecho = _fecho()
    sem_ai = {k: v for k, v in NAO_EXPORTADAS.items() if k != "ai_messages"}
    sem_lanc = {k: v for k, v in EXPORTADAS.items() if k != "lancamentos"}
    assert _nao_classificadas(EXPORTADAS, sem_ai, fecho)[0] == ["ai_messages"]
    assert _nao_classificadas(sem_lanc, NAO_EXPORTADAS, fecho)[0] == ["launches"]
    assert _nao_classificadas(EXPORTADAS, {**NAO_EXPORTADAS, "tabela_fantasma": "x"}, fecho)[1] == ["tabela_fantasma"]
    assert _nao_classificadas(EXPORTADAS, {**NAO_EXPORTADAS, "pockets": "x"}, fecho)[2] == ["pockets"]
    # A isenção DP9 é estreita: são exatamente as três, e elas não existem neste schema.
    assert set(ORFAS_PENDENTES_DP9) == {t for t, _ in EXPORTADAS.values()} - fecho


def _sql_ruim(exportadas) -> list[str]:
    return [nome for nome, (_, sql) in exportadas.items() if "%(uid)s" not in sql or "%s" in sql]


def _colunas_secretas(exportadas) -> list[tuple[str, str]]:
    achadas = []
    for tabela, sql in exportadas.values():
        if not _ESTRELA.search(sql):
            continue
        for r in _q("select attname from pg_attribute where attrelid = to_regclass(%s) and attnum > 0"
                    " and not attisdropped", (tabela,), fetch=True):
            if _SEGREDO.search(r["attname"]) and (tabela, r["attname"]) not in COLUNAS_SEM_SEGREDO:
                achadas.append((tabela, r["attname"]))
    return achadas


def test_t2_estrutura_do_registro_real():
    assert _sql_ruim(EXPORTADAS) == []
    assert _colunas_secretas(EXPORTADAS) == []
    assert _colunas_secretas({"x": ("ofx_imports", "select * from ofx_imports")}) == []  # a isenção não é morta


def test_t2_guarda_acusa_posicional_e_select_estrela_com_segredo():
    assert _sql_ruim({"x": ("users", "select * from users where id = %s")}) == ["x"]
    assert _colunas_secretas({"x": ("user_mfa", "select * from user_mfa where user_id = %(uid)s")}) \
        == [("user_mfa", "secret_encrypted")]
    assert set(_colunas_secretas({"x": ("user_identities", "select * from user_identities")})) \
        == {("user_identities", "external_id_hash"), ("user_identities", "external_id_enc")}


def _pessoa(marca: str) -> int:
    uid = int(uuid.uuid4().int % 900_000_000) + 1
    ensure_user(uid)
    _q("insert into auth_accounts (user_id, email, display_name, password_hash, email_hash, display_name_enc)"
       " values (%s, %s, %s, %s, %s, %s)",
       (uid, f"{marca}_email@x.com", f"{marca}_nome", f"{marca}_password_hash", f"{marca}_email_hash",
        f"{marca}_display_name_enc"))
    _q("insert into user_identities (provider, external_id, user_id, external_id_hash, external_id_enc)"
       " values ('whatsapp', %s, %s, %s, %s)", (f"{marca}_ext", uid, f"{marca}_ext_hash", f"{marca}_ext_enc"))
    _q("insert into dashboard_sessions (code, user_id, expires_at, code_challenge)"
       " values (%s, %s, now() + interval '2 hours', %s)", (f"{marca}_codigo_magico", uid, f"{marca}_challenge"))
    raw = {"webhookUrl": f"https://x/open-finance/pluggy/webhook?token={marca}_SEGREDO_WEBHOOK",
           "clientUserId": f"{marca}_client"}
    con = _q("insert into open_finance_connections (user_id, provider, provider_item_id, status, institution_id,"
             " institution_name, raw) values (%s, 'pluggy', %s, 'UPDATED', '1', 'Banco', %s::jsonb) returning id",
             (uid, f"{marca}_item", json.dumps(raw)), fetch=True)[0]["id"]
    _q("insert into open_finance_accounts (connection_id, provider_account_id, name, type)"
       " values (%s, %s, %s, 'BANK')", (con, f"{marca}_conta_of", f"{marca}_nome_conta_of"))
    _q("insert into launches (user_id, tipo, valor, nota) values (%s, 'despesa', 10, %s)", (uid, f"{marca}_nota"))
    _q("insert into system_event_logs (level, event_type, message, user_id) values ('info', 'x', %s, %s)",
       (f"{marca}_evento", uid))
    return uid


def _zip(uid: int) -> tuple[dict, str, list[str]]:
    z = zipfile.ZipFile(io.BytesIO(build_user_export_zip(uid)))
    texto = "".join(z.read(n).decode("utf-8") for n in z.namelist())
    return json.loads(z.read("dados.json"))["dados"], texto, z.namelist()


def test_t3_zip_de_a_nao_tem_nada_de_b():
    a = _pessoa("MARCA_A")
    _pessoa("MARCA_B")
    dados, texto, _ = _zip(a)
    for semeado in ("email", "nome", "ext", "nome_conta_of", "nota", "evento", "client"):
        assert f"MARCA_A_{semeado}" in texto  # positivo: o de A está lá
    assert "MARCA_B" not in texto
    assert dados["contas_open_finance"][0]["name"] == "MARCA_A_nome_conta_of"  # o join chega ao dono


def test_t4_segredo_semeado_nao_sai():
    uid = _pessoa("SEG")
    dados, texto, _ = _zip(uid)
    for segredo in ("SEG_codigo_magico", "SEG_challenge", "SEG_ext_hash", "SEG_ext_enc", "SEG_SEGREDO_WEBHOOK",
                    "SEG_password_hash", "SEG_email_hash", "SEG_display_name_enc"):
        assert segredo not in texto, segredo
    # positivo: as linhas que guardavam o segredo continuam, com o que é da pessoa
    assert dados["sessoes_dashboard"][0]["expires_at"]
    assert dados["identidades"] == [{"provider": "whatsapp", "external_id": "SEG_ext",
                                     "created_at": dados["identidades"][0]["created_at"]}]
    assert dados["conexoes_open_finance"][0]["raw"] == {"clientUserId": "SEG_client"}
    assert dados["conta_login"][0]["display_name"] == "SEG_nome"


# Datasets da `main` (51e2e861), na ordem. Os três de categoria seguem até a DP9.
DATASETS_DA_MAIN = (
    "usuario conta_login identidades contas lancamentos declaracoes_bancarias saques_depositos_dinheiro"
    " cobertura_open_finance orcamentos regras_categorias gatilhos_categorias candidatos_gatilhos_categorias"
    " feedback_categorias acoes_pendentes caixinhas investimentos historico_patrimonio lotes_investimentos"
    " cartoes faturas_cartao transacoes_cartao preferencias_resumo_diario marcacoes_assinaturas insights_estado"
    " guia_do_painel metas importacoes_ofx sessoes_dashboard conexoes_open_finance contas_open_finance"
    " transacoes_open_finance investimentos_open_finance historico_investimentos_open_finance"
    " recorrencias_open_finance faturas_cartao_open_finance afiliado afiliado_comissoes afiliado_saques"
    " indicacao_recebida eventos_login eventos_sistema cobrancas_pix").split()

# Colunas dos datasets de colunas NOMEADAS na `main` (os de `select *` são as colunas da tabela).
_CONTA_LOGIN_MAIN = (
    "id user_id email phone_e164 phone_status phone_confirmed_at whatsapp_verified_at plan plan_expires_at"
    " created_at stripe_customer_id engagement_opt_out last_activity_at last_tip_sent_at tip_email_opt_out"
    " last_insight_sent_at insight_email_opt_out insight_aviso_aderiu_em insight_aviso_coberto_ate"
    " whatsapp_updates_opt_out last_reengagement_sent_at deletion_requested_at deletion_scheduled_for"
    " deletion_status deletion_processing_started_at dashboard_profile signup_quiz").split()

# O que mudou de propósito (P3 do dono): dataset -> (colunas que entram, colunas que saem).
MUDARAM = {
    "conta_login": ({"display_name"}, set()),
    "identidades": (set(), {"user_id", "external_id_hash", "external_id_enc"}),
    "sessoes_dashboard": (set(), {"code"}),
}


def _colunas(sql: str) -> list[str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, {"uid": -1})
        return [d.name for d in cur.description]


def test_t5_membros_e_colunas_preservados():
    _, _, membros = _zip(_pessoa("FMT"))
    assert membros == ["dados.json", "manifesto.json", *(f"csv/{d}.csv" for d in DATASETS_DA_MAIN)]
    assert list(EXPORTADAS) == DATASETS_DA_MAIN
    main = {"conta_login": _CONTA_LOGIN_MAIN,
            "identidades": _colunas("select * from user_identities"),
            "sessoes_dashboard": ["code", "user_id", "expires_at", "created_at"]}
    for nome, (entram, saem) in MUDARAM.items():
        assert set(_colunas(EXPORTADAS[nome][1])) == (set(main[nome]) | entram) - saem, nome
