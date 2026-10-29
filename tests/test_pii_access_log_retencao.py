"""Retenção de 180 dias de `pii_access_log` (`purge_pii_access_log`, #665) e o `actor`
do titular sem uid (`ATOR_TITULAR`, DP8).

Isolamento: a poda é GLOBAL, e o `run_table_cleanup` real deste arquivo, de
`test_table_cleanup.py` e de `test_system_event_logs_retencao.py` roda no mesmo database
quando cai no mesmo worker xdist (um database por worker, `tests/conftest.py`; um só para
todos com PYTEST_DB_ISOLATION=0). Por isso cada linha leva uma chave SÓ DE LETRAS em
`purpose`, toda asserção filtra por ela, e nenhuma asserção de "fica" usa linha mais
velha que o default.

Cego para:
  1. o valor de PII_ACCESS_LOG_RETENTION_DAYS no Railway (só a constante);
  2. a G1 só vê `actor` em f-string cujo campo interpolado usa o MESMO nome do
     `subject_user_id` (inclusive `f"user:{int(uid)}"`), por keyword: uid com outro
     nome (`u = user_id`), ator montado fora da chamada (`a = f"user:{uid}"`),
     `"user:" + str(uid)`, `"user:%s" % uid`, `"user:{}".format(uid)` (só f-string é
     vista; as 4 linhas de `db_support.py` o R4 cobre pelo banco), argumento por posição e
     `dataclasses.replace` passam (hoje não há nenhum). E erra para o lado de acusar quando
     ator e titular dividem um nome (`f"admin:{ctx.admin}"` com `subject_user_id=ctx.user_id`,
     ou um builtin como `int`); hoje não ocorre na árvore;
  3. o R4 não passa pelo WhatsApp nem pelo `handle_incoming`: prova a função que
     grava, não quem a chama;
  4. o volume e o tempo reais do DELETE em produção.

Rodar:
  DATABASE_URL=... PYTHONPATH=. .venv/bin/python -m pytest tests/test_pii_access_log_retencao.py -q
"""
from __future__ import annotations

import ast
import uuid
from pathlib import Path

import pytest

import db
import db_support
from _helpers_pii import bind_identity_pii, insert_auth_account_pii
from core.crypto import ATOR_TITULAR
from core.services import log_retention
from core.services.log_retention import apagar_em_lotes, purge_pii_access_log
from core.services.table_cleanup import run_table_cleanup
from db.connection import get_conn
from test_max_lines_python import _rastreados

_SO_LETRAS = str.maketrans("0123456789", "ghijklmnop")
_RAIZ = Path(__file__).resolve().parent.parent
_ENV = "PII_ACCESS_LOG_RETENTION_DAYS"


@pytest.fixture
def chave():
    """Gera chaves só de letras (gravadas em `purpose`) e apaga as linhas no fim."""
    feitas: list[str] = []

    def nova() -> str:
        feitas.append(uuid.uuid4().hex.translate(_SO_LETRAS))
        return feitas[-1]

    yield nova
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("delete from pii_access_log where purpose = any(%s)", (feitas,))
        conn.commit()


def _inserir(chave: str, rotulo: str, dias: int, subject: int | None = None) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into pii_access_log (purpose, actor, subject_user_id, field, created_at)"
            " values (%s, 'system:teste', %s, %s, now() - make_interval(days => %s))",
            (chave, subject, rotulo, dias),
        )
        conn.commit()


def _sobra(chave: str) -> set[str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select field from pii_access_log where purpose = %s", (chave,))
        return {r["field"] for r in cur.fetchall()}


def test_linha_velha_cai_com_e_sem_titular_e_a_recente_fica(chave, user_id, monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    k = chave()
    _inserir(k, "d181_sem_dono", 181)
    _inserir(k, "d181_com_dono", 181, user_id)
    _inserir(k, "d400", 400)
    _inserir(k, "d179", 179, user_id)

    resumo = run_table_cleanup()

    assert resumo["errors"] == []
    assert "pii_access_log" in resumo["removed"]
    assert _sobra(k) == {"d179"}


@pytest.mark.parametrize("valor, dias, avisa", [
    (None, 180, False), ("0", None, False), ("-5", None, False),
    ("abc", 180, True), ("", 180, True), ("400", 400, False), ("30", 180, True),
    ("179", 180, True), ("180", 180, False),
])
def test_env_da_retencao(monkeypatch, capsys, valor, dias, avisa):
    chamadas = []

    def espiao(tabela, onde, params, **kw):
        chamadas.append((tabela, onde, params, kw))
        return 7

    monkeypatch.setattr(log_retention, "apagar_em_lotes", espiao)
    if valor is None:
        monkeypatch.delenv(_ENV, raising=False)
    else:
        monkeypatch.setenv(_ENV, valor)

    n = purge_pii_access_log()

    if dias is None:
        assert (n, chamadas) == (0, [])
    else:
        assert n == 7
        # `kw == {}`: lote e teto ficam nos defaults (DP6)
        assert chamadas == [("pii_access_log", "created_at < now() - make_interval(days => %s)",
                             (dias,), {})]
    assert (_ENV in capsys.readouterr().err) is avisa


def test_teto_de_lotes_no_pii_access_log(chave):
    """1 dia de idade: a poda global nunca toca estas linhas."""
    k = chave()
    for i in range(5):
        _inserir(k, f"l{i}", 1)
    assert apagar_em_lotes("pii_access_log", "purpose = %s", (k,), lote=2, teto_lotes=2) == 4
    assert len(_sobra(k)) == 1
    assert apagar_em_lotes("pii_access_log", "purpose = %s", (k,), lote=2, teto_lotes=2) == 1
    assert _sobra(k) == set()


def test_actor_do_titular_nao_leva_o_uid(user_id, monkeypatch):
    monkeypatch.setenv("PII_AUDIT_DISABLED", "0")  # o conftest desliga por padrão
    uid = user_id
    sufixo = uuid.uuid4().hex[:8]
    with get_conn() as conn, conn.cursor() as cur:
        insert_auth_account_pii(cur, uid, f"pii-{sufixo}@teste.local",
                                phone=f"+55119{uuid.uuid4().int % 10**8:08d}", name="Fulana")
        bind_identity_pii(cur, "whatsapp", f"55119{uuid.uuid4().int % 10**8:08d}", uid)
        conn.commit()
    db_support.invalidate_auth_user_cache(uid)  # cache quente esconderia o decrypt

    db.get_auth_user(uid)
    db.list_identities_by_user(uid)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id, purpose, field, actor from pii_access_log where subject_user_id = %s"
                    " and purpose in ('get_auth_user', 'list_identities')", (uid,))
        linhas = cur.fetchall()
    assert {(r["purpose"], r["field"]) for r in linhas} == {
        ("get_auth_user", "email"), ("get_auth_user", "phone"), ("get_auth_user", "name"),
        ("list_identities", "whatsapp_id")}
    assert {r["actor"] for r in linhas} == {ATOR_TITULAR}

    db.delete_user_data(uid)

    ids = [r["id"] for r in linhas]
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select subject_user_id, actor, purpose, endpoint, extra::text as extra"
                    " from pii_access_log where id = any(%s)", (ids,))
        depois = cur.fetchall()
        cur.execute("delete from pii_access_log where id = any(%s)", (ids,))
        conn.commit()
    assert len(depois) == len(ids)  # a trilha sobrevive à exclusão (SET NULL)
    for r in depois:
        assert r["subject_user_id"] is None
        texto = " ".join(str(r[c] or "") for c in ("actor", "purpose", "endpoint", "extra"))
        assert str(uid) not in texto


def _achados(fonte: str, nome: str) -> list[str]:
    """`PiiAccessContext(...)` cujo `actor` em f-string interpola um nome do `subject_user_id`."""
    achados = []
    for no in ast.walk(ast.parse(fonte)):
        if not (isinstance(no, ast.Call) and ast.unparse(no.func).endswith("PiiAccessContext")):
            continue
        kw = {k.arg: k.value for k in no.keywords}
        actor, titular = kw.get("actor"), kw.get("subject_user_id")
        if isinstance(actor, ast.JoinedStr) and titular is not None:
            nomes = {n.id for n in ast.walk(titular) if isinstance(n, ast.Name)}
            if any(isinstance(n, ast.Name) and n.id in nomes
                   for v in actor.values if isinstance(v, ast.FormattedValue)
                   for n in ast.walk(v.value)):
                achados.append(f"{nome}:{no.lineno}")
    return achados


def test_nenhum_gravador_poe_o_titular_no_actor():
    achados = []
    for rel in _rastreados(_RAIZ):
        if rel.startswith(("tests/", "harness_tests/")):
            continue
        achados += _achados((_RAIZ / rel).read_text(encoding="utf-8"), rel)
    assert achados == []
    # negativo e positivo, pela mesma função
    assert _achados('PiiAccessContext(actor=f"user:{uid}", subject_user_id=uid)', "x") == ["x:1"]
    assert _achados('PiiAccessContext(actor=f"user:{int(uid)}", subject_user_id=uid)', "x") == ["x:1"]
    assert _achados('PiiAccessContext(actor=f"admin:{username}", subject_user_id=uid)', "x") == []
