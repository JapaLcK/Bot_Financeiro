"""Xerife: o gatilho do alerta de gasto fora do padrão, caracterizado com banco real.

Estes casos foram escritos e rodaram VERDES contra o código anterior ao PL-04 (SQL inline em
`_xerife_detect_for_user`); são a coluna "main" da varredura. Depois do refator seguem
verdes: nenhum limiar mudou. As TRÊS mudanças de gatilho declaradas:
  1. média de referência <= 0 não alerta (antes disparava por `valor > 2,5 * 0`): `media_zero`;
  2. empate EXATO no limiar não alerta: o SQL antigo comparava em float8 e alertava por ruído
     de float; o novo é Decimal e estrito: `empate_exato_que_o_float_antigo_alertava`;
  3. `config` legada inválida recupera defaults (PL-04 B); a entrada HTTP agora é estrita.
     `test_config_crua_nao_derruba_o_bloco_de_limites`.
"""
from __future__ import annotations

import pytest

import db
from db import get_conn
from core.services.piggy_agents import run_xerife_once
from conftest import usuario_pagante

CAT = "Alimentação"


def _lanca(uid, valor, *, cat=CAT, dias=10, horas=0, tipo="despesa", interno=False, alvo=None):
    """Insere um lançamento `dias` dias + `horas` horas atrás. Devolve o id."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into launches (user_id, tipo, valor, alvo, categoria, criado_em, is_internal_movement)
                values (%s, %s, %s, %s, %s, now() - make_interval(days => %s, hours => %s), %s)
                returning id
                """,
                (uid, tipo, valor, alvo, cat, dias, horas, interno),
            )
            lid = cur.fetchone()["id"]
        conn.commit()
    return lid


def _eventos(uid):
    return db.list_agent_events(uid, 50, kind="xerife")


def _roda(uid, config=None):
    db.activate_agent(uid, "xerife", config or {})
    return run_xerife_once(user_id=uid)


@pytest.fixture
def uid():
    return usuario_pagante()


CINCO_100 = [100, 100, 100, 100, 100]


# (nome, histórico, candidato, kwargs_do_candidato, kwargs_do_histórico, config, dispara?)
CASOS = [
    ("acima_do_limiar", CINCO_100, 251, {}, {}, None, True),
    ("exatamente_2_5x_nao_dispara", CINCO_100, 250, {}, {}, None, False),
    ("um_centavo_acima_dispara", CINCO_100, 250.01, {}, {}, None, True),
    ("amostra_4_nao_dispara", CINCO_100[:4], 400, {}, {}, None, False),
    ("amostra_5_dispara", CINCO_100, 400, {}, {}, None, True),
    ("media_nao_redonda", [100, 100, 100, 100, 200], 300, {}, {}, None, False),   # 2,5 x 120
    ("media_nao_redonda_acima", [100, 100, 100, 100, 200], 300.01, {}, {}, None, True),
    ("minimo_49_99_nao_dispara", [10] * 5, 49.99, {}, {}, None, False),
    ("minimo_50_dispara", [10] * 5, 50, {}, {}, None, True),
    ("historico_91_dias_fora", CINCO_100, 400, {}, {"dias": 91}, None, False),
    ("historico_85_dias_dentro", CINCO_100, 400, {}, {"dias": 85}, None, True),
    ("historico_nas_ultimas_24h_nao_conta", CINCO_100, 400, {}, {"dias": 0, "horas": 23}, None, False),
    ("candidato_fora_das_24h", CINCO_100, 400, {"dias": 2}, {}, None, False),
    ("categoria_com_caixa_diferente", CINCO_100, 400, {"cat": "ALIMENTAÇÃO"}, {}, None, True),
    ("categoria_sem_acento_e_outra", CINCO_100, 400, {"cat": "Alimentacao"}, {}, None, False),
    ("categoria_nula_nao_alerta", CINCO_100, 400, {"cat": None}, {}, None, False),
    ("tipo_legado_saida_candidato", CINCO_100, 400, {"tipo": "saida"}, {}, None, True),
    ("tipo_legado_saida_historico", CINCO_100, 400, {}, {"tipo": "saida"}, None, True),
    ("receita_nao_alerta", CINCO_100, 400, {"tipo": "receita"}, {}, None, False),
    ("receita_nao_entra_na_media", CINCO_100, 400, {}, {"tipo": "receita"}, None, False),
    ("candidato_interno_nao_alerta", CINCO_100, 400, {"interno": True}, {}, None, False),
    ("historico_interno_nao_conta", CINCO_100, 400, {}, {"interno": True}, None, False),
    ("cfg_multiplicador", CINCO_100, 210, {}, {}, {"multiplicador": 2}, True),
    # 2.3 x 100 = 230 exato: com Decimal(2.3) (binário) o limiar cairia em 229,99999… e 230 alertaria
    ("cfg_mult_nao_binario_empate", CINCO_100, 230, {}, {}, {"multiplicador": 2.3}, False),
    ("cfg_mult_nao_binario_acima", CINCO_100, 230.01, {}, {}, {"multiplicador": 2.3}, True),
    ("cfg_minimo", [10] * 5, 40, {}, {}, {"minimo": 30}, True),
    ("media_zero", [0] * 5, 60, {}, {}, None, False),      # mudança de gatilho 1 do PL-04
    # mudança de gatilho 2: média 104,216 x 2,5 = 260,54 exato; em float8 o SQL antigo alertava (ruído)
    ("empate_exato_que_o_float_antigo_alertava", [89.44, 182.4, 137.42, 70.26, 41.56], 260.54, {}, {}, None, False),
]


@pytest.mark.parametrize("nome,hist,cand,ckw,hkw,cfg,esperado", CASOS, ids=[c[0] for c in CASOS])
def test_grade_do_gatilho(uid, nome, hist, cand, ckw, hkw, cfg, esperado):
    base = hkw.get("dias", 10)
    for i, v in enumerate(hist):
        _lanca(uid, v, **{**hkw, "dias": base + i if base else 0})
    lid = _lanca(uid, cand, **{**{"dias": 0, "horas": 1}, **ckw})

    _roda(uid, cfg)

    evs = _eventos(uid)
    assert (len(evs) == 1) is esperado, evs
    if esperado:
        p = evs[0]["payload"]
        assert p["tipo"] == "anomalia" and p["launch_id"] == lid
        assert p["valor"] == pytest.approx(float(cand))
        assert p["titulo"].startswith("Gasto fora do padrão em ")


def test_roda_duas_vezes_um_evento(uid):
    for i in range(5):
        _lanca(uid, 100, dias=10 + i)
    _lanca(uid, 400, dias=0, horas=1)
    _roda(uid)
    _roda(uid)
    assert len(_eventos(uid)) == 1


def test_outro_usuario_nao_entra_na_media_nem_dispara(uid):
    outro = usuario_pagante()
    for i in range(20):
        _lanca(outro, 1, dias=10 + i)                 # histórico enorme e barato na mesma categoria
    for i in range(5):
        _lanca(uid, 100, dias=10 + i)
    _lanca(uid, 400, dias=0, horas=1)                 # 4x a média DELE; contra a do outro seria 400x
    _lanca(outro, 400, dias=0, horas=1)
    _roda(uid)
    assert [e["payload"]["media"] for e in _eventos(uid)] == [100.0]
    assert _eventos(outro) == []                      # o outro nem tem o agente ativado


@pytest.mark.parametrize("bruto", ["nan", "inf", -2, 0, "abc"])
def test_config_crua_nao_derruba_o_bloco_de_limites(uid, bruto):
    """Config legada inválida recupera os mesmos defaults mostrados na tela (PL-04 B).
    A escrita HTTP recusa esses valores; DB direto aqui simula a configuração antiga."""
    for i in range(5):
        _lanca(uid, 100, dias=10 + i)
    _lanca(uid, 480, dias=0, horas=0)
    _roda(uid, {"multiplicador": bruto, "minimo": bruto, "limites": {"alimentação": 100}})
    tipos = {e["payload"]["tipo"] for e in _eventos(uid)}
    assert "limite" in tipos
    assert "anomalia" in tipos
