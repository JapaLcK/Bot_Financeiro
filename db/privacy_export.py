"""
db/privacy_export.py — Exportação LGPD: o ZIP que o titular baixa sobre si mesmo.

Saiu de `db/privacy.py`, que continua reexportando `build_user_export_zip`.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import zipfile
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from core.crypto import ATOR_TITULAR, PiiAccessContext, decrypt_pii_optional

from .connection import get_conn
from .privacy import _table_exists
from .privacy_export_conjuntos import EXPORTADAS

logger = logging.getLogger(__name__)


class PrivacyJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, (bytes, bytearray, memoryview)):
            return bytes(obj).hex()
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        return super().default(obj)


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, cls=PrivacyJSONEncoder, ensure_ascii=False))


def _fetch_rows(cur, name: str, sql: str, params: dict) -> tuple[str, list[dict]]:
    cur.execute(sql, params)
    return name, [dict(row) for row in cur.fetchall()]


# A9 (dono, 2026-10-29): chaves de topo de `system_event_logs.details` que não vão para o ZIP do
# titular. A pilha mostra o NOSSO código (caminho, linha, trecho), não é dado dele; o banco e o painel
# admin continuam com ela. Só o nível de cima: nenhum gravador aninha `traceback`.
_DETAILS_FORA_DA_EXPORTACAO = frozenset({"traceback"})


_CHAVE_INDECIFRAVEL = "(indecifrável)"
_TOKEN = re.compile(r"token(=|%3d)", re.I)
_SEGREDO_MINIMO = 16  # abaixo disso o valor do segredo casaria com texto comum


def _segredo_do_webhook() -> str | None:
    segredo = (os.getenv("PLUGGY_WEBHOOK_SECRET") or "").strip()
    return segredo if len(segredo) >= _SEGREDO_MINIMO else None


def _sem_token(valor: Any, segredo: str | None) -> Any:
    """Troca, em qualquer nível e também na chave, a string com `token=` (ou `token%3D`) ou com o próprio valor do
    `PLUGGY_WEBHOOK_SECRET` por um marcador. O `webhookUrl` que mandamos à Pluggy leva `?token=<segredo>`
    (frontend/routes/open_finance.py), e o `raw` da conexão é o item ou o corpo do webhook `item/deleted`
    (db/open_finance.py), com a URL em chave, nível e forma que não controlamos."""
    if isinstance(valor, str):
        vaza = _TOKEN.search(valor) or (segredo and segredo in valor)
        return "(removido: continha token)" if vaza else valor
    if isinstance(valor, dict):
        return {_sem_token(k, segredo): _sem_token(v, segredo) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_sem_token(v, segredo) for v in valor]
    return valor


def _chave_pix_em_claro(rows: list[dict], user_id: int) -> None:
    """Troca `pix_key_enc` por `chave_pix` em claro: o cifrado nunca chega ao ZIP. O token de uso
    único já foi consumido, então uma linha indecifrável vira marcador em vez de derrubar a exportação."""
    for row in rows:
        enc = row.pop("pix_key_enc")
        try:
            row["chave_pix"] = decrypt_pii_optional(enc, ctx=PiiAccessContext(
                purpose="data_export", actor=ATOR_TITULAR, subject_user_id=user_id, field="pix_key"))
        except Exception:
            row["chave_pix"] = _CHAVE_INDECIFRAVEL
            logger.warning("PII indecifrável na exportação: user_id=%s field=pix_key", user_id,
                           extra={"user_id": user_id})


_PALAVRA = re.compile(r"[A-Za-z0-9_-]+")  # o alfabeto do `token_urlsafe` do código do link mágico


def _sem_link_magico(mensagens: list[dict], codigos: set[str]) -> None:
    """PB8: no texto da conversa com o Piggy, todo `code` de `dashboard_sessions` da pessoa vira marcador. É o
    link `/d/{code}` que a resposta do `open_dashboard` grava em `ai_messages.content`: a mesma credencial que
    não sai em `sessoes_dashboard`. Código já consumido sumiu da tabela e fica no texto, mas está morto.
    ponytail: só olha mensagem com `/d/` (o único formato em que o código chega ao texto); fora dele, sem o custo
    de tokenizar dezenas de milhares de mensagens."""
    for m in mensagens:
        if codigos and m["content"] and "/d/" in m["content"]:
            m["content"] = _PALAVRA.sub(lambda p: "(removido)" if p[0] in codigos else p[0], m["content"])


# PB6: os 3 avisos de falha do Plano Família levam o uid de outra pessoa (`db/family.py`, `accept_invite`;
# `core/services/family_access.py`, `sync_from_owner`; `db/family_grants.py`, `recompute_users`). O log interno
# fica com a correlação; no ZIP sai só o uid do titular do ZIP, inclusive nas linhas já gravadas.
_FAMILIA_COM_TERCEIRO = re.compile(
    r"^(family: (?:grant do convidado|grant do membro|recompute do membro) )(\d+)( \(titular )(\d+)")


def _sem_uid_de_terceiro(mensagem: str | None, user_id: int) -> str | None:
    def so_o_titular(uid: str) -> str:
        return uid if uid == str(user_id) else "(removido)"
    return _FAMILIA_COM_TERCEIRO.sub(lambda m: m[1] + so_o_titular(m[2]) + m[3] + so_o_titular(m[4]),
                                     mensagem) if mensagem else mensagem


def build_user_export_zip(user_id: int) -> bytes:
    datasets: dict[str, list[dict]] = {}

    with get_conn() as conn, conn.cursor() as cur:
        for name, (tabela, sql) in EXPORTADAS.items():
            datasets[name] = _fetch_rows(cur, name, sql, {"uid": user_id})[1] if _table_exists(cur, tabela) else []
        cur.execute("select code from dashboard_sessions where user_id = %(uid)s", {"uid": user_id})
        codigos = {row["code"] for row in cur.fetchall()}

    _sem_link_magico(datasets["conversas_ia"], codigos)

    # O saque decifra o próprio snapshot: a chave usada naquele pedido, não a cadastrada hoje.
    for name in ("afiliado", "afiliado_saques"):
        _chave_pix_em_claro(datasets[name], user_id)

    segredo = _segredo_do_webhook()
    for conexao in datasets["conexoes_open_finance"]:
        conexao["raw"] = _sem_token(conexao["raw"], segredo)

    for evento in datasets["eventos_sistema"]:
        evento["message"] = _sem_uid_de_terceiro(evento["message"], user_id)
        if isinstance(evento["details"], dict):
            evento["details"] = {k: v for k, v in evento["details"].items()
                                 if k not in _DETAILS_FORA_DA_EXPORTACAO}

    manifest = {
        "generated_at": datetime.now(timezone.utc),
        "user_id": user_id,
        "format": "json+csv",
        "datasets": {name: len(rows) for name, rows in datasets.items()},
        "notes": [
            "Não exportamos credenciais: hashes de senha, códigos e links de acesso, tokens, o segredo da"
            " verificação em duas etapas e os tokens de notificação.",
            "Arquivos CSV são cópias tabulares; dados aninhados também aparecem no JSON completo.",
            "Do programa de afiliados saem só os dados do titular, com a chave Pix em claro (a cadastrada"
            " e a usada em cada saque); as pessoas indicadas não são identificadas, e de quem você indicou"
            " sai só a contagem.",
            "Do Plano Família sai a sua participação; quem leu os seus dados aparece como 'titular do grupo'.",
            "Os acessos aos seus dados pessoais saem agregados por dia, por quem (você, sistema, equipe PigBank"
            " ou não identificado), por finalidade e por dado; o nome de quem trabalha no PigBank não sai.",
        ],
    }
    payload = {"manifesto": manifest, "dados": datasets}

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "dados.json",
            json.dumps(_json_safe(payload), ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "manifesto.json",
            json.dumps(_json_safe(manifest), ensure_ascii=False, indent=2),
        )
        for name, rows in datasets.items():
            csv_buffer = io.StringIO()
            fieldnames = sorted({key for row in rows for key in row.keys()})
            writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames or ["sem_dados"])
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    key: json.dumps(_json_safe(value), ensure_ascii=False) if isinstance(value, (dict, list)) else value
                    for key, value in row.items()
                })
            zf.writestr(f"csv/{name}.csv", csv_buffer.getvalue())

    return buffer.getvalue()
