"""
db/privacy_export.py — Exportação LGPD: o ZIP que o titular baixa sobre si mesmo.

Saiu de `db/privacy.py`, que continua reexportando `build_user_export_zip`.
"""
from __future__ import annotations

import csv
import io
import json
import logging
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


def build_user_export_zip(user_id: int) -> bytes:
    datasets: dict[str, list[dict]] = {}

    with get_conn() as conn, conn.cursor() as cur:
        for name, (tabela, sql) in EXPORTADAS.items():
            datasets[name] = _fetch_rows(cur, name, sql, {"uid": user_id})[1] if _table_exists(cur, tabela) else []

    # O saque decifra o próprio snapshot: a chave usada naquele pedido, não a cadastrada hoje.
    for name in ("afiliado", "afiliado_saques"):
        _chave_pix_em_claro(datasets[name], user_id)

    # O `webhookUrl` do item da Pluggy pode levar `?token=<PLUGGY_WEBHOOK_SECRET>` (frontend/routes/open_finance.py).
    for conexao in datasets["conexoes_open_finance"]:
        if isinstance(conexao["raw"], dict):
            conexao["raw"].pop("webhookUrl", None)

    for evento in datasets["eventos_sistema"]:
        if isinstance(evento["details"], dict):
            evento["details"] = {k: v for k, v in evento["details"].items()
                                 if k not in _DETAILS_FORA_DA_EXPORTACAO}

    manifest = {
        "generated_at": datetime.now(timezone.utc),
        "user_id": user_id,
        "format": "json+csv",
        "datasets": {name: len(rows) for name, rows in datasets.items()},
        "notes": [
            "Hashes de senha não são exportados.",
            "Arquivos CSV são cópias tabulares; dados aninhados também aparecem no JSON completo.",
            "Do programa de afiliados saem só os dados do titular, com a chave Pix em claro (a cadastrada"
            " e a usada em cada saque); as pessoas indicadas não são identificadas, e de quem você indicou"
            " sai só a contagem.",
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
