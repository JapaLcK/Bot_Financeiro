"""
Testes unitários da escada de planos v2 (Grátis/Essencial/Plus/Pro) + trial 30d.

Tudo com monkeypatch — sem tocar o banco. Cobre:
  - v2 OFF: comportamento legado 100% preservado (is_pro, has_app_access, limites)
  - v2 ON: resolução de tier ('pro' legado→plus, expirado→free)
  - trial (2026-08-06): virou assinatura Stripe do plano escolhido (trialing);
    sem assinatura vigente = free (não há mais trial sem cartão → plus)
  - get_trial_status: contagem de dias (usado por UI/e-mails)
  - limites por tier (histórico do mês corrente no Grátis, lançamentos/mês, IA)
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from core.services import plan_service
from core.services.plan_limits import (
    FREE_LIMITS, ESSENCIAL_LIMITS, PLUS_LIMITS, PRO_LIMITS,
    PlanLimitExceeded, limits_for, limits_for_tier, tier_at_least,
)


def _user(plan="free", expires=None, trial_started=None, plan_selected=None):
    return {
        "plan": plan,
        "plan_expires_at": expires,
        "trial_started_at": trial_started,
        "plan_selected_at": plan_selected,
    }


def _patch_user(monkeypatch, user):
    monkeypatch.setattr(plan_service, "get_auth_user", lambda uid: user)


NOW = datetime.now(timezone.utc)
FUTURE = NOW + timedelta(days=10)
PAST = NOW - timedelta(days=10)


# ─── v2 OFF: legado intacto ──────────────────────────────────────────────────

class TestLegacyMode:
    def test_is_pro_active(self, monkeypatch):
        monkeypatch.setenv("PLANS_V2_ENABLED", "0")  # freio: default agora é LIGADO
        _patch_user(monkeypatch, _user("pro", FUTURE))
        assert plan_service.is_pro(1) is True

    def test_is_pro_lifetime_grandfathered(self, monkeypatch):
        monkeypatch.setenv("PLANS_V2_ENABLED", "0")  # freio: default agora é LIGADO
        _patch_user(monkeypatch, _user("pro", None))
        assert plan_service.is_pro(1) is True

    def test_is_pro_expired(self, monkeypatch):
        monkeypatch.setenv("PLANS_V2_ENABLED", "0")  # freio: default agora é LIGADO
        _patch_user(monkeypatch, _user("pro", PAST))
        assert plan_service.is_pro(1) is False

    def test_is_pro_free(self, monkeypatch):
        monkeypatch.setenv("PLANS_V2_ENABLED", "0")  # freio: default agora é LIGADO
        _patch_user(monkeypatch, _user("free"))
        assert plan_service.is_pro(1) is False

    def test_free_nao_ganha_trial_com_v2_off(self, monkeypatch):
        """Trial ancorado NÃO vaza pro mundo v1: só vale com o flag ligado."""
        monkeypatch.setenv("PLANS_V2_ENABLED", "0")  # freio: default agora é LIGADO
        _patch_user(monkeypatch, _user("free", trial_started=NOW))
        assert plan_service.is_pro(1) is False

    def test_paywall_block(self, monkeypatch):
        monkeypatch.setenv("PLANS_V2_ENABLED", "0")  # freio: default agora é LIGADO
        monkeypatch.setenv("PAYWALL_ENABLED", "true")
        _patch_user(monkeypatch, _user("free"))
        assert plan_service.has_app_access(1) is False

    def test_limits_legacy_mapping(self):
        assert limits_for("pro") is PLUS_LIMITS
        assert limits_for("free") is FREE_LIMITS
        assert limits_for("") is FREE_LIMITS


# ─── v2 ON: resolução de tier ────────────────────────────────────────────────

@pytest.fixture
def v2(monkeypatch):
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")


class TestTierResolution:
    def test_stored_pro_e_alias_de_plus(self, v2, monkeypatch):
        _patch_user(monkeypatch, _user("pro", FUTURE))
        assert plan_service.get_plan_tier(1) == "plus"
        assert plan_service.is_pro(1) is True

    def test_stored_essencial(self, v2, monkeypatch):
        _patch_user(monkeypatch, _user("essencial", FUTURE))
        assert plan_service.get_plan_tier(1) == "essencial"
        assert plan_service.is_pro(1) is False  # Essencial não é Plus

    def test_stored_pro_max_e_tier_pro(self, v2, monkeypatch):
        _patch_user(monkeypatch, _user("pro_max", FUTURE))
        assert plan_service.get_plan_tier(1) == "pro"
        assert plan_service.is_pro(1) is True

    def test_assinatura_expirada_cai_pra_free(self, v2, monkeypatch):
        _patch_user(monkeypatch, _user("essencial", PAST))
        assert plan_service.get_plan_tier(1) == "free"

    def test_grandfathered_vitalicio(self, v2, monkeypatch):
        _patch_user(monkeypatch, _user("pro", None))
        assert plan_service.get_plan_tier(1) == "plus"

    def test_free_sem_trial(self, v2, monkeypatch):
        _patch_user(monkeypatch, _user("free"))
        assert plan_service.get_plan_tier(1) == "free"
        assert plan_service.has_app_access(1) is True  # Grátis ENTRA no app


# ─── Gate de escolha de plano no cadastro (2026-08-11) ───────────────────────

class TestNeedsPlanSelection:
    def test_cadastro_novo_sem_escolha_cai_na_precos(self, v2, monkeypatch):
        # plan_selected_at None + Grátis = ainda não escolheu → gate ligado
        _patch_user(monkeypatch, _user("free", plan_selected=None))
        assert plan_service.needs_plan_selection(1) is True

    def test_gratis_escolhido_libera(self, v2, monkeypatch):
        _patch_user(monkeypatch, _user("free", plan_selected=PAST))
        assert plan_service.needs_plan_selection(1) is False

    def test_assinante_pago_ativo_nunca_trava(self, v2, monkeypatch):
        # Pagou/entrou em trial mas o mark ainda não gravou → não trava mesmo assim
        _patch_user(monkeypatch, _user("plus", FUTURE, plan_selected=None))
        assert plan_service.needs_plan_selection(1) is False

    def test_pago_expirado_sem_escolha_ainda_trava(self, v2, monkeypatch):
        _patch_user(monkeypatch, _user("plus", PAST, plan_selected=None))
        assert plan_service.needs_plan_selection(1) is True

    def test_gate_dormente_com_v2_off(self, monkeypatch):
        monkeypatch.setenv("PLANS_V2_ENABLED", "0")
        _patch_user(monkeypatch, _user("free", plan_selected=None))
        assert plan_service.needs_plan_selection(1) is False

    def test_usuario_inexistente_nao_trava(self, v2, monkeypatch):
        _patch_user(monkeypatch, None)
        assert plan_service.needs_plan_selection(1) is False

    def test_trial_sem_assinatura_e_free(self, v2, monkeypatch):
        """Novo modelo: trial é assinatura Stripe. trial_started_at sozinho (sem
        assinatura vigente) NÃO dá plus — cadastro novo entra Grátis."""
        _patch_user(monkeypatch, _user("free", trial_started=NOW - timedelta(days=5)))
        assert plan_service.get_plan_tier(1) == "free"
        assert plan_service.is_pro(1) is False

    def test_trial_stripe_resolve_o_plano_escolhido(self, v2, monkeypatch):
        """Trial via Stripe = assinatura trialing do plano escolhido (expires no
        futuro). Escolheu Pro → tier pro; escolheu Plus → tier plus."""
        _patch_user(monkeypatch, _user("pro_max", FUTURE))
        assert plan_service.get_plan_tier(1) == "pro"
        _patch_user(monkeypatch, _user("essencial", FUTURE))
        assert plan_service.get_plan_tier(1) == "essencial"

    def test_trial_expirado_cai_pra_free(self, v2, monkeypatch):
        _patch_user(monkeypatch, _user("free", trial_started=NOW - timedelta(days=31)))
        assert plan_service.get_plan_tier(1) == "free"

    def test_assinatura_expirada_nao_ressuscita_trial_queimado(self, v2, monkeypatch):
        """Ex-assinante com trial antigo queimado: nada de plus de brinde."""
        _patch_user(monkeypatch, _user("pro", PAST, trial_started=NOW - timedelta(days=90)))
        assert plan_service.get_plan_tier(1) == "free"


class TestTrialStatus:
    def test_days_left_inicio(self, v2, monkeypatch):
        _patch_user(monkeypatch, _user("free", trial_started=NOW - timedelta(hours=1)))
        st = plan_service.get_trial_status(1, _user("free", trial_started=NOW - timedelta(hours=1)))
        assert st["active"] is True
        assert st["days_left"] == 30

    def test_days_left_ultimo_dia(self, v2):
        st = plan_service.get_trial_status(
            1, _user("free", trial_started=NOW - timedelta(days=29, hours=12)))
        assert st["active"] is True
        assert st["days_left"] == 1

    def test_expirado(self, v2):
        st = plan_service.get_trial_status(
            1, _user("free", trial_started=NOW - timedelta(days=30, minutes=1)))
        assert st["active"] is False
        assert st["days_left"] == 0

    def test_sem_trial(self, v2):
        st = plan_service.get_trial_status(1, _user("free"))
        assert st["active"] is False
        assert st["days_left"] == 0


# ─── v2 ON: limites por tier ─────────────────────────────────────────────────

class TestLimits:
    def test_escada_historico(self):
        # 2026-08-06: Grátis mês corrente · Essencial 90d · Plus 12m · Pro 24m
        assert FREE_LIMITS["history_current_month_only"] is True
        assert ESSENCIAL_LIMITS["history_days"] == 90
        assert ESSENCIAL_LIMITS["history_current_month_only"] is False
        assert PLUS_LIMITS["history_days"] == 365        # 12 meses
        assert PRO_LIMITS["history_days"] == 730         # 24 meses

    def test_escada_bancos_e_agentes(self):
        # Escada final v3: bancos 0(1 só no trial)/1/2/5 · agentes 0/0/3/6
        assert FREE_LIMITS["of_banks_max"] == 0
        assert ESSENCIAL_LIMITS["of_banks_max"] == 1
        assert PLUS_LIMITS["of_banks_max"] == 2
        assert PRO_LIMITS["of_banks_max"] == 5
        assert FREE_LIMITS["agents_max"] == 0
        assert ESSENCIAL_LIMITS["agents_max"] == 0
        assert PLUS_LIMITS["agents_max"] == 3
        assert PRO_LIMITS["agents_max"] == 6

    def test_orcamento_de_energia_por_plano(self):
        # Modelo de energia: Grátis/Essencial 0 · Plus 4 · Pro 14 (cabe os 7 agentes).
        assert FREE_LIMITS["agents_energy_budget"] == 0
        assert ESSENCIAL_LIMITS["agents_energy_budget"] == 0
        assert PLUS_LIMITS["agents_energy_budget"] == 4
        assert PRO_LIMITS["agents_energy_budget"] == 14

    def test_tier_at_least(self):
        assert tier_at_least("plus", "essencial")
        assert tier_at_least("pro", "plus")
        assert not tier_at_least("essencial", "plus")
        assert not tier_at_least("free", "essencial")

    def test_trial_de_pro_libera_5_bancos(self, v2, monkeypatch):
        """Trial agora é o plano escolhido: trial de Pro (pro_max, trialing) tem
        os 5 bancos cheios — sem cap especial de trial."""
        _patch_user(monkeypatch, _user("pro_max", FUTURE))
        assert plan_service.get_user_limits(1)["of_banks_max"] == 5

    def test_plus_pago_of_2_bancos(self, v2, monkeypatch):
        _patch_user(monkeypatch, _user("pro", FUTURE))
        assert plan_service.get_user_limits(1)["of_banks_max"] == 2

    def test_pro_max_of_5_bancos(self, v2, monkeypatch):
        _patch_user(monkeypatch, _user("pro_max", FUTURE))
        assert plan_service.get_user_limits(1)["of_banks_max"] == 5

    def test_free_so_mes_corrente(self, v2, monkeypatch):
        _patch_user(monkeypatch, _user("free"))
        assert plan_service.history_current_month_only(1) is True

    def test_history_caps(self, v2, monkeypatch):
        _patch_user(monkeypatch, _user("essencial", FUTURE))
        assert plan_service.history_months_cap(1) == 3     # 90 dias
        _patch_user(monkeypatch, _user("pro", FUTURE))     # 'pro' legado = Plus
        assert plan_service.history_months_cap(1) == 12    # 12 meses
        _patch_user(monkeypatch, _user("pro_max", FUTURE))
        assert plan_service.history_months_cap(1) == 24    # 24 meses

    def test_history_free_comeca_no_primeiro_dia_local(self, v2, monkeypatch):
        _patch_user(monkeypatch, _user("free"))
        now = datetime(2026, 8, 6, 12, tzinfo=timezone.utc)
        assert plan_service.history_earliest_date(1, now) == date(2026, 8, 1)

    def test_history_essencial_respeita_90_dias(self, v2, monkeypatch):
        _patch_user(monkeypatch, _user("essencial", FUTURE))
        now = datetime(2026, 8, 6, 12, tzinfo=timezone.utc)
        assert plan_service.history_earliest_date(1, now) == date(2026, 5, 8)

    def test_feature_enabled_por_tier(self, v2, monkeypatch):
        _patch_user(monkeypatch, _user("free"))
        assert plan_service.feature_enabled(1, "audio_enabled") is False
        _patch_user(monkeypatch, _user("essencial", FUTURE))
        assert plan_service.feature_enabled(1, "audio_enabled") is True

    def test_launch_cap_free(self, v2, monkeypatch):
        _patch_user(monkeypatch, _user("free"))
        monkeypatch.setattr("db.plans.count_launches_this_month", lambda uid: 30)
        with pytest.raises(PlanLimitExceeded):
            plan_service.check_can_create_launch(1)

    def test_launch_cap_free_abaixo_do_teto(self, v2, monkeypatch):
        _patch_user(monkeypatch, _user("free"))
        monkeypatch.setattr("db.plans.count_launches_this_month", lambda uid: 29)
        plan_service.check_can_create_launch(1)  # não levanta

    def test_launch_sem_cap_essencial(self, v2, monkeypatch):
        _patch_user(monkeypatch, _user("essencial", FUTURE))
        plan_service.check_can_create_launch(1)  # ilimitado, nem consulta o count

    def test_launch_cap_noop_com_v2_off(self, monkeypatch):
        monkeypatch.setenv("PLANS_V2_ENABLED", "0")  # freio: default agora é LIGADO
        _patch_user(monkeypatch, _user("free"))
        plan_service.check_can_create_launch(1)  # v1 não tinha esse limite


class TestAgentGates:
    def test_free_e_essencial_sem_agentes(self, v2, monkeypatch):
        # Orçamento de energia 0 → nenhum agente (o kind não importa).
        _patch_user(monkeypatch, _user("free"))
        assert plan_service.agents_energy_budget(1) == 0
        assert plan_service.agent_kind_allowed(1, "xerife") is False
        _patch_user(monkeypatch, _user("essencial", FUTURE))
        assert plan_service.agents_energy_budget(1) == 0
        assert plan_service.agent_kind_allowed(1, "xerife") is False

    def test_plus_libera_todos_os_agentes(self, v2, monkeypatch):
        """Modelo de energia: SEM trava por kind — Plus libera todos os agentes
        (inclusive Detetive/Banqueiro/Barão). Quem limita quantos é o orçamento
        (⚡4), checado na ativação. 'pro' legado = Plus."""
        _patch_user(monkeypatch, _user("pro", FUTURE))
        assert plan_service.agents_energy_budget(1) == 4
        for kind in ("xerife", "reporter", "carteiro", "detetive", "cofre", "barao"):
            assert plan_service.agent_kind_allowed(1, kind) is True

    def test_pro_tem_energia_pra_todos(self, v2, monkeypatch):
        _patch_user(monkeypatch, _user("pro_max", FUTURE))  # Pro novo
        assert plan_service.agents_energy_budget(1) == 14
        for kind in ("xerife", "detetive", "cofre", "barao", "faria_limer"):
            assert plan_service.agent_kind_allowed(1, kind) is True

    def test_trial_stripe_ativa_agentes_do_plano(self, v2, monkeypatch):
        """Trial é o plano escolhido: trial de Plus (assinatura trialing) libera
        agentes. trial_started_at sozinho (sem assinatura) = free = sem energia."""
        _patch_user(monkeypatch, _user("pro", FUTURE))          # trial de Plus
        assert plan_service.agent_kind_allowed(1, "xerife") is True
        _patch_user(monkeypatch, _user("free", trial_started=NOW))  # sem assinatura
        assert plan_service.agent_kind_allowed(1, "xerife") is False

    def test_v1_off_nao_filtra(self, monkeypatch):
        monkeypatch.setenv("PLANS_V2_ENABLED", "0")  # freio: default agora é LIGADO
        _patch_user(monkeypatch, _user("free"))
        assert plan_service.agent_kind_allowed(1, "xerife") is True  # gate legado decide na rota

    def test_beta_tester_orcamento_cheio(self, v2, monkeypatch):
        """Tester do beta usa TODOS os agentes independe do tier: orçamento cheio
        (Pro) mesmo em Free, pra a isenção valer de ponta a ponta — no gate da
        rota (budget > 0) E na checagem transacional de energia (budget comporta
        todos). Usuário comum em Free continua sem energia."""
        monkeypatch.setenv("AGENTS_BETA_USER_IDS", "99")
        monkeypatch.setenv("AGENTS_BETA_EMAILS", "")  # zera a allowlist por e-mail
        _patch_user(monkeypatch, _user("free"))
        assert plan_service.agents_energy_budget(99) == 14
        assert plan_service.agent_kind_allowed(99, "detetive") is True
        assert plan_service.agents_energy_budget(1) == 0
        assert plan_service.agent_kind_allowed(1, "xerife") is False

    def test_custo_de_energia_por_agente(self):
        from core.services.plan_limits import agent_energy_cost
        assert agent_energy_cost("reporter") == 1
        assert agent_energy_cost("carteiro") == 1
        assert agent_energy_cost("xerife") == 2
        assert agent_energy_cost("barao") == 2
        assert agent_energy_cost("faria_limer") == 2
        assert agent_energy_cost("detetive") == 3
        assert agent_energy_cost("cofre") == 3
        assert agent_energy_cost("desconhecido") == 3  # default = o mais caro

    def test_calibracao_pro_cabe_os_7_agentes(self):
        """O orçamento do Pro é calibrado pra caber a equipe inteira: a soma do
        custo dos 7 agentes disponíveis bate exatamente com o orçamento (14)."""
        from core.services.plan_limits import (agent_energy_cost, AGENT_ENERGY_COST,
                                               PRO_LIMITS)
        total = sum(agent_energy_cost(k) for k in AGENT_ENERGY_COST)
        assert total == PRO_LIMITS["agents_energy_budget"] == 14

    def test_calibracao_plus_3_baratos_ou_2_caros(self):
        """A regra 'até 3 baratos, ou 2 se caro' cai da calibração dos números:
        os 3 baratos somam exatamente o orçamento do Plus; um caro (⚡3) só deixa
        espaço pra mais 1 barato."""
        from core.services.plan_limits import agent_energy_cost, PLUS_LIMITS
        budget = PLUS_LIMITS["agents_energy_budget"]
        baratos = (agent_energy_cost("reporter") + agent_energy_cost("carteiro")
                   + agent_energy_cost("xerife"))
        assert baratos == budget == 4                                  # 3 baratos = 4/4
        assert agent_energy_cost("detetive") + agent_energy_cost("reporter") == 4  # caro + 1 = 2 agentes
        assert (agent_energy_cost("detetive") + agent_energy_cost("reporter")
                + agent_energy_cost("carteiro")) > budget              # não cabe um 3º


class TestAIQuota:
    def test_cota_free(self, v2, monkeypatch):
        _patch_user(monkeypatch, _user("free"))
        assert plan_service.ai_monthly_limit_for(1) == 20

    def test_cota_essencial(self, v2, monkeypatch):
        _patch_user(monkeypatch, _user("essencial", FUTURE))
        assert plan_service.ai_monthly_limit_for(1) == 200

    def test_cota_plus_cai_no_limite_global(self, v2, monkeypatch):
        from core.services.ai_chat_commands import AI_CHAT_MONTHLY_LIMIT
        _patch_user(monkeypatch, _user("pro", FUTURE))
        assert plan_service.ai_monthly_limit_for(1) == AI_CHAT_MONTHLY_LIMIT

    def test_free_com_cota_pode_falar(self, v2, monkeypatch):
        _patch_user(monkeypatch, _user("free"))
        monkeypatch.setattr("db.ai_chat.get_usage_this_month", lambda uid: 19)
        assert plan_service.ai_chat_allowed(1) is True

    def test_free_cota_estourada_bloqueia(self, v2, monkeypatch):
        _patch_user(monkeypatch, _user("free"))
        monkeypatch.setattr("db.ai_chat.get_usage_this_month", lambda uid: 20)
        assert plan_service.ai_chat_allowed(1) is False

    def test_v1_so_pro_fala(self, monkeypatch):
        monkeypatch.setenv("PLANS_V2_ENABLED", "0")  # freio: default agora é LIGADO
        _patch_user(monkeypatch, _user("free"))
        assert plan_service.ai_chat_allowed(1) is False
        _patch_user(monkeypatch, _user("pro", FUTURE))
        assert plan_service.ai_chat_allowed(1) is True
