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
from core.services import billing_access, family_access
from db import family as fam
from db import family_consents as fc
from db import family_grants
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
    """A titular (pro_max) com um assento pago e um convite pendente; B e C membros com `aggregates` liberado.
    A lê SÓ o C: com dois membros, uma SQL que filtre pelo grupo e não pela pessoa põe o dado de C no ZIP de B."""
    a, b, c = _u("pro_max"), _u(), _u()
    gid = fam.create_group(a)
    _sql("insert into family_seat_purchases (group_id, source, external_ref, amount_cents, status)"
         " values (%s, 'pix', 'vaga1', 1000, 'paid')", (gid,))
    for membro in (b, c):
        fam.accept_invite(fam.create_invite(a), membro)
        fc.grant_consent(membro, "aggregates")
    fc.log_read(gid, a, c, "aggregates", "agregados:2026-09")
    return a, b, c, fam.create_invite(a)


def _vazamentos(monkeypatch, registro, a: int, b: int, c: int, convite: str) -> list[str]:
    monkeypatch.setattr(privacy_export, "EXPORTADAS", registro)
    (za, ta), (zb, tb), (zc, tc) = _zip(a), _zip(b), _zip(c)
    erros = []
    for nome, z, leituras in (("B", zb, []), ("C", zc, [("titular do grupo", "agregados:2026-09")])):
        if [p["role"] for p in z["familia_participacao"]] != ["member"]:
            erros.append(f"{nome} participação: {z['familia_participacao']}")
        if [p["scope"] for p in z["familia_permissoes"]] != ["aggregates"]:
            erros.append(f"{nome} permissões: {z['familia_permissoes']}")
        if [(r["quem_leu"], r["recurso"]) for r in z["familia_leituras_dos_meus_dados"]] != leituras:
            erros.append(f"{nome} leituras: {z['familia_leituras_dos_meus_dados']}")
        if z["familia_grupo"] or z["familia_assentos"]:
            erros.append(f"{nome} com grupo/assentos do titular: {z['familia_grupo']} {z['familia_assentos']}")
    if [p["role"] for p in za["familia_participacao"]] != ["owner"]:  # PB5 (a)
        erros.append(f"A participação: {za['familia_participacao']}")
    if len(za["familia_grupo"]) != 1 or [s["external_ref"] for s in za["familia_assentos"]] != ["vaga1"]:
        erros.append(f"A grupo/assentos: {za['familia_grupo']} {za['familia_assentos']}")
    if za["familia_permissoes"] or za["familia_leituras_dos_meus_dados"]:
        erros.append(f"A com permissões/leituras dos membros: {za['familia_permissoes']} "
                     f"{za['familia_leituras_dos_meus_dados']}")
    textos = {"A": (a, ta), "B": (b, tb), "C": (c, tc)}
    erros += [f"uid de {x} no ZIP de {y}" for x, (uid, _) in textos.items()
              for y, (_, texto) in textos.items() if x != y and str(uid) in texto]
    if any(segredo in texto for segredo in (convite, fam.hash_token(convite)) for _, texto in textos.values()):
        erros.append("token do convite no ZIP")
    return erros


def _troca(registro, nomes, de, para):
    return {**registro, **{n: (registro[n][0], registro[n][1].replace(de, para)) for n in nomes}}


def test_cada_papel_recebe_so_o_seu(monkeypatch, grupo):
    assert _vazamentos(monkeypatch, EXPORTADAS, *grupo) == []  # positivo


_PAPEIS = ("familia_permissoes", "familia_leituras_dos_meus_dados")


def test_guarda_acusa_coluna_de_pessoa_trocada(monkeypatch, grupo):
    erros = _vazamentos(monkeypatch, _troca(EXPORTADAS, _PAPEIS, "member_user_id", "viewer_user_id"), *grupo)
    assert any(e.startswith("A com permissões/leituras") for e in erros), erros
    assert any(e.startswith("B permissões") for e in erros), erros
    # D2: filtrar pelo grupo da pessoa, e não pela pessoa, traz o dado do outro membro
    por_grupo = _troca(EXPORTADAS, _PAPEIS, "member_user_id = %(uid)s",
                       "group_id in (select group_id from family_members where user_id = %(uid)s)")
    erros = _vazamentos(monkeypatch, por_grupo, *grupo)
    assert "B leituras: " in " ".join(erros) and any(e.startswith("B permissões") for e in erros), erros
    assentos = _troca(EXPORTADAS, ("familia_assentos",), "g.owner_user_id", "p.group_id is not null or g.owner_user_id")
    assert any(e.startswith("B com grupo/assentos") for e in _vazamentos(monkeypatch, assentos, *grupo))


def test_membro_que_sai_e_grupo_dissolvido(grupo):
    a, b, c, _ = grupo
    fam.leave_family(c)
    zc, _ = _zip(c)
    assert [(p["role"], p["left_reason"], bool(p["left_at"])) for p in zc["familia_participacao"]] \
        == [("member", "left", True)]
    assert [bool(p["revoked_at"]) for p in zc["familia_permissoes"]] == [True]
    assert len(zc["familia_leituras_dos_meus_dados"]) == 1
    fam.dissolve_group(a)
    assert {n for z in (_zip(a)[0], _zip(b)[0]) for n, v in z.items() if n.startswith("familia_") and v} == set()
    assert {n for n, v in _zip(c)[0].items() if n.startswith("familia_") and v} == {"familia_leituras_dos_meus_dados"}


# PB6, linhas antigas: as 3 mensagens no formato e no `source` com que `db/family.py` (accept_invite),
# `core/services/family_access.py` (sync_from_owner) e `db/family_grants.py` (recompute_users) gravam desde o
# #1338/#1342. {m} = membro, {t} = titular. O formato de hoje vem dos gravadores reais, no teste seguinte.
_AVISOS = (
    ("membro", "db.family", "family: grant do convidado {m} (titular {t}) falhou causa=RuntimeError sqlstate=None"),
    ("titular", "core.services.family_access",
     "family: grant do membro {m} (titular {t}) falhou causa=OperationalError sqlstate=40P01"),
    ("titular", "db.family_grants", "family: recompute do membro {m} (titular {t}) falhou causa=RuntimeError sqlstate=None"),
)


def _avisos_no_zip(t: int, m: int) -> tuple[list[str], list[str]]:
    """(mensagens `family:` do ZIP do titular, do membro), conferindo que nenhum ZIP traz o uid do outro."""
    (dt, tt), (dm, tm) = _zip(t), _zip(m)
    assert str(m) not in tt and str(t) not in tm  # nem no dados.json nem em csv/
    return tuple(sorted(e["message"] for e in d["eventos_sistema"] if e["message"].startswith("family:"))
                 for d in (dt, dm))


def test_aviso_antigo_de_falha_da_familia_sai_sem_o_uid_de_terceiro(monkeypatch):
    asyncio.run(ensure_admin_tables())  # `system_event_logs` só nasce aqui
    t, m = _u(), _u()
    for dono, source, msg in _AVISOS:
        _sql("insert into system_event_logs (level, event_type, message, source, user_id, details)"
             " values ('warning', 'logger.warning', %s, %s, %s, %s)",
             (msg.format(m=m, t=t), source, m if dono == "membro" else t, json.dumps({"logger": source})))
    assert _avisos_no_zip(t, m) == (sorted(msg.format(m="(removido)", t=t) for _, _, msg in _AVISOS[1:]),
                                    [_AVISOS[0][2].format(m=m, t="(removido)")])
    # negativo: sem a troca, o uid de terceiro sai
    monkeypatch.setattr(privacy_export, "_sem_uid_de_terceiro", lambda mensagem, user_id: mensagem)
    assert str(m) in _zip(t)[1] and str(t) in _zip(m)[1]


def test_aviso_dos_gravadores_reais_sai_sem_o_uid_de_terceiro(monkeypatch):
    """D4: as 3 falhas forçadas nas funções reais, gravadas pelo espelho do logging (o `_DashboardHandler` da
    suíte, `tests/conftest.py`): a mensagem e o `source` vêm do código, não deste teste."""
    asyncio.run(ensure_admin_tables())
    a, b = _u("pro_max"), _u()
    fam.create_group(a)

    def falha(*_a, **_k):
        raise RuntimeError("falha forçada")
    monkeypatch.setattr(family_access, "grant_member", falha)
    fam.accept_invite(fam.create_invite(a), b)  # db/family.py: "grant do convidado"
    family_access.sync_from_owner(a)  # "grant do membro"
    monkeypatch.setattr(billing_access, "recompute_entitlement", falha)
    family_grants.recompute_users([b], a)  # "recompute do membro"
    monkeypatch.undo()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select source, user_id from system_event_logs where user_id = any(%s) and message like 'family:%%'"
                    " order by id", ([a, b],))
        assert [(r["source"], r["user_id"]) for r in cur.fetchall()] == [
            ("db.family", b), ("core.services.family_access", a), ("db.family_grants", a)]
    titular, membro = _avisos_no_zip(a, b)
    assert len(titular) == 2 and all("(removido) (titular " in e for e in titular), titular
    assert len(membro) == 1 and "(titular (removido)) falhou" in membro[0], membro
