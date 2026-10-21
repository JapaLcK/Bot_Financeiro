# PigBank — contexto de domínio

> **Antes de escrever qualquer código, leia o `CLAUDE.md` da raiz.** Ele traz as
> **regras permanentes de desenvolvimento** (§0: procurar antes de criar, escrever o
> mínimo, mudança cirúrgica, uma fonte de verdade, organização de arquivos), o
> processo de teste e PR, e as armadilhas que já custaram caro. Este arquivo aqui é
> **só o domínio**: o que o produto é, onde cada coisa mora e o que existe hoje.
>
> As duas regras que mais se aplicam a este arquivo: **não repita aqui o que o código
> já declara** (aponte para o arquivo-fonte) e **não invente o que não está no
> repositório**.

---

## O que é o projeto

Assistente financeiro pessoal usado por **WhatsApp** (canal principal), **Discord** e
por um **dashboard web**. Registra despesa e receita em linguagem natural, categoriza
com IA, e cobre cartão de crédito, parcelamentos, caixinhas, metas, orçamentos,
boletos, recorrentes, investimentos com CDI, importação de extrato (OFX/CSV/PDF) e
Open Finance.

**Stack:** Python 3.13 · FastAPI · PostgreSQL (psycopg 3) · discord.py · Railway
(deploy) · Cloudflare (borda). Frontend em HTML/CSS/JS escritos à mão, **com
ilhas React delimitadas** (`webapp/` → bundles IIFE em `frontend/`); o
`package.json` da raiz continua sem script `build`, e o porquê está em "Decisões
tomadas". App iOS em Capacitor carregando o próprio site.

---

## Mapa do repositório

```
launch.py                 — entrypoint do Railway: carrega o ambiente e vira o uvicorn ($PORT)
bot.py                    — bot do Discord (fora do launch.py desde o PR 5a; não roda)
ai_router.py              — chamada à OpenAI (modelo em OPENAI_MODEL, default gpt-6-luna)
parsers.py                — parse de linguagem natural ("gastei 50 mercado")
statement_import.py       — importação de extrato (OFX/CSV/PDF)

core/
  handle_incoming.py      — roteador principal de mensagens (Discord e WhatsApp)
  intent_classifier.py    — classificação de intenção
  intent_router.py        — despacho por intenção
  handlers/               — handlers por domínio do fluxo de mensagem
  services/               — e-mail, Pluggy, planos, push, agentes, agendadores,
                            OFX, PIX, categorias, cartão… (quantos: `ls
                            core/services/*.py | wc -l`; o número que estava
                            aqui dizia 35 e envelheceu no PR seguinte — §2)
  reports/                — relatório diário (reports_daily.py)
  crypto.py, audit.py     — PII cifrada e trilha de auditoria

api/v2/                   — a /api/v2 do dashboard v2: sub-app FastAPI montado pelo
                            monólito em /api/v2, com envelope de erro próprio
                            (erros.py), a dependência única do usuário (sessao.py)
                            e um router por assunto (me.py, eventos.py,
                            perfil.py, contas.py, assinaturas.py, resumo_mes.py)

db/                       — PACOTE com ~30 módulos, um por domínio
  schema.py               — DDL de TODAS as tabelas (init_db) — fonte de verdade
  connection.py           — pool psycopg3

frontend/
  finance_bot_websocket_custom.py — app FastAPI (~14,5k linhas): auth, MFA, billing,
                                    WebSocket, dashboard. AINDA é o monólito.
  routes/                 — routers já extraídos: static_pages, settings, pockets,
                            cards, analytics, open_finance, push, agents, affiliates,
                            shared (html_file, stamp_asset_versions, página de erro)
  *.html                  — servidas por static_pages.py (quantas: `ls frontend/*.html
                            | wc -l`; o número que estava aqui dizia 26 e eram 27);
                            ver o §5 do CLAUDE.md da raiz
  *.css / *.js            — cada arquivo tem uma rota própria; não há StaticFiles mount.
                            Página ou asset sem rota é código morto que ninguém alcança:
                            tests/test_frontend_assets_e_rotas.py reprova os dois casos

adapters/
  whatsapp/               — webhook + cliente da API oficial (Cloud API)
  discord/                — bot e cogs

mobile/                   — app iOS (Capacitor) que carrega https://pigbankai.com/login
scripts/                  — utilitários operacionais e de build de assets
tests/                    — pytest (backend) e tests/frontend/*.mjs (node --test)
docs/refactor_plan.md     — plano de quebra do monólito FastAPI
docs/open_finance_validacao_manual.md — o que do Open Finance só se valida em
                            aparelho ou com Pluggy real, e o roteiro para isso
```

---

## Backend

### App e rotas

O `app` FastAPI vive em `frontend/finance_bot_websocket_custom.py`. Parte das rotas já
saiu para routers em `frontend/routes/`, registrados com `include_router`:
`static_pages`, `settings`, `pockets`, `cards`, `analytics`, `affiliates`, `agents`,
`open_finance`, `push`, `simulator`.

`POST /simulator/{user_id}` (`frontend/routes/simulator.py`) é o simulador de compra
do Pro: 1 a 3 cenários (à vista, parcelado, financiado pela tabela Price) comparados
com o atual sobre a mesma leitura da previsão de saldo, 90 dias + resumo do contrato.
O pior dia e a reserva incluem hoje após a compra e os 90 dias seguintes (91 datas);
o saldo final continua sendo o do dia 90. A tool avisa antes dos números se os
bancos conectados estiverem excluídos ou o saldo consolidado não for confirmado.
Preço, entrada, custos, despesa mensal nova e reserva aceitam no máximo duas casas
decimais; frações de centavo são recusadas na validação comum da API e da tool.
Taxas percentuais mantêm precisão livre, inclusive valores muito pequenos (na rota legada e na tool; a v2 aceita juros com no máximo 4 casas, ver "API v2").
Sem persistência; lógica em `core/services/decision_simulator.py` (`simular` sobre uma snapshot, em Decimal;
`simulate` é o adaptador legado em float, sem a trajetória diária). Esta rota é a legada (tool e card);
a tela web usa `POST /api/v2/simulador` (ver "API v2").

**Rota nova vai para um router de `frontend/routes/`**, não para o monólito — exceto
rota da `/api/v2`, que vai para `api/v2/` (ver "API v2" abaixo). Ao procurar uma rota
existente, procure nos dois lugares:

```bash
grep -rn '@\(app\|router\)\.\(get\|post\|put\|patch\|delete\)("/caminho' --include="*.py" frontend/ adapters/
```

São ~198 rotas. Os grupos maiores: `/auth` (27), `/open-finance` (11), `/settings`
(10), `/billing` (8), `/agents` (7), `/cards`, `/categories`, `/pockets`,
`/recurring-bills` (6 cada), `/analytics` (6), `/investments`, `/installments`,
`/recurring-incomes`, `/recurring-expenses` (5 cada), `/budgets` (4).

### API v2 (`api/v2/`)

Sub-app FastAPI (`api/v2/app.py`) montado pelo monólito com `app.mount("/api/v2", ...)`.
É o backend do dashboard v2 (`docs/plano-dashboard-v2.md`, §3). Regras, presas por
`tests/test_api_v2_rotas.py`:

- **Nenhuma rota recebe `user_id`** — nem path, query, header, cookie ou corpo. O
  usuário vem da dependência `usuario_atual` (`api/v2/sessao.py`): sessão (Bearer ou
  cookie `dashboard_token`) → conta agendada para exclusão (403) → gate de plano
  (`_enforce_subscription_gate`, 402) → chave `dashboard_v2_enabled` (404
  `dashboard_v2_disabled`). O user agent não entra.
- Toda rota tem `response_model` (a de SSE, o tipo do item do stream; ver `/eventos`).
- **Erro** sai no envelope `{"error": {"code", "message", "details"?}}`
  (`api/v2/erros.py`), com os headers da exceção preservados (`WWW-Authenticate` do
  401, `Allow` do 405). A exceção não tratada sai 500 `internal_error` ou, se for
  timeout/queda de conexão de banco, 503 `service_unavailable` — a regra é a
  `status_do_erro` (`core/admin_dashboard.py`), a mesma do pai — e registra
  `log_system_event` ali mesmo: o `admin_error_logging_middleware` do pai não enxerga
  exceção que o sub-app já respondeu. `ClientDisconnect` é levantada de novo para o
  pai, que responde 499 sem evento. O `ServerErrorMiddleware` do sub-app re-levanta a
  exceção depois de responder; o `erros.sem_reraise`, por fora dele (`_AppV2` em
  `api/v2/app.py`), a engole quando a resposta já começou, e o `TestClient` padrão não
  a vê. Erro no meio de um stream SSE chega num `ExceptionGroup` e é desembrulhado
  antes de classificar. Ficam **fora** do envelope o 403 do CSRF e o 422 do
  `query_venenosa_middleware`, que nascem nos middlewares do pai e saem `{"detail": ...}`.
- `GET /api/v2/me` devolve `{"plan_tier": "free"|"essencial"|"plus"|"pro", "agents_ui_enabled": bool}`, sem PII. `agents_ui_enabled` é o freio `AGENTS_UI_ENABLED=0` (o mesmo gate do `/auth/me` e do `/agents`): com ele `false` o /painel esconde a tela de Agentes.
- `GET /api/v2/eventos` (`api/v2/eventos.py`): SSE, `data: {"recurso": "open_finance"|"tudo"}`
  (sem dado financeiro, sem id, sem `id:`/replay) e `: ping` a cada 15 s. Quem avisa chama
  `eventos.avisar(user_id, recurso)` na thread do loop, depois do commit: o fim do sync do
  Open Finance, o "Recomeçar do zero" e, desde o #691, toda escrita nas tabelas de
  `db/schema.py::TABELAS_QUE_AVISAM` (trigger `pg_notify('pb_escrita', dono)` →
  `escutar_banco()` → `"tudo"`), de qualquer canal, o WhatsApp também. `usuario_atual` roda de novo antes de
  cada envio e a cada 30 s: sessão ou plano caídos fecham o stream sem aviso. Teto de 5
  streams por usuário (429 no envelope). Rota SSE tipa o item pela anotação de retorno
  (`-> AsyncIterable[Aviso]`), e a varredura aceita isso no lugar do `response_model`. O
  cliente (`webapp/src/dashboard/lib/eventos.ts`) invalida todas as consultas a cada
  aviso e a cada conexão aberta.
- `GET /api/v2/perfil` e `PUT /api/v2/perfil` (`api/v2/perfil.py`): `{"perfil": ...}` com
  os 5 perfis do quiz (`db/signup_quiz.PERFIS`), `"padrao"` (escolheu o painel padrão) ou
  `null` (nunca escolheu); grava em `auth_accounts.dashboard_profile`. O PUT é escrita:
  exige o CSRF do pai (cookie `csrf_token` + header `x-csrf-token`, 403
  `{"detail": ...}` fora do envelope), corpo fora da lista é 422 no envelope, e conta sem
  linha é 404 `conta_nao_encontrada`. O quiz continua recusando `"padrao"`.
- `GET /api/v2/contas` (`api/v2/contas.py`, regra em `db/contas_hoje.py`): o bloco de contas
  do Resumo — `total`, `motivos`, `fora_do_total`, `carteira {saldo, motivos}` e `contas[]`
  (`id`, `instituicao`, `nome`, `saldo`, `moeda`, `no_total`, `conexao` — um estado de
  `connection_ui_state` —, `sincronizado_em`, `motivos`). Mesmo recorte e mesmos critérios da
  foto do patrimônio (`db/patrimonio.py`), num snapshot só (repeatable read, só leitura):
  `total` = `calcular().carteira + calcular().bancos`. Conta desatualizada (sync > 48 h,
  pela metade ou nunca: `banco_desatualizado`) e conta fora do último sync
  (`conta_fora_do_ultimo_sync`) seguem no total; outra moeda (`outra_moeda`, saldo na moeda
  dela), conexão pausada/apagada (`conexao_pausada`, saldo `null`) e saldo
  ausente/malformado/NaN/±Inf (`saldo_ausente`, saldo `null`) ficam fora (`no_total: false`,
  contadas em `fora_do_total`). A carteira sai sempre com `carteira_nao_confirmada` até a
  Q37. `motivos` vazio = número exato. Cartão, posições e caixinhas não entram; `raw` e
  `provider_*_id` nunca saem.
- `GET /api/v2/resumo-do-mes?mes=AAAA-MM` (`api/v2/resumo_mes.py`, regra em `db/resumo_mes.py`):
  `mes`, `ate` (o último dia do mês, o corrente também: a soma cobre o mês inteiro), `entrou`, `saiu`, `anterior`
  (`{mes, entrou, saiu}` do mês anterior inteiro, ou `null` quando a janela do plano corta
  qualquer parte dele) e `motivos` (`conciliacao_pendente`, `movimentos_pendentes`,
  `banco_desatualizado` — os do bloco de contas: refletem a situação atual das contas, não
  a do mês pedido — e `inicio_do_historico`, quando a janela do plano corta o mês pedido ou
  o `anterior`). Sem `mes` = o mês
  corrente no fuso do app; formato fora de `AAAA-MM` ou mês futuro = 422 no envelope.
  **Regra única do mês** (Q18): `TOTAIS_SQL` = lançamentos não internos por `criado_em`
  (forma legada canonizada) + compras no cartão sem estorno pelo `period_end` da fatura,
  no mês-calendário INTEIRO (a fatura que fecha depois de hoje, dentro do mês, entra). Leem
  dela a rota, o "Gastos em <mês>" do WhatsApp (`core/handlers/balance.py`), o relatório
  mensal (o pedido na hora também soma o mês inteiro: total, contagem e "Período"), a
  consulta 5 do /app, `compute_kpis` das Análises e as tools da IA de mês inteiro
  (`get_period_summary` chama `resumo_do_mes`, inclusive o corte da janela do
  plano; `compare_periods` com `mes=` lê `totais`/`categorias` da mesma regra (PL-07); `get_top_categories`, `get_largest_expenses` e `get_category_spend` leem o mês inteiro, o
  cartão pela fatura, com o MESMO corte (`motivos: [inicio_do_historico]` quando o corte cai
  no dia 1 do mês pedido ou depois; o "mês anterior cortado" do resumo não tem par nas listas; mês todo
  cortado = lista vazia com as datas do mês); mês = dia 1 até o último dia, ou, no corrente, dia 1
  até hoje ou depois; `_meses_inteiros`/`_janela` em `core/services/ai_chat/tools/launches.py`).
  **Vários meses inteiros** (2 a 12) seguem a mesma regra, mês a mês: `get_period_summary`
  chama `db.mes_detalhes.ler_periodo` (a MESMA função do card `periodo` do chat, `api/nativo/mes_detalhes.py`:
  `meses[]`, `entrou`/`saiu` = Σ dos meses, categorias somadas e as 5 `maiores` linhas de cada; o
  `compare_periods` NÃO usa esta função: lê `resumo_mes.totais`/`categorias`, ver PL-07); no app,
  `get_top_categories`/`get_category_spend`/`get_period_summary` nessa faixa são recusadas com `periodo_no_card`
  (1 mês: `mes_inteiro_no_card`; no resumo a mensagem aponta `resumo` e o saldo = `contas`; a recusa é uma só, em `_recusa_se_for_do_card`, e devolve o dado quando o turno já gastou as 3 vagas de card, quando o card para o qual mandaria já foi mostrado, quando o `get_category_spend` leva uma `categoria` que o `show_card` deste turno já devolveu com `encontrada=false` (`CATEGORIAS_NAO_ACHADAS_DO_TURNO`, comparada pela `cat_key`, zerada a cada turno pelo runner; o número sai em texto, sem 2º card; outra categoria, ou uma que o card achou, segue a recusa normal), ou quando o plano corta o mês pedido (só 1 mês: nem o `resumo` nem o `categorias` trazem o `historico_desde`, a tool traz; 2 a 12 meses vão ao `periodo`, que traz); também devolve o dado a conta que não passa em `exigir_acesso_do_app` (sem onboarding de OF, sem plano) e o turno em que o `show_card` do card-alvo falhou (`FALHAS_DE_CARD_DO_TURNO` guarda o id do card; falha de outro card não libera), senão a recusa não deixa saída; o `card_repetido` aponta o caminho de cada card); no app, `compare_periods` de DOIS meses inteiros, diferentes e passados devolve `comparar_no_card` (`show_card categorias com meses=[A,B]`, `_recusa_comparar`; mês corrente, lado de mais de um mês, corte do plano e card que não resolve não são recusados: no app que declara `comparacao` viram o card `comparacao` (os que têm `despesa` e não são `sem_base`), sem ele seguem em texto); a recusa do `periodo` grava a janela em `JANELAS_DO_TURNO` e o `show_card periodo` usa a janela cortada por 12 meses quando o modelo diverge dela (`mostrar_card._da_recusa`); a visão que o modelo recebe dos cards `periodo`/`categorias`/`renda`/`calendario` é o RESUMO (total, saldo, 3 maiores categorias, `categorias_total`, maior e menor mês ou `maior_dia`; o bloco do app segue inteiro, `cards_resumos._RESUMOS`, as funções puras de resumo, fora de `cards.py` pelo teto de 350 linhas) e a categoria que o usuário citou vem em `categoria_pedida` pelo parâmetro `categoria` do `show_card`; no app o
  `get_open_bill` devolve período e as 3 maiores compras (`itens_total` = quantas são; `todos_os_itens: true` dá a lista) com `error: total_no_card` (o total é do card `fatura`) enquanto o card puder aparecer (`card_ainda_resolve`, o mesmo teste da recusa acima); quando não pode, devolve o total. **Teto de 12
  meses** (`MAX_MESES_PERIODO` em `cards.py`, uma constante para o `periodo`, o `renda`, o `get_spending_trend`
  e `_limita_periodo`): acima disso vêm os 12 mais recentes com `limitado_a_12_meses`, `pedido` e
  `aviso_limite`, nunca `error` (a mesma chave na tool e na visão do `mostrar_card`); período que entra no
  futuro termina no fim do mês corrente, com `fim_ajustado` e `aviso_fim`.
  **Card junto da tool (PR 3A; `compare_periods` e as listas na onda 2).** No app, `get_largest_expenses` (card `maiores_gastos`), `forecast_month_end`
  (`fim_do_mes`), `compare_periods` (`comparacao`), `list_recent_launches` (`ultimos_lancamentos`) e `get_uncategorized_launches` (`sem_categoria`) mostram o próprio card, sem `show_card` (`cards.mostrar_da_tool`; `cards.DA_TOOL` fica FORA de
  `cards.IDS`). O bloco é o resultado que a tool calculou, uma conta só (`fim_do_mes` sem `nota_metodologia` e
  `cartao_incluido`; traz `dias_corridos` e `dias_no_mes`); gasta uma vaga do turno e respeita o repetido e o
  `card_ainda_resolve`. Só sai se o corpo do `/ai/chat/stream` declarar o card em `cards` (`AIChatBody.cards`,
  `_context.CARDS_DO_APP`): o build 31 não manda o campo e recebe o dado de sempre. Com o card, o modelo recebe
  `card_na_tela: true` (`maiores_gastos`: resumo, sem `expenses`, com `total_dos_itens` (soma só dos itens listados, não o gasto do período), `maior`, `categorias`; `fim_do_mes`:
  o resultado inteiro). Sem emissor (WhatsApp, `/ai/chat`) a saída é byte a byte a de antes. No stream só o 🐷 do começo
  da resposta sai (`streaming.executar`/`completar`); o histórico gravado mantém o 🐷. O texto curto junto do card é só
  um pedido no prompt (`prompt_do_usuario(app=True)`), sem código que o imponha: quem mede é o staging. Tool de leitura sem card: `tests/test_ai_chat_tool_card.py` (`AUDITORIA`) lista uma a uma. Web: o /painel
  ainda não chama o stream (Etapa 7 do web).
  `comparacao` (onda 2, PR A): o bloco é o resultado de `compare_periods` sem `escolha` e `nota` (`modo`, `atual`/`anterior`, `base`, `despesa`/`receita`/`resultado` em `total_atual`/`total_anterior`/`total_delta`/`pct`/`estado`, até 10 `categorias` por |delta|, `categorias_omitidas`, `motivo`, `motivos`; só no `modo: mes_aberto`, `dias_corridos` (o dia de hoje) e `dias_no_mes`, para a legenda "dia 17 de 31", porque `atual.fim` é o fim do mês; dinheiro em float); o modelo recebe `cards_resumos.resumo_comparacao` (o mesmo resultado, só a 1ª categoria, com `escolha`, `nota` e `card_na_tela`). Sem card: `pro_required`, `error`, `inicio_do_historico` e `sem_base`; dois meses passados seguem a recusa para `categorias`. `serie` (só no card; o modelo e o `ai_guard` nunca a recebem): `{atual, anterior}` com o gasto (DESPESA, sem receita) acumulado por dia, `serie.atual[i]`/`anterior[i]` = dia `inicio + i`, reais em float, do mesmo filtro do `dias[]` do Resumo (`db.lancamentos.listar` sem interno e sem outra moeda). Garantia: a última posição é o `total` da `despesa` do lado, senão `serie: null` (outra moeda, falha da leitura, ou período livre, que lê o cartão pela data da compra e não tem série). Compra de cartão anterior ao período, na fatura dele, cai no dia 1; a compra dentro da janela entra no dia da compra. O lado que contém hoje (o `atual` ou o `anterior`, conforme a ordem em que o modelo pediu) para em hoje e sobe em degrau até um lançamento de data futura, então o app não pode assumir `len == dias_corridos`; o outro lado tem `dias` posições. O app o desenha em `app/src/features/painel/cardComparacao.tsx`: o delta de despesa com sinal, o chip de %, as duas linhas acumuladas sobrepostas (reaproveita `CurvaAcumulada`; o eixo X é o MAIOR dos dois períodos e a série mais curta só acaba antes) e as 3 categorias que mais mudaram, com o ícone vindo do catálogo do usuário (`/api/app/categorias`, o `dados.categorias` do provider), inclusive o da categoria que zerou; `serie: null` desenha o card sem gráfico. O servidor só o emite a quem o declara em `cards`. Fixture: `tests/fixtures/ai_chat_bloco_comparacao.json`.
  `ultimos_lancamentos` e `sem_categoria` (onda 2, PR C2): o bloco é `{itens, motivos}`, sem total, com até 10 itens (o app mostra 5 e um "+5") no formato `Lancamento` de `/api/v2/lancamentos`
  (`mostrar_itens` em `core/services/ai_chat/tools/listas_ia.py`; fixtures `tests/fixtures/ai_chat_bloco_{ultimos_lancamentos,sem_categoria}.json`). O modelo recebe a lista INTEIRA (até `limit`, 50) com `card_na_tela: true`,
  porque precisa do `id` para "apaga o segundo"; o item dele é `_para_ia` (`valor` float, `tipo` receita/despesa ou entrada/saída para o interno, `categoria` pelo nome do catálogo (fora dele, como foi gravada), `parcelamento` em texto pronto (nunca o `parcela` cru), `user_seq` só nos `l`). A confirmação de APAGAR
  (`delete_launch`, `Tool.ao_armar`) mostra o item, 1 linha, no card `ultimos_lancamentos`: o card sai DEPOIS de a pendência estar armada (com pendência viva de outro pedido nada sai), só ao app que o declara, com vaga; no mesmo turno da lista
  o card já está na tela e a regra do repetido não emite outro. `recategorize_launch` não ganha card (reversível; o resumo dela já nomeia o item). O app os desenha (`app/src/features/painel/cardLancamentos.tsx`, PR B).
  A lista é a do Extrato (`db/lancamentos_leitura.recentes` = `listar`, 12 meses): os itens em comum com a 1ª página de `/api/app/lancamentos` são idênticos campo a campo
  (`tests/test_ai_chat_listas.py`). **Três divergências declaradas:** a janela vai até o fim do mês SEGUINTE (a compra no cartão feita depois do fechamento entra pela fatura do mês que vem, e o Extrato só a mostra lá); o
  guardado em caixinha/investimento entra (`interno`, `guardado`, `pode: []`); e o parcelamento vem em UMA linha por data, a da menor parcela (`listar(uma_por_compra=True)`, depois do filtro `sem_categoria`: no Extrato cada parcela é a
  linha da sua fatura; o manual grava a data da compra em todas e as de faturas futuras repetiriam a compra, então vira uma linha; no Open Finance cada parcela é uma cobrança com data própria e cada uma aparece; recategorizar o `c<id>` já propaga ao grupo). `sem_categoria` = saída não interna com categoria vazia ou "outros", cartão incluído (`listar(sem_categoria=True)`).
  No WhatsApp (sem card) o texto da lista muda: entram as compras no cartão e o guardado, a data é a do Extrato e o `#N` vem de `user_seq`. O comando determinístico "últimos N" do WhatsApp não muda.
  Limites da IA: (a) no mês corrente, QUALQUER janela dia 1..X com X >= hoje (a do dia 1 até
  hoje, mas também 1..20) é lida como o mês inteiro, então "quanto gastei hoje" no dia 1 vira o
  mês corrente inteiro; (b) mês futuro: é o número da regra (`resumo_do_mes`), só o que já está lançado
  (parcelas do cartão, lançamentos com data futura), igual nas listas; sai com `motivos: [mes_futuro]`
  e a nota "não é previsão", sem `error` (o turno do agente cai em `DataQueryError` com `error`);
  `forecast_month_end` só projeta o mês corrente; (c) período livre não corta pela janela do plano; data
  inválida é erro (`data inválida: <campo> (use AAAA-MM-DD)`), não "hoje"; `2026-10-01T00:00:00`
  e espaços em volta valem como a data, só em `launches.py`: `investments.py:35` tem cópia própria
  de `_parse_iso_date` (lá datetime ISO e data inválida ainda viram o mês corrente em silêncio;
  follow-up). (d) com o corte do plano no MEIO do mês, as listas devolvem `start_date = corte`; o
  "mais detalhes" (#931) chama `get_largest_expenses` de novo com esse início, `_meses_inteiros` dá None
  e vira período livre (data da compra, sem corte). (e) `inicio_do_historico` nas tools só sai com
  corte >= dia 1 do mês pedido (o resumo da rota também o levanta pelo mês anterior cortado); no resumo de
  2+ meses o motivo segue o `>` do painel (`ler_periodo`), nas listas o `>=` (limite declarado).
  `compute_evolution` é cópia em consulta
  única (um GROUP BY por mês); `tests/test_resumo_mes_regra.py` compara as duas por mês.
  `credit_bills.user_id` é NOT NULL (backfill pelo dono do cartão, #772): a fatura só entra
  se for do usuário (`b.user_id = %s`), em cada perna do cartão.
  **Divergência conhecida:** relatório diário e semanal
  (`get_summary_by_period`) e o Repórter (`piggy_agents._month_stats`) seguem só em `launches`,
  sem o cartão. Das tools da IA em período livre (não são meses inteiros), `get_period_summary`
  soma `launches` + cartão pela DATA DA COMPRA (Σ de `get_top_expense_categories`,
  a mesma consulta da lista; saem com `cartao_por: data_da_compra` e uma `nota`; meses inteiros saem com
  `cartao_por: fatura`); `get_top_categories` e `get_largest_expenses` incluem o cartão pela DATA DA COMPRA,
  `get_category_spend` lê o cartão sempre pela fatura (`period_end`) e `get_spending_trend` soma o
  cartão pela data da compra. Pré-existente, não corrigido: `get_top_categories` mostra lançamento
  sem categoria como `outros`, mas `get_category_spend("outros")` só soma a categoria `outros`; o
  sem categoria fica em "sem categoria". Limites mantidos de
  propósito: o mês corta `criado_em` pela data ingênua (fuso da sessão do Postgres), a
  conciliação pendente conta em dobro (sai com motivo), estorno não abate.
  **Semanal (PL-05):** `build_weekly_report_data` (`core/reports/weekly.py`, reexportada por `reports_daily`) é o
  payload tipado (Decimal/date); `build_weekly_report_summary` o adapta para texto e mantém as 6
  chaves do template da Meta. Bancos e comparação são extras: se falharem, o resumo sai sem eles
  (`bancos=None`, `comparacao_motivo="indisponivel"`). No job do WhatsApp, build que falha depois do
  claim devolve a semana até 10 vezes (≈5 min, contador em memória); na 10ª a semana se perde. Se o próprio release falha (banco fora), ele fica pendente e é
  repetido no tick seguinte, antes do claim e sem contar como tentativa.
  Mantém a regra de `get_summary_by_period` e expõe `cartao_incluido=False`
  (o texto avisa que compra no cartão não entra). A "semana equivalente" é n dias contra n dias
  (`_semana_equivalente`): fechada = seg→dom contra seg→dom da retrasada; parcial = segunda até
  hoje contra a segunda anterior até o mesmo dia da semana. `lancamentos` conta só despesa/receita
  não internas (o mensal ainda conta toda linha de `launches`: diferença conhecida). O template
  homologado (5 variáveis) não muda; o texto rico vai no pedido manual e no Discord.
  Decisões do dono (PL-05): o envio é na segunda, no horário do resumo diário no WhatsApp (o
  Discord roda fixo às 09:00 e não lê a preferência); dia e horário próprios não foram feitos, por decisão; compra no cartão fica fora do semanal, com aviso no
  texto; o template da Meta segue com 5 variáveis (o texto rico só no pedido manual e no Discord;
  levá-lo ao WhatsApp automático depende de template novo aprovado na Meta); a comparação com a
  semana anterior é só Plus+; não há saldo livre calculado (o texto separa "Resultado da semana"
  de "Saldo atual nas contas"). Limites conhecidos: um par pendente de conciliação (lançamento
  manual + movimento do Open Finance ainda não fundido) conta em dobro e o semanal não avisa; falha
  de `send_template` consome o claim da semana; o resumo mensal (WhatsApp e Discord) não tem a mesma
  proteção de build que falha depois do claim.
  `scripts/comparar_resumo_mes.py` compara antigo × novo por usuário e mês, só lendo.
  **Comparação de períodos (PL-07, Plus+):** `core/services/comparacao_periodos.py` decide a janela e o
  delta; a tool `compare_periods` da IA é o único consumidor (o endpoint e a tela v2 são o PR B). Lê a
  regra única só no caminho `mes=` (`totais` + `categorias` de `db/resumo_mes`, cartão pela FATURA; janela parcial
  = faturas que fecham dentro dela). Com as 4 datas decide o adaptador, pelo mesmo critério da main (`_meses_inteiros`, depois de `_limita_periodo`):
  se AS DUAS janelas são meses-calendário inteiros (dia 1 até o último dia; no mês corrente, dia 1 até hoje ou
  depois, e então vale o mês TODO) é a mesma conta de `mes=` e do painel, cartão pela FATURA ("compara abril com
  maio" não diverge do painel); exceção: mês CORRENTE (dia 1 até hoje ou o mês todo) × o mês imediatamente anterior
  inteiro responde como `mes=<corrente>` (o mês inteiro, fatura; o resultado é o mesmo do chat). Senão é período livre nas DUAS janelas (nunca uma base de cada lado): reusa as
  consultas das outras tools (`get_top_expense_categories`, `get_summary_by_period`), cartão pela DATA DA COMPRA, e a
  compra parcelada (mesma `purchased_at` nas parcelas) entra inteira no dia da compra; o número pode diferir do painel; categoria sem nome sai "outros" (a consulta da main já agrupou
  vazio com o "outros" real) e em `mes=` sai "sem categoria". `base.cartao`
  (`fatura` | `data_da_compra`), `base.regra` e `escolha` dizem qual. Teto de 12 meses e fim ajustado:
  `_limita_periodo`. Mês aberto = o mês corrente INTEIRO (o já lançado, inclusive a fatura que fecha depois de hoje e o lançamento
  com data futura) contra o anterior inteiro: `entrou`/`saiu`/`anterior` iguais ao `resumo_do_mes` (decisão do dono, P1-B,
  2026-10-17); a `escolha` avisa que o mês não acabou. `anterior_encurtado` fica sempre False (contrato). Mês fechado = mês inteiro × mês inteiro. Categoria nova tem
  `pct=None`; sem gasto agora, `-100.0`; nunca +100%. O gate `financial_comparison` é a primeira instrução
  do serviço; o `anterior` de `GET /api/v2/resumo-do-mes` não muda (Essencial segue vendo os dois totais).
  Saída da tool: dinheiro e `pct` são NÚMERO JSON (o `ai_guard` só lê número como evidência; mesma convenção de
  `_resumo_periodo`) e moram em `total_atual`/`total_anterior`/`total_delta` (o guard só lê número sob chave
  monetária; `atual`/`anterior`/`delta` soltos não contariam). `motivos` + `nota` são os avisos de dado incompleto do
  resumo do mês (`contas_hoje.listar`, mesma ordem de `resumo_mes.MOTIVOS`, na mesma transação); o `motivo` (singular)
  é outra coisa: por que o payload veio limitado (`sem_base`, `inicio_do_historico`, `categorias_indisponiveis`).
  Período que ainda não terminou: `escolha` diz que só entra o já lançado (parcelas e datas futuras), não é previsão.
  Casamento de categoria no período livre: `_chave` (Python) espelha `cat_key_sql` em nomes PT-BR (acento, caixa,
  cedilha, til; `tests/test_comparacao_periodos.py` compara os dois); casos exóticos de Unicode (Ñ, Å, NFD,
  ligaturas) podem diferir, e não se tentou igualar.
  Divergência conhecida: `get_spending_trend` soma o cartão pela data da compra, então a tendência de um mês pode
  diferir da comparação por `mes=` (fatura) na mesma conversa; `get_period_summary` e a comparação por datas
  parciais concordam (as duas, data da compra). No período livre o rótulo da categoria é a grafia de MAIOR total.
- `GET /api/v2/orcamento-domestico?mes=AAAA-MM`, `POST .../potes` e `POST .../renda` (`api/v2/orcamento_domestico.py`, regra em `db/household_budget.py`, que segue em float; a fronteira converte para `Decimal` de 2 casas e tudo sai como TEXTO): o Orçamento doméstico (6 potes) do `/painel#/orcamento` (cabeçalho com "Gasto fixo" e "Editar percentuais"; "Visão geral do mês" com donut e legenda em Gasto|Orçado, "Renda do mês" com a origem e a tabela "Potes" (cards no celular); clicar na linha abre o modal com os `lancamentos` do pote; sem rodapé de total: o `total.restante_livre` segue na API e o painel não o mostra. **Paleta própria do /painel**: ícone e cor de cada pote vêm de `webapp/src/dashboard/lib/potes.ts` (por `chave`; chave desconhecida cai na `cor` do servidor e no círculo), e NÃO de `BUCKETS`, cuja cor o /app antigo lê e não pode mudar; Metas e Liberdade financeira são potes "guardados": passar do teto não é estouro, texto "Guardado" — a regra é do servidor: `guarda` em `BUCKETS` (`db/household_budget.py`) vira `guardado` no pote, e `total.restante_livre` = Σ restante dos não guardados + Σ max(restante, 0) dos guardados, enquanto `total.restante` segue o líquido; o /painel não exibe nenhum dos dois totais), fora do menu lateral e do Menu do celular (só o card de Ferramentas e o ⌘K). Gate `household_budget` (Plus/Pro): Essencial/Free = 403 `{"error":"pro_required","feature":"household_budget"}` nas 3 rotas, sem gravar. Mês: GET sem `mes` = o corrente no fuso do app, formato errado ou futuro = 422 (`mes_pedido(mes, onde)` de `resumo_mes.py`, `loc` `query` ou `body`); o módulo SEMPRE recebe o mês explícito (sem ele usa o `date.today()` do servidor). Resposta `OrcamentoDomestico` `{mes, renda {valor, origem "calculada"|"informada"}, potes[6 em ordem fixa: chave, nome, cor, pct, teto, gasto, restante (negativo = passou), usado_pct|null, categorias[str] (nomes de exibição das categorias do pote), por_categoria[{categoria, total}], lancamentos[{data AAAA-MM-DD, descricao, categoria, valor, origem "conta"|"cartao"}] (do mês, mais novo primeiro; somam o `gasto`)], total}`; `usado_pct` nulo = teto zero (renda 0, que é válida). `POST /potes` `{mes, potes[{chave, pct "55" ou "33.33"}]}`: chave repetida, pote faltando/desconhecido, pct fora de 0–100 ou soma ≠ 100 (em centésimos) = 422 em `body.potes`, tudo-ou-nada; os % são globais (não por mês). `POST /renda` `{mes, valor: "1234.56"|null}` (`valor` obrigatório e anulável; `null` volta à renda calculada; "0" vale; vírgula e número JSON = 422): o override é por mês. Cada POST devolve o `OrcamentoDomestico` do mês. O 1º GET de quem nunca abriu grava os % padrão (um aviso "tudo" no SSE; as tabelas já estão em `TABELAS_QUE_AVISAM`). Divergências que existem e não foram corrigidas: o "gasto" dos potes NÃO é o "Saiu" do Resumo (inclui aportes, filtra por `date_part(criado_em)` e não exclui outra moeda); a renda calculada soma só `launches` de receita do mês. `/api/app/orcamento-domestico*` existe (o app nativo reaproveita `criar_app`) mas nenhuma tela do app o chama.
- `GET /api/v2/patrimonio` (`api/v2/patrimonio.py`; também `/api/app/patrimonio`, montada pelo `criar_app`; `api/nativo/patrimonio.py` só reexporta `patrimonio` e `Patrimonio` para o card do chat): o patrimônio de hoje (`db/patrimonio.calcular`), as fotos diárias (`patrimonio_fotos`) e, desde a Etapa 6 (PR A), `trechos[{desde, ate}]` (crescente, nunca vazio, o último termina em hoje) e `variacao{valor, desde, ate}|null`. Datas em texto ISO, dinheiro em centavos (`api.nativo.dinheiro.Dinheiro`). `historico` = as fotos dos últimos 12 meses (`DIAS_MAX`), ou menos se a janela do plano for menor. O `/api/app/patrimonio` ganhou `trechos` e `variacao` e passou a ter o mesmo teto de 12 meses (a rota antiga só cortava pela janela do plano; sem efeito hoje, o histórico começa em 01/10/2026). **Regra da quebra** (`db.patrimonio.quebra(a, b)`, pura, sobre a `base` de dois pontos consecutivos): a linha quebra se mudar `v`, a contagem de contas e posições em outra moeda (`fora.moeda`) ou o conjunto de CONTAS, ou se mudarem as conexões VIVAS (fora `paused`/`removed`) E as posições ao mesmo tempo; base ilegível quebra dos dois lados. Estado não terminal que muda, CDB novo/resgate, e reconexão com os mesmos ids não quebram. Conta BRL nova no mesmo banco quebra (decisão do dono, P1). Pontos da série = fotos com `dia < hoje` + o ponto AO VIVO de hoje (a foto de hoje sai da série, não duplica o dia, mas fica em `historico`); `variacao` = total de hoje menos o do 1º ponto do trecho atual, diferença dos totais exibidos (até 1 centavo da exata); `null` quando o trecho atual tem um ponto só (nunca 0 inventado); zero real sai `"0.00"`. Uma transação `repeatable read, read only`. Limites declarados: reconexão com ids novos quebra a linha mesmo com total igual (comparar por `institution_id` faria o passado mudar quando a conexão é apagada); banco só de investimento cuja 1ª coleta cai depois da foto do dia faz o salto entrar na variação; troca de moeda compensando a contagem, só em posições, não quebra; um buraco de dias sem foto esconde pausa e volta; o estado `paused`/`removed` também pode vir do `status_reason` (`core/services/pluggy_health.py`, `_LABELS`), mas hoje ninguém grava esses valores e o teste liga só `_TERMINAL` → estado; a foto de hoje que quebrou com o hoje ao vivo aparece no trecho [hoje]. Testes: `tests/test_patrimonio_quebra.py` (regra e montagem puras), `tests/test_api_v2_patrimonio.py` (banco real), fixtures em `tests/frontend/api_v2_respostas.json["patrimonio"]`. Tela web: PR B.
- `GET /api/v2/contas-a-pagar` (`api/v2/contas_a_pagar.py`) e `GET /api/v2/caixinhas` (`api/v2/caixinhas.py`): os dados dos cards do Carteiro e do Banqueiro no `/painel`, só LEITURA, e a MESMA regra da consulta do agente (`_get_bills_to_pay(uid, {}, sync=False)` e `_list_pockets(uid, {}, accrue=False)`, de `core/services/ai_chat/tools/`; nenhuma regra reimplementada). Ler NÃO gera instância do próximo ciclo (ao contrário da rota antiga `/recurring-bills`) nem aplica juros: o saldo é o último registrado. Dinheiro sai como TEXTO (float do módulo vira `Decimal` de 2 casas), sem `provider_*_id` nem `raw`. `ContasAPagar` `{hoje, pendentes, vencidas, total_estimado, tem_variavel, contas[id, nome, categoria, vence_em, dias (0 = hoje, negativo = vencida), valor|null (com `variavel`, só a estimativa), variavel, vencida]}` (só as pendentes, até 200, a mais atrasada primeiro); gate `recurring_expenses` (Essencial+, o mesmo do `/recurring-bills`), 403 `pro_required`. `Caixinhas` `{total, caixinhas[id, nome, saldo, meta|null, progresso_pct|null, atingida|null]}` (do maior saldo; progresso limitado a 100); sem gate de plano (o Grátis tem caixinha). Não confundir com `/api/v2/metas` (`db/metas`). Montadas no `criar_app`, então também em `/api/app`; nenhuma tela do app as chama. Testes: `tests/test_api_v2_contas_caixinhas.py`.
- `GET /api/v2/orcamentos-categoria`, `POST .../orcamentos-categoria` (cria ou atualiza `{categoria, valor "1234.56"}`) e `POST .../orcamentos-categoria/apagar` (`{categoria}`) (`api/v2/orcamentos_categoria.py`, regra em `db/budgets.py`; float vira `Decimal` de 2 casas e tudo sai como TEXTO): os Orçamentos por categoria do `/painel#/orcamentos-categoria`, fora do menu lateral e do Menu do celular (só o card de Ferramentas e o ⌘K), sem seletor de mês. **Só o mês corrente** (fuso do app; o GET não recebe `mes`, o módulo recebe o mês explícito). **Sem gate de plano**: o Grátis usa, com teto `plan_limits.budgets_max` (3; `None` nos pagos), a ÚNICA fonte do limite, aplicada em `db.upsert_budget(..., limite=)` (só o INSERT conta; atualizar nunca conta; casa categoria sem caixa/acento, então não nasce gêmea). No v2 o estouro é 403 `{"error":"plan_limit"}` (o `detail` extra se perde no envelope, por isso o `limite` vem no GET); a rota antiga `POST /budgets/{uid}` usa o mesmo `upsert_budget` e mantém o corpo legado 403 `{"error":"pro_required","feature":"budgets_limit","limit":N}` que o `dashboard.js` lê. Categoria nova tem de ser do catálogo do usuário (`resolve_category_input(create=False)`, senão 422 em `body.categoria`); editar um orçamento que já existe vale mesmo fora do catálogo. Valor ≤ 0, vírgula, número JSON ou fora de `^[0-9]{1,9}(\.[0-9]{1,2})?$` = 422 em `body.valor`; apagar o que não existe = 404 `not_found`. Resposta `OrcamentosCategoria` `{mes, limite|null, orcamentos[categoria (a grafia gravada, identidade), chave (cat_key), emoji, icone, cor, teto, gasto, restante (negativo = passou), usado_pct, status verde|amarelo|vermelho], total{teto, gasto, restante, usado_pct, em_risco}}`; cada POST devolve a lista nova. Divergências que existem e não foram corrigidas: o "gasto" NÃO é o da página Gastos (usa `tipo in (despesa, saida)` por `date_part(criado_em)` e soma o cartão pelo mês da fatura) nem o do alerta do WhatsApp (`core/budget_alerts.py` soma só `launches`); gêmeas antigas (`cafe` e `café`) aparecem como duas linhas (editar atualiza a canônica, apagar remove as duas); a IA (`set_budget`) ainda não respeita o teto do plano; count + INSERT sem lock deixa N POSTs simultâneos passarem até N-1 acima do teto (a trava de duplo clique cobre uma aba só); o teto conta linhas, não `cat_key` (gêmeas antigas contam duas); NUL/tamanho na rota antiga é herdado; o `voando` do form é redundante com a trava da mutação. Orçamento nas categorias internas (`investimento_aporte`, `criptomoedas`, `rendimentos`, o mesmo conjunto de `utils_text.is_internal_category` / `core/budget_alerts.INTERNAL_CATEGORIES`) é aceito, como no dashboard antigo: nunca acumula gasto (o status soma só despesa) nem alerta. Categorias arquivadas aparecem no `<select>` (`/categorias` não filtra). Rebaixamento: quem fica com mais orçamentos que o teto edita e apaga, mas não cria. A rota antiga `POST /budgets/{uid}` mudou: devolve a grafia canônica (mandar "Alimentação" com "alimentação" gravada atualiza, não cria gêmea) e categoria só de espaço dá 400. O donut do `/dashboard` antigo pode atrasar até 45 s e os insights até o TTL do `ai_proactive_cache`; faixas de alerta já enviadas (`budget_alert_sent`) não disparam de novo ao apagar e recriar ou subir o teto no mesmo mês. O INSERT é `on conflict` (POSTs paralelos da mesma categoria nova não dão 500). `/api/app/orcamentos-categoria*` existe (o app nativo reaproveita `criar_app`) mas nenhuma tela do app o chama.
- `POST /api/v2/gastos-fixos` (`api/v2/gastos_fixos.py`, grava por `db.recurring.create_recurring_expense`, sem regra nova): o "+ Gasto fixo" do `/painel#/orcamento` (não aparece no DEMO). Corpo `{nome 1..80, valor "1500.50", dia 1..31, categoria, forma "conta"|"cartao", cartao_id}`; grava mensal e `autopay`. Gate `recurring_expenses` (Essencial+): 403 `{"error":"pro_required","feature":"recurring_expenses"}`. Categoria tem de ser do catálogo do usuário (`resolve_category_input(create=False)`, senão 422 em `body.categoria`); `forma="cartao"` sem `cartao_id` ou com cartão de outro usuário = 422 em `body.cartao_id`, nada gravado. Valor ≤ 0, vírgula ou fora de `^[0-9]{1,9}(\.[0-9]{1,2})?$` = 422. Resposta `{id, nome, valor (texto), dia, categoria, forma}`. **Limite:** o /api/v2 não tem leitura de cartões, então o formulário só oferece débito em conta (a rota já aceita cartão). O gasto entra na Previsão; só conta no pote quando o pagamento vira lançamento.
- `GET /api/v2/assinaturas` e `POST /api/v2/assinaturas/marca` (`api/v2/assinaturas.py`):
  a lista do Recurring Payments da Pluggy (`core/services/assinaturas.py`) e a marcação
  do usuário por chave do comerciante (`assinatura`/`ignorar`/`nenhuma`; chave fora da
  lista dá 404). As chaves ignoradas saem em `ignoradas`, fora do total e só para a tela
  (Detetive e chat leem `servicos + outras`); nela, `marcada` é a marca guardada, que o
  "Voltar a mostrar" restaura. Gate `subscriptions` em `FEATURE_MIN_TIER_V2` (Plus ou Pro). O cliente é
  `webapp/src/dashboard/widgets/Subscriptions.tsx` (o card do Resumo e a página
  `/assinaturas`); o POST sai pelo `apiPost` de `lib/v2.ts`, com o header de
  `window.pbCsrfHeaders` (auth-refresh.js), e o 403 `pro_required` vira o convite.
- `GET /api/v2/lancamentos` (`api/v2/lancamentos.py`, regra em `db/lancamentos.py`): só
  leitura. Query `mes` (como em `resumo-do-mes`), `origem`, `conta` (o `id` de `/contas`),
  `cartao`, `categoria` (a chave; `sem categoria` pega `NULL`, vazio e a de mesmo nome, também a compra de cartão), `tipo` (`entrada`|`saida`), `dia` (`AAAA-MM-DD`, filtra pelo `data` devolvido; é texto e não `date` porque o gerador de tipos recusa `format: date` em query; inválido como `2026-02-30` = 422; leve sempre junto o `mes` da fatura, que é o que pega a compra de cartão de outro mês), `q`, `cursor`, `limite`
  (padrão 50, acima de 100 corta, abaixo de 1 é 422). Resposta `{mes, itens, proximo, motivos}`.
  **O mês é o de `TOTAIS_SQL`**: mesma janela (`db/resumo_mes.MES_JANELA_SQL`) e mesma regra
  de totais (`TOTAIS_SQL`/`CATEGORIAS_SQL` seguem em `MES_LANCAMENTOS_SQL`; a lista só
  acrescenta os tipos guardado, que nunca entram em total), e a perna `MES_CARTAO_SQL`: a soma dos itens não internos é o Entrou/Saiu do Resumo; o cartão
  entra pelo mês da fatura com a data da COMPRA; o interno entra com `interno: true`, fora
  de qualquer soma. **Guardado (P8, #983) é OPT-IN: só `?guardado=1` o inclui; sem o parâmetro a lista é a de antes
  da P8** (nenhuma linha guardado), porque `api/nativo/app.py` monta este mesmo router em
  `/api/app/lancamentos`: o app nativo não conhece o campo e apps instalados não se atualizam
  com o deploy do backend (o `Extrato` pintaria o depósito de despesa e o saque de receita, e os
  filtros Entradas/Saídas devolveriam movimento interno). O `/api/app` herda o default e não
  manda o parâmetro até um PR do app aceitar `guardado`; o `/painel` manda sempre
  (`lancamentosQuery`, `webapp/src/dashboard/lib/v2.ts`); `mes_detalhes` lê sem ele. Com o
  parâmetro, depósito de caixinha e aporte entram como
  `saida`, saque de caixinha e resgate como `entrada`, valor BRUTO, `guardado: true` e
  `interno: true` (forçado: guardado ⇒ interno), fora de toda soma e agregado (nada de
  `resumo-do-mes` nem `mes-detalhes` muda; o bloco `guardado` de `mes-detalhes` segue vindo
  de `launches` pelos mesmos tipos, `lancamentos.GUARDAR`/`RESGATAR`); `categoria=` nunca os
  devolve; `pode: []`, `conta_id: null`, origem `registro_antigo`. `criar_caixinha` & cia
  seguem fora. Limites: depósito pago pelo banco gera 2 linhas fora do total (a do banco e a
  guardado) sem vínculo; `conta=` e `origem=carteira` não pegam guardado; caixinha do banco
  (Open Finance) não gera guardado. Com `q` (até 6 palavras de 2+ letras, `unaccent`; `db/analytics.termos_busca`/
  `clausula_busca`, as mesmas do `list_history`) a lista varre a janela inteira do plano em
  vez do mês. Janela do plano cortando = `inicio_do_historico`. Página por KEYSET (cursor
  opaco: base64 de `[dia, instante, tabela, id]`, adulterado = 422), sem contagem total.
  Item: `id` (`l<n>` lançamento, `c<n>` compra no cartão: as tabelas compartilham o espaço de
  ids), `data` (`launch_day`) e `hora` (ou `null`), `tipo`, `interno`, `guardado`, `valor` (texto),
  `moeda` (a da conta do banco ligada), `descricao`, `mensagem` (a nota do usuário),
  `categoria` (a chave `cat_norm_sql`, a mesma de `GET /api/v2/categorias`), `origem`,
  `fundido` + `instituicao`, `conta_id`, `cartao_id`, `parcela {n, total}`, `fatura`
  (`AAAA-MM`), `pode` e `motivos` (`conciliacao_pendente`, `transacao_pendente` = o banco
  diz PENDING, `outra_moeda`, `moeda_presumida`). **`origem`**: `carteira` (manual com
  `delta_conta` ≠ 0 e com a marca `launches.origem`), `banco` (sombra do Open Finance),
  `cartao` (compra do cartão do Open Finance) e `registro_antigo` (o resto: manual sem a
  marca, manual com delta 0, OFX, compra manual no cartão). **Desde o PR-A da [ADR 0006](adr/0006-declaracao-bancaria-despesa-receita.md) (#969, mergeado em 2026-10-15):** `banco` inclui também a despesa ou receita que o usuário declarou como paga pelo banco (`DECLARADA_SQL`, `db/bank_declaracoes.py`; o resto do “manual com delta 0” continua em `registro_antigo`); `fundido` vale “casada com o extrato”, e a linha sem par tem o motivo `conciliacao_pendente` e `pode = [categoria, descricao, apagar]`. A tela precisa tratar `instituicao` nula nessa linha sem par. Nenhum caminho de produção cria essa declaração até o PR-B. **`pode`** é uma regra só
  (`db/lancamentos.PODE_SQL`): guardado = `[]` sempre (1ª cláusula, vence todas as outras,
  inclusive P5 e `source='open_finance'`; editar/apagar dão 409 `nao_editavel`, e essa é a
  guarda de escrita, `pode_da_linha`); antigo = `[]` (P2: só leitura no v2); banco e cartão do Open
  Finance = categoria e descrição (P5); carteira marcada = tudo, menos data e valor se fundida
  ou em par pendente com o banco (P3), só descrição e apagar se ligada ao dinheiro em espécie
  (Q41), só categoria e data se paga conta ou fatura, e só categoria se além disso fundida
  (sem apagar no v2: apagar o pagamento de
  conta devolve o dinheiro e a conta segue paga; o de fatura do cartão manual cai no mesmo ramo,
  `bill_id`, e sai junto; o `/app` e o WhatsApp seguem apagando; a IA segue este `pode` e NÃO apaga compra manual
  no cartão, guardado nem pagamento de conta, nem recategoriza o ligado a dinheiro em espécie). `launches.origem` (NULL =
  antigo, sem backfill) vale `'carteira'` (`db.accounts.ORIGEM_CARTEIRA`) e quem grava é o
  escritor da carteira, por padrão, em todo canal (`/app`, WhatsApp, quick_entry, IA, saldo
  inicial e ajuste, pagamento de fatura pela carteira, a v2) e o saque/depósito automático da
  Q41 (`open_finance_cash._credita`); o pagamento de conta pela carteira (`mark_bill_paid`)
  nasce sem a marca e a ganha no mesmo statement que o liga à conta (antes disso o v2 o
  apagaria como carteira pura); antecipar parcela e o estorno de fatura do
  cartão manual gravam `origem=None` (o `efeitos` não guarda o que desfazer), e OFX, sombra do
  Open Finance, caixinha e aporte não passam pelo escritor. `conta`/`cartao` de
  outro usuário dão a lista vazia, igual a id inexistente; `raw`, `provider_*_id`,
  `external_id` e o id da transação do banco nunca saem. **Divergência declarada:** a lista do
  `/app` (`list_history`) e as consultas 3 e 4 / "últimos N" do WhatsApp seguem com as
  regras delas (data de gravação no cartão, sem o interno); a IA (`list_recent_launches`) usa esta lista,
  com a janela até o mês seguinte e o guardado (ver "Card junto da tool").
- `POST /api/v2/lancamentos/carteira`, `/lancamentos/editar` e `/lancamentos/apagar`
  (`api/v2/lancamentos.py`, PR 2a): a escrita, `def` síncronas, id no corpo, CSRF do pai (403
  `{"detail"}` fora do envelope), resposta `{id}`. **carteira**: `tipo` (`entrada`|`saida`),
  `valor` (texto `^[0-9]{1,9}(\.[0-9]{1,2})?$` e > 0), `descricao` (1 a 200, vira `alvo` e a
  nota), `categoria` (opcional; `null` ou ausente = inferida como no `/app`; `""` = 422, onde o
  `/app` infere), `data` (`AAAA-MM-DD`, entre o
  corte do plano e hoje; a hora é a de agora). Sempre dinheiro na Carteira (Q40: o v2 não
  recebe forma de pagamento), teto do plano = 403 `plan_limit`; o miolo é
  `core/services/carteira.lancar`, o mesmo do `POST /launches` do `/app`. **editar**: `id`
  (`l<n>` ou `c<n>`) e ao menos um de `categoria`, `descricao`, `data`, `valor` (PR 2b-1: o
  mesmo texto e > 0 da criação; só a carteira pura tem 'valor' no `pode`, e o saldo da Carteira
  anda pela diferença na mesma transação, então apagar depois desfaz exato; `c<n>` = 409),
  cada um contra o `pode` da linha. A data da linha fundida com o banco
  (`db/lancamentos.FUNDIDO_SQL`) é travada em TODO canal por `update_launch_fields` (o PATCH
  /launches do `/app` dá 409 com frase própria; o v2 dá 409 `nao_editavel`); **apagar**: só `l<n>` (cartão = 409). Apagar a linha fundida desfaz a junção na hora, em
  todo canal (PR 2b-2): a transação do banco volta como linha `banco` (`reconciliation._desfaz`,
  chamado por `delete_launch_and_rollback`). O v2 responde `{id}` sem aviso; WhatsApp, IA e
  `/app` (chave `aviso` do DELETE /launches) mostram a frase "A transação do banco (R$ X, DESC)
  continua na sua lista…"; o "apagar tudo" não desfaz. O `pode` é relido por
  `db/lancamentos.pode_da_linha` dentro da transação da escrita, depois do lock do usuário
  (`_lock_user`) e da linha (`exigir_pode=True` em `update_launch_fields`,
  `delete_launch_and_rollback` e `update_credit_transaction_fields`, este pelo
  `PODE_CARTAO_SQL`). Erros: 404 `lancamento_nao_encontrado` (o MESMO corpo para id de outro
  usuário e inexistente), 409 `nao_editavel` (fora do `pode` e as recusas de domínio do apagar,
  sem motivo no corpo), 422 `validation_error`. Sem chave de idempotência (dois POST iguais
  gravam dois).
  A IA apaga e recategoriza pelo MESMO `pode` (`core/services/ai_chat/tools/extrato_ia.py`, `delete_launch` e
  `recategorize_launch`): o id da escrita é o da lista, `l<id>` (launches) ou `c<id>` (compra no cartão); o inteiro
  vale SÓ como o `#N` (`user_seq`), nunca como id interno nem de compra. `validate` recusa fora do `pode` com o motivo
  do estado e o que a IA ainda faz; o resumo da pergunta vem do banco (`_item` na pendência) e o "sim" relê: item
  sumido, fora do `pode` ou editado depois da pergunta não executa. Registro que o Extrato não lista (criar/apagar
  caixinha e investimento) é recusado com frase própria. `c<id>` nunca apaga.
- `GET /api/v2/categorias` (`api/v2/categorias.py`): `{categorias: [{chave, nome, emoji, icone, cor}]}`, o
  catálogo do usuário pelo `CAT_META_SQL` (chave = `cat_norm_sql(name)`, nome = a grafia que
  vence entre gêmeas, `emoji`/`cor` da mesma linha, nunca vazios), semeado como o `/categories` do
  /app. Só o catálogo: a categoria que só aparece nos lançamentos e a "sem categoria" não têm entrada aqui
  (o fallback neutro é do `/gastos`).
  `icone` (também em `/gastos` e `/gastos/categoria`, campo aditivo) é o nome Phosphor derivado do `emoji` por
  `icone_da_categoria` (`db/cat_icones.py`, o mesmo mapa do `EMOJI_TO_PH` do `dashboard.js`, comparado por
  `tests/test_cat_icones.py`); emoji sem par cai em `tag`. O `emoji` segue no payload (app instalado não atualiza)
  e o /painel e o app desenham o `icone`, nunca o emoji.
- `GET /api/v2/gastos?mes=AAAA-MM` (`api/v2/gastos.py`, regra em `db/gastos.py`; também em `/api/app/gastos`,
  porque `criar_app` monta o mesmo router nos dois: rota nova não afeta o app instalado): "Para onde vai",
  a visão do mês, só leitura (plano em `docs/plano-etapa5-gastos.md`). `mes` como em `resumo-do-mes` (sem
  ele o corrente; futuro ou fora do formato = 422). Resposta: `mes`, `disponivel`, `hoje` (AAAA-MM-DD no
  fuso do app), `dia_de_corte`, `historico_desde` (o corte do plano, se cai depois do dia 1 do mês; senão
  `null`), `total` (Σ das categorias, em reais; `null` se `!disponivel`), `categorias[]` (`chave`, `nome`,
  `emoji`, `icone`, `cor`, `valor` = mês inteiro, `quantidade`, `valor_ate_corte`, `anterior_ate_corte`), `dias[]`
  (só dias com saída dentro da grade, crescente: `dia`, `total`, `maiores` = até 3 por `valor`, a mais
  recente primeiro no empate, `{descricao, valor, categoria}`; `quantidade` = quantas saídas somam o `total` do dia
  e `categorias` = `[{chave, valor}]` do dia, Σ `valor` = `total`, em `(-valor, chave)`, das MESMAS linhas do `total`:
  para o card do dia (PR B, #1029) mostrar "Compras N", a categoria principal e a barra sem somar dinheiro
  no cliente; a saída de `fora_da_grade` não entra), `fora_da_grade {quantidade, valor}` (sempre
  presente), `saidas_em_outra_moeda`, `anterior_total_ate_corte` (todas as saídas do mês anterior até o
  mesmo corte, também de categoria que só existe lá e não é listada; `null` com `anterior_ate_corte`
  `null` ou `!disponivel`: é o total certo para a variação) e `motivos` (`MOTIVOS_GASTOS`, derivada de `resumo_mes.MOTIVOS` + os de
  `lancamentos.MOTIVOS_ITEM` que faltam + `arredondamento_por_grupo`; nesta ordem: `conciliacao_pendente`, `movimentos_pendentes`,
  `banco_desatualizado`, `inicio_do_historico`, `transacao_pendente`, `outra_moeda`, `moeda_presumida`,
  `arredondamento_por_grupo`). Os `motivos` juntam os das contas que estão em `resumo_mes.MOTIVOS` (`outra_moeda`/`moeda_presumida` de `/contas` não passam), das linhas do mês pedido E das linhas do
  mês anterior lido: um `transacao_pendente` do mês anterior aparece na resposta do mês pedido (fixado por
  `test_pendente_do_mes_anterior_levanta_o_motivo_do_mes_pedido`). **Regra**: as MESMAS linhas do `/lancamentos` (`lancamentos.listar`, guardado
  fora porque é opt-in) por `mes_detalhes._em_reais` (sem interno, sem outra moeda), só `saida`: Σ
  categorias = `saiu` de `resumo-do-mes` nos meses sem `outra_moeda`. O mês pedido e o anterior saem
  de UMA transação repeatable read / read only (o seed do catálogo, que escreve e abre conexão própria,
  roda antes). **Corte por dia**: `valor_ate_corte` e `anterior_ate_corte` somam o dia 1 até
  `dia_de_corte` (mês corrente: o dia de hoje; mês passado: o último dia do mês); no mês anterior, o
  mesmo dia (`min` com o último dia dele, 31/03 contra fim de fevereiro) no mês corrente e o mês
  anterior inteiro no passado (fevereiro contra janeiro conta os dias 29 a 31). Saída com data depois
  do corte entra em `valor` e não nos dois "até o corte"; data anterior ao mês conta como já ocorrida.
  `anterior_ate_corte` e `anterior_total_ate_corte` são `null` (nunca zero) quando o corte do plano toca qualquer parte do mês anterior;
  mês todo antes do corte = `disponivel: false`, `total: null`, listas vazias e `motivos` com
  `inicio_do_historico` (a tela não mostra "R$ 0"). **Grade**: `[max(dia 1, corte), fim do mês)`; saída
  com `dia` fora dela (compra de cartão do mês anterior numa fatura do mês, linha antiga do Open Finance
  com `posted_at` antes do corte) vai para `fora_da_grade`, dentro do `total`. **Moeda**:
  `saidas_em_outra_moeda` é derivado (saídas não internas − saídas em reais, por chave); entrada ou
  movimento de caixinha em outra moeda não conta, e `outra_moeda` só sobe com contador > 0. **Sem
  categoria**: a chave `sem categoria` junta `NULL`, vazio e a categoria de mesmo nome (`cat_key_sql`); o nome,
  o emoji e a cor são os do catálogo quando há linha dele (o catálogo vence), senão `CAT_NEUTRA_EMOJI`/
  `CAT_NEUTRA_COR` (`db/connection.py`), que valem também para a categoria que só aparece nos lançamentos; o
  nome, nesse caso, é a própria chave. `cor` fora de `#RRGGBB` e `emoji` vazio ou só espaços saem como o
  neutro (`CAT_NEUTRA_COR`/`CAT_NEUTRA_EMOJI`), em `/categorias` e em `/gastos` (`cat_visual`). **Cor padrão**:
  `#7c3aed` é o default da coluna e da semente (ninguém o escolheu); `#64748b` também é um swatch do seletor
  do painel antigo (`dashboard.js`, `CATEGORY_COLOR_OPTIONS`), e quem o escolheu lá passa a ver
  `CAT_NEUTRA_COR` (`#94A3B8`) por decisão do dono (cinza unificado). Os dois saem assim em `/api/v2/categorias`
  e `/gastos` (`CAT_COR_PADRAO`, uma regra só, no servidor; o front não reconhece cor); o painel antigo segue
  com a cor crua (`db/budgets.py` devolve `#7c3aed`).
  `categorias[]` vêm por `valor` decrescente, empate pela `chave`; `maiores[].categoria` é a `chave`.
  `descricao` nunca nula (`SEM_DESCRICAO`). `arredondamento_por_grupo`
  sobe quando a soma dos valores exibidos em centavos das categorias (ou dos dias mais `fora_da_grade`, ou, dia a dia, as `categorias` do dia contra o `total` do dia)
  difere do total (`soma_arredondada_diverge`). Consumidores (PR 2): os widgets `GastosCategorias`,
  `GastosCalendario` e `GastosDetalhe` (`webapp/src/dashboard/widgets/`), em `/gastos` e nos blocos "Para onde
  vai" e "Dia a dia" do Resumo, via `gastosQuery`/`gastosCategoriaQuery` (`lib/v2.ts`); valores com centavos
  (`moneyText`). Tocar num dia do calendário ou em "Ver compras" do detalhe abre o card de compras na própria tela
  (`GastosCompras`, estado `gastosAberto` na store; no modo dia o topo é `parts/GastosDiaTopo.tsx`: setas de dia, o total do dia em destaque, a pílula contra a média do mês, os fatos e a barra por categoria, tudo do `quantidade`/`maiores`/`categorias[]` do dia e da média de `lib/gastos-dia.ts`, que o calendário também lê; PR B, #1029; o modo categoria é uma linha "R$ … · N compras"). A consulta do card tem duas formas: modo dia
  `/lancamentos?mes&dia&tipo=saida&guardado=true` (mais `categoria` quando uma foi escolhida) e modo categoria
  `/lancamentos?mes&tipo=saida&categoria&guardado=true`. Só o "Ver em Lançamentos" grava `s.filter.day` e leva ao Extrato;
  a escolha de categoria em "Para onde vai" (`GastosCategorias`) é que grava `s.filter.category`.
- `GET /api/v2/gastos/categoria?mes=AAAA-MM&categoria=<chave>` (`api/v2/gastos.py`, regra em
  `db/gastos.py::ler_categoria` e `db/estabelecimentos.py`; também em `/api/app/gastos/categoria`): o detalhe de UMA
  categoria do mês, só leitura. `mes` como em `/gastos`; `categoria` obrigatória (1 a `CATEGORY_NAME_MAX_LEN` caracteres,
  senão 422). A chave é o `cat_key_sql` do parâmetro (no banco, no mesmo snapshot): "Alimentação" casa com `alimentacao` e
  `sem categoria` junta `NULL` e vazio; `categoria {chave, nome, emoji, icone, cor}` pelo catálogo (o catálogo vence; senão
  neutro, nome = chave). Resposta: `mes`, `disponivel`, `hoje`, `historico_desde`, `total` e `compras` (as saídas da
  chave no mês pedido, as MESMAS linhas do `/gastos`; categoria sem gasto = `0`/`0`, nunca 404), `valor_medio`
  (`total ÷ compras` HALF_UP a centavos, `null` sem compras), `ultimas` (as 6 primeiras na ordem do `listar`, a mais
  recente primeiro, com data futura incluída: `{data, descricao, mensagem, origem, valor}`, descrição nunca nula),
  `serie` (3 meses crescentes `[M-2, M-1, M]`, `{mes, valor}`; `valor` = o mês inteiro da chave, `null` quando o corte
  do plano toca o mês, inclusive M; mês cortado nem é lido; sem corte em M, `serie[2].valor == total`; mês disponível
  sem gasto = `0`), `estabelecimentos` (até 5, `{nome, valor, quantidade}`, `(-valor, -quantidade, nome)`), `dias` e
  `fora_da_grade` (o mesmo cálculo do `/gastos`, só das saídas da chave), `saidas_em_outra_moeda` (só a categoria e só o
  mês M) e `motivos`: os das CONTAS (∩ `resumo_mes.MOTIVOS`) mais os das linhas dos meses lidos da série (M-2, M-1 e M;
  mês cortado não é lido), `inicio_do_historico` quando o corte do plano toca QUALQUER mês da série (M-2, M-1 ou M, mesmo
  com `historico_desde` `null`), `outra_moeda` só com o contador da categoria no mês M > 0 (no `/gastos` o contador é o
  do mês inteiro; aqui outra categoria com USD não sobe o motivo) e `arredondamento_por_grupo` olhando só os dias mais
  `fora_da_grade` contra o total (sem a comparação por categoria do `/gastos`). Fixado em
  `tests/test_api_v2_gastos_categoria.py` linhas 137 (corte em M-2) e 194 (USD em outra categoria). Mês todo antes do corte: `disponivel: false`, `total`/`compras`/`valor_medio` `null`, listas vazias, `serie`
  toda `null`, `motivos` com `inicio_do_historico`. **Estabelecimento**: parte das linhas que somam o total e lê o `raw`
  do Open Finance à parte (`merchant.cnpj` de 14 dígitos e não todo zero, senão o nome do recebedor do Pix/TED, senão a
  descrição; o nome e a descrição passam por `utils_text.merchant_key`, o CNPJ fica só com os 14 dígitos, e a descrição sem letra nem dígito cai em `texto.strip()`), dono pela conta -> conexão do usuário (uma transação forjada de outro
  não entra); rótulo = a descrição mais frequente; grupos de rótulo com o mesmo `merchant_key` se juntam e o nome da união é o rótulo mais frequente
  entre os rótulos desses grupos (P1 b: "Pix enviado" para pessoas diferentes é UMA linha; nenhum nome repete). Nunca saem `raw`, CNPJ, nome do recebedor, `businessName`,
  `provider_*`, `user_id` nem id de linha. Uma transação repeatable read / read only; o seed do catálogo roda antes.
  Plano: `docs/plano-etapa5-gastos.md` §3.1 e §4.1.
- `GET`/`POST /api/v2/guia` (`api/v2/guia.py`, estado em `db/guia.py`, tabela `guia_painel`,
  uma linha por usuário): o guia do `/painel` (#728). `PASSOS` em `api/v2/guia.py` é a fonte
  única do roteiro (`id`, `tela`, `ancora`, `acao`, `dado` `real`|`exemplo`, `fala {titulo,
  apresenta, texto}` — `apresenta` explica o bloco antes do "Entendi", `texto` diz o que
  tocar depois dele —, `avanca: "cliente"`); o cliente desenha o selo "exemplo" a partir de `dado`.
  Resposta `{estado, motivo, passos: [{...roteiro, disponivel, motivo, feito}]}`. Passo 1
  (`resumo.saiu`) disponível quando o Saiu de `resumo_do_mes` (com a janela do plano) é > 0 no
  mês corrente OU no anterior; senão `motivo` = `sincronizando` (alguma conexão `updating`)
  > `conexao_com_erro` (`error_recoverable`/`needs_user_action`/`item_missing`) > `sem_dados`.
  Passos 2 e 3 (exemplo) sempre disponíveis. `estado`, nesta ordem: `concluido` (todos os
  ids em `feitos`, `?&`) > `dispensado` > `em_andamento` (o guia já foi oferecido ou
  começado: o cliente não mostra o convite sozinho, só retoma pela Ajuda) > `oferecer` (nada
  feito, nunca oferecido, passo 1 disponível) > `indisponivel`. Quem fez 2 e 3 sem dados segue `em_andamento` quando os dados chegam, com
  o passo 1 agora disponível e não feito. O `motivo` do topo é o do passo 1 quando ele está
  indisponível (só em `em_andamento` e `indisponivel`); senão `null`. O POST recebe `{acao: "visto"|"feito"|"dispensar"|"reabrir", passo?, ordem?}`
  (`ordem` é validada em TODAS as ações: inválida = 422 também em `visto`/`feito`, onde é ignorada)
  e devolve o mesmo `Guia`; `feito` sem `passo` ou `passo` fora do roteiro = 422 no envelope;
  `feito` de passo com `disponivel: false` = 409 `passo_indisponivel` no envelope, sem gravar
  (sem dados o cliente mostra a orientação e segue para 2 e 3); nas outras ações `passo` é ignorado. Exige o CSRF do pai. Carimbos só gravam uma vez
  (`oferecido_em` no `visto` e no `reabrir`, `dispensado_em`, `concluido_em`, e o 1º de cada
  passo em `feitos`); `reabrir` zera `dispensado_em` (salvo gesto descartado pela ordem, abaixo). A oferta conta o convite **e** a Ajuda:
  quem abre pela Ajuda sem nunca ter visto o convite fica `em_andamento` e não o recebe depois,
  e entra em `oferecidos` na medição abaixo (conclusão/oferecidos mistura as duas portas; quem
  abre pela Ajuda antes de ter dado põe a espera pela sincronização dentro de
  `mediana_oferta_ate_1o_valor`, então ela não mede só o guia).
  **Ordem dos gestos.** `dispensar`/`reabrir` aceitam `ordem?: {aba, n}` (`aba` 1–32 caracteres, sorteada
  por carregamento de página; `n` 1–2³¹−1, contador da aba que só cresce). O servidor guarda o último
  `(aba, n)` aplicado em `guia_painel.ordem_aba`/`ordem_n` (anuláveis, `alter … if not exists`, fora do
  `GET`); gesto da MESMA aba com `n` menor ou igual ao guardado não grava o efeito (200 com o estado atual;
  o `reabrir` descartado ainda carimba `oferecido_em`, quando ele é null e `feitos` está vazio, como no reabrir de sempre). Outra aba, ou sem `ordem` (cliente antigo), aplica
  e grava o novo `(aba, n)` (sem `ordem` → null). A comparação mora no `WHERE` do `UPDATE` (trava a
  linha), nunca num `SELECT` antes. Limite aceito: a referência é UM par `(aba, n)` por usuário, o do último gesto aplicado; a garantia da mesma aba
  só vale enquanto nenhum gesto de outra aba, de outro aparelho ou de cliente antigo (sem `ordem`) chegar entre os dois
  POSTs. Ex.: A reabrir 9 → B reabrir 1 → A dispensar 5 (emitido antes do 9) aplica e desfaz o reabrir 9. Upgrade: guardar
  o último `n` por aba, com teto de abas.
  `visto` e `reabrir` só carimbam enquanto `feitos` está vazio: **`oferecido_em`, quando existe, é anterior ou igual a todo carimbo de `feitos`**
  (as medianas abaixo nunca saem negativas). O servidor confia no cliente para o `feito`
  (não confere a ação; confere só a disponibilidade). Fora do aviso SSE e do merge; o "Recomeçar do
  zero" apaga a linha (`_RESET_TABLES`); entra na exportação LGPD (`guia_do_painel`) e sai com a conta (cascade). Medição, só leitura:

  ```sql
  select count(*) filter (where g.oferecido_em is not null) as oferecidos,
         count(*) filter (where g.concluido_em is not null and g.oferecido_em is not null) as concluidos,
         percentile_cont(0.5) within group (order by (select min(v::timestamptz)
             from jsonb_each_text(g.feitos) e(k, v)) - g.oferecido_em) as mediana_ate_1a_acao,
         percentile_cont(0.5) within group (order by (g.feitos->>'resumo.saiu')::timestamptz
             - g.oferecido_em) as mediana_oferta_ate_1o_valor,
         percentile_cont(0.5) within group (order by (g.feitos->>'resumo.saiu')::timestamptz
             - u.created_at) as mediana_cadastro_ate_1o_valor
    from guia_painel g join users u on u.id = g.user_id;
  ```

  **Dicas de tela.** O `Guia` também traz `dicas: [{id, tela, titulo, texto, vista}]`, do
  catálogo `DICAS` em `api/v2/guia.py` (hoje só `assinaturas.marcas`), filtradas pelo plano
  (`plan_gate_ok` do recurso da tela: `subscriptions` para Assinaturas; o Essencial recebe
  `[]`). `POST /api/v2/guia/dica {dica}` carimba a 1ª vez em `guia_painel.dicas`
  (`{dica_id: carimbo}`, coluna por `alter … if not exists`) e devolve o `Guia`; id fora do
  catálogo = 422; não toca `oferecido_em` nem `feitos` (o guia segue em `oferecer`). Dica que
  o plano não dá grava e é inofensiva: o GET não a devolve. O cliente (`parts/Dica.tsx`) mostra
  a dica uma vez, sem mover o foco e nunca com o guia aberto; a Ajuda vira menu só na tela com
  dica (`parts/Ajuda.tsx`), e o Cmd-K ganha "Como funciona esta tela" lá.

- `POST /api/v2/simulador` (`api/v2/simulador.py`; também `/api/app/simulador`): simulador de compra do
  Pro, o mesmo `simular` da rota legada sobre `previsao_v2.ler_snapshot(uid, 90)` (a mesma leitura da
  Previsão). Gate `pro_required` (recurso `simulator`) antes de qualquer leitura financeira e antes da
  validação do corpo (Plus com corpo inválido recebe 403, não 422). Limite declarado: Plus com JSON malformado (não parseável) recebe 422 `json_invalid` antes do 403 do gate (provado pelo Tester); só o JSON bem formado e inválido chega ao 403. Entrada em texto (`_VALOR`, `_DATA`,
  juros `0..20`), `extra=forbid`, sem `user_id`; a conversão para float acontece só na fronteira de
  `decision_simulator.Simulacao` (fonte única da validação) e o erro dela vira 422 `validation_error` com
  `loc` prefixado por `body`, só `loc`/`msg`/`type`. Juros: texto com no máximo 4 casas decimais (`_JUROS`; a tela
  diz isso). Cada `value_error` do `Simulacao` sai com `type` = código estável e o campo no `loc` (tabela
  `CODIGOS_ERRO`, chaveada pelas constantes `ERRO_*` de `decision_simulator.py`, que a tool de IA continua recebendo
  com o texto de sempre): `entrada_maior_que_preco` e `entrada_igual_ao_preco` (loc `entrada`),
  `entrada_parcial_sem_parcelas`, `juros_sem_parcelas` e `financiado_pequeno_demais` (loc `parcelas`),
  `data_no_passado` e `data_fora_do_calendario` (loc `data_compra`), `dinheiro_casas` (campo do `loc`),
  `nomes_repetidos` (loc `["body"]`); `value_error` sem código mapeado sai `valor_invalido`, sem o `msg` original
  (`msg` fixo "Valor inválido."). Um teste enumera os `raise ValueError` do modelo e falha se algum não tiver código.
  As `premissas` da resposta são as da snapshot (as mesmas da Previsão, inclusive a do banco quando há) mais `PREMISSAS_V2` (escritas para a pessoa), não a `PREMISSAS` da tool; um teste liga as duas listas regra a regra (§0.7). Resposta: dinheiro em texto, `atual` e cada cenário com
  `saldo_final`, `pior_dia`, dias abaixo da reserva e `trajetoria` diária (hoje..hoje+90, 91 pontos: um a mais
  que a da Previsão, que começa amanhã), `delta_vs_atual`, `contrato`. `cabe_nas_premissas` é sempre false e
  `orientacao` sempre `abster`: sem veredito. Não grava nada. A tela é `#/simulador/compra`
  (`widgets/SimuladorCompra.tsx`, fora do protótipo e do menu lateral; entra pela ferramenta "Vale a pena
  comprar?"). O `atual` inclui a estimativa do dia a dia (mediana, ver Previsão); em aberto: o corte de hábito real
  por categoria. No app: folha `app/(app)/simulador-compra.tsx` (`features/painel/simuladorCompra.ts`,
  `simuladorForm.tsx`, `simuladorResultado.tsx`), aberta pelo `+` do bloco Simulador na aba Gastos, só Pro;
  frases mais curtas que as do web, chaves comparadas por `simulador_textos_paridade.test.ts`.
- `POST /api/v2/simulador/habitos` (mesmo módulo; também `/api/app/simulador/habitos`; só Pro, mesmo gate): corte
  de hábito real. Corpo `{"cortes":[{"categoria","percentual"}]}` (1..50, `percentual` int estrito 0..100, sem `user_id`).
  Ordem: gate (403 antes de ler corpo e dado) -> corpo -> `categorias_repetidas` -> leitura da snapshot ->
  `categoria_fora_da_estimativa` (loc `["body","cortes",i,"categoria"]`; também com estimativa null; a chave tem de estar
  na estimativa DA SNAPSHOT do próprio uid). Conta: `previsao_variavel.cortar` (economia por categoria em `centavos`,
  total = soma) e `decision_simulator.simular_corte`: só as ocorrências da fonte `FONTE_VARIAVEL` trocam, por
  `ocorrencias(hoje, hoje+90, despesa_mensal − economia)`; o corte começa amanhã (ponto de hoje igual) e `delta_vs_atual`
  no dia 90 = 3 × `economia_mensal`. Reserva 0. Resposta: dinheiro em texto, `atual` e `com_corte` (91 pontos), `cortes`
  só os enviados, na ordem da estimativa; premissas da snapshot + `PREMISSAS_HABITOS_V2`. Não grava. Cada POST relê a
  snapshot; o cliente posta ao soltar o slider. `categoria` sem teto de tamanho (a chave é coluna `text` sem teto; o
  teto do corpo protege); códigos de 422 próprios em `CODIGOS_HABITOS` (fixture `simulador.codigos_422_habitos`). Card
  `simulador` do chat: o bloco ganha o recurso `previsao` (90 dias) ao lado do `detalhes` do mês (build antigo ignora);
  a visão do modelo é `{mes, gasto_do_dia_a_dia: null | {despesa_mensal, categorias[{nome, despesa_mensal}]}}` (a chave é
  `despesa_mensal` porque o `ai_guard` só aceita número sob chave monetária) e o Piggy fala do gasto ESTIMADO, nunca do
  mês (sem marcador de capacidade: decisão do dono, 20/10/2026). Com `cortes` no `show_card` (`cards_corte.py`: até 10 itens
  `{categoria | todas, percentual | valor}`; `valor` em R$/mês; o servidor resolve o nome contra a estimativa DA MESMA snapshot
  da conta, `casar_categoria`), o bloco ganha o recurso `simulador_habitos` (o corpo do POST, via `habitos_da_snapshot`; o
  `valor_pedido` do corte em R$ é o único campo que o POST nunca preenche) e a visão do modelo vira `{corte: {...}}`
  (`resumo_corte`); o app desenha só o resultado (`simuladorCorte.tsx`), sem slider. Erros do pedido não marcam falha no turno
  (`PedidoInvalido`: `corte_invalido`, `categoria_fora_da_estimativa` com a lista de nomes, `categorias_repetidas`,
  `valor_acima_da_estimativa`, `cortes_so_no_simulador`); estimativa nula é `indisponivel`. Telas (PR 3b): web `SimuladorHabitos.tsx` (bloco do
  Resumo com as `MAX_NO_RESUMO` maiores categorias (`SimuladorHabitos.tsx`) e `#/simulador` com todas; só Pro; o POST é uma query cuja chave é o corpo, no
  evento nativo `change`) e app `simuladorHabitos.tsx` (aba Gastos e card do chat; `onSlidingComplete`); o protótipo
  (DEMO) mantém o exemplo antigo.
- `GET /api/v2/progresso-milhao` (`api/v2/progresso_milhao.py`; também `/api/app/progresso-milhao`): o
  patrimônio de hoje (`db/patrimonio.calcular`, a conta da tela Patrimônio do app, lida como em
  `api/v2/patrimonio.py`) para o `/painel#/milhao`, fora do menu lateral e do Menu do celular (só o card
  de Ferramentas e o ⌘K). Gate `million_progress` (Plus/Pro): Essencial/Free = 403 `pro_required` ANTES de
  qualquer leitura. Resposta `{patrimonio: "1234.57" (texto, 0,01 HALF_UP), motivos[]}`: os de `calcular` mais
  `sem_banco_conectado` quando nenhuma conta BRL nem posição de conexão viva entrou (só Carteira, caixinhas e
  manuais, ou conexão pausada, ou só conta em outra moeda; vira o convite "Conecte um banco"). Sem entrada: o
  usuário é o da sessão. A projeção (juros compostos, taxa fixa padrão 10% a.a., aporte só informado, teto de 600
  meses) é do cliente, `webapp/src/dashboard/lib/milhao.ts`; nada é gravado. Limites declarados: o "mês de
  hoje" vem do relógio do navegador (±1 mês na virada em fuso diferente) e o "Guardar por mês" vai até R$ 10
  mil. Fora do escopo: app Expo, aporte do histórico, inflação, metas além do próximo milhão.
- `GET /api/v2/previsao?dias=30|60|90` (`api/v2/previsao.py`): previsão
  condicional do motor único, usuário da sessão e gates antes da leitura financeira.
  Sem `dias`, escolhe o menor horizonte permitido; Plus recebe marcos de 30 dias,
  Pro recebe 30/60/90 conforme o horizonte solicitado. Fora do horizonte do plano é
  403 `forecast_horizon_not_allowed`; recurso ausente é 403 `pro_required`; query
  fora dos três valores é 422 no envelope. O recurso `cashflow` concede os detalhes,
  independentemente da lista de horizontes. Uma snapshot repeatable read/read only,
  bancos elegíveis da base v2 (`cashflow_snapshot.ler(..., True)`), sem sync, escrita,
  reparo, expiração de pendência ou aviso SSE.
  `base` é saldo observado; `ancora` é hoje ajustado pelas premissas do motor; série
  começa amanhã e pior dia usa apenas os pontos futuros (primeiro empate, causas
  desde o último pico). Plus tem apenas capacidade `marcos` e todos os campos de
  detalhe Pro são `null`, inclusive sem nomes/ids/ciclos nos motivos públicos.
  Pro sem saldo mantém detalhes conhecidos e pontos `null`, com pior dia `null`.
  Motivos expõem somente código/direção. Compromissos usam chaves opacas estáveis
  por usuário e identidade da fonte; homônimos não se fundem. Instâncias ficam em
  grupo próprio, pois a snapshot não fornece o vínculo persistido de apresentação.
  `incluida_no_calculo` exige valor e data utilizáveis, inclusão na snapshot e
  ocorrência não realizada; desconhecidos e excluídos continuam na explicação.
  Base e valor da ocorrência preservam o Decimal/escala original da snapshot;
  saldos calculados mantêm a quantização do motor. Exemplo discriminante: base
  `"10.005"`, saída `"2.675"`, saldo calculado `"7.33"`. Pontos e causas resolvem as
  ocorrências originais, sem usar o valor arredondado do detalhe legado.
  `calculado_em`/`valido_ate`, qualidade e cobertura vêm da mesma snapshot;
  `cabe_nas_premissas` segue `false`. **Estimativa do dia a dia** (`estimativa_variavel`,
  `core/services/previsao_variavel.py`, sem chave de desligar): por categoria, a mediana de
  até 3 janelas de 30 dias que terminam ontem, das MESMAS linhas do "Para onde vai"
  (`db.lancamentos.listar` + `_em_reais`); fora o que a previsão soma por outro caminho
  (parcela, pagamento de conta, legado do cobrador, cadeia recorrente do banco fora do
  cartão, fixo manual pago por fora). Piso de 7 dias de histórico (abaixo: `null` e o
  motivo `gastos_variaveis_nao_estimados` continua); de 7 a 29 dias o total é levado a 30. Estimativa de total zero vira `null` (o motivo continua). A premissa
  (`premissa_com_estimativa`, `cashflow_contract.py`) diz o histórico que entrou: mediana dos últimos N dias, ou o
  gasto dos N dias levado a um mês.
  Entra no saldo como uma saída por dia (`mensal/30`) a partir de amanhã, com
  `fonte='estimativa_variavel'`: fica fora de `compromissos`, `causas` e do risco
  (o `_risco` ignora a estimativa); `pior_dia.despesa_variavel` explica a parte dela. Campo novo no
  topo da `Previsao` (também no Plus), `cobertura.inclui_estimativa_variavel` vira
  `true`. Horizonte até 90 pontos
  futuros e até 3 marcos; grupos/ocorrências completos, sem truncamento. Não há teto
  global de bytes comprovado/adicionado: esses limites dimensionais não limitam
  fontes, nomes ou motivos, nem concluem o requisito de um teto global de payload.
  Consumidor (estimativa do gasto do dia a dia): `Previsao.tsx` mostra `estimativa_variavel` (fato "Gastos do dia a dia −R$ X/mês" no card e na página, categorias só na página) e a parte `pior_dia.despesa_variavel` na frase do pior dia; o app mostra a linha na folha e no card do chat (`futuro.tsx`; `previsaoSchema` lê os campos com `.catch(null)`). Só o mensal; sem estimativa (`null`) as telas são as de antes. Dinheiro é o texto da API (`saida()` em `Compromissos.tsx`); a única conta do cliente é a largura da barra. Paridade plena entre os dois lados (por isso fora de `docs/paridade-app-dashboard.md`).
  Consumidor (Etapa 3 PR3): `webapp/src/dashboard/widgets/Previsao.tsx` (página
  `/previsao` e card "Saldo previsto" do Resumo) e `Compromissos.tsx`, com
  `previsaoQuery` em `lib/v2.ts`: chave `["previsao", dias]`, `gcTime: 0` (dado sem
  tela olhando sai do cache, inclusive no downgrade) e releitura em `valido_ate`,
  medida no relógio do servidor, com piso de 60 s. Dinheiro na tela é `moneyText`
  (`lib/format.js`), sem float; a tela não soma (grupo repetido é "3 × −R$ 25,00" ou
  "valores diferentes"). Limite declarado: pendência financeira NOVA em
  `pending_actions`/`ai_pending_actions` não avisa o SSE (as duas tabelas ficam fora
  de `TABELAS_QUE_AVISAM`); a ressalva `acao_financeira_pendente` só aparece no próximo
  foco, aviso de outra escrita, `valido_ate` ou "Tentar de novo". Fechar isso mexe nos
  escritores compartilhados: PR próprio, faixa Completo.
  Receitas do extrato (PR 2a, `widgets/ReceitasDoBanco.tsx`; #1076 PR 1 as moveu para a pilha): as PENDENTES são avisos da PILHA DE AVISOS do topo
  do `/painel` (`parts/AvisosDoTopo.tsx`, montada uma vez no `App.tsx`, um aviso por vez, pontos "Aviso k de n", rosa para decisão; o BANCO desatualizado,
  âmbar, vem antes; `GET /api/v2/bancos` lido por `avisoDeBancoQuery` (`lib/v2.ts`; NÃO é o `bancosQuery(uid)` da página Bancos, `conta/api.ts`, que mostra valores em R$ e confere a conta), `motivos` com `banco_desatualizado` ou `conexao_pausada`, "Ver bancos conectados" abre a página
  `#/conta/bancos` (`href("/conta/bancos")`) quando não há conserto pelo painel; "Atualizar agora" e "Reconectar {banco}" são do #1076 PR 2, parágrafo "Atualizar agora do aviso do banco" mais abaixo; o X vale até recarregar a página). As receitas só entram na pilha em `/` e em `/previsao`, e só nos planos com previsão (`locked("hero", plano)`: Essencial e Free não fazem GET). A lista é a que o card/a página
  leu quando o layout salvo diz que o card existe (exceção: limite (j)), no horizonte que ele mostra (`useListaDaPagina`, publicada por `usePrevisao`; durante a troca de horizonte ela mantém a última, e um 4xx a apaga: 4xx nunca mostra dado guardado, inclusive no downgrade com o card em 60/90 dias).
  Em `/` o anfitrião espera o `/perfil` para saber se o card está no layout (`comCardPrevisao`, do Board): se está, não abre consulta nenhuma (UM GET por carga, testado com `/perfil` a 0, 150 e 600 ms);
  só quando o Resumo NÃO tem o card (ex.: Economizar, ou o bloco tirado no Organizar) a pilha lê a chave `["previsao", null]` sozinha. Tirar o card no Organizar: ele desmonta e limpa a lista, o que re-renderiza a pilha, que passa a ler sozinha (NÃO coberto por teste; se o card nunca tinha publicado a lista, a leitura própria só abre no próximo render da pilha). Nas outras
  telas a pilha só tem o banco e não há GET `/previsao`); `/piggy`, a Conta e o
  protótipo não têm pilha. "Conexão com o banco pendente" do app NÃO existe no web (no app vem do cofre do aparelho, onde o marcador expira em 60 min e o aviso tem "Descartar"; a tela de volta do banco (`open-finance-volta`) também tem "Descartar" na fase `conferindo`, que apaga só o marcador da rodada e não chama o servidor; sem fonte equivalente aqui). As
  RESPONDIDAS (Desfazer) ficam na seção "Receitas na previsão" no fim da `/previsao` (`RespondidasDoBanco`). A lógica da mutação é o hook `useReceitas`
  (uma instância por ponta, mesma `mutationKey`); o alerta "Não deu para salvar" é UM só (contexto `ReceitasProvider`), fica FORA da pilha, acima dela, e
  não atravessa telas. Lê
  `receitas_do_banco` e faz Sim/Não/Desfazer em `POST /api/v2/previsao/receita`. Sem atualização otimista: a mutação só termina
  (e destrava os botões) depois de reler toda `["previsao", …]` ativa, porque a resposta do POST é a do menor horizonte. Resposta
  perdida com a marca gravada: a releitura mostra que pegou e a tela trata como sucesso. Duas pontas (`useReceitas(modo)`): `pendentes` na pilha do topo (a pilha some quando não há aviso; vazia, vira um alvo de foco sr-only "Nenhum aviso.", que só nasce quando o último aviso sai por AÇÃO do usuário na pilha, o Sim/Não ou o X, nunca por troca de rota nem por releitura: nelas o foco é do título da tela) e `respondidas` no fim, a seção "Receitas na previsão" com o Desfazer (decisão do dono, 2026-10-17: respondida
  sai do topo). Sem o que mostrar, a seção das respondidas vira `sr-only` e fica montada para o anúncio e o foco (Desfazer na última respondida); o foco do Sim na pilha vai para o título do aviso que subiu (`PilhaAvisos`).
  No app, as respondidas moram no card "Saldo previsto" do Resumo (contexto `ProvedorRespondidas`; perfil sem esse card não as alcança, limite aceito). Limites: (a) POST ok e GET de releitura falha: sem alerta, o resultado é anunciado, e depois de ~3 s de
  retries a lista fica a de antes do POST, com Sim/Não ativos e o aviso de previsão antiga; (b) POST falha e a releitura não
  mostra a marca: alerta "Não deu para salvar", nada anunciado. Só conta como gravado quem volta na lista com o status esperado:
  resposta perdida de uma linha que sumiu da lista (ou lista vazia, p.ex. banco fora do ar) mostra "Não deu para salvar" mesmo que
  tenha gravado, e nenhum dado se perde (falso erro é menos grave que falso sucesso). Também aceitos: (c) erro numa caixa sem linha
  para tentar de novo (pendente única que some, 404 e releitura vazia: o alerta fica sozinho no topo, sem pilha); (d) ver (h); (e) em lista longa,
  `preventScroll` deixa o foco no título fora da vista, e o anúncio sai pela região viva; GET de releitura pendurado mantém os
  botões travados até o navegador cortar a conexão; sem rede a mutação falha na hora (`networkMode: "always"`). O mesmo padrão de
  `useMutation` em `Subscriptions.tsx` (sem `networkMode`, foco movido sem checar onde o usuário está) não foi tocado. Também aceitos na pilha (#1076 PR 1):
  (f) 5xx ou rede na releitura do horizonte novo: o POST do Sim pegou, é anunciado, mas a lista antiga fica na pilha (o card não sai) e um 2º Sim manda outro POST. Isso é
  inofensivo: `POST /previsao/receita` é idempotente (`api/v2/previsao.py` `responder_receita`, upsert em `subscription_marks` e delete sem erro em `db/of_recurring.py` `marcar`;
  `tests/test_receita_confirmada.py::test_repetir_a_mesma_resposta_e_idempotente`); (g) o POST em voo quando o usuário troca de tela: o alerta de falha NÃO aparece fora da tela do POST
  (sem receita a repetir) e o anúncio "Nome: confirmada." entra numa região `role=status` que já estava montada vazia durante o POST (não nasce preenchida; o M3 de
  `dashboard_v2_pilha_avisos.test.mjs` marca o nó com `data-marca` antes da resposta e confere que o texto entra no MESMO nó), sai sozinho em 3 s. Que o leitor de tela o FALE não foi medido aqui (só no aparelho, VoiceOver/NVDA);
  (h) a troca de horizonte na `/previsao` apaga o alerta de falha que já estava na tela (o `useEffect` limpa quando a página fica sem `d`), mas NÃO o que chega depois: um POST que volta 500 com o
  horizonte novo ainda carregando mostra o alerta, e ele fica depois que a resposta do horizonte chega (a receita segue pendente na pilha, com Sim/Não ativos; o Sim de novo o mostra outra vez); (i) o clique num BOTÃO da pilha (Sim, Não, X, ponto; clicar no texto não conta, teste O2) vale como "respondeu" até a próxima mudança da lista: POST que falha seguido de uma releitura que
  esvazia a pilha, com o foco solto no body, ainda leva o foco ao alvo "Nenhum aviso.";
  (j) o layout salvo e o card montado podem divergir. K5: o Pro está em `/` com o card em 90 dias; outra aba grava o layout salvo sem `hero` (Organizar); a pilha re-renderiza (p.ex. o `/bancos` muda) e passa a ler `["previsao", null]` sozinha mesmo com o card ainda montado, e no rebaixamento o card mostra o dado do Pro por um instante. K7, o inverso: no perfil Economizar, outra aba põe o `hero` no layout; a receita some da pilha até trocar de tela ou recarregar (sem perda de dado). Causa: `AvisosDoTopo.tsx` usa `comCardPrevisao`, que lê o layout salvo no localStorage em vez de observar se o card está montado. A correção estrutural é observar a montagem (contexto `undefined` = nada montado × `null` = montado sem lista); issue a abrir. Servidor
  sem o campo (deploy fora de ordem): a caixa não aparece.
  **"Atualizar agora" do aviso do banco (#1076 PR 2; par do `usarAtualizarBanco.ts` do app, #1100)**: o corpo do aviso (`parts/CorpoAvisoBanco.tsx`) escolhe a ação pela CLASSIFICAÇÃO do snapshot `GET /open-finance/{uid}`
  (`classificar` em `lib/atualizarBanco.ts`; prioridade (b) > (a) > (a0) > (c)): (a) `partial`/`updated`/`error_recoverable`/`no_accounts`/`updating` com coleta vencida: "Atualizar agora", que faz `POST /open-finance/{uid}/refresh`
  e acompanha a coleta (GETs do snapshot em 5, 15, 35, 75 s e depois a cada 60 s, um de cada vez, teto de 300 s); (a0) já `updating`: parte SOZINHA e calada ("O banco está atualizando…"); (b) `needs_user_action` com vaga no plano (`/limite`):
  link "Reconectar {banco}" para `#/conta/bancos` (`href("/conta/bancos")`; o Reconectar com widget mora na página, não no aviso); (c) o resto: `Erro em {bancos}.` (o #1127; o porquê de cada banco mora em "Bancos conectados") + "Ver bancos conectados" (`#/conta/bancos`); `updating` sem `provider_item_id`: o texto padrão do aviso, sem "Erro". A paridade compara o texto. Sem a leitura do snapshot (lendo, erro, conta trocada) fica o corpo do PR 1.
  O `uid` sai SÓ do `contaQuery`; as duas rotas têm o `uid` no caminho e o servidor dá 403 se a sessão não for dele. O leitor do snapshot é UM (`lerConexoes` em `conta/api.ts`, usado pelo `conexoesQuery` e pela máquina).
  A máquina existe duas vezes: `app/src/features/painel/atualizarBanco.ts` e o PORTO `webapp/src/dashboard/lib/atualizarBanco.ts` (SEM nenhum import: o typecheck do app segue a cadeia de imports de qualquer arquivo que o jest do app leia).
  Presas por `app/__tests__/features/atualizar_banco_paridade.test.ts` (a MESMA tabela de cenários nos dois lados, traço inteiro igual) e por `tests/test_app_espelhos.py` (textos, cadência, motivos, prazo e janela). Mudou uma, mude a outra; o `.github/workflows/app.yml` roda a paridade quando só o porto muda.
  O estado mora no hook `parts/usarAtualizarBanco.ts`, chamado por `AvisosDoTopo` (o único anfitrião sempre montado: o corpo do aviso desmonta a cada troca de ponto da pilha). A RODADA é a identidade de um `AbortController` (`vivo`), e toda escrita de estado e todo barulho passam por `atual()`.
  O `marco` decide quando uma leitura pode PARTIR um (a0): só uma feita depois do último marco (montagem, aviso que entra ou sai da tela, partida, fim, abort, volta da aba). O POST tem prazo de 60 s próprio (`comPrazo` de `conta/api.ts`, que corta junto com a rodada), sem `AbortSignal.timeout`/`any` (Safari 14): estourou, lê o snapshot e acompanha. O corte do sinal sai como `AbortError` na raiz (`chamar` em `lib/v2.ts`, que só engole o corpo cortado quando a resposta NÃO é 2xx; `executar` e `enviar` em `app/src/api/client.ts`); 4xx e 5xx mantêm o status.
  O X, o motivo que some do `/bancos`, a troca de `uid` e o desmonte abortam a rodada calados. Medido em `dashboard_v2_atualizar_banco*.test.mjs` (relógio instalado; cada caso leva o nome no teste). Dois julgamentos do app foram corrigidos junto, para bater com o `refreshVerdict` do `/settings` (`Origem: #1100`):
  D1, `updating` neutro com `still_updating:false` ACOMPANHA (antes era "em dia"); D2, `items:[]` com `ok:false` é falha (antes era "em dia"; `schemas/openFinance.ts` passou a ler `sync.ok`, opcional). D2 não é alcançável pelo backend de hoje (`items:[]` só sai com `ok:true`); é paridade e defesa.
  Limites aceitos: (k) o SUCESSO é calado (o app vibra; o web não tem par e anunciar pede texto aprovado pelo dono): o aviso some e o foco vai para o próximo aviso ou "Nenhum aviso." só se o usuário não o levou para fora (`agiu` do PR 1); com o motivo persistindo por OUTRA fonte,
  "Atualizar agora" volta e o servidor responde `rate_limited` no cooldown de 120 s. (l) Com `acompanhando` o botão sai e o foco cai no `<body>`; o fim leva o foco ao próximo aviso só se a lista não mudou no meio. (m) A rodada viva NÃO para com a aba em segundo plano (no app, para): o POST já foi e abortar
  não cancela o servidor; o teto é de relógio, então termina certo. (n) "Reconectar" leva a `#/conta/bancos`, onde o botão Reconectar do banco abre o widget em modo update (`PBOpenFinance.reconnect`, W3 #1153); o aviso não abre o widget direto (um toque a mais, para não iniciar o módulo de Open Finance em toda página). O `updateItem` real só está provado no staging/aparelho, não aqui. (o) Entre o sucesso do POST e a releitura do `/bancos`, o botão volta ativo e um 2º clique faz um 2º POST: o servidor responde `rate_limited` (cooldown de 120 s por item), que conta como em dia, então não há dano nem cobrança dupla; fechar a janela custa estado (um "enviando" até a releitura) que não vale o ganho. A leitura do snapshot tem prazo de 15 s (o do app, `TEMPO_LIMITE_AUTH_MS`): o teto de 5 min só é checado entre leituras, então a rodada fecha em até 15 s depois dele. `updating` SEM `provider_item_id` não é (a0) nem (c) (é "c0": texto padrão sem "Erro" + "Ver bancos conectados"): `classificar` e `baseDe` têm de concordar, senão a rodada concluía na hora e recarregava em laço. Também: o fim de toda rodada faz `invalidateQueries()` (relê tudo, como um aviso do SSE); o snapshot traz contas e transações (só as conexões ficam no cache; é lido com o aviso na tela e a cadência de uma rodada);
  `aria-disabled` e não `disabled` no botão em voo (o `disabled` mandaria o foco para o `<body>`). NÃO verificado aqui (só no staging/aparelho): a Pluggy real (PATCH, a espera de ~18 s, cota, `still_updating` de verdade), o widget/OAuth do "Reconectar", o SSE de outro processo, o estrangulamento real dos timers em aba oculta, VoiceOver/NVDA falando o anúncio, Safari/iOS real e o /painel publicado.
  **No app nativo (PR 2b, `app/src/features/painel/receitas.tsx`)**: uma caixa só, no TOPO do Resumo (`TelaResumo`), para todo
  usuário com Previsão liberada, tenha ou não o card "Saldo previsto" no layout (o provider lê a previsão sempre). Previsão
  `negado` ou `erro` sem falha de toque: sem caixa. Falha de um toque deixa o aviso até a próxima leitura boa; se a previsão está `erro`, a caixa
  fica só com o aviso até o "Tentar novamente" do card; se está `negado` (p.ex. o plano caiu para Essencial com o app aberto), caixa e aviso somem. Mesma rota (`POST /api/app/previsao/receita`) e mesma regra de reconciliação da web. Releitura só da
  previsão por `relerPrevisao()` do provider (não `atualizar()`, que zera a tela e relê tudo). Diferenças: sem a frase de abertura; resultado
  anunciado por `announceForAccessibility`, sem mover o foco; Sim e Não ambos neutros (`Acao`, "Fantasma"); POST ok e releitura que
  falha: a caixa some até a próxima leitura boa (o app não guarda cache da previsão), sem alerta. `.catch([])` no schema: campo
  ausente, que não seja array, ou item de forma nova nunca derrubam a Previsão nem o card `hero` do chat. A tolerância é POR ITEM:
  o inválido sai (p.ex. status novo no servidor) e os válidos ficam. O aviso "Não deu para salvar" é ESTADO (`falhou`) que
  se apaga quando chega uma leitura `pronto` da previsão (troca de horizonte concluída, "Tentar novamente", recarga) e quando começa uma
  recarga completa (puxar para atualizar: previsão `undefined`, a caixa some enquanto relê). Assim o Banner, que anuncia ao montar, não
  remonta com aviso velho nem repete o anúncio. Mantêm o aviso: a troca de horizonte em curso (`carregando`) e a previsão `erro`. A releitura
  que acompanha a própria falha não o apaga. A avaliação do resultado só olha a leitura que a PRÓPRIA resposta pediu (começa num `carregando`
  novo ou numa recarga completa): leitura de horizonte que cai antes é dado de antes do POST e é ignorada. `negado` esconde caixa e aviso e só se sai dele por uma recarga (passa por `undefined`).
  Limites declarados: (a) POST 2xx anuncia sucesso mesmo que a releitura venha velha (réplica atrasada), como na web; (b) o
  deslocamento de layout quando a linha muda de grupo, e o foco do VoiceOver quando o botão tocado desmonta, só se medem no aparelho.
  **Recorrências do Open Finance (Fase 1a, `core/services/previsao_recorrencias.py`)**: com os
  bancos na base, `ler` gera a fonte `recorrencia_banco` das cadeias mensais de
  `of_recurring_payments` (a mesma seleção de Assinaturas, `assinaturas.cadeias`), **só despesa**,
  sempre `estimado`/data `presumida`, valor = o último cobrado. **A receita vem SÓ do extrato
  (P7 a, PR A)**: `receitas_do_extrato` (regra pura) sobre `db.of_recurring.ler_creditos`
  (créditos de conta BANK em BRL, conexão viva, últimos 180 dias, calculado na leitura, sem
  tabela). A chave é **só** o documento do pagador (`paymentData.payer.documentNumber`), e só vale
  com 11 (CPF) ou 14 (CNPJ) dígitos e não todo zero (`_doc`): crédito sem documento válido fica
  fora, como o mascarado ou "0", que uniriam pessoas diferentes (a descrição do Pix é genérica,
  "Pix recebido", e também uniria). A cadeia anda de trás para frente enquanto o intervalo for 30±5 dias e
  vale com `MINIMO = 2` créditos, todos a ±20% da mediana (com 2 créditos, a mediana é a média:
  eles podem diferir até 50%); se ela não fecha, ou se uma cadeia que pula créditos avulsos do mesmo
  pagador (`_pulando`) é mais longa, vale a mais longa, com `MINIMO_PULANDO = 3` elos e valores a ±20% da
  âncora e da mediana, como a consecutiva (2026-10-16; decisão do dono: a defesa contra receita falsa de pagador
  frequente não é o valor, é a confirmação do usuário, abaixo; em simulação, 24% de 20 Pix de R$ 50–300 em 6 meses
  fecham cadeia de 3 por acaso, contra 2% da regra antiga); uma receita por pagador; valor = o último, dia = `dia_da_cadeia`. Crédito idêntico (mesmo
  documento, data e valor: pendente e efetivado, ou duas conexões do mesmo banco) conta uma vez;
  créditos do mesmo documento na mesma data **somam** num só (salário + vale; sem a soma o
  intervalo 0 matava a cadeia); cada parcela somada guarda as contas das suas cópias idênticas
  (`grupos`) e a receita só entra se TODA parcela tem pelo menos UMA conta na base (parcelas diferentes somam: todas precisam;
  cópias da mesma, como a mesma conta em duas conexões vivas, bastam por uma), senão fica `incluida=False`
  com `recorrencia_banco_conta_fora_da_base`.
  Ficam fora o movimento interno (`assinaturas._interno`), o rendimento (as palavras
  interest(s)/dividend(s)/dividendo(s)/rendimento(s), inteiras (`\b`: "Interestadual" não casa), na
  categoria OU na descrição; filtro só da receita), o Pix para si (pagador =
  recebedor) e a autotransferência (débito de mesmo valor ±R$ 0,05 em OUTRA conta real (`provider_account_id` diferente, não `id`: a mesma conta em duas conexões vivas não conta) BANK em BRL do MESMO
  usuário em ±1 dia (débito em dólar de mesmo número não conta), com limites sargáveis: com `abs(...)` no `not exists` o índice por
  conta/data não serve e a consulta degrada com o volume). A recorrência de ENTRADA da Pluggy
  (`average_amount > 0`) deixou de ser lida (fechava a contagem dupla). Id
  `extrato#` + `hash_pii(documento)[:16]` (o campo `documentNumber` nunca sai, nem em `origem_id`,
  motivo, log, `/api/v2/previsao` ou IA: o id é HMAC com pepper; sem `payer.name` o nome cai na
  descrição, que é dado do próprio usuário). Sem aviso novo de "nenhuma receita
  detectada".
  **A detectada é só CANDIDATA; entra na conta a que o usuário confirmou (2026-10-16, decisão do dono).** A
  resposta mora em `subscription_marks` (sem migração), com `merchant_key` = o id `extrato#…` da receita:
  `status 'assinatura'` = confirmada (`CONFIRMADA`), `'ignorar'` = negada (`NEGADA`; os nomes são do status
  da assinatura, e aqui NÃO significam assinatura), sem linha = pendente; desfazer = `marcar(uid, chave, 'nenhuma')`.
  O id é por pagador, então aumento de salário mantém a confirmação, e `merchant_key` da Pluggy passa por
  `normalize_text` (o `#` vira espaço), então as duas famílias de chave não colidem. `anexar` lê as marcas uma vez,
  só vira `Ocorrencia` a confirmada (valor e dia seguem o extrato, a cada leitura; o pagador que para fica
  `interrompida` e sai sozinho, e se volta, volta confirmada sem nova pergunta), e vão para
  `Snapshot.receitas_do_banco` as confirmadas, as negadas e a pendente cujo Sim incluiria pelo menos uma ocorrência até `HORIZONS[0]` (30 dias, o único
  horizonte do Plus; `_sim_muda_a_conta`, com as mesmas datas e cortes do laço de `anexar` via `_datas_banco`): fica fora a interrompida
  (sem crédito há mais de `DIAS_ATIVA` dias), a de conta fora da base e a cuja próxima data esperada cai a 31+ dias (salário que
  caiu hoje, mês de 31 dias: a pergunta volta no dia seguinte; com o último crédito adiantado ela fica escondida
  até cerca de `TOLERANCIA_DIA`+1 dias, medido: 5). A lista não depende do `dias` pedido; `sim`/`nao` numa pendente escondida dá 404 (`chave`, `nome`, `valor`, `dia`, `status` `pendente|confirmada|negada`, pendentes
  primeiro, depois por valor). Como toda leitura passa por `anexar`, a candidata fica fora da CONTA do `/painel`, do app,
  do WhatsApp, da IA, do forecast e do simulador; a lista só vai ao cliente (API) para a tela perguntar, e a visão do modelo
  do card `hero` da IA omite a lista `receitas_do_banco` mas leva `receitas_confirmadas` (as que o dono respondeu Sim: nome, valor, dia; uma confirmada que pode não somar na previsão (pagador que parou de depositar, ou conta fora da previsão) segue listada, sem entrar na conta, porque a visão do modelo não tem o `incluida_no_calculo`, que é do `detalhes` do Pro) (`core/services/ai_chat/cards.py`; testes `test_visao_do_modelo_no_hero_*`, `test_visao_hero_*` e `test_confirmada_interrompida_*`): o único consumidor
  de `Previsao` da IA é esse card, e `compromissos` usa `_so` com chaves explícitas. A lista é devolvida em `receitas_do_banco` de `GET /api/v2/previsao`
  (e `/api/app/previsao`), também no Plus (fora do `detalhes`); a receita que deixa de ser detectada (confirmada ou não) some da lista.
  `POST /api/v2/previsao/receita` (`api/v2/previsao.py`; também em `/api/app/previsao/receita`), corpo
  `{chave, resposta: "sim"|"nao"|"desfazer"}`, mesmo gate `forecast` da leitura e CSRF do pai: a chave tem de estar em
  `receitas_do_banco` do PRÓPRIO usuário, senão 404 `not_found` e nada é gravado (resposta fora das três = 422); só o `desfazer`
  também vale para a marca `extrato#…` do próprio usuário que saiu da lista (consulta por `user_id`; chave de outro usuário segue 404);
  devolve a `Previsao` do menor horizonte permitido, com os detalhes do plano. Tela no `/painel` (PR 2a, `ReceitasDoBanco.tsx`);
  sem tela ainda no app (2b) e no WhatsApp (3); sem o Sim ninguém confirma, mas hoje a regra acha receita só no dono. Limites: uma receita por pagador;
  a receita confirmada ou negada que deixa de ser detectada some da lista (sem valor nem nome a mostrar) e a marca fica na tabela (hash, sem documento; sai com a conta e na exportação de privacidade, em `marcacoes_assinaturas`), mas o `desfazer` por chave continua funcionando;
  **a confirmação é por PAGADOR, não por valor**: confirmar o salário de um CPF faz qualquer cadeia futura desse CPF (ex.: 3 reembolsos de R$ 50) entrar sem perguntar, até o usuário desfazer;
  **`nome` vem do pagador (`payer.name`) ou da descrição, texto livre de terceiro: a tela (PR 2a, 2b, 3) tem de escapar**; o POST lê o snapshot duas vezes (validação e resposta), aceito.
  Limites: **receita sem documento do pagador não é detectada** (o Pix de muita gente
  chega sem ele); com só 2 créditos, 13º/bônus em outro dia do mês, no meio ou depois do último, quebra a cadeia
  (com 3+ a cadeia pula o avulso, desde que ele NÃO caia 30±5 dias depois do último elo: um Pix avulso do
  mesmo pagador 25–35 dias depois do último salário faz a receita sumir da Previsão até o próximo salário
  chegar; a regra antiga também dava vazio); extra no mesmo dia que some mais de 20% ao salário tira o crédito da cadeia; uma receita por pagador (a cadeia mais longa); aumento acima de 20% da mediana rejeita a cadeia; renda quinzenal
  de valor igual, com 3+ meses, vira receita MENSAL (um dos dois créditos do mês; o valor previsto subestima a renda) e a irregular não é detectada; pendente em D × efetivado em D+1 quebra a cadeia; o mesmo documento com formato
  diferente entre meses (zeros à esquerda) vira chaves distintas (o filtro de 11/14 dígitos reduz,
  não elimina); cashback mensal com documento pode entrar (rendimento não). Falso positivo só infla o
  saldo se o usuário o confirmar.
  Receita CONFIRMADA (a pendente fica fora da conta) leva o `_risco` a `abster` (`recorrencia_banco_estimada` é `ambos`:
  `cashflow.py::_risco` não dá veredito com ela), como já acontecia com a saída detectada.
  `amount` do espelho é `numeric` e guarda NaN/±Infinity, que no Postgres são `> 0`: `_SQL_CREDITOS`
  só lê crédito finito (`amount < 'Infinity'`, que tira os dois), senão a Previsão inteira daria 500
  (mesma classe do `custo_mensal`, `recorrencia_valor_invalido`). Identidade
  `merchant_key#k` (nunca o id da tabela, que muda a cada sync). Datas: a 1ª no dia mais
  frequente a 15+ dias da última cobrança; atrasada (até 40 dias) entra como a conferir só
  na saída; interrompida (40+ dias) fica fora. Convivência com o fixo manual (só a saída):
  casamento 1:1 (mesmo meio, dia ±5 circular e nome parecido ou valor ±R$ 0,05); casado,
  conta o manual e a do banco fica fora com aviso; sem casamento e com fixo manual
  sobrando, a saída do banco entra (pessimista, rotulada). **Receita fixa manual (R2,
  2026-10-14): só para quem NÃO tem banco** (a regra da Q40: conexão fora de PAUSED/DELETED,
  `forma_pagamento.regra_ativa`; conexão em erro conta como banco). Com banco, `cashflow_snapshot.ler`
  nem lê a manual (a do extrato a substitui, na mesma leitura repeatable read), então não há
  casamento de entrada; com o freio `OF_CONSOLIDATED_BALANCE_ENABLED=0` ou sem banco vivo ela conta.
  Criar e editar (inclusive pausar) recusam com `FonteUnicaOF("receita", MSG_RECEITA)` em `db/recurring_income.py`
  (o texto mora em `fonte_unica.MSG_RECEITA`, fora de `MENSAGENS`; 400 no `/app`, o texto puro no
  WhatsApp, antes de qualquer pergunta do roteador). Se a recusa do roteador consome a clarification
  do próprio `recurring.add`, a resposta vem com o aviso "🔕 Cancelei a pergunta anterior."; se o
  consumo falha, loga e devolve só a recusa. No handler, `FonteUnicaOF` da corrida (banco conectou
  depois da 1ª checagem) volta como texto puro, sem o "Não consegui criar". Apagar segue livre; o
  `GET /recurring-incomes` devolve `[]` + `banco_conectado: true` (antes do gate de plano) e a aba
  "Receitas fixas" do `/app` só aparece com `banco_conectado: false`. O legado fica no banco.
  **Limites do R2 (declarados, sem código):** (1) o freio `OF_CONSOLIDATED_BALANCE_ENABLED=0` só
  vale para quem lê por `cashflow_snapshot.carregar` (WhatsApp, forecast, simulador);
  `previsao_v2.ler_previsao` (o `/painel`) chama `ler(..., True)` e ignora o freio (pré-existente),
  então lá a receita manual de quem tem banco nunca conta. (2) A recusa no WhatsApp depende de o LLM
  devolver `tipo` de receita; sem `tipo`, "salário" pode virar gasto fixo (pré-existente). (3) Falha
  na consulta de banco conta como COM banco (`regra_ativa`): quem não tem banco pode ver a aba
  "Receitas fixas" sumir, ou a recusa, naquele carregamento. A aba também some para quem não tem
  banco quando o GET falha ou volta 403 de plano (só `banco_conectado === false` a mostra); "Gastos
  fixos" segue visível com o convite de assinatura. (4) A recusa do roteador do WhatsApp
  consome a pergunta pendente do próprio `recurring.add`; a de outro fluxo fica intacta.
  Limites: recorrência no cartão fica fora (não projeta na fatura); `ignorar` vale só para
  saída (a marca não tem direção); o movimento interno que `classify_open_finance_launch`
  reconhece (pagamento de fatura, aplicação e resgate, caixinha, poupança, transferência "same
  person" da Pluggy) não entra — transferência entre contas próprias com descrição genérica e
  sem essa categoria entra como recorrência de saída, sem casamento com a outra ponta; duas
  conexões do mesmo banco
  duplicariam a cadeia. Lista velha ou nunca lida = motivo `recorrencias_banco_nao_lidas`, e a
  previsão em cache vence quando a lista vira velha. Recorrência de conta cujo saldo não está
  na base (saldo ausente, moeda presumida, outra moeda) fica fora, com
  `recorrencia_banco_conta_fora_da_base` (a conta é a da última cobrança da cadeia).

- `GET /api/v2/investido` (`api/v2/investido.py`, regra em `db/investido.py`; também
  `/api/app/investido`): o total investido **nos bancos conectados** — `{total, por_tipo:
  [{tipo, rotulo, valor, motivos}], por_banco: [{banco, valor, motivos}], motivos}`. Gate
  `investments` (403 `pro_required`). Só posições do Open Finance, com o recorte da foto
  (`POSICOES_BANCO_SQL` + `separar_posicoes` de `db/patrimonio.py`): investimento e caixinha
  manuais não entram. A mesma regra serve a tool `get_investment_summary` da IA e o "meus
  investimentos" do WhatsApp (`core/handlers/investido.py`). `total: null` = não dá para
  saber (`sem_banco_conectado`; nenhuma conexão viva sincronizada com linha no espelho
  `open_finance_investments` — o sync não grava "li e veio vazio", então sem linha não há
  R$ 0; ou toda posição sem saldo), nunca zero. R$ 0,00 só com posições que somam 0.
  `nenhum_investimento` só com total null e **toda** conexão viva saudável (o contrário do
  `banco_desatualizado`: `desatualizada` False, tela "Atualizado"); qualquer dúvida
  (reconexão pendente, item em erro, `item_missing`, coleta travada, falha de leitura, sync
  velho) fica só com `banco_desatualizado`. Nunca junto de um número. Com ele os
  três consumidores dizem "Não encontrei investimentos nos seus bancos conectados", na ordem
  `sem_banco_conectado` > `saldo_ausente` > `nenhum_investimento` > "ainda não consegui ler".
  Parte só com posição sem saldo sai `valor: null`; parte que fecha em 0 some.
  **Centavos:** cada posição entra quantizada em 2 casas e as partes somam essas parcelas, então
  Σ por_tipo == total == Σ por_banco, exato; o custo é o total poder diferir do
  `investimentos_banco` da foto em até 0,005 × nº de posições (só com saldo de 3+ casas).
  `rotulo` vem de `investido.TIPOS` (o TS não tem tabela de tipos). Consumidor: o assunto
  `investido` do chat do `/painel` (`parts/InvestidoResposta.tsx`).

- `GET /api/v2/bancos` (`api/v2/bancos.py`, regra em `db/bancos.py`; também `/api/app/bancos`): o que cada banco
  conectado vale — `{total, motivos, bancos: [{conexao_id, saldo_conta, investido, total, contas, cartoes,
  investimentos, motivos}]}`, dinheiro em texto, `null` ≠ zero. **Ressalva do plano:** sem o recurso `investments`, a
  conexão que só tem posição (sem conta) fica fora de `bancos` (o app mostra "—" para ela). Uma entrada por CONEXÃO (`conexao_id` é o id local, na
  ordem crescente dele; duas "Nubank" são duas), e o app não soma. Nenhuma regra de dinheiro nova, tudo num snapshot
  (repeatable read, só leitura): `saldo_conta` = Σ das contas `no_total` de `contas_hoje.listar` (`null` se nenhuma
  entra; outra moeda, conexão pausada e saldo ausente ficam fora, com o motivo); `investido` =
  `investido.calcular(por_conexao=True)`: `null` sem posição lida na conexão, só posição sem saldo, conexão pausada, e
  também quando `/investido` devolve `total: null` (nenhuma conexão viva sincronizada com posição: o critério é do
  conjunto; os motivos, como `banco_desatualizado`, seguem). Σ `investido` coincide com o `total` de `/investido`, SALVO
  (i) quando `/investido` anula o total por `saldo_ausente` com soma 0 e há outra conexão com resgatada/zero conhecido
  (Σ dá `0.00`, o total é `null`) e (ii) com posição só em outra moeda (`/investido` dá `0.00`, a conexão fica `null`
  com `outra_moeda`); em nenhum dos dois o `/bancos` perde dado, ele é o mais honesto. Resgatada
  de conexão viva é `0.00`. `total` do banco = as partes não nulas, `null` se as duas são, e exclusão parcial não anula o
  número; `total` e `motivos` do topo saem SÓ dos bancos listados. Sem o recurso `investments` do plano: `investido` e
  `investimentos` `null` + `plano_sem_investimentos`, e o saldo segue (não é o 403 de `/investido`). Limite: nesse
  plano o motivo de estado (`conexao_pausada`, `banco_desatualizado`) de uma conexão SÓ com cartão e sem conta sai como
  `plano_sem_investimentos` (o estado se perde nessa conexão; o saldo não é afetado) e, se as demais conexões listadas
  não têm o motivo, o topo também não o acusa: plano sem `investments`, uma conexão viva com conta e outra PAUSED só
  com cartão dão o topo `["plano_sem_investimentos"]`, sem `conexao_pausada`, e o estado da pausada se perde por
  completo (da entrada e do topo). Só ocorre com o freio `PLANS_V2_ENABLED=0` / plano sem `investments`; testado
  em `tests/test_api_v2_bancos.py` só no nível da entrada.
  **Quem não vem em `bancos`:** conexão sem nenhuma conta, cartão e posição no espelho (DELETED sem linhas, reconexão
  cujas identidades migraram para a nova, viva recém-conectada sem dado). O app mostra "—" quando a conexão não
  vem aqui (`open_finance_bancos_tela.test.tsx`); o `/painel` faz o mesmo em `/painel#/conta/bancos` (`conta/Bancos.tsx`, `lib/bancos.ts`). Conexão pausada COM linhas continua listada, com valores `null` e `conexao_pausada`.
  **Contagens:** `contas`, `cartoes`, `investimentos` são as linhas do espelho da conexão (sem duplicata de reconexão,
  `distinct on` pela identidade do provedor) — o que o banco mostra, e NÃO o que entra no valor: contam também conexão
  pausada, outra moeda e posição resgatada (uma pausada pode ter `cartoes: 1` com tudo `null`). Carteira, cartão e
  fatura ficam fora dos valores. **Texto decimal:** a escala é a da fonte, a mesma de `/contas` (`"0"`, `"0.00"`,
  `"1000"`): o cliente interpreta o decimal, não compara texto. Limites: conta com `raw` sem `balance` e coluna ≠ 0
  entra no `total` de `/contas` e sai `null` aqui (`patrimonio.sem_saldo`). Fixture do app: `tests/fixtures/app_bancos.json`.
  **Consumidor (app):** `app/app/(app)/conexoes.tsx` + `app/src/features/openFinance/conexoes/`. A leitura é paralela ao
  snapshot e OPCIONAL: qualquer falha (403, 404, 5xx, rede, schema) deixa a tela sem números, nunca sem a lista nem
  as ações. `bancosSchema` é tolerante por item (um item inválido não derruba os outros); a junção é por `conexao_id`
  (`juntar.ts`); `null` e conexão ausente de `/bancos` aparecem como "—", nunca R$ 0,00. A lista não mostra valor;
  o total do detalhe (uma `Folha` aberta pela linha) é o `total` do servidor, sem soma no app, e a barra só entra com
  Em conta e Investido positivos (cálculo do cliente, sem R$). "Ocultar valores" é agora contexto do app inteiro
  (`PrivacidadeProvider`, `app/src/features/painel/privacidade.tsx`), não mais estado local do Painel.

- `GET /api/v2/metas`, `POST /api/v2/metas`, `/metas/editar` e `/metas/apagar` (`api/v2/metas.py`,
  regra em `db/metas.py`, tabela `metas`; também em `/api/app/metas`): metas do `/painel`. Meta não
  guarda dinheiro, só organiza dado do banco (sem input financeiro manual): `nome` (1 a 60, com ao menos um caractere visível e sem caractere de controle; NUL no meio é saneado), `tipo`
  (`meta` | `reserva`), `alvo` (texto `_VALOR`, > 0), `data_alvo` opcional (hoje ou depois, no fuso
  do app; na edição só a data NOVA precisa ser futura, a meta vencida reenvia a velha) e a FONTE que o usuário escolhe (P5): `investido` (tudo investido), `banco` (investido num
  banco) ou `conta` (saldo de uma conta). O `atual` é o valor de HOJE da fonte, pela MESMA regra das
  telas (`db/investido.calcular` e `db/contas_hoje.listar`, num snapshot só). O cliente manda a conta
  pelo `id` público de `/contas` e o banco pelo texto de `por_banco` de `/investido`; o servidor
  resolve DENTRO do que é do usuário (de outro usuário ou fora do BRL = 422, nada gravado) e grava
  `fonte_ref` (o `provider_account_id`, não o `id` da linha, que muda a cada reconexão),
  `fonte_banco`, `fonte_numero` e `fonte_rotulo`. Conta: casa pelo `provider_account_id`; se ele
  sumiu OU está numa conexão PAUSED/DELETED (`conexao_pausada`: o webhook `item/deleted` grava DELETED e mantém a linha) e há UMA conta VIVA com a
  mesma instituição + número + tipo/subtipo guardados (poupança ≠ corrente), a viva vence (reconexão com ids novos; o GET não grava, a
  meta segue guardando o id antigo); ambíguo (2+ vivas) ou sem candidato: a conta antiga com o motivo dela (`conexao_pausada`), ou
  `fonte_indisponivel` se nem ela existe. `provider_*_id` e o número da conta nunca saem.
  Resposta `{metas: [...], custo_mensal: {valor, recorrencias, motivos}}`; cada meta: `id`, `nome`,
  `tipo`, `meses`, `data_alvo`, `atual`, `alvo`, `falta`, `percentual` (inteiro, piso, entre 0 e 100),
  `por_mes` (falta ÷ meses de calendário até `data_alvo`, mês parcial conta cheio, arredondado para
  cima; `null` sem data ou com `prazo_passou`), `motivos` e `fonte {tipo, rotulo, conta_id, banco}`.
  `atual: null` (e `falta`/`percentual`/`por_mes` com ele) quando a fonte não dá número: conta
  pausada, saldo ausente, outra moeda, banco que sumiu (sem nenhuma linha no espelho), parte com `valor: null`,
  fonte que sumiu, nunca R$ 0; banco lido que resgatou tudo mostra `0.00` SEMPRE (`por_banco_com_zero` de
  `db/investido.calcular`, que `/investido` não expõe; a posição `TOTAL_WITHDRAWAL` de conexão viva é R$ 0 conhecido e conta como "dentro", mesmo com irmão pausado ou em dólar: `0.00` + o motivo); o banco que sumiu (nenhuma linha no espelho do usuário) sai só com `fonte_indisponivel` (+ `sem_banco_conectado` se não há conexão viva), sem herdar motivo de outro banco; banco que só tem linhas de conexão pausada NÃO sumiu: `null` + `conexao_pausada` (+ `sem_banco_conectado` se for o caso, como em `/investido`); `falta` e `por_mes` saem em centavos (`falta`
  HALF_UP) e `atual` segue o da tela da fonte; sem o recurso `investments` do plano (o gate de
  `/investido`, que com o freio `PLANS_V2_ENABLED=0` fecha o Essencial) as fontes `investido`/`banco`
  saem `atual: null` + `plano_sem_investimentos`, e criar/editar com elas é o mesmo 403 `pro_required`. Regra do `atual` nas fontes `investido`/`banco`: a mesma SOMA de `/investido` e `/contas`; a meta anula (`null`) quando nada da fonte ficou dentro do recorte (ex.: banco só em dólar; conta em outra moeda), onde `/investido` mostra `0.00` e `/contas` o saldo na outra moeda — exclusão PARCIAL não anula o número. Posição em reais + dinheiro fora do recorte (`outra_moeda`, `conexao_pausada`, do banco escolhido) → `atual` = o R$ de `/investido` com o motivo (a tela mostra "a conferir"; item vivo zerado + irmão pausado = `0.00` + `conexao_pausada`). `atual: null` só quando NADA da fonte ficou dentro (`com_posicao`/`bancos_com_posicao` de `investido.calcular(com_zero=True)`, que contam a resgatada de conexão viva): banco só em dólar ou só com item pausado; `saldo_ausente` já vem como valor `null`. `banco_desatualizado`, `moeda_presumida` e `conta_fora_do_ultimo_sync` só acompanham (`FORA` em `db/metas.py`). Fonte `conta` em outra moeda segue `null` (a conta inteira está fora).
  **Reserva de emergência** = meta `reserva`, no máximo uma por usuário (409 `reserva_ja_existe`: o
  índice único parcial `uq_metas_reserva`, que vale na corrida mesmo sem linha em `accounts`): `alvo` = `meses` (1 a 24, padrão
  `db.metas.MESES_RESERVA` = 6) × `custo_mensal`, quantizado em 2 casas. O custo é
  `previsao_recorrencias.custo_mensal`: a soma do último valor das cadeias de SAÍDA ativas do Open
  Finance (última cobrança há até `DIAS_ATIVA` dias), cartão INCLUÍDO, `ignorar` respeitado, mesma
  seleção da Previsão (`cadeias_do_banco`). Sem o recurso `subscriptions` do plano (o gate de `/assinaturas`: é a lista de
  cadeias de `of_recurring_payments` que o custo soma; Essencial leva 403 lá e na Previsão) o custo nem é lido:
  `custo_mensal {valor: null, recorrencias: 0, motivos: ["plano_sem_custo_mensal"]}` e a reserva sai com `alvo`
  `null` + esse motivo (o usuário a converte em meta comum com o valor dele). Sem custo: `alvo`/`falta`/`percentual` `null` + motivo
  `custo_desconhecido` (o `atual` aparece), e o topo traz por quê (`sem_banco_conectado`,
  `sem_recorrencias` — também quando a soma das cadeias ativas dá 0, que vira `valor: null`, `recorrencias: 0` —,
  `recorrencias_banco_nao_lidas` — esta também acompanha um valor velho —, `recorrencia_valor_invalido` — valor NaN/±Infinity no espelho, que a coluna `numeric` aceita: `valor: null`, nunca um 500 nem uma soma parcial). O
  usuário converte a reserva em meta comum com o alvo digitado (`/metas/editar` com `tipo: "meta"` +
  `alvo`; os `meses` saem); meta virar reserva é 422. `/metas/editar` aceita `nome`, `alvo` (só
  meta), `meses` (só reserva, 1 a 24), `data_alvo` (`null` explícito limpa), `fonte` e a conversão;
  `null` em qualquer outro campo é 422, mesmo junto de campos válidos; corpo só com `id` é 422
  "nada para editar", e campo igual ao gravado é 200 (nada escrito, nem SSE). O cliente manda só os campos que
  mudaram: reenviar o objeto do `GET` inteiro dá 422 (`meses: null` numa meta, `alvo` numa reserva).
  Escrita: `def` síncronas, id no corpo, CSRF do pai (403 `{"detail"}` fora do envelope), resposta
  `{id}`; 404 `meta_nao_encontrada` (o MESMO corpo para id de outro usuário e inexistente), 422
  `validation_error`. NUL no nome é saneado (`limpa_para_pg`), não recusado. A tabela avisa o SSE
  (`TABELAS_QUE_AVISAM`), entra na exportação LGPD (`metas`), no "Recomeçar do zero" e no merge
  (`_MOVIDAS`; reserva dos dois lados = `UniqueViolation` → `MergeRefused` `colisao`, nada movido;
  `metas` não entra em `_tem_dados_financeiros`: só a reserva colide). Limites declarados: duas conexões do mesmo banco dobram a cadeia no custo (herdado
  da Previsão); autotransferência genérica entra como recorrência; sem teto de metas por usuário;
  a reconexão só desvia para a conta viva quando a conta antiga está em conexão PAUSED/DELETED (`conexao_pausada`); se a conexão antiga está em erro (`ERROR`/`item_missing`, `LOGIN_ERROR`, `OUTDATED`; o item apagado na Pluggy chega como `item_missing`, `core/services/pluggy_health.py:~248-250`), a meta segue a conta antiga com `banco_desatualizado`, como `/contas` (que soma as duas); decidir se `item_missing` conta como conexão morta é decisão para o sistema inteiro (`/contas` + metas); `atual` de conta não é quantizado (paridade com `/contas`, saldo `numeric` sem escala).
  Consumidor: a página `/painel#/metas` (`widgets/Metas.tsx`, `parts/MetaCartao.tsx`, `parts/MetaForm.tsx`, `metasQuery` em `lib/v2.ts`; a cópia por motivo em `lib/metas-textos.ts`, com mapas exaustivos sobre o enum deste contrato; `DIAS_MINIMOS`/`MESES_PADRAO` ali copiam `RITMO_DIAS_MINIMOS`/`MESES_RESERVA` e `tests/test_metas_textos_constantes.py` compara).
  Consumidor: a aba Metas do app (a partir do primeiro build arquivado depois deste merge, `GET /api/app/metas`, mesmo formato) desenha as MESMAS pílulas do chat (`MetasChat`, `app/src/features/painel/metasChat.tsx`), todas as metas, sem "+N"; sem meta, "Nenhuma meta ainda."; relê toda vez que a aba abre (`relerMetas`, a meta pode ter sido criada no chat). No chat as linhas "Montar reserva"/"Criar meta" seguem sendo pedidos à IA. A aba não mostra caixinhas (só o card `caixinhas` do chat). A rota do app NÃO filtra pela chave `dashboard_v2_enabled`: a lista só vem vazia para quem não tem meta (quem perde a chave depois de criar continua vendo as metas no app).
  Consumidor, escrita pela aba do app (PR 2 do app v2): a aba CRIA, EDITA e APAGA meta pelas mesmas rotas (`POST /api/app/metas`, `/editar`, `/apagar`, Bearer, sem a chave `dashboard_v2_enabled`: decisão E1-A do dono, todo usuário do app; `tests/test_api_app_metas_escrita.py`). Entradas só na aba (no chat nada muda): "+" no cabeçalho do cartão, pílula tocável (editar) e a linha "Montar reserva"; todas abrem a folha `app/app/(app)/meta.tsx` (`formSheet`, `fitToContents`), cujo formulário é `features/painel/metaForm.tsx` e cuja regra (corpo de criar, diff da edição, mapeamento de erro) é `metasEscrita.ts`, cópia mínima do `MetaForm.tsx`/`falhaDaEscrita` da web presa por `metas_escrita_paridade.test.ts`. Edição manda SÓ o que mudou; rede/5xx/tempo esgotado é "Pode ter salvo. Confira a lista." e nunca reenvia sozinho (o POST não é idempotente: um novo "Salvar" manual cria a 2ª meta, limite da web também); só o apagar pede confirmação (`Alert` nativo). O seletor de prazo usa `@react-native-community/datetimepicker` (dependência nativa: só chega no build novo). **Limite declarado:** quem não tem a chave `dashboard_v2_enabled` cria meta no app, mas o Piggy (chat, `list_metas`) e o WhatsApp dele NÃO a enxergam e o `/painel` não abre para ele, até a chave abrir (P1-b); o chat de quem tem a chave enxerga as metas do app. Os `motivos` das metas entram no aviso de banco do topo (`temMotivoBanco` varre todos os recursos): `conexao_pausada`/`banco_desatualizado` numa meta acendem "Dados do banco desatualizados", de propósito (o sinal é verdadeiro e o aviso leva a "Ver bancos conectados"); `painel_metas_tela.test.tsx` fixa. O bloco `metas` não está mais na Home: `sanitizarLayout` filtra por `BLOCOS_HOME`, e a ordem salva perde o id na leitura (como a web, #961). A rota legada `/goals/{uid}/status` segue para builds ≤31 e o `/app` web.
  Consumidor, o chat do `/painel` (PR C3): o assunto `metas` do chat lê `GET /api/v2/metas` (mesma `metasQuery` da página, viva, sem `refetchOnMount` próprio) e mostra a frase e as PÍLULAS do app (`app/src/features/painel/metasChat.tsx`, "app e dashboard andam juntos", mínimo de texto): `parts/MetaPilula.tsx` (ícone no círculo, nome, SÓ o % à direita, ✓ verde se batida, ⚠ se há aviso/sem saldo/sem alvo, barra, na COR DE SÉRIE; a reserva usa um BLOCO por mês, 12 por linha, cheios = `floor((alvo - falta) × meses / alvo)` em centavos com `BigInt`, nunca o `percentual` nem o `atual`; nenhuma frase e nenhum R$ na tela, as frases de `lib/metas-textos.ts` e os `avisos` (regra do `_avisos` do Python) moram no `aria-label` da pílula, que explica o ⚠). Cor de série (`data-serie` na pílula, tokens `--serie-1..4` e `-wash` em `styles/metas.css`, sem `color-mix` por causa do Safari 14): a reserva é SEMPRE o rosa da marca (`--pink`, sem `data-serie`); as outras recebem 1 de 4 (ciano, lilás, azul, fúcsia claro) por `(Math.abs(id) % 4) + 1`, do `id` da meta e não da posição, então a meta não troca de cor quando a lista muda ou expande; meta batida é `--gain` (✓, barra e blocos) por cima da série; o ⚠ fica neutro (`--ink-3`) e nenhuma série usa verde, âmbar nem laranja de estado (testes medem o matiz a 25° ou mais e o contraste de não-texto ≥ 3:1 contra `--plane-2` e `--plane-3`). `parts/MetasResposta.tsx`: reserva primeiro (`emOrdem` de `widgets/Metas.tsx`), até `VISIVEIS` = 5 pílulas (a vaga "Monte no painel" da reserva que ainda não existe conta), "+N" expande o resto no próprio card ("Mostrar menos", sem folha), link "Abrir no painel" / "Ver todas as N metas no painel" / "Criar no painel". A frase ("Você tem N metas." / "Você ainda não tem metas. Crie a primeira no painel." / "Calculando…" / o erro) vai no `text` do `Answer`; as pílulas vão no `card`, fora do `<p class="msg-text">` e fora da cópia do `role=status` (que só lê a frase e, pelo contexto `Anuncio`, nunca consulta: uma requisição por pergunta). `SO_DO_RITMO` de `MetaPilula.tsx` copia `MOTIVOS_RITMO` (`tests/test_metas_textos_constantes.py` compara); `DO_ALVO` é o exportado de `MetaCartao.tsx` (o teste do app o lê dali: a linha `const DO_ALVO = [...]` não pode mudar). Erros: 403 `password_required` = `SEM_SENHA`, 401 = sessão, o resto (rede, 5xx, 402, 404) = `ERRO`; sem `pro_required`, que a rota não emite (plano sem investimentos aparece por meta). Os 3 chips de metas (a próxima meta, a reserva, a meta de economia) e o texto livre SÓ DE LEITURA (`/\bmetas?\b/` sem verbo de escrita, em `lib/conversation.ts`, depois do `investido`; `ESCRITA` copia a ideia de `_VERBO_ESCRITA` de `core/intent_classifier.py`, sem importar) dão a MESMA resposta real; a guarda `!base.real` em `answer()` (`lib/topics.tsx`) impede que a LEAD de exemplo (número inventado) cubra uma resposta real. O protótipo (`DEMO`) segue com o assunto de exemplo (`GOALS`). As cores de série existem nos DOIS (web #1065; app, `SERIE_META` em `app/src/ui/tokens.ts`): reserva sempre `brand`, as outras `abs(id) % 4` (`serieDaMeta`, `metasChat.tsx`), ciano/lilás/azul/fúcsia claro, verde de batida por cima. No ESCURO o app usa os mesmos 4 hex e o wash a 12% da web, presos por `app/__tests__/features/metas_serie_paridade.test.ts` (lê `metas.css` e `MetaPilula.tsx`, e FALHA ALTO se o formato mudar; os dois arquivos estão nos `paths` de `.github/workflows/app.yml`). O CLARO não tem par na web (as 4 da web dão 1,5 a 2,3:1 sobre `surfaceControl` claro): paleta própria, mesmo matiz e mais escura (#0E7490, #9333EA, #2563EB, #C026D3; de 4,1 a 4,7:1), presa por propriedade em `ui/contraste.test.ts` (3:1 contra pílula, trilha e wash; matiz até 12° do escuro; 25° ou mais dos estados) e não por igualdade. Limite conhecido que JÁ existia (#1055): o ícone da reserva no claro é `brand` sobre `brandSoft` (2,96:1, o par proibido de `contraste.ts`); não foi tocado. O app não tem atualização pelo ar (`expo-updates` ausente): a cor nova só chega num build novo (o primeiro arquivado depois deste merge). Duplicado entre web e app (§0.7) com o teste mais barato possível: `blocosCheios` (`MetaPilula.tsx` × `app/src/features/painel/metasChat.tsx`; o web é preso por uma tabela fixa de 12 casos em `dashboard_v2_chat_metas_pilula.test.mjs`, os mesmos que o Manager comparou com o app à mão e bateram; o app NÃO tem o teste espelho) e `avisos` (web: `MetaPilula.tsx`; app: `metasTextos.ts`; mesma regra e mesmo ⚠, e para código de motivo DESCONHECIDO os dois dizem "Há informações a conferir nesta meta." uma vez, sem o código cru, exceto a frase do RITMO sem número, que na web vem de `fraseRitmo` e lê o código cru como a página Metas; nenhum teste compara os dois). Limites declarados: frase de ESCRITA sobre meta no texto livre ("cria uma meta", "apaga a meta", "quero uma meta nova") cai nos atalhos do protótipo, como antes do C3 (a escrita é no painel ou na IA, Etapa 7). O filtro é por palavra e erra para os dois lados (medido pelo Manager em 2026-10-17): de 30 frases de escrita, 20 ficam nos atalhos e 10 vão para `metas` ("põe", "coloca", "adiciona", "bota", "guarda", "tira", "cancelar", "zera", "mudaria"); de 30 de leitura, 27 vão para `metas` (as 3 que só dizem "reserva", sem "meta", ficam nos atalhos). O pior caso é "Você tem N metas." com o link para o painel, aceitável. "novo/nova" NÃO conta como escrita (é adjetivo do nome da meta: "a meta do notebook novo"; "cria uma meta nova" cai nos atalhos pelo verbo); corpo 200 fora do contrato derruba a tela (já era assim na página Metas; o servidor valida com Pydantic); erro de releitura (foco, rede) troca as pílulas de todas as bolhas de metas pelo `ERRO` (a página mostra o dado antigo com aviso); a frase do custo sem valor ("Defina o valor você mesmo.", `FRASE_CUSTO` do web) e a de fonte sumida ("edite a meta...") saem no `aria-label` sem botão (o app usa a voz só leitura, "no painel"); sem o modo "ocultar valores" do app (o web não tem); `.msg-open` e "+N" têm 44 px dentro do card; `CHAT_REAL` segue `false` (7 dos 9 assuntos são de exemplo); a escrita pelo chat web e a IA real no chat ficam para a Etapa 7.
  Consumidor, a IA (PR C1): a tool `list_metas` (`core/services/ai_chat/tools/metas.py`) e o card `metas` do `show_card` leem `listar` desta rota e devolvem TEXTO pronto com "R$" (nunca float; é o que o `ai_guard` aceita como evidência), de `core/services/metas_conversa.py` (`resumo` + `avisos` por meta; a cópia por motivo `FRASE_RITMO`/`FRASE_CUSTO` espelha o `.ts`, chaves e ordem comparadas em `tests/test_ai_chat_metas.py`). Negativo sai `R$ -250,00` (a forma do `fmt_brl` e do `ai_guard`); o ritmo fala do SALDO da meta, com o sentido em palavra ("está crescendo/diminuindo R$ X por mês"), nunca como o que a pessoa guarda. O dinheiro entra como STRING decimal e nunca passa por float: `metas_conversa.ler` devolve o `model_dump(mode="json")` da rota, a tool e o card (cujo leitor é o mesmo `ler`; o `_py` deixa string intacta) formatam do mesmo texto, então os dois dizem os mesmos R$ mesmo com saldo de conta de muitas casas (0,00499...9 não vira R$ 0,01). Reserva com alvo vindo de custo cuja leitura das recorrências do banco venceu (`recorrencias_banco_nao_lidas`) leva no fim do `resumo` "Não consegui ler as cobranças do banco agora. O valor pode estar velho." (espelha o `avisoCusto` do `MetaCartao.tsx`, que NÃO depende do saldo; sem saldo vem antes "O tamanho da reserva é R$ X.", pois o resumo sem saldo não cita o alvo; o `_avisos` não repete). Reserva sem saldo E sem alvo diz também "E ainda não sei o tamanho da reserva: <frase do custo>" (a tela mostra as duas), sem repetir se as frases forem iguais; reserva com saldo e sem alvo diz, quando o ritmo não tem número, o motivo dele (a tela mostra o `<Ritmo>`). A regra "o texto não omite aviso que a tela mostra" é provada pelo produto de saldo x custo x ritmo x motivo em `tests/test_ai_chat_metas_extra2.py`. Fora dessa regra, de propósito: o alvo, o prazo ("O prazo era X"), o `(N × custo)` e o número do ritmo da reserva sem alvo não saem no resumo de quem não tem saldo/alvo (não são avisos).
  - **Bloco condicional por usuário (P1-b).** Só quem tem a chave `dashboard_v2_enabled` (`metas_conversa.metas_ligadas`) recebe as metas: o prompt de metas (`SYSTEM_PROMPT_METAS`, `ADENDO_CARDS_METAS`), o schema de `list_metas`, o `show_card` com `metas` e `caixinhas` e a descrição nova do `list_pockets`. Quem não tem a chave recebe `SYSTEM_PROMPT`, `ADENDO_CARDS`, `SCHEMAS` e `SCHEMAS_APP` exatamente como antes das metas (`system_prompt.prompt_do_usuario`, `tools.schemas_para`, montados por turno no `runner`); nele `show_card metas` mostra as caixinhas, com o id `caixinhas` no bloco. A tool `list_metas` sem a chave (só se o modelo imitar o histórico) devolve `use_caixinhas`.
  - **Onde o app lê metas.** `list_metas` fica fora de `SCHEMAS_APP` (`OCULTAS_NO_APP`); no app o prompt de K nem a cita (`SYSTEM_PROMPT_METAS_APP`): metas = `show_card` com `card="metas"` (recurso `metas`, formato de `/api/v2/metas`), caixinhas = `card="caixinhas"` (recurso `caixinhas`, formato legado `{ok, goals}`). Criar, editar e apagar meta pelo chat existe desde o PR C4 (parágrafo "Escrita" abaixo): `create_meta`, `edit_meta` e `delete_meta` valem também no app (o nome vem do card `metas`; o card é foto e não atualiza sozinho depois da escrita, ponto para o E1/C3); pedido de orientação a IA ajuda em conversa (limite 14).
  - **Limites declarados.** (1) O `ai_guard` é cego a percentual, mês ("junho de 2027") e sinal: só confere R$. O sentido (crescendo × diminuindo) é do texto do `resumo`; o modelo que o inverta passa como "sustentado". (2) A visão do modelo traz até `MAX_METAS` (`core/services/metas_conversa.py`) metas e "e mais N no painel"; passando disso, as mais novas ficam fora e a tool não tem argumento de nome. (3) Follow-up sem tool ("e a viagem?") responde do histórico, e o guard fatia por turno: o número do turno anterior não conta como evidência do atual. (4) Nome que é de meta E de caixinha ("Reserva"): "apaga a meta Reserva" não pede qual (não usa nenhuma tool destrutiva); "guardei 100 na reserva" confere `list_pockets` antes. (5) Medido no harness real (`gpt-4o-mini`), não no pytest: o roteamento do modelo; medição feita com gpt-4o-mini, não refeita com gpt-6-luna. (6) "N idêntico à base" vale para a ENTRADA do modelo (`system` e `tools` de cada turno, comparados execução a execução no PR; `tests/test_ai_chat_metas_extra.py` fixa que o prompt de N é o `_BASE` com o marcador trocado). Qualquer edição futura do texto base do prompt muda N também, por desenho. O que SAI ao app para N mudou em dois pontos, aceitos (ver "Para o E1"). (7) "Guardei 100 na reserva de emergência" com meta e caixinha de nomes parecidos, em K: o modelo tenta `pocket_deposit` com o nome da meta (em todas as execuções do harness) e a confirmação depois falha com "não achei a caixinha", igual à base; nada se perde. (8) "Quanto tem na reserva" pode responder por `list_metas` (chat) ou por `list_pockets` (app) quando há meta e caixinha de nome parecido. (9) "Guardei 100 na meta" em K: o "nada registrado" aparece só em parte das respostas, mas nenhuma tool é chamada (o essencial). (10) O guard é cego a sinal: o ritmo sai em módulo ("diminuindo R$ 250,00") e quem copia "R$ -250,00" cai em 🚨 falso positivo. (11) No WhatsApp, "apaga a meta Casa nova" de K pode cair no handler determinístico de caixinha ("Não achei essa caixinha"): é o C2. (12) **Texto × widget no app antigo (build 26), ACEITO pelo dono até o E1/C3:** o chip "Minha reserva de emergência está no tamanho certo?" de K responde com a META (R$ da meta, via `show_card metas`) e o app antigo desenha embaixo o widget das caixinhas (lê `/goals/{uid}/status`): os dois não coincidem. Não muda o comportamento por causa disso. (13) A chave é lida UMA vez por turno (`_context.METAS_DO_TURNO`/`metas_do_turno`, usada pelo runner, `schemas_para`, `list_metas`, `show_card` e o gate do card); FORA de um turno (chamada direta de `montar`/`get_tool`) ela é lida na hora, com o cache de `get_auth_user` que `dashboard_v2_enabled` usa (`AUTH_USER_CACHE_TTL_SECONDS` em `db_support.py`). (14) A IA de K ajuda em pedido de ORIENTAÇÃO sobre meta ("me ajuda a montar uma meta") e só ao fim manda registrar no painel; a recusa vale para o pedido EXPLÍCITO de criar, editar ou apagar no sistema. Quem decide qual dos dois é o modelo: o harness real mede, o pytest não. (15) Desvios do plano do C1: a chave `reserva_sem_tamanho` não existe (o `resumo` já diz que o tamanho da reserva é desconhecido); o prompt de K manda usar `onde_criar` com `sem_metas`, mas a visão do card `metas` não traz `onde_criar` (no app o `ADENDO_CARDS_METAS` dá a URL do painel).
  - **O que o E1 (app, build 27) fez.** (i) `caixinhas` entrou no `cardId` de `app/src/api/schemas/painel.ts` (`["periodo", "caixinhas", ...Object.keys(TITULOS)]`, FORA do `TITULOS`, que é o catálogo de widgets: `caixinhas` é só card de chat), com o widget legado `Caixinhas`/`ListaMetas` (`app/src/features/painel/patrimonio.tsx`) lendo o recurso `caixinhas` do bloco (formato legado `{ok, goals}`, `metasSchema`); `PENDENTE_NO_APP` em `tests/test_ai_chat_cards_ids_app.py` está vazia. Quem NÃO tem a chave (N) recebe `show_card` com `card="metas"` como `card: "caixinhas"` (decisão aceita; a mensagem de tool oculta de N segue a de antes). (ii) **Card `metas` v2 (K), feito (PR separado do E1, antes do build 27).** O recurso `metas` do bloco tem schema próprio, `metasV2Schema` em `app/src/api/schemas/painel.ts` (dinheiro string → centavos com `decimalParaCentavos`; `percentual` 0–100; `chega_em`/`data_alvo` validados), usado por `schemasRecurso.metas` (provider e aba Metas) e, por herança, pelo bloco do chat; só o `caixinhas` do bloco segue com o `metasSchema` legado, e `ListaMetas` fica para ele. Quem desenha é `MetasChat` (`app/src/features/painel/metasChat.tsx`), escolhido por `Metas` (`patrimonio.tsx`: `RecursoDados` lê o provider na aba e o bloco no chat), sem calcular dinheiro, VISUAL (regra do dono para o app: mínimo de texto, dados em forma): uma pílula por meta (ícone do TIPO em círculo, reserva `PiggyBank` em `brand`/`brandSoft` e meta `Target` na cor de série `SERIE_META[esquema][abs(id) % 4]` com wash a 12%; nome; só o `%` à direita; embaixo a `ProgressBar` com `percentual / 100`), e a RESERVA em blocos no lugar da barra (um por mês de `meses`, ou `MESES_PADRAO`; cheios = `floor((alvo-falta)*meses/alvo)` em BigInt sobre os centavos, de 0 a `meses`; nem o `percentual` serve (o servidor o trunca, `db/metas.py:148`: 4/6 meses viraria 3 blocos) nem o `atual` (o servidor não o quantiza e o cliente arredonda HALF_UP: 2999,995 de 18000 encheria 1 bloco, 17999,995 encheria todos sem ✓); o `falta` já vem em centavos e é a régua do "batida", então meta não batida nunca fecha todos; quebra a cada 12). Meta batida (`falta === 0`): SÓ a barra/blocos em `positive` (círculo e ícone ficam na série) e `CheckCircle` no lugar do %; reserva sem alvo ou meta sem saldo: `WarningCircle` e nada cheio; aviso (`avisos(m)`, "a conferir", ou o custo da reserva sem número/velho): um `WarningCircle` em `inkMuted` ao lado do %, nunca a frase. Valores ocultos: sem %, sem barra e zero bloco cheio (o ✓ de batida fica; os blocos neutros seguem desenhados). Reserva primeiro e mais 4 metas; sem reserva, a linha "Montar reserva" ocupa a vaga dela (ela + 4 metas) e, tocada, envia "Quero montar minha reserva de emergência" no próprio chat; sem meta nenhuma, o botão "Criar meta" envia "Quero criar uma meta" (os dois via `pedir` do `BlocoContexto`, que `CardChat` monta com `useConversa().enviar`; ficam desativados com valores ocultos, cota cheia, turno em voo ou conversa não pronta (`bloqueio !== "livre"`), e fora do chat não há `pedir`, então também não enviam); passando de 5 linhas no total, a pílula "+N" (botão, rótulo `+N, mostrar mais N meta(s)`) expande as restantes DENTRO do card e vira "Mostrar menos" (rótulo `Mostrar menos metas`) (nenhuma folha: os cards do chat ficam 100% no chat). Cada pílula é UM elemento `accessible` (sem papel de botão): as frases (`fraseChegada`, `fraseRitmo`, `fraseCusto`, `avisos`, prazo, valores falados) vivem SÓ no `accessibilityLabel`, nunca na tela; com valores ocultos o rótulo troca % e valores por "Valor oculto" e cala a chegada, os valores, o % e a contagem de blocos (derivam do dinheiro), MAS continua lendo a frase do ritmo, o prazo e os avisos de motivo, porque não são dinheiro (decisão: não "corrigir" de volta). Decisão de design: o rótulo lê as frases, não os números de ritmo (`por_mes`, `ritmo.valor`). As frases vêm de `app/src/features/painel/metasTextos.ts`, cópia mínima de `webapp/src/dashboard/lib/metas-textos.ts` (sem os botões `acao` nem `antesDoCartao`); `app/__tests__/features/metas_textos_paridade.test.ts` compara chaves, ordem e textos com o web. DUAS exceções de texto, de propósito (voz só-leitura, iguais ao Python em `core/services/metas_conversa.py`): `FRASE_RITMO.fonte_indisponivel` ("…no painel você edita a meta e escolhe outra.") e `FRASE_CUSTO.plano_sem_custo_mensal` ("…O valor da reserva precisa ser definido no painel."); o teste compara essas duas com o literal do Python. Motivo fora do mapa sai como "Há informações a conferir nesta meta." (nunca cru, nunca some). Título do card no chat e da aba: "Metas" (`TITULOS.metas`). Fixtures: `tests/frontend/api_v2_respostas.json["metas"]` (`vazio`, `varios`, `estresse`, `sem_custo`). Limites declarados: o app alcança TODAS as metas do bloco pelo "+N" (a visão do modelo corta em `MAX_METAS`, o card não); o roteamento do modelo até `show_card metas` só se prova no harness real; layout (fonte grande, tema escuro, VoiceOver) só no simulador/aparelho; dois pontos ficam para o aparelho: o contraste dos blocos vazios (`border` sobre `surfaceControl`, por volta de 1,1:1, a mesma convenção da trilha da `ProgressBar`) e os 12 blocos de uma linha, que podem quebrar em 11+1 em 375pt.
  Consumidor, o WhatsApp (PR C2, leitura): o comando "minhas metas" é determinístico (sem LLM), sem escrita (escrever meta pelo chat é a IA, PR C4, parágrafo "Escrita" abaixo).
  - **Quem recebe, e o comando.** Só quem tem a chave `dashboard_v2_enabled` (P1-b); quem não tem recebe EXATAMENTE o que recebia antes. O classificador (`core/intent_classifier.py`, `_pede_metas`) devolve `metas.list` 1.0 só com `user_id` E a chave (`metas_ligadas`; sem `user_id`, como nos oráculos de abandono, a regra não existe — é o que mantém N idêntico; `tests/test_wa_minhas_metas.py` prende o resultado de N e do corpus de saque/depósito/caixinha ao da `main`). Só o PLURAL inequívoco: "metas", "minhas metas", "ver (minhas) metas", "listar metas", "lista minhas metas", "como (estão|está|anda|andam|vão) (as) (minhas) metas", "quanto falta pra/pras/para (as) (minhas) metas". "meta" e "reserva" em QUALQUER forma ("minha reserva", "reserva de emergência", "como está a reserva", "como está a meta Casa nova", "quanto falta pra minha meta") ficam FORA e seguem o caminho de antes (tier 3/IA, que para K já responde por `list_metas` com o nome pedido): podem ser nome de caixinha ou de investimento (K sem metas e com a caixinha "Reserva de emergência" ouvia "Você ainda não tem metas"), e o singular pede UMA meta, não a lista inteira (`tests/test_wa_minhas_metas_extra.py`).
  - **O texto.** `metas_conversa.texto_whatsapp(uid)`: o `resumo` e os `avisos` de `para_conversa` (nada recalculado), nome em `*…*` via `wrap_wa_markup` (nome com `*_~` ou crase sai sem negrito; quebra de linha vira espaço), URL simples do painel (`ONDE_CRIAR`, sem link mágico) e "e mais N no painel"; se passar dos 4096 do WhatsApp (`_wpp_len`, unidade UTF-16) tira metas do FIM (ficam as primeiras) e anuncia; falha de leitura vira texto humano, nunca traceback; a chave que quebra só na 2ª leitura (`_execute`) cai na lista de caixinhas, como no classificador (falha fechada). Sem metas: "Você ainda não tem metas." + onde criar.
  - **Pendência.** `metas.list` está no `ABANDONA` (e, por herança, no `_ABANDONA_CREDITO`). Enxergam-no a porta 2 (`_clarification_abandonada`), a porta 4 ("✅ Já paguei", `wa_runtime`, `abandona_pergunta_de_valor(texto, uid)`) e as cinco pendências de cartão, que passam o `user_id` ao classificador; as portas 1 e 3 não o consultam com `user_id` e abandonam sozinhas com "minhas metas" (testado). Veto de catálogo (#185): **só o NOME EXATO escolhe a caixinha** (decisão do dono, a mesma igualdade `eh_nome_do_catalogo` de antes das metas: caixa, acento, espaços e pontuação final). Comando de LEITURA não mexe em dinheiro para K: com a caixinha "metas" e "saquei 200" ou "esvaziar caixinha" pendente, "metas" é a caixinha (saca/esvazia), mas "ver minhas metas", "listar metas", "como estão minhas metas", "quanto falta pras metas" LISTAM as metas, abandonam a pergunta e nada é movido (o resolvedor casa o nome por CONTENÇÃO de palavra: sem este recorte, "listar metas" esvaziava a caixinha, R$ 300 -> 0, como a `main` faz para N). Vale SÓ para `metas.list`: para N e para as demais intents do `ABANDONA` o veto é o de antes (`test_n_com_pendencia_comando_de_saldo_ainda_abandona`). Caixinha ou investimento chamado exatamente "minhas metas" + resposta "minhas metas" é o nome exato: escolhe a caixinha. `investment_pick` abandona por qualquer comando confiante, `metas.list` incluso, sem mover dinheiro. `funding_source_choice` de K existe (depósito em CAIXINHA não é bloqueado pela `fonte_unica`; só criar e aportar investimento manual são, então o fluxo `investment_deposit` não é armado por K): a frase de leitura a abandona, nada anda (`test_pendencia_de_origem_do_deposito_em_caixinha_*`). `payment_method_choice` ("dinheiro vivo ou banco?", `core/intent_router.py`, bloco do `desempate`) abandona com "minhas metas" de K: responde "🔕 Cancelei a pergunta anterior." e lista as metas, sem gravar lançamento (`test_forma_de_pagamento_pendente_*`); o guard anti-órfão (confirmação destrutiva pendente, `DESTRUCTIVE_PENDING_TYPES`) também a abandona, sem aviso e sem executar. Os dois são fail-safe e iguais a "minhas caixinhas". `metas.list` NÃO entra em `INCLUSOES_SEM_CAIXA`/`INTENCOES_SEM_CAIXA` (`core/services/cashflow_snapshot.py`): essas listas classificam payload de clarification e `metas.list` nunca vira payload (não pergunta nada). Fora do `_INTENTS_DO_ROTEADOR`: com `WA_IA_PRIMEIRO` a IA responde pela tool `list_metas` (mesmos números). Ajuda, catálogo e tutorial não anunciam o comando (a ajuda é para todos e N não tem metas).
  - **Limites declarados.** (1) O tier 3 (LLM) NÃO foi ensinado a `metas.list` (mudar o prompt mudaria N): variações fora das frases acima ("quero ver o andamento dos meus objetivos") seguem para o LLM/IA; se o LLM devolver `metas.list` a quem não tem a chave, o roteador mostra a lista de caixinhas. (2) "apaga a meta Casa nova"/"criar meta X" de K pelo caminho determinístico podia virar caixinha (o LLM do tier 3 devolve `pockets.delete`/`pockets.create`): fechado pelo C4 (`metas.escrita`, abaixo) para K, inclusive com marcador de recorrência ("cria uma meta de 2000 por mes pro presente": a regra `metas.escrita` agora vem ANTES de `vai_ao_tier3_por_marcador`, só com `user_id` + chave; sem chave o marcador segue como na `main`, `tests/test_wa_metas_escrita_corpus.py::test_marcador_de_recorrencia_*`). Para N a frase segue indo ao tier 3 e pode criar caixinha, como na `main` (não é regressão). (3) Só o pytest (LLM e envio falsos) cobre isto; a interpretação real e o WhatsApp real ficam para o harness `--allow-real-services` e para depois do deploy. (4) `classify` com `user_id` lê a conta (`get_auth_user`) quando o texto é uma dessas frases; o `isencao_do_barrado` (gate do paywall, "sem DB") não passa o `user_id` por isso (`test_barrado_*`). (5) Uma meta com nome tipo "Posso te ajudar com a casa" faz o texto cair no `_looks_like_help_fallback` (`core/handle_incoming.py`) e a lista é trocada por uma chamada à IA: a mesma exposição de "minhas caixinhas" (nome inventado, não corrigido). (6) `format_for_platform` mexe na marcação do nome depois do `wrap_wa_markup` (crase vira texto, `**x**` vira `*x*`, linha que começa com "* ", "- " ou "> " perde o negrito): cosmético; negrito que atravessa linhas por `*` solto só o cliente real prova. (7) Para K, "metas" (nome exato) com uma caixinha de mesmo nome escolhe a caixinha mesmo que o usuário quisesse a lista: sem pendência de nome aberta ela lista; com uma aberta, vale o nome. (8) Pendências de CARTÃO (`pay_bill_choice`, `credit_delete_card`, ...) não têm veto de catálogo (cartão chamado `Conta` já perde a resposta `conta` na `main`): K com um cartão chamado "Metas" e "metas" como resposta ABANDONA a pergunta com aviso e lista as metas (fail-safe: nada é pago nem apagado; N pagaria), `test_cartao_chamado_metas_k_abandona_em_vez_de_pagar_limite_declarado`. (9) O gate do classificador não olha o canal: um usuário com a chave que escreva "minhas metas" pelo adaptador do Discord (sem uso, `CLAUDE.md` memória do dono 2026-08-26) recebe o mesmo texto, só leitura. Passar `platform` ao `classify` mudaria a assinatura de todos os chamadores por um canal morto. (10) Desvios do plano do C2: o plano pedia as frases "minha reserva", "reserva de emergencia" e "minha reserva de emergencia", o regex com `(metas?|reserva)` (singular incluso) e uma linha `metas.list` no prompt do tier 3. Ficou só o PLURAL de "metas" e o prompt do tier 3 não mudou: K sem metas e com a caixinha "Reserva de emergência" ouvia "Você ainda não tem metas", e o singular pede UMA meta, não a lista. "Reserva" e o singular dependem da IA/LLM (que para K já responde por `list_metas`).
  Escrita (PR C4): a IA CRIA, EDITA e APAGA meta, nos três canais do `runner` (WhatsApp, `/ai/chat`, chat do app), SÓ para quem tem a chave `dashboard_v2_enabled` (P1-b); N fica byte-idêntico à `main` (prompt, `SCHEMAS`, `SCHEMAS_APP`, `WRITE_TOOL_NAMES`, classificador, roteador: medido por diferencial contra `git archive origin/main`, relato do PR; `tests/test_ai_chat_metas_escrita_confirma.py` e `tests/test_wa_metas_escrita.py` fixam o que dá para fixar). Decisões do dono (2026-10-16): criar, editar e apagar; **CRIAR grava direto, sem confirmação; editar e apagar pedem "sim"/"não"**; fonte padrão "Tudo investido" (o nome exato de um banco ou conta do usuário usa esse: primeiro o rótulo exato, "Investido · Nubank" ou "Nubank · Conta"; se nada casar, o nome da conta ("Conta") ou o do banco dela; mais de uma opção em qualquer camada pergunta; sem investimentos no plano a IA pede a conta); meta não tem arquivo nem pausa, apagar = `delete` como no painel (também é o "desfazer" do criar).
  - **As tools** (`core/services/ai_chat/tools/metas_escrita.py`, separado do `metas.py` só por tamanho; `TOOLS` de `metas.py` as agrega e `schemas_com_metas` as oferece a K nos dois canais). Escrevem SÓ pelas funções da rota (`api.v2.metas.criar/editar/apagar` com `NovaMeta`/`EdicaoMeta`/`MetaId`, `uid` do runner, nunca de `args`): a validação de fronteira (valor `^[0-9]{1,9}(\.[0-9]{1,2})?$`, nome, data não no passado, meses 1–24, fonte do próprio usuário, plano de investimentos, reserva única) é a da rota, e a tool só a traduz em frase. O valor vira TEXTO sem aritmética de float (`str(v)`): NaN, Infinity, 1e300, negativo, bool e vírgula caem no padrão da rota. Meta é achada pelo NOME por IGUALDADE normalizada (`normalize_text`, nunca contenção: a lição do `nome_do_alvo`); 0 achadas = "Não achei a meta *X*. Suas metas: ..."; 2+ = manda ao painel. `create_meta` (`requires_confirmation=False`) GRAVA DIRETO só quando a MENSAGEM DO TURNO manda criar (`pede_criar_meta_ja` em `core/services/metas_gate.py`, lida de `CURRENT_USER_MESSAGE`: a FRASE é fechada por uma gramática positiva (`fullmatch`, fail-closed); o SLOT DO NOME é texto livre protegido por lista (`_STOP`/`_RAD`) e pela regra de infinitivo: [pode|por favor|pf] + cria/crie/criar/faz/fazer + [a|uma|nova|minha]* + `meta` + (`de VALOR [pra|pro|para|chamada NOME]` ou `(chamada|pra|pro|para NOME | Nome-Capitalizado) [de] VALOR`) + [`até` DATA] + [, por favor|pf|valeu|obrigado] + [. ou !]; "registra/cadastra" só com "uma meta"/"meta chamada" (senão pode ser depósito); ou `a/uma/minha reserva de emergência [de N meses]`. VALOR = R$/dígitos/`5 mil`/`2,5k`/`5.000,00`/extenso simples (ano nu 2020-2099 não é valor); NOME antes do valor = 1 palavra (só com `chamada|pra|pro|para` na frente ou Capitalizada no texto original: não aceita "uns 5000" nem "ficticia de 5000"); NOME depois do valor = 1 palavra ou "X de|da|do Y", sem hesitação (`_STOP`, radicais `_RAD`: brincad*, mentir*, ficti*, hipote*, simula*, imagin*, exemplo, fake, teste, zoeira...) e sem infinitivo (`ar|er|ir`). Pontuação, emoji, `?`, aspas, quebra de linha e qualquer palavra que sobre fazem a frase NÃO encaixar. Fora de propósito: "abre" (abrir = ver), "faça", "me cria", "da pra/podia criar" (pergunta sem `?`). Além da frase, tudo é conferido no MESMO match: o VALOR dito = `alvo` dos args, tipo meta × reserva pela estrutura da frase, `nome` dos args = o nome dito no slot, `data_alvo` só com "até" na frase e com EXATAMENTE a data que a frase diz (o dia dito; só mês = último dia do mês; só ano = 31/12; data impossível pergunta), `meses` iguais, e `fonte` nunca (a padrão sai por código); divergiu, pergunta): a resposta da própria tool é a resposta final, montada do Decimal gravado. Em QUALQUER outra mensagem (desejo "queria uma meta de 5000", hipótese, relato, "sim" solto, receita que cita meta) o `confirmar_se` do runner transforma a chamada em pendência de 10 min e responde "Só confirmando: registrar *a meta ...*? Responde *sim* ou *não*"; o "sim" grava 1 vez (duplo "sim" 1x), "não" ou outra coisa cancela. Pedido já inválido (valor, nome repetido) não é perguntado: a frase do erro sai na hora. É a trava EM CÓDIGO por cima do prompt: o modelo real (gpt-4o-mini) criou meta por "queria uma meta de 5000" 3/3 só com o prompt (medição feita com gpt-4o-mini, não refeita com gpt-6-luna). A escolha da fonte vive em `metas_fonte.py`; os textos de sucesso ("juntar R$ 5.000,00 até dd/mm/aaaa, acompanhando ..." e "Se não era isso, me diga "apaga a meta X"") vivem em `_criar`, em `metas_escrita.py`; `arma_pendencia_no_execute=True` só para a rodada com 2+ escritas virar `_UM_POR_VEZ` (o nome do campo não descreve o uso; se aparecer um 2º caso, renomear). `edit_meta` (nome, `novo_nome`, `alvo`, `data_alvo`, `sem_prazo`, `meses`) e `delete_meta` usam `validate` (nada é armado se o pedido já é inválido: formato, valor > 0, meses 1–24 e data, as regras que a rota só diria depois do "sim") e `summary` para o template 3; a pendência leva o NOME, e o "sim" repete gate, nome e validação. Fonte e conversão de reserva em meta ficam no painel. `confirmar_se` só devolve False (executa e responde a frase do erro) para erro de usuário conhecido (`_Recusa`, `ValidationError`, `RequestValidationError`, `HTTPException` com `detail["error"]` em `_HTTP`, como o `_guarda`; 500/503 transitório NÃO grava e NÃO sobe: loga e arma o "Só confirmando", porque relançar deixava o tool_call sem resposta no histórico e a OpenAI devolvia 400 em todo turno seguinte; o "sim" passa pelo `_guarda`); qualquer outra exceção é logada e arma o "Só confirmando" (falha fechada: nunca grava direto). `create_meta`/`edit_meta`/`delete_meta` estão em `NAO_FINANCEIRAS_IA` (`core/services/cashflow_snapshot.py`): pendência de meta (inclusive o "Só confirmando" do criar) não mexe em caixa. Nome com NUL ou caractere de controle é recusado com frase em criar e editar (o JSONB da pendência recusa NUL; `NovaMeta` o trocaria por U+FFFD).
  - **Roteamento no WhatsApp.** O classificador (`_pede_escrita_de_meta`) devolve `metas.escrita` 1.0 só com `user_id` E a chave: verbo de escrita NO COMEÇO (cria, nova, abre, cadastra, apaga, exclui, deleta, remove, muda, altera, edita, troca, aumenta, diminui, renomeia, atualiza; prefixo opcional "quero/queria/preciso/pode/da pra/me ajuda a") + a palavra `meta(s)` + nenhuma de caixinha, cofrinho, investimento, orçamento, categoria, cartão, gasto, despesa (essas seguem como antes). Não há parser de valor, data nem fonte fora do modelo. `metas.escrita` está no `ABANDONA` (herdado pelo `_ABANDONA_CREDITO`): com "saquei 200 -> de qual caixinha?" aberta e uma caixinha "Casa", "apaga a meta Casa nova" abandona a pergunta SEM sacar (antes a contenção resolvia o saque na caixinha) e responde o texto fixo `_ESCRITA_DE_META_MSG`; o veto de catálogo continua deixando só o nome EXATO escolher a caixinha. Em `handle_incoming` (5b) `metas.escrita` também chama a IA (confiança 1.0 serve ao abandono, não para fugir dela); sem IA/cota, vale o texto fixo. `ONDE_CRIAR` agora diz "ou me peça aqui".
  - **Limites declarados.** (1) CRIAR grava direto: a trava contra criar duas vezes é o NOME repetido recusado (igualdade normalizada, por usuário, só no chat; o painel continua aceitando nome repetido). Reenvio com nome DIFERENTE e mesmo alvo cria outra meta (não se distingue de duas viagens de R$ 5.000); corrida painel × chat no mesmo segundo pode gravar dois nomes iguais. Meta criada por engano: "apaga a meta X" (pede confirmação). (2) A decisão "mandou criar" × conversa é do CÓDIGO (`pede_criar_meta_ja`, regex sobre a mensagem do turno): o que a gramática aceita grava na hora; o resto pergunta "Só confirmando". Medido em 2026-10-16 com `python -m pytest tests/test_metas_gate.py` (corpora em `tests/_metas_gate_corpus.py` e `tests/_metas_gate_proprio.py`; as taxas saem das tabelas dos testes): Tester passada 2: 45/63 comandos gravam e 0/187 hedgeadas (250 frases); Manager: 0/71 negativas (eram 62/71 com o regex da passada 1) e 4/26 informais; Tester passada 3: 0/59 dirigidas ao slot do nome (eram 56/59) e, no corpus cego dele, 0/253 negativas (eram 2); corpus próprio escrito antes de medir: 0/86 negativas em 10 categorias e 33/37 informais, mais 0/61 negativas focadas em qualificador e hedge no slot do nome e 24/24 informais. Falso negativo é o preço (um "sim", cerca de 30% dos comandos): "faça", "me cria", "cria ai uma meta", "bom dia, cria...", "para o carro" (só pra/pro/para + 1 palavra), "até dezembro" sem ano, "2 milhões", "meio milhão", "mil e quinhentos", cauda extra ("...fonte tudo investido"), emoji, "!" depois de cortesia, nome com 2+ palavras depois de "pra", nome terminado em ar/er/ir ("celular", "pagar a faculdade"), acento composto, "cria meta viagem 5000" (minúsculo sem chamada/pra), "registra a meta X de 5000", "cadastrar meta de 1500" e "criar meta de 5000" quando o modelo inventa o nome (a IA real pergunta o nome). Limite: o slot do nome é texto livre protegido por LISTA. Resíduo: "cria uma meta de 5000 pra X" com X uma palavra de hedge fora de `_STOP`/`_RAD` (ex.: "ficcao") cria a meta "X", sem mover dinheiro; a resposta traz "apaga a meta X". "cria uma meta" sem valor não casa (nada grava). Nos demais casos o modelo só consegue armar a pergunta. Harness real (gpt-4o-mini, K, flags 0 e 1, 3 repetições, passada 1; medição feita com gpt-4o-mini, não refeita com gpt-6-luna): "cria uma meta de 5000 pra viagem" 6/6 grava 1; "queria uma meta de 5000 pra viagem" 6/6 grava 0 e pergunta; o "sim" seguinte 6/6 grava 1; "registrei 100 de receita pra meta 2028" 6/6 grava 0. Passadas 2 e 3: números no relato do PR. WhatsApp real só depois do deploy. (3) `"5.000"` (texto com 3 casas) é RECUSADO pela rota (o plano supunha R$ 5,00); `"5.00"` é R$ 5,00. O schema da tool é `number`, o que torna o caso raro. (4) Editar/apagar pela pendência do NOME: meta renomeada ou apagada no painel dentro dos 10 min vira "Não achei"; outra meta que ganhe o mesmo nome nesse intervalo recebe a ação. (5) O modelo escreve o template 3 de editar/apagar a partir do `summary`: o `ai_guard` só confere R$; o texto do nome é do usuário (injeção pelo nome: no pior caso o modelo cria uma meta, inofensiva e apagável). (6e) B-4: "não, deixa" depois do "Só confirmando" pode fazer o modelo chamar `delete_meta` e responder "🐷 Não achei a meta *Viagem*. Você ainda não tem metas.": nada é apagado (o `validate` recusa), a resposta é que é confusa. (6a) Reflexo de "sim": a uma pergunta/hipótese ("cria uma meta de 5000?") o usuário que responde "sim" no reflexo arma o "Só confirmando" com nome que o modelo inventou ("Meta de 5000"); nada grava sem o 2º "sim" (limite M3 do Tester, não corrigido). (6b) O nome com `*_~` aparece cru na confirmação (herdado da rota). (6c) "registra 100 na meta 2028" (K) ainda vai à IA como `metas.escrita` (na `main` era `out_of_scope`); a IA não tem tool de depósito e responde em texto, e não cria meta; "faz um aporte/transferência/guarda ... na meta" deixou de ir. (6d) Erro transitório da OpenAI no meio do turno cai no texto fixo de falha. (6) O modelo pode, em vez de chamar a tool, perguntar em texto "quer criar?"; o "sim" seguinte (mensagem não explícita) arma o "Só confirmando" de novo: confirma duas vezes, mas nunca grava sem pedido. O prompt manda chamar a tool e não perguntar. Dito "não, deixa" a uma pergunta, o modelo ainda chama `delete_meta` em 1 de 3 (só valida/arma, nada grava). (7) "reserva" sozinha ("cria minha reserva de emergência") e frases com marcador de recorrência (`vai_ao_tier3_por_marcador`) ficam fora da regra determinística e seguem para a IA/LLM como antes. (8) Pendências de CARTÃO: um cartão chamado "Meta X" abandona com aviso quando a resposta é frase de escrita de meta (fail-safe, como o C2 declarou para `metas.list`). (9) O app (build 27) não mudou: o chat confirma por texto ("sim"/"não") e o card `metas` do chat é foto (não atualiza depois da escrita). (10) Desvios do plano: as tools moram em `metas_escrita.py` e `metas_fonte.py` (o `metas.py` passaria de 350 linhas), `"5.000"` é recusada (ponto 3), o classificador também pega `faz`/`registra` e o desejo sem verbo ("queria/quero/preciso de/gostaria de uma meta", só K) e o passado/narração ("criei", "minha amiga criou") e lançamento, fatura, depósito, resgate, relatório e pix que só citam meta ficam de fora; `metas.escrita` anuncia "🔕 Cancelei a pergunta anterior." quando abandona uma pergunta de caixinha.
  **Ritmo** (`db/metas_ritmo.py`, PR A2): cada meta traz `ritmo {valor, dias_de_historico, parcial, motivos}` e `chega_em` (`AAAA-MM`).
  - `valor` = o crescimento ATUAL da fonte, R$/mês (pode ser negativo), ao lado do `por_mes` (o NECESSÁRIO): (`atual` − valor em D0) ÷ dias × 30. Medido nas fotos POR POSIÇÃO (`open_finance_investment_snapshots`), não em `patrimonio_fotos` (só tem o total). O valor de um dia sai da MESMA regra de `investido.calcular` (`separar_posicoes` + `q_posicao`); a moeda vem do espelho (posição que já saiu dele entra como BRL com `moeda_presumida`); só foto `collection_confirmed`.
  - Pertinência (`_DE`, uma regra para o valor do dia E para o dia-base): as fotos do passado cobrem as MESMAS posições de hoje, por identidade do provedor. A foto PERTENCE à fonte quando a VENCEDORA de hoje da identidade (`POSICOES_BANCO_SQL`, `c.id desc` entre TODAS as conexões do dono) é uma conexão da fonte; a foto pode vir de QUALQUER conexão do dono (identidade que mudou de conexão a → c vale pela foto da antiga a; com foto nas duas no mesmo dia, vale a da mais nova). Vencedora fora da fonte (pausada, outro banco): a foto não entra no passado, como não entra hoje. Identidade fora do espelho vale pela foto de conexão da fonte.
  - D0 = o dia mais antigo de [hoje − 90, hoje) em que TODAS as conexões que compõem a fonte HOJE (`conexoes_por_banco` de `investido.calcular(com_zero=True)`: vivas com posição VENCEDORA no espelho; as do banco, na fonte `banco`; conexão viva que só perde identidades não conta, mas empresta as fotos) têm foto que pertence à fonte e NENHUMA foto do dia que pertence à fonte é não confirmada (dia misto subestimaria D0 e inflaria o ritmo; coleta que nunca confirma uma posição deixa a base `null`; foto não confirmada de quem não pertence à fonte não decide o dia). Conexão que entrou depois empurra D0 para a primeira foto dela e a série recomeça; conexão apagada leva as fotos (cascata) e recomeça sozinha; pausada fica fora nas duas pontas; posição nova ou resgatada DENTRO da mesma conexão não quebra a série (é crescimento ou dinheiro que saiu).
  - `parcial: true` = há número com `dias_de_historico` < 90. Sem número (`valor: null`) há TRÊS situações, cada uma com o seu motivo: (1) `historico_curto` com `dias_de_historico` = N quando há dia-base com menos de `RITMO_DIAS_MINIMOS` dias, e com `dias_de_historico: null` quando não há dia comum a todas as conexões da fonte na janela (esperar resolve); (2) `historico_incompleto` (`dias_de_historico: null`) SÓ quando existe na janela ao menos um dia com foto (de qualquer confirmação) de TODAS as conexões da fonte e, ainda assim, nenhum dia serve de base: falta a confirmação (p.ex. uma conexão cujo INVESTMENTS parado na Pluggy nunca confirma a coleta: esperar não resolve). Conexão da fonte ainda sem foto na janela (recém-conectada, ou identidade que migrou para conexão nova) ou sem dia em comum com as outras é `historico_curto`: o tempo resolve. Limite (hipótese, não provada com dado real): conexões que sincronizam em dias alternados nunca têm dia comum e ficam `historico_curto`; (3) banco que perdeu todas as identidades para outro (sem conexão na fonte, nenhuma foto a medir): também `historico_incompleto`, porque `historico_curto` prometeria que o tempo traz a base. O valor sai sem `-0.00`.
  - Fonte `conta`: `null` + `sem_historico_da_fonte` (não há histórico de saldo por conta e reconstruir pelas transações é proibido). Os motivos da fonte acompanham o ritmo; `saldo_ausente` na foto de D0 com soma 0 deixa o valor `null`. O tipo `Ritmo.motivos` leva só o que o ritmo pode emitir (`MOTIVOS_RITMO` em `db/metas.py`: os da fonte e os três do histórico, sem `custo_desconhecido` nem `plano_sem_custo_mensal`, que são do alvo da reserva).
  - Nunca 500: saldo de foto que o `quantize` não alcança (≥ 1e26) é foto ilegível (`saldo_ausente`); ritmo que não cabe no `quantize` (|Δ|·30÷dias ≥ 1e26) sai `null` + `saldo_ausente`; o `percentual` não faz `//` quando o alvo já foi batido.
  - `chega_em` = hoje + ⌈falta ÷ ritmo × 30⌉ dias, em `AAAA-MM`; `null` com ritmo `null`/≤ 0, alvo desconhecido (reserva sem custo) ou alvo batido (`falta` 0).
  - LIMITES declarados: o ritmo NÃO separa aporte de rendimento (a tela diz "inclui rendimento e variação"); portabilidade de outro banco conta como crescimento; mês de 30 dias; dia sem foto de uma posição dentro de conexão que tem foto no dia não é detectado; identidade que mudou de conexão cuja antiga está pausada/apagada começa do zero no passado (cresce de repente); foto antiga não confirmada, mesmo sombreada por uma mais nova confirmada da mesma identidade, invalida o dia (conservador); `falta` com `atual` ≤ −1e26 (saldo negativo absurdo) ainda estoura o `quantize` (não coberto; PR A, fora do ritmo); moeda e status do passado vêm do espelho da conexão da FOTO, não da vencedora de hoje (identidade que muda de moeda entre reconexões entra no passado e fica fora de hoje, onde o ritmo mostra `outra_moeda`); identidade sem espelho com foto em dois bancos conta uma vez no total e uma vez em cada banco no passado (os dois improváveis); uma leitura de fotos por composição de conexões distinta (cache por GET).

- **Dinheiro na v2 é `Decimal` e sai como TEXTO decimal** (`"1234.56"`, sem arredondar e sem
  float), em toda rota: no TS é `string`. A escala é a da coluna (`"1000"` e `"1000.00"`
  valem). O contrato vale para toda rota futura.
- **Contrato:** o envelope entra no OpenAPI como resposta `default` (`ErroV2`, em
  `api/v2/erros.py`; a resposta real continua saindo de `_envelope`). Os tipos TS saem de
  `python scripts/gerar_tipos_api_v2.py` para `webapp/src/dashboard/lib/api-v2.gen.ts`
  (gerado e commitado; construção fora da lista aceita levanta `ValueError`, e `number`
  (float) está fora dela: dinheiro é `Decimal`; a query de GET sai em `QueryGet`, só
  parâmetro `in: query`), e
  `tests/test_api_v2_contrato.py` compara o arquivo com o `openapi()` de hoje e valida as
  fixtures dos testes de navegador (`tests/frontend/api_v2_respostas.json`). Mudou modelo:
  rode o gerador e depois o build do `webapp/`.
- Chave: `DASHBOARD_V2_BETA_EMAILS` (sem a env = os e-mails de teste do beta de
  Agentes; definida e vazia = ninguém) e `DASHBOARD_V2_BETA_USER_IDS`.
- **Q36 fora do v2: Open Finance é a fonte única para quem tem a chave**
  (`core/services/fonte_unica.py`). Vale em todos os canais (`/app`, WhatsApp, IA do chat
  e do WhatsApp), porque a trava (`exigir`) está nas funções de escrita que todos chamam:

  | | onde trava |
  |---|---|
  | **bloqueado**: criar investimento manual e aportar nele | `db.create_investment`, `db.create_investment_db`, `db.investment_deposit_from_account` |
  | **bloqueado**: importar extrato (OFX no `/app` e no WhatsApp; CSV/PDF no WhatsApp) | `ofx_service.handle_ofx_import`, `statement_service.handle_statement_import` |
  | **bloqueado**: importar fatura OFX; compra manual no cartão (à vista e parcelada) | `ofx_service.handle_credit_ofx_import`, `db.add_credit_purchase`, `db.add_credit_purchase_installments` |
  | **liberado**: resgatar e apagar investimento manual, e desfazer o apagar (decisão do dono: restaura o que o usuário já tinha, sem dinheiro novo); caixinha manual; Carteira (lançamento em dinheiro, ajuste e saldo inicial; a Q40 continua em `core/handlers/forma_pagamento.py`); sync e importação do Open Finance | — |

  A exceção é `FonteUnicaOF` (`ValueError`, `codigo = "FONTE_UNICA_OF"`, o texto do caso
  em `str()`, como a `PlanLimitExceeded`); os textos (investimento, extrato, cartão) moram
  em `fonte_unica.MENSAGENS` (a exceção é o da receita, `fonte_unica.MSG_RECEITA`). `/app` = 400 com `detail` em texto; WhatsApp = o texto
  (`handle_incoming` traduz o que sobe, os handlers de cartão e de aporte devolvem); IA = o
  texto, e o `validate` de `create_investment`/`investment_deposit` recusa antes de pedir
  confirmação. A checagem da chave que falha **libera** (fail-open: é trava de produto num
  beta, não segurança). O usuário é sempre o da sessão/remetente, nunca o corpo. Escritor
  novo nessas tabelas ou dos importadores de arquivo reprova em
  `tests/test_fonte_unica_q36.py` até ser classificado (trava ou motivo de ficar livre).
- **Nota de revisão de 2026-10-09 (Q36 e Q40, que têm coortes diferentes):** ver
  [ADR 0006](adr/0006-declaracao-bancaria-despesa-receita.md), decidida pelo dono. **O código desta seção continua valendo até os PRs de código (A2, B, B1, C)**; o PR-A (#969, mergeado em 2026-10-15) é inerte (nenhum chamador de produção grava declaração enquanto o PR-B não existir e a flag dele estiver desligada) e por isso não mudou o comportamento; nada
  desta seção mudou na `main`. A **Q36** (esta seção) vale só para a chave `dashboard_v2_enabled`. A
  **Q40** (`core/handlers/forma_pagamento.py`, citada na tabela acima) vale para quem tem Open
  Finance conectado (`regra_ativa`), e **não** é parte da Q36 nem depende da chave. O que a ADR
  prevê: a Q36 deixará de travar investimento manual (PR-C) e compra manual em cartão não
  sincronizado (PR-B1), e continuará travando extrato OFX/CSV/PDF, fatura OFX e cartão
  sincronizado. Fora da chave do v2, quem tem banco, é Pro e importa OFX já pode dobrar hoje
  (risco existente); o dono decidiu em 2026-10-13 **preservar esse comportamento neste pacote**, e
  qualquer novo bloqueio de arquivo exige decisão separada. Na Q40, a resposta “banco” passará a
  gravar como declaração conferida com o extrato: o PR-B entrega o caminho atrás de uma flag que
  nasce desligada, e ligá-la é ato operacional separado. A conciliação (PR-A) e o lembrete de 7 dias (PR-A2; “foi em dinheiro” só age por ação e confirmação do usuário, e o PR-A2 inclui “trocar o par” e “desfazer a confirmação”, pela decisão do dono de 2026-10-13) vêm antes (ordem decidida pelo dono); o PR-A2 é pré-condição para ligar a flag, por decisão do dono. A Q42
  não muda.
- A página é `/painel` (`frontend/painel.html` + o artefato `frontend/dashboard-app.*`,
  de `webapp/src/dashboard`): sessão por `auth_token` ou `dashboard_token`
  (`_resolve_page_user_id`), senão `/login?next=/painel`; UA do app ou fora da chave
  (ou a chave falhando) vai para `/app`; depois os gates de plano e onboarding do `/app`.
  O `/auth/me` devolve `dashboard_v2_enabled`, que revela o link no menu do `/app`
  (fora do app). O `/painel` carrega o `/static/auth-refresh.js` antes do bundle: o 401
  de autenticação da `/api/v2` (só aceita `dashboard_token`/Bearer) é renovado e repetido
  por ele.
- **Erro no cliente** (`webapp/src/dashboard/parts/Entrada.tsx`): nada do painel monta
  antes do `/me`; qualquer erro é uma tela só, com texto fixo em português (a `message`
  do envelope não vai para a tela: em 402/404 ela sai em inglês), Recarregar e "Painel
  antigo" (o 403 `password_required` troca o texto e ganha o "Criar senha", para a
  `/home`, que não volta sozinha ao `/painel`: o texto manda voltar), **sem redirecionamento no cliente** — o Recarregar passa pelo `serve_painel`, que já manda cada
  caso ao lugar certo. Rede e 5xx tentam 3 vezes (com `networkMode: "always"`, para o evento `offline`
  não pausar o `/me` em "Carregando…"); 4xx (inclusive 429) nunca repete. Limite
  conhecido: conta agendada para exclusão leva 403 da `/api/v2` e o Recarregar serve a
  mesma tela, porque o `serve_painel` não barra exclusão (herdado do #659; o `/app`
  também não) — a única saída visível é o "Painel antigo".

### Autenticação

Sessão por **JWT em cookie `HttpOnly`** + **refresh token** (tabela
`auth_refresh_tokens`), com **CSRF por cookie `csrf_token`** (`SameSite=strict`) e
rate limiting via `slowapi` nos endpoints sensíveis (chave `rate_limit_key`, ver abaixo).

No cliente, `frontend/auth-refresh.js` faz *monkey-patch* de `window.fetch`: em 401
que não seja o próprio `/auth/refresh`, dispara o refresh, deduplica chamadas
paralelas e repete a request original. Se o refresh falhar, o 401 passa para o
chamador decidir. **Esse interceptor é global nas páginas autenticadas** — considere-o
antes de tratar 401 na mão em qualquer tela.

Caminhos de entrada, todos em `/auth/*`: `register` → `verify-email` (código de 6
dígitos) → `login`; **quiz de venda**: o webhook `POST /xquiz/webhook` (fora de
`/auth`, token `XQUIZ_WEBHOOK_TOKEN`) grava a verificação SEM senha e manda o código,
e a `/q` chama o mesmo `verify-email` depois de o usuário confirmar o e-mail na tela
(`quiz/resend` reenvia; `frontend/routes/quiz_signup.py`); a `/q` com `plano` na query vai
para a `/assinar` (nome, e-mail e WhatsApp no fragmento `n/e/w`); `forgot-password`/`reset-password`; **Google OAuth**
(`google/start`, `google/callback`, `google/complete-signup`, `google/pending/{token}`,
e `google/exchange`, que troca por Bearer o código que o callback devolve ao app nativo
quando o login começa em `google/start?app=2`); **Apple**, só no app nativo iOS
(`apple/exchange`, que verifica o identity token pelo JWKS da Apple e devolve sessão,
desafio de MFA ou cadastro pendente, e `apple/complete-signup`; o pendente mora na
mesma `pending_google_signups`, com `provider='apple'`);
`dashboard-link`/`dashboard-token` (link mágico); `link-code` (vincula WhatsApp e
Discord à conta); `logout`; `refresh`; `account` (exclusão) e `account/export`.

**Exclusão de conta (S1): imediata, sem carência nem cancelamento.** `DELETE /auth/account`
apaga no ato, com a frase fixa `EXCLUIR` conferida PRIMEIRO no servidor (sem banco, sem
gastar tentativa). O "modo" da conta vem de `db/reauth_codes.py::MODO_SQL` (fonte única,
derivada de `SEM_CREDENCIAL_SQL`): `senha` (senha + código por e-mail), `codigo` (conta só
Google/Apple: só o código) e `sem_credencial` (409 `PASSWORD_NOT_SET_MSG`, como no export).
O código de 6 dígitos sai de `POST /auth/account/reauth-code` (JSON `{}` com
`Content-Type: application/json`: sem corpo o middleware de CSRF devolve 403; tetos, validade
e tentativas vivem em `db/reauth_codes.py` e na rota `frontend/routes/conta_reauth.py`; só o
HMAC fica em `account_reauth_codes`). MFA ligado exige também `totp` (TOTP ou backup); código
e backup só ficam consumidos se a marca commitar. A marca (`schedule_account_deletion`, nome
histórico) põe `deletion_status='processing'` e revoga `auth_sessions` e
`auth_refresh_tokens` na mesma transação; `excluir_conta_marcada` (o mesmo corpo do job)
apaga no mesmo request. **Stripe (S2):** antes de apagar o banco (que leva o `stripe_customer_id`),
`excluir_conta_marcada` chama `core/services/stripe_exclusao.cancelar_assinaturas`: lê os
customers da própria conta em `auth_accounts`, expira os checkouts abertos de cada um (um
checkout pago depois da exclusão criaria assinatura sem conta; QR Pix pendente fica fora), lista as assinaturas vivas (inclui as com
`cancel_at_period_end`, `past_due`, `unpaid`), solta o `SubscriptionSchedule` e cancela na hora,
**sem reembolso** (`cancellation_details.comment = MARCA_EXCLUSAO`, que o ramo `deleted` do
webhook lê para não mandar e-mail "assinatura cancelada"). Levanta em qualquer dúvida (sem
chave, listagem falha, sobrou checkout aberto ou assinatura viva, assinatura com `finbot_user_id` de outro usuário). Ordem:
credenciais, marca `processing` e revoga sessões, cancela Stripe, apaga banco; por isso o cartão
nunca é cancelado por senha errada. **Corrida com checkout:** `cancelar_assinaturas` roda sob o MESMO advisory lock do checkout (`billing_checkout:<uid>`, o de `_billing_user_lock`), solto antes de `delete_user_data`; checkout que já estava sob o lock termina antes e é expirado ali, e quem toma o lock depois leva 403 da guarda dentro de `_billing_user_lock` (conta com `auth_accounts` em `scheduled`/`processing`, ou sem linha em `users`, já apagada; só-WhatsApp, `users` sem `auth_accounts`, segue), em create-checkout, bump e Pix. Falha no Stripe: a conta volta a `scheduled` vencida, a rota
responde 200 `deleting`, o job tenta de novo a cada rodada. **Resposta 200:** `{ok, user_id, status:"deleted"|"deleting", deleted,
deletion_scheduled_for, grace_days:0, message}` (os dois últimos campos de data e carência são
legados). `deleting` = a limpeza LOCAL falhou (`delete_user_data`: lock, deadlock) ou o
cancelamento no Stripe falhou; a conta volta a `scheduled` vencida e o job termina e manda o e-mail. A Pluggy é best-effort
(`db/privacy.py`): falha dela NÃO leva a `deleting`. **Erros** (`{"code","message"}` no
`detail`): 400 `confirmacao_invalida`, 409 `export_pendente` (link de export ≤15 min, que morre
com a conta), 503 `stripe_indisponivel` (pré-checagem: a consulta falhou, nada é gasto nem apagado; não há mais
409 `assinatura_ativa`), 400 `codigo_obrigatorio`, 401 `codigo_invalido`, 401
`codigo_expirado` (vencido, usado, esgotado ou inexistente), 400 `mfa_obrigatorio`, 401
`mfa_invalido`. Senha errada/vazia e sem credencial seguem como `detail` string (401 "Senha
incorreta.", 400 "Informe sua senha...", 409). Só o 401 de SESSÃO leva `WWW-Authenticate`;
os de credencial não (o interceptor do `auth-refresh.js` renovaria em vez de mostrar o erro).
Limite conhecido: token de dashboard LEGADO sem `jti` não é revogável por `auth_sessions` e,
passada a exclusão, ainda valida por até 12 h; todo emissor atual passa `jti`.
O export continua pedindo só a senha (modo `codigo`: sem reautenticação) e devolve
`email_mascarado`.

**Deep link do WhatsApp: um construtor só, `core/dashboard_links.link_whatsapp(texto)`**
(Grupo 7, PR-5b). Lê `WHATSAPP_NUMBER` a cada chamada, monta `api.whatsapp.com/send?phone=N&text=...`
e, sem número, devolve `""` (falha fechada: o `wa.me/?text=` abre o seletor de contatos e o
código de vínculo, de uso único, iria para quem a pessoa escolhesse). Só texto sem segredo (`/wa`,
e-mail do fundador) passa `seletor=True` e cai em `wa.me/?text=...`. Um teste (`tests/test_whatsapp_link_unico.py`) falha se `api.whatsapp.com`
ou `wa.me/` aparecer em outro `.py`/`.js`/`.html`. As rotas que devolvem `whatsapp_link`
(`link-code`, `login`, `verify-email`, `mfa/verify-login`, `google/complete-signup`) passam
`vincular <link_code>`: o site pré-preenche o mesmo texto que o parser do bot entende
(`account.vincular`), e o `/comecar` mostra essa mensagem ao lado do botão. `/wa`, `/teste` e o
e-mail do fundador passam uma saudação, sem código. **O e-mail de boas-vindas não leva
código** (vale 15 min e o e-mail é lido depois, e a conta do quiz não tem senha, que o
`link-code` exige): o botão "Conectar meu WhatsApp" abre `/login?next=%2Fhome`, e lá
"Conectar WhatsApp" gera o código no clique.

**PKCE no Google do app nativo** (#604, `core/services/pkce.py`; só S256). O app manda
`code_challenge` em `google/start?app=2` (inválido → 400), que o guarda no cookie
`google_oauth_challenge` (nunca no `state` do Google), e o `code_verifier` no CORPO de
`google/exchange` e `google/complete-signup`. O callback `nat-` grava o challenge em
`dashboard_sessions.code_challenge` e `pending_google_signups.code_challenge`; nulo
significa sem PKCE (web, iOS antigo, Apple, link do bot). **Igualdade estrita nos dois
sentidos** (`consume_dashboard_session`, `consume_pending_google_signup`): com
`code_verifier` só se consome o código/pendente cujo challenge é `S256(verifier)`; sem
verifier, só o que NÃO tem challenge. Daí: `/d/{code}` e `dashboard-link` (sem verifier)
não consomem código do app; e código/pendente SEM challenge (injetado por outro app, do
bot, ou de cookie perdido) não é trocável por quem manda verifier, que é o app novo, então
a injeção de código não entra. O que não casa recusa antes de qualquer sessão e NÃO queima
o código nem o pendente. Cookie do challenge presente mas que não é um S256 de 43 chars
`[A-Za-z0-9_-]` faz o callback falhar fechado. A Apple não tem PKCE: o verifier é ignorado
no `apple/complete-signup`.

Limites conhecidos:
- O iOS antigo, sem verifier, continua na janela: seu código sem challenge é trocável por
  quem o interceptar. Fecha quando o challenge virar obrigatório (app antigo fora de
  circulação).
- Cookie do challenge AUSENTE no callback (perdido no caminho) gera código sem challenge,
  que o app novo, mandando verifier, recusa: falha fechado e a pessoa tenta de novo. A
  validar no aparelho Android: se o cookie `google_oauth_challenge` sobrevive ao Custom Tab
  (se não, o login Google do Android falharia sempre).
- A PKCE não cobre um fluxo iniciado pelo próprio sequestrador do scheme no aparelho (ele
  abre `/auth/google/start` e usa o próprio par verifier/challenge); a defesa estrutural
  seria App Links https no Android no lugar do scheme customizado, follow-up da #604.
- Cookie tossing por subdomínio comprometido permite plantar state + challenge: é o mesmo
  pressuposto do login CSRF por state, não uma regressão da PKCE.

Resíduo próprio, que NÃO fecha com o challenge obrigatório: `GET google/pending/{token}`
devolve e-mail e nome sem exigir o verifier, porque a rota não o recebe. Fechar pede mudar
a rota (e o contrato com o app).

**MFA no Google web e no magic link (#605).** Conta com MFA ligado NUNCA ganha sessão só
pelo Google ou pelo link do bot. O callback web e `GET /d/{code}` (todo código: bot ou app
antigo) respondem 302 para `/login?next=<destino>#mfa=<desafio>` sem cookie nem
`auth_sessions`; o desafio vai no fragmento (não chega ao servidor nem ao Referer) e o
`login.html` abre o formulário de código; a sessão nasce em `mfa/verify-login`, que também
registra o login (o callback `app-` não registra mais quando há MFA). O desafio vem de
`_desafio_mfa`, o mesmo de `_concluir_login`. O `next` de `/d/` só chega ao destino se
estiver na allowlist do `login.html`.
Exclusão agendada (#989): `/d/` checa logo depois de consumir o código e ANTES de
`_desafio_mfa` (403 pela página de erro, sem desafio, cookie nem `auth_sessions`);
`mfa/verify-login` checa depois da reserva da tentativa e ANTES de consumir o TOTP (400
`conta_em_exclusao`; o desafio e o código seguem valendo se a exclusão for desfeita).
`tests/test_login_exclusao_vinculo.py` (x1 a x4).

Auto-vínculo Google/Apple por e-mail (#988): `_vincular_se_permitido` (ao lado de `_mfa_ligado`)
só grava `auth_identities` se NÃO há exclusão agendada E `not _mfa_ligado`; é o único caminho de
produção até `link_google_identity` (callback web/app do Google e `POST /auth/apple/exchange`). Conta
com MFA entra como antes (o e-mail do provedor casa a cada login, mais o código), só não ganha o
vínculo antes de provar o segundo fator. Limites, sem código:
- Conta SEM MFA continua auto-vinculada por e-mail. Follow-up (issue); por isso o #988 fecha só a
  parte do MFA e da exclusão.
- MFA com setup pendente (`user_mfa.enabled = false`) conta como desligado, coerente com `_desafio_mfa`.
- Janela de milissegundos entre `_mfa_ligado` do helper e o de `_desafio_mfa` (TOCTOU): MFA ligado
  nesse intervalo ainda deixa o vínculo nascer.
- Apple com MFA e sem vínculo depende do claim `email` em todo login (sem ele cai em
  `apple_sem_email`). NÃO verificado: só se prova com Apple real no aparelho.
`tests/test_login_exclusao_vinculo.py` (y1 a y5), `test_auth_apple_troca.py` (a6, a6c, a6d) e
`test_google_web_mfa.py` (c3).

O `#mfa=` sai da URL num script no topo do `<head>` do `login.html`, ANTES dos snippets de
GA4/Meta Pixel que `inject_tracking` põe no fim do `<head>` (o GA4 lê `location.href` no
`config`); `tests/test_google_web_mfa.py::test_e6` e `tests/frontend/login_mfa_tracking.test.mjs`
prendem isso. Aceitos, sem código:
- Com `#mfa=` na URL o `login.html` não consulta mais a sessão viva (#991), então o formulário abre
  mesmo com a sessão de outra conta. Resíduo (A5, anterior ao #991): depois do `replaceState` que tira
  o `#mfa=` da URL, RECARREGAR o `/login` com sessão viva de outra conta roda o `/auth/validate`
  normal e pula o formulário (não vaza dado, só perde o formulário).
- `/d/` com falha em `get_auth_user`/`get_mfa_status` responde 500 e queima o código (fail-closed:
  pedir link novo).
- Recarregar o `/login` depois do `replaceState` perde o formulário de código (o desafio saiu da URL);
  no `/d/` o código do link já foi queimado, então recomeça por senha ou por um link novo.
- O link do bot COM MFA passa a registrar login e e-mail de novo IP no `verify-login`; o SEM MFA segue
  sem registro. Assimetria de auditoria conhecida, não decidida.

**IP do cliente: `core/client_ip.py`** (`client_ip`, `rate_limit_key`; #766). Atrás de
Cloudflare → Railway, o `request.client.host` é o proxy do Railway (100.64/10). Com
`CLOUDFLARE_ORIGIN_SECRET` (≥ 32 chars), o `CF-Connecting-IP` só vale quando a regra da
Cloudflare manda o mesmo valor em `x-pigbank-cf-secret`; sem ela, o comportamento antigo
(conexão da Cloudflare). Configuração em `.env.example`; a regra inteira e os riscos
residuais, no docstring do módulo. A sonda `client_ip_sonda` (`system_event_logs`) mede.
Todo IP gravado ou exibido (auditoria, `auth_login_events`, sessão, admin, export) usa
`client_ip`; todo teto por IP (slowapi, `_check_auth_rate_limits`, quiz) usa `rate_limit_key`.
Nunca ler o peer nem cabeçalho de IP direto: `tests/test_client_ip_fonte_unica.py` reprova.
Limite conhecido: o detector de pico de falha de login (`core/services/security_alerts.py`)
agrupa por `auth_login_events.ip_address`, o IP completo — IPv6 não vira /64 ali, então quem
tem um bloco troca de endereço dentro do próprio /64 sem somar no balde. Só o limitador agrupa /64.

**Conta pela `/assinar` (funil v3 do quiz): `POST /auth/quiz/conta`**
(`frontend/routes/quiz_signup.py`, com CSRF). Recebe e-mail, nome, WhatsApp
(obrigatório) e o aceite dos termos, e cria a conta **sem senha e sem código** na
mesma requisição (`db/signup_quiz.criar_conta_sem_codigo`, que NÃO passa por
`email_verification_codes`), já logada. Responde `criada`, `logado` (a sessão do
pedido já é dessa conta), `tem_conta`, `cadastro_pendente` (há código de
`/auth/register` vivo para o e-mail: alguém está no meio do cadastro, e o código dele
não é tocado) ou `ocupado` (409: outro pedido do mesmo e-mail está com a trava; a rota
não espera, para uma rajada não segurar o pool de conexões). Só `criada` escreve e dá
sessão. É o único lugar do site que diz se um e-mail tem conta (aceito pelo dono), com
10/h por IP (balde `quiz`, chave `rate_limit_key`) e 3/h por e-mail (balde `quiz-conta`, separado do
`register` para o anônimo não gastar o teto do cadastro da vítima). A prova do e-mail vem depois, no "Crie sua senha".
A página é `frontend/assinar.html` + `assinar.js`: o script limpa o fragmento antes do
Pixel e do GA4 (sem Clarity), percorre os estados formulário → já tem conta →
pagamento, e todo caminho para o Stripe hospedado (app, Stripe.js que falha, 10 s sem
iframe, link manual) passa por um só `irParaHospedado`; testes em `tests/frontend/assinar_*`.

**Conta sem credencial: 403 `password_required`.** Conta sem senha (`''` conta como sem)
e sem identidade Google/Apple (`db.conta_sem_credencial`, a fonte única, a mesma que o
job do e-book usa; sem linha em `auth_accounts`, o só-WhatsApp, é False) não lê nem grava dado, mesmo
paga, até criar a senha pelo link do e-mail. Vale no servidor: a perna da credencial do
`_enforce_subscription_gate` (depois das duas do 402; não lê `ACCESS_GATE_ENABLED` nem
`PLANS_V2_ENABLED`, porque é segurança e não cobrança) e `shared.exigir_credencial` nos
pontos fora dele; o `/ws` fecha com 4403, o `/conta` manda para a `/home`, e o bot não
liga o número pelo telefone (responde com o texto fixo). O bot também barra toda
mensagem de número já ligado a conta sem credencial.
**O telefone digitado no site não liga o WhatsApp a conta nenhuma: só o código liga (Grupo 7, PR-5, D1(c); fecha #711/#630 e o V3).**
O site não verifica o telefone (#610), então ele não prova de quem é o número: o V3 era B digitar
o número de C, C mandar "oi" e o bot ligar C na conta de B. `attempt_whatsapp_phone_link`
(`db_support.attempt_whatsapp_phone_link_impl`; o nome ficou, renomear toca ~16 testes) agora só
CLASSIFICA o número: `already_linked` (o remetente já é conta: `auth_accounts` ou identidade que não é
WhatsApp; o telefone é lido no mesmo SELECT só para devolver o `target_user_id` do opt-out, e não decide o status), `precisa_senha` (o telefone casa, pelos `utils_phone.candidatos_simetricos`
#909, com conta SEM credencial: a do quiz/lead; é a leitura que decide o status, para o texto dela e o opt-out do
lead), `remetente_com_dados` (o mesmo, com remetente só-WhatsApp que tem dados: o turno segue na conta dele) e
`no_match`, que é todo o resto, inclusive o telefone de conta COM senha. Nenhum caminho do telefone grava
identidade nem mescla; `JA_E_CONTA_WA` e `NAO_JUNTEI_WA` saíram. `no_match` recebe o convite
(`_send_no_account_notice`: "Conectar WhatsApp" no site e `vincular 123456`) em toda mensagem que
não é tentativa de código, e a tentativa de código passa também no `precisa_senha` (senão o dono do telefone
digitado por outra conta ficava trancado). `merge_users` e as recusas (`apagaria_login`, #970) não mudam: o
código passa por elas. `link_platform_identity` (por onde `link` e `vincular` passam) é agora o ÚNICO
gravador de `whatsapp_verified_at` e `phone_status='confirmed'`, e só quando o número ligado casa com o
`phone_hash` da conta: número diferente do digitado liga sem confirmar o telefone. O checklist da Início
(`whatsapp_linked` do `/auth/dashboard-profile`) lê a identidade, não o marcador. Sucesso do código:
evento `whatsapp_code_link_success` (só o número mascarado; substitui `whatsapp_auto_link_success`).
**Resíduo (DP-2):** vínculos feitos pelo auto-vínculo antes do PR-5 continuam valendo: não há como separar o
legítimo do V3 (o dono e a vítima têm o mesmo registro), e reauditar derrubaria gente legítima. O suporte
desfaz se a pessoa reclamar.
**Resíduo conhecido (A2):** `vincular` consome o código de conta sem credencial (a do quiz), que o `/auth/link-code` recusaria; follow-up.
E o próprio `merge_users` recusa (`apagaria_login`) a junção com login nos dois
lados, para todo chamador: ela apagaria o login da origem (#635), e fica bloqueada até existir
confirmação explícita; origem com login e destino sem login junta, e o login migra.
A exceção do bot são os botões de opt-out de `_WA_INTERACTIVE_ISENTOS` (relatórios diário,
semanal e mensal, e atualizações): quem não pode usar tem de conseguir parar de receber
mensagem, então eles funcionam no número já ligado e no `precisa_senha` (desligam a
preferência da conta sem credencial). Os botões de relatório gravam só a preferência; o "parar
atualizações" também apaga pendências do remetente (abaixo). O texto digitado
que for, inteiro e normalizado (sem caixa, acento nem pontuação), `parar`, `stop`, `sair`,
`parar mensagens`, `parar de receber` ou `nao quero mais receber` (`wa_runtime.WA_PARAR_TEXTOS`,
decisão do dono; "pare" e "para" ficam fora, são palavras de cancelar do fluxo de valor
pendente) vale como o botão de "parar atualizações" nesses mesmos pontos e no fluxo comum,
antes das pendências e do paywall. "parar report diario" não casa e segue para `report.disable`.
O "parar atualizações" (botão ou digitado; NÃO os botões de relatório) também apaga as
pendências de confirmação do bot (`pending_actions`) e da IA (`ai_pending_actions`), para um
"sim" depois não executar o que a pessoa mandou parar, e encerra a pergunta aberta da IA
(`finally` do `process_message`: todos os ramos menos `conta_sem_credencial`, onde a âncora
nem é lida). A limpeza vale só para o remetente (quem conversa: `uid` do fluxo comum e do
`remetente_com_dados`, a própria conta no `conta_sem_credencial`, o usuário do WhatsApp no
`precisa_senha`), nunca para a conta sem senha que digitou o número: o opt-out vale para as
duas, as pendências são de cada um. Se a gravação ou a limpeza falha, responde
`OPT_OUT_FALHOU_WA` (nunca silêncio; falha só da limpeza deixa o opt-out gravado e um 2º PARAR
conserta). Nada no bot religa o opt-out (só a tela de
Configurações). Nos dois ramos sem senha, quem nunca comprou
(`db.remarketing.ja_comprou` falso) recebe `CONVITE_LEAD_WA` no lugar de `PRECISA_SENHA_WA` e
`PARAR_LEAD_WA` ao digitar PARAR; quem comprou, os textos de antes. `ja_comprou` que levanta vale
como "comprou" (log, e a mensagem segue).
Saem livres as rotas da própria conta (`authorize_account_access`), o `PATCH /settings/{id}/security/contact`
(`exige_credencial=False`), o `/auth/me` (campo `precisa_criar_senha`), login,
logout, refresh e o reset. Quem bloqueia e quem libera, rota a rota, está em
`tests/test_rotas_senha_obrigatoria.py`, que reprova rota nova sem linha. Na tela, a
`/home` e o `/app` carregam `frontend/criar-senha.js`: overlay que não fecha, também
disparado por qualquer 403 `password_required`. A `/settings` não o carrega (é a saída):
com `precisa_criar_senha`, ela mostra só a Segurança (o link da senha no topo) e não chama
os carregadores que dariam 403 (#758). O `/auth/me` é rebuscado no PTR e na volta do foco
(`visibilitychange`/`pageshow`, que só escutam com a conta travada ou com o boot sem
`/auth/me`), e a página recarrega nos dois sentidos quando ele discorda da tela: destrava
quando a senha passou a existir, trava quando a tela estava livre (inclusive por boot com
`/auth/me` falho) e a conta precisa de senha. Não recarrega com rascunho ou modal aberto
(`RECARGA_PERDERIA` em `settings.html`); o próximo gesto refaz a decisão. E o convite do
MFA fica calado no servidor enquanto não há credencial.

**Os três criadores de conta** (o `confirm` do register, o `complete-signup` do
Google/Apple e a `/assinar`) gravam pelo mesmo `db_support.inserir_conta_nova`:
trava por e-mail + `on conflict (email) do nothing`. O e-mail que ganhou conta no meio
é **recusado**, nunca fundido; o `verify-email` responde "Este e-mail já tem conta" e a
saída é o "Esqueci a senha". A sessão, as atribuições (afiliado, prospecção, quiz) e o
CAPI CompleteRegistration de conta nova moram num helper só, `_sessao_de_conta_nova`
no monólito, usado pelas três rotas.

### MFA

TOTP (`pyotp`) com códigos de backup: `/auth/mfa/setup`, `enable`, `disable`,
`verify-login`, `status`, `regenerate-backup-codes`, `onboarding-seen`. Tabelas
`user_mfa`, `user_mfa_backup_codes`, `mfa_login_challenges`; segredo cifrado com
`MFA_ENCRYPTION_KEY`.

**Os códigos de backup aparecem uma única vez.** Qualquer tela ou refresh que passe
por cima deles perde os códigos do usuário — já quase aconteceu (registro no §4 do
`CLAUDE.md` da raiz).

### WebSocket

`ConnectionManager` + endpoint `@app.websocket("/ws/{user_id}")` no monólito. O
dashboard pede dados por ele (pergunta e resposta); empurrar algo sem o cliente pedir
só acontece em `open_finance_synced`, do fim do sync do Open Finance e do "Recomeçar do
zero" — confira com `grep -rn "broadcast_to_user(" --include="*.py" frontend/ core/`. Os
mesmos 2 avisos também saem pelo `/api/v2/eventos` (`eventos.avisar`).
**Lançamento feito pelo WhatsApp não avisa o `/app` pelo `/ws`** (o `/painel` é avisado pelo
trigger de escrita, ver `/api/v2/eventos` em "API v2"). Mudou o formato
de mensagem? Os dois lados mudam junto — o consumidor está no `dashboard.js`.

### Pagamentos

Stripe: `/billing/create-checkout`, `/billing/checkout/bump` (página própria, abaixo),
`webhook`, `portal`, `subscription`,
`change-plan`, `cancel-change`, `plans-config` e `select-free` (esta só RECUSA
com 410: a escolha do plano Grátis saiu da /precos em 2026-09-02; a rota
sobrevive pra devolver `detail.message` a cliente antigo em cache).

**`POST /billing/portal` aceita `?voltar=`** (query, opcional): o cliente só escolhe uma chave de `_PORTAL_VOLTAR` (`/painel` volta para `/painel`, `/app` para `/app`); valor fora da lista (inclusive vazio) é 400 "Destino de retorno inválido." antes de qualquer chamada ao Stripe, e o valor nunca entra na URL. Sem o parâmetro a volta é `/app`, como sempre; o `GET /conta` (que o resto do site e o WhatsApp usam) volta para `/settings` e não mudou. Consumidor: a Cobrança do `/painel`. O Stripe aceitar o `return_url` de produção só se prova no staging ou depois do deploy.

**Funil (`checkout_funnel_events`):** além de `started`/`completed`, grava `viewed_pricing`
(GET `/precos` de usuário logado, 1 por janela móvel de 24h) e `expired` (webhook
`checkout.session.expired`). Esse evento também dispara quando o app troca/expira a sessão
aberta (`finance_bot_websocket_custom.py`, ao reaproveitar/expirar sessões em
`/billing/create-checkout`), então `expired` NÃO é abandono: abandono confiável = abriram − concluíram.
Quem consome a tabela (painel de funil): `started` e `expired` repetem por `session_id`
(reuso de sessão, reentrega do webhook) → `COUNT(DISTINCT session_id)`; `viewed_pricing` repete a cada 24h
por usuário e a dedupe é racy → `COUNT(DISTINCT user_id)` na janela. `viewed_pricing` é só de usuário
LOGADO (quem vem do quiz anônimo não entra; entram também quem já paga e abre a /precos, e quem o
`gate_plan_selection` redireciona): é "logado que viu a /precos", não "visitante". Sem backfill: antes do
deploy `viewed_pricing` e `expired` são zero, então taxa vista→iniciou não vale numa janela que cruza o
deploy (usar `min(created_at)` do kind como "medido desde"). Exclusão de conta zera `user_id`
(`viewed_pricing` não tem `session_id`, então some de qualquer distinct por usuário).

**`/billing/create-checkout` serve a `/precos` e a `/assinar`.** O corpo ganha
`origem` (`"precos"` default | `"assinar"`; outro valor é 400) e `embutido` (default
`false`). Hospedado responde `{checkout_url, interval, plan}`; embutido responde
`{client_secret, publishable_key, trial_days, interval, plan}` (`ui_mode="embedded_page"`,
`return_url` = a mesma URL de sucesso do hospedado). O `session_id`
nunca vai no corpo. A sessão grava `origem` e `td` (dias de trial) no metadata e no
da assinatura; uma sessão aberta só é reaproveitada pelo mesmo plano × intervalo ×
origem × modo (sessão sem `origem` = `/precos`), e a embutida reaproveitada devolve o
trial com que nasceu (`td`). Só a `/assinar` (e a página própria, abaixo) fixa BRL (`adaptive_pricing` off) e volta
para `/assinar?plano=&ciclo=` no abandono. Os produtos extras (`optional_items`) vão nas
**duas** origens (dono, Q3); fora eles e as chaves `ebook*` da foto, a `/precos` segue
com os kwargs de antes. Toda sessão da `/assinar` (embutida **e**
hospedada), e todo embutido, expira em 1 h (`expires_at`): o default de 24 h do Stripe
deixaria aberta a janela de cobrança dupla (Pix numa aba, cartão na outra); o
hospedado da `/precos` segue sem. Envs:
`STRIPE_PUBLISHABLE_KEY` (sem ela o embutido é 503, antes de tocar no Stripe),
e os produtos extras em **variáveis numeradas** (`core/services/extras_assinar.da_env`,
lidas a cada sessão): slot 1 = `STRIPE_PRICE_ID_EBOOK` + `EBOOK_URL`, slot n (2..10) =
`STRIPE_PRICE_ID_EBOOK_n` + `EBOOK_URL_n`. Teto de 10 (limite do `optional_items` do
Stripe); a ordem no checkout é a do número do slot, e um buraco (slots 1 e 3) mantém a
ordem. Cada slot vale sozinho: só é oferecido com **as duas** preenchidas (preço sem URL
venderia o que o webhook não tem como entregar) e a URL com no máximo **500
caracteres** (limite de metadata do Stripe, medido); senão sai o warning
`ebook_nao_oferecido` com o slot e o tamanho, **nunca a URL** (ela é o acesso ao PDF
pago), e os outros slots seguem. Preço repetido entra uma vez (o primeiro) e avisa
igual. Os oferecidos vão de foto no metadata da sessão e no da assinatura
(`extras_assinar.para_metadata`), numerados pela **posição** na lista oferecida, não
pelo slot da env: o 1º sempre em `ebook_price`/`ebook_url` (com um produto, o metadata
de antes), o 2º em `ebook_2_*`, etc.; sem produto as chaves não existem. Com 10
produtos e o rastreio são 29 chaves (o Stripe aceita 50). O webhook identifica os
produtos por essa foto, nunca pela env do momento. A sessão hospedada da `/precos`
dura 24 h (sem `expires_at`): para **tirar** um produto, apague as variáveis, espere o
deploy e mais 24 h, e só então arquive o preço no Stripe. Se o Stripe recusar os extras
(`InvalidRequestError`: preço arquivado, inexistente), o checkout é refeito UMA vez sem
`optional_items` e sem a foto `ebook*` nos dois metadatas, e loga o erro
`ebook_oferta_recusada` (mensagem do Stripe e preços, nunca as URLs): a página segue
vendendo o plano e TODOS os extras somem daquela sessão (não só o recusado). Sem extras, ou recusado também sem
eles, ou erro que não é `InvalidRequestError`, é o 502 de antes. Uma sessão aberta
criada pelo fallback (sem extras) ou antes de mudar a lista é reaproveitada por até 1 h
(`/assinar`) ou 24 h (`/precos`) e segue sem os extras novos, mesmo depois de a env ser
corrigida: o reaproveitamento não compara os extras.

**Página própria (`ui_mode="elements"`), atrás da flag `CHECKOUT_PAGINA_PROPRIA=1`.**
O corpo ganha `pagina` (default `false`); vale só com a flag
(`extras_assinar.pagina_propria_ligada`, lida a cada pedido). Com a flag desligada, ou
sem `pagina`, o checkout é o de antes nas duas origens (a `/precos` com `pagina` segue
no hospedado, nunca no embutido). Ligada, `pagina` vira sessão `elements` (é um
embutido: `client_secret`, `return_url`, 1 h, `publishable_key`) **sem**
`optional_items`: os extras entram como linha do carrinho pelo `POST /billing/checkout/bump`
(abaixo). Até 3 extras (`extras_assinar.CAIXAS`): `da_env()` filtrado por
`Price.retrieve(expand=["product"])` (preço ativo, BRL, avulso, produto ativo) ANTES
de recortar; o que sai, ou uma falha do Stripe ao ler (aí nenhum entra), loga
`ebook_oferta_recusada` só com os preços, e o plano vende assim mesmo. A foto dos 3 vai
nos dois metadatas, como no embutido. Recusa do Stripe na criação é o 502 de antes
(não há `optional_items` a tirar). A resposta é a do embutido + `pagina: true` +
`extras: [{posicao, nome, descricao, imagem, valor_centavos, no_carrinho}]` (texto e
`images[0]` do Product; capa só `https://`, senão `null`). A sessão `elements` só
reaproveita pedido com `pagina` e flag ligada, e vice-versa (o matcher compara o
`ui_mode`, que o `Session.list` devolve como `elements` ou `embedded_page`, medido em
2026-10-03); a reaproveitada devolve as caixas da SUA foto e marca `no_carrinho` pelo
`list_line_items` (falha = 503). A reaproveitada NÃO refiltra a foto (um preço
arquivado depois do nascimento segue na caixa), e uma falha do Stripe ao ler qualquer
extra na criação tira TODAS as caixas (o plano vende sem elas). A página própria fixa
BRL (`adaptive_pricing` off) nas **duas** origens: medido no Stripe de teste em
2026-10-03, sem o campo a sessão `elements` da `/precos` nasce com ele LIGADO (o
default da conta), e as caixas mostram R$. O hospedado da `/precos` segue sem o campo.

O `GET /billing/plans-config` expõe a flag como `pagina_propria`; com ela, o deslogado
que escolhe um plano no cartão na `/precos` vai direto à `/assinar?plano=…&ciclo=…` (sem
`/cadastro`). No frontend, a `/assinar` manda `pagina: true` (e `origem` da query: só `precos`, senão
`assinar`) e, se a resposta trouxer `pagina`, monta `frontend/pagamento-pagina.js`
(Payment Element só cartão, resumo pelo `change` do Stripe, as caixas e o cupom
"Tem cupom?" → `applyPromotionCode`); sem `pagina`, o embutido de antes. O Stripe.js é o
`endive` na página própria e o `dahlia` no embutido, carregado UMA vez pelo `assinar.js`
(uma 2ª versão é recusada e cai no plano B; o embutido não depende do arquivo novo).
Cada caixa marcada chama o `/billing/checkout/bump` dentro do `runServerUpdate`, com
tudo travado; falha → as caixas voltam ao último conjunto aceito. **Pagar sincroniza o
estado da tela ANTES do `confirm`** (o que a tela mostra é o que se cobra); falha → não
confirma. Prazos: `loadActions` 10 s e `/bump` 15 s (plano B / caixas voltam); o relógio
de 10 s olha `#pagamento iframe`. Sessão fechada (409 `sessao_fechada`, ou
`session.status.type === "expired"` no `change`, no `loadActions` ou depois de um confirm com erro)
→ S3 com "Tentar de novo". O dinheiro da sessão é formatado com `currency` e
`minorUnitsAmountDivisor` lidos dela, junto com o `total.total.minorUnitsAmount`: o SDK exige
essa leitura, senão o `confirm` lança (docs.stripe.com/js/custom_checkout). Limites aceitos: rede lenta que passa dos 10 s do
`loadActions` vai ao plano B; um `/bump` que volta depois do prazo é corrigido pela
sincronização do Pagar; o Pagar faz 1 `/bump` de sincronização quando há caixas (conta nos 120/h por IP). Testes:
`tests/frontend/pagamento_pagina*.test.mjs`. As carteiras (Apple Pay/Google Pay) são o Express Checkout em
`#pp-express`, acima do Payment Element (que fica com `wallets` "never", sem botão em dobro): sem altura até o
`availablepaymentmethodschange` trazer botão, `inert` com `/bump`, cupom ou pagamento em voo (a folha nunca abre
com o carrinho mudando; ela mostra o total da sessão, que é o que se cobra). O `click` do Express só chama o
`resolve` (que abre a folha) com nada em voo, e então trava caixas, cupom e Pagar até o `cancel` ou o `confirm` —
no Google Pay do desktop a folha é um popup e a página segue clicável por baixo. `confirm` com pedido em voo, ou
sem folha aberta numa montagem que já viu um `click` (tardio, depois de um `cancel`), é recusado: `paymentFailed()`
no evento e o aviso "Pagamento não iniciado. Tente de novo." (nunca em silêncio); senão, `confirm` →
`actions.confirm({expressCheckoutConfirmEvent})`. O Apple Pay do Safari parece não mandar o `click` (staging,
2026-10-05: a folha abria, girava e fechava sem nenhum PaymentIntent; causa provável, a confirmar no reteste — a
alternativa é o `confirm` nem chegar à página); sem `click` a trava do carrinho
fica por conta da folha ser modal (iPhone). Risco aceito: numa folha não modal sem `click`, um `/bump` que termina
antes do `confirm` cobra o total novo, que a folha não mostrou. Pré-requisito: o domínio registrado em "Domínios de métodos de
pagamento" do Stripe no modo TESTE (staging) e no LIVE (produção) — sem isso os botões não aparecem. O desenho das
caixas mora em `frontend/bump-caixas.js` (PR C, abaixo); o do resumo e do botão, em `frontend/pagamento-caixas.js`. Testes: `tests/frontend/pagamento_express.test.mjs`.

A `/precos` (`startCheckout`) manda `pagina: true` só fora do app (`window.PB_IN_APP`: no
app a `/assinar` vai ao hospedado, e uma sessão `elements` criada antes seria expirada e
refeita) e, com a resposta `pagina`, navega para
`/assinar?plano=…&ciclo=…&origem=precos`, que reaproveita a sessão; o
InitiateCheckout/begin_checkout fica para a `/assinar` (perda conhecida: o begin_checkout
de lá vai sem `value`). Sem `pagina` na resposta, o hospedado de antes, com o rastreio de
antes. Limite aceito: cada compra pela `/precos` gasta 2 chamadas do limite de 20/h do
create-checkout (a da `/precos` e a da `/assinar`). Testes:
`tests/frontend/precos_pagina_propria.test.mjs`.

**`POST /billing/checkout/bump`** (`frontend/routes/billing_bump.py`): o order bump da
página própria. Corpo `{sid, posicoes}` = o CONJUNTO desejado inteiro, em posições da
foto (`[]` = nenhum); o preço sai sempre da foto da sessão, nunca do cliente. Campo a
mais (ex.: `price`) é 422; `sid` fora de `cs_(test|live)_…`, posição repetida, fora de
1..10 ou além da foto é 400. Sessão de outra conta (`finbot_user_id` exato **e**
`customer` da conta), de outro `ui_mode` (só `elements`) ou de outra origem (só
`assinar`/`precos`) é 404 indistinguível de "não existe", sem `modify`; o dono é
checado ANTES do estado. Sessão paga, expirada ou `open` com `expires_at` vencido é 409
`sessao_fechada`. O carrinho (`list_line_items`) vira `extras_assinar.linhas_do_bump`:
o plano e o extra que fica vão pelo `id`, o que entra por `price`, o que sai é omitido;
nada mudou = sem chamada ao Stripe. O `Session.modify` leva só `line_items` (a foto do
metadata não muda). Recusa do Stripe no `modify` (`InvalidRequestError`) é 409
`extra_recusado` + log `ebook_oferta_recusada` só com os preços; qualquer outro
`StripeError` (retrieve, list, modify) é 502. Roda sob `_billing_user_lock` (o mesmo
do checkout), limite de 120/h por IP, CSRF do middleware global, e **não olha a
flag**: com ela desligada não nasce sessão `elements`, e a página já aberta segue pagável.

**Retomada do checkout abandonado (PR 4 de `docs/plano-remarketing.md`; inerte até o PR 6
emitir um token).** `GET /retomar?t=…` (`frontend/routes/retomada.py`, só 303) troca o token
Fernet (`core/services/retomada.py`, rótulo próprio de `JWT_SECRET`, 7 dias, `user_id` +
etapa 1–3) pelo cookie `pb_retomada` (`HttpOnly`, `Secure`, `SameSite=Strict`, 2 h; o valor é
um token próprio do cookie, emitido no clique, com rótulo e validade de 2 h no servidor: o token do
link não serve de cookie) e marca `remarketing_envios.clicado_em`. O cookie NÃO é sessão: só
a dependência `comprador`/`comprador_pix` o lê — em `POST /billing/create-checkout`,
`POST /billing/checkout/bump`, `POST /billing/pix/checkout`, `GET /billing/pix-extras`,
`GET /billing/pix/{token}` e `GET /billing/plans-config` (`retomada: {email: "l•••@dominio"}`).
Sessão válida vence; o cookie só vale se o pedido não traz cookie de acesso, cookie do painel
nem `Authorization`; credencial de sessão inválida segue 401. As outras rotas
continuam com o resolver de sessão delas e dão 401 com só o cookie. A sessão do Stripe leva `rm_etapa` (sessão e
assinatura) e volta em `/retomar/pronto?sid=…` (rota provisória até o PR 5);
`_checkout_session_matches` compara o valor de `rm_etapa` e não mistura sessão de retomada
com a logada. O Pix recusa
`confirm_cancel_stripe` com 409 `retomada_sem_migracao`. `POST /billing/retomada/senha` (3/h,
CSRF, só com cookie e sem sessão) reaproveita o reset de senha só para conta sem credencial,
com resposta igual nos dois casos, e expira o cookie. O `POST /auth/logout` e o
`DELETE /auth/account` expiram o `pb_retomada`; o `_clear_session_cookies` não, porque o 401
do `/auth/refresh` (que a `/precos` deslogada provoca) o chama e mataria o link. Testes:
`tests/test_retomada_backend.py`.

**Entrega do e-book (#708) — N produtos por compra.** A metadata da sessão é a foto
dos produtos oferecidos: o 1º em `ebook_price`/`ebook_url`, o n-ésimo (2..10) em
`ebook_n_price`/`ebook_n_url` (posição na oferta, não o slot da env), lidos por `core/services/extras_assinar.da_metadata`.
O `checkout.session.completed` com pelo menos um slot grava uma linha POR produto em
`ebook_entregas` (`db/ebook_entregas.py`, PK `user_id + session_id + ebook_price`, num
insert só: todos ou nenhum) logo depois do grant e ANTES dos outros efeitos, sem try:
falha → 5xx e a reentrega refaz tudo. Produto sem a foto da URL grava assim mesmo e
loga `ebook_sem_url` (um por produto). Quem entrega é o job `_ebook_worker` (abaixo,
"Tarefas de fundo"), uma linha por vez: só envia com `not conta_sem_credencial(uid)`
(`db/google_auth.py`: senha não vazia ou identidade Google/Apple — a prova do e-mail;
sem linha em `auth_accounts` a função dá False, e o job não envia porque não acha
e-mail), confirma a compra daquele produto pelo `checkout.Session.list_line_items`
com `limit=100` (o padrão do Stripe é 10; plano + 10 extras = 11 linhas) — senão fecha
`nao_comprou` —, manda `send_ebook_email` com o nome do produto (`description` da linha
da sessão) para o e-mail ATUAL da conta e fecha `enviado` naquela linha. O claim
(`reivindicada_ate`, 10 min dobrando a cada tentativa até 1 dia, contadas em
`tentativas`; a linha nunca fecha sozinha) é por produto — a falha de um não segura os
outros — e não segura transação durante o Stripe/Resend; entrega é "pelo menos uma
vez". A tabela fica fora do export LGPD e sai com a conta (cascade).

**Estorno segura a entrega (PR 3 dos extras).** Antes de enviar, o job confere a
cobrança da compra (`_compra_estornada` em `core/services/ebook_entrega.py`):
`checkout.Session.retrieve(sid).invoice` → `InvoicePayment.list(invoice=…)` → em cada
pagamento `status == "paid"`, `payment.payment_intent` →
`PaymentIntent.retrieve(pi, expand=["latest_charge"])`. Qualquer estorno
(`amount_refunded > 0`, parcial ou total — decisão D2 do dono) ou contestação
(`disputed` — D3) fecha a linha `estornado` e loga `ebook_entrega_estornada`
(warning, `session_id` + `ebook_price`, nunca a URL), sem enviar. A regra é por compra:
todos os produtos ainda não entregues daquela sessão fecham `estornado`, cada um na sua
passada. Sessão sem fatura ou fatura sem pagamento (cupom 100%) entrega normal.
Pagamentos `open`/`canceled` são ignorados sem consulta; o estornado continua `paid`
(medido no Stripe de teste: depois de estorno TOTAL o `InvoicePayment.status` e a fatura
seguem `paid`, e a `latest_charge` expandida vem com `refunded` True). Falha do Stripe
na consulta propaga, e forma inesperada num pagamento `paid` também levanta (falha
FECHADO): `payment.type` diferente de `payment_intent`, `payment_intent` vazio, ou
`latest_charge` não expandida (string, nulo, sem os campos). Nos dois casos não envia
nem fecha, o claim expira com backoff e a próxima passada confere de novo. Depois de
enviado não há o que desfazer: a linha `enviado` fica como está, e o link já saiu.
`estornado` é final: não há botão para liberar a entrega, e liberar exige ajuste manual
no banco; contestação GANHA continua com `disputed` True, então também segura para
sempre. Limite conhecido: estorno por nota de crédito para o saldo do cliente (sem refund na
charge) NÃO é detectado. Estorno "pending" real e contestação real chegando antes da
entrega só se provam no Stripe; o modo teste sobe `amount_refunded` na hora.

**Cartão nunca cobra período que um Pix pago cobre.** Checkout de cartão concluído (ou
1ª fatura, `subscription_create`) com Pix cobrindo hoje (`pix_cobre_agora`: grant vigente
ou cobrança paga com a janela em curso, ainda sem grant; grant Pix revogado não conta; o
`create-checkout` recusa com `409 pix_active` pela mesma função) cancela a assinatura na hora
(`core/services/cartao_recusado_por_pix.py`, `Subscription.cancel` com
`cancellation_details.comment = "pigbank:pix_vigente"`), registra os cadernos e não
materializa nada; plano cobrado vira alerta de estorno manual, e o `deleted` com a marca
não manda e-mail de cancelamento. Na ordem inversa (cartão primeiro, Pix pago depois), o
efeito `stripe_cancel` do dreno pergunta ao Stripe (`_stripe_vivo`) e agenda
`cancel_at_period_end` quando a cobrança não tem `stripe_subscription_id` ou quando a
gravada já está morta (`canceled`/`incomplete_expired`; o cliente a cancelou e assinou
outra antes de pagar um QR antigo), gravando a assinatura achada (sempre a que foi
agendada) e a janela adiada na linha antes de o efeito contar como feito; com a gravada
morta, o começo que esperava o fim dela é desfeito antes de adiar até o fim da viva (que
pode acabar antes, e o Pix não fica esperando a morta); gravada morta e
nenhuma viva: não há `modify` e a janela que esperava o fim dela volta para agora. Com
cadernos, o alerta manda estornar o plano só depois de `ebook_entregas` marcar `enviado`.
Limites conhecidos: (1) assinatura que morre depois de o
`stripe_cancel` registrar deixa a janela adiada; (2) cobrança paga com grant ainda por
nascer: o `create-checkout` recusa, mas a tela de status mostra sem plano até o grant sair;
(3) a reentrega do checkout repete o alerta de estorno.

**Cadernos extras no Pix anual (PR A: receber e entregar; inerte até o checkout
gravar a foto).** `pix_charges.extras` (`jsonb`, default `[]`, check de array) guarda a
FOTO dos cadernos escolhidos, `[{price, url, nome, valor_cents}]`, gravada só por
`criar_cobranca`. `amount_cents` continua sendo SÓ o plano (crédito de upgrade,
`pix_charges_amount_fecha` e `plan_grants` não mudam); o que o cliente paga é
`core/services/pix_extras.total_cents` (plano + Σ cadernos), usado no `alertar_valor`,
no alerta do estorno parcial, no GA4/CAPI e no `total_cents` do poll. O dreno ganhou o
efeito `ebook` logo depois do `grant`: uma linha em `ebook_entregas` por caderno, com
`session_id` = `external_reference` (`pix:<id>`) e `ebook_price` = o Price do Stripe;
sem cadernos é no-op registrado, e ele herda D3 (estorno antes do `RECEIVED` não grava)
e o órfão (nada). O e-mail de confirmação discrimina plano, cada caderno e o total
(dono, Q4); sem cadernos é o de antes. O job entrega a linha `pix:` ANTES de qualquer
chamada ao Stripe (`pix_extras.conferir_entrega`): cobrança do dono por `user_id` +
referência (senão `nao_comprou`); status local `refunded`/`refunded_partial`/`chargeback`
fecha `estornado` sem consultar o Asaas; senão `GET /v3/payments/{id}`: `RECEIVED`/
`CONFIRMED` sem `refunds` entrega (nome do caderno vem da foto), status de estorno ou
contestação, ou `refunds` não vazia, fecha `estornado`; falha ou forma inesperada não
envia nem fecha (claim expira). Sem `STRIPE_SECRET_KEY` o job não roda, inclusive para
as linhas Pix. Na junção de contas, caderno ainda não entregue cai com a origem
(cascade), como no Stripe — no Pix a origem com plano vigente já fica presa. Nomes reais
dos status de estorno/contestação e a forma de `refunds` só se provam no sandbox do
Asaas.

**PR B: emitir com os cadernos (backend; invisível até o modal mandar `extras`).** O
`POST /billing/pix/checkout` aceita `extras: list[str]` (ids de Price, até 3, sem
repetir, senão 400). O cliente nunca manda preço: `pix_extras.escolher` relê a oferta
ATUAL (`extras_assinar.ofertas_da_pagina`, a mesma do cartão) depois das recusas que já
existiam e antes de qualquer escrita; id fora dela dá 409 `extras_indisponiveis` com a
oferta atual, sem gravar e sem chamar o Asaas. Sem `extras` o Stripe nem é consultado
(o plano vende com ele fora). O Asaas cobra `total_cents` e a descrição ganha " + N
caderno(s)". A cobrança pendente só é reaproveitada com o MESMO conjunto de ids (a ordem
não importa); outro conjunto cancela a remota e cria nova (DELETE que falha dá 503). O
`GET /billing/pix-extras` (logado) devolve as caixas (texto e preço do cartão + id, sem
a URL) e a seleção da cobrança `pending` com QR vivo (Q6). A oferta é vazia, e o POST com
cadernos dá 409, quando a venda Pix ou a `CHECKOUT_PAGINA_PROPRIA` estão desligadas
(Q2), ou sem `STRIPE_SECRET_KEY`.

**PR C: as caixas no modal do Pix.** As caixas (desenho e pintura) têm uma fonte só, o
`frontend/bump-caixas.js` + `bump-caixas.css` (`window.PBBumpCaixas.montar/pintar`), e a
/assinar (`pagamento-pagina.js`; o `pagamento-caixas.js` ficou só com resumo e botão) e o
modal do Pix desenham com elas. O `frontend/pix-extras.js` busca o
`GET /billing/pix-extras` ao abrir o formulário do documento, desenha as caixas com a
seleção pendente marcada e guarda os ids marcados, que o `pixEnviar` manda em `extras`
(também no reenvio da migração, quando o formulário já saiu da tela). Com o POST em voo
as caixas ficam travadas, como o botão, e destravam quando ele volta. O 409
`extras_indisponiveis` redesenha com a oferta nova, mantendo marcados só os ids que ainda
valem; na caixa da migração vai para o toast. GET falhando ou sem `bump-caixas.js` = sem
caixas, e o Pix do plano segue. O QR e o Purchase do pixel (home.html) usam `total_cents`.
Testes: `tests/frontend/precos_pix_extras.test.mjs`.

Rollback do código de N produtos: o código velho usa `on conflict (user_id,
session_id)`, que exige a PK de 2 colunas. Antes de reverter, apagar as linhas extras
de cada compra e recriar a PK:
`delete from ebook_entregas e using ebook_entregas o where e.user_id = o.user_id and
e.session_id = o.session_id and e.ebook_price > o.ebook_price;` e
`alter table ebook_entregas drop constraint ebook_entregas_pkey, add primary key
(user_id, session_id);` (confira o nome da PK em `pg_constraint` antes). O `delete`
fica com UM produto por compra: as pendências dos outros se perdem.

**E-mail trocado chega ao Stripe (PR 4b).** A `PATCH /settings/{uid}/security/contact`
que troca o e-mail de conta com `stripe_customer_id` grava, na MESMA transação, uma linha
em `stripe_email_pendente` (`db/stripe_email_pendente.py`, PK `user_id`, só `versao` —
sem PII; troca de novo sobe a versão). O job `_stripe_email_worker` manda
`stripe.Customer.modify(email=<e-mail ATUAL da conta>)` e apaga a linha só se a versão
não mudou durante o envio. A troca no app nunca é desfeita: falha transitória espera o
claim (mesma régua do e-book); `InvalidRequestError` (cliente apagado, e-mail recusado)
fecha e loga `stripe_email_sync_recusado`, sem o e-mail. Fora do export LGPD; cascade.

**Trocar o e-mail pede a senha atual (B0a).** `PATCH /settings/{uid}/security/contact` com
e-mail que muda (comparado por hash) exige `current_password` (`frontend/routes/settings_contato.py`):
campo ausente/vazio → 400; errada → 401 "Senha incorreta." SEM `WWW-Authenticate` (o
`auth-refresh.js` não renova); conta só-Google (sem senha, com identidade) → 409 "defina uma
senha" (o de `_reauth_password_ok`, antes do 400). A checagem vem DEPOIS do teto `settings-contact`
(5/h, senha errada também conta) e ANTES de qualquer 409 de contato em uso: senha errada nunca
enumera conta alheia. Recusa = nada gravado (nem nome nem telefone do mesmo corpo); nome,
telefone e e-mail igual ao atual não pedem senha. A conta paga SEM credencial (quiz/funil,
`conta_sem_credencial`) troca o e-mail sem senha e sem aviso: é a saída "E-mail errado?
Corrigir" do `criar-senha.js`, e ela não tem senha para dar. Depois do commit, o e-mail ANTIGO
recebe `send_email_changed_notice` com o novo mascarado (`n•••@dominio`; sem IP nem horário),
best-effort: a decifragem do antigo e o envio só logam e nunca desfazem a troca. "Mudou?" e o
e-mail antigo são decididos DENTRO da transação, com a linha de `auth_accounts` travada
(`travar_e_ler_email`, `select … for update`): dois PATCH que trocam o e-mail (duplo clique,
duas abas) se serializam nesse lock, o segundo enxerga o e-mail já trocado e responde 200 sem
mudança (sem 2º aviso, sem 2ª pendência do Stripe, sem 2ª auditoria). Só o PATCH que troca o
e-mail toma esse lock; um PATCH julgado "sem mudança" só espera se gravar nome/telefone na mesma
linha. Invariante: o PATCH julgado "sem mudança" NÃO regrava o e-mail (o `UPDATE` do e-mail e a
pendência do Stripe só rodam sob `email_actually_changed` pós-lock), para não desfazer uma troca
concorrente feita com senha. O botão Salvar do `settings.html` também fica
travado enquanto o PATCH está em voo. As outras sessões NÃO são encerradas, e o e-mail NOVO não
é confirmado (quem troca para o e-mail de outra pessoa é assunto da issue própria, via
auto-vínculo Google/Apple). Sobra: conta com 2 linhas em `auth_accounts` (o `user_id` não é
unique) segue em 409 na troca, como antes; o "antigo" do aviso é o da primeira linha lida. Resend lento atrasa a resposta do PATCH (o aviso é `await` depois do commit, sem lock e sem timeout próprio), e o e-mail antigo vai ao `system_event_logs`, como os outros avisos. O teto de 5/h conta a tentativa antes da senha e do lock, sem estorno: 5 PATCH concorrentes com o mesmo e-mail novo (só API direta ou várias abas; o botão trava em voo) gastam as 5 e o próximo update leva 429 até a hora virar. Declarado de propósito: estornar enfraqueceria o limite de brute-force da senha.

**Configurações do /painel mostram a Segurança (S0 + C1a + C1b).** `conta/Seguranca.tsx`: o link de redefinição de senha por e-mail
(`password-reset`) e o MFA (`/auth/mfa/*`, modal em `conta/MfaForm.tsx`). Os códigos de backup moram no Cofre
(`conta/Cofre.tsx`), irmão do `<Entrada>` em `main.tsx`, na memória do módulo: o servidor os devolve uma vez e fechar o modal,
trocar de página ou o portão cair não os apagam; só o "Concluído". `enable` e `regenerate-backup-codes` NÃO têm timeout (o
servidor comita e a resposta é a única cópia dos códigos); resposta perdida relê o status (T2 "gerar novos códigos" se o MFA ligou;
o 409 "já ativado" do `enable` também vira T2). O S0 corrigiu `_current_session_jti` (`frontend/routes/settings.py`): no painel, que roda só com o `dashboard_token`, o jti vinha `None` e "encerrar os outros" revogava a PRÓPRIA sessão; agora vem do que o gate já resolveu. O C1b pôs os "Dispositivos conectados" na mesma página (`conta/Sessoes.tsx`), pelas rotas antigas `GET /settings/{uid}/sessions`, `DELETE /settings/{uid}/sessions/{jti}` e `DELETE /settings/{uid}/sessions` (encerrar as outras): os DELETE têm `comPrazo` (30 s); a releitura depois de falha usa `comPrazoLeitura` (`conta/api.ts`, compartilhado com `lerStatusMfa`); depois do DELETE a linha sai do cache e o `invalidateQueries` relê (o `cancelRefetch` dele cancela o GET em voo); a sessão atual (`is_current` ou `current_jti`) nunca tem botão; resposta fora do contrato cai no erro com "Tentar de novo" (`sessoesDe`). Limite: com `current_jti` null (só token legado sem jti) "encerrar as outras" revoga todas as linhas de `auth_sessions`, igual à `/settings` (`core/sessions.py:revoke_other_sessions`); o token legado não é linha, então a atual segue válida. O app não tem lista de sessões (`app/app/(app)/seguranca.tsx`).

**Configurações do /painel mostram as Notificações (C2).** `conta/Notificacoes.tsx`, pelas rotas antigas `GET`/`PATCH /settings/{uid}/notifications`: os mesmos 7 controles da `/settings`, cada um salva sozinho (corpo só com o campo tocado, no máximo 1 PATCH em voo por página: a trava é do módulo, então sair da rota e voltar não a solta; várias abas são independentes; a tela segue a resposta e o `invalidateQueries` relê). O horário manda o `daily_report_enabled` atual junto: o servidor liga o diário ao gravar a hora (`set_daily_report_hour`), então sem o campo mexer no horário religaria um diário desligado (a própria `/settings` ainda faz isso: issue #1122). `whatsapp_updates_enabled` é consentimento e só vai com o clique no interruptor dele. `HORAS` (`conta/api.ts`) copia as opções de `#notif-daily-hour` do `settings.html`; o teste compara as duas. Par no app: `app/app/(app)/notificacoes.tsx` (#1121), com horário de qualquer hora e minuto e a seção "No celular" para o push de `plano-push-app.md`.

**Cobrança do /painel (`/conta/cobranca`).** `conta/Cobranca.tsx` lê `GET /billing/subscription` (fora da `/api/v2`: `billing` está na lista do `chamar`, `lib/v2.ts`) e mostra o plano (`NOME_DO_PLANO`, `lib/v2.ts`, a fonte única do painel), o selo Mensal/Anual/Pix/Vitalício e a data. A rota não diz se é teste, carência ou cancelada no fim: por isso o cartão só diz "Período atual até DD/MM/AAAA", e o Pix "Válido até" (a data sai por split, nunca `new Date`). Ações: `POST /billing/portal?voltar=%2Fpainel` (abre o portal na mesma aba, só se a resposta for `https:`), `POST /billing/cancel-change` (a troca agendada; 400 `no_change` e 409 `no_subscription` só releem) e o link para a `/precos`. Uma trava no módulo para as duas escritas; `refetchOnWindowFocus` desligado (cada leitura chama o Stripe de 1 a 4 vezes), com releitura no `pageshow` do bfcache. Na carência o `plan_tier` do `/api/v2/me` é `free`: o nome vem da assinatura. Par no app: específico da plataforma (ver `paridade-app-dashboard.md`).

**Fatura com produtos extras:** no `invoice.paid`/`payment_succeeded`, `amount_cents`
é só o plano: `amount_paid` menos o líquido (`_extras_liquido_cents`) de TODAS as
linhas cujo `pricing.price_details.price` está entre os preços da foto da metadata da
assinatura (`da_metadata`, slots 1..10 — nunca a env do momento; `amount` da linha é
BRUTO; o cupom vem em `discount_amounts`). A fatura embute no máximo 10 linhas
(plano + 10 extras = 11): com extras na foto, `extras_assinar.linhas_da_fatura` usa
as embutidas ou, com `lines.has_more`, `stripe.Invoice.list_lines(id, limit=100)`, sem try (falha → 5xx, o Stripe reentrega).
Crédito de saldo do cliente (`amount_paid` menor que a soma das linhas) fica com o
plano: o extra é subtraído cheio. Vale igual para as duas origens (`assinar` e
`precos`): a origem não entra na conta. É esse valor que vai para o e-mail de
cobrança, a comissão de afiliado e o rastreio da fatura — a 1ª fatura de trial +
extras dá 0 e pula os três (a comissão, que só paga a 1ª fatura paga, fica para a do
plano).

**Rastreio do checkout:** um evento por compra, com a soma; não olha a origem nem
quantos extras. O Meta `Purchase` sem trial leva o `amount_total` da sessão (plano +
todos os extras, com cupom — o mesmo número do GA4; sem o campo, cai no
`unit_amount`). Com trial e `amount_total > 0` (a soma dos extras), saem UM Meta
`Purchase` e UM GA4 `purchase` server-only com id `ebook_<sid>`
(`meta_capi.ebook_event_id`) e item `ebook`; o `StartTrial` não muda.

A **escada de planos é `free < essencial < plus < pro`**, atrás do flag
`PLANS_V2_ENABLED` (lido dinamicamente, sem redeploy; `0`/`false` é freio de
emergência e colapsa no binário legado). **A fonte de verdade é
`core/services/plan_service.py`** — não duplique a tabela de tiers, limites ou nomes
em outro lugar (§0.7 da raiz). Limites por plano em `core/services/plan_limits.py`.

**IA primeiro no WhatsApp** (`core/services/wa_ia_primeiro.py`): com
`WA_IA_PRIMEIRO` em `1`/`true`/`yes`/`on`, o texto do WhatsApp vai à IA antes do
classificador; o roteador fica com a lista fechada de `fica_no_roteador`
(saudação, ajuda, e-mails, relatórios, desfazer, cartão,
recorrência/conta a pagar, vários lançamentos numa frase), com pendência viva e
com o que a IA devolve sem resposta. `WA_IA_PRIMEIRO_USER_IDS` (ids por vírgula):
ausente ou `""` exato = todos; com ids, só os listados; qualquer outro valor que
não dê id válido (`,`, só espaços, `abc`) = ninguém, com warning no log. As duas são lidas a cada mensagem, mas
trocar env no Railway reinicia o serviço (~1 min sem bot). O prazo do turno da IA
(`IA_PRIMEIRO_PRAZO_TURNO`, 15 s) também limita cada chamada à OpenAI (timeout por
requisição = o menor entre 8 s e o que sobra), e resposta com tool calls que chega depois
dele não roda nada: a mensagem volta ao roteador (ou, se já houve escrita no turno,
`ERROR_MSG`). Quando a mensagem volta ao roteador, ele não chama LLM de novo no mesmo
turno (`SEM_LLM_NO_TURNO`): classificador sem tier 3 e categoria sem GPT. Com a flag, o
`add_launch` da IA pede "sim" quando QUALQUER parâmetro que a gravação usa não está
apoiado no texto (`lancamento_com_certeza`, um critério por parâmetro do schema): valor
(exatamente um número em dígitos e nenhum por extenso, igual); data (o dia do mesmo parser da gravação, no fuso do app, igual
ao do texto; texto sem data → IA sem data ou hoje); tipo (lista positiva: receita só
com verbo de receita no começo; despesa só com verbo de saída no começo, ou forma curta
"mercado 80": além do número e de "R$/reais" sobra UMA palavra e ela é o alvo inteiro —
"o mercado me devolveu 50 reais", "reembolso mercado 50", "uber aeroporto 23" e
"R$ 1.234,56 no aluguel" são incertos; na forma curta a categoria local também tem de ser
de despesa padrão — "dividendo 50" (rendimentos) e categoria personalizada são incertos;
despesa em categoria só de receita é incerta mesmo com verbo); categoria
(regra local confiante; hashtag só se a regra local da nota não a contradiz);
`forma_pagamento` (se veio, `forma_pagamento.detectar` do texto dá a mesma — também
quando a decisão é BANCO, que não grava: forma "banco" inventada para um gasto em
dinheiro pede confirmação, apoiada no texto ("no pix") segue direto com o `msg_banco`); alvo e
nota (se vieram, palavras inteiras do texto, sem acento nem caixa); texto com negação
("não", "nem", "nunca", "sem", "jamais", palavra inteira) ou pergunta ("?") nunca é
certo ("não gastei 50…" não vira lançamento). O resumo da
confirmação (`_add_launch_summary`; só é usado por esta confirmação — WhatsApp com a
flag —, porque o `add_launch` não pede confirmação em nenhum outro caminho) mostra tipo,
valor, alvo, nota, categoria, o dia que vai ser gravado e a forma, se veio, e o "sim"
grava exatamente isso: o dia efetivo do resumo (`_dia_efetivo`, a mesma função do
congelamento) vira `data` da pendência, então um "sim" depois da meia-noite grava o dia
mostrado; com hashtag no texto, a pendência guarda a categoria dela e a
marca `_categoria_explicita` (do código; o runner descarta toda chave `_` vinda do
modelo), e a gravação a passa como `explicit`, que o cross-check com a regra local não
troca (a categoria vai canonizada, como a gravação a deixa). Sem hashtag, quando a IA
mandou categoria, o resumo e a pendência levam a categoria EFETIVA: a que a execução usaria
depois do cross-check com a regra local (`resolver_categoria_da_ia`, a mesma função do
`add_from_entities`), com o motivo dela em `_category_reason` (chave do código). Sem
categoria da IA, a execução infere e o resumo não mostra categoria. Rodada do modelo com uma
escrita que armaria pendência e QUALQUER outra escrita: nada roda (seção "IA"), também
no modo ia_primeiro (o roteador não faz as duas juntas). Leituras não contam: a que vem
antes do `_CONFIRMA` roda; a que vem depois recebe "não executada: aguardando a
confirmação do usuário" (corte, rede de segurança).
Desligar a flag NÃO desfaz o cancelamento da confirmação da IA não mostrada (seção
"IA"): ele vale para todos os canais (decisão do dono, 2026-10-08).

**Inadimplência de cartão** (`core/services/billing_dunning.py`): a coluna
`auth_accounts.past_due_since` guarda a **primeira falha de cobrança do ciclo**,
carimbada pelo webhook `invoice.payment_failed` (`db.dunning.claim_past_due_since`,
idempotente no SQL **e condicionada ao status atual**) e zerada por
pagamento/cancelamento (`clear_past_due_since`, que os ramos `checkout` e
`invoice.paid` só chamam quando `_materializar_assinatura` disse que o evento
decidiu o acesso). Os três helpers moram em `db/dunning.py`, não em `db/plans.py`.
`DUNNING_GRACE_DAYS = 7` é a carência.

**Ninguém perde acesso POR INADIMPLÊNCIA** — o relógio só CONCEDE tempo. Desde
o corte do Grátis (#274/#354) existe gate de acesso, e a autoridade dele é o
DIREITO pago (`plan_service.has_app_access` → `tem_direito_hoje`), nunca o
status de cobrança. A coluna alimenta **três** coisas: o **lembrete de pagamento
do 6º dia**
(`core/services/payment_reminder.py`, no tick de `engagement_scheduler`; e-mail
sempre, WhatsApp só se `WA_TEMPLATE_PAYMENT_REMINDER` apontar para um template
aprovado na Meta — vazio por padrão → caminho dormente), a janela de dedupe do
e-mail de falha no webhook e o predicado `carencia_aberta`, lado DIREITO do OR
de `plan_service.tem_direito_hoje` (o relógio só CONCEDE tempo; a autoridade é
o direito pago), consumido pelo aviso de corte
(`scripts/aviso_fim_do_gratis.py`) **e pelo GATE DE ACESSO** — `has_app_access`,
e por ele os quatro enforcements (HTML, rotas de dados, WebSocket, bot) mais o
filtro dos relatórios proativos. Freio de emergência do gate:
`ACCESS_GATE_ENABLED=0`, gêmeo exato do `PLANS_V2_ENABLED`. O lembrete fica atrás de `PAYMENT_REMINDER_ENABLED`
(**default off**, lida a cada tick, sem redeploy; a guarda é a 1ª linha de
`check_payment_reminder`, então desligada nem consulta o funil). Grant
`pix`/`admin` vigente pula o lembrete (`legacy` não). **A copy do LEMBRETE
continua proibida de prometer pausa ou perda de acesso** — ele sai no 6º dia,
dentro da carência, com o acesso ainda de pé. Quem PODE falar em perda são o
e-mail de falha e o de cancelamento, reescritos no PR do corte (#354).

**A INVARIANTE**: `past_due_since` não nulo só existe em conta com
`last_payment_status` em `PAST_DUE_PAYMENT_STATUSES`. Ela é mantida na ESCRITA,
e os dois writers da coluna de status são `db_support.set_payment_status_impl` e
o SQL cru de `core/admin_dashboard.set_account_plan` — mexeu num, leia o outro.

**A máquina inteira está enumerada em `docs/dunning_estados_eventos.md`**:
estados (o par relógio × status) × eventos (os quatro webhooks de cobrança, o
`recompute_entitlement`, o `set_account_plan` e o tick do lembrete) × validade
do evento (novo / reentrega / velho), com o que cada célula faz hoje, o que
deveria fazer, e as células deixadas abertas de propósito. **Leia antes de
tocar em qualquer writer do relógio** — o subsistema levou VÁRIAS rodadas de
revisão porque cada conserto foi feito como transição isolada, e a tabela existe
para a próxima não repetir o método (raiz §4, registro do PR #60). Quais rodadas
apontaram o quê está na coluna "quem achou" da tabela do fim daquele arquivo; a
contagem não vive aqui de propósito, porque ela sobe a cada rodada (§2).

### Open Finance

Via **Pluggy**. Endpoints em `frontend/routes/open_finance.py`
(`/open-finance/{user_id}` e `connect-token`, `connectors`, `sync`, `refresh`,
`pluggy-item`, `caixinhas`, `caixinhas/bind`, `mock-connect` (só com `OF_MOCK_CONNECT_ENABLED`; sem ele, 404),
`limite` (GET só leitura, `{ok, of_banks_max, em_uso, pode_adicionar, code, message}`: se cabe
um banco NOVO, pela mesma decisão do `_enforce_bank_limit`; o teto nunca vira 402 aqui, mas o
gate comum de dados sim (402 `subscription_required`/`plan_selection_required` sem plano ativo);
não barra reconexão e o 402 do `/pluggy-item` continua valendo)) mais o webhook
`/open-finance/pluggy/webhook`. O `connect-token` aceita `app_scheme` opcional no corpo
(`pigbank`, `pigbank-staging` ou `pigbank-dev`; fora da lista, 400), que vira o
`oauthRedirectUri` `<scheme>://open-finance-volta` da Pluggy; o site não manda o campo.
Aceita também `item_id` opcional (reconectar banco já conectado; vai como `itemId` ao lado de
`options`): só item não pausado do próprio usuário no nosso banco e com o `clientUserId` dele
na Pluggy; não-string ou vazio é 400, todo o resto é o mesmo 404 `OF_ITEM_NAO_ENCONTRADO`.
`item_id: null` conta como ausente (token de banco novo). No site, o legado
`frontend/open-finance-connect.js` expõe `PBOpenFinance.reconnect(itemId)`: manda `item_id`, abre o
widget com `updateItem` e sem `selectedConnectorId`, e não passa pelo picker nem pelo `refreshLimits`.
Callbacks do host: `onOpen()` quando o widget foi iniciado (síncrono, não quando terminou de abrir); `onError(msg, status)` (status HTTP quando
vem do servidor, ausente no erro do SDK); `onClose()` só depois de o `/pluggy-item` terminar, e
SÓ na reconexão (o picker chama na hora, como sempre). `itemId` vazio ou não-string => `onError`,
nenhum pedido. Uma conexão por vez: `open()`/`reconnect()` durante uma em curso são ignorados sem
callback (`reconnect()` devolve `false`; o `onError`/`onClose`/init rejeitado de um widget VELHO (já nasceu uma conexão mais nova, pelo tempo de vida, não pela guarda `ocupado`) não chegam ao host: só a mais nova é dona dele. O `onSuccess` de qualquer widget ainda salva o item, mas o de um widget VELHO o faz em silêncio (nem `onConnected`, nem `notify`, nem `onError` do save chegam ao host): o item salvo por ele só aparece na lista na próxima releitura (a da conexão mais nova ao terminar, ou ao recarregar) (limite declarado); guarda liberada no onClose, no fim do save, no erro do connect, no `onError` do SDK (a
Pluggy não garante `onClose` depois dele), no `init()` do SDK que rejeita (`onError` e em seguida
`onClose()`) e no `destroy()`), e depois do `destroy()` nenhum callback do host dispara. Limites:
depois de um `onError` do SDK com o widget ainda na tela, um novo clique abre outro widget (como antes
da guarda); o `onClose` tardio de uma conexão cujo save já terminou pode chegar durante a conexão seguinte e o
host não o distingue (hoje nenhum dos dois hosts deixa clicar nesse intervalo). Testes: `tests/frontend/of_connect_reconectar.test.mjs`. O campo opcional `attempt_id`
exige UUID canônico em minúsculas e `app_scheme` permitido; o servidor monta a URI
`<scheme>://open-finance-volta/<attempt_id>`. Sem esse campo, pedidos legados válidos
mantêm a URI anterior. Após os gates de sessão e acesso, JSON malformado, campos
repetidos, corpo acima de 4096 bytes ou lento retornam 400 antes de qualquer chamada
à Pluggy; corpo vazio continua permitido. Contrato completo em
[`open-finance-ios-backend.md`](open-finance-ios-backend.md).
Cada conexão do snapshot (`GET /open-finance/{uid}`, `POST .../pluggy-item`, `.../sync`, `.../refresh`,
`.../mock-connect`: todas as rotas que devolvem `**snapshot`) traz `logo_url`:
`https` ou `null`, derivado de `raw.connector.imageUrl` e validado em `logo_url_valida`
(`db/open_finance_state.py`); o `raw` não sai. Sem `connector` no `raw` (as conexões antigas, e toda
conexão depois de `item/deleted`, que substitui o `raw`), o snapshot empresta o logo do catálogo de
conectores da Pluggy que já vive em memória (`core/services/pluggy_catalogo.py`, `logo_do_conector`):
casa só pelo id do conector (`institution_id`, `str` dos dois lados; nunca pelo nome), só em
`provider='pluggy'`, ignora o TTL (logo velho serve) e nunca vai à rede nem levanta, então o caminho
do snapshot não espera a Pluggy. O `raw` vence o catálogo. O cache é aquecido por
`_aquece_catalogo` (monólito) no boot, como tarefa de fundo, e no começo de cada tique do Open Finance,
com a mesma função e a mesma chave do modal "Conectar banco"; falha vira uma linha com o tipo da exceção.
Continua `null`: cache frio (segundos depois do boot, ou até o 1º tique, em até 10 min, se o
aquecimento do boot falhar; só com `RUN_BACKGROUND_TASKS`; ou Pluggy fora do ar), conector que saiu do
catálogo, `mock_pluggy` e `institution_id = "pluggy"` (item sem `connector` nem `connectorId`).
Ids de produção das 4 antigas, medidos pelo dono em 2026-10-18: 611, 612, 651, 775; que o catálogo
real os liste só se prova depois do deploy. O servidor NÃO fixa o host (`logo_url_valida` segue
aceitando qualquer https); quem fixa é o cliente.
Logo PRÓPRIO dos 10 bancos principais (Itaú, Bradesco, Banco do Brasil, Santander, Caixa, Inter, C6, Nubank, BTG, Mercado Pago): cada
conexão do snapshot e cada item do `/connectors` traz também `logo_proprio` (`/brand/bancos/<ispb>.png` ou `null`; campo a mais,
`logo_url` e `logo` não mudam). A fonte única é `_LOGO_PROPRIO_ISPB` (`core/services/pluggy_catalogo.py`, `logo_proprio`): id do conector
(texto) → ISPB, só id EXATO e só as variantes pessoa física (nunca por nome); Itaú Empresas, Bradescard, Mercado Bitcoin etc. ficam
fora de propósito e seguem com o logo da Pluggy. Os PNG 256×256 moram em `frontend/brand/bancos/` (pacote `logos-bancos-br` 0.8.2; as
marcas são dos bancos, uso nominativo, sem deformar nem recolorir) e saem pela rota `/brand/{path}` com cache imutável de 1 ano:
trocar a arte de um banco exige trocar o NOME do arquivo E ampliar a regex do cliente (web e, quando existir, app) no mesmo PR. Precedência no cliente: próprio > `logo_url` da Pluggy > inicial. O /painel
(`conta/api.ts::logoDoBanco`) só aceita `/^\/brand\/bancos\/\d{8}\.png$/` (mesma origem, sem host de terceiro); o que não bate cai no
`logo_url`, e o próprio que falha ao carregar cai na inicial (não tenta a Pluggy no meio). O seletor legado
(`open-finance-connect.js`) fica de fora. O app aplica a mesma precedência (`logoDoBanco` em `app/src/features/openFinance/conexoes/juntar.ts`, URL = `baseUrl()` + caminho, o PNG vai ao `<Image>` do Avatar e o fetch de SVG segue só no CDN da Pluggy); `tests/frontend/logo_banco_paridade.test.mjs` compara a regex e a função dos dois lados, e trocar a arte de um banco exige ampliar a regex também no app.
Cada conexão do snapshot traz também `institution_id` (texto, o id do conector da Pluggy; é coluna `not null`, e o `pop` do
laço de `_read_open_finance_connections` nunca o tirou: o gate de reconexão do picker já o lia, `ids` em `refreshLimits`):
`tests/test_of_connection_state.py` (`CHAVES_DA_CONEXAO`) prende a chave e `tests/test_of_snapshot_institution_id.py` o valor e o
isolamento por usuário. É por ele que o /painel decide "já conectado": `String(connector.id) === institution_id`, nunca pelo nome.
**Tela Bancos conectados do /painel** (`#/conta/bancos`, `conta/Bancos.tsx`, `ResumoBancos.tsx`, `BancoCard.tsx`, `MenuBanco.tsx`, `Disponiveis.tsx`, `SegurancaBancos.tsx`, `BancoDetalhe.tsx`, `styles/bancos.css`):
layout da referência do dono (2026-10-19), UMA coluna de 4 cards `.panel`. (1) Resumo: quantidade de bancos ("N de M bancos" de `/limite`, discreto, só com teto), "Última sincronização"
(a `last_sync_at` mais recente, "Hoje, 14:32"/"Ontem, …"/"dd/mm, …", no fuso de São Paulo; sem data "—") com "Concluída" (todos em tom ok de `tomDoEstado`) ou "Atenção" (no tom do pior), e
"Conectar banco" (ou a mensagem do servidor sem vaga). (2) "Meus bancos conectados": por banco, ladrilho, nome, pílula com o `ui.label`, "Última atualização" e três botões de 44 px:
"Detalhes" (o detalhe com os valores em R$, que moram só nele), "Sincronizar" e o menu "…" (Reconectar, só quando `reconectavel`, e Desconectar; as MESMAS ações do detalhe, que as mantém).
"Sincronizar" é a rodada do "Atualizar agora" do aviso do topo (`useAtualizarBanco`, com `automatico: false`: a página não parte rodada sozinha): o servidor só atualiza todos os bancos
(`POST /open-finance/{uid}/refresh`), então TODOS os "Sincronizar" travam juntos (`aria-busy`/`aria-disabled`, "Sincronizando…", até o fim da rodada) e o resultado sai na tela e na região viva; sem laço de retentativa. Reconectar e Desconectar travam só enquanto o POST sai (`sync.enviando()`); na fase `acompanhando` (o banco coleta sozinho, até 5 min) ficam livres. A pílula de `updated` diz "Conectado" (os outros estados, o rótulo do servidor) e o resumo ignora banco em `updating`: "Concluída" volta com a coleta, "Atenção" só para erro ou estado pendente de verdade (decisões do dono, 2026-10-19).
A partir de 1181 px (trilho aberto) a linha é uma só e o resumo tem 3 zonas; abaixo disso e no celular empilham, e até 480 px os botões viram só ícone (`aria-label`). (3) "Disponíveis para conectar"
(abaixo) e (4) o aviso de segurança com "Saiba mais" (externo, Banco Central). Par no app: o app NÃO muda neste PR (divergência de plataforma, `docs/paridade-app-dashboard.md`). Abaixo, "Disponíveis para conectar": até 5 dos populares (Itaú,
Bradesco, Banco do Brasil, Santander, Caixa, Inter, C6, Nubank, BTG, Mercado Pago, nessa ordem; o NOME só escolhe a ordem) de
`GET /open-finance/{uid}/connectors` que ainda não estão conectados (por id) e "Ver todos os bancos". A seção só existe com vaga
(`pode`, o gate do `/limite`) e some se o `/connectors` falha (leitura opcional, nunca derruba a tela) ou se não sobra banco.
O "Conectar" de cada um, o "Conectar banco" do resumo e o "Ver todos" são `<a href="/settings?view=open-finance">` com `onClick`: com
`window.PBOpenFinance` presente abrem o seletor ali mesmo (`open({id, name})` pré-seleciona o banco; o usuário ainda toca em
"Conectar", e todo o teto/plano/confirmação é o do módulo), sem ele o href segue. `Bancos.tsx` faz `init` ao montar (o `onConnected`
relê snapshot, `/limite`, `/bancos`, `/connectors` e as contas) e `destroy` ao desmontar, salvo com widget da Pluggy já aberto (`busy()`): aí o `destroy` não roda, para o `onSuccess` ainda salvar o item. O `painel.html` carrega
`open-finance-connect.css/.js` e o SDK da Pluggy (mesma versão e SRI do `settings.html`; `async`, em TODO carregamento do /painel
— carga sob demanda é follow-up); os tokens do seletor saem do painel em `.bankpick-overlay` (`bancos.css`). Par no app: o "Disponíveis"
é plano futuro (PR próprio). Testes: `tests/frontend/dashboard_v2_bancos*.test.mjs` (o seletor, em stub) e `of_connect_settings.test.mjs`
(`open({id, name})` no módulo real).
Opção do módulo `init({ guardaDuploClique: true })` (só o /painel a liga): nos 350 ms depois de abrir o picker, clique em linha e em "Conectar" é ignorado (só o
"fechar" explícito vale), para o 2º clique de um duplo clique no "+" não escolher outro banco. O clique no fundo é ignorado sempre nesse prazo, também no /settings.
O /settings NÃO liga a opção, de propósito: ali a classe de bug irmã continua (o 2º clique de um duplo clique no botão que abre o picker pode escolher outra linha),
para não mudar o comportamento que `of_connect_settings.test.mjs` fixa. Testes: `dashboard_v2_bancos_seletor.test.mjs` e `of_connect_settings.test.mjs`.
Reconectar (W3, `conta/reconectar.ts`, `BancoDetalhe.tsx`): o detalhe do banco tem "Reconectar banco" (acima do "Desconectar banco") quando há `provider_item_id`, o estado não é `removed`/`item_missing`/`paused` (`SEM_RECONEXAO`, igual ao app, comparado em `bancos_paridade.test.mjs`), `of_banks_max !== 0` e o módulo carregou; não usa `pode_adicionar` (reconectar não gasta vaga). Usa o MESMO `init` da página: os callbacks do módulo vão à reconexão em curso (`emCurso`) ou, sem ela, ao seletor. O detalhe fecha ANTES do widget (o `<dialog>` modal cobriria o widget da Pluggy); o resultado vai à lista (aviso sr-only "<banco> reconectado." ou alerta, texto do servidor só em 400/402/404/409/429 e só se for texto de pessoa) e o foco volta à linha. Uma conexão por vez (Reconectar, Desconectar, "+"), prazo de 30 s até o widget abrir. O `uid` é o do clique. `PBOpenFinance.reconnect()` devolve `false` quando há conexão em curso. Com "Disponíveis" e viewport >= 1101 px a tela tem duas colunas (bancos | disponíveis); abaixo disso, uma. Testes: `dashboard_v2_bancos_reconectar.test.mjs`, `..._reconectar_bordas.test.mjs`, `dashboard_v2_bancos_alinhamento.test.mjs`, `of_connect_reconectar.test.mjs` (A24). NÃO provado aqui: o SDK real aceitar `updateItem` com token de `itemId`, o overlay real da Pluggy acima do detalhe e a ordem real dos callbacks (só em produção). Plano e decisões: `plano-bancos-conectados-painel.md`, W3. Foco: com o widget por cima ele só volta (à linha) no `onClose` tardio do widget; sem esse `onClose` fica onde estava. Limites: o alerta de falha acima da lista desalinha os topos das duas colunas enquanto existir (41 px a 1280); com `of_banks_max` nulo e "Disponíveis" há uma linha vazia (25 px) acima da lista da esquerda só nas duas colunas (em uma coluna ela some por CSS); o toast do "+" do seletor só mostra texto do servidor de pessoa (`textoDoServidor`, a mesma regra do Reconectar: 400/402/404/409/429, sem `<{[`, até 300 caracteres); o aviso do resultado só sai depois da releitura (até 30 s se lenta). Limite declarado (cadeia de 4 condições, só no SDK real): o widget A emite `onError` e ainda manda um `onSuccess` tardio; a reconexão B nasce com A na tela; o `connect-token` de B passa dos 30 s; e o save de A está em voo nesse instante. O prazo de B chama `destroy()` do módulo, que aborta o fetch compartilhado e A não grava o item (consentimento fica na Pluggy; reconectar de novo refaz). Fechar pede o módulo abortar só o fetch da própria sessão; não feito.
Limites declarados dessa tela (decisão do dono pendente, nenhum código para eles): (1) o gêmeo direto × Open Finance: um banco conectado pelo conector
direto (ex.: Caixa, id 219) e o mesmo banco no Open Finance (id 619) são conectores diferentes; "já conectado" é por id, então o 619 aparece em
"Disponíveis" como se o banco não estivesse conectado (o teto de vagas e o `connections` do servidor valem igual); (2) o SDK da Pluggy carrega em
todo /painel, não só no clique (o Reconectar usa esse mesmo carregamento); (3) sair da página com o widget já aberto não o destrói (`busy()` só vale depois de o widget ser criado; antes, durante o `/connect-token`, sair ainda chama `destroy()` e aborta): o `onSuccess` ainda salva o item, mas uma falha ao salvar (teto do plano, rede) fica muda, porque a tela já saiu, e o item/consentimento ficam na Pluggy sem conexão no PigBank; voltar à página com o widget antigo ainda aberto deixa o "+" sem efeito até o widget fechar (`open()` é ignorado em silêncio pela guarda "uma conexão por vez"; o overlay da Pluggy cobre a página, então só dá para alcançar pelo teclado); (4) o aviso do seletor (toast) fica sob um `<dialog>` aberto em top layer (o detalhe do banco, o Confirma): o `z-index` não vence o top layer; (5) o StrictMode (montar, desmontar, montar) só foi provado por leitura: o build de teste é de produção.
`PBOpenFinance.open({id, name})` (opcional; `open()` sem argumento segue idêntico para o /settings): abre com esse banco
selecionado e à vista; id que não está na lista, ou argumento que não é `{id inteiro, name}` (um Event), é ignorado.
No /painel (`conta/BancoCard.tsx`, `Ladrilho`) o ladrilho quadrado arredondado (44 px na linha, 64 px no detalhe) troca a inicial pela imagem (`<img alt="">`, fundo `--ink`,
`overflow: hidden`; o painel só tem tema escuro); `logoSeguro` (`conta/api.ts`) é a cópia da regra do app e
`tests/frontend/logo_banco_paridade.test.mjs` as compara; imagem que falha volta à inicial na cor do banco.
No app (`conexoes/juntar.ts`, `logoSeguro`), o `logo_url` só é carregado com o host fixo `https://cdn.pluggy.ai/`; o SVG
vem por `fetch` + `parse` + `SvgAst` (uma vez por URI, sem `Authorization`) e o `Avatar` cai na inicial em falha, `!ok` ou
corpo que não é SVG. Limites: sem teto de tamanho do corpo; `<image href>` e `<use href>` dentro do SVG são renderizados;
o `fetch` segue redirecionamento (o host fixo vale só no 1º salto). Sem `expo-updates`: a tela só chega com build novo.
Serviços em `core/services/pluggy*.py` e
`open_finance*.py`; tabelas `open_finance_connections/accounts/transactions/investments`,
`open_finance_investment_snapshots` (foto diária por posição, `db/of_snapshots.py`) e
`open_finance_item_registry` — o rastro de todo item que passou por aqui, inclusive o
que nunca virou conexão (token emitido e abandonado, webhook de item desconhecido); o
`GET /items` da Pluggy devolve 401, então sem ela o universo remoto não é enumerável;
ela guarda também a marca de remoção deliberada (`origin='removed'`), escrita na mesma
transação do delete pelo disconnect e pelo reset.

Assinaturas vêm do **Recurring Payments** da Pluggy (`db/of_recurring.py`):
`of_recurring_payments` guarda o resultado por conexão, substituído inteiro a cada
sync — falha na Pluggy mantém o anterior; `subscription_marks` guarda a marcação do
usuário por `merchant_key` (vale para todos os itens da chave; com a chave `extrato#…`, a resposta sobre a receita detectada, ver Previsão), e `assinatura_antes` a
marca `assinatura` que o `ignorar` substituiu (linhas ignoradas antes da coluna nascem `false`).
A Previsão também lê essa lista (fonte `recorrencia_banco`, ver `/api/v2/previsao`).
`of_card_bills` (`db/of_card_bills.py`) guarda as faturas que o banco fechou (`/bills` da Pluggy,
só contas `CREDIT`), por upsert em (conta, `provider_bill_id`); fail-soft (leitura ou gravação que
falha vira log, o sync carimba igual). Sem user_id: o dono é conta -> conexão. **Quem a lê:** `quitar_pelo_banco`
(mesmo módulo), chamado pelo sync logo depois do import, sob o `pluggy_item_lock`, também fail-soft: grava
`credit_bills.paid_amount` do cartão OF pelo que o banco pagou, compra a compra (`creditCardMetadata.billId`), só em
fatura FECHADA (`(closing_date or due_date) < hoje`) de moeda BRL ou nula, com total > 0,05 e pagamento legível (sinais
misturados na lista de pagamentos = ilegível), teto 1 por fatura (fechada com total <= 0,05 entra com parte 0: o banco zerou
a fatura, o pago das compras dela volta a 0, e ela não pontua a regra), escrita
absoluta (sobrescreve o pago manual antigo). Regra por conta: a conta só quita quando a leitura "própria" (pagamento de
Y quita Y) vence a "N+1"; empate ou N+1 melhor não quita as faturas de valor e loga `regra indefinida`, mas as zeradas
(total <= 0,05) entram sempre. Só quita com fatura FRESCA (frescor por fatura): `salvar_faturas_do_banco` devolve os pares
(conta, fatura) válidos gravados naquele sync e `quitar_pelo_banco(user_id, connection_id, frescas)` só usa as linhas
de `of_card_bills` desses pares. Fatura que veio inválida, que sumiu da Pluggy (fica gravada, o upsert nunca apaga),
`/bills` que falhou ou gravação que levantou ficam fora: a linha velha não pode se combinar com compra nova; o aparo
roda em todo cartão mesmo assim. Limites: sem crédito carregado
para a fatura seguinte, rotativo (encargos) não tratado, conexão PAUSED não sincroniza, compra sem espelho na conta
atual não conta, estorno sem `billId` faz o pago parcial sair maior (o teto é o total do Pig). O desfazer de
pagamento manual (`db/accounts.py`) segue descontando do `paid_amount` também no cartão OF: o próximo sync regrava o
valor do banco nas faturas que ele possui, e nas outras (compra manual sem espelho) o desconto é o correto; com a conexão
PAUSED (ou a conta com regra `{}`) não há sync que regrave, e o pago fica reduzido até o próximo sync que quite. Pago que não cabe no total do Pig é
aparado (`APARAR_PAGO_SQL`: `paid_amount` ao total, `paid`/`paid_at`; pega pago > total e também pago == total com a fatura
ainda aberta; fatura 'paid' com pago <= 0, a zumbi (de qualquer total, inclusive total > 0 com pago 0), reabre como 'open' com `paid_at` nulo, igual o `UPDATE` da quitação, pela convenção de `db/cards.py` de que 'paid' exige pago > 0): a cada `quitar_pelo_banco`, em todo cartão da conexão (cobre, no próximo sync, a única compra casada que some por
`transactions/deleted`, que só reabre a fatura e deixaria total 0 com pago velho; o webhook não resincroniza, e até o sync a fatura
fica com pago acima do total), e ao desconectar, no cartão que sobrevive;
consequência aceita pelo dono: compra manual ainda não paga nesse cartão pode passar a aparecer paga (150 de total com 120 pago
vira 30 com 30 pago), porque o pago do banco das compras que saem fica no limite do total. Limite: o aparo também alcança a fatura
corrente do cartão do banco: pagamento manual acima do total que sobra depois que uma compra some é aparado ao total, como o
`rebuild_bill_totals` já faz. Limite: se o `billId` de uma compra
passa a apontar para fatura do banco que não está no `/bills` (a aberta), a fatura antiga do Pig mantém o pago anterior até a
do banco fechar e voltar no `/bills`. (também o export LGPD, `db/privacy.py`, lê a tabela.)
`open_finance_connections.recurring_fetched_at` e `recurring_seed_silent` controlam o
silêncio da 1ª busca do Detetive numa conexão que já existia: as chaves dela — a foto
guardada em `recurring_seed_descricoes`, não a atual — viram lápide por `record_agent_event(silencioso=True)`, que grava o evento já com
`stale_at` (não aparece no feed nem vai por e-mail).

**Cartão do Open Finance (medido na produção em 2026-10-09)**: no `amount` da conta `CREDIT`,
**positivo é compra** (`type: DEBIT`) e **negativo é estorno ou pagamento** (`type: CREDIT`),
como diz a doc do campo. `db.cards.sinal_cartao_of` é a única fonte do sinal: `valor = amount`
(assinado, a fatura faz `total += valor`), `is_refund = amount < 0`, `tipo`. Sem ramo por conector
nem por sandbox (o sandbox Pluggy Bank mostrou o oposto). O pagamento da fatura **não entra**:
`db.open_finance.pagamento_no_cartao` = `is_credit_card_payment` ou crédito (`amount < 0`) da
categoria `transferencia_interna` ("Transfer - Internal" é o pagamento visto do cartão), sem regra
de texto; é só do lado CREDIT, o BANK segue em `is_credit_card_payment`. O sync de cada item
deriva `valor`/`is_refund`/`tipo` do espelho (`_sync_imported_credit_updates`) e tira da fatura
o pagamento já importado; `scripts/corrigir_sinal_cartao_of.py` faz o mesmo (dry-run por padrão,
`--user`, `--apply`) para a conexão que não sincroniza; imprime banco e host antes de agir.
O pagamento legado ligado só ao espelho de uma conexão substituída por reconexão (o `LATEST`
devolve o espelho da nova, que nunca o ligou) é achado por `pagamentos_no_cartao_legados`:
sem recorte por conexão, e quem decide se a linha é pagamento é o espelho MAIS NOVO ligado a ela
(maior conexão, depois maior id); o sync e o script usam o mesmo. A remoção recusa se aparecer
vínculo novo depois do select (`of_tx_ids` = todas as referências vistas); o sync seguinte decide
de novo. Fatura `paid`/`closed` que passa a dever reabre na mesma transação
(`db.cards.reabrir_faturas_com_saldo`).
**Limite declarado (decisão do dono, 2026-10-09):** a correção do cartão não serializa dois
syncs de itens DIFERENTES do mesmo usuário (a trava é por item). Com a mesma transação nas duas
conexões, sincronizando ao mesmo tempo, (1) o outro sync pode reclassificar o espelho de pagamento
para compra entre a decisão e a remoção, e a compra sai até o sync seguinte reimportá-la; (2) a
fatura de destino de uma data corrigida é criada ou reaberta antes da guarda do `update`, e pode
ficar aberta com 0 a pagar se a linha não migrar. Na produção, em 2026-10-09, nenhuma transação de
cartão estava em mais de uma conexão (consulta M11). Fechar a classe pede fila por usuário na
correção do cartão: PR próprio, faixa Completo.
**Compra e parcela do banco não se apagam nem se antecipam** (`undo_credit_transaction`,
`undo_installment_group`, `anticipate_installment` levantam `db.CompraDoBanco`; /app responde
409, WhatsApp e IA a frase): o sync reimporta a linha e, na antecipação, a Carteira ficava
debitada com a fatura de volta. Mesmo `pode` do v2 (`PODE_CARTAO_SQL`).
**O cartão do banco também não se apaga** (`delete_card` levanta `db.CartaoDoBanco`; WhatsApp e /app
recusam com `MSG_CARTAO_DO_BANCO`, /app com 409 e o modal só avisa): o sync o recriaria. Para tirar, desconectar o banco.
**Cartão do banco também não tem "pagar fatura" manual** (`pay_bill_amount` levanta `db.CartaoDoBanco` com
`MSG_FATURA_DO_BANCO`, com `for share` no cartão para a adoção não passar no meio; WhatsApp, IA e /app 409; o modal
do /app esconde o botão): quem quita é o banco, via `quitar_pelo_banco`.

Boa parte do comportamento é regida por flags `OF_*` (beta por e-mail/user_id, limite
de bancos no free, refresh proativo). Antes de mexer, leia as flags — o
comportamento em produção pode não ser o do seu ambiente.

### IA

`ai_router.py` chama a OpenAI (`OPENAI_MODEL`, default `gpt-6-luna`) como fallback da
categorização determinística. O default desde 2026-10-17 é `gpt-6-luna` com `reasoning_effort` `none` (com `reasoning_effort` `low` as ferramentas dão 400 e com `none` funcionam; `max_tokens` dá 400, use `max_completion_tokens`), alias sem snapshot datado (reaferir quando o `served` do `_ai_meter` mudar); rollback = `OPENAI_MODEL` no Railway (texto; a visão é a constante `VISION_MODEL`). Há rate limiting próprio (`core/ai_rate_limiter.py`),
limite mensal de chat (`AI_CHAT_MONTHLY_LIMIT`), chat "Piggy" no dashboard
(`core/services/ai_chat/`) e agentes proativos (`core/services/piggy_agents.py`,
atrás de `AGENTS_ENABLED` + listas de beta).
A tool `simulate_purchase` (`core/services/ai_chat/tools/simulator.py`) usa o mesmo
simulador e a mesma validação da rota `/simulator`, com gate soft de Pro.

**Confirmação da IA armada e não mostrada é cancelada.** Em qualquer canal, com ou
sem `WA_IA_PRIMEIRO`, a pendência da IA (`ai_pending_actions`) armada num turno é
cancelada por CAS (`db.ai_consume_pending_action`) quando o turno termina com uma
resposta que não é a pergunta dela: erro (`ERROR_MSG`), texto vazio do modelo, prazo
estourado, `MAX_TOOL_LOOPS`, `_UM_POR_VEZ`, write direto ou recusa de validação na
mesma rodada, ou exceção. Senão um "sim" posterior executaria algo que o usuário nunca
viu (ex.: um `delete_all_launches` escondido atrás de um erro). Limites declarados:
texto livre do modelo depois de armar não cancela; falha depois do commit da
pendência e antes de o runner receber a linha gravada não cancela; falha ao cancelar só
loga. O token do CAS é a linha que o próprio `ai_set_pending_action` devolve
(`returning`), sem reler: outra janela que re-arme no meio não é cancelada.

**Pendência viva não é sobrescrita** (todos os canais; contrato da confirmação).
`db.ai_chat.set_pending_action` só substitui a linha existente se ela já venceu o TTL
(`PENDING_TTL_MINUTES`); com pendência viva de outro pedido devolve None e não grava. O
runner responde `_OUTRO_PEDIDO` ("tem outro pedido seu esperando confirmação") no
`_CONFIRMA` do `add_launch` e também no ramo `requires_confirmation`, onde a resposta é
fixa e não o texto do modelo (que ofereceria um "confirma?" cujo "sim" executaria o outro);
o `set_budget` faz o mesmo. A janela de corrida entre gravar e entregar deixou de existir;
a releitura `_ainda_e_a_mesma` no `_CONFIRMA` ficou como redundância barata para o caso de
a outra janela consumir ("não") e armar a dela no meio. O "mudou de assunto" já consome a
pendência anterior no começo do turno, então nenhuma conversa fica bloqueada pela própria
pendência. Se a rodada seguinte do MESMO turno tentar armar outra depois de armar a
primeira, a viva é deste turno: o modelo recebe "já existe uma confirmação pendente deste
turno" (resultado de tool, sem resposta final) e a primeira não é cancelada;
`_OUTRO_PEDIDO` fica só para pendência viva de outra janela. Código em
`core/services/ai_chat/runner.py` (`_cancela_pendencia_do_turno`); testes `test_p*` em
`tests/test_wa_ia_primeiro_runner.py`.

**Uma escrita com pendência por rodada.** Em qualquer canal, com ou sem
`WA_IA_PRIMEIRO`, uma rodada do modelo com uma escrita que armaria pendência
(`requires_confirmation=True`, sem rodar o `validate`; `arma_pendencia_no_execute=True`,
a que arma a pergunta dentro do próprio execute — `set_budget` e `mark_bill_paid` contadas
SEMPRE, mesmo quando aquela chamada não armaria; o `add_launch` por predicado, só quando
armaria a Q40, no WhatsApp com `WA_IA_PRIMEIRO`; ou `confirmar_se` verdadeiro) e
QUALQUER outra escrita, em qualquer ordem, não roda nada (nem as leituras dela): a
pendência do turno é cancelada e a resposta é o texto fixo `_UM_POR_VEZ` ("me manda um
por mensagem"). Antes, no dashboard e com a flag desligada, "gasta 50 no ifood e apaga o
#3" gravava o gasto e deixava a pendência do apagar viva e escondida (e, com o
cancelamento acima, ela sumia calada). Escrita com pendência sozinha na rodada, ou só
com leituras, segue como sempre. Limites: a pendência que o execute arma por conta
própria não é rastreada pelo turno (não é cancelada se uma exceção vier depois dela no
mesmo execute); e as ofertas de conveniência (botões de recategorizar / apagar) e a
oferta de gasto fixo que o `add_launch` arma pelo `add_from_entities` não entram na regra.

O "sim"/"não" de uma confirmação da IA já mostrada não gasta cota: quando o plano tem IA
e só a cota acabou (`aviso_de_cota` devolve texto; inclusive se a própria mensagem que
armou a pergunta gastou a última), o `handle_ai_chat_command` ainda o leva ao runner,
que resolve a pendência antes da cota. Sem IA no plano (v1 sem Pro, downgrade), o gate
de sempre: pendência descartada e mensagem de upgrade. Qualquer outro texto com a cota
zerada também segue como antes: aviso de cota e pendência descartada.

**Feedback 👍/👎 do chat do app (`POST /ai/messages/{id}/feedback`).** Colunas em `ai_messages`: `feedback` (1/-1/NULL), `feedback_motivo` e `feedback_texto` (nulas, sem CHECK: a borda valida). Corpo: `{nota, motivo?, texto?}`; `motivo` é uma chave de `MOTIVOS` (`db/ai_feedback.py`, fonte única; o `Literal` do `FeedbackBody` é derivado dele), só com `nota == -1`; `texto` só com `motivo == "outro"`, strip, vazio vira NULL, NUL/surrogate saneados por `limpa_para_pg`, mais de 500 caracteres é 422 (não trunca). Violação = 422 sem gravar (o handler único de 422 não ecoa o corpo). `set_feedback` grava o ESTADO INTEIRO (`feedback`, motivo e texto no mesmo UPDATE): 👍, `null` ou 👎 sem motivo zeram motivo e texto. O build 31 do app manda só `{nota}` e segue valendo (200). Isolamento como antes: `WHERE id AND user_id AND role='assistant'`, o mesmo 404 para os três casos.

**Xerife, alerta de anomalia explicado (PL-04 PR A).** A regra, a amostra e o texto moram em
`core/services/anomalia.py` (puro, Decimal; `AMOSTRA_MINIMA` é a única constante da regra de amostra:
hoje 5 lançamentos, `dias=0`) e o SQL em `db/anomalias.py` (`listar_candidatos_xerife`). Compara UM
lançamento das últimas 24h com a média POR LANÇAMENTO da mesma categoria (`lower`) nos 90 dias que
terminam 24h antes; limiar 2,5x, mínimo R$ 50 e janela não mudaram. Três mudanças de gatilho: (1) média
de referência <= 0 não alerta (antes disparava por `valor > 2,5 * 0`); (2) o empate EXATO no limiar (2,5x
ou o limiar da `config`) não alerta: o SQL antigo comparava em float8 e alertava por ruído de float
(ex.: histórico 89,44/182,40/137,42/70,26/41,56 e gasto de 260,54 = 2,5 × 104,216), e agora a conta é
em Decimal e estrita; (3) `config` com `multiplicador`/`minimo` `nan`, `inf`, `<= 0` ou `0` cai no
padrão (na main, nan/inf nunca alertavam e -2 alertava sempre) e "abc", que na main derrubava o Xerife
inteiro do usuário (inclusive o evento de limite), agora derruba só a anomalia. Só `launches` entra:
compra no cartão (`credit_transactions`) fica fora do Xerife. O evento mantém os campos legados
(`tipo`, `launch_id`, `categoria`, `descricao`, `valor`, `media`, `titulo`, `mensagem`) e ganha
`explicacao` (versao 1: `detector`, `calculado_em`, `fonte`, `referencia`, `atual`, `diferenca`,
`amostra` com `historico_incompleto` e `esperados_fora`); evento antigo não tem o bloco. Histórico
incompleto (menos de 90 dias desde a primeira despesa do usuário) é flag e frase na mensagem, não
bloqueio. Candidato que o piso de amostra suprimiu volta em `suprimidos_amostra` no retorno de
`run_xerife_once` (sem lápide: o sync do Open Finance pode completar o histórico depois). Se a
explicação falha ao montar, o alerta sai com o texto simples. `launches.esperado_em` (timestamptz,
nulo = não esperado) marca UM lançamento como "era esperado": fora do candidato e da média, sem
expiração. `PUT /agents/{user_id}/xerife/lancamentos/{launch_id}/esperado` (`{"esperado": bool}`,
mesmas portas das rotas de agentes; 404 igual para inexistente e de outro usuário) grava a coluna,
tira o alerta existente do feed e da fila de e-mail e deixa uma lápide em `anomalia:{id}`; desmarcar
não ressuscita alerta velho. A exclusão do lançamento não limpa o alerta: ele é limpo no primeiro clique em "Era esperado" (a rota responde 404 e tira o evento do feed; ocultar ao apagar é follow-up do PR B). Botão "Era esperado" no feed: `frontend/dashboard-agent-esperado.js`.
PR B: sensibilidade editável, validação estrita da config, e-mail configurável, lista de esperados
e regras recorrentes com teto/data final estão descritos em `docs/pl04-prb.md`. O contador já exclui
eventos stale. Continuam fora: `_detect_category_spike` (`db/insights.py`, compara mês parcial
com meses cheios) e cartão no Xerife (PL-06/07). Limites conhecidos: o
mesmo gasto como lançamento manual e do Open Finance gera dois alertas; alerta gerado com histórico
parcial no 1º sync fica gravado. `config` com `multiplicador`/`minimo` não finito ou <= 0 cai no padrão
(`anomalia.limiar`) no PR A. No PR B, `config_publica` recupera campos legados inválidos por
campo, inclusive lixo não numérico, com os mesmos padrões exibidos na tela; regras legadas
inválidas são ignoradas por `regras_publicas`. O "esperado" é atômico: o `update` da coluna, a lápide e o `stale` do evento existente rodam na mesma
transação (SQL local em `db/anomalias.py`; `record_agent_event` e `mark_agent_event_stale`, compartilhados,
não foram tocados), e o detector que já leu o lançamento esbarra na chave ocupada. Continua verdadeiro: e-mail
já enviado antes do PUT não é desfeito.

**Chat com agentes no `/painel` (`@Nome` na barra do Piggy).** Consumidor das rotas acima (`@Nome` na barra, `parts/AskBar.tsx`, `parts/Mencoes.tsx`, `lib/agentes.ts`; as rotas já existiam, e o contrato do `card` abaixo é o único acréscimo de backend): reaproveita `GET /agents/{uid}`, `POST /agents/{uid}/{kind}/activate` e `POST /agents/{uid}/{kind}/chat` de `frontend/routes/agents.py` (o `uid` vem só do `contaQuery`; `chamar` aceita o prefixo `/agents` e o `ErroApi.corpo` guarda o `detail` objeto). Sem `@` o Piggy segue como era (sem IA, atalhos), e o catálogo da barra só é lido quando há `@` no começo ou depois de espaço no texto. Exceção: o **painel lateral** da conversa (`parts/PainelChat.tsx`, `styles/chat-painel.css`), só a partir de 1181 px (`useMedia` em `PiggyChat.tsx`; abaixo disso nem monta, então no celular e em 761–1180 continua sem `GET /agents` sem `@`), lê `GET /agents/{uid}` mesmo sem `@`, só com a conta conferida (`conferida`), e mostra dois blocos: os agentes por grupo (as cabeças de `GRUPOS` e `⚡custo` só nos que faltam ativar; **nunca** `saved_365d`, `fired_30d` nem a energia livre), cujo clique dispara `dash:mencao` (a barra troca o token em edição; senão a menção do começo; senão põe `@Nome ` antes, sem apagar nada e com o cursor no fim), e "Nesta conversa", o índice dos cards (`Msg.cartao`, título de `tituloDe`, sem número), cujo clique rola até a resposta e põe o foco nela. Enquanto o catálogo carrega, o painel ocupa o lugar com "Carregando agentes…" (`role=status`). Catálogo em 4xx (o freio `AGENTS_UI_ENABLED=0`, `/agents` em 404) ou fora do contrato, e sem card: o painel some, `body[data-chat-painel]` desliga e a tela volta a uma coluna centralizada. Em 5xx ou rede (decisão do dono, 2026-10-20) o painel FICA, com "Tentar de novo" (`aria-disabled` durante a nova tentativa, que guarda o foco), para a coluna não saltar depois das tentativas. O envio com `@` cancela a leitura do catálogo em voo antes de reler (`cancelQueries` em `AskBar.tsx`): com o painel, a leitura tem um observador fixo e não se cancela sozinha, e o envio aproveitaria uma leitura de antes de o plano mudar noutra aba. A geometria é uma só (`--chat-w`, `--chat-x`, `--painel-w`, declaradas no `body` em `chat.css`): a coluna, a barra, a fileira de perguntas e a lista do `@` leem as mesmas variáveis. Limites: o card que não aparece (mês virou, 401/403) continua no índice e leva à resposta; dois cards do mesmo tipo têm o mesmo título no índice; quem fica sem painel (4xx, catálogo vazio) vê "Carregando agentes…" e depois a coluna desloca ~144 px uma vez (só com o freio ligado: `agents_ui_enabled` é `True` por padrão); as "próximas perguntas" ficam na fileira acima da barra; o app (`app/`) não tem o painel. A PRIMEIRA menção válida (nome de `AGENTES`, sem acento/maiúscula, o mais longo primeiro) decide o agente e a mensagem vai sem esse token; as outras ficam no texto; a bolha pinta toda menção válida de `--pink-ink`. `acessoDe` copia a fórmula de `dashboard-agent-chat.js` e só AGRUPA a lista (ativos / ativar para conversar = `activate` + `no_energy` / No PigBank+ = `upgrade`, que o servidor aplica a todos os agentes de uma vez, então o grupo nunca aparece junto dos outros); quem decide é o servidor. Estados da resposta (`lib/conversation.ts`): enviando, pronto (texto puro, `white-space: pre-line`, nunca HTML; um botão por `redirects`), erro (`detail.message`; "Tentar de novo" só se o servidor mandou `detail.retryable === true` ou se a requisição falhou na rede, sem resposta, como no chat legado `frontend/dashboard-agent-chat.js`: um POST que chegou ao servidor e se perdeu na volta, repetido, pode gastar cota de novo; 401, 403 sem `error` de objeto (CSRF e "Acesso negado para este usuário" de troca de conta em outra aba) e `password_required` mostram "Sua sessão terminou. Recarregue a página." (ou o pedido de senha), sem repetir; o mesmo TEXTO de `MetasResposta`/`InvestidoResposta`, mas critério próprio. `/agents/*` fica fora do `/api/v2`, então o `password_required` real vem como `{"detail":{"error":"password_required"}}` e é lido em `detail.error`, no chat e no "Ativar e responder"), inativo ("Ativar e responder": o bloqueio vale enquanto a ativação/o envio daquele agente está em voo, então duplo clique é uma ativação só, mas duas bolhas inativas do mesmo agente clicadas em sequência fazem duas (o servidor aceita reativar)), sem energia e sem plano ("Ver planos" → `/precos`). O `context` de cada agente e os envios em voo ficam fora do React (um envio por agente por vez); `invalid_context` apaga o context. `kind`/`nome` de `AGENTES` são presos a `DOMAINS` e ao `AGENT_CATALOG` por `tests/test_painel_agentes_catalogo.py`. O app tem o mesmo `@` na aba Piggy (PR #1161, `app/src/features/painel/agentes.ts`); como `app/` e `webapp/` são projetos npm separados, o catálogo fica duplicado, e o mesmo teste prende os dois a `DOMAINS`/`AGENT_CATALOG` e exige `AGENTES` igual (kind, nome e tema) nos dois lados. **Card com os dados do tema (Faria Limer, Barão, Detetive, Repórter, Xerife, Carteiro e Banqueiro).** A resposta de `POST /agents/{uid}/{kind}/chat` ganha `card`, sempre presente: `null | {"tipo": "investido" | "renda_fixa" | "assinaturas" | "caixinhas"} | {"tipo": "contas", "hoje": "AAAA-MM-DD"} | {"tipo": "orcamentos", "mes": "AAAA-MM"} | {"tipo": "resumo_mes", "mes": "AAAA-MM", "motivos": [...]} | {"tipo": "comparacao_meses", "meses": ["AAAA-MM", "AAAA-MM"], "motivos": [...]}` (o servidor só acrescenta: o app e o `dashboard-agent-chat.js` ignoram campo extra). Quem decide é `core/services/agent_chat_card.py::card_da_resposta`, pelas consultas que o `_answer` fez (`consultas`): `consultar_dados_do_agente` no `faria_limer` → `investido`, no `barao` → `renda_fixa`, no `detetive` → `assinaturas`; `get_period_summary` no `reporter` → `resumo_mes`; `compare_periods` no `reporter` → `comparacao_meses` (regra abaixo); `get_budget_status` no `xerife` → `orcamentos`; `get_bills_to_pay` no `carteiro` → `contas` (só sem `include_paid`: com as pagas o texto fala de mais que a rota, que traz só as pendentes; resultado sem `bills` = consulta falhou = `null`); `list_pockets` no `cofre` (Banqueiro) → `caixinhas` (a consulta não tem filtro; sem `pockets` = `null`); só `get_balance`, outro agente ou conversa sem consulta → `null`. O gate é o da rota v2 que o card lê (`GATES` em `agent_chat_card.py`): `investments` (`/api/v2/investido`) para `investido`/`renda_fixa`, `subscriptions` (`/api/v2/assinaturas`) para `assinaturas`, `recurring_expenses` (`/api/v2/contas-a-pagar`, o gate da agenda de boletos `/recurring-bills`) para `contas`, sem gate para `resumo_mes` e `comparacao_meses` (`/api/v2/resumo-do-mes`, lido uma vez por mês; a comparação só chega ao card se a tool passou do `financial_comparison`, e a rota não tem gate), `orcamentos` (`/api/v2/orcamentos-categoria`) e `caixinhas` (`/api/v2/caixinhas`, que não têm), e sem o plano → `null`. O card mostra o MESMO período do texto: `resumo_mes` só quando o resultado do `get_period_summary` é de UM mês civil (`cartao_por: "fatura"`, `start_date` e `end_date` no mesmo `AAAA-MM`; período livre, vários meses, mês futuro ou resposta sem datas → `null`) e o `mes` sai das datas do resultado, os `motivos` também (os selos do card são os avisos que o agente viu: a rota ainda avisa do "início do histórico" quando o corte só pegou o mês anterior, que a consulta de um mês inteiro tira); `comparacao_meses` só quando o resultado do `compare_periods` é de EXATAMENTE dois meses civis inteiros, cartão pela fatura (`base.cartao`), o mais antigo em `anterior` e o mais novo em `atual` (`meses` = `[anterior, atual]`, tirados das datas do resultado, nunca dos argumentos): período livre, lado com vários meses, mês igual, ordem trocada, mês futuro (a rota dá 422), `inicio_do_historico`, `sem_base` ou lado com `fim_ajustado`/`pedido` → `null`; o card lê o `resumo-do-mes` dos dois meses (duas leituras, UMA conferência de conta depois das duas, chave `card-comparacao_meses-<a>-<b>`: falha de uma = o card inteiro falha, nunca um lado só) e mostra Entrou, Saiu e Sobrou de cada um e a diferença (mais novo − mais antigo, em BigInt) com cor por sentido (verde quando melhorou: Entrou e Sobrou subiram, Saiu caiu; aviso quando piorou; zero neutro; o sinal +/− fica no texto); `orcamentos` só no `get_budget_status` sem `categoria` e com orçamentos, levando o mês em que a consulta rodou (`execute_read` o carimba); a rota só serve o mês corrente, então se o mês virou entre a consulta e o card, o card não aparece; `contas` leva o `today` da consulta e, pela mesma razão ("vence amanhã" e "vencida" dependem do dia), some se `/api/v2/contas-a-pagar` responder outro `hoje`; duas consultas que dão cards diferentes (dois meses) → `null`. O `mes` é validado no servidor (`MES` = `PADRAO_MES` de `api/v2/resumo_mes.py`) e no cliente (`cardDe`) (a cópia TS é presa à do servidor por `tests/test_agent_chat_card.py::test_padrao_do_mes_no_cliente_e_o_da_rota`) antes de entrar na URL; falha ao decidir é logada e vira `null` (cosmético, não derruba a resposta). O texto e o card mostram o MESMO número: o snapshot do agente ganhou `investido` (`db.investido.ler`, a regra de `/api/v2/investido`) e, no Barão, `total_renda_fixa` (Renda fixa + Tesouro, `None` se algum falta saldo; os dois testes leem a mesma tabela, `tests/frontend/total_renda_fixa.json`); o do Detetive ganhou `total_mensal` (a soma das assinaturas ativas, o número do card). No painel, `parts/CardAgente.tsx` escolhe a leitura pelo tipo (um componente por tipo: `CardInvestido`, `CardAssinaturas`, `CardResumoMes`, com o "Sobrou" = entrou − saiu em BigInt, `CardComparacaoMeses` (a mesma subtração, `menos`), `CardOrcamentos`, que reusa a `Linha` de `widgets/OrcamentosCategoria.tsx`, `CardContas` e `CardCaixinhas`; testes dos que leem `resumo_mes` e `comparacao_meses` e dos de assinaturas e orçamentos em `tests/frontend/dashboard_v2_chat_agentes_card_temas.test.mjs` e dos dois últimos em `tests/frontend/dashboard_v2_chat_agentes_card_contas.test.mjs`) e lê a rota v2 do tipo sob a chave `["conta", "card-investido" | "card-assinaturas" | "card-resumo_mes-<mes>" | "card-comparacao_meses-<a>-<b>" | "card-orcamentos" | "card-contas" | "card-caixinhas", uid]` e só mostra a resposta depois de `daConta` (`conta/api.ts`: relê `/auth/dashboard-profile` e lança `ContaTrocada` se a sessão já não é a do `uid`, porque a /api/v2 lê o usuário da SESSÃO): sessão de outra conta = nenhum número na tela e `lerContaFresca` (a conversa da anterior some). Rede/5xx ou corpo fora do contrato: "Não deu para carregar os dados agora." + "Tentar de novo"; 401/403: nada. Limites: o card é um retrato lido DEPOIS da resposta (relido no foco), então pode diferir do que o agente leu se o banco sincronizar no meio; os selos do `resumo_mes` e do `comparacao_meses` são os da resposta e não são relidos (um aviso resolvido depois continua no card); `get_period_summary` e `compare_periods` na mesma resposta, ou dois `compare_periods` de meses diferentes, não têm card (cada um daria um card diferente); só estes tipos; `contas` e `caixinhas` não têm link (o painel não tem tela de contas a pagar nem de caixinhas; `/metas` é outra entidade); o `resumo_mes` do mês corrente lê o mês inteiro (a rota inclui lançamento com data futura, como o `get_period_summary` de meses inteiros); o Detetive sempre consulta o snapshot, então toda resposta dele com dados leva o card de assinaturas, mesmo a de duplicidade, e o "Ver as N" conta só as assinaturas ativas (o texto tem `total_recorrencias`, com as outras e as canceladas); o Carteiro e o Banqueiro não têm argumento que recorte o conjunto (`get_bills_to_pay` só tem `include_paid`, `list_pockets` nenhum), então numa pergunta sobre uma conta ou uma caixinha só o texto fala dela e o card mostra todas as pendentes / todas as caixinhas; conta variável sem estimativa (gravada como 0) aparece como "valor a definir" e fica fora do total; o app (`app/`) não desenha o card (par: issue #1197). O protótipo usa dados e respostas fixas, sem rede. Fora, de propósito: contador de cota, acesso vindo do servidor na lista (o 403 do chat vale) e pausar agentes para liberar energia.

Categorização tem uma armadilha própria: **categoria e regra de categoria são tabelas
diferentes** (`user_categories` × `user_category_rules`) e a regra ganha da categoria
na inferência.

**"Testar o Piggy" (demo no WhatsApp, sem conta).** `GET /teste` (`frontend/routes/static_pages.py`)
grava o clique em `demo_sessions` (utms) e abre o WhatsApp com "Quero testar o PigBank (teste CODIGO)";
`adapters/whatsapp/wa_demo.py` desvia a mensagem no começo de `process_message`, ANTES de
`get_or_create_canonical_user`: 8 perguntas (`LIMITE_MSGS` em `core/services/demo/dados.py`) sobre dados
fictícios de exemplo (`core/services/demo/`), sem ferramentas; o Piggy fala em 2ª pessoa ("seu saldo"), nunca em 3ª nem com nome de persona, e se perguntarem da conta real esclarece que é exemplo. O demo não grava conta, lançamento nem texto; o único rastro é `demo_sessions`
(contador, carimbos e hash do número por 30 dias): não toca em users / user_identities / launches /
ai_messages, e a tabela é anônima (sem `user_id`; toda query filtra por `code` ou `wa_hash`). Número com conta (telefone de conta sem credencial, que ainda produz `precisa_senha`; `vincular CODIGO` ou outro canal) nunca
entra (`numero_tem_conta`, espelho de `attempt_whatsapp_phone_link`, com os mesmos `candidatos_simetricos` e o mesmo `db.google_auth.SEM_CREDENCIAL_SQL`; telefone de conta com senha não conta, o telefone não liga mais); `vincular 123456` segue o fluxo
normal. Ao fim, o link `/t/{code}` carimba `checkout_at` e manda para `/precos?origem=teste` (destino fixo,
`Cache-Control: no-store`, `shared_limit`). Interruptor e teto: `DEMO_DAILY_MAX` (padrão 0 = desligado; sessões
abertas por 24h; `<= 0` desliga, lida a cada chamada). Retenção: 1 teste por número a cada 30 dias e o
`wa_hash` é zerado após 30 dias (`esquecer_numeros_antigos`, em `table_cleanup`); copy: "o que você
escrever aqui não é salvo no PigBank, só vai para a IA que responde", nunca "não guardamos nada" nem "nada
fica salvo" (o hash do telefone é o mesmo de `auth_accounts.phone_hash`).
Painel: bloco `teste` do `/admin/funil` ("Clicaram" são cliques, não pessoas). Limites declarados:
histórico da conversa em memória (reinício zera; o contador não), teto diário não estritamente atômico entre
instâncias, falha de banco ao abrir sessão aparece como "lotou". Também: (a) `DEMO_DAILY_MAX` padrão 0 = desligado; o dono
liga na Railway (sugestão inicial 300; medir a latência da fila do worker antes de subir); (b) `wa_hash` = mesmo HMAC de `auth_accounts.phone_hash`,
guardado 30 dias: a política de privacidade deve citar o teste; (c) o texto vai à OpenAI e o histórico fica só em
RAM (sem prazo estrito; some no reinício); (d) falha transitória de banco em `sessao_recente` faz `decidir`
devolver `None` e a mensagem seguir o fluxo normal, que pode criar linha em `user_identities` (fail-open raro);
(e) linhas de clique em `/teste` sem abrir nunca são podadas;
(f) L2: resposta 401 do `send_text` (token expirado) conta a pergunta sem entrega;
(g) depois do "lotou" a mensagem seguinte do número segue o fluxo normal e cria `user_identities`;
(h) se `sessao_recente` falhar no banco e a mensagem tiver o gatilho, `decidir` manda abrir e nasce a 2ª
sessão com 8 perguntas novas (fail-open raro);
(i) o webhook (`wa_app.py`, ~:313-324) grava `recipient_id` CRU em `system_event_logs` quando a Meta devolve
erro de envio: defeito ANTERIOR a este PR e de todos os usuários;
(j) o worker do WhatsApp é único e serial (`wa_app.py`, ~:360-364): cada pergunta do demo ocupa a fila dos
pagantes por até 30s×2 tentativas, e a fila (maxsize 500) descarta o excedente: medir a latência antes de
subir o teto;
(k) `/teste` e `/t/` estão em `Disallow` no robots.txt, o que mitiga cliques de robôs; linhas sem abrir não
são podadas.

### E-mail

**Resend** (`RESEND_API_KEY`), em `core/services/email_service.py` — **não é mais
SMTP/Gmail**. Além dos transacionais (verificação, boas-vindas, reset), há e-mails de
ciclo de vida (reengajamento, downsell de trial, relatório de
agente, mudança de plano), com link de descadastro (`make_unsub_url` + `unsub_headers`).

Falha de e-mail é silenciosa por contrato: loga e não quebra o fluxo principal.

### Push (app iOS)

APNs direto, sem serviço intermediário: `core/services/push_service.py` +
`frontend/routes/push.py`, tabela `push_tokens` (token único por aparelho, com
`environment` separando sandbox de produção — o mesmo token não vale nos dois).
Configuração em `APNS_KEY_ID`, `APNS_TEAM_ID`, `APNS_AUTH_KEY`, `APNS_TOPIC`.
Sem as quatro o envio só loga e devolve 0: **`APNS_TOPIC` não tem padrão** (o id do app Expo é
`com.pigbankai.mobile`, `.staging` no staging e `.dev` no desenvolvimento, `app/app.config.ts`; um padrão errado apagaria tokens válidos).
O APNs recusar com `DeviceTokenNotForTopic` apaga o token, como `BadDeviceToken`/`Unregistered`
(só ele; `TopicDisallowed`/`BadTopic`/`MissingTopic` são nossos e não apagam).

- **Token amarrado à sessão.** `push_tokens.session_jti` recebe o `jti` da sessão que fez o
  `POST /api/push/register`. `db.list_push_tokens` (leitor do sender, do teste do admin e de
  `scripts/push_test.py`) só devolve token de sessão não revogada **e** com um
  `auth_refresh_tokens` daquele `session_jti` utilizável. `session_jti` NULL (registro antigo)
  nunca recebe até o aparelho registrar de novo. Não fecha o logout sem rede (PR 2, no aparelho).
- **Baixa com dono.** `POST /api/push/unregister` só apaga token do próprio usuário (200 sempre).
- **Push "banco precisa de atenção".** `core/services/open_finance_proactive.py::run_reconnect_push`,
  no tique do Open Finance. Mesma regra do aviso do WhatsApp (`list_connections_needing_reconnect`).
  Trava por conexão `open_finance_connections.push_reconectar_em` (NULL = nenhum push neste
  episódio; limpa só quando a conexão volta a ficar saudável na tela, atualizada ou parcial (LOGIN_ERROR → ERROR → LOGIN_ERROR é um episódio só); solta se nenhum aparelho recebeu).
  Um push por usuário por varredura, sem nome de banco; `data={"rota": "conexoes"}`. Barras
  antes de reivindicar: `filtrar_por_acesso`, preferência, algum token entregável.
- **Preferência.** `auth_accounts.push_bank_reconnect_opt_out` (default `false`), exposta como
  `push_bank_reconnect_enabled` em `/settings/{uid}/notifications` (GET e PATCH); leitura direta
  `db.push_bank_reconnect_bloqueado`, sem o cache do `get_auth_user`.

- **Lado do app (PR 2).** `app/src/services/push.ts::registrarAparelho` faz `POST /api/push/register` (só
  iOS, só com permissão concedida e sessão no cofre; engole qualquer erro, sem retentativa própria) a cada
  login, abertura e volta do fundo, disparado por `app/src/features/push/Push.tsx` (montado no `_layout`), sempre
  com a trava biométrica livre: a volta do fundo só conta a volta e o registro roda no efeito com o `registravel` já
  atualizado, então depois da carência ele espera o Face ID (a borda de subida registra uma vez). O
  `environment` vem do `aps-environment` do perfil (`features/push/aparelho.ts::ambienteApns`), não do
  `APP_ENV`: `development` = sandbox, `production` = production, sem perfil = production em aparelho real e
  sandbox no simulador. O desregistro local no APNs é o gancho em `apagarSessaoNoCofre`
  (`app/src/storage/secure.ts`): toda limpeza do cofre passa por ali; o Sair NÃO chama
  `/api/push/unregister` (decisão do dono). `aparelho.ts` é folha (nunca importa `client`/`secure`) e tem uma
  fila que põe o desregistro antes de qualquer token pedido depois, e o `getDevicePushTokenAsync` tem prazo
  (`TEMPO_TOKEN_MS`, `app/src/features/push/aparelho.ts`; o nativo só resolve em didRegister/didFail; offline ou no simulador pode nunca resolver e travaria o
  desregistro do Sair): estourou, o registro desiste e o token que chegar depois é ignorado (nenhum POST). O
  desregistro CANCELA a espera do token em voo antes de enfileirar (roda na hora: depois da chamada nativa que já
  estava em voo, mas possivelmente antes da resposta dela), e um registro que decidia enquanto o Sair chegava desiste sem pedir o token;
  sem sessão no cofre nunca se chama `getDevicePushTokenAsync`. O toque lê `notification.request.trigger.payload.rota` (o iOS não copia a raiz do
  payload para `content.data`) numa tabela própria (`Map`), espera a sessão e a trava e descarta se anônimo. Cada
  resposta vale uma vez, por `identifier:date` (o servidor manda `apns-collapse-id` e, no iOS, o identificador de
  um push remoto com collapse id é o próprio collapse id, igual em todos).
  Token que rola com o app aberto: `Push.tsx` assina `addPushTokenListener` só com sessão e trava livres, e o
  ouvinte (`services/push.ts::registrarToken`) registra o token do evento sem NUNCA pedi-lo ao nativo (o evento
  dispara a cada `didRegister`, inclusive os do nosso `getDevicePushTokenAsync`: pedir de novo faria laço). Ele
  confere o `ultimoToken` (em `aparelho.ts`, zerado em todo desregistro e quando o POST falha); o
  `registrarAparelho` continua postando sempre, porque o login novo precisa amarrar o token à sessão nova. Pode
  sair um POST repetido entre os dois (upsert no servidor).
  **Limites só do iPhone.** (a) O `getDevicePushTokenAsync` real guarda a promessa nativa no módulo e não chama o
  nativo de novo enquanto ela está pendente: se o iOS nunca responder ao pedido pendente depois do
  `unregisterForRemoteNotifications` (Sair offline), todo registro seguinte nesse processo estoura o
  prazo (`TEMPO_TOKEN_MS`) e devolve `null` até o app reiniciar. O desregistro em si não espera mais o prazo. Conferir: modo avião, abrir o app, Sair, logar como B, tirar do
  modo avião; o push de A não pode mais chegar (se a resposta tardia re-registrar o aparelho no APNs, chega). (b) A dedupe do toque por `identifier:date` supõe que o 2º push com o mesmo `apns-collapse-id`
  traz um `date` novo (a data de entrega).
  **Limite:** `expo-notifications` entra no import de todo o app pelo `secure.ts` (gancho do desregistro): um dev
  client antigo, sem o módulo nativo, cai nesse import; o build da loja traz o módulo e não corre o risco.
  A chave da tela de Notificações ("Banco precisa de atenção", só iOS) é `push_bank_reconnect_enabled`:
  com a permissão do sistema concedida escreve o PATCH (só esse campo); negada abre os Ajustes (zero PATCH);
  indeterminada pede a permissão, registra e, se a preferência estava desligada no servidor, grava `true`
  (`features/push/usePermissaoPush.ts`, `services/push.ts::pedirPermissaoPush`). Negada mostra o selo "Nos Ajustes".

Atraso: o push só sai na varredura do tique (~6 h). Plano do push no app e decisões do dono:
[`plano-push-app.md`](plano-push-app.md).

Limites declarados do PR 1 (servidor):

- A reconexão pelo widget não solta a trava: se o banco quebrar de novo antes de uma varredura ver a
  conexão Atualizada ou Parcial, esse episódio fica sem push (reconexão que fica em updating/erro
  temporário mantém a trava sem prazo).
- Só Atualizado/Parcial reabre a trava: oscilação, reconexão sem sync, pausado ou removido que voltam a
  quebrar não geram 2º push.
- Banco curado no mesmo instante da varredura ainda pode receber 1 push (intervalo entre a leitura e a
  reivindicação, igual ao aviso do WhatsApp).
- Requisito para o PR 2: tokens existentes ficam com `session_jti` NULL e param de receber no deploy; o
  app precisa chamar o register depois de CADA login.
- Re-registro sem `jti` (cliente legado) zera o `session_jti` do token, que para de receber.
- A varredura não tem prazo próprio: com o APNs fora, cada token espera até 10 s em série e atrasa o
  PATCH do mesmo tique.
- Ao ligar as envs (PR 3), todo usuário com banco já quebrado e token entregável recebe 1 push. Um
  `APNS_TOPIC` digitado errado apaga os tokens no 1º envio (`DeviceTokenNotForTopic`); eles voltam
  quando o app registrar de novo. Conferir a env antes de ligar.

### Admin e afiliados

Área administrativa própria (`/admin`, `/admin/login`, `/admin/api/*`) com sessão
separada (`ADMIN_DASHBOARD_*`), visão de usuários, overview, auditoria de acesso a PII
e gestão do programa de afiliados (comissões, payouts, PIX). As páginas
`admin-login.html` e `admin-dashboard.html` **não carregam app-mode nem o shim de área
segura**, de propósito.

O drill-down de uma conta troca o plano à mão (`POST /admin/api/users/{id}/plan`
→ `set_account_plan`, a mesma escrita do `/admin/grant-pro`): grava
`plan`/`plan_expires_at` no banco e **não fala com a Stripe** — assinatura viva
continua lá e o próximo webhook dela sobrescreve.

A segunda escrita do drill-down libera novo trial
(`POST /admin/api/users/{id}/trial-reset` → `db.plans.reset_trial_for_user`):
apaga a linha de `plan_trials` do **telefone** da conta e zera
`trial_started_at`/`trial_downsell_sent_at`. **Também não fala com a Stripe** —
por isso recusa com 409 quando `last_payment_status` é `trialing|active|past_due`.

`/admin/feedback` (`core/feedback_routes.py` + `db/ai_feedback.py::resumo_semanal`/`listar_negativos` +
`frontend/admin-feedback.html`) é a tela do dono para ler o 👍/👎 do chat do Piggy (PR 4a/4b gravam, esta lê):
semanas em BRT (segunda a domingo) com 👍, 👎 e 👎 por motivo, e a lista dos 👎 com pergunta, resposta, motivo e
comentário. Mesma sessão do `/admin` (reusa `get_current_admin`), só GET, `no-store`, `limit` 1–200. É a ÚNICA leitura
entre usuários de `db/ai_feedback.py` (CLAUDE.md §0): a mensagem é identificada só por data e id, a saída não tem
`user_id`/nome/e-mail, e cada leitura grava em `pii_access_log` (`purpose=render_admin_ai_feedback`, uma linha por
usuário da página, `extra.message_ids`), e a auditoria FALHA FECHADA: se a gravação falhar (INSERT, FK de usuário
apagado no meio) a rota dá 503 sem dados e registra `admin_feedback_auditoria` (diferente de `_record_access`, que engole o
erro); `PII_AUDIT_DISABLED=1` segue desligando tudo. Motivo fora de `MOTIVOS` entra em `outros_motivos`. A pergunta é a última mensagem `user` DO MESMO `user_id` antes da resposta.
Sem índice parcial em `feedback`: as consultas varrem `ai_messages` sob `statement_timeout` de 5 s; se estourar, o
conserto é um índice `where feedback is not null`, não aumentar o prazo. A frase de `frontend/privacy.html` que avisa
que a equipe lê essas avaliações é parte do contrato.

`/admin/funil` (`core/funil_routes.py` + `core/funil_dashboard.py` + `frontend/funil.html`)
é o painel de funil: só leitura, agregados fechados (nenhum `user_id`/e-mail/quiz),
7d e 30d na mesma resposta de `GET /admin/api/funil`, mesma sessão do `/admin` (reusa
`get_current_admin`; sem auth nova). Links externos só aparecem com o ID configurado
(`GA4_PROPERTY_ID`, `META_PIXEL_ID`, `CLARITY_PROJECT_ID`, `STRIPE_SECRET_KEY`).
Regras de leitura: coorte = cadastros criados na janela, e as etapas são
CUMULATIVAS ("alcançou pelo menos"; o Pix só grava `completed`). Taxa ligada a
/precos vira "—" quando o coorte começa antes do "medido desde"
(`min(created_at)` de `viewed_pricing`), então nos primeiros dias/semanas
pós-deploy a janela de 30d mostra "—". Canal: afiliado > prospecção > quiz >
direto (quiz só sim/não, nunca perfil). `expired` NÃO é abandono (abandono =
abriram − concluíram). `origem` é restrita a `ORIGENS` (+ "outro"): provedor
social novo entra em `ORIGENS` e no teste de igualdade com
`signup_source_from_request`. Sem cache/índice por decisão **para o banco** (o
`/admin/api/funil` em si); se a consulta passar de ~5s, TTL de 60s antes de índice. As
**fontes externas** (abaixo) são outra coisa e TÊM cache. Contas de cortesia/internas
entram em "cadastros".
**Fontes externas** (`core/funil_fontes.py` + `core/funil_fonte_<nome>.py`): rota
`GET /admin/api/funil/fonte/{nome}` (mesma auth do painel, `no-store`, só GET), uma
chamada por fonte, e `/admin/api/funil` NÃO muda. `nome` só vale se estiver em
`funil_fontes.MODULOS` (fonte nova = uma linha lá + um módulo que exporta `FONTE`); fora
disso, 404. Hoje `stripe` (assinaturas agora; cobranças de cartão em 7d/30d; a receita
é só do Stripe, o Pix anual NÃO entra, e `canceladas` é o histórico total), `ga4`, `clarity` e `meta` (abaixo). Envelope
comum e fechado: `fonte`, `estado` (`ok|nao_configurado|erro|stale`), `mensagem` (SEMPRE
da tabela fixa `MENSAGENS`, nunca texto da fonte), `falta` (só NOMES de env, quando
`nao_configurado`), `buscado_em`, `janela {rotulo, fuso}`, `dados`. Cache em TABELA
(`funil_fontes_cache`, só agregados, sem `user_id`): TTL por fonte, `falha_em` + backoff
(não martela uma fonte caída), `stale` quando falha mas há payload, e cota diária por
reserva atômica (`reservar`: um `INSERT ... ON CONFLICT DO UPDATE ... WHERE`, vale com
várias instâncias). Banco do cache fora do ar: fonte sem cota segue ao vivo, fonte com
cota recusa. O front pede cada fonte separado depois do funil, e "Atualizar" nunca força
chamada externa (quem manda é o TTL do servidor). Regras de segredo: chave/token só no
servidor e lidos na hora da chamada; os módulos de fonte logam SÓ `type(exc).__name__` e
um código fechado (nunca `exc_info`, `str(exc)` nem corpo de resposta: a mensagem de erro
do SDK pode trazer a chave e o `_DashboardHandler` grava log em `system_event_logs`);
host fixo; chamada bloqueante só no executor dedicado (`submit` + `wrap_future`), nunca no loop. O Stripe reaproveita
`admin_dashboard.fetch_billing_summary` (cache próprio de 5 min) e faz UMA listagem
nova de `charges` (cliente com timeout de 8s, sem retry, teto de 1.000 itens e prazo
total de 10s na paginação; ao estourar, devolve o que tem com `truncado: true`).
Execução: as fontes rodam num executor dedicado de 4 threads (`funil_fontes.EXECUTOR`),
nunca no padrão do loop, com uma marca de voo por fonte e por processo; para fonte
SÍNCRONA isso é no máximo UMA thread em voo (para `async def buscar`, como o Stripe, só a
marca: o `to_thread` interno do resumo segue no executor padrão). Quem chega com a fonte
ocupada recebe cache/`stale`/`erro` com a mensagem `ocupada`, sem thread nova nem cota
gasta. Thread que sobrevive ao timeout (não dá para matar) segura a marca até terminar ou
até `TIMEOUT_S + backoff_s`. Depois de pegar a marca o cache é RELIDO (TTL/backoff de novo),
senão uma leitura antiga chamaria a fonte dentro do backoff. `dados` só passa se for JSON
puro: objeto na raiz, chaves e textos sem NUL nem surrogate solitário, no máximo 20 níveis,
20.000 nós e 256 KB; senão `erro` com `resposta_invalida` e `falha_em` (backoff). Se gravar
o cache falhar por qualquer causa, devolve `ok` com o dado vivo e grava `falha_em` para não
rechamar a fonte a cada GET. O DIA da cota vem do relógio do BANCO (`_HOJE_TESTE`/`hoje` são override só
de teste) e o contador só reinicia em dia MAIS NOVO. Já `buscado_em`/`falha_em` são gravados
com o relógio da INSTÂNCIA que gravou; quem lê trata timestamp no FUTURO como vencido (a
fonte é rechamada, respeitando cota e voo único), senão um relógio adiantado congelaria a
fonte. O cache vive em `core/funil_fontes_cache.py`; a regra de TTL/backoff em `funil_fontes.py`. `limite_dia` é `None` ou >= 1 (senão `ValueError` na importação).
Cancelar a requisição propaga o cancelamento: a reserva já feita fica gasta e `falha_em`
não é gravado (limite declarado).
Limites declarados do cartão Stripe (sem correção): cobrança `succeeded` com
`captured is False` (só autorização) não conta; disputas e captura parcial
(`amount_captured`) NÃO são abatidas da receita; `buscado_em` pode subestimar a idade das
assinaturas em até ~5 min (cache em memória do `fetch_billing_summary`);
`STRIPE_LOG=debug` faz o SDK imprimir o corpo de `charges.list` no stderr (não habilitar em
produção); o 401 de `/admin/api/*` não leva `no-store` (já era assim em `/admin/api/funil`);
o resumo de assinaturas (`fetch_billing_summary`) segue com o timeout padrão do SDK (80s,
`max_network_retries=2`, até ~240s) e roda no executor padrão; o backoff de 60s limita isso
a ~4-5 threads vivas. O `timeout=8` da listagem é por OPERAÇÃO de rede: servidor que goteja
bytes segura a thread além do prazo de 10s. O SDK loga sozinho no logger `stripe` (INFO
com `error_message`, DEBUG com o corpo): o módulo do Stripe fixa esse logger em WARNING. O `reason` dele agora é
só o NOME do tipo da exceção (antes era `str(exc)`, que podia levar a chave).
**Fonte GA4** (`core/funil_fonte_ga4.py`, cartão "8b. GA4"): Google Analytics Data API
(`runReport`), TTL 30 min, backoff 300 s, SEM cota diária; cada refresh faz no máximo 3 chamadas
(token da conta de serviço, só quando o de ~1 h venceu, mais 2 relatórios), ou seja, bem abaixo
da cota da API. Envs: `GA4_PROPERTY_ID` (só dígitos, validado ANTES de montar a URL; ID ruim
vira `nao_encontrado` sem nenhuma chamada) e `GA4_SERVICE_ACCOUNT_JSON` (o JSON inteiro da chave;
`faltam()` lista só os nomes). Hosts FIXOS (`oauth2.googleapis.com/token` e
`analyticsdata.googleapis.com`): o `token_uri` de dentro do JSON nunca é lido. A troca do JWT é
PyJWT + `requests` (e não `google-auth`, cujas exceções podem carregar trecho do corpo do Google
ou do JSON); o token fica em memória, com chave = SHA-256 do JSON (trocar a env invalida) e
`_LOCK`; 401 da Data API zera o token. Devolve (lista fechada) `eventos {7d|30d: {<evento>:
{eventos, usuarios}}}`, só para a lista `EVENTOS` (única fonte: `page_view` do `gtag('config')`,
`view_item_list`, `begin_checkout`, `sign_up`, `start_trial`, `onboarding_complete` (via
`pbTrack` em `comecar.js`), `vsl_play`, `vsl_progress`, `teste_click` (botão "Testar o Piggy", `teste-piggy.js`) e `purchase` do servidor; um teste compara a
lista com o que o código dispara, e o front tem a mesma lista em `GA4_EVENTOS`, comparada em
`tests/frontend/funil_ga4.test.mjs`) e `origens {7d|30d: [{canal, sessoes, usuarios}]}` (top 8
por janela; `canal` é texto do GA, sanitizado e truncado em 40, e o front o passa por `esc()`).
Evento fora da lista é descartado; sem linha = zeros; número que vem como texto vira `int`.
Códigos de erro (mensagens fixas em `MENSAGENS`): 400 `resposta_invalida`, 401 `auth`, 403
`permissao_ga4` (API não habilitada no projeto OU conta de serviço sem papel Leitor), 404
`nao_encontrado` (ID da propriedade errado), 429 `limite`, timeout `timeout`, JSON de credencial
ilegível/sem campo/PEM ruim `credencial_invalida` (nada do JSON é ecoado). SEGREDO: a
`private_key` e o token OAuth não saem do módulo (log só com `type(exc).__name__`, status e
código; `from None`); o `urllib3` em DEBUG só imprime host, caminho e status, então nenhum
logger é rebaixado. Limites declarados: 7d/30d são `7daysAgo..today` e `30daysAgo..today`
(incluem HOJE, parcial) no fuso da propriedade; os números do GA4 não batem com os do banco
(bloqueador de anúncio, consentimento); `purchase` inclui os enviados pelo servidor
(Measurement Protocol); usuários não somam entre linhas; `timeout=5` é por operação de rede (o
backoff limita threads presas); o formato de `runReport` foi seguido pela documentação e testado
contra um servidor local, NÃO contra o Google real (parser tolerante). O `limit` da API vale para
as linhas dos dois ranges juntas: pede-se 100 e corta-se em 8 por janela no parser. Fica de fora
de propósito: demografia, dimensões customizadas (ex.: `method` do `sign_up`) e o relatório de
funil nativo (v1alpha).
Limites do GA4 declarados, sem correção: o corpo da resposta não tem teto de tamanho (um par
hostil no TLS poderia mandar 100 MB: ~20 s de parse e ~900 MB de RSS) e o `timeout` do requests é
por operação, não total (resposta que goteja segura a thread; até ~21 min por ciclo, limitado
pelo backoff). Sem `orderBys`: com mais de 100 linhas somadas (7d+30d; só com muitos canais
personalizados) o top 8 pode ficar errado sem aviso (com ~18 grupos padrão são ~36 linhas);
`rowCount` é ignorado. Um 403 de COTA esgotada aparece como a mensagem de permissão (a cota
normal esgotada é 429, `limite`); `invalid_grant` por relógio adiantado da instância aparece
como "recusou as credenciais". `HTTP_PROXY` não-https mandaria o assertion ao proxy em claro (em
produção a URL é https: o proxy só vê o CONNECT). O `_TOKEN` guarda o access_token em claro, por
desenho (é o que se reaproveita); redirecionamentos NÃO são seguidos (3xx vira `indisponivel`).
O `access_token` só passa se casar `[A-Za-z0-9._~+/=-]{1,4096}`. Dois canais iguais após a
sanitização aparecem como duas linhas. `obter` (infra) devolve `erro` genérico se o módulo da
fonte não importa, `faltam()` quebra ou a montagem do envelope falha (só o NOME do tipo vai ao
log); só o cancelamento propaga.
Outros limites aceitos do GA4: o `overflow-wrap: anywhere` do `.wrapcol` (tabelas do cartão) é
ignorado no Safari 14 (cosmético: o canal tem no máximo 40 caracteres e a tabela rola dentro
do `.tblwrap`); 400 da Data API e resposta que não é JSON mostram a mesma mensagem ("não deu
para ler"), e quem distingue é o log do Railway (`[funil_ga4] dados: HTTP 400`); ID de
propriedade errado tende a aparecer como 403 (por isso a mensagem de `permissao_ga4` manda
conferir `GA4_PROPERTY_ID`). Dois logs só de etapa ajudam a primeira carga: `[funil_ga4] token:
formato recusado` e `[funil_ga4] credencial: JSON inválido` (nunca o valor, o tamanho ou o tipo).
Limites declarados da infra de fontes (sem correção): a marca `_EM_VOO` é por processo e a
corrida entre loops só existe com vários workers (o limite real é a cota no banco); o
executor tem 4 threads, então 4 fontes travadas ao mesmo tempo fazem a 5ª virar `timeout` e
queimar cota (toda fonte nova precisa de timeout próprio); `buscar` que levanta
`CancelledError` vira 500 sem backoff (não acontece nas fontes planejadas);
`SystemExit`/`KeyboardInterrupt` propagam (correto); texto de 600 caracteres sem espaço
estouraria o layout (inalcançável: as mensagens vêm da tabela fixa); o WARNING persistido
pelo `_DashboardHandler` faz um INSERT síncrono no loop (uma falha por backoff); `async def
buscar` com corpo bloqueante trava o loop.
**Fonte Clarity** (`core/funil_fonte_clarity.py`, cartão "8c. Clarity"): Microsoft Clarity Data Export
API (`GET https://www.clarity.ms/export-data/api/v1/project-live-insights`, `numOfDays=3`,
`dimension1=URL`, `Authorization: Bearer`). Env: só `CLARITY_API_TOKEN` (JWT do projeto: Clarity >
Settings > Data Export > Generate new API token, aparece uma vez; o `CLARITY_PROJECT_ID` NÃO é
necessário, só alimenta o link do card "9"). Devolve (lista fechada) `{pagina: "/precos", janela_dias: 3,
trafego {sessoes, usuarios}, rolagem_media_pct, cliques_mortos {pct_sessoes}, cliques_raiva
{pct_sessoes}, truncado, reconhecido}`; `null` = "n/d" (nunca 0). Só a página /precos
(`path.rstrip("/") == "/precos"`, host/query/fragmento ignorados, soma de todas as variantes, caixa
EXATA: `/PRECOS` e `/precos-app.js` não contam); janela FIXA "últimos 3 dias (UTC)" (o cartão ignora o
seletor 7d/30d). Sessões = `totalSessionCount − totalBotSessionCount` (sem o campo de bots vale o
total); rolagem e % de cliques são médias ponderadas pelas sessões da própria linha SÓ quando a linha
traz a chave `session`+`count` (a doc pública do Clarity só mostra a contagem no Traffic; na rolagem é incerto);
sem ela em nenhuma linha a média é SIMPLES das variantes da URL, e o cartão só diz "média das variantes da URL;
ponderada pelas sessões só quando o Clarity informa a contagem". Risco: com tráfego de anúncio cada `fbclid`
vira uma variante da URL e a média simples pode divergir muito do valor real (1 sessão a 10% e 99 a 60% dão 35,0%
em vez de 59,5%): conferir na 1ª carga. Melhoria futura (NÃO implementada): ponderar pelo `totalSessionCount` da
linha de Traffic da MESMA URL.
Cliques mortos e de raiva mostram SÓ o % das sessões (chave `session`+`percentage`, sem
`without`/`bot`, 0..100): a CONTAGEM de sessões deles NÃO é exposta porque `sessionsCount` nessas
métricas provavelmente é o total de sessões da URL e não as com o evento (memória não confirmada: é
ambíguo até haver resposta real). Quando houver resposta real, o parser fixa e a contagem volta. Fica de fora de
propósito: dimensões device/país/campanha, engagement time, quickback, erros de script, páginas
populares. COTA (a mais apertada de todas as fontes): o Clarity aceita 10 requisições por projeto por
dia, então `limite_dia=8` (reserva real no banco; 2 de folga), `ttl_s=10800` (3 h × 8/dia = a cota,
sem folga para falha), `backoff_s=10800` e EXATAMENTE 1 chamada HTTP por `buscar` (a reserva é por
`buscar`). Falha, timeout e token ilegível depois da reserva QUEIMAM cota sem devolução (o backoff de
3 h limita isso a 1 por ciclo); `ocupada` não queima. Um 429 vira `cota` e a infra ESGOTA o dia no
banco (`funil_fontes_cache.esgotar`: `chamadas_dia = limite`, no dia UTC do relógio do banco, nunca
baixa o contador nem afeta dia mais novo): a fonte só volta quando o dia UTC virar, servindo `stale`.
Erros por STATUS (corpo nunca lido): 401 `auth`, 403 `permissao`, 429 `cota`, 400 `resposta_invalida`,
demais `indisponivel`, timeout `timeout` (8 s), conexão `indisponivel`; resposta que não é lista JSON
`resposta_invalida`. Redirecionamento não é seguido. Token fora de `[A-Za-z0-9._~+/=-]{1,4096}` vira
`resposta_invalida` SEM chamada (e sem log do valor; a reserva já foi gasta). Resposta de 1.000 linhas
(limite da API, sem paginação) marca `truncado: true` ("números parciais"; com `fbclid` nas URLs de
anúncio a dimensão URL pode truncar) e só cabe no `_validar` porque o módulo agrega antes.
VALIDAÇÃO DA 1ª CARGA (a fazer pelo dono): abrir o painel do Clarity para /precos na janela de 72 h e comparar
sessões, rolagem, % de cliques mortos e de raiva com o cartão; se a rolagem ou os percentuais parecerem pequenos
demais (ex.: 0,1%), a escala pode ser 0 a 1 (o cartão traz o aviso); se o cartão ficar amarelo ("formato não
reconhecido"), copiar do log do Railway a linha `[funil_clarity] formato:`.
Mais limites declarados: o campo de usuários (`distantUserCount`) provavelmente inclui bots (o cartão só avisa dos
bots nas sessões); o prazo total de 10 s só é conferido depois que chega um pedaço do corpo (conexão e cabeçalhos
usam `timeout=8` por operação: até ~18 s de thread presa contra os 12 s da infra, sem cota extra); `numOfDays=3` é
janela móvel das últimas 72 h; o limite de 1.000 linhas pode ser por métrica ou total (a doc não diz) e `truncado`
só detecta se ALGUMA métrica chegar a 1.000.
FORMATO NÃO CONFIRMADO: a doc não dá os nomes exatos de `metricName` nem dos campos de Scroll Depth,
Dead Click Count e Rage Click Count, nem a chave da dimensão URL; o parser é tolerante (nomes
normalizados, heurística descrita no docstring do módulo) e honesto (`null`, `reconhecido: false`,
aviso amarelo no cartão). PROCEDIMENTO se o cartão disser "formato não reconhecido": copiar do log do
Railway a linha `[funil_clarity] formato: metricas=[...] campos={...}` (só NOMES de métrica e de campo
da 1ª linha, até 20 métricas e 30 campos por busca; cada nome passa por ALLOWLIST
`[A-Za-z][A-Za-z0-9_ ]{0,39}` com no máximo 2 dígitos, senão vira a constante `(nome fora do padrão)`,
sem nenhum caractere do original: o Clarity pode mandar DADO como nome de campo; nunca valores, URLs ou números;
nomes fora do padrão colapsam numa só chave de `campos`; `sessionsWithoutMetricPercentage` passa) e fixar o
parser com a forma real. Usuários somam as variantes da URL
e podem repetir pessoa. Regras do parser (1ª passada do Tester): a métrica é a de nome normalizado EXATO (`traffic`,
`scrolldepth`, `deadclickcount`/`deadclicks`, `rageclickcount`/`rageclicks`); sem exata vale a ÚNICA que
contém o fragmento; duas ou mais candidatas (ou o mesmo nome repetido) = não reconhecida (nunca soma
métricas diferentes: `TrafficSources` não dobra `Traffic`). NUNCA soma parcial: se qualquer linha de /precos
não tem o número legível, o campo agregado vira `null`; se QUALQUER linha da métrica não tem URL legível (chave
ausente ou valor que não é texto), a métrica inteira fica DESCONHECIDA (`null` em todos os campos dela, "n/d" no
cartão, nunca "Sem visitas"), porque a linha descartada pode ser de /precos; `reconhecido` segue `true` (o formato é
reconhecido, só os dados são incertos; o contrato fechado do `dados` não ganha chave) e sai UMA linha de log de
texto fixo `[funil_clarity] linha sem URL legível: os campos da métrica ficam n/d` (sem nome, valor ou número, no
máximo uma por métrica). Uma URL legível de OUTRA página, inclusive a string "(not set)", é "outra página" e não torna
o agregado desconhecido. Risco: se o Clarity mandar uma linha sem URL em cada resposta (ex.: sessões sem URL), o
cartão fica "n/d" sem aviso amarelo: o dono vê o log do Railway (peso presente em só algumas linhas: média `null`;
em nenhuma: média simples). Sem linha de /precos: sessões e usuários 0 e as MÉDIAS `null`; com `truncado` e
sem linha de /precos, sessões e usuários `null` (a linha pode estar no corte) e o cartão só diz "Números
parciais", nunca "Sem visitas". A resposta é lida em stream por `r.raw.read1(65536, decode_content=True)` (urllib3 2.x; `iter_content` só entrega com
64 KB e deixaria o goteio passar do prazo), com teto de 8 MB do corpo DESCOMPRIMIDO (gzip/brotli bomba:
`resposta_invalida`) e PRAZO TOTAL de 10 s da chamada ao último byte (`timeout`; menor que o `TIMEOUT_S` de 12 s
da infra, então um Clarity gotejando não prende a thread do `EXECUTOR` compartilhado); o `timeout=8` do
requests segue sendo por operação. O corpo de erro NUNCA é lido (`r.close()` logo após o status; a resposta é
fechada em todos os caminhos); exceção do urllib3/requests durante a leitura vira `indisponivel`, e
`ReadTimeoutError` no meio do corpo vira `timeout`. Dois nomes exatos de alias ao mesmo tempo (`DeadClicks` e
`DeadClickCount`, idem Rage) = DUPLICIDADE: métrica não reconhecida (não adivinha qual vale); sozinhos, valem.
`Scroll` e `ExcessiveScroll` não são rolagem (exige `scroll`+`depth`). `_registrar_falha` grava a falha e esgota a cota em `try` separados, com
log próprio (só o tipo).
Limites declarados do Clarity, sem correção: escala 0..1 em vez de 0..100 NÃO é detectável (um 0.123 apareceria
como 0,1%): conferir na 1ª carga com o painel do Clarity; o `esgotar` pode esgotar o dia NOVO se um 429
cruzar 00:00 UTC (~12 s por dia, por evento 429); o voo único é por PROCESSO, não por réplica (N réplicas
gastam a cota 8/N janelas por dia e o resto do dia fica em "cota" servindo `stale`; a cota atômica no banco
nunca passa de 8, e a folga de 2 até o limite de 10 do Clarity cobre só rajada na virada do dia ou outro
consumidor do mesmo token; em sequência realista uma janela de 24 h fechadas pode ter até 9 chamadas, sem
folga para outro consumidor); token inválido/401 gasta a reserva e o backoff de 3 h vive no BANCO: depois de
CORRIGIR o token o cartão pode levar até 3 h para tentar de novo, e a mensagem acionável ("recusou as
credenciais") aparece só na 1ª visão (depois vira "indisponível" durante o backoff, porque a tabela não
guarda o código); ESCAPE para forçar a tentativa: `update funil_fontes_cache set falha_em = null where fonte = 'clarity'`
(mantém o contador do dia e o último dado; só libera a próxima tentativa, que CONTA na cota). NÃO use delete:
`delete from funil_fontes_cache where fonte = 'clarity'` (NÃO use delete) devolve a cota do dia e apaga o último dado,
porque o contador (`dia_utc`/`chamadas_dia`) mora na MESMA linha (dá para passar de 8 chamadas por dia contra o limite
real de 10 do Clarity); o prazo total de 10 s cobre o corpo, mas a fase de conexão e de cabeçalhos usa o
`timeout=8` por operação (connect e leitura de cabeçalho, no pior caso ~16 s antes do 1º byte); o front trata só `null` como
n/d (`""`, `false` ou `[]` virariam "0"/"NaN%"; o servidor nunca os manda); a cabeça pública de um JWT
(20 caracteres, 2 dígitos) e strings curtas como `hunter2`/`Joao Silva` passam na allowlist de nome do log
(hipótese aceita: o Clarity teria de pôr dado em nome de campo); `sessoes === 0` também vale quando bots ≥ total
(clamp a 0) e o cartão diz "Sem visitas"; `r.close()` não é determinístico se houver exceção retida
(traceback guardado: o socket só fecha quando a referência cai; teórico); campo numérico com lixo mostra
"NaN" no front (o backend só manda número); texto de 300 caracteres sem espaço estoura a largura (as
mensagens vêm de tabela fixa); com gzip/brotli o teto de 8 MB vale para o corpo descomprimido (testado
com bombas). Sem `stale` parcial (uma chamada traz todas as métricas, ou tudo ou erro).
**Fonte Meta** (`core/funil_fonte_meta.py`, cartão "8d. Meta"): estatísticas do pixel pela Graph API
(`GET https://graph.facebook.com/v25.0/{pixel_id}/stats?aggregation=event_total_counts&start_time=<agora-7d>&end_time=<agora>`),
TTL 30 min, backoff 600 s, SEM `limite_dia` (a Graph tem limite próprio, tratado como `limite` + backoff), EXATAMENTE
1 chamada HTTP por `buscar`. A versão v25.0 é constante do módulo; o `core/services/meta_capi.py` (Conversions API,
escrita) segue na v21.0 e NÃO foi alinhado. Envs: `META_INSIGHTS_ACCESS_TOKEN` (usuário do sistema do Business Manager
com o pixel atribuído e, provavelmente, permissão de leitura de anúncios (a exata não foi confirmada); SEPARADO do `META_PIXEL_ACCESS_TOKEN` da CAPI, que esta fonte
nunca lê) e `META_PIXEL_ID` (só `[0-9]{1,20}`, validado ANTES de montar a URL; ID ruim vira `nao_encontrado` sem
chamada). `faltam()` lista só NOMES, lidos na hora. O token vai SÓ no cabeçalho `Authorization: Bearer` (nunca em
query/params: o `str()` de exceção do requests carrega a URL) e só depois de casar `[A-Za-z0-9._~+/=-]{1,4096}` (senão
`auth` SEM chamada: aspas, prefixo "Bearer " ou espaço no meio; o token colado errado é recusado pela Meta de qualquer jeito); host fixo; redirecionamento NÃO é seguido; log só com `type(exc).__name__`, status HTTP e o
inteiro `error.code`; `from None`. Devolve (lista fechada) `{eventos: {<evento>: int}, reconhecido: bool}` para TODOS os
nomes de `EVENTOS` (0 se ausentes), na janela fixa de 7 dias (limite da API; o cartão ignora o seletor 7d/30d). `EVENTOS`
= os nomes que o app dispara no pixel e na CAPI: `PageView` (snippet em `frontend/routes/shared.py`), `ViewContent`
(/precos), `InitiateCheckout`, `CompleteRegistration`, `StartTrial`, `Purchase`, e os `trackCustom` `OnboardingComplete`
(`comecar.js`) e `TesteClick` (`teste-piggy.js`); um teste compara a lista com o código e o front tem a mesma em
`META_EVENTOS` (comparada em `tests/frontend/funil_meta.test.mjs`). Evento fora da lista é DESCARTADO (nome exato, caixa
inclusa) e nunca chega ao JSON.
ERROS: a Graph responde 400/401/403 com `{"error":{"code":N,...}}`. O corpo de erro é lido em stream com teto de 64 KB e
dele sai SÓ o inteiro `error.code` (0..999999; mensagem, subcódigo e `fbtrace_id` nunca são guardados nem logados).
190 `auth`; 200 e 10 `permissao_meta` (mensagem fixa: o usuário do sistema precisa ter o pixel atribuído no Business
Manager e o token "provavelmente" a permissão de leitura de anúncios, "a permissão exata não foi confirmada": o texto
(e o `.env.example`) têm hedge explícito, porque a permissão exata não foi confirmada); 80004, 4, 17, 32 e
613 `limite`; 100 `nao_encontrado` (a Graph usa 100 também para pixel que o token não enxerga, então "não encontrou o
recurso" pode ser ID errado OU pixel não atribuído ao usuário do sistema); código legível mas desconhecido,
`indisponivel`. SEM `error.code` legível (corpo vazio, HTML, sem o campo, não inteiro): o STATUS decide: 401 `auth`,
403 `permissao_meta`, 429 `limite` (corpo nem é lido); 400, 404, 5xx, 3xx e o resto `indisponivel`; timeout `timeout`; 200 com corpo ilegível ou
que não é objeto JSON `resposta_invalida`. Resposta 200 lida em stream (`read1`), teto de 1 MB DESCOMPRIMIDO e PRAZO TOTAL de
10 s (menor que o `TIMEOUT_S` de 12 s da infra; vale também para o corpo de erro); o `timeout=8` do requests é por operação.
FORMATO NÃO CONFIRMADO: a doc diz `data` = lista de resultados (`aggregation`, `start_time`, `data` = lista de
`{value, count}`), mas os nomes `value`/`count` não foram vistos numa resposta real. Parser tolerante: aceita (a) resultados
com `data` aninhada e (b) entradas direto em `data`; chaves normalizadas (minúsculas, só letras e dígitos): nome =
`value`/`event`/`eventname`, contagem = `count`/`eventcount`/`total`; soma as contagens do mesmo evento (buckets); contagem
não numérica, negativa, booleana, >= 1e15, NaN/inf ou nome que não é texto IGNORA a entrada; a SOMA de buckets também
para em 10**15 (`min`). `reconhecido` = true SÓ se ao menos uma entrada de evento da lista foi lida OU se `data` é uma
lista vazia (ou só de resultados com `data` vazia: "sem disparos"). É false (+ a linha de log) para `data` ausente, null
ou que não é lista (200 com `{}`, `{"success":false}`, `{"error":{"code":190}}`, `{"events":[...]}`), para item que não
é objeto em qualquer nível (`[1,"x"]`, `["PageView"]`, pares `[["PageView",5]]`, `[null,5,"x"]` dentro da `data`
interna) e para entradas sem nenhum evento da lista com forma legível (inclui o caso de a Meta só ter disparado eventos de
fora da lista); um evento da lista lido basta para true mesmo com lixo ao lado.
Nesse caso o cartão mostra a faixa âmbar "formato não reconhecido" e "n/d" no lugar dos zeros, e sai UMA linha de log só com
NOMES (allowlist `[A-Za-z][A-Za-z0-9_ ]{0,39}` com no máximo 2 dígitos, senão `(nome fora do padrão)`; até 30 nomes; nunca
valor, ID ou contagem): `[funil_meta] formato: chaves=[...]`.
VALIDAÇÃO DA 1ª CARGA (a fazer pelo dono): abrir Events Manager > o pixel > Visão geral, janela de 7 dias, e comparar os
disparos de `PageView`, `ViewContent`, `InitiateCheckout`, `CompleteRegistration` e `Purchase` com o cartão (os números
são DISPAROS recebidos, navegador + servidor, sem deduplicação: podem divergir do Events Manager e do nosso banco; o do
GA4 também). Cartão amarelo ("formato não reconhecido"): copiar do log do Railway a linha `[funil_meta] formato:` e fixar o
parser com a forma real. Cartão vermelho: a mensagem é fixa; o log tem `[funil_meta] HTTP <status> código <N>`, que diz o
código da Graph que caiu em `indisponivel` (ex.: código novo a mapear). `permissao_meta`/`nao_encontrado` persistentes:
conferir no Business Manager se o usuário do sistema tem o pixel atribuído e se o token foi gerado com ele.
Limites declarados do Meta, sem correção: `start_time` = agora - 7 dias + 1 h de folga (se a Graph ainda recusar a janela, o erro
seria 100 e apareceria como `nao_encontrado`; conferir na 1ª carga); a permissão exata do token
não foi confirmada; a Graph pode PAGINAR (`paging.next`) e só a 1ª página é lida (se houver mais, os números ficam
parciais, sem aviso); token inválido/expirado volta como 190 `auth` e o backoff de 600 s vive no BANCO (depois de corrigir a
env o cartão pode levar até 10 min para tentar de novo; `update funil_fontes_cache set falha_em = null where fonte = 'meta'`
libera a próxima tentativa). Um payload `reconhecido:false` (ou um 0 errado) já CACHEADO continua sendo servido até o TTL
(30 min) vencer, mesmo depois de um deploy que conserta o parser; para forçar a nova busca na hora, recue o relógio do
cache (o `update` mantém o último dado como `stale` se a nova busca falhar): `update funil_fontes_cache set buscado_em = buscado_em - interval '1 day', falha_em = null where fonte = 'meta'`
(um teste executa este SQL no Postgres de teste; NÃO use `delete`). O `timeout=8` do requests NÃO limita
goteio de cabeçalho: medido, um servidor que manda 1 byte a cada 5 s prende a thread por ~60 s, e o prazo total de 10 s só
vale dentro da leitura do corpo (`_ler`); mesmo limite do GA4, contido pelo backoff e pela marca `_EM_VOO` (no máximo 1
thread por processo) e sem watchdog; só `PageView` etc. por NOME exato (renomear um evento no pixel exige atualizar `EVENTOS`, `META_EVENTOS` e
o teste); `HTTP_PROXY` não-https mandaria o Bearer ao proxy em claro (em produção a URL é https).
**Avisos para as fontes com cota (Clarity, já feito, e as demais)**: `reservar` reserva 1 por `buscar`, não por
chamada HTTP (o `buscar` do Clarity faz EXATAMENTE 1 chamada externa); falha, timeout
ou `ocupada` depois da reserva queimam cota sem devolução, e com `backoff_s=60` a cota diária
pode acabar em ~8 min (o Clarity precisa de backoff de horas); TTL de 3 h × 8/dia é
exatamente a cota, sem folga; resposta de 1.000 linhas só cabe nos limites do `_validar` (20
níveis, 20.000 nós, 256 KB) se for agregada dentro do módulo; fonte com várias métricas não
tem sucesso parcial (ou tudo, ou erro); nos PRs B/D o token pode ir na query e a exceção do
`requests` traz a URL: a regra "logar só o tipo" cobre, manter.
Outros fatos registrados: `billing.reason` em `/admin/api/users` mudou de formato (agora
`type(exc).__name__`; nenhum leitor no repo); com o banco do cache fora do ar a fonte sem cota
(Stripe) é chamada ao vivo a cada GET, só com voo único; `cache.gravar_ok` falhando devolve `ok`
ao vivo e grava `falha_em`, então o 1º GET dá `ok` e o 2º `erro` por até o backoff (60 s no
Stripe: "flapping"); o logger `stripe` fixado em WARNING só vale depois da 1ª consulta da fonte
(import preguiçoso) e silencia o INFO do SDK no app inteiro (decisão a confirmar com o dono).
Limites declarados do painel (pessoa = conta mais antiga do `user_id`; `auth_accounts.user_id` não é único):
(a) `atraso` conta a pessoa se QUALQUER linha dela está em atraso, enquanto `estado_atual` usa a conta mais antiga, então a mesma pessoa pode ser "free" num bloco e "em atraso" no outro (atraso é sobre cobrança: qualquer linha vale);
(b) quem converte só na 2ª conta (cadastro novo do mesmo `user_id`) não aparece como conversão do coorte;
(c) custo da ativação: falta índice em `user_identities(user_id)` (EXISTS por linha); o Tester mediu 3,6s no pior caso com 100k cadastros nos últimos 30d e ~30s com 20k conclusões (só admin). Remédio: `create index on user_identities(user_id)` em issue separada, ou o TTL de 60s acima.

### Tarefas de fundo

Sobem no startup do app quando `RUN_BACKGROUND_TASKS != "0"`: rendimento de
investimento, Open Finance (abaixo), contas a pagar dos recorrentes, agendadores de
engajamento e de IA proativa, retenção de eventos de login, poda das tabelas de
refresh token / challenge de MFA / cadastro Google pendente
(`core/services/table_cleanup.py`), e a entrega do e-book da `/assinar`
(`_ebook_worker` → `core/services/ebook_entrega.entregar_pendentes`, a cada 5 min, a
1ª volta sem delay; inerte sem `STRIPE_SECRET_KEY` no ambiente), o e-mail trocado em
`/settings` levado ao cliente do Stripe (`_stripe_email_worker` →
`core/services/stripe_email_sync.sincronizar_pendentes`, mesma cadência e mesma guarda da
chave), e a foto diária do
patrimônio (`_patrimonio_foto` → `core/services/patrimonio_foto.py`, a cada hora, a partir
das 18h do fuso do app, uma por usuário com acesso por dia em `patrimonio_fotos`; atrás de
`PATRIMONIO_FOTO_ENABLED`, desligada por padrão e lida a cada volta — desligada, não
consulta nada), e os índices das FKs (`_tarefa_fk_indexes` →
`db/schema_repairs.ensure_fk_indexes_once`: uma vez por boot, 1 conexão do pool, sem
retry em processo; falha vira WARNING e o próximo boot repara), e o CHECK de valor
finito em toda coluna numérica (`_tarefa_finito_checks` →
`db/finito_checks.ensure_finito_checks_once`: mesmo molde, lock de sessão próprio; ver
"Banco de dados"). Ficam desligadas só onde
`RUN_BACKGROUND_TASKS=0` é forçado: `dashboard_dev.py` e
`scripts/whatsapp_qa_vault_harness.py`. O `tests/conftest.py` **não** força, então
teste que sobe o `app` herda o default (`1`) — `tests/test_table_cleanup.py` passa
`"1"` de propósito, para ver a tarefa subir.

O Open Finance tem **seis** trabalhos: expiração de trial
(`_open_finance_trial_expiry`), **aquecimento do catálogo** (`_aquece_catalogo`), **job de
saúde**, **retentativa**, **push de reconectar** (`run_reconnect_push`, ver "Push (app iOS)")
e refresh proativo — os cinco últimos no mesmo tick de `_open_finance_refresh`, nessa ordem (o
tick começa pelo aquecimento). O lifespan sobe também a tarefa `pluggy_catalogo`, que aquece o
catálogo uma vez no boot (só com `RUN_BACKGROUND_TASKS`). O 1º tick roda 10 min depois do boot
(`_PRIMEIRO_TIQUE_SEC`) só com aquecimento, saúde, retentativa e push (GET); o
PATCH periódico não roda no boot e entra do 2º tick em diante, a cada
`OF_REFRESH_INTERVAL_SEC` (6 h). O refresh
proativo depende de `OF_REFRESH_ENABLED` (off por padrão em produção); o job de saúde
roda MESMO com ele desligado e ESCREVE `status`/`status_reason`/`health` na conexão do
usuário. É de propósito: ele só faz `GET /items` (não consome cota de coleta) e é o que
tira do "Atualizado" a conexão cujo item sumiu da Pluggy — sem refresh e sem webhook,
nada mais faria essa verificação. A retentativa (Onda 5, PR-B2,
`frontend/routes/of_retentativa.py`) vem logo depois: relê a Pluggy, também só com GET,
para até `OF_RETRY_MAX_PER_TICK` conexões com dado atrás (default 20; só `0` ou
negativo desliga só ela, valor que não é inteiro cai no padrão), uma de cada vez, pelo mesmo caminho de sync do webhook. Quem entra e por quê:
`docs/open_finance_estados.md` §2.2. Kill switch da saúde e da retentativa:
`OF_HEALTH_CHECK_ENABLED=0` (default `1`). O aquecimento do catálogo NÃO tem kill switch (esse
flag não o desliga); sua falha só loga o tipo da exceção e nunca bloqueia saúde, retentativa
nem PATCH.
(Há ainda `_open_finance_proactive`, que retorna na hora sem `OF_PROACTIVE_ENABLED`.)

---

## Frontend

O detalhamento das armadilhas está no **§5 do `CLAUDE.md` da raiz** (é lá que ele
mora; não duplicar aqui). O essencial de domínio:

- **O site é HTML/CSS/JS à mão, com ilhas React delimitadas.** O
  `package.json` da raiz continua servindo só ao harness de testes de frontend —
  e continua **sem script
  `build`** de propósito: é a ausência dele que mantém a detecção automática do
  Railway apontando para o Python. O build de JS que existe é o de `webapp/`,
  projeto npm separado, com package.json e lockfile próprios.
- **Páginas públicas e área logada são as duas MPA.** Existe um POC de navegação
  client-side (`pb-nav.js`) **desligado por padrão**, restrito ao modo app e a duas
  rotas. Não trate a área logada como SPA: a migração para React que existe é por
  ILHA (abaixo, em "Decisões tomadas") e não alcança a navegação.
- **`dashboard.js` tem 10.587 linhas e 414 funções globais**, e `dashboard.html` tem
  139 handlers `onclick=` que dependem disso. Funcionalidade nova de dashboard deve
  nascer em arquivo próprio (§0.5 da raiz), com rota própria em `static_pages.py`.
- **Segurança de borda** (medida em produção): CSP com allowlist explícita
  (`cdnjs`, `jsdelivr`, `cdn.pluggy.ai`, `connect.facebook.net`,
  `static.cloudflareinsights.com`; o Stripe em `script-src` — `js.stripe.com`,
  `*.js.stripe.com`, `checkout.stripe.com` — e em `frame-src` — os mesmos mais
  `hooks.stripe.com` —, para o checkout embutido da `/assinar`), HSTS, `X-Frame-Options: DENY`,
  `Permissions-Policy` zerando câmera/microfone/geolocalização,
  `Referrer-Policy: strict-origin-when-cross-origin`, `X-Content-Type-Options: nosniff`.
  O `'unsafe-inline'` do `script-src` só sai quando os handlers inline saírem.
- **PWA**: `manifest.json` (`start_url: /login`) + `service-worker.js` (HTML e auth
  nunca cacheados; assets network-first; API e WS passam direto).
- **App iOS**: `mobile/` (Capacitor) aponta para `https://pigbankai.com/login` com
  `allowNavigation` do domínio inteiro — **qualquer rota do site abre dentro do app**.

---

## Banco de dados

**A fonte de verdade do schema é `db/schema.py::init_db()`** — DDL de ~62 tabelas.
Não mantenha uma segunda lista de tabelas ou colunas em documentação (§0.7 da raiz);
para saber o que existe:

```bash
grep -ohiE "create table if not exists ([a-z_]+)" db/*.py | awk '{print $NF}' | sort -u
```

Exceção: os índices de FK nascem em `db/schema_repairs.py::ensure_fk_indexes_once`,
fora do `init_db` (que segue a fonte do resto do DDL) — ver "Tarefas de fundo".

**Toda coluna `numeric`/`double precision`/`real` do schema `public` tem um CHECK
`finito_<coluna>` = `abs(col) <= utils_text.VALOR_MAX` (10**13, limite técnico do banco; NULL
passa).** O teto de negócio do valor digitado é `utils_text.TETO_ENTRADA` (R$ 1 bi, decisão do
dono 2026-10-15), aplicado só nas portas de entrada. Existe porque `nan <= 0` é False:
NaN/Infinity/1e308 passavam pelas guardas por porta (`valor_valido`/`valor_finito`, que ficam,
pela mensagem amigável).
Nasce em `db/finito_checks.py::ensure_finito_checks_once`, na tarefa de fundo (fora do
`init_db`, para dado sujo nunca travar o boot): pré-scan tudo-ou-nada (coluna suja =>
nada é adicionado, `logger.error` uma vez por boot só com `tabela.coluna`, para
entrar no `backend_errors_24h`), `ADD CONSTRAINT ... NOT VALID` com
`lock_timeout` de 1s (tabela travada vira `falhas` e o próximo boot tenta de novo),
depois `VALIDATE`. **Coluna numérica nova é coberta no boot seguinte**; só se exclui
com motivo escrito em `SEM_CHECK_FINITO` (hoje: `pix_unmatched_payments.value/net_value`).
A violação vira 400 (`valor_finito_violado_handler` no monólito, `erros.valor_finito_erro`
no `/api/v2` e `/api/app`, texto fixo no `handle_incoming`), reconhecida por
`violacao_finito(exc)`; as frases ao usuário são `MSG_VALOR_API` e `MSG_VALOR_WHATSAPP`
(`db/finito_checks.py`, também no chat de IA). Nunca devolver `str(exc)` do CheckViolation
(traz "Failing row contains (...)"). O que a redação do DETAIL (`core.pg_text.redigir_failing_row`)
COBRE: (0) 1ª linha: `psycopg.Error.__str__` redigido em `db/connection.py` pelo `diag.message_detail` de toda
violação de constraint (SQLSTATE 23xxx), em qualquer idioma de `lc_messages` (todo `print(exc)`,
f-string, `logger.*(..., exc)` e traceback, de qualquer conexão; o texto cru fica em `exc.args[0]`);
(1) o filtro `_sem_failing_row` em todo handler do root (`_configure_root_logger` o anexa também aos que já existiam)
(stderr e `_DashboardHandler`): texto e traceback, a pilha fica; (2) os dois gravadores de
`system_event_logs` (`log_system_event_sync` e `admin_dashboard.log_system_event`): `message` e
`details`. NÃO cobre (a 1ª linha pega o que vem de `str(exc)`): logger com `propagate=False` (uvicorn) ou `print` de texto cru, handler fora
do root, nem outro INSERT em `system_event_logs`, nem a resposta HTTP/WhatsApp (essa é a frase fixa).
Rota que embrulha `except Exception` em `HTTPException(f"...{exc}")`
tem de deixar o `CheckViolation` passar antes (`except psycopg.errors.CheckViolation: raise`).
Em teste: o `conftest` instala as constraints no banco da execução; teste que grava veneno
DE PROPÓSITO (o espelho legado sujo que a leitura tem de tolerar) usa a fixture
`sem_check_finito("tabela", "coluna")`, que derruba o CHECK e o recoloca NOT VALID no fim.
Escritor real do produto que esbarra no CHECK é bug da porta, não motivo para a fixture.

Os agrupamentos, para orientar a busca: **core** (`users`, `accounts`, `launches`) ·
**auth** (`auth_accounts`, `auth_identities`, `auth_sessions`, `auth_refresh_tokens`,
`auth_login_events`, `auth_rate_limits`, `user_identities`, `link_codes`,
`password_reset_tokens`, `email_verification_codes`, `pending_google_signups`) ·
**MFA** (`user_mfa`, `user_mfa_backup_codes`, `mfa_login_challenges`) ·
**crédito** (`credit_cards`, `credit_bills`, `credit_transactions`) ·
**planejamento** (`category_budgets`, `pockets`, `pocket_lots`, `financial_spaces`,
`recurring_*`, `bill_instances`) · **investimentos** (`investments`,
`investment_lots`, `market_rates`) · **Open Finance** (`open_finance_*`) ·
**IA** (`ai_messages`, `ai_pending_actions`, `ai_fallback_log`, `ai_proactive_cache`,
`agents`, `agent_events`) · **afiliados** (`affiliates`, `affiliate_*`) ·
**privacidade/auditoria** (`audit_events`, `pii_access_log`, `data_export_tokens`).

**Isolamento por usuário é regra dura**: toda query com `WHERE user_id = %s`.

---

## Integrações externas

| Serviço | Para quê | Onde |
|---|---|---|
| WhatsApp **Cloud API oficial** (`graph.facebook.com`) | canal principal | `adapters/whatsapp/` |
| Discord | fora do `launch.py` desde o PR 5a do dashboard v2: o código segue e não roda | `adapters/discord/`, `bot.py` |
| OpenAI | categorização, chat, agentes | `ai_router.py`, `core/services/ai_chat/` |
| Stripe | assinaturas | billing no monólito |
| Pluggy | Open Finance | `core/services/pluggy*.py` |
| Resend | e-mail transacional e de ciclo de vida | `core/services/email_service.py` |
| APNs | push do app iOS | `core/services/push_service.py` |
| Meta Pixel / CAPI | marketing (só páginas públicas) | `inject_tracking`, `core/services/meta_capi.py` |
| Google Analytics 4 | medição de funil (mesmas páginas do pixel) | `ga4_snippet`/`inject_tracking`; eventos ao lado de cada `fbq` |
| GA4 Measurement Protocol | receita server-side (compra, fim do trial, renovação) | `core/services/ga4_mp.py`, no webhook do Stripe |

O webhook do WhatsApp **verifica assinatura** (`X-Hub-Signature-256` com
`WA_APP_SECRET`) e se recusa a subir em `APP_ENV=prod` sem o segredo.

---

## Variáveis de ambiente

São ~130, lidas com `os.getenv` espalhado pelo código (só `APP_ENV` passa por
`config/env.py`). Para a lista real:

```bash
grep -rhoE 'os\.(getenv|environ(\.get)?)\(\s*"([A-Z][A-Z0-9_]{2,})"' --include="*.py" . \
  | grep -oE '"[A-Z][A-Z0-9_]{2,}"' | tr -d '"' | sort -u
```

Obrigatórias para o app subir: `DATABASE_URL` e `JWT_SECRET` — sem elas o import faz
`sys.exit(1)`. Para rodar a suíte, ver o §3 do `CLAUDE.md` da raiz.

Grupos: `DATABASE_URL`/`DB_POOL_*` · `JWT_SECRET`/`DASHBOARD_*` ·
`PII_ENCRYPTION_KEY`/`PII_HASH_PEPPER`/`PII_AUDIT_DISABLED` · `MFA_ENCRYPTION_KEY` ·
`WA_*` · `DISCORD_BOT_TOKEN` · `OPENAI_*`/`AI_*`/`AGENTS_*` · `STRIPE_*`/`PLANS_V2_ENABLED` ·
`PLUGGY_*`/`OF_*` · `RESEND_API_KEY`/`EMAIL_FROM*` · `APNS_*` · `ADMIN_DASHBOARD_*` ·
`META_PIXEL_ID` · `RUN_BACKGROUND_TASKS`/`SKIP_INIT_DB`/`ENABLE_DEV_ENDPOINTS` ·
`ACCOUNT_DELETION_JOB_LIMIT`/`TABLE_CLEANUP_INTERVAL_HOURS` (os dois kill switches
de job que apaga linha; `TABLE_CLEANUP_INTERVAL_HOURS=0` desliga a poda).

---

## Convenções de código

- Query sempre com `WHERE user_id = %s`. Nunca vazar dado entre usuários.
- Falha de e-mail é silenciosa (log, não quebra o fluxo principal).
- Endpoints sensíveis com rate limit (`@limiter.limit()`).
- Modelo Pydantic para todo body de POST.
- `ensure_user()` antes de operação de banco para usuário novo.
- Import dentro da função nos endpoints, quando necessário para evitar import circular.
- PII cifrada (`core/crypto.py`) e acesso registrado (`pii_access_log`, `core/audit.py`).
- Nunca passe segredo em `details` de auditoria (senha, TOTP, código de backup).

---

## Decisões tomadas (não sugerir alternativa sem pedido explícito)

- **Railway** para deploy, **Cloudflare** na borda.
- **psycopg 3**, não psycopg2.
- **WhatsApp Cloud API oficial** — a migração planejada já aconteceu.
- **Resend** para e-mail — SMTP/Gmail foi abandonado.
- **Sem Google Sheets.**
- **Sem Redis** até hoje: não há fila nem cache externo no repositório.
- **A migração para React COMEÇOU, e a decisão está tomada.** O padrão é **ilha**,
  não SPA: Vite + React 19 em `webapp/` (o único projeto de build do frontend),
  bundles IIFE de escopo delimitado, saída de nome FIXO e sem hash em `frontend/`,
  **artefato commitado** —
  porque não há `StaticFiles` mount e cada asset precisa de rota escrita à mão.
  As ilhas de preços e dos chats convivem com os scripts clássicos. A convenção
  é IIFE, mount síncrono com `flushSync` e propriedade exclusiva do trecho
  renderizado pelo React: controladores publicam estado, sem alterar seus nós.
  Preços continuam vindo do markup, evitando duplicar regras comerciais.
  A ilha de preços usa CSS comum e os tokens da página. A integração dos chats,
  solicitada explicitamente pelo usuário, usa TypeScript e Tailwind 3.4 com
  prefixo, sem Preflight e com processamento de CSS exclusivo dessa ilha;
  [a decisão](adr/0001-interface-compartilhada-dos-chats.md) registra o alcance.
  O piloto público de `/como-funciona` também está decidido, mas ainda não
  implementado: terá mount exclusivo, fallback no markup legado e entrará no
  mesmo `webapp/`, build, gate e deploy; ver o
  [ADR 0002](adr/0002-piloto-como-funciona-como-ilha-react.md).
  Ao alterar o build, preserve o alvo Safari 14 nos artefatos JS e CSS e
  `emptyOutDir: false`: o destino é o diretório do site.
  A ilha do v2 (`/painel`) busca dados com **TanStack Query v5**. O alvo safari14 só
  rebaixa sintaxe (os `this.#x` viram WeakMap), não faz polyfill de API: por isso
  `tests/frontend/dashboard_v2_safari14.test.mjs` varre o `dashboard-app.js` commitado
  atrás das APIs que o Safari 14 não tem (`.at(`, `structuredClone`, `Object.hasOwn(`,
  `WeakRef`, `static{`, `this.#` e outras) e monta o `/painel` no Chromium com elas
  apagadas.
  O gate do CI recompila `webapp/` e exige artefatos idênticos aos commitados.
  Dependências novas exigem rebuild e inclusão dos artefatos afetados no commit.
  **O que isto NÃO autoriza:** transformar a área logada em SPA, adicionar
  framework em página nova por gosto, criar outro projeto/pipeline de build ou
  deploy, ou pôr script `build` na raiz.
