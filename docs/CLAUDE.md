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
ai_router.py              — chamada à OpenAI (modelo em OPENAI_MODEL, default gpt-4o-mini)
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
Taxas percentuais mantêm precisão livre, inclusive valores muito pequenos.
Sem persistência e sem tela ainda; lógica em `core/services/decision_simulator.py`.

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
- `GET /api/v2/me` devolve `{"plan_tier": "free"|"essencial"|"plus"|"pro"}`, sem PII.
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
  consulta 5 do /app e `compute_kpis` das Análises. `compute_evolution` é cópia em consulta
  única (um GROUP BY por mês); `tests/test_resumo_mes_regra.py` compara as duas por mês.
  `credit_bills.user_id` é NOT NULL (backfill pelo dono do cartão, #772): a fatura só entra
  se for do usuário (`b.user_id = %s`), em cada perna do cartão.
  **Divergência conhecida:** relatório diário e semanal, ferramentas da IA de período
  livre e projeção de fechamento (`get_summary_by_period`) e o Repórter
  (`piggy_agents._month_stats`) seguem só em `launches`, sem o cartão. Limites mantidos de
  propósito: o mês corta `criado_em` pela data ingênua (fuso da sessão do Postgres), a
  conciliação pendente conta em dobro (sai com motivo), estorno não abate.
  `scripts/comparar_resumo_mes.py` compara antigo × novo por usuário e mês, só lendo.
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
  `cartao`, `categoria` (a chave), `tipo` (`entrada`|`saida`), `q`, `cursor`, `limite`
  (padrão 50, acima de 100 corta, abaixo de 1 é 422). Resposta `{mes, itens, proximo, motivos}`.
  **O mês é o de `TOTAIS_SQL`**, pelas mesmas pernas (`db/resumo_mes.MES_LANCAMENTOS_SQL` e
  `MES_CARTAO_SQL`): a soma dos itens não internos é o Entrou/Saiu do Resumo; o cartão
  entra pelo mês da fatura com a data da COMPRA; o interno entra com `interno: true`, fora
  de qualquer soma; tipos que não são despesa/receita (caixinha, aporte) ficam fora, como
  na soma. Com `q` (até 6 palavras de 2+ letras, `unaccent`; `db/analytics.termos_busca`/
  `clausula_busca`, as mesmas do `list_history`) a lista varre a janela inteira do plano em
  vez do mês. Janela do plano cortando = `inicio_do_historico`. Página por KEYSET (cursor
  opaco: base64 de `[dia, instante, tabela, id]`, adulterado = 422), sem contagem total.
  Item: `id` (`l<n>` lançamento, `c<n>` compra no cartão: as tabelas compartilham o espaço de
  ids), `data` (`launch_day`) e `hora` (ou `null`), `tipo`, `interno`, `valor` (texto),
  `moeda` (a da conta do banco ligada), `descricao`, `mensagem` (a nota do usuário),
  `categoria` (a chave `cat_norm_sql`, a mesma de `GET /api/v2/categorias`), `origem`,
  `fundido` + `instituicao`, `conta_id`, `cartao_id`, `parcela {n, total}`, `fatura`
  (`AAAA-MM`), `pode` e `motivos` (`conciliacao_pendente`, `transacao_pendente` = o banco
  diz PENDING, `outra_moeda`, `moeda_presumida`). **`origem`**: `carteira` (manual com
  `delta_conta` ≠ 0 e com a marca `launches.origem`), `banco` (sombra do Open Finance),
  `cartao` (compra do cartão do Open Finance) e `registro_antigo` (o resto: manual sem a
  marca, manual com delta 0, OFX, compra manual no cartão). **`pode`** é uma regra só
  (`db/lancamentos.PODE_SQL`): antigo = `[]` (P2: só leitura no v2); banco e cartão do Open
  Finance = categoria e descrição (P5); carteira marcada = tudo, menos data e valor se fundida
  ou em par pendente com o banco (P3), só descrição e apagar se ligada ao dinheiro em espécie
  (Q41), só categoria e data se paga conta ou fatura, e só categoria se além disso fundida
  (sem apagar no v2: apagar o pagamento de
  conta devolve o dinheiro e a conta segue paga; o de fatura do cartão manual cai no mesmo ramo,
  `bill_id`, e sai junto; o `/app` e o WhatsApp seguem apagando). `launches.origem` (NULL =
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
  regras delas (data de gravação no cartão, sem o interno).
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
- `GET /api/v2/categorias` (`api/v2/categorias.py`): `{categorias: [{chave, nome}]}`, o
  catálogo do usuário pelo `CAT_META_SQL` (chave = `cat_norm_sql(name)`, nome = a grafia que
  vence entre gêmeas), semeado como o `/categories` do /app.
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
  indisponível (só em `em_andamento` e `indisponivel`); senão `null`. O POST recebe `{acao: "visto"|"feito"|"dispensar"|"reabrir", passo?}`
  e devolve o mesmo `Guia`; `feito` sem `passo` ou `passo` fora do roteiro = 422 no envelope;
  `feito` de passo com `disponivel: false` = 409 `passo_indisponivel` no envelope, sem gravar
  (sem dados o cliente mostra a orientação e segue para 2 e 3); nas outras ações `passo` é ignorado. Exige o CSRF do pai. Carimbos só gravam uma vez
  (`oferecido_em` no `visto` e no `reabrir`, `dispensado_em`, `concluido_em`, e o 1º de cada
  passo em `feitos`); `reabrir` zera `dispensado_em`. A oferta conta o convite **e** a Ajuda:
  quem abre pela Ajuda sem nunca ter visto o convite fica `em_andamento` e não o recebe depois,
  e entra em `oferecidos` na medição abaixo (conclusão/oferecidos mistura as duas portas; quem
  abre pela Ajuda antes de ter dado põe a espera pela sincronização dentro de
  `mediana_oferta_ate_1o_valor`, então ela não mede só o guia).
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
  `cabe_nas_premissas` e estimativa variável seguem `false`. Horizonte até 90 pontos
  futuros e até 3 marcos; grupos/ocorrências completos, sem truncamento. Não há teto
  global de bytes comprovado/adicionado: esses limites dimensionais não limitam
  fontes, nomes ou motivos, nem concluem o requisito de um teto global de payload.
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
  só em `fonte_unica.MENSAGENS`. `/app` = 400 com `detail` em texto; WhatsApp = o texto
  (`handle_incoming` traduz o que sobe, os handlers de cartão e de aporte devolvem); IA = o
  texto, e o `validate` de `create_investment`/`investment_deposit` recusa antes de pedir
  confirmação. A checagem da chave que falha **libera** (fail-open: é trava de produto num
  beta, não segurança). O usuário é sempre o da sessão/remetente, nunca o corpo. Escritor
  novo nessas tabelas ou dos importadores de arquivo reprova em
  `tests/test_fonte_unica_q36.py` até ser classificado (trava ou motivo de ficar livre).
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
mensagem de número já ligado a conta sem credencial; no auto-vínculo, remetente que já
tem dados financeiros segue na própria conta, sem vínculo nem mescla
(`remetente_com_dados`); o vazamento da mescla por telefone digitado está na #711.
A exceção do bot são os botões de opt-out de `_WA_INTERACTIVE_ISENTOS` (relatórios diário,
semanal e mensal, e atualizações): quem não pode usar tem de conseguir parar de receber
mensagem, então eles funcionam no número já ligado, no `precisa_senha` (desligam a
preferência da conta sem credencial) e no `remetente_com_dados` (a do remetente e a da
conta que digitou o número), e nada além da preferência é gravado.
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
(saudação, ajuda, e-mails, relatórios, "sim"/"não", desfazer, cartão,
recorrência/conta a pagar, vários lançamentos numa frase), com pendência viva e
com o que a IA devolve sem resposta. `WA_IA_PRIMEIRO_USER_IDS` (ids por vírgula):
ausente ou `""` exato = todos; com ids, só os listados; qualquer outro valor que
não dê id válido (`,`, só espaços, `abc`) = ninguém, com warning no log. As duas são lidas a cada mensagem, mas
trocar env no Railway reinicia o serviço (~1 min sem bot). O prazo do turno da IA
(`IA_PRIMEIRO_PRAZO_TURNO`, 15 s) também limita cada chamada à OpenAI (timeout por
requisição = o menor entre 8 s e o que sobra), e resposta com tool calls que chega depois
dele não roda nada: a mensagem volta ao roteador (ou, se já houve escrita no turno,
`ERROR_MSG`). Com a flag, o
`add_launch` da IA pede "sim" quando QUALQUER parâmetro que a gravação usa não está
apoiado no texto (`lancamento_com_certeza`, um critério por parâmetro do schema): valor
(um número só, igual); data (o dia do mesmo parser da gravação, no fuso do app, igual
ao do texto; texto sem data → IA sem data ou hoje); tipo (verbo de receita); categoria
(regra local confiante; hashtag só se a regra local da nota não a contradiz);
`forma_pagamento` (se veio, `forma_pagamento.detectar` do texto dá a mesma); alvo e
nota (se vieram, palavras inteiras do texto, sem acento nem caixa). O resumo da
confirmação (`_add_launch_summary`; só é usado por esta confirmação — WhatsApp com a
flag —, porque o `add_launch` não pede confirmação em nenhum outro caminho) mostra tipo,
valor, alvo, nota, categoria, o dia que vai ser gravado e a forma, se veio, e o "sim"
grava exatamente isso: com hashtag no texto, a pendência guarda a categoria dela e a
marca `_categoria_explicita` (do código; o runner descarta toda chave `_` vinda do
modelo), e a gravação a passa como `explicit`, que o cross-check com a regra local não
troca (a categoria vai canonizada, como a gravação a deixa). Rodada do modelo com uma
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
`item_id: null` conta como ausente (token de banco novo). O campo opcional `attempt_id`
exige UUID canônico em minúsculas e `app_scheme` permitido; o servidor monta a URI
`<scheme>://open-finance-volta/<attempt_id>`. Sem esse campo, pedidos legados válidos
mantêm a URI anterior. Após os gates de sessão e acesso, JSON malformado, campos
repetidos, corpo acima de 4096 bytes ou lento retornam 400 antes de qualquer chamada
à Pluggy; corpo vazio continua permitido. Contrato completo em
[`open-finance-ios-backend.md`](open-finance-ios-backend.md).
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
usuário por `merchant_key` (vale para todos os itens da chave), e `assinatura_antes` a
marca `assinatura` que o `ignorar` substituiu (linhas ignoradas antes da coluna nascem `false`).
`open_finance_connections.recurring_fetched_at` e `recurring_seed_silent` controlam o
silêncio da 1ª busca do Detetive numa conexão que já existia: as chaves dela — a foto
guardada em `recurring_seed_descricoes`, não a atual — viram lápide por `record_agent_event(silencioso=True)`, que grava o evento já com
`stale_at` (não aparece no feed nem vai por e-mail).

Boa parte do comportamento é regida por flags `OF_*` (beta por e-mail/user_id, limite
de bancos no free, refresh proativo). Antes de mexer, leia as flags — o
comportamento em produção pode não ser o do seu ambiente.

### IA

`ai_router.py` chama a OpenAI (`OPENAI_MODEL`, default `gpt-4o-mini`) como fallback da
categorização determinística. Há rate limiting próprio (`core/ai_rate_limiter.py`),
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
(`returning`), sem reler: outra janela que re-arme no meio não é cancelada. No
`_CONFIRMA` do `add_launch` (WhatsApp com a flag), o runner relê a linha logo depois de
armar: se outra janela (o `/ai/chat` aberto junto) a sobrescreveu, responde
`_OUTRO_PEDIDO` ("tem outro pedido seu esperando confirmação") em vez de mostrar um
resumo cujo "sim" executaria a da outra. Limite: resta a janela entre essa releitura e
a entrega da mensagem; fechar de vez exige pendência por canal ou sem sobrescrita, fora
deste PR. No ramo `requires_confirmation` a pergunta é texto do modelo na rodada
seguinte e não há essa releitura. Código em
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

Categorização tem uma armadilha própria: **categoria e regra de categoria são tabelas
diferentes** (`user_categories` × `user_category_rules`) e a regra ganha da categoria
na inferência.

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
consulta nada). Ficam desligadas só onde
`RUN_BACKGROUND_TASKS=0` é forçado: `dashboard_dev.py` e
`scripts/whatsapp_qa_vault_harness.py`. O `tests/conftest.py` **não** força, então
teste que sobe o `app` herda o default (`1`) — `tests/test_table_cleanup.py` passa
`"1"` de propósito, para ver a tarefa subir.

O Open Finance tem **quatro** trabalhos: expiração de trial
(`_open_finance_trial_expiry`), **job de saúde**, **retentativa** e refresh proativo —
os três últimos no mesmo tick de `_open_finance_refresh`, nessa ordem. O 1º tick
roda 10 min depois do boot (`_PRIMEIRO_TIQUE_SEC`) só com saúde e retentativa (GET); o
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
`docs/open_finance_estados.md` §2.2. Kill switch dos dois: `OF_HEALTH_CHECK_ENABLED=0`
(default `1`).
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
