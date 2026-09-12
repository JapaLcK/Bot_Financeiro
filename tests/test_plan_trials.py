from __future__ import annotations

import pytest

from db import plans


def _db_unavailable():
    raise RuntimeError("db indisponível")


def test_eligibilidade_nao_falha_aberta_quando_banco_cai(monkeypatch):
    monkeypatch.setattr(plans, "get_conn", _db_unavailable)

    with pytest.raises(plans.TrialEligibilityError):
        plans.is_trial_eligible_for_user(123)


def test_claim_propaga_falha_para_webhook_tentar_novamente(monkeypatch):
    monkeypatch.setattr(plans, "get_conn", _db_unavailable)

    with pytest.raises(plans.TrialClaimError):
        plans.claim_trial_for_user(123)


class _ConnFake:
    """`get_conn()` de mentira, devolvendo as linhas que o SELECT acharia.

    `motivo_trial_indisponivel` faz DUAS queries, nesta ordem: o `phone_hash` da
    conta e a linha de `plan_trials` daquele hash. Uma linha por query, na
    ordem. Serve de conn e de cursor — os dois são usados como context manager e
    nada mais do protocolo é tocado.
    """

    def __init__(self, *linhas):
        self._linhas = list(linhas)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def execute(self, sql, params):
        self._atual = self._linhas.pop(0)

    def fetchone(self):
        return self._atual


# ── A BORDA `sem_telefone` do wrapper de elegibilidade ───────────────────────
#
# `is_trial_eligible_for_user` virou wrapper de `motivo_trial_indisponivel`
# (`db/plans.py`) quando a mensagem de bloqueio do corte passou a precisar do
# MOTIVO, e a docstring dele AFIRMA que a resposta para "sem telefone" é a mesma
# de "já usou": não há trial. Até 2026-09-11 nada media isso — a injeção abaixo
# passava por 9 arquivos de teste sem uma linha vermelha, e o consumidor de
# produção é `frontend/finance_bot_websocket_custom.py`, que decide
# `trial_period_days` no checkout. Dinheiro.
#
# CONTROLE DECLARADO (`docs/controles_declarados.md`) — em
# `db.plans.is_trial_eligible_for_user`, troque o `return` por::
#
#     return motivo_trial_indisponivel(user_id) != "telefone_ja_usou"
#
# VERMELHO (medido 2026-09-11):
#   `test_conta_sem_telefone_nao_ganha_trial`  (só a 2ª asserção)
# Direção: falso POSITIVO de trial — cadastro web que nunca vinculou WhatsApp
# ganharia 15 dias grátis a cada checkout, sem nada em `plan_trials` para
# impedir a repetição.
#
# Positivos do PAR, VERDES sob a injeção (é o que os torna positivos):
#   a 1ª asserção de `test_conta_sem_telefone_nao_ganha_trial` (o MOTIVO, que
#   continua `sem_telefone` — a injeção é no wrapper, não na fonte)
#   `test_telefone_novo_ganha_trial` (o caminho legítimo não foi recusado)


def test_conta_sem_telefone_nao_ganha_trial(monkeypatch):
    """Conta sem `phone_hash`: motivo `sem_telefone`, booleano False."""
    monkeypatch.setattr(plans, "get_conn", lambda: _ConnFake({"phone_hash": None}))

    assert plans.motivo_trial_indisponivel(7) == "sem_telefone"
    assert plans.is_trial_eligible_for_user(7) is False


def test_telefone_novo_ganha_trial(monkeypatch):
    """Telefone que nunca apareceu em `plan_trials` continua elegível."""
    monkeypatch.setattr(
        plans, "get_conn", lambda: _ConnFake({"phone_hash": "h"}, None))

    assert plans.is_trial_eligible_for_user(7) is True
