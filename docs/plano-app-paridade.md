# Paridade entre o app nativo e o dashboard web (`/painel`)

Rascunho de plano, aberto em 2026-10-16 sobre a `main` em `b7c8232d`. **Só plano: nenhum código do app, do painel web
ou do servidor muda neste PR.** É o plano do lado do **app** (`app/`); o lado do **web** está na seção "Paridade com o
app" de `docs/plano-dashboard-v2.md` (§6), que resume a matriz e aponta para cá. A matriz completa mora só aqui (§0.7:
uma fonte de verdade).

**Faixa deste PR: Direto (docs).** O conteúdo guia trabalho futuro de faixa Leve e Completo (§4 diz qual é qual).

## 1. A regra do dono

Decisão do dono, 2026-10-16: o app nativo e o `/painel` web **andam juntos, nos dois sentidos**, com as mesmas
características e funções, adaptadas à plataforma. Não é cópia: no app valem "visual, mínimo de texto, ideal ZERO"
(2026-10-14), "quanto mais nativo, melhor" (2026-09-29) e a identidade de `docs/identidade-visual-painel.pdf`.
O que se faz num lado e não no outro vira dívida **declarada**, com plano, e não esquecimento (§7).

A task paralela do card "Seu dia" no app (`docs/plano-seu-dia-app.md`, PR #1050, ainda não mergeado; branch
`docs/plano-seu-dia-app`) **não é duplicada aqui**: a matriz a conta como a lacuna A1 e a ordem (§6) a põe em primeiro.
Quem a executa segue aquele plano.

## 2. Como foi medido (e como remedir)

Nada aqui é de memória; as contagens que um comando responde **não estão escritas** (CLAUDE.md §2). Os comandos, rodados
em 2026-10-16 na `main` `b7c8232d`, **remedir antes de reusar**:

| O que | Comando |
|---|---|
| Rotas de `/api/v2` e do app, e quem as consome (web e app) | script do Anexo A (heurística por regex, não parser; comentários descartados) |
| Rotas montadas em cada namespace | `sed -n 1,60p api/v2/app.py api/nativo/app.py` (`criar_app` e `dependency_overrides`) |
| Rotas protegidas por teste, por introspecção | `tests/test_rotas_senha_obrigatoria.py` (`test_toda_rota_tem_linha_e_nenhuma_linha_e_orfa`) |
| De onde o app lê cada recurso | `grep -n "function rota" -A3 app/src/features/painel/provider.tsx` e `app/src/services/painel.ts` |
| Rotas legadas que o app ainda usa | `grep -rnoE "[\`\"](/goals\|/cards\|/installments\|/analytics\|/insights)[^\`\"]*" app/src --include='*.ts' --include='*.tsx'` |
| Blocos do web que ainda são de exemplo | `grep -rnE "<Frame " webapp/src/dashboard/widgets webapp/src/dashboard/parts webapp/src/dashboard/pages.tsx \| grep -v "\breal\b"` (sem `real`, o `Frame` grava `data-dado="exemplo"`, `parts/Frame.tsx:24`) |
| Chat do web real ou de exemplo | `grep -n "CHAT_REAL" webapp/src/dashboard/lib/v2.ts` |
| Telas do app | `find app/app -type f \| sort` e `grep -n TITULOS app/src/features/painel/catalogo.ts` |
| PRs abertos que mexem nos dois lados | `gh pr list --state open` (em 2026-10-16: #1033, #1050, #1051) |

Achado de método: o web **não** tem só "dados reais ou não" por tela. Cada bloco declara sozinho (`real`), e a lista de
blocos sem `real` é a dívida do web (§3, linhas 3, 9, 15 e 18 a 22).

## 3. Matriz de paridade

Legenda do veredito: **paridade** · **app atrás** · **web atrás** · **plataforma** (específico, com o porquê) ·
**ambos atrás** (existe só no painel antigo `/app` ou no site). "a verificar" e "a conferir" = não verificado a fundo (§9).

### 3.1 Resumo e blocos

| # | Função / tela | Web `/painel` | App | Diferença que importa | Veredito |
|---|---|---|---|---|---|
| 1 | Home em blocos, perfis e ordem | `Board` (`parts/Board.tsx`): blocos, Organizar, perfil via `GET/PUT /perfil` | Home nativa (`app/(app)/index.tsx`, `catalogo.ts`): blocos, seis perfis, perfil via `GET/PUT /api/app/perfil`, ordem no SecureStore por usuário e perfil | Catálogos quase iguais; o web tirou "Metas e caixinhas" do Resumo (decisão do dono, plano §8), o app ainda tem o bloco `metas`. A ordem salva no web fica no `localStorage` por **perfil** (`lib/profiles.js`), sem o `user_id`, e a limpeza central de fim de sessão (`auth-refresh.js`) a apaga a cada logout; o app guarda por uid e perfil (SecureStore) e mantém. Diferença de **persistência** (a de isolamento só aparece se a limpeza não rodar) O web filtra por plano os blocos de plano alto (`WIDGET_FEATURE` e `FEATURE_TIER` em `lib/profiles.js`: `hero` e `piggy` pedem Plus, `simulador` pede Pro) nos presets e no layout salvo; o app oferece todo `TITULOS` em "Adicionar blocos" (`cabecalho.tsx`) e o bloco bloqueado cai no estado "negado" do 403 do servidor, com gate local só no simulador | paridade no recurso; **app atrás (A19)** no filtro por plano; **web atrás (W10)** na persistência (catálogo comparado em 2026-10-16: os cinco presets de perfil e o `padrao` têm os mesmos ids nos dois lados; o app tem a mais só `metas`, que o web tirou do Resumo) |
| 2 | Contas (saldo por conta) | `GET /contas` (`widgets/Contas.tsx`, real) | `GET /api/app/contas` (`contasResumo.tsx`) | — | paridade |
| 3 | Resumo do mês: Entrou e Saiu | `GET /resumo-do-mes` (`Stats.tsx`, real): o valor do mês e, ao lado, o do mês anterior inteiro, **sem porcentagem (decisão N4)**; **Fatura é de exemplo** | `GET /api/app/resumo-do-mes` (`resumoMes.tsx`): compara o mesmo ponto do mês com o anterior, com variação e curvas | Os dois comparam; a diferença (porcentagem, mesmo ponto do mês, curva) é do app e a ausência de porcentagem no web é decisão do dono. A comparação de períodos mais rica no web é o #1033 (aberto) | paridade (a riqueza da comparação é diferença deliberada; reabrir só se o dono pedir) |
| 4 | Saldo do mês e **Guardado líquido** | Não há. "Guardado no mês" saiu do Resumo por ser dado inventado (plano §8, Etapa 4 PR B) | Topo da aba Gastos (`numerosMes.tsx`): Entrou, Saiu, Saldo, Guardado líquido, de `GET /api/app/mes-detalhes` | O número do app é real (`guardado` de `mes-detalhes`); `mes-detalhes` só existe no namespace do app (`api/nativo/`) | web atrás |
| 5 | Modo privacidade (esconder valores) | Não há no `/painel`; existe no painel antigo (`frontend/dashboard.js`, `toggleHideBalance`) | `oculto` em todo o app (`Valor`, `Anel`, cards) | Barato e só cliente | web atrás |
| 52 | Seletor de mês e a janela de histórico do plano | `MESES` em `lib/v2.ts`: **só os 6 últimos meses**, fixos; `/api/v2/me` não traz `history_earliest_date` (grep em `api/v2/me.py`) | `mesesDoHistorico(mesAtual(), usuario.history_earliest_date, …)` (`cabecalho.tsx`): todos os meses até o início do histórico do plano (mês atual Free, 90 dias Essencial, 365 Plus, 730 Pro; `core/services/plan_service.py`) | Quem é Plus ou Pro abre no app resumo, gastos e extrato de meses que o web não deixa escolher. No mês do corte o servidor já corta o resumo e manda `inicio_do_historico` (`db/resumo_mes.py`), e o app e o web leem esse selo | **web atrás (W12)** |

### 3.2 Gastos

| # | Função / tela | Web | App | Diferença que importa | Veredito |
|---|---|---|---|---|---|
| 6 | Para onde vai (categorias) | `GET /gastos`: estabelecimentos por categoria, série de 3 meses, detalhe (`GET /gastos/categoria`) | `Categorias` lê `mes-detalhes`; **não consome `/gastos`** | Sem estabelecimentos nem série, e `Categorias` abre o extrato filtrado em vez de um detalhe; o servidor já serve `/api/app/gastos` (`tests/test_rotas_senha_obrigatoria.py:148`). O plano do Seu dia **não cobre** este bloco (só calendário e folha do dia) | **app atrás (A15)** |
| 7 | Calendário "Dia a dia" | `GastosCalendario` sobre `/gastos`: grade real, quantidade e categorias por dia | `Calendario` sobre `mes-detalhes`: `{dia, entrou, saiu}`; toque leva ao extrato | Sem quantidade, categoria ou maiores | **app atrás (A1)** |
| 8 | Card "Seu dia" (#1029) | Setas de dia, total, pílula contra a média, fatos, barra, compras | Não há | Plano pronto em `docs/plano-seu-dia-app.md` (#1050): folha ao tocar no dia, sem frase | **app atrás (A1)** |
| 9 | Simulador | **De exemplo** (`widgets/Simulator.tsx`, `pages.tsx` `Simulate`), mas com mais controles: **novo gasto fixo**, economia em 12 meses, saldo em 90 dias, a data da meta **e uma página `/simulador` com a trajetória de 90 dias (sem mudar nada × com a simulação, faixa provável) e as metas afetadas, mais cenários prontos** (`pages.tsx` `Simulate`, `SimChart`, `Goals`) | Real: só corte por categoria e a soma da economia (`gastos.tsx`, `Simulador`), com gate de plano | O app calcula no cliente a partir de dados que o web também lê (`/gastos`); os controles a mais do web são decisão do W3: ficam (e entram no app como **A17**) ou saem | **web atrás (W3)**; **app atrás (A17)** se os controles ficarem |
| 10 | Assinaturas (serviços, outras, ignoradas, marcar) | `GET /assinaturas`, `POST /assinaturas/marca` | `GET /api/app/assinaturas`, `POST /api/app/assinaturas/marca` (`complementos.tsx`, `services/painel.ts`) | O web: "É assinatura", "Ignorar", "Não é assinatura" (quando há marca), "Voltar a mostrar" (**restaura a marca guardada**) e "Desfazer" no aviso, com aviso de cobranças do mesmo comerciante. O app: "Ignorar", "É assinatura" e "Reconsiderar", que **sempre manda `nenhuma`** e por isso apaga a marca manual que o usuário tinha posto antes de ignorar (o app já recebe `marcada`, `schemas/painel.ts`); sem "Não é assinatura", sem "Desfazer" (`complementos.tsx`, `Subscriptions.tsx`). **Detalhes que o schema do app já recebe e a tela não mostra**: desde quando existe, valor anterior e data do reajuste ("subiu de R$ X em dd/mm", `Subscriptions.tsx:104-110`) | **app atrás (A20)** |

### 3.3 Previsão

| # | Função / tela | Web | App | Diferença que importa | Veredito |
|---|---|---|---|---|---|
| 11 | Saldo previsto, horizonte 30/60/90, marcos, pior dia | `GET /previsao` (`Previsao.tsx`, real) | `Previsao` (`futuro.tsx`): chips de dias, curva, pior dia | O web mostra também o **saldo de partida**, o **saldo de hoje com as premissas** (`ancora`), o aviso de **bancos conectados fora do cálculo** e, no menor saldo, **desde quando e quais causas** (`pior_dia.desde`, `causas`), além do estado da previsão; o app só desenha marcos, curva e o menor saldo com a data, embora `previsaoSchema` já receba esses campos (`futuro.tsx`, `Previsao.tsx`) | **app atrás (A6)** |
| 12 | "Como esta previsão foi feita" (premissas, cobertura, base) | `Previsao.tsx` mostra premissas e cobertura | O schema do app lê `premissas`/`cobertura`, mas **nenhuma tela as usa** | Só leitura de campos que já chegam | **app atrás (A6)** |
| 13 | Compromissos (grupos, qualidade) | `Compromissos.tsx`: agrupados como a API manda, com qualidade | `Compromissos` (`futuro.tsx`): lista plana (junta os `ocorrencias` de todos os grupos) | O app mostra a qualidade por ocorrência (`valor estimado`, `data presumida`, `a conferir`, `fora do cálculo`: `futuro.tsx`); falta só o **agrupamento** | **app atrás (A6, só grupos)** |
| 14 | **Receitas do extrato** (confirmar, negar, desfazer) | `ReceitasDoBanco.tsx`, `POST /previsao/receita` (#1034) | **Não há**: o schema do app **nem tem** o campo `receitas_do_banco` | Escrita que altera a previsão; é a "2b (app)" já prevista no P7 | **app atrás (A5)** |
| 15 | Compromissos no **Resumo** do web | `compromissos: Bills` é de exemplo (a versão real só existe na página `/previsao`) | `compromissos` real (`Previsao`) | Inconsistência interna do web, não de paridade | web atrás |

### 3.4 Metas, patrimônio e cartões

| # | Função / tela | Web | App | Diferença que importa | Veredito |
|---|---|---|---|---|---|
| 16 | Metas: ler | `GET /metas`: fonte, ritmo, `chega_em`, reserva de emergência (`Metas.tsx`) | `TelaMetas` usa **`/goals/{uid}/status` (legado)**; só o card do chat lê o formato v2 (E1, via bloco do servidor, não via `/api/app/metas`) | O app mostra a meta por uma regra, o web/IA/WhatsApp por outra (#950, #954, C1, C2) | **app atrás (A2)** |
| 17 | Metas: criar, editar, apagar, converter reserva | `POST /metas`, `/metas/editar`, `/metas/apagar` (`MetaForm.tsx`) | Não há | `MetaFonte` do web também lê `/investido` e `/contas` para escolher a fonte | **app atrás (A3)** |
| 18 | Patrimônio (total, partes e histórico, na janela do plano) | **De exemplo** (`NetWorth.tsx`, `Wealth.tsx`; lê `lib/api.ts`, sem rede) | Real: `GET /api/app/patrimonio` (`patrimonio.tsx`); a janela do histórico é a do plano (`history_earliest_date`: mês corrente no Free, 90 dias no Essencial, 365 no Plus, 730 no Pro, `core/services/plan_limits.py`) e o widget desenha o que a rota devolver | A rota só existe no namespace do app; a foto diária já grava (#723) | **web atrás (W1)** |
| 19 | Rendimento (taxa contratada) | **De exemplo** (`Yield.tsx`, "Rendimento × CDI") | Real: `GET /api/app/rendimento` (`Rendimento`, taxa contratada) | Mesma situação do 18 | **web atrás (W1)** |
| 20 | Fatura do cartão, Parcelas futuras | **De exemplo** (`Invoice.tsx`, `Installments.tsx`) | Reais, mas pelo **legado**: `/cards/{uid}/summary`, `/installments/{uid}/list` | Nenhum contrato v2; o app depende de rota legada com `user_id` no caminho | **web atrás (W4)**; app a migrar do legado |
| 21 | Renda mês a mês | **De exemplo** (`Income.tsx`) | Real, pelo legado: `/analytics/{uid}/evolution?months=6` | Idem | **web atrás (W4)** |
| 22 | "Piggy notou" | **De exemplo** (`Piggy.tsx`) | Real, pelo legado: `/insights/{uid}/current` (`Notou`) | Idem | **web atrás (W4)** |
| 23 | `/investido` (total investido nos bancos) | `GET /investido`: resposta do assunto `investido` do chat demo **e** fonte da meta nova (`MetaFonte.tsx`) | Não chama; o total chega pelo `patrimonio` (parte `investimentos_banco`) e pela IA | Sem consumidor próprio no app; só vira necessário com a escrita de metas (A3) | **entra com A3**, não é item sozinho |
| 24 | Comparação de períodos (#1033, aberto) | `GET /api/v2/comparacao` + widget (Plus+) | `/api/app/comparacao` montado, sem cliente | Nasceu no padrão certo: montada nos dois namespaces (`api/v2/app.py`) | app atrás (**A8**, quando mergear) |

### 3.5 Extrato

| # | Função / tela | Web | App | Diferença que importa | Veredito |
|---|---|---|---|---|---|
| 25 | Ler: busca (`q`), origem, tipo, categoria, **conta**, dia, cursor | `GET /lancamentos` (`parts/Lancamentos.tsx`): seletor de **Conta** (de `GET /contas`) e de Origem com as quatro (inclui `registro_antigo`) | `GET /api/app/lancamentos` (`extrato.tsx`): busca, origem (sem `registro_antigo`, de propósito: o comentário do arquivo só diz que "o filtro não o oferece", o motivo não foi achado), tipo, categoria; `dia` filtra só no aparelho, nas páginas já carregadas (num mês com mais de 50 lançamentos o dia antigo pode abrir "Nenhum resultado nesta página"): **o app não manda `dia` nem `conta`**; a busca com termo curto (só palavras de 1 letra) o servidor ignora (`termos_busca` em `db/analytics.py`, mínimo 2 letras), então devolve o mês sem filtro, e o app ainda diz "Buscando em todo o histórico do seu plano"; o web bloqueia a busca curta e avisa (`Lancamentos.tsx`, `curta`); o plano do Seu dia (§5, linha "Ver extrato do dia") já prevê mandar `dia` na consulta | O servidor já aceita `conta` e `cartao` (`api/v2/lancamentos.py`); o web só expõe `conta` e o app nenhum dos dois. Os dados de `/contas` já chegam ao app (`contasResumo`) | **app atrás**: `dia` no servidor entra com **A1**, filtro por conta é a **A14**; `registro_antigo` e `cartao` a decidir |
| 26 | `guardado=1` (aportes e saques como linhas marcadas, #983) | O `/painel` manda | O app **não manda, de propósito**: app instalado não atualiza com o backend (plano §8) | Um build novo pode mandar | **app atrás (A7)** |
| 27 | Escrever: lançar, editar, apagar na Carteira (Q36) | `POST /lancamentos/carteira`, `/editar`, `/apagar`, regra `pode` | O Extrato só lê | **Sem chave de idempotência** (`api/v2/lancamentos.py:107`): num celular com rede ruim, repetir o POST grava duas vezes | **app atrás (A4)** |

### 3.6 Chat do Piggy

| # | Função / tela | Web | App | Diferença que importa | Veredito |
|---|---|---|---|---|---|
| 28 | Conversa | **De exemplo**: `CHAT_REAL = false`, respostas de `lib/topics.tsx`; só o assunto `investido` é real | **Real**: `POST /ai/chat/stream`, `GET /ai/messages`, `DELETE /ai/pending`, **copiar resposta**, feedback 👍👎, parar, chips (#943 a #956) | Etapa 7 do web "continua inteira" (plano §8) | **web atrás (W2)** |
| 29 | Cards no chat (`show_card`) | Blocos de exemplo | `periodo`, `caixinhas`, `metas` (só leitura, E1) e os do `TITULOS`; o card de gastos E2 (#1031) já está na `main` | Quando o web ligar o chat real, o `show_card` já vem do servidor | acompanha o 28 |

### 3.7 Guia e ajuda

| # | Função / tela | Web | App | Diferença que importa | Veredito |
|---|---|---|---|---|---|
| 30 | Guia do Piggy e dicas de primeiro uso | `GET/POST /guia`, `POST /guia/dica` (`Guia.tsx`, `Dica.tsx`) | Não há | O guia do web aponta para elementos da tela (`data-guia`); o equivalente nativo é outro desenho | **app atrás (A10)**, decidido em P3 |
| 31 | Ajuda e atalhos (Cmd-K, Menu) | `Command.tsx`, `Ajuda.tsx`, `MenuTelas.tsx` | A barra de abas e a Folha | Atalho de teclado não tem par em toque | plataforma |

### 3.8 Conta, segurança e sistema

| # | Função / tela | Web | App | Diferença que importa | Veredito |
|---|---|---|---|---|---|
| 32 | Conectar, **reconectar** e desconectar banco (Open Finance) | Fora do shell v2: `/settings?view=open-finance` (`frontend/settings.html`, `open-finance-connect.js`). **Conectar** e limites por plano (`refreshLimits`) existem; **reconectar não**: o widget sempre pede `connect-token` com corpo vazio (`open-finance-connect.js:563`) e o servidor exige `item_id` para um banco já conectado (`routes/open_finance.py:1811`), senão a Pluggy recusa "already exists". **Desconectar** só todos de uma vez (`disconnectAll`, `DELETE /open-finance/{user_id}`); **Atualizar agora** (`POST .../refresh`) | `conectar-banco`, `conexoes` ("Reconectar" manda `provider_item_id`, "Conferir de novo"), `open-finance-volta` (OAuth no navegador, ADR 0005). **Desconecta um banco por vez** (`DELETE .../connections/{id}`), sem "desconectar todos"; só relê o sincronizado, sem `refresh` | Superfícies diferentes pela plataforma, e cada lado tem o que o outro não tem | **web atrás (W11)** em reconectar e desconectar um; **app atrás (A13)** no Atualizar agora e **(A16)** no desconectar todos |
| 33 | MFA e segurança | `settings.html` | `seguranca`, `mfa-ativar`, `mfa-desativar`, `mfa-novos-codigos` | — | paridade |
| 34 | Definir senha (conta Google/Apple sem senha) | `settings.html` ("Definir senha", #976) | Segurança do app (`seguranca.tsx`): `EsqueciSenha` em modo `emailFixo`, rotulado "Definir senha", pede o link de reset | Mesmo efeito por caminhos próprios | paridade |
| 35 | **Excluir conta** e **exportar meus dados** | `settings.html` | Não há | Destrutivo e LGPD. **Provável exigência da App Store** para app com cadastro (diretriz 5.1.1(v), a confirmar antes da submissão; hoje só TestFlight) | **app atrás (A9)** |
| 36 | Plano e cobrança | Stripe/Asaas no site | Compras Apple são "entrega separada" (`app/README.md`) | Regra da loja | plataforma |
| 37 | Trava biométrica, tampa de privacidade | n/a | `features/bloqueio`, `expo-local-authentication` | Só existe em aparelho | plataforma |
| 38 | Tempo real | SSE `GET /eventos` (`lib/eventos.ts`) | Foco e foreground; `com_eventos=False` no namespace do app (`api/nativo/app.py`) | Deliberado e documentado (`docs/api-app-painel.md`) | plataforma |
| 39 | Push | Não há | **Não há**: `expo-notifications` não está no `app/package.json` | O orquestrador listou "push" como algo que o app tem; não tem | nenhum dos dois |
| 40 | Categorias: criar, renomear, emoji, cor, arquivar, apagar, fundir | Só no painel antigo; o v2 só lê (`GET /categorias`) | Só lê (`/api/app/categorias`) | Plano §6 "Depois": o web faz primeiro | **ambos atrás** |
| 41 | Sessões ativas: listar e encerrar; atividade recente | `settings.html` (`GET /settings/{user_id}/sessions`, `DELETE .../sessions/{jti}` e `.../sessions`) | Não há: `seguranca.tsx` diz que sessões "entram como seção irmã" | Segurança de conta; as rotas legadas de `/settings` levam `user_id` no caminho e **aceitar Bearer do app não foi verificado** | **app atrás (A12)** |
| 42 | Preferências de notificação (relatórios, avisos) | `GET/PATCH /settings/{user_id}/notifications` | Não há | Hoje o app não tem push (linha 39); a preferência existe para WhatsApp e e-mail | **app atrás (A12)** |
| 43 | Dados de contato: e-mail, nome de exibição e telefone/WhatsApp | `PATCH /settings/{user_id}/security/contact` (`SecurityContactPayload`: `phone`, `display_name`, e-mail) | Não há: o app coleta nome e telefone só no cadastro e mostra o nome sem edição | Troca de e-mail alcança o Stripe (`stripe_email_pendente`); é auth | **app atrás (A12)** |
| 44 | **Recomeçar do zero** (apagar os dados financeiros e recomeçar) | `settings.html`, `POST /settings/reset` (#217) | Não há | Destrutivo; já emite o aviso SSE para o web; a confirmação do app tem de ser nativa | **app atrás (A12)** |
| 45 | Funções que só existem no painel antigo (`/app`): orçamentos por categoria, contas a pagar recorrentes, investimentos e caixinhas manuais, cartões (CRUD), agentes, afiliados | `/app` (`dashboard.js`), rotas `/budgets`, `/recurring-bills`, `/investments`, `/pockets`, `/cards`, `/agents`; no v2 a página Ferramentas mostra algumas como "Em breve" (`pages.tsx` `TOOLS`) | Não levantado: o app só lê cartões, parcelas e caixinhas (pelo legado) | Fora do escopo `/painel` × app deste levantamento; cada uma vira linha quando entrar no v2 (plano §6, "as demais telas") | **ambos atrás do v2; a levantar** |
| 46 | Trocar a senha de quem já tem senha (link de reset pela conta logada) | `POST /settings/{user_id}/password-reset` (`settings.html`) | Só `EsqueciSenha` para conta **sem** senha (`seguranca.tsx:155-166`); quem tem senha só vê o MFA e precisa sair para pedir o reset | Segurança; o reset revoga as outras sessões (`/auth/reset-password`) | **app atrás (A12)** |
| 47 | Contas sincronizadas e últimas transações do banco | Cards em `settings.html` (`view=open-finance`) | Não há tela de configuração com isso; os saldos e as transações aparecem no Patrimônio e no Extrato do painel nativo | Mesma informação em outra superfície; a duplicata em Configurações é do web | paridade (equivalente nas telas do painel) |
| 48 | Vincular caixinha do banco a uma meta | `GET /open-finance/{user_id}/caixinhas` e `POST .../caixinhas/bind` (`_require_caixinha_access`: **Essencial ou acima**, `require_min_tier`; planos menores recebem 404); card "Caixinhas & metas" em `settings.html` (o comentário do cliente que diz "beta, allowlist" está velho) | Não há | Disponível hoje para todo plano pago no web; o app mostra a caixinha só como parte do total do patrimônio (`partes.caixinhas`, `schemas/painel.ts`), sem o vínculo | **app atrás (A18)** |
| 49 | Política de Privacidade | Seção "Privacidade e documentos" em Configurações (`/privacy`, `settings.html`) | Só no cadastro (`features/auth/Termos.tsx`); Configurações tem Suporte e Sair, sem o link | Quem já tem conta não alcança a política pelo app | **app atrás (A13)** |
| 50 | Atalhos de conta no shell: **Configurações/Conta**, **Suporte** e **Sair** | **Não há no `/painel`**: `frontend/painel.html` só monta o bundle e `webapp/src/dashboard` não tem link para `/suporte`, `/settings` (só "Conectar banco" → `?view=open-finance`) nem logout (`git grep` 2026-10-16). O painel antigo (`dashboard.html`) tem os três | `configuracoes.tsx`: Bancos conectados, Segurança, Suporte (`/suporte` no navegador) e Sair | Sair toca a sessão: a limpeza de logout é central (`auth-refresh`), o `/painel` não pode inventar outro caminho | **web atrás (W8)** |
| 51 | Tema claro e escuro | **Só escuro**: `color-scheme: dark` em `webapp/src/dashboard/styles/base.css`; "tema claro" está em "Depois" no plano do web (§6) | Segue o sistema (`app/app/_layout.tsx`, paletas `claro` e `escuro`) | Dívida já conhecida do web, só faltava na matriz | **web atrás (W9)** |
| 53 | Diagnóstico "Teste Open Finance" | Não há | `configuracoes.tsx` e `teste-pluggy.tsx`: contagem de conexões, horário de sincronização, tentativa pendente e ações de recuperação; o comentário do arquivo diz "mantido até validar as telas definitivas em banco real/iPhone" | Ferramenta provisória de validação, não função de produto | app-only **provisório**: sai quando `conexoes` e `open-finance-volta` forem validados em aparelho; não é dívida do web |
| 54 | Entrar, criar conta, esqueci a senha | Páginas do site (`/login`, `/cadastro`, redefinição) | `(auth)/boas-vindas`, `entrar`, `criar-conta`, `esqueci-senha`; compras e termos no cadastro | Fluxos próprios por plataforma, **não comparados campo a campo** nesta matriz | paridade por plataforma (a conferir) |
| 55 | Vitrine do design system (`app/app/_ds`) | n/a | Rotas internas de componentes, declaradas por último na pilha (`_layout.tsx`) | Fora do produto; não medi se alguma é alcançável em build de produção | fora do escopo |

### 3.9 O sentido contrário: o que o app tem e o web não

Resumo, para não perder: linhas 1 (ordem da Home que persiste), 52 (meses do seletor), 32 (reconectar e desconectar um banco), 4 (Saldo e Guardado líquido), 5 (privacidade), 9 (Simulador real), 18 a 22
(Patrimônio, Rendimento, Fatura, Parcelas, Renda, "Piggy notou" reais), 28 (chat real) 50 (Suporte, Conta e Sair no shell) e 51 (tema claro). Dentro dos "específicos da
plataforma" o app ainda tem biometria, tampa de privacidade e Compras Apple (linhas 36 e 37).
Isso é **mais** do que a regra do dono previa ao dizer "o app está atrás": **o web também está atrás**, e por dívida
grande (§6).

## 4. Lacunas do app e o que cada uma pede

Legenda: servidor **0** = nenhuma mudança (a rota já está no namespace do app); esforço **P** = 1 PR, **M** = 2 a 3,
**G** = 4 ou mais, ou servidor + cliente + build. App instalado não atualiza com o deploy do backend: servidor só
**acrescenta** campo, nunca muda o que o app já lê.

| ID | Lacuna | Servidor | Cliente (app) | Faixa | Esforço | Dependências |
|---|---|---|---|---|---|---|
| A1 | `/gastos` + calendário real + folha "Seu dia" | 0 (só teste de contrato, `docs/plano-seu-dia-app.md` §6) | schema, `gastosDia.ts`, folha, pílula, barra | Leve | M (3 PRs do plano) | #1031 já está na `main`; build pelo Xcode |
| A2 | Metas v2: leitura, no lugar de `/goals/{uid}/status` | 0 (`/api/app/metas` já montado) | schema `metas`, `metasTextos.ts` e `MetaV2` já existem (E1), trocar a fonte da `TelaMetas` | Leve | P | Nenhuma; reduz a divergência entre canais |
| A3 | Metas v2: criar, editar, apagar, converter reserva | 0 (limite declarado: sem idempotência no POST, duas metas iguais; a reserva tem 409) | formulário nativo, escolha de fonte (**precisa de `/investido` e `/contas`**) | Leve (tela), Completo se tocar servidor | M | A2; o web `MetaForm` é a referência; #1051 (IA cria metas) usa a mesma regra do servidor, sem conflito |
| A4 | Escrita de lançamentos na Carteira | **Completo**: aceitar `Idempotency-Key` (coluna com índice único parcial, exportação, reset e exclusão, como o comentário de `lancamentos.py:107`), **só acréscimo** | formulário nativo, regra `pode`, estados de falha de rede | **Completo** | G | `docs/plano-so-open-finance.md` (decisão de 2026-10-07): a Carteira é a única escrita manual; confirmar com o dono que o app a quer |
| A5 | Receitas do extrato na Previsão (P7 2b) | 0 (`POST /api/app/previsao/receita` montado e protegido) | acrescentar `receitas_do_banco` ao schema (campo novo, tolerante), tela de confirmar, negar e desfazer | **Completo** (altera a previsão) | M | #1028 e #1034 mergeados; build |
| A6 | Previsão: premissas, cobertura ("Como esta previsão foi feita"), **saldo de partida e saldo de hoje com as premissas, aviso de bancos fora do cálculo, causas do menor saldo (`pior_dia.desde` e `causas`) e o estado da previsão**, e **agrupamento** dos compromissos (a qualidade por ocorrência o app já mostra) | 0 | telas só de leitura sobre campos que já chegam | Leve | P | A5 (mesma tela) |
| A7 | `guardado=1` no Extrato, com o rótulo "Guardado" | 0 | mandar o parâmetro, rótulo e sinal conforme #983 | Leve | P | Um build novo (apps antigos continuam sem) |
| A8 | Comparação de períodos | 0 (#1033 monta `/api/app/comparacao`) | card de comparação, Plus+ | Leve | P | Esperar o #1033 mergear |
| A9 | Excluir conta e exportar dados | **a verificar**: se `DELETE /auth/account` e `account/export` aceitam Bearer; senão acrescentar | telas nativas | **Completo** (auth, sessão, destrutivo) | G | Sem data de submissão (§10, P4) |
| A12 | Sessões ativas e atividade recente, preferências de notificação, editar e-mail, nome e telefone, trocar senha, recomeçar do zero (linhas 41 a 44 e 46) | **a verificar**: as rotas `/settings/{user_id}/...` aceitam Bearer? Se não, acrescentar equivalentes sem `user_id` no caminho | telas nativas na Segurança e em Configurações, mínimo de texto | **Completo** (auth e sessão) | G | A9 (mesma área); o PR #1035 (login com MFA e exclusão agendada) pode mexer no mesmo código |
| A13 | **Atualizar agora** do Open Finance (linha 32) e link da **Política de Privacidade** em Configurações (linha 49) | 0 para o link; **a verificar** para o refresh: `POST /open-finance/{user_id}/refresh` é rota legada que o app já usa para ler (`/open-finance/{uid}`), então tende a aceitar Bearer, e o servidor limita a frequência por item (`claim_manual_refresh`, `OF_MANUAL_REFRESH_COOLDOWN_SEC`, 120 s por padrão: dentro do cooldown o item volta `rate_limited` e só se relê o snapshot), então a tela precisa mostrar esse estado; a espera padrão (`OF_REFRESH_WAIT_SEC`, 18 s) é configurável por ambiente e o valor em produção não foi lido. O `?wait=` **não limita a requisição inteira**: `refresh_and_sync_pluggy_user` (`core/services/pluggy_sync.py`) faz o PATCH de cada item antes de contar a espera e roda o `sync_pluggy_user` inteiro depois dela, e o `?wait=` tem teto de 18 s (`routes/open_finance.py`). `comLimite()` aborta o pedido do app em 15 s (`TEMPO_LIMITE_AUTH_MS`, `api/client.ts`). Então o app usa o **mesmo prazo do web** (`OF_REFRESH_PRAZO_MS = 60000`, `settings.html`) com um limite próprio de 60 s, com botão e indicador (não o gesto de puxar, que tem watchdog curto), e trata o estouro do prazo como "ainda atualizando": relê `/open-finance/{uid}` em vez de falha | uma chamada nova em `services/openFinance.ts` e **um botão novo** "Atualizar agora" em `conexoes.tsx`; "Conferir de novo" e o puxar-para-atualizar **continuam** como leitura do snapshot (GET), sem trocar de função; `ListRow` "Política de Privacidade" abrindo `/privacy` (mesmo padrão do Suporte) | Leve (**Completo** se o refresh exigir mudar o servidor) | P | Nenhuma; refresh toca fornecedor externo, então confirmar o limite antes |
| A14 | **Extrato: filtro por conta e validação da busca curta** (linha 25): só buscar com ao menos uma palavra de 2 letras e avisar, sem a frase de "todo o histórico" enquanto não há busca válida; o `dia` no servidor não é daqui, vem com a A1 | 0 (`/api/app/lancamentos?conta=` e `/api/app/contas` já montados) | um `Chip`/seletor de conta em `extrato.tsx`, lista vinda do que `contasResumo` já lê; decidir `registro_antigo` e `cartao` | Leve | P | Nenhuma; a mesma lista de contas serve à escolha de fonte da meta (A3) |
| A15 | **Para onde vai nativo** (linha 6): estabelecimentos por categoria, série de 3 meses e detalhe da categoria (`GET /gastos/categoria`) | 0 (`/gastos` e `/gastos/categoria` são do mesmo router `api/v2/gastos.py`, que `criar_app` monta nos dois namespaces; falta só conferir por teste de contrato, como o plano do Seu dia §6 faz para `/gastos`) | tela de detalhe e série no lugar do extrato filtrado de `Categorias`; reaproveita o schema `/gastos` do A1 | Leve | M | A1 (schema e `gastosDia.ts`); o plano do Seu dia não inclui este bloco |
| A16 | **Desconectar todos os bancos** (linha 32) | 0 (`DELETE /open-finance/{user_id}` é a rota legada que o app já usa para os outros `/open-finance/{uid}`; confirmar Bearer) | uma ação com confirmação nativa em `conexoes.tsx`, mesmo padrão do "Desconectar" por banco | **Completo** (Open Finance, destrutivo) | P | A13 (mesma tela) |
| A17 | **Simulador com os controles do web** (linha 9): novo gasto fixo, economia em 12 meses, saldo em 90 dias, meta que recebe a economia, **trajetória de 90 dias com e sem a simulação, metas afetadas e cenários prontos** | 0 (`/api/app/previsao` e `/api/app/metas` montados) | estender `Simulador` em `gastos.tsx` e uma tela com a trajetória (gráfico nativo), mínimo de texto | Leve | G | **Condicionada** à decisão do W3 (os controles ficam no web?) e à A2 (a meta vem da fonte única) |
| A18 | **Vincular caixinha do banco a uma meta** (linha 48) | 0 (rotas legadas `/open-finance/{uid}/caixinhas` e `/caixinhas/bind`, do mesmo router que o app já usa em `/open-finance/{uid}`; **confirmar Bearer**). **Atenção aos ids:** a lista `metas` dessa rota vem de `db.list_pockets` e o `pocket_id` do `bind` é de **pocket legado**, não da tabela `metas` que a A2 lê; o app usa **só** a lista devolvida por essa rota e nunca manda id de meta v2 (poderia falhar ou atingir outro pocket de mesmo número) | lista de caixinhas com a meta (pocket) escolhida e desvincular; respeitar `pocket_auto` (vínculo fixo criado pelo sync, a rota recusa soltar); sem texto a mais | **Completo** (Open Finance e dinheiro de meta) | M | **Nenhuma da A2**; confirmar com o dono se o app deve mostrar caixinha por caixinha (hoje só o total) e se as metas v2 devem um dia ligar a caixinhas (hoje só pockets ligam) |
| A19 | **Catálogo de blocos filtrado por plano** (linha 1): não oferecer em "Adicionar blocos", nem nos presets, os blocos que o plano não libera | **Recomendado: campo aditivo** com a lista de blocos liberados em `/api/app/me` e `/api/v2/me` (modelo `Me` em `api/v2/me.py` e `planoSchema` no app), derivado dos mesmos `plan_gate_ok` das rotas, com teste de contrato. **Alternativa sem servidor**: tabela local no app a partir do plano canônico, que já está em `p.dados.plano` (`/api/app/me`), um `Resultado` que só vale com `fase === "pronto"`, em `p.dados.plano.dado.plan_tier`; **não usar `usuario.plan`** do `/auth`, que guarda o vocabulário armazenado (`pro` é o Plus público e `pro_max` o Pro público, `_STORED_PLAN_TO_TIER` em `core/services/plan_service.py`). A tabela local repete o problema do espelho do web (abaixo) | filtrar `TITULOS` por uma tabela de plano mínimo por bloco **no momento da leitura**, como o web (`readLayout(profile, preset, known, plan)` filtra sem reescrever o salvo, então o bloco volta se o plano subir). Enquanto o plano está `carregando` ou em `erro`, **não filtrar nem apagar nada** (o 403 do servidor segue sendo o estado "negado"); ao chegar o `plan_tier`, refiltrar o layout ativo e os presets, que hoje nascem antes dessa resposta; o espelho do web (`WIDGET_FEATURE` e `FEATURE_TIER` em `lib/profiles.js`, comparado por `tests/frontend/dashboard_v2_profiles.test.mjs` com `FEATURE_MIN_TIER_V2`) **está incompleto e o teste não vê**, porque só compara o que está no mapa: o mapa do web só tem `hero`, `piggy` e `simulador`, e o levantamento das rotas (`grep -rn "require_plan_feature\|plan_gate_ok" api frontend/routes`, 2026-10-16) acha mais quatro blocos com portão: `assinaturas` (`subscriptions`, Plus, `api/v2/assinaturas.py`), `compromissos` (`forecast`, Plus, `/previsao`), `renda` (`financial_comparison`, Plus, `/analytics/{uid}/evolution`) e, dentro de `metas`, o conteúdo por `investments` e `subscriptions` (`api/v2/metas.py`); o `hero` ainda tem `cashflow` (Pro) para os detalhes. Contas, resumo, categorias, calendário, fatura, parcelas, patrimônio e rendimento não têm portão de plano nas rotas lidas; no web `assinaturas` e `compromissos` mostram o aviso "Ver planos" dentro do bloco, e **o app não pode ter esse link** (compras só pela loja, linha 36), então lá o bloco deve **sumir**. Por isso **recomendo** que o servidor informe a lista de blocos liberados (campo aditivo em `/api/app/me` e `/api/v2/me`, derivado dos mesmos `plan_gate_ok` que as rotas usam) em vez de um segundo espelho no app | Leve (o servidor só acrescenta um campo de leitura) | M | W13 (o mesmo campo serve ao web); decidir o caminho (campo ou tabela local) no plano do A19 |
| A20 | **Assinaturas** (linha 10): "Reconsiderar" restaura a marca guardada (`assinatura` se `marcada`, senão `nenhuma`), "Não é assinatura" nas marcadas, "Desfazer", mostrar `desde`, `valor_anterior` e `reajuste_em` (aumento ou queda de preço) e **avisar quando a marca vale para o comerciante inteiro**: a chave agrupa cobranças do mesmo comerciante, o servidor muda o grupo todo e o web diz "… e mais N cobranças do mesmo comerciante" (`Subscriptions.tsx:175-185`); sem isso o usuário acha que mudou só a linha tocada | 0 (`POST /api/app/assinaturas/marca` e o campo `marcada` já chegam ao app) | `complementos.tsx`: escolher o `status` pelo `marcada`, ação e desfazer nativos; defeito de dado do usuário hoje (apaga a marca ao reconsiderar) | Leve | P | Nenhuma |
| A10 | Guia e dicas, adaptados ao nativo (P3: sim) | **Muda o contrato, só por acréscimo**: o roteiro `PASSOS` (`api/v2/guia.py`) é do web (âncoras e ações do DOM; o passo `piggy.pergunta` é `exemplo` e a fala diz que o chat real "chega em breve", o que é falso no app, onde o chat é real). Decidir no plano do A10: (a) roteiro por plataforma no servidor, sem mudar o que o web lê, ou (b) mapeamento nativo no cliente que ignora `ancora`/`acao` e troca a fala. Não reaproveitar a fala do web como está. **O progresso é um só por usuário**: `guia_painel` (`db/guia.py`) é lida por `where user_id = %s`, com `oferecido_em`, `dispensado_em`, `concluido_em`, `feitos` e `dicas` compartilhados, e `concluido` vence `dispensado` vence `em_andamento` (`api/v2/guia.py`). Quem já concluiu ou dispensou o guia no web **não receberia** o do app, e passos nativos novos não bastam porque `concluido_em` continua mandando. O plano do A10 precisa escolher: progresso **por plataforma** (tabela ou coluna aditiva, com a regra de leitura do app olhando só o seu) ou uma política explícita de que o app não herda o do web | desenho nativo (coach marks ou cartões de primeiro uso), mínimo de texto | **Completo** (migração de schema para o progresso por plataforma, mesmo aditiva) + cliente | M | Depois dos itens de dinheiro |
| A11 | Categorias: gerenciar | Depende do web | idem | Leve/Completo | G | Web primeiro (plano §6) |

O que o app **deve migrar do legado** (`/goals`, `/cards`, `/installments`, `/analytics`, `/insights`) para contratos v2
acompanha o item W4 do web (`docs/plano-dashboard-v2.md`): como o web precisa do mesmo dado, **a rota nova nasce em
`/api/v2` e é montada nos dois namespaces**, e o app troca a fonte depois (campo novo, nunca mudando o que o build
antigo lê).

## 5. Específico da plataforma (decidido: o guia não é específico; aberto: modo privacidade)

Não entram na matriz como dívida: SSE × foco e foreground (38), atalhos de teclado (31), biometria e tampa (37),
Compras Apple × cobrança do site (36), a superfície de conexão de banco, navegador × widget (32; reconectar, desconectar e atualizar **não** são plataforma, são dívida das duas pontas, linha 32). O dono decidiu (§10, P3) que o
guia **não** é específico: existe no app, adaptado ao nativo (A10). Segue aberta a pergunta do modo privacidade (5), cuja
recomendação é **não** ser específico (o painel antigo já o tem).

## 6. Ordem recomendada e por quê

A sugestão inicial do orquestrador era: Metas v2 e escrita de lançamentos; depois Para onde vai v2 e o card do dia;
depois `/investido`; por último guia e dicas. **A evidência pede outra ordem:**

| # | Item | Por que nessa posição |
|---|---|---|
| 1 | **A1 "Seu dia" e calendário real**, depois **A15 Para onde vai** (mesmo schema `/gastos`) | É o único com plano fechado e decisões do dono tomadas (2026-10-16), servidor pronto, só Leve, e a dependência (#1031) já está na `main`. Foi o gatilho da regra. |
| 2 | **A2 Metas v2 (leitura)** | É barato (o formato e os textos já estão no app por causa do E1) e **corrige um risco que existe hoje**: o app mostra a meta por uma regra e o web, a IA e o WhatsApp por outra. Vem antes da escrita. |
| 3 | **A5 Receitas do extrato + A6** | Já era trabalho prometido (P7 2b); fecha uma função de dinheiro que o web já oferece. |
| 4 | **A3 Metas (escrita)** com `/investido` e `/contas` | Depende de A2. `/investido` **não é item sozinho**: o único consumidor real é a escolha da fonte da meta. |
| 5 | **A4 Escrita na Carteira**, com o PR de servidor (idempotência) **antes** | Contesto "escrita primeiro": é o item mais caro (Completo, G), o servidor não protege contra repetição de POST e o produto já reduziu a escrita manual a uma só (Carteira). Vale começar o PR de servidor cedo, em paralelo, porque ele não depende de build. |
| 6 | A7, A8, A13, A14, A16, A20; **A19** e **A18** à parte | Pequenos: A8 espera o #1033; A13 é uma chamada com prazo próprio e um link; A14 é um filtro. A19 (M) leva um campo aditivo no servidor e anda junto do W13 do web. A18 (Completo, M) não depende da A2 e pode subir quando o dono confirmar se o app mostra caixinha por caixinha. |
| 7 | **A9 e A12** | Sem data de App Store (§10, P4): fica aqui. Sobe no dia em que o dono marcar uma submissão. |
| 8 | A10, A11, A17 | A10 já decidido (P3, sim); A11 depende do web; A17 espera a decisão do W3 e a A2. |

Em paralelo, no web, a ordem de W1 a W13 mora em um só lugar: "Ordem recomendada do lado do web", §6 de `docs/plano-dashboard-v2.md`. Cada W tem lá a sua definição, faixa e dependência (inclusive o que exige servidor e o que é Completo); não é repetida aqui para não ficar para trás.

## 7. Regra operacional daqui para a frente

Aprovada pelo dono em 2026-10-16 (§10, P1). **Este PR não edita o `CLAUDE.md` raiz**: a edição e o template vão em PR próprio.

**7.1 No PR.** Todo PR de feature de qualquer dos dois lados traz no corpo uma linha:

```text
Par no outro lado: já existe | plano/issue #N | específico da plataforma porque <razão>
```

Não existe `.github/pull_request_template.md` hoje; criá-lo é a forma barata de a linha aparecer sem ninguém lembrar.

**7.2 No `CLAUDE.md` raiz (texto aprovado em P1, §4 "PR, Codex e threads"; entra em PR próprio).**

> **Paridade app × `/painel`.** Toda feature de um lado declara no corpo do PR o par no outro:
> `Par no outro lado: já existe | plano/issue #N | específico da plataforma porque …`. Feature
> que muda dado ou rota nasce em `api/v2` e é montada nos dois namespaces; rota só do app
> (`api/nativo`) exige a razão. O servidor só **acrescenta** campo: app instalado não atualiza
> com o deploy. A matriz vive em `docs/plano-app-paridade.md`; o PR que muda uma linha dela a
> atualiza no mesmo PR.

**7.3 Um teste para a dívida não ficar invisível (proposta, não implementada).** O repositório já tem o molde:
`tests/test_rotas_senha_obrigatoria.py` enumera as rotas dos sub-apps `/api/v2` e `/api/app` por introspecção e falha se
uma rota nova não tem linha. A mesma ideia vale para paridade:

- um manifesto versionado (`docs/paridade-rotas.json`, uma entrada por rota) com `web: sim | nao | nao_se_aplica` e
  `app: sim | nao | nao_se_aplica`, **e um `porque` obrigatório** quando for `nao` ou `nao_se_aplica`;
- o teste enumera as rotas, exige uma entrada por rota (rota nova sem declaração reprova) e confere por grep, como o
  Anexo A, que `sim` tem consumidor (declarar `sim` sem consumidor reprova; `nao` com consumidor também);
- **sem contagem escrita**: o manifesto guarda o veredito de cada rota, e quantas estão em dívida é uma consulta
  (`jq '[.[] | select(.app=="nao")] | length' docs/paridade-rotas.json`), nunca um número no texto (CLAUDE.md §2);
- o teste de frontend e o do app **não** entram: o escopo é só rota, que é o que dá para enumerar sem heurística frágil.
  Tela sem rota nova (um bloco de exemplo, por exemplo) segue pela matriz deste documento e pelo grep do §2.

Faixa do trabalho do teste: **Leve** (CI e ferramenta). Limite declarado: a heurística de consumidor é regex; um
consumidor montado por string dinâmica passa despercebido, e é por isso que `sim` e `nao` são declaração humana
conferida, não inferência.

## 8. Dependências e build

- **Build do app:** pelo Xcode, com `PATH=/usr/bin` primeiro no `export` (o app não tem projeto EAS). O `buildNumber: "15"`
  em `app/app.config.ts` é velho de propósito (o Xcode escolhe o próximo livre no App Store Connect). O último
  número registrado é o 30 (2026-10-12, segundo `docs/plano-seu-dia-app.md` §7); o próximo quem confirma é o dono, que vê o App Store Connect.
- **Quase todo item de cliente (A1 a A8 e A13 a A20) acumula em builds**: A1, A2 e A7 cabem no mesmo build; A4 e A9 pedem build
  próprio por terem teste de aparelho.
- **PRs abertos relacionados:** #1050 (plano do Seu dia), #1033 (comparação; condiciona A8), #1051 (IA cria metas;
  mesma regra do servidor de A3), #1035 (login com MFA e exclusão agendada; pode condicionar A9, a conferir).

## 9. O que foi medido e o que não foi

Medido, por leitura estática e grep, em 2026-10-16: rotas dos dois sub-apps, consumidores no web e no app (Anexo A),
`TITULOS` e perfis do app, `Frame` com e sem `real`, `CHAT_REAL`, schema da previsão (campos que o app lê e usa),
`MetaFonte` e `investidoQuery`, filtros do `/lancamentos`, rotas legadas que o app usa, o `app.config.ts`, os PRs abertos.

**Não levantado (escopo):** as funções que só existem no painel antigo `/app` (linha 45) e o conteúdo de cada card de `frontend/settings.html`, que foi varrido card a card na 4ª rodada do Codex (títulos dos cards e chamadas `fetch`, comando `grep -n 'card-title\|data-title\|legal-title\|fetch(' frontend/settings.html`, 2026-10-16): cada card tem linha na matriz (32 a 35, 41 a 44, 46 a 49). Os campos internos de cada card e as regras de plano de cada chamada não foram lidos um a um. Na 5ª rodada varri também os filtros do Extrato (linha 25) e os atalhos de conta do shell (linha 50, `git grep` em `frontend/painel.html` e `webapp/src/dashboard`), e conferi pela lista de ações as linhas 2, 10, 11 e 33, os "paridade" que sobravam com "—" na diferença; a comparação foi por ações disponíveis, não por pixel nem por regra de plano.

**Não medido:** nenhum jest, nenhum teste web, nenhuma chamada ao servidor nem a produção, nada aberto em aparelho
ou simulador. Varredura de rotas do app: `find app/app -name "*.tsx"` (2026-10-16) tem cada tela mapeada a uma linha (extrato, gastos, metas, piggy, resumo, index: §3.1 a §3.6; conexões, autorizando, volta e conectar: 32; MFA e segurança: 33 e 34; configurações: 50; teste-pluggy: 53; auth: 54; `_ds`: 55). O que segue sem verificar (as linhas dizem "a verificar" ou "a conferir"): se as rotas de excluir conta e exportar dados aceitam Bearer; as regras de plano e os limites do Open Finance **no servidor** (web e app têm cada um a sua checagem de limite no cliente, comparada só por existir, não por valor). Deixaram de ser dúvida por achado do Codex: onde fica a ordem salva da Home no web (W10) e a reconexão de banco (W11). a regra da App Store (5.1.1(v)) é conhecimento da
política, não verificada nesta sessão.

## 10. Decisões do dono (2026-10-16) e perguntas que seguem abertas

Decididas:

1. **P1, aprovada.** O texto do §7.2 entra no `CLAUDE.md` raiz (§4) e o `.github/pull_request_template.md` do §7.1 é criado,
   **em PR próprio** (Direto, depois deste). O teste de manifesto do §7.3 vira item Leve separado.
2. **P2, aprovada.** Vale a ordem do §6 (no lugar da sugestão inicial).
3. **P3, sim.** O guia existe no app, **adaptado ao nativo** (A10, **Completo** por causa do progresso por plataforma, M, depois dos itens de dinheiro; mínimo de texto).
4. **P4, não há data.** Só TestFlight por ora: A9 fica na posição 7 do §6. Se uma data aparecer, A9 sobe.

Abertas (não bloqueiam este PR):

5. **P5.** O modo privacidade no `/painel` é paridade (recomendado, W6) ou específico do app?
6. **P6.** O Resumo do `/painel` deve voltar a mostrar Saldo do mês e Guardado líquido, agora com a fonte real
   (`mes-detalhes`)? O dono tirou o "Guardado no mês" do Resumo por ser dado inventado (plano do dashboard §8, Etapa 4 PR B);
   a versão real é uma decisão nova, não uma volta automática (W5).

## Anexo A. Protótipo da matriz de rotas (leitura, não está no repositório)

Rodado em 2026-10-16 na `main` `b7c8232d` com `python3 -I script.py .`. Saída resumida: das rotas de `api/v2` (método e caminho),
as que **não têm consumidor no app** são `/eventos` (plataforma), `/gastos`, `/gastos/categoria`, `/guia` (GET e POST),
`/guia/dica`, `/investido`, `/lancamentos/carteira|editar|apagar`, `/metas` (GET, POST, editar, apagar) e
`/previsao/receita`; e **nenhuma rota do v2 fica sem consumidor no web**. As rotas só do app (`/mes-detalhes`,
`/patrimonio`, `/rendimento`) não têm consumidor no web. Remedir antes de reusar.

```python
#!/usr/bin/env python3
import re, sys
from pathlib import Path

RAIZ = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
DEC = re.compile(r'@router\.(get|post|put|patch|delete)\(\s*"([^"]+)"')


def rotas(pasta, ns):
    for f in sorted((RAIZ / pasta).glob("*.py")):
        for m, p in DEC.findall(f.read_text()):
            yield ns, m.upper(), p


def sem_comentario(txt):
    out = []
    for l in txt.splitlines():
        if l.strip().startswith(("//", "*", "/*", "#")):
            continue
        out.append(re.sub(r"\s//\s.*$", "", l))
    return "\n".join(out)


def arquivos(base, exts):
    for e in exts:
        for f in (RAIZ / base).rglob(f"*{e}"):
            if not any(x in str(f) for x in ("__tests__", ".gen.", "node_modules")):
                yield f


web, app = {}, {}
for f in arquivos("webapp/src/dashboard", (".ts", ".tsx", ".js")):
    t = sem_comentario(f.read_text())
    for fn, p in re.findall(r'api(Get|Post|Put)\(\s*"(/[^"]*)"', t):
        web.setdefault((fn.upper(), p), set()).add(f.name)
    if 'EventSource("/api/v2/eventos")' in t:
        web.setdefault(("GET", "/eventos"), set()).add(f.name)
for f in arquivos("app/src", (".ts", ".tsx")):
    t = sem_comentario(f.read_text())
    for seg in re.findall(r'/api/app(/[a-z\-]+(?:/[a-z\-]+)?)', t):
        app.setdefault(seg, set()).add(f.name)
    for seg in re.findall(r'base \+ [`"\']([a-z\-]+)', t):
        app.setdefault("/" + seg, set()).add(f.name)

escreve = {"/perfil": "PUT", "/assinaturas/marca": "POST"}  # o app só escreve nestas
for ns, m, p in list(rotas("api/v2", "v2")) + list(rotas("api/nativo", "app")):
    w = "(só do app)" if ns == "app" else ",".join(sorted(web.get((m, p), []))) or "-"
    a = ",".join(sorted(app.get(p, []))) if (m == "GET" or escreve.get(p) == m) else ""
    print(f"{ns:4} {m:5} {p:22} web={w:28} app={a or '-'}")
```
