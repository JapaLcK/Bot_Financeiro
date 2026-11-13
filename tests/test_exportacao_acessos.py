"""PR-11 B (#637): o `pii_access_log` sai agregado em `acessos_dados_pessoais` (P2 a).

Por dia (em São Paulo) × quem × finalidade × dado, com a contagem. Quem vem de lista explícita (PB3 c): `user` é
"você"; `system` e `webhook`, "sistema"; `admin` e `cli`, "equipe PigBank"; outro prefixo, "não identificado".
A finalidade é só o que vem antes de `:` (PB4 a). Não saem o nome do operador, `endpoint` nem `extra`.

INSERT direto: a suíte roda com `PII_AUDIT_DISABLED=1` (`tests/conftest.py`). Os registros alterados são os
controles negativos, na suíte.
"""
from __future__ import annotations

import io
import json
import uuid
import zipfile

from db import ensure_user, get_conn, privacy_export
from db.privacy_export import build_user_export_zip
from db.privacy_export_conjuntos import EXPORTADAS

_HOJE = "2026-11-11 15:00+00"
_ONTEM_NO_BRASIL = "2026-11-10 02:30+00"  # 23h30 de 09/11 em São Paulo
_LINHAS_DE_A = [
    *[("user:self", "get_auth_user", "email", _HOJE)] * 3,
    ("user:{a}", "get_auth_user", "email", _HOJE),  # formato antigo (antes do #1323), do próprio titular
    ("admin:fulano_SEG", "render_admin_user_detail", "phone", _HOJE),
    ("cli:operador_SEG", "cli_inspect:motivo_SEG", "name", _HOJE),
    ("webhook:pluggy", "of_sync", "email", _HOJE),
    ("suporte:beltrano_SEG", "ler_ficha", "email", _HOJE),
    *[("system:audit", "audit_new_login_notice", "email", _ONTEM_NO_BRASIL)] * 2,
]
ESPERADO = [
    ["2026-11-09", "sistema", "audit_new_login_notice", "email", 2],
    ["2026-11-11", "equipe PigBank", "cli_inspect", "name", 1],
    ["2026-11-11", "equipe PigBank", "render_admin_user_detail", "phone", 1],
    ["2026-11-11", "não identificado", "ler_ficha", "email", 1],
    ["2026-11-11", "sistema", "of_sync", "email", 1],
    ["2026-11-11", "você", "get_auth_user", "email", 4],
]
_NAO_SAEM = ("fulano_SEG", "operador_SEG", "motivo_SEG", "beltrano_SEG", "SEG_endpoint", "SEG_extra", "B_SEG")


def _sql(q, args):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(q, args)
        conn.commit()


def _insere(subject, actor, purpose, field, at):
    _sql("insert into pii_access_log (purpose, actor, subject_user_id, field, endpoint, extra, created_at)"
         " values (%s, %s, %s, %s, '/admin/SEG_endpoint', '{\"SEG_extra\": 1}', %s)",
         (purpose, actor, subject, field, at))


def _problemas(monkeypatch, registro, a: int) -> list[str]:
    monkeypatch.setattr(privacy_export, "EXPORTADAS", registro)
    z = zipfile.ZipFile(io.BytesIO(build_user_export_zip(a)))
    texto = "".join(z.read(n).decode("utf-8") for n in z.namelist())
    linhas = [[r["dia"], r["quem"], r["finalidade"], r["dado"], r["acessos"]]
              for r in json.loads(z.read("dados.json"))["dados"]["acessos_dados_pessoais"]]
    return [f"no ZIP: {m}" for m in _NAO_SAEM if m in texto] + ([] if linhas == ESPERADO else [f"agregado: {linhas}"])


def _variante(de: str, para: str) -> dict:
    tabela, sql = EXPORTADAS["acessos_dados_pessoais"]
    assert de in sql, de  # a variante tem de mudar a SQL de verdade
    return {**EXPORTADAS, "acessos_dados_pessoais": (tabela, sql.replace(de, para))}


def test_acessos_saem_agregados_e_sem_operador(monkeypatch):
    a, b = (int(uuid.uuid4().int % 900_000_000) + 1 for _ in range(2))
    ensure_user(a)
    ensure_user(b)
    try:
        for actor, purpose, field, at in _LINHAS_DE_A:
            _insere(a, actor.format(a=a), purpose, field, at)
        _insere(b, "user:self", "B_SEG_purpose", "email", _HOJE)
        assert _problemas(monkeypatch, EXPORTADAS, a) == []  # positivo
        # negativos: cada variante quebra uma decisão do dono
        assert "no ZIP: fulano_SEG" in _problemas(
            monkeypatch, _variante("case split_part(actor", "actor || case split_part(actor"), a)
        assert "no ZIP: motivo_SEG" in _problemas(
            monkeypatch, _variante("split_part(purpose, ':', 1)", "purpose"), a)
        assert "no ZIP: B_SEG" in _problemas(monkeypatch, _variante("%(uid)s", "%(uid)s or true"), a)
        for de, para in ((" when 'cli' then 'equipe PigBank'", " when 'cli' then 'sistema'"),
                         ("else 'não identificado'", "else 'sistema'")):
            assert [p for p in _problemas(monkeypatch, _variante(de, para), a) if p.startswith("agregado")], de
    finally:  # FK `set null`: a limpeza do conftest deixaria as linhas soltas
        _sql("delete from pii_access_log where subject_user_id = any(%s)", ([a, b],))
