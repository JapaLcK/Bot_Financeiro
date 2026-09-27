"""Linha de base do portão de tamanho dos arquivos Python (`TETO = 350`).

Para código de PRODUÇÃO, isenção é por CAMINHO EXATO e só isso. Não há isenção
por nome de arquivo nem por diretório: foi assim que o portão de JavaScript
deixou passar `frontend/index.js` com 404 linhas, e as duas isenções do
template TypeScript foram removidas em 2026-09-04 (`eslint-rules/utils.cjs`).
`tests/test_max_lines_python.py` prende as duas do lado Python com sondas.
(Arquivo de TESTE não usa esta lista — ver o parágrafo seguinte.)

**Sem entrada de `tests/` ou `harness_tests/` aqui.** Arquivo de teste acima do
teto não precisa mais de isenção nominal: `test_max_lines_python.py` trata os
dois diretórios de teste como AVISO, não reprovação — o mesmo tratamento que o
lado JS já dá a `tests/frontend/**/*.mjs`. O ratchet abaixo continua valendo
só para código de PRODUÇÃO.

A lista é um RATCHET, não uma anistia: arquivo daqui que cair para 350 ou menos
deixa o CI vermelho até ser REMOVIDO desta lista. O que o CI garante
MECANICAMENTE é só isso: o ratchet dos nomes JÁ registrados. Nome novo não
entra aqui por POLÍTICA, não por impedimento técnico — acrescentar um arquivo
novo e grande a esta lista passa em tudo. Alteração de baseline depende de
revisão de diff, e é lá que a política é aplicada.

Sem o tamanho de cada um anotado: número que um comando responde envelhece em
silêncio (CLAUDE.md §2 — o `eslint.config.mjs` recusou a mesma anotação pelo
mesmo motivo). Para remedir a lista inteira, da raiz do repositório — o
`grep -v` tira `tests/`/`harness_tests/`, que não entram aqui (linha acima):

    git ls-files -z '*.py' | xargs -0 -n1 awk 'END{if (NR > 350) print FILENAME}' \
        | sort | grep -vE '^(tests|harness_tests)/'

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
        "utils_date.py",
        "utils_text.py",

        # --- Bloco de 2026-09-07: os 2 arquivos de PRODUÇÃO que o PR #298
        # acrescentou --- Entram como legado por decisão do dono, e não porque
        # o portão os aprovou: o #298 já estava mergeado na `main` quando este
        # portão nasceu, então cobrá-los aqui seria reprovar código que entrou
        # sob regra que ainda não existia — o portão passaria a medir a data
        # do merge, não o tamanho do arquivo. Sem data: quebrar
        # `billing_access.py` é trabalho de fatoração, fora deste ciclo.
        #
        # Os dois arquivos de TESTE que este bloco trazia
        # (`tests/test_billing_grants_reducao.py`, `tests/test_billing_grants_tier.py`)
        # saíram desta lista quando `tests/` deixou de precisar de isenção
        # nominal (teste acima do teto virou aviso, não reprovação).
        "core/services/billing_access.py",
        "db/plan_grants.py",
    }
)
