# Plano de refactor do Bot Financeiro

## Diagnostico atual

- `frontend/finance_bot_websocket_custom.py` concentra a aplicacao FastAPI inteira: conexao async com banco, auth, MFA, billing, paginas estaticas, dashboard, cartoes/faturas, analytics, privacidade, Open Finance e WebSocket.
- O arquivo tem mais de 6 mil linhas e mistura rotas HTTP, regras de negocio, SQL, serializacao e startup. Isso dificulta teste isolado e aumenta risco de conflito em mudancas pequenas.
- Existem dois mundos de handlers de credito: `handlers/credit.py` usado pelo Discord legado e `core/handlers/credit.py` usado pelo fluxo novo. As funcoes auxiliares sao muito parecidas e devem convergir.
- `db_support.py` ainda carrega parte da logica de relatorios/auth como camada intermediaria para `db/reports.py`, enquanto outros dominios ja vivem diretamente em `db/`.
- As migrations ja estao centralizadas em `db/schema.py`; manter migrations tambem no servidor web gera duplicacao e startup mais dificil de entender.

## Remocoes ja feitas nesta limpeza

- Removidos arquivos mortos em `_deprecated/`, sem referencias fora da propria pasta.
- Removidas migrations duplicadas do `frontend/finance_bot_websocket_custom.py`; o servidor agora depende de `init_db()` e dos helpers ainda especificos de privacidade/admin no startup.

## Candidatos encontrados, mas nao removidos ainda

- `handlers/credit.py`: duplica varias funcoes de `core/handlers/credit.py`, mas ainda e importado por `adapters/discord/discord_bot.py`.
- `db_support.py`: parece camada de transicao, mas `db/reports.py` e `db/accounts.py` ainda delegam funcoes para ele.
- Funcoes de rota no `frontend/finance_bot_websocket_custom.py` aparecem sem chamada direta em analise AST, mas nao sao mortas: o FastAPI as usa via decorators.
- Helpers com nomes repetidos (`_normalize`, `_parse_iso_date`, `_extract_ledger_balance`, `add_months`) existem em contextos diferentes; devem ser consolidados com testes de regressao, nao apagados direto.

## Refactor proposto

### Fase 1 - Quebrar o servidor FastAPI sem mudar comportamento

Criar um pacote `frontend/dashboard_app/` e mover blocos por responsabilidade:

- `app.py`: fabrica `FastAPI`, CORS, middlewares e registro de routers.
- `settings.py`: leitura de envs do dashboard, cookies e flags.
- `db.py`: pool async, `db_connect()` e helpers de query.
- `auth.py`: JWT, cookies, CSRF, usuario atual e rotas `/auth/*`.
- `billing.py`: Stripe checkout, portal e webhook.
- `static_pages.py`: rotas HTML, manifest, service worker, sitemap e favicon.
- `dashboard_data.py`: `get_financial_data()`, historico mensal, CSV e cache do dashboard.
- `launches.py`, `pockets.py`, `cards.py`, `budgets.py`, `categories.py`, `recurring.py`, `investments.py`: routers por dominio.
- `analytics.py`: KPIs, evolucao, categorias, historico e insights.
- `security.py`: atividade, sessoes, contatos e reset.
- `open_finance.py`: snapshot, Pluggy, mock e webhook.
- `websocket.py`: `ConnectionManager` e endpoint `/ws/{user_id}`.

Manter `frontend/finance_bot_websocket_custom.py` temporariamente como compatibilidade, exportando `app` do novo pacote para preservar `frontend.finance_bot_websocket_custom:app`.

### Fase 2 - Consolidar duplicacoes de dominio

- Migrar o Discord para usar `core/handlers/credit.py` ou extrair helpers compartilhados para `core/services/credit_commands.py`.
- Remover `handlers/credit.py` quando nenhum adapter importar o modulo legado.
- Absorver gradualmente `db_support.py` em modulos de dominio (`db/reports.py`, `db/accounts.py`, `db/privacy.py`) e apagar a camada intermediaria.
- Padronizar helpers duplicados simples (`_normalize`, `_parse_iso_date`, `_extract_ledger_balance`, `add_months`) em `utils_text.py`, `utils_date.py` ou servicos de dominio.

### Fase 3 - Reduzir SQL inline nas rotas

- Mover SQL de dashboard/cartoes/faturas para funcoes em `db/analytics.py`, `db/cards.py` e `db/accounts.py`.
- Rotas devem validar entrada, chamar servico/db e serializar resposta. SQL complexo nao deve ficar no router.
- Criar testes de contrato para payloads usados pelo frontend antes de alterar queries.

### Fase 4 - Fortalecer testes e guardrails

- Adicionar teste de import para `frontend.finance_bot_websocket_custom:app` e para cada router novo.
- Cobrir rotas criticas apos cada extracao: auth, billing webhook, lancamentos, cartoes, caixinhas, export CSV, Open Finance e WebSocket.
- Adicionar `ruff`/`pyflakes` ao ambiente para detectar imports mortos, nomes nao usados e duplicacoes basicas em CI.

## Ordem recomendada

1. Extrair `settings.py`, `db.py`, `dashboard_data.py` e `websocket.py`; sao blocos grandes e com dependencias relativamente claras.
2. Extrair routers pequenos de paginas estaticas e comandos catalogados.
3. Extrair auth/MFA junto com cookies/CSRF, mantendo testes de sessao e MFA como rede de seguranca.
4. Extrair dominios transacionais: lancamentos, cartoes, faturas, caixinhas, orcamentos e investimentos.
5. Consolidar handlers de credito legado/novo.
6. Eliminar `db_support.py`.
