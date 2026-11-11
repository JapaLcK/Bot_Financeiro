"""PL-06 B6, D1 depois do Tester r2: a margem de 1 hora não se estende e não se esconde.

  • `least` no passo 5: escritas com validade "há pouco" a cada <1 h não encadeiam a margem (R1 do Tester);
  • gatilho também no INSERT: linha que nasce com adesão passa pela regra;
  • status sozinho também é escrita do direito (`canceled` tira a carência, passos 4 e 5);
  • adesão por SQL direto com o direito vencido há menos de 1 hora não pega (o passo 2 não tem margem);
  • a tela do cortado com adesão (`/settings`) admite a margem, com o texto do dono (§11, 2 (a)).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import db
import db_support
from _insights_adesao_apoio import ambiente, cliente, eventos, flags, sql  # noqa: F401 — `ambiente` é fixture
from conftest import usuario_pagante
from core.services.insights_notificacao import AVISO_SEM_PLANO, MARGEM_DIREITO
from db.reports import insight_aviso_aderiu, insight_email_permitido
from test_insights_adesao_direito import _assinante, _relogio_passou

pytestmark = pytest.mark.usefixtures("ambiente")
RAIZ = Path(__file__).resolve().parent.parent


def _agora():
    return datetime.now(timezone.utc)


def _escreve_validade(uid, quando):
    sql("update auth_accounts set plan_expires_at = %s where user_id = %s", (quando, uid))
    db_support.invalidate_auth_user_cache(uid)


@pytest.mark.parametrize("passos", [1, 2, 4])
def test_margem_nao_se_encadeia_com_escritas_de_validade_no_passado(user_id, passos):
    """Perdeu o direito há 50 min; a cada 50 min uma escrita grava validade "há 5 min"; no fim, o pagamento."""
    _assinante(user_id)
    _relogio_passou(user_id, expira_ha=timedelta(minutes=50), coberto_ate=_agora() - timedelta(minutes=50))
    for i in range(passos):
        _escreve_validade(user_id, _agora() - timedelta(minutes=5))
        if not insight_aviso_aderiu(user_id):
            break                                   # a lacuna somada passou de 1 h: retirou no caminho
        assert flags(user_id)["coberto"] <= _agora() - timedelta(minutes=49), "a marca avançou para o passado"
        m = flags(user_id)["coberto"]
        _relogio_passou(user_id, expira_ha=timedelta(minutes=55), coberto_ate=m - timedelta(minutes=15 if i == passos - 1 else 50))
    _escreve_validade(user_id, _agora() + timedelta(days=30))
    assert not insight_aviso_aderiu(user_id), "lacuna de mais de 1 h e a adesão ficou"


def test_positivo_validade_no_passado_dentro_da_margem_sozinha_fica(user_id):
    _assinante(user_id)
    _relogio_passou(user_id, expira_ha=timedelta(minutes=20), coberto_ate=_agora() - timedelta(minutes=20))
    _escreve_validade(user_id, _agora() - timedelta(minutes=5))
    _escreve_validade(user_id, _agora() + timedelta(days=30))
    assert insight_aviso_aderiu(user_id) and flags(user_id)["coberto"] > _agora() + timedelta(days=29)


def _nasce(plano, validade, aderida=True):
    uid = int(uuid.uuid4().int % 900_000_000) + 1
    db.ensure_user(uid)
    sql("insert into auth_accounts (user_id, email, password_hash, plan, plan_expires_at, insight_aviso_aderiu_em)"
        " values (%s, %s, 'x', %s, %s, %s)",
        (uid, f"nasce-{uuid.uuid4().hex[:8]}@test.local", plano, validade, _agora() if aderida else None))
    return uid


def test_insert_com_adesao_sem_cobertura_nao_adere_nem_vira_vitalicio():
    uid = _nasce("pro", _agora() - timedelta(days=3))
    assert flags(uid)["aderiu"] is None and flags(uid)["coberto"] is None
    _escreve_validade(uid, _agora() + timedelta(days=30))
    assert not insight_aviso_aderiu(uid), "a linha nasceu sem direito: pagar depois não cria adesão"


def test_insert_com_adesao_e_plano_finito_ganha_a_marca():
    fim = _agora() + timedelta(days=30)
    uid = _nasce("pro", fim)
    assert insight_aviso_aderiu(uid) and abs((flags(uid)["coberto"] - fim).total_seconds()) < 1


def test_insert_vitalicio_e_sem_adesao():
    assert flags(_nasce("pro", None))["coberto"] is None and insight_aviso_aderiu(_nasce("pro", None))
    assert flags(_nasce("pro", _agora() + timedelta(days=3), aderida=False))["coberto"] is None


def _em_carencia(uid):
    sql("update auth_accounts set plan = 'free', plan_expires_at = null, last_payment_status = 'past_due',"
        " past_due_since = now() - interval '2 days', plan_selected_at = now() where user_id = %s", (uid,))
    db_support.invalidate_auth_user_cache(uid)


def test_escrita_so_de_status_canceled_tira_a_carencia_e_a_margem_conta_dali(user_id):
    """N04 (status fora da lista do gatilho) e N06 (passo 4 com qualquer status) ficam vermelhas aqui.

    `canceled` sozinho tira a carência. Pelo §13 (b) é perda natural: a marca vira o instante da perda (não
    o fim da carência que ainda faltava), o portão fecha e, passada 1 hora, a escrita seguinte retira."""
    _assinante(user_id)
    _em_carencia(user_id)
    assert insight_aviso_aderiu(user_id) and flags(user_id)["coberto"] > _agora() + timedelta(days=4)
    sql("update auth_accounts set last_payment_status = 'canceled' where user_id = %s", (user_id,))
    assert flags(user_id)["coberto"] <= _agora(), "a carência que acabou continuou valendo"
    assert insight_aviso_aderiu(user_id) and not insight_email_permitido(user_id)
    _relogio_passou(user_id, coberto_ate=_agora() - timedelta(minutes=61))
    sql("update auth_accounts set plan = 'pro', plan_expires_at = now() + interval '30 days' where user_id = %s", (user_id,))
    assert not insight_aviso_aderiu(user_id) and eventos(user_id)[-1]["details"] == {"motivo": "direito"}


def test_positivo_escrita_so_de_status_vivo_em_carencia_fica(user_id):
    _assinante(user_id)
    _em_carencia(user_id)
    sql("update auth_accounts set last_payment_status = 'unpaid' where user_id = %s", (user_id,))
    assert insight_aviso_aderiu(user_id)


def test_aderir_por_sql_com_direito_vencido_dentro_da_margem_nao_pega(user_id):
    """N02: o passo 2 não tem margem. Vencido há 10 min, aderir direto no banco não grava."""
    _assinante(user_id)
    sql("update auth_accounts set insight_aviso_aderiu_em = null where user_id = %s", (user_id,))
    _relogio_passou(user_id, expira_ha=timedelta(minutes=10))
    sql("update auth_accounts set insight_aviso_aderiu_em = now() where user_id = %s", (user_id,))
    assert not insight_aviso_aderiu(user_id)


@pytest.mark.parametrize("minutos,continua", [(20, True), (75, False)])
def test_cortado_com_adesao_a_tela_diz_a_margem_e_ela_vale(user_id, minutos, continua):
    """R19 do Tester: cortado há `minutos`, a tela mostra "Desligar agora" e o texto da margem; o pagamento
    que chega depois respeita exatamente o que o texto diz."""
    _assinante(user_id)
    _relogio_passou(user_id, expira_ha=timedelta(minutes=minutos), coberto_ate=_agora() - timedelta(minutes=minutos))
    r = cliente(user_id).get(f"/settings/{user_id}/notifications")
    assert r.status_code == 402
    assert (r.json()["detail"].get("insight_alert_email_enabled") is True) is continua, "a tela só mostra adesão viva"
    _escreve_validade(user_id, _agora() + timedelta(days=30))
    assert insight_aviso_aderiu(user_id) is continua
    assert insight_email_permitido(user_id) is continua


def test_texto_do_cortado_e_o_da_margem_e_o_html_copia_o_do_servidor():
    assert AVISO_SEM_PLANO == ("Se o pagamento restabelecer seu plano em até 1 hora após a perda do acesso, "
                               "os avisos continuam; depois disso, será preciso ligar de novo.")
    assert MARGEM_DIREITO == timedelta(hours=1)
    html = (RAIZ / "frontend" / "settings.html").read_text(encoding="utf-8")
    assert AVISO_SEM_PLANO in html, "a cópia do settings.html divergiu de AVISO_SEM_PLANO"
    assert "não voltam sozinhos" not in html


def test_flag_do_402_so_no_corte_e_nao_na_escolha_de_plano():
    """N15: quem nunca escolheu plano não vê o botão de desligar, mesmo com adesão gravada."""
    uid = usuario_pagante("pro")
    from _insights_adesao_apoio import aderir
    aderir(uid)
    c = cliente(uid)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("set local session_replication_role = replica")
        cur.execute("update auth_accounts set plan = 'free', plan_expires_at = null, plan_selected_at = null"
                    " where user_id = %s", (uid,))
        conn.commit()
    db_support.invalidate_auth_user_cache(uid)
    r = c.get(f"/settings/{uid}/notifications")
    assert r.status_code == 402 and r.json()["detail"] == {"error": "plan_selection_required"}
