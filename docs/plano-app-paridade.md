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
**ambos atrás** (existe só no painel antigo `/app` ou no site). `?` = não verificado a fundo (§9).

### 3.1 Resumo e blocos

| # | Função / tela | Web `/painel` | App | Diferença que importa | Veredito |
|---|---|---|---|---|---|
| 1 | Home em blocos, perfis e ordem | `Board` (`parts/Board.tsx`): blocos, Organizar, perfil via `GET/PUT /perfil` | Home nativa (`app/(app)/index.tsx`, `catalogo.ts`): blocos, seis perfis, perfil via `GET/PUT /api/app/perfil`, ordem no SecureStore por usuário e perfil | Catálogos quase iguais; o web tirou "Metas e caixinhas" do Resumo (decisão do dono, plano §8), o app ainda tem o bloco `metas`. Onde fica a ordem salva no web `?` | paridade (a conferir o catálogo) |
| 2 | Contas (saldo por conta) | `GET /contas` (`widgets/Contas.tsx`, real) | `GET /api/app/contas` (`contasResumo.tsx`) | — | paridade |
| 3 | Resumo do mês: Entrou e Saiu | `GET /resumo-do-mes` (`Stats.tsx`, real); **Fatura é de exemplo** | `GET /api/app/resumo-do-mes` (`resumoMes.tsx`) com comparação contra o mês anterior | O app compara com o mês anterior (`detalhesAnterior`); o web não (só no widget Comparação do #1033, aberto) | web atrás |
| 4 | Saldo do mês e **Guardado líquido** | Não há. "Guardado no mês" saiu do Resumo por ser dado inventado (plano §8, Etapa 4 PR B) | Topo da aba Gastos (`numerosMes.tsx`): Entrou, Saiu, Saldo, Guardado líquido, de `GET /api/app/mes-detalhes` | O número do app é real (`guardado` de `mes-detalhes`); `mes-detalhes` só existe no namespace do app (`api/nativo/`) | web atrás |
| 5 | Modo privacidade (esconder valores) | Não há no `/painel`; existe no painel antigo (`frontend/dashboard.js`, `toggleHideBalance`) | `oculto` em todo o app (`Valor`, `Anel`, cards) | Barato e só cliente | web atrás |

### 3.2 Gastos

| # | Função / tela | Web | App | Diferença que importa | Veredito |
|---|---|---|---|---|---|
| 6 | Para onde vai (categorias) | `GET /gastos`: estabelecimentos por categoria, série de 3 meses, detalhe (`GET /gastos/categoria`) | `Categorias` lê `mes-detalhes`; **não consome `/gastos`** | Sem estabelecimentos nem série; o servidor já serve `/api/app/gastos` (`tests/test_rotas_senha_obrigatoria.py:148`) | **app atrás (A1)** |
| 7 | Calendário "Dia a dia" | `GastosCalendario` sobre `/gastos`: grade real, quantidade e categorias por dia | `Calendario` sobre `mes-detalhes`: `{dia, entrou, saiu}`; toque leva ao extrato | Sem quantidade, categoria ou maiores | **app atrás (A1)** |
| 8 | Card "Seu dia" (#1029) | Setas de dia, total, pílula contra a média, fatos, barra, compras | Não há | Plano pronto em `docs/plano-seu-dia-app.md` (#1050): folha ao tocar no dia, sem frase | **app atrás (A1)** |
| 9 | Simulador | **De exemplo** (`widgets/Simulator.tsx`, `pages.tsx` `Simulate`) | Real: economia hipotética sobre gastos observados (`gastos.tsx`, `Simulador`), com gate de plano | O app calcula no cliente a partir de dados que o web também lê (`/gastos`) | web atrás |
| 10 | Assinaturas (serviços, outras, ignoradas, marcar) | `GET /assinaturas`, `POST /assinaturas/marca` | `GET /api/app/assinaturas`, `POST /api/app/assinaturas/marca` (`complementos.tsx`, `services/painel.ts`) | — | paridade |

### 3.3 Previsão

| # | Função / tela | Web | App | Diferença que importa | Veredito |
|---|---|---|---|---|---|
| 11 | Saldo previsto, horizonte 30/60/90, marcos, pior dia | `GET /previsao` (`Previsao.tsx`, real) | `Previsao` (`futuro.tsx`): chips de dias, curva, pior dia | — | paridade |
| 12 | "Como esta previsão foi feita" (premissas, cobertura, base) | `Previsao.tsx` mostra premissas e cobertura | O schema do app lê `premissas`/`cobertura`, mas **nenhuma tela as usa** | Só leitura de campos que já chegam | **app atrás (A6)** |
| 13 | Compromissos (grupos, qualidade) | `Compromissos.tsx`: agrupados como a API manda, com qualidade | `Compromissos` (`futuro.tsx`): lista plana (junta os `ocorrencias` de todos os grupos) | Sem grupo nem qualidade | **app atrás (A6)** |
| 14 | **Receitas do extrato** (confirmar, negar, desfazer) | `ReceitasDoBanco.tsx`, `POST /previsao/receita` (#1034) | **Não há**: o schema do app **nem tem** o campo `receitas_do_banco` | Escrita que altera a previsão; é a "2b (app)" já prevista no P7 | **app atrás (A5)** |
| 15 | Compromissos no **Resumo** do web | `compromissos: Bills` é de exemplo (a versão real só existe na página `/previsao`) | `compromissos` real (`Previsao`) | Inconsistência interna do web, não de paridade | web atrás |

### 3.4 Metas, patrimônio e cartões

| # | Função / tela | Web | App | Diferença que importa | Veredito |
|---|---|---|---|---|---|
| 16 | Metas: ler | `GET /metas`: fonte, ritmo, `chega_em`, reserva de emergência (`Metas.tsx`) | `TelaMetas` usa **`/goals/{uid}/status` (legado)**; só o card do chat lê o formato v2 (E1, via bloco do servidor, não via `/api/app/metas`) | O app mostra a meta por uma regra, o web/IA/WhatsApp por outra (#950, #954, C1, C2) | **app atrás (A2)** |
| 17 | Metas: criar, editar, apagar, converter reserva | `POST /metas`, `/metas/editar`, `/metas/apagar` (`MetaForm.tsx`) | Não há | `MetaFonte` do web também lê `/investido` e `/contas` para escolher a fonte | **app atrás (A3)** |
| 18 | Patrimônio (total, partes, histórico de 12 meses) | **De exemplo** (`NetWorth.tsx`, `Wealth.tsx`; lê `lib/api.ts`, sem rede) | Real: `GET /api/app/patrimonio` (`patrimonio.tsx`) | A rota só existe no namespace do app; a foto diária já grava (#723) | **web atrás (W1)** |
| 19 | Rendimento (taxa contratada) | **De exemplo** (`Yield.tsx`, "Rendimento × CDI") | Real: `GET /api/app/rendimento` (`Rendimento`, taxa contratada) | Mesma situação do 18 | **web atrás (W1)** |
| 20 | Fatura do cartão, Parcelas futuras | **De exemplo** (`Invoice.tsx`, `Installments.tsx`) | Reais, mas pelo **legado**: `/cards/{uid}/summary`, `/installments/{uid}/list` | Nenhum contrato v2; o app depende de rota legada com `user_id` no caminho | **web atrás (W4)**; app a migrar do legado |
| 21 | Renda mês a mês | **De exemplo** (`Income.tsx`) | Real, pelo legado: `/analytics/{uid}/evolution?months=6` | Idem | **web atrás (W4)** |
| 22 | "Piggy notou" | **De exemplo** (`Piggy.tsx`) | Real, pelo legado: `/insights/{uid}/current` (`Notou`) | Idem | **web atrás (W4)** |
| 23 | `/investido` (total investido nos bancos) | `GET /investido`: resposta do assunto `investido` do chat demo **e** fonte da meta nova (`MetaFonte.tsx`) | Não chama; o total chega pelo `patrimonio` (parte `investimentos_banco`) e pela IA | Sem consumidor próprio no app; só vira necessário com a escrita de metas (A3) | **entra com A3**, não é item sozinho |
| 24 | Comparação de períodos (#1033, aberto) | `GET /api/v2/comparacao` + widget (Plus+) | `/api/app/comparacao` montado, sem cliente | Nasceu no padrão certo: montada nos dois namespaces (`api/v2/app.py`) | app atrás (**A8**, quando mergear) |

### 3.5 Extrato

| # | Função / tela | Web | App | Diferença que importa | Veredito |
|---|---|---|---|---|---|
| 25 | Ler: busca (`q`), origem, tipo, categoria, dia, cursor | `GET /lancamentos` (`parts/Lancamentos.tsx`) | `GET /api/app/lancamentos` (`extrato.tsx`): mesmos filtros; `dia` filtra no aparelho | Paridade de filtros (conta e cartão existem no servidor e nenhum dos dois expõe) | paridade |
| 26 | `guardado=1` (aportes e saques como linhas marcadas, #983) | O `/painel` manda | O app **não manda, de propósito**: app instalado não atualiza com o backend (plano §8) | Um build novo pode mandar | **app atrás (A7)** |
| 27 | Escrever: lançar, editar, apagar na Carteira (Q36) | `POST /lancamentos/carteira`, `/editar`, `/apagar`, regra `pode` | O Extrato só lê | **Sem chave de idempotência** (`api/v2/lancamentos.py:107`): num celular com rede ruim, repetir o POST grava duas vezes | **app atrás (A4)** |

### 3.6 Chat do Piggy

| # | Função / tela | Web | App | Diferença que importa | Veredito |
|---|---|---|---|---|---|
| 28 | Conversa | **De exemplo**: `CHAT_REAL = false`, respostas de `lib/topics.tsx`; só o assunto `investido` é real | **Real**: `POST /ai/chat/stream`, `GET /ai/messages`, `DELETE /ai/pending`, feedback 👍👎, parar, chips (#943 a #956) | Etapa 7 do web "continua inteira" (plano §8) | **web atrás (W2)** |
| 29 | Cards no chat (`show_card`) | Blocos de exemplo | `periodo`, `caixinhas`, `metas` (só leitura, E1) e os do `TITULOS`; o card de gastos E2 (#1031) já está na `main` | Quando o web ligar o chat real, o `show_card` já vem do servidor | acompanha o 28 |

### 3.7 Guia e ajuda

| # | Função / tela | Web | App | Diferença que importa | Veredito |
|---|---|---|---|---|---|
| 30 | Guia do Piggy e dicas de primeiro uso | `GET/POST /guia`, `POST /guia/dica` (`Guia.tsx`, `Dica.tsx`) | Não há | O guia do web aponta para elementos da tela (`data-guia`); o equivalente nativo é outro desenho | **app atrás (A10)**, decidido em P3 |
| 31 | Ajuda e atalhos (Cmd-K, Menu) | `Command.tsx`, `Ajuda.tsx`, `MenuTelas.tsx` | A barra de abas e a Folha | Atalho de teclado não tem par em toque | plataforma |

### 3.8 Conta, segurança e sistema

| # | Função / tela | Web | App | Diferença que importa | Veredito |
|---|---|---|---|---|---|
| 32 | Conectar e reconectar banco (Open Finance) | Fora do shell v2: `/settings?view=open-finance` (`frontend/settings.html`) | `conectar-banco`, `conexoes`, `open-finance-volta` (OAuth no navegador, ADR 0005) | Superfícies diferentes pela plataforma. Limites e reconexão de cada lado `?` | paridade (a conferir os limites) |
| 33 | MFA e segurança | `settings.html` | `seguranca`, `mfa-ativar`, `mfa-desativar`, `mfa-novos-codigos` | — | paridade |
| 34 | Definir senha | `settings.html` ("Definir senha", #976) | Só `esqueci-senha` (`EsqueciSenha.tsx`) | Conta social sem senha: no app não há "criar senha" (`?`) | app atrás (**A9**, a confirmar) |
| 35 | **Excluir conta** e **exportar meus dados** | `settings.html` | Não há | Destrutivo e LGPD. **Provável exigência da App Store** para app com cadastro (diretriz 5.1.1(v), a confirmar antes da submissão; hoje só TestFlight) | **app atrás (A9)** |
| 36 | Plano e cobrança | Stripe/Asaas no site | Compras Apple são "entrega separada" (`app/README.md`) | Regra da loja | plataforma |
| 37 | Trava biométrica, tampa de privacidade | n/a | `features/bloqueio`, `expo-local-authentication` | Só existe em aparelho | plataforma |
| 38 | Tempo real | SSE `GET /eventos` (`lib/eventos.ts`) | Foco e foreground; `com_eventos=False` no namespace do app (`api/nativo/app.py`) | Deliberado e documentado (`docs/api-app-painel.md`) | plataforma |
| 39 | Push | Não há | **Não há**: `expo-notifications` não está no `app/package.json` | O orquestrador listou "push" como algo que o app tem; não tem | nenhum dos dois |
| 40 | Categorias: criar, renomear, emoji, cor, arquivar, apagar, fundir | Só no painel antigo; o v2 só lê (`GET /categorias`) | Só lê (`/api/app/categorias`) | Plano §6 "Depois": o web faz primeiro | **ambos atrás** |

### 3.9 O sentido contrário: o que o app tem e o web não

Resumo, para não perder: linhas 4 (Saldo e Guardado líquido), 5 (privacidade), 9 (Simulador real), 18 a 22
(Patrimônio, Rendimento, Fatura, Parcelas, Renda, "Piggy notou" reais) e 28 (chat real). Dentro dos "específicos da
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
| A6 | Previsão: premissas, cobertura e grupos de compromissos | 0 | telas só de leitura sobre campos que já chegam | Leve | P | A5 (mesma tela) |
| A7 | `guardado=1` no Extrato, com o rótulo "Guardado" | 0 | mandar o parâmetro, rótulo e sinal conforme #983 | Leve | P | Um build novo (apps antigos continuam sem) |
| A8 | Comparação de períodos | 0 (#1033 monta `/api/app/comparacao`) | card de comparação, Plus+ | Leve | P | Esperar o #1033 mergear |
| A9 | Excluir conta, exportar dados, definir senha | **a verificar**: se as rotas do site aceitam Bearer; senão acrescentar | telas nativas | **Completo** (auth, sessão, destrutivo) | G | Decisão do dono sobre a data de submissão à App Store (§10, P4) |
| A10 | Guia e dicas, adaptados ao nativo (P3: sim) | 0 (`/api/app/guia*` montado) | desenho nativo (coach marks ou cartões de primeiro uso), mínimo de texto, `dado` real/exemplo do roteiro | Leve | M | Depois dos itens de dinheiro |
| A11 | Categorias: gerenciar | Depende do web | idem | Leve/Completo | G | Web primeiro (plano §6) |

O que o app **deve migrar do legado** (`/goals`, `/cards`, `/installments`, `/analytics`, `/insights`) para contratos v2
acompanha o item W4 do web (`docs/plano-dashboard-v2.md`): como o web precisa do mesmo dado, **a rota nova nasce em
`/api/v2` e é montada nos dois namespaces**, e o app troca a fonte depois (campo novo, nunca mudando o que o build
antigo lê).

## 5. Específico da plataforma (proposta, aguardando o dono)

Não entram na matriz como dívida: SSE × foco e foreground (38), atalhos de teclado (31), biometria e tampa (37),
Compras Apple × cobrança do site (36), conexão de banco por navegador × widget (32). O dono decidiu (§10, P3) que o
guia **não** é específico: existe no app, adaptado ao nativo (A10). Segue aberta a pergunta do modo privacidade (5), cuja
recomendação é **não** ser específico (o painel antigo já o tem).

## 6. Ordem recomendada e por quê

A sugestão inicial do orquestrador era: Metas v2 e escrita de lançamentos; depois Para onde vai v2 e o card do dia;
depois `/investido`; por último guia e dicas. **A evidência pede outra ordem:**

| # | Item | Por que nessa posição |
|---|---|---|
| 1 | **A1 Para onde vai + "Seu dia"** | É o único com plano fechado e decisões do dono tomadas (2026-10-16), servidor pronto, só Leve, e a dependência (#1031) já está na `main`. Foi o gatilho da regra. |
| 2 | **A2 Metas v2 (leitura)** | É barato (o formato e os textos já estão no app por causa do E1) e **corrige um risco que existe hoje**: o app mostra a meta por uma regra e o web, a IA e o WhatsApp por outra. Vem antes da escrita. |
| 3 | **A5 Receitas do extrato + A6** | Já era trabalho prometido (P7 2b); fecha uma função de dinheiro que o web já oferece. |
| 4 | **A3 Metas (escrita)** com `/investido` e `/contas` | Depende de A2. `/investido` **não é item sozinho**: o único consumidor real é a escolha da fonte da meta. |
| 5 | **A4 Escrita na Carteira**, com o PR de servidor (idempotência) **antes** | Contesto "escrita primeiro": é o item mais caro (Completo, G), o servidor não protege contra repetição de POST e o produto já reduziu a escrita manual a uma só (Carteira). Vale começar o PR de servidor cedo, em paralelo, porque ele não depende de build. |
| 6 | A7, A8 | Pequenos; A8 espera o #1033. |
| 7 | **A9** | Sem data de App Store (§10, P4): fica aqui. Sobe no dia em que o dono marcar uma submissão. |
| 8 | A10, A11 | A10 já decidido (P3, sim); A11 depende do web. |

Em paralelo, no web (`docs/plano-dashboard-v2.md`): W1 (Patrimônio e Rendimento reais) e W5 (Saldo e Guardado líquido)
só precisam **montar no namespace v2** rotas que já existem; W2 (chat real) é a Etapa 7; W3 (Simulador) é
cliente puro; W4 (Fatura, Parcelas, Renda, "Piggy notou") precisa de contrato novo e de decisão sobre as fontes
(plano "só Open Finance").

## 7. Regra operacional daqui para a frente

Aprovada pelo dono em 2026-10-16 (§10, P1). **Este PR não edita o `CLAUDE.md` raiz**: a edição e o template vão em PR próprio.

**7.1 No PR.** Todo PR de feature de qualquer dos dois lados traz no corpo uma linha:

```text
Par no outro lado: já existe | plano/issue #N | específico da plataforma porque <razão>
```

Não existe `.github/pull_request_template.md` hoje; criá-lo é a forma barata de a linha aparecer sem ninguém lembrar.

**7.2 No `CLAUDE.md` raiz (proposta de texto, §4 "PR, Codex e threads").**

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
- **Quase todo item de cliente (A1 a A8) acumula em builds**: A1, A2 e A7 cabem no mesmo build; A4 e A9 pedem build
  próprio por terem teste de aparelho.
- **PRs abertos relacionados:** #1050 (plano do Seu dia), #1033 (comparação; condiciona A8), #1051 (IA cria metas;
  mesma regra do servidor de A3), #1035 (login com MFA e exclusão agendada; pode condicionar A9, a conferir).

## 9. O que foi medido e o que não foi

Medido, por leitura estática e grep, em 2026-10-16: rotas dos dois sub-apps, consumidores no web e no app (Anexo A),
`TITULOS` e perfis do app, `Frame` com e sem `real`, `CHAT_REAL`, schema da previsão (campos que o app lê e usa),
`MetaFonte` e `investidoQuery`, filtros do `/lancamentos`, rotas legadas que o app usa, o `app.config.ts`, os PRs abertos.

**Não medido:** nenhum jest, nenhum teste web, nenhuma chamada ao servidor nem a produção, nada aberto em aparelho
ou simulador. Os `?` da matriz: onde fica a ordem salva da Home no web; limites e reconexão do Open Finance em cada
lado; se as rotas de excluir conta e exportar dados aceitam Bearer; se há "criar senha" para conta social no app; se o
catálogo de blocos tem exatamente os mesmos itens por perfil; a regra da App Store (5.1.1(v)) é conhecimento da
política, não verificada nesta sessão.

## 10. Decisões do dono (2026-10-16) e perguntas que seguem abertas

Decididas:

1. **P1, aprovada.** O texto do §7.2 entra no `CLAUDE.md` raiz (§4) e o `.github/pull_request_template.md` do §7.1 é criado,
   **em PR próprio** (Direto, depois deste). O teste de manifesto do §7.3 vira item Leve separado.
2. **P2, aprovada.** Vale a ordem do §6 (no lugar da sugestão inicial).
3. **P3, sim.** O guia existe no app, **adaptado ao nativo** (A10, Leve, M, depois dos itens de dinheiro; mínimo de texto).
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
