"""PR-A2c (ADR 0006): QUEM é lembrado e QUANDO. G1 janela dos 7 dias e horário, G2 só declaração viva de fluxo,
G10 migração. Postgres real, `notify_declaracoes_whatsapp_once` real, `send_text` real (só o `requests.post` trocado).

Controles NEGATIVOS (rodados numa CÓPIA; `core/services/declaracao_lembrete.py` e `db/bank_lembrete.py`):
- G1. `LEMBRETE_DIAS = 7` virar 6 -> VERMELHO `test_borda_dos_7_dias[6-0]`; virar 8 -> VERMELHO `[7-1]`; calcular o
  `limite` pelo dia UTC (`datetime.now(utc)` ou `.astimezone(utc)`) -> VERMELHO
  `test_o_dia_do_gasto_e_o_do_app_e_nao_o_utc[23h30]` (não derrubou sozinho: a mutação do Tester sobreviveu) e
  `test_21h30_local_com_o_dia_utc_ja_virado_nao_adianta_o_limite`; `<` virar `<=` no `_VIVA` -> VERMELHO
  `test_borda_exata_meia_noite_de_T_menos_6_nao_envia`; tirar o `.astimezone(_tz())` do começo -> VERMELHO
  `test_now_em_utc_vale_o_fuso_do_app`; tirar o piso (`bill_reminder_hour() <=`) -> VERMELHO
  `test_horario_de_envio[8:59]`; tirar o teto (`< _HORA_LIMITE`) -> VERMELHO `[22:00]`; ler o relógio real no lugar de
  `now` -> VERMELHO `test_o_relogio_e_o_injetado_2031`. Positivos: `[7-1]`, `[8-1]`, `[30-1]`, `[9:00]`, `[21:59]`.
- G2. tirar `{DECLARADA_SQL}` do `_VIVA` (o aporte entra) -> VERMELHO `test_aporte_nunca_entra`; tirar
  `dec.matched_transaction_id is null` do `_VIVA` -> VERMELHO `test_casada_nao_e_lembrada`; tirar
  `dec.lembrete_em is null` -> VERMELHO `test_desfeita_sem_lembrete_previo_e_lembrada_e_so_uma_vez`. Positivos:
  `test_receita_entra` e a primeira metade do teste da desfeita (lembra uma vez).
- G10. `add column if not exists` virar `add column` -> VERMELHO `test_init_db_duas_vezes_e_idempotente`.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import pytest

import core.services.declaracao_lembrete as dl
import db
import db.bank_conferencia as conferencia
from db.bank_dinheiro import foi_em_dinheiro
from tests._lembrete_declaracao import (  # noqa: F401 (_event_logs é fixture autouse)
    FONE_A, _event_logs, agora, agora_2031, armar, corpos, declara_ha, dono, lembretes, para, reservadas)
from tests._patrimonio_helpers import q
from tests.test_bank_movements import _bank, _deposit
from tests.test_declaracao_fluxo_reconcilia import sincroniza, tx
from utils_date import _tz


def _roda(ag, posts):
    return dl.notify_declaracoes_whatsapp_once(now=ag), len(para(posts, FONE_A))


@pytest.mark.parametrize("dias, envia", [(6, 0), (7, 1), (8, 1), (30, 1)], ids=lambda v: str(v))
def test_borda_dos_7_dias(user_id, monkeypatch, dias, envia):
    ag = agora()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    declara_ha(user_id, ag, dias)

    assert _roda(ag, posts) == (envia, envia)
    assert reservadas(user_id) == envia


@pytest.mark.parametrize("hora, minuto", [(23, 30), (0, 30)], ids=["23h30", "00h30"])
def test_o_dia_do_gasto_e_o_do_app_e_nao_o_utc(user_id, monkeypatch, hora, minuto):
    """Às 23h30 locais do dia T-7 já são 02h30 UTC de T-6: pelo dia UTC seriam 6 dias e não sairia."""
    ag = agora()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    declara_ha(user_id, ag, quando=(ag - timedelta(days=7)).replace(hour=hora, minute=minuto))

    assert _roda(ag, posts) == (1, 1)


def test_o_dia_6_as_23h59_ainda_nao(user_id, monkeypatch):
    ag = agora()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    declara_ha(user_id, ag, quando=(ag - timedelta(days=6)).replace(hour=23, minute=59))

    assert _roda(ag, posts) == (0, 0) and reservadas(user_id) == 0


def test_borda_exata_meia_noite_de_T_menos_6_nao_envia(user_id, monkeypatch):
    """`declared_at < limite`: exatamente 00:00 local de T-6 (N = 6) não sai; um instante antes (N = 7) sai."""
    ag = agora()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    exato = datetime.combine(ag.date() - timedelta(days=6), time.min, tzinfo=_tz())
    declara_ha(user_id, ag, quando=exato)
    assert _roda(ag, posts) == (0, 0) and reservadas(user_id) == 0

    declara_ha(user_id, ag, quando=exato - timedelta(microseconds=1), alvo="sete")
    assert _roda(ag, posts) == (1, 1) and reservadas(user_id) == 1


def test_21h30_local_com_o_dia_utc_ja_virado_nao_adianta_o_limite(user_id, monkeypatch):
    """21h30 em São Paulo já é o dia seguinte em UTC: pelo dia UTC o gasto de T-6 viraria "7 dias" e sairia."""
    monkeypatch.setenv("REPORT_TIMEZONE", "America/Sao_Paulo")  # a premissa é um fuso atrás do UTC
    ag = agora(21).replace(minute=30)
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    declara_ha(user_id, ag, quando=datetime.combine(ag.date() - timedelta(days=6), time(12, 0), tzinfo=_tz()))

    assert _roda(ag, posts) == (0, 0) and reservadas(user_id) == 0


def test_now_em_utc_vale_o_fuso_do_app(user_id, monkeypatch):
    """B3: 00:30 UTC de 11/03 são 21:30 de 10/03 no app. Sem converter, `now.hour` = 0 (fora da janela) e nada sai."""
    monkeypatch.setenv("REPORT_TIMEZONE", "America/Sao_Paulo")
    posts = armar(monkeypatch)
    ag = datetime(2031, 3, 11, 0, 30, tzinfo=timezone.utc)
    dono(user_id, FONE_A, ag)
    declara_ha(user_id, ag, quando=datetime(2031, 3, 3, 12, 0, tzinfo=_tz()))  # T-7 no dia do app (10/03)
    declara_ha(user_id, ag, quando=datetime(2031, 3, 4, 12, 0, tzinfo=_tz()), alvo="seis")  # T-6: ainda não

    assert _roda(ag, posts) == (1, 1) and reservadas(user_id) == 1
    assert "seis" not in corpos(posts, FONE_A)[0]


@pytest.mark.parametrize("hora, minuto, envia", [(8, 59, 0), (9, 0, 1), (21, 59, 1), (22, 0, 0)],
                         ids=["8:59", "9:00", "21:59", "22:00"])
def test_horario_de_envio(user_id, monkeypatch, hora, minuto, envia):
    """Das 9h às 21:59; fora disso nem reserva (a volta seguinte, ou a manhã, manda)."""
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, agora())
    declara_ha(user_id, agora(), 8)

    assert _roda(agora(hora).replace(minute=minuto), posts) == (envia, envia)
    assert reservadas(user_id) == envia


def test_a_volta_das_9h_manda_o_que_a_das_8h59_deixou(user_id, monkeypatch):
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, agora())
    declara_ha(user_id, agora(), 8)

    assert _roda(agora(8).replace(minute=59), posts) == (0, 0)
    assert _roda(agora(9), posts) == (1, 1)


@pytest.mark.parametrize("valor", ["nove", "-1", "25"])
def test_hora_invalida_vale_o_padrao_9(user_id, monkeypatch, valor):
    posts = armar(monkeypatch)
    monkeypatch.setenv("WA_BILL_REMINDER_HOUR", valor)
    dono(user_id, FONE_A, agora())
    declara_ha(user_id, agora(), 8)

    assert _roda(agora(8), posts) == (0, 0)
    assert _roda(agora(9), posts) == (1, 1)


def test_o_relogio_e_o_injetado_2031(user_id, monkeypatch):
    """`agora` em 2031, longe do relógio real: a regra dos 7 dias, a janela de 23 h e a hora saem DELE.
    Com o relógio real (2026) uma declaração "de 2031" seria do futuro e nada sairia."""
    ag = agora_2031()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    sete = declara_ha(user_id, ag, 7, alvo="sete")
    seis = declara_ha(user_id, ag, 6, alvo="seis")

    assert _roda(ag, posts) == (1, 1)
    corpo, = corpos(posts, FONE_A)
    assert "gasto com sete," in corpo and "seis" not in corpo and "03/03" in corpo
    assert lembretes(user_id)[sete] is not None and lembretes(user_id)[seis] is None
    # a janela de 23 h também é a de 2031: quem falou há 25 h (relativo a `ag`) não recebe
    q("update auth_accounts set last_activity_at=%s where user_id=%s", (ag - timedelta(hours=25), user_id))
    q("update bank_movement_declarations set lembrete_em=null where user_id=%s", (user_id,))
    assert _roda(ag, posts) == (0, 1)  # o 1 é o envio do passo anterior


# --- G2: só declaração viva de fluxo ---------------------------------------------------------------------------

def _casada(user_id, ag):
    cid, _ = _bank(user_id)
    lid = declara_ha(user_id, ag, 8)
    sincroniza(cid, user_id, tx(days=-8))
    par = q("select matched_transaction_id m from bank_movement_declarations where launch_id=%s", (lid,))["m"]
    assert par
    return cid, lid, par


def test_casada_nao_e_lembrada(user_id, monkeypatch):
    ag = agora()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    _casada(user_id, ag)

    assert _roda(ag, posts) == (0, 0) and reservadas(user_id) == 0


def test_desfeita_sem_lembrete_previo_e_lembrada_e_so_uma_vez(user_id, monkeypatch):
    """#9 e #10: casada e desfeita sem lembrete prévio = viva há 8 dias (lembra); depois de lembrada, casar e
    desfazer de novo NÃO reabre (`lembrete_em` sobrevive ao desfazer)."""
    ag = agora()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    _, lid, par = _casada(user_id, ag)
    conferencia.undo_bank_movement(user_id, lid, par)

    assert _roda(ag, posts) == (1, 1)
    conferencia.confirm_bank_movement(user_id, lid, par)
    conferencia.undo_bank_movement(user_id, lid, par)
    assert lembretes(user_id)[lid] is not None
    assert _roda(ag, posts) == (0, 1)


def test_receita_entra(user_id, monkeypatch):
    ag = agora()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    declara_ha(user_id, ag, 9, tipo="receita", valor=300, alvo="freela")

    assert _roda(ag, posts) == (1, 1)
    assert "A receita de freela, de R$ 300,00" in corpos(posts, FONE_A)[0]


def test_aporte_nunca_entra(user_id, monkeypatch):
    """Aporte/caixinha com declaração de conta explícita há 30 dias: o lembrete é de despesa/receita."""
    ag = agora()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    _, source = _bank(user_id)
    aporte = _deposit(user_id, source)
    q("update bank_movement_declarations set declared_at=%s where launch_id=%s", (ag - timedelta(days=30), aporte))
    assert q("select count(*) n from bank_movement_declarations where user_id=%s", (user_id,))["n"] == 1

    assert _roda(ag, posts) == (0, 0) and reservadas(user_id) == 0


def test_convertida_em_dinheiro_e_apagada_somem_da_lista(user_id, monkeypatch):
    ag = agora()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    convertida = declara_ha(user_id, ag, 9, alvo="convertida")
    apagada = declara_ha(user_id, ag, 9, alvo="apagada")
    viva = declara_ha(user_id, ag, 9, alvo="viva")
    foi_em_dinheiro(user_id, convertida)
    q("delete from launches where id=%s", (apagada,))

    assert _roda(ag, posts) == (1, 1)
    corpo, = corpos(posts, FONE_A)
    assert "gasto com viva," in corpo and "convertida" not in corpo and "apagada" not in corpo
    assert set(lembretes(user_id)) == {viva}


def test_merge_de_contas_leva_o_lembrete_junto(user_id, monkeypatch):
    """#36: a linha muda de dono com `lembrete_em`; o destino não é lembrado de novo."""
    from conftest import _cleanup_user
    ag = agora()
    armar(monkeypatch)
    destino = user_id + 1
    try:
        db.ensure_user(destino)
        lid = declara_ha(user_id, ag, 8)  # origem sem plano pago: a que some não pode estar presa
        q("update bank_movement_declarations set lembrete_em=%s where launch_id=%s", (ag, lid))
        antes = lembretes(user_id)[lid]

        db.merge_users(user_id, destino)

        assert lembretes(destino) == {lid: antes}
        dono(destino, FONE_A, ag)
        assert dl.notify_declaracoes_whatsapp_once(now=ag) == 0
    finally:
        _cleanup_user(destino)


# --- G10: migração -----------------------------------------------------------------------------------------------

def test_init_db_duas_vezes_e_idempotente(user_id):
    lid = declara_ha(user_id, agora(), 8)
    assert lembretes(user_id) == {lid: None}  # linha existente: nullable, sem backfill
    db.init_db()
    db.init_db()
    assert lembretes(user_id) == {lid: None}
    col = q("""select is_nullable, column_default from information_schema.columns
                where table_name='bank_movement_declarations' and column_name='lembrete_em'""")
    assert col == {"is_nullable": "YES", "column_default": None}
