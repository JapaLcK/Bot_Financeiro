"""A foto gravada: uma por dia, concorrência, reset no meio, chave, hora, acesso e LGPD.

A conta em si está em `tests/test_patrimonio_foto.py`.
"""
from __future__ import annotations

import io
import threading
import time
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

import db
import db.patrimonio as P
from conftest import usuario_pagante
from core.services import patrimonio_foto as job
from db.privacy import build_user_export_zip, delete_user_data, reset_user_data
from db.users import _hash_password
from tests._patrimonio_helpers import caixinha, q

DIA = date(2026, 9, 29)
SENHA = "senha-forte-123"


def _fotos(uid):
    return q("select dia, total, motivos from patrimonio_fotos where user_id=%s order by dia",
             (uid,), fetch=True)


@pytest.fixture
def uid():
    u = usuario_pagante()
    db.set_balance(u, Decimal("100"))
    return u


# ── uma por dia ──────────────────────────────────────────────────────────────

def test_grava_uma_vez_e_a_segunda_nao_regrava(uid):
    assert P.gravar_foto(uid, DIA) is True
    db.set_balance(uid, Decimal("5"))
    assert P.gravar_foto(uid, DIA) is False
    assert [(f["dia"], f["total"]) for f in _fotos(uid)] == [(DIA, Decimal("100"))]
    assert _fotos(uid)[0]["motivos"] == ["carteira_nao_confirmada"]
    assert uid not in P.candidatos(DIA) and uid in P.candidatos(date(2026, 9, 30))


def test_duas_instancias_ao_mesmo_tempo_gravam_uma(uid):
    barreira, res = threading.Barrier(2), []

    def grava():
        barreira.wait()
        res.append(P.gravar_foto(uid, DIA))

    ts = [threading.Thread(target=grava) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(res) == [False, True]
    assert len(_fotos(uid)) == 1


def test_usuario_sem_accounts_nao_grava():
    u = usuario_pagante()
    q("delete from accounts where user_id=%s", (u,))
    assert P.gravar_foto(u, DIA) is False and _fotos(u) == []


# ── "Recomeçar do zero" no meio da foto ─────────────────────────────────────

def _reset_no_meio(uid, monkeypatch):
    q("update auth_accounts set password_hash=%s where user_id=%s", (_hash_password(SENHA), uid))
    caixinha(uid, "Viagem", "900")
    calculou, prossiga = threading.Event(), threading.Event()
    real = P.calcular

    def calcular_e_esperar(cur, user_id):
        f = real(cur, user_id)
        calculou.set()
        prossiga.wait(10)
        return f

    monkeypatch.setattr(P, "calcular", calcular_e_esperar)
    t_foto = threading.Thread(target=P.gravar_foto, args=(uid, DIA))
    t_foto.start()
    assert calculou.wait(10)
    t_reset = threading.Thread(target=reset_user_data, args=(uid, SENHA))
    t_reset.start()
    fim = time.monotonic() + 10  # o reset termina (sem trava) ou fica esperando a foto
    while t_reset.is_alive() and time.monotonic() < fim and not q(
            "select 1 from pg_stat_activity where wait_event_type='Lock' and datname=current_database()"):
        time.sleep(0.05)
    prossiga.set()
    t_foto.join(10)
    t_reset.join(10)


def test_reset_no_meio_nao_deixa_foto_de_antes_do_reset(uid, monkeypatch):
    _reset_no_meio(uid, monkeypatch)
    assert _fotos(uid) == []
    assert q("select count(*) n from pockets where user_id=%s", (uid,))["n"] == 0


def test_reset_que_commita_com_a_foto_esperando_nao_deixa_foto(uid, monkeypatch):
    """Ordem inversa: o reset já segura `accounts` quando a foto abre o snapshot e pede
    o `for share`; o reset commita e a foto leva SerializationFailure → False."""
    import db.privacy as privacy
    q("update auth_accounts set password_hash=%s where user_id=%s", (_hash_password(SENHA), uid))
    caixinha(uid, "Viagem", "900")
    real, res = privacy._table_exists, []
    t_foto = threading.Thread(target=lambda: res.append(P.gravar_foto(uid, DIA)))

    def foto_esperando_o_reset(cur, table):  # 1º `_delete`, depois do `update accounts`
        if not t_foto.is_alive() and not res:
            t_foto.start()
            fim = time.monotonic() + 10
            while time.monotonic() < fim and not q(
                    """select 1 from pg_stat_activity where wait_event_type='Lock'
                        and datname=current_database() and query like %s""", ("%for share%",)):
                time.sleep(0.02)
        return real(cur, table)

    monkeypatch.setattr(privacy, "_table_exists", foto_esperando_o_reset)
    reset_user_data(uid, SENHA)
    t_foto.join(10)
    assert res == [False] and _fotos(uid) == []


def test_reset_depois_da_foto_regrava_do_zero(uid, monkeypatch):
    _reset_no_meio(uid, monkeypatch)
    monkeypatch.undo()
    assert P.gravar_foto(uid, DIA) is True
    assert _fotos(uid)[0]["total"] == 0


# ── o job: chave, hora, acesso ───────────────────────────────────────────────

def _so(monkeypatch, *uids):
    real = P.candidatos
    monkeypatch.setattr(P, "candidatos", lambda dia: [u for u in real(dia) if u in uids])


@pytest.fixture
def ligado(monkeypatch):
    monkeypatch.setenv("PATRIMONIO_FOTO_ENABLED", "1")
    monkeypatch.setenv("REPORT_TIMEZONE", "America/Sao_Paulo")


def _utc(dia, hora, minuto=0):
    return datetime(2026, 9, dia, hora, minuto, tzinfo=timezone.utc)


def test_chave_desligada_nao_consulta_nem_grava(uid, monkeypatch):
    monkeypatch.delenv("PATRIMONIO_FOTO_ENABLED", raising=False)

    def proibido(*a, **k):
        raise AssertionError("desligado não consulta")

    monkeypatch.setattr(P, "candidatos", proibido)
    monkeypatch.setattr(P, "calcular", proibido)
    for valor in (None, "0", "false", ""):
        if valor is not None:
            monkeypatch.setenv("PATRIMONIO_FOTO_ENABLED", valor)
        assert job.gravar_fotos_do_dia(_utc(30, 23)) is None
    assert _fotos(uid) == []


def test_chave_ligada_grava(uid, monkeypatch, ligado):
    _so(monkeypatch, uid)
    assert job.gravar_fotos_do_dia(_utc(30, 23)) == {"dia": "2026-09-30", "gravadas": 1, "falhas": 0}
    assert [f["dia"] for f in _fotos(uid)] == [date(2026, 9, 30)]


def test_depois_da_meia_noite_utc_grava_o_dia_de_sao_paulo(uid, monkeypatch, ligado):
    _so(monkeypatch, uid)
    job.gravar_fotos_do_dia(_utc(30, 0, 30))  # 21:30 de 29/09 em SP
    assert [f["dia"] for f in _fotos(uid)] == [DIA]


def test_antes_das_18h_de_sao_paulo_nao_grava(uid, monkeypatch, ligado):
    _so(monkeypatch, uid)
    assert job.gravar_fotos_do_dia(_utc(30, 20, 59)) is None  # 17:59 em SP, 20:59 em UTC
    assert _fotos(uid) == []
    job.gravar_fotos_do_dia(_utc(30, 21))  # 18:00 em SP
    assert [f["dia"] for f in _fotos(uid)] == [date(2026, 9, 30)]


def test_so_quem_tem_acesso_ganha_foto(uid, monkeypatch, ligado, user_id):
    _so(monkeypatch, uid, user_id)
    job.gravar_fotos_do_dia(_utc(30, 23))
    assert len(_fotos(uid)) == 1 and _fotos(user_id) == []


def test_falha_de_um_usuario_nao_para_os_outros(monkeypatch, ligado):
    a, b = usuario_pagante(), usuario_pagante()
    _so(monkeypatch, a, b)
    real = P.calcular

    def quebra_o_a(cur, user_id):
        if user_id == a:
            raise RuntimeError("x")
        return real(cur, user_id)

    monkeypatch.setattr(P, "calcular", quebra_o_a)
    r = job.gravar_fotos_do_dia(_utc(30, 23))
    assert (r["gravadas"], r["falhas"]) == (1, 1)
    assert _fotos(a) == [] and len(_fotos(b)) == 1


# ── LGPD ─────────────────────────────────────────────────────────────────────

def test_exportacao_leva_o_historico(uid):
    P.gravar_foto(uid, DIA)
    z = zipfile.ZipFile(io.BytesIO(build_user_export_zip(uid)))
    assert "carteira_nao_confirmada" in z.read("csv/historico_patrimonio.csv").decode()


def test_reset_e_exclusao_apagam_so_do_dono(uid):
    outro, vizinho = usuario_pagante(), usuario_pagante()
    for u in (uid, outro, vizinho):
        P.gravar_foto(u, DIA)
    q("update auth_accounts set password_hash=%s where user_id=%s", (_hash_password(SENHA), uid))
    reset_user_data(uid, SENHA)
    delete_user_data(outro)
    assert _fotos(uid) == [] and _fotos(outro) == [] and len(_fotos(vizinho)) == 1


def test_juncao_nao_recusa_e_a_foto_da_origem_some(user_id):
    origem, destino = user_id, usuario_pagante()  # origem sem plano: a junção não é presa
    for u in (origem, destino):
        P.gravar_foto(u, DIA)
    db.merge_users(origem, destino)
    assert _fotos(origem) == [] and len(_fotos(destino)) == 1
