"""PR-11 B (#637): na exportação LGPD, cada papel do Plano Família recebe só o que é dele (P1 a).

O T3 genérico (`tests/test_exportacao_completa.py`) põe o mesmo uid em toda coluna de pessoa da linha, então é
cego a uma SQL que filtre pela coluna de pessoa errada (`member_user_id` × `viewer_user_id`). Aqui A é titular e
B membro do MESMO grupo, montados pelas funções reais (`create_group`, convite aceito, `grant_consent`,
`log_read`), e cada ZIP é conferido papel a papel. Os registros trocados são os controles negativos, na suíte.

Também aqui (PB6): os 3 avisos de falha da família levam o uid da outra pessoa; no ZIP ele vira marcador, inclusive
nas linhas já gravadas, e o resto da mensagem fica.
"""
from __future__ import annotations

import asyncio
import io
import json
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

import db
from _billing_grants_helpers import conta
from core.admin_dashboard import ensure_admin_tables
from db import family as fam
from db import family_consents as fc
from db import get_conn, privacy_export
from db.privacy_export import build_user_export_zip
from db.privacy_export_conjuntos import EXPORTADAS


def _u(plano: str = "free") -> int:
    uid = 100_000_000 + uuid.uuid4().int % 800_000_000  # 9 dígitos: o uid de um não é trecho do de outro
    db.ensure_user(uid)
    conta(uid, plano, datetime.now(timezone.utc) + timedelta(days=30), "active")
    return uid


def _sql(q, args=()):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(q, args)
        conn.commit()


def _zip(uid: int) -> tuple[dict, str]:
    z = zipfile.ZipFile(io.BytesIO(build_user_export_zip(uid)))
    return json.loads(z.read("dados.json"))["dados"], "".join(z.read(n).decode("utf-8") for n in z.namelist())


@pytest.fixture
def grupo():
    """A titular (pro_max), B membro com `aggregates` liberado e lido por A, um assento pago e um convite pendente."""
    a, b = _u("pro_max"), _u()
    gid = fam.create_group(a)
    fam.accept_invite(fam.create_invite(a), b)
    fc.grant_consent(b, "aggregates")
    fc.log_read(gid, a, b, "aggregates", "agregados:2026-10")
    _sql("insert into family_seat_purchases (group_id, source, external_ref, amount_cents, status)"
         " values (%s, 'pix', 'vaga1', 1000, 'paid')", (gid,))
    return a, b, fam.create_invite(a)


def _vazamentos(monkeypatch, registro, a: int, b: int, convite: str) -> list[str]:
    monkeypatch.setattr(privacy_export, "EXPORTADAS", registro)
    za, ta = _zip(a)
    zb, tb = _zip(b)
    erros = []
    if [p["role"] for p in zb["familia_participacao"]] != ["member"]:
        erros.append(f"B participação: {zb['familia_participacao']}")
    if [p["scope"] for p in zb["familia_permissoes"]] != ["aggregates"]:
        erros.append(f"B permissões: {zb['familia_permissoes']}")
    if [(r["quem_leu"], r["recurso"]) for r in zb["familia_leituras_dos_meus_dados"]] \
            != [("titular do grupo", "agregados:2026-10")]:
        erros.append(f"B leituras: {zb['familia_leituras_dos_meus_dados']}")
    if zb["familia_grupo"] or zb["familia_assentos"]:
        erros.append(f"B com grupo/assentos do titular: {zb['familia_grupo']} {zb['familia_assentos']}")
    if [p["role"] for p in za["familia_participacao"]] != ["owner"]:  # PB5 (a)
        erros.append(f"A participação: {za['familia_participacao']}")
    if len(za["familia_grupo"]) != 1 or [s["external_ref"] for s in za["familia_assentos"]] != ["vaga1"]:
        erros.append(f"A grupo/assentos: {za['familia_grupo']} {za['familia_assentos']}")
    if za["familia_permissoes"] or za["familia_leituras_dos_meus_dados"]:
        erros.append(f"A com permissões/leituras de B: {za['familia_permissoes']} {za['familia_leituras_dos_meus_dados']}")
    if str(a) in tb:
        erros.append("uid de A no ZIP de B")
    if str(b) in ta:
        erros.append("uid de B no ZIP de A")
    for segredo in (convite, fam.hash_token(convite)):
        if segredo in ta or segredo in tb:
            erros.append("token do convite no ZIP")
    return erros


def _troca(registro, nomes, de, para):
    return {**registro, **{n: (registro[n][0], registro[n][1].replace(de, para)) for n in nomes}}


def test_cada_papel_recebe_so_o_seu(monkeypatch, grupo):
    a, b, convite = grupo
    assert _vazamentos(monkeypatch, EXPORTADAS, a, b, convite) == []  # positivo


def test_guarda_acusa_coluna_de_pessoa_trocada(monkeypatch, grupo):
    a, b, convite = grupo
    viewer = _troca(EXPORTADAS, ("familia_permissoes", "familia_leituras_dos_meus_dados"),
                    "member_user_id", "viewer_user_id")
    erros = _vazamentos(monkeypatch, viewer, a, b, convite)
    assert any(e.startswith("A com permissões/leituras") for e in erros), erros
    assert any(e.startswith("B permissões") for e in erros), erros
    assentos = _troca(EXPORTADAS, ("familia_assentos",), "g.owner_user_id", "p.group_id is not null or g.owner_user_id")
    assert any(e.startswith("B com grupo/assentos") for e in _vazamentos(monkeypatch, assentos, a, b, convite))


def test_membro_que_sai_e_grupo_dissolvido(grupo):
    a, b, _ = grupo
    fam.leave_family(b)
    zb, _ = _zip(b)
    assert [(p["role"], p["left_reason"], bool(p["left_at"])) for p in zb["familia_participacao"]] \
        == [("member", "left", True)]
    assert [bool(p["revoked_at"]) for p in zb["familia_permissoes"]] == [True]
    assert len(zb["familia_leituras_dos_meus_dados"]) == 1
    fam.dissolve_group(a)
    za, _ = _zip(a)
    zb, _ = _zip(b)
    assert {n: v for n, v in za.items() if n.startswith("familia_") and v} == {}
    assert {n for n, v in zb.items() if n.startswith("familia_") and v} == {"familia_leituras_dos_meus_dados"}


# PB6: as 3 mensagens como `db/family.py` (accept_invite), `core/services/family_access.py` (sync_from_owner)
# e `db/family_grants.py` (recompute_users) gravam desde o #1338/#1342. {m} = membro, {t} = titular.
_AVISOS = (
    ("membro", "family: grant do convidado {m} (titular {t}) falhou causa=RuntimeError sqlstate=None"),
    ("titular", "family: grant do membro {m} (titular {t}) falhou causa=OperationalError sqlstate=40P01"),
    ("titular", "family: recompute do membro {m} (titular {t}) falhou causa=RuntimeError sqlstate=None"),
)


def test_aviso_de_falha_da_familia_sai_sem_o_uid_de_terceiro(monkeypatch):
    asyncio.run(ensure_admin_tables())  # `system_event_logs` só nasce aqui
    t, m = _u(), _u()
    for dono, msg in _AVISOS:
        _sql("insert into system_event_logs (level, event_type, message, source, user_id, details)"
             " values ('warning', 'logger.warning', %s, 'db.family', %s, '{\"logger\": \"db.family\"}')",
             (msg.format(m=m, t=t), m if dono == "membro" else t))
    dt, tt = _zip(t)
    dm, tm = _zip(m)
    assert str(m) not in tt and str(t) not in tm  # nem no dados.json nem em csv/
    assert [e["message"] for e in dm["eventos_sistema"]] \
        == [_AVISOS[0][1].format(m=m, t="(removido)")]
    assert sorted(e["message"] for e in dt["eventos_sistema"]) \
        == sorted(msg.format(m="(removido)", t=t) for _, msg in _AVISOS[1:])
    # negativo: sem a troca, o uid de terceiro sai
    monkeypatch.setattr(privacy_export, "_sem_uid_de_terceiro", lambda mensagem, user_id: mensagem)
    assert str(m) in _zip(t)[1] and str(t) in _zip(m)[1]
