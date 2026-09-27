"""Sentinela do mundo do plano: a suíte inteira roda o de produção — v2 e gate
de acesso ligados —, e os helpers de plano do conftest entregam o que prometem."""
from core.services.plan_service import access_gate_enabled, has_app_access, plans_v2_enabled


def test_suite_roda_v2_com_gate():
    assert plans_v2_enabled() is True
    assert access_gate_enabled() is True


def test_usuario_sem_plano_nao_tem_acesso(user_id):
    assert has_app_access(user_id) is False


def test_usuario_pagante_tem_acesso(pro_user_id, pro_small_uid):
    assert has_app_access(pro_user_id) is True
    assert has_app_access(pro_small_uid) is True
    assert pro_small_uid < 1_000_000_000
