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
