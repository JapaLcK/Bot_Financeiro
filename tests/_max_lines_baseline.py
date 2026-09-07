"""Linha de base do portão de tamanho dos arquivos Python (`TETO = 350`).

Isenção é por CAMINHO EXATO e só isso. Não há isenção por nome de arquivo nem
por diretório: foi assim que o portão de JavaScript deixou passar
`frontend/index.js` com 404 linhas, e as duas isenções do template TypeScript
foram removidas em 2026-09-04 (`eslint-rules/utils.cjs`). `tests/test_max_lines_python.py`
prende as duas do lado Python com sondas.

A lista é um RATCHET, não uma anistia: arquivo daqui que cair para 350 ou menos
deixa o CI vermelho até ser REMOVIDO desta lista. Ninguém acrescenta nome novo
sem quebrar o arquivo primeiro.

Sem o tamanho de cada um anotado: número que um comando responde envelhece em
silêncio (CLAUDE.md §2 — o `eslint.config.mjs` recusou a mesma anotação pelo
mesmo motivo). Para remedir a lista inteira, da raiz do repositório:

    git ls-files -z '*.py' | xargs -0 -n1 awk 'END{if (NR > 350) print FILENAME}' | sort

Sem prefixo `test_` de propósito, como `tests/_billing_grants_helpers.py`: o
pytest não coleta este arquivo. Sendo `.py` e rastreado, ele é medido pelo
próprio portão.
"""

LEGADOS: frozenset[str] = frozenset(
    {
        "adapters/whatsapp/wa_app.py",
        "adapters/whatsapp/wa_client.py",
        "adapters/whatsapp/wa_runtime.py",
        "adapters/whatsapp/wa_tutorial.py",
        "admin.py",
        "core/admin_dashboard.py",
        "core/ai_patterns.py",
        "core/blog_guides.py",
        "core/handle_incoming.py",
        "core/handlers/credit.py",
        "core/handlers/help_handler.py",
        "core/handlers/investments.py",
        "core/handlers/launches.py",
        "core/handlers/pending.py",
        "core/help_text.py",
        "core/intent_classifier.py",
        "core/intent_router.py",
        "core/services/ai_chat/runner.py",
        "core/services/ai_chat/tools/bills.py",
        "core/services/ai_chat/tools/budgets.py",
        "core/services/ai_chat/tools/cards.py",
        "core/services/ai_chat/tools/investments.py",
        "core/services/ai_chat/tools/launches.py",
        "core/services/ai_chat/tools/pockets.py",
        "core/services/ai_guard.py",
        "core/services/category_service.py",
        "core/services/email_service.py",
        "core/services/piggy_agents.py",
        "core/services/plan_service.py",
        "core/services/pluggy_health.py",
        "core/services/pluggy_sync.py",
        "core/services/recurring_charger.py",
        "db/__init__.py",
        "db/accounts.py",
        "db/affiliates.py",
        "db/agents.py",
        "db/analytics.py",
        "db/budgets.py",
        "db/cards.py",
        "db/categories.py",
        "db/insights.py",
        "db/investments.py",
        "db/mfa.py",
        "db/open_finance.py",
        "db/open_finance_state.py",
        "db/pending.py",
        "db/pockets.py",
        "db/privacy.py",
        "db/recurring.py",
        "db/reports.py",
        "db/schema.py",
        "db/users.py",
        "db_support.py",
        "frontend/finance_bot_websocket_custom.py",
        "frontend/routes/cards.py",
        "frontend/routes/open_finance.py",
        "frontend/routes/pockets.py",
        "frontend/routes/settings.py",
        "frontend/routes/shared.py",
        "frontend/routes/static_pages.py",
        "ofx_credit_import.py",
        "parsers.py",
        "scripts/coluna_dupla.py",
        "scripts/medir_guarda_efeitos_214.py",
        "scripts/migrate_pii_to_encrypted.py",
        "scripts/post_deploy_deletion_check.py",
        "scripts/whatsapp_qa_vault_harness.py",
        "statement_import.py",
        "tests/conftest.py",
        "tests/test_account_reset.py",
        "tests/test_admin_users_panel.py",
        "tests/test_ai_chat_bloco_b.py",
        "tests/test_ai_chat_concurrent_confirm.py",
        "tests/test_ai_chat_tier2.py",
        "tests/test_auth_cookie.py",
        "tests/test_bill_amount_pending.py",
        "tests/test_billing_checkout.py",
        "tests/test_budget_category_accent.py",
        "tests/test_categoria_custom_pela_conversa.py",
        "tests/test_category_launches_query.py",
        "tests/test_category_normalization.py",
        "tests/test_coluna_dupla_gate.py",
        "tests/test_credit_transactions_endpoints.py",
        "tests/test_custom_category_infer.py",
        "tests/test_db_core.py",
        "tests/test_db_investments.py",
        "tests/test_delete_all_launches.py",
        "tests/test_error_pages.py",
        "tests/test_full_handler_smoke.py",
        "tests/test_funding_source.py",
        "tests/test_fuso_do_app.py",
        "tests/test_ga4_measurement_protocol.py",
        "tests/test_log_falha_traceback.py",
        "tests/test_log_falha_user_id.py",
        "tests/test_market_rates_cache_first.py",
        "tests/test_mfa.py",
        "tests/test_multi_launch_concurrency.py",
        "tests/test_nlp_and_pending_flow.py",
        "tests/test_of_concurrency.py",
        "tests/test_of_connection_state.py",
        "tests/test_of_health.py",
        "tests/test_of_refresh_schedule.py",
        "tests/test_pending_consume_race.py",
        "tests/test_pending_rollback.py",
        "tests/test_perguntas_guardam_contexto.py",
        "tests/test_piggy_agents.py",
        "tests/test_plan_tiers.py",
        "tests/test_prospect_referrals.py",
        "tests/test_routes_categories.py",
        "tests/test_statement_import.py",
        "tests/test_static_pages_routes.py",
        "tests/test_tipo_legado_na_cauda.py",
        "tests/test_tipo_legado_na_tendencia.py",
        "tests/test_virada_de_mes.py",
        "tests/test_whatsapp_markup_escape.py",
        "utils_date.py",
        "utils_text.py",

        # --- Bloco de 2026-09-07: os 4 arquivos que o PR #298 acrescentou ---
        # Entram como legado por decisão do dono, e não porque o portão os
        # aprovou: o #298 já estava mergeado na `main` quando este portão
        # nasceu, então cobrá-los aqui seria reprovar código que entrou sob
        # regra que ainda não existia — o portão passaria a medir a data do
        # merge, não o tamanho do arquivo.
        #
        # Os DOIS de teste saem no PR seguinte, pelo corte já definido no
        # registro do assunto: canais `R2-5` e `R2-5b`, os três `B1` cruzados
        # com veredito/matriz (`9r` ×2, `9t-b`), `R3-1` e `R3-5`. Quando o
        # corte entrar, os dois nomes saem DESTA lista — e o teste `sobrando`
        # de `tests/test_max_lines_python.py` fica vermelho até que saiam.
        # Os dois de produção não têm data: quebrar `billing_access.py` é
        # trabalho de fatoração, fora deste ciclo.
        "core/services/billing_access.py",
        "db/plan_grants.py",
        "tests/test_billing_grants_reducao.py",
        "tests/test_billing_grants_tier.py",
    }
)
