"""PR-11 A (#637): a exportação LGPD classifica toda tabela da pessoa e não leva credencial.

T1 completude: toda tabela do fecho de FK até `users` é tabela principal de `EXPORTADAS` ou chave de
   `NAO_EXPORTADAS`; nome sem tabela (órfão) e tabela nas duas listas reprovam.
T2 estrutura: toda SQL filtra por `%(uid)s` e nenhuma usa `%s`; nenhuma coluna que a SQL DEVOLVE
   (`cur.description`, não o texto da SQL) tem cara de segredo, salvo `COLUNAS_SEM_SEGREDO`.
T3 isolamento: A e B com uma linha marcada em cada tabela exportada (`tests/_semeia_exportacao.py`); todo
   dataset de A e de B tem linha, e semear B não muda nada no ZIP de A nem põe a marca de B nele.
T4 segredo semeado: o `code` do link mágico, o hash e o cifrado das identidades, a URL com token do webhook
   (em 6 formas do `raw`), o valor do `PLUGGY_WEBHOOK_SECRET` em qualquer forma e os hashes e cifrados da
   conta não aparecem em nenhum membro do ZIP; a linha legítima continua lá.
T5 formato: os membros do ZIP e as colunas de cada dataset são os da `main` de 2026-11-12, salvo `MUDARAM`.

CONTROLES rodados à mão (CLAUDE.md §3), cada um num caso verde:
- T1: tirar "lancamentos" de `EXPORTADAS` no arquivo de produção deixa `test_t1_registro_real` vermelho.
- T3: `or true` depois do `%(uid)s` de `cartoes`, `transacoes_open_finance` ou `eventos_login` deixa
  `test_t3_zip_de_a_nao_tem_nada_de_b` vermelho.
- T4: voltar `sessoes_dashboard` para `select code, ...` e `identidades` para `select *`, ou trocar o
  `_sem_token` pelo `pop("webhookUrl")` só do topo, deixa `test_t4_segredo_semeado_nao_sai` vermelho.
- T4: tirar de `_sem_token` o teste do valor do segredo, ou a troca na chave do dict, ou o tamanho mínimo,
  deixa `test_t4_valor_do_segredo_do_webhook_nao_sai_em_forma_nenhuma` vermelho.

CLASSES CEGAS: outro segredo, sem `token=`, dentro de coluna JSON (`raw`, `payload`, `details`); o segredo
do webhook com menos de 16 caracteres, fora de `token=`; tabela ligada à
pessoa por e-mail ou telefone, fora do fecho de FK (#1557); tabela que só existe em produção (DP9); tabela
que o semeador não consegue preencher (o T3 reprova, não passa calado).
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
from db.open_finance import update_pluggy_open_finance_item_status
from db.privacy_export import _segredo_do_webhook, build_user_export_zip
from db.privacy_export_conjuntos import COLUNAS_SEM_SEGREDO, EXPORTADAS, NAO_EXPORTADAS
from tests._semeia_exportacao import semeia

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
_SEGREDO = re.compile(r"hash|token|secret|_enc$|password|^code$|jti|challenge|webhook"
                      r"|(^|_)(otp|cvv|salt|api_key|nonce|signature|private)(_|$)")


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


def _colunas(sql: str) -> list[str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, {"uid": -1})
        return [d.name for d in cur.description]


def _colunas_secretas(exportadas) -> list[tuple[str, str]]:
    existem = {t for t, _ in exportadas.values()} - set(ORFAS_PENDENTES_DP9)
    return [(nome, c) for nome, (tabela, sql) in exportadas.items() if tabela in existem
            for c in _colunas(sql) if _SEGREDO.search(c) and (nome, c) not in COLUNAS_SEM_SEGREDO]


def test_t2_estrutura_do_registro_real():
    assert _sql_ruim(EXPORTADAS) == []
    assert _colunas_secretas(EXPORTADAS) == []
    for nome, coluna in COLUNAS_SEM_SEGREDO:  # nenhuma isenção morta
        assert coluna in _colunas(EXPORTADAS[nome][1]), (nome, coluna)


def test_t2_guarda_acusa_posicional_estrela_e_coluna_nomeada_com_segredo():
    assert _sql_ruim({"x": ("users", "select * from users where id = %s")}) == ["x"]
    assert _colunas_secretas({"x": ("user_mfa", "select m.user_id, m.* from user_mfa m where user_id = %(uid)s")}) \
        == [("x", "secret_encrypted")]
    assert _colunas_secretas({"x": ("users", "select id, 'a' as secret from users where id = %(uid)s")}) \
        == [("x", "secret")]
    assert set(_colunas_secretas({"identidades": ("user_identities", "select * from user_identities")})) \
        == {("identidades", "external_id_hash"), ("identidades", "external_id_enc")}
    # os termos curtos só casam como palavra do snake_case: `basalt` e `notpin` não são `salt` nem `pin`
    assert _colunas_secretas({"x": ("users", "select 1 as otp_code, 1 as card_cvv, 1 as basalt, 1 as private"
                                              " from users where id = %(uid)s")}) \
        == [("x", "otp_code"), ("x", "card_cvv"), ("x", "private")]


def _zip(uid: int) -> tuple[dict, str, list[str]]:
    z = zipfile.ZipFile(io.BytesIO(build_user_export_zip(uid)))
    texto = "".join(z.read(n).decode("utf-8") for n in z.namelist())
    return json.loads(z.read("dados.json"))["dados"], texto, z.namelist()


def _vazios(dados: dict, erros: dict) -> list[tuple[str, str | None]]:
    """Datasets sem linha (menos os da DP9), com o erro do banco que impediu semear a tabela principal."""
    return [(d, erros.get(EXPORTADAS[d][0])) for d, linhas in dados.items()
            if not linhas and EXPORTADAS[d][0] not in ORFAS_PENDENTES_DP9]


def test_t3_zip_de_a_nao_tem_nada_de_b():
    tabelas = {t for t, _ in EXPORTADAS.values()} - set(ORFAS_PENDENTES_DP9)
    a, marca_a, soltas_a, erros_a = semeia("ISOA", tabelas)
    soltas_b = []
    try:
        antes, texto_antes, _ = _zip(a)
        assert _vazios(antes, erros_a) == []  # nada vazio por construção
        assert marca_a in texto_antes
        b, marca_b, soltas_b, erros_b = semeia("ISOB", tabelas)
        depois, texto, _ = _zip(a)
        assert {d: len(v) for d, v in depois.items()} == {d: len(v) for d, v in antes.items()}
        assert marca_b not in texto
        dados_b, texto_b, _ = _zip(b)  # positivo: B foi semeado de verdade, em todo dataset
        assert _vazios(dados_b, erros_b) == [] and marca_b in texto_b
    finally:  # FK `set null`: a cascata do conftest deixaria essas linhas soltas, com a marca
        for tabela, coluna, valor in soltas_a + soltas_b:
            _q(f"delete from {tabela} where {coluna} = %s", (valor,))


_URL = "https://pigbank.app/open-finance/pluggy/webhook?token=SEG_SEGREDO_WEBHOOK"
# As formas que o `raw` da conexão pode ter: o item da Pluggy, o corpo do webhook `item/deleted`
# (`update_pluggy_open_finance_item_status`), e o que mais não controlamos.
_RAW_COM_TOKEN = {
    "topo": {"webhookUrl": _URL, "clientUserId": "SEG_client"},
    "snake": {"webhook_url": _URL},
    "lista": {"history": [{"webhookUrl": _URL}]},
    "string": _URL,
    "outra_chave": {"parameter": {"callbackUrl": _URL.replace("token=", "TOKEN%3D")}},
}


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
    for forma, raw in _RAW_COM_TOKEN.items():
        _q("insert into open_finance_connections (user_id, provider, provider_item_id, status, institution_id,"
           " institution_name, raw) values (%s, 'pluggy', %s, 'UPDATED', '1', %s, %s::jsonb)",
           (uid, f"{marca}_{forma}_{uid}", forma, json.dumps(raw)))
    # O caminho real que regrava o `raw` com outro formato: o corpo do webhook `item/deleted`.
    _q("insert into open_finance_connections (user_id, provider, provider_item_id, status, institution_id,"
       " institution_name) values (%s, 'pluggy', %s, 'UPDATED', '1', 'aninhado')", (uid, f"{marca}_del_{uid}"))
    update_pluggy_open_finance_item_status(f"{marca}_del_{uid}", "DELETED", {
        "event": "item/deleted", "itemId": f"{marca}_del_{uid}", "item": {"webhookUrl": _URL}})
    return uid


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
    raws = {c["institution_name"]: c["raw"] for c in dados["conexoes_open_finance"]}
    assert raws["topo"] == {"webhookUrl": "(removido: continha token)", "clientUserId": "SEG_client"}
    assert raws["aninhado"]["item"] == {"webhookUrl": "(removido: continha token)"}
    assert dados["conta_login"][0]["display_name"] == "SEG_nome"


def test_t4_valor_do_segredo_do_webhook_nao_sai_em_forma_nenhuma(monkeypatch):
    """Além de `token=`: o próprio valor de `PLUGGY_WEBHOOK_SECRET`, em outro parâmetro, sozinho ou como chave."""
    segredo = "SEG_CRU_0123456789abcdef"
    monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", segredo)
    uid = int(uuid.uuid4().int % 900_000_000) + 1
    ensure_user(uid)
    raw = {"cb": f"https://x/hook?t={segredo}", "valor": segredo, segredo: 1, "clientUserId": "SEG_client"}
    _q("insert into open_finance_connections (user_id, provider, provider_item_id, status, institution_id,"
       " institution_name, raw) values (%s, 'pluggy', %s, 'UPDATED', '1', 'Banco', %s::jsonb)",
       (uid, f"cru_{uid}", json.dumps(raw)))
    dados, texto, _ = _zip(uid)
    assert segredo not in texto
    assert dados["conexoes_open_finance"][0]["raw"]["clientUserId"] == "SEG_client"
    monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", "curto")  # segredo curto casaria com texto comum: desligado
    assert _segredo_do_webhook() is None


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


def test_t5_membros_e_colunas_preservados():
    _, _, membros = _zip(_pessoa("FMT"))
    assert membros == ["dados.json", "manifesto.json", *(f"csv/{d}.csv" for d in DATASETS_DA_MAIN)]
    assert list(EXPORTADAS) == DATASETS_DA_MAIN
    main = {"conta_login": _CONTA_LOGIN_MAIN,
            "identidades": _colunas("select * from user_identities"),
            "sessoes_dashboard": ["code", "user_id", "expires_at", "created_at"]}
    for nome, (entram, saem) in MUDARAM.items():
        assert set(_colunas(EXPORTADAS[nome][1])) == (set(main[nome]) | entram) - saem, nome
