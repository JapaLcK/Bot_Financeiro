"""A9 do grupo D: o ZIP da exportação LGPD leva `eventos_sistema` sem `details.traceback`.

A pilha é do NOSSO código, não dado do titular; o banco e o painel admin continuam com
ela. Só a chave de topo sai (`db/privacy.py::_DETAILS_FORA_DA_EXPORTACAO`); o resto do
`details` e a linha inteira ficam. Conferido no `dados.json` E no `csv/eventos_sistema.csv`.

CONTROLES (CLAUDE.md §3), rodados à mão no PR: lista vazia → vermelho; o arquivo contra a
`main` sem o filtro → vermelho; CSV lendo cópia feita antes do filtro → vermelho só no CSV.
Positivo: as linhas sem `traceback` da T2 saem idênticas, e nenhuma linha some.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import zipfile

from psycopg.types.json import Jsonb

import core.handle_incoming as hi
from conftest import usuario_pagante
from core.admin_dashboard import ensure_admin_tables
from core.types import IncomingMessage
from db.connection import get_conn
from db.privacy import build_user_export_zip


def _zip(uid: int) -> tuple[dict, dict, dict]:
    """(eventos do JSON por id, `details` cru do CSV por id, manifesto)."""
    with zipfile.ZipFile(io.BytesIO(build_user_export_zip(uid))) as zf:
        dados = json.loads(zf.read("dados.json"))
        linhas_csv = list(csv.DictReader(io.StringIO(zf.read("csv/eventos_sistema.csv").decode())))
    eventos = {e["id"]: e for e in dados["dados"]["eventos_sistema"]}
    return eventos, {int(r["id"]): r["details"] for r in linhas_csv}, dados["manifesto"]


def _celula(celula: str, esperado):
    """Desfaz a escrita de `build_user_export_zip`: dict/list em JSON, None vazio, resto cru."""
    if isinstance(esperado, (dict, list)):
        return json.loads(celula)
    return None if celula == "" else celula


def _db(sql: str, params: tuple):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        linhas = cur.fetchall() if cur.description else None
        conn.commit()
    return linhas


def test_falha_da_conversa_sai_no_zip_sem_a_pilha(monkeypatch):
    asyncio.run(ensure_admin_tables())  # `system_event_logs` não vem de db/schema.py
    a, b = usuario_pagante(), usuario_pagante()  # id pequeno: `_normalize_user_id` não troca

    def explode(*_a, **_k):
        raise RuntimeError("boom")
    monkeypatch.setattr(hi, "_paywall_gate", explode)  # 1º passo do try, log_system_event_sync REAL
    texto = "gastei 18,90 no açaí"
    for uid in (a, b):
        hi.handle_incoming(IncomingMessage(platform="whatsapp", user_id=uid, text=texto))

    def falhas(uid):
        return _db("select id, details from system_event_logs where user_id = %s "
                   "and event_type = 'message_processing_failed'", (uid,))
    (linha_a,), (linha_b,) = falhas(a), falhas(b)
    # O banco (e o painel) continua com a pilha: sem isto o teste passaria mudo.
    assert 'File "' in linha_a["details"]["traceback"], linha_a

    eventos, csv_details, _ = _zip(a)
    esperado = {k: v for k, v in linha_a["details"].items() if k != "traceback"}
    assert esperado["chars"] == len(texto) and esperado["platform"] == "whatsapp", esperado
    assert eventos[linha_a["id"]]["details"] == esperado, eventos[linha_a["id"]]
    assert json.loads(csv_details[linha_a["id"]]) == esperado, csv_details[linha_a["id"]]
    assert linha_b["id"] not in eventos and linha_b["id"] not in csv_details  # isolamento


def test_formas_de_details_no_zip(user_id):
    asyncio.run(ensure_admin_tables())
    pilha = 'Traceback (most recent call last):\n  File "/app/x.py", line 1'
    casos = [  # (level, gravado, esperado no ZIP)
        ("error", {"traceback": pilha, "sqlstate": "23505", "chars": 12, "aninhado": {"traceback": "fica"}},
         {"sqlstate": "23505", "chars": 12, "aninhado": {"traceback": "fica"}}),
        # Não é `error` e a pilha veio cortada pelo fim (`tb[-1500:]`), sem o prefixo "Traceback":
        # sai igual. Pega filtro por nível e filtro pelo conteúdo do valor.
        ("warning", {"traceback": '  File "/app/x.py", line 3, in f\n', "chars": 3}, {"chars": 3}),
        ("error", {"sqlstate": "23505", "items": ["a", "b"]}, {"sqlstate": "23505", "items": ["a", "b"]}),
        ("error", {}, {}),
        ("error", None, None),
        ("error", ["traceback", "x"], ["traceback", "x"]),
        ("error", "traceback", "traceback"),
    ]
    esperado_por_id = {}
    for level, gravado, esperado in casos:
        (linha,) = _db(
            "insert into system_event_logs (level, event_type, message, source, user_id, details) "
            "values (%s, 'a9_forma', 'm', 't', %s, %s) returning id",
            (level, user_id, None if gravado is None else Jsonb(gravado)),
        )
        esperado_por_id[linha["id"]] = (level, esperado)

    eventos, csv_details, manifesto = _zip(user_id)
    for id_, (level, esperado) in esperado_por_id.items():
        assert eventos[id_]["details"] == esperado, (id_, eventos[id_])
        assert (eventos[id_]["level"], eventos[id_]["event_type"], eventos[id_]["message"]) == (
            level, "a9_forma", "m"), eventos[id_]
        assert _celula(csv_details[id_], esperado) == esperado, (id_, csv_details[id_])
    (total,) = _db("select count(*) as n from system_event_logs where user_id = %s", (user_id,))
    assert manifesto["datasets"]["eventos_sistema"] == total["n"] == len(casos)
