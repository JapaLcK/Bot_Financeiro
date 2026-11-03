"""Helpers dos testes de "o dono do log vai na coluna" (#657, grupo D): o `_DashboardHandler`
real no root, a exceção do psycopg com marcador e telefone no `DETAIL`, a busca das linhas de
`system_event_logs` por marca, a exportação do titular e o `_vazou`.

Sem prefixo `test_` de propósito: o pytest não coleta este arquivo. Quem importa o
`banco_real` (fixture autouse) o liga no próprio módulo.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import uuid
import zipfile

import psycopg
import pytest

import core.observability as observability
import db
import db.privacy as privacy
from conftest import promote_to_pro
from core.admin_dashboard import ensure_admin_tables
from db.connection import get_conn
from utils_text import normalize_text

FONE = "11987654321"


@pytest.fixture(autouse=True)
def banco_real():
    """O `_DashboardHandler` real no root, gravando de fato em `system_event_logs`."""
    asyncio.run(ensure_admin_tables())  # `system_event_logs` não vem de db/schema.py
    observability._configure_root_logger()
    assert any(isinstance(h, observability._DashboardHandler)
               for h in logging.getLogger().handlers), \
        "sem o _DashboardHandler no root nada chega ao banco e o grupo não mede nada"


def _violacao(marcador: str) -> psycopg.errors.UniqueViolation:
    return psycopg.errors.UniqueViolation(
        'duplicate key value violates unique constraint "launches_nota_key"\n'
        f"DETAIL:  Key (user_id, nota)=(1, {marcador} {FONE}) already exists.")


def _levanta(marcador: str):
    def _f(*a, **k):
        raise _violacao(marcador)
    return _f


def _uid() -> int:
    """Id abaixo de 2e9: o `_normalize_user_id` comprime os maiores (id de WhatsApp)."""
    uid = 1_000_000 + uuid.uuid4().int % 1_000_000_000
    db.ensure_user(uid)
    promote_to_pro(uid)
    return uid


def _marca() -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select coalesce(max(id), 0) as m from system_event_logs")
        m = int(cur.fetchone()["m"])
        conn.commit()
    return m


def _linhas(marca: int, where: str = "true", args: tuple = ()) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id, event_type, message, source, user_id, details from system_event_logs"
                    f" where id > %s and {where} order by id", (marca, *args))
        r = cur.fetchall()
        conn.commit()
    return [dict(x) for x in r]


def _uma(linhas: list[dict], event_type: str, source: str, prefixo: str = "") -> dict:
    """A linha única do `event_type`, com o `source` exato (o painel filtra e rotula por eles)."""
    achadas = [x for x in linhas if x["event_type"] == event_type and x["message"].startswith(prefixo)]
    assert len(achadas) == 1, (event_type, prefixo, [(x["event_type"], x["message"][:80]) for x in linhas])
    assert achadas[0]["source"] == source, achadas[0]
    return achadas[0]


def _exportados(uid: int) -> tuple[str, set[int]]:
    with zipfile.ZipFile(io.BytesIO(privacy.build_user_export_zip(uid))) as zf:
        bruto = zf.read("dados.json").decode()
    return bruto, {e["id"] for e in json.loads(bruto)["dados"]["eventos_sistema"]}


def _vazou(texto: str, *proibidos: str) -> str | None:
    """O 1º proibido que aparece no texto, comparando NORMALIZADO: o código loga a `nota` já
    passada pelo `normalize_text` ("acai da dona zelia"), sem acento nem maiúscula. Proibido que
    normaliza para vazio casaria com tudo e esconderia os seguintes: falha alto. O `DETAIL:` do
    psycopg vai cru, para não casar com "details"."""
    dobrado = normalize_text(texto)
    for p in (*proibidos, FONE, "already exists"):
        assert normalize_text(p or ""), f"proibido vazio ({p!r}) não confere nada"
        if normalize_text(p) in dobrado:
            return p
    return "DETAIL:" if "DETAIL:" in texto else None


def _sem_vazamento(linha: dict, *proibidos: str) -> None:
    texto = linha["message"] + json.dumps(linha["details"], ensure_ascii=False)
    assert not (p := _vazou(texto, *proibidos)), f"{p!r} vazou em {linha['event_type']}: {texto}"


def _limpa(linha: dict, *proibidos: str) -> None:
    """Sem vazamento e sem pilha: um `exc_info` no record vira `details.traceback` no
    `_DashboardHandler`, então a ausência da chave prova que o record não o tinha."""
    _sem_vazamento(linha, *proibidos)
    assert "traceback" not in (linha["details"] or {}), linha


def _isola_e_apaga(a: int, b: int, ids_a: set[int], id_b: int | set[int], marca: int) -> None:
    """As linhas de A saem no ZIP de A e não no de B; a exclusão de A as apaga e deixa as de B
    (`id_b`: uma linha ou um conjunto)."""
    ids_b = id_b if isinstance(id_b, set) else {id_b}
    assert ids_a <= _exportados(a)[1], "a falha de A não saiu na exportação de A"
    assert not ids_a & _exportados(b)[1], "linha de A na exportação de B"
    privacy.delete_user_data(a)
    assert _linhas(marca, "user_id = %s", (a,)) == [], "log de A sobreviveu à exclusão"
    assert len(_linhas(marca, "id = any(%s)", (list(ids_b),))) == len(ids_b), "a exclusão de A levou linha de B"


def _nenhuma(linhas: list[dict], event_type: str, prefixo: str = "") -> None:
    """Nenhuma linha do `event_type` com o prefixo: a falha de um não cai na coluna do outro."""
    achadas = [x for x in linhas if x["event_type"] == event_type and x["message"].startswith(prefixo)]
    assert not achadas, (event_type, prefixo, achadas)


def _marcador() -> str:
    return f"MARCADOR-{uuid.uuid4().hex}"


# A falha cai no 1º visitado (prova que o laço SEGUE) e no 2º (prova que a linha é do dono da volta
# ATUAL, e não do primeiro do lote: `extra={"user_id": lista[0]}` passaria só com o 1º).
FALHA_NO = pytest.mark.parametrize("falha_no", [1, 2], ids=["falha_no_1o", "falha_no_2o"])
