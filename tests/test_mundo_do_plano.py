"""Sentinela da fixture `_mundo_do_plano` (conftest): fora de `_AINDA_EM_V1`
a suíte roda o mundo de produção — v2 e gate de acesso ligados."""
from pathlib import Path

from conftest import _AINDA_EM_V1, _aplica_mundo_do_plano
from core.services.plan_service import access_gate_enabled, has_app_access, plans_v2_enabled


def test_fora_da_lista_roda_v2_com_gate():
    assert Path(__file__).name not in _AINDA_EM_V1
    assert plans_v2_enabled() is True
    assert access_gate_enabled() is True


def test_arquivo_da_lista_puxa_o_freio_do_v2(monkeypatch):
    _aplica_mundo_do_plano(sorted(_AINDA_EM_V1)[0], monkeypatch)
    assert plans_v2_enabled() is False


def test_arquivo_fora_da_lista_apaga_env_do_shell(monkeypatch):
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    monkeypatch.setenv("ACCESS_GATE_ENABLED", "0")
    _aplica_mundo_do_plano(Path(__file__).name, monkeypatch)
    assert plans_v2_enabled() is True
    assert access_gate_enabled() is True


def test_setenv_no_corpo_ganha_da_fixture(monkeypatch):
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    assert plans_v2_enabled() is False


def test_usuario_sem_plano_nao_tem_acesso(user_id):
    assert has_app_access(user_id) is False


def test_usuario_pagante_tem_acesso(pro_user_id, pro_small_uid):
    assert has_app_access(pro_user_id) is True
    assert has_app_access(pro_small_uid) is True
    assert pro_small_uid < 1_000_000_000


def test_lista_so_tem_arquivos_que_existem():
    pasta = Path(__file__).parent
    assert sorted(n for n in _AINDA_EM_V1 if not (pasta / n).is_file()) == []
