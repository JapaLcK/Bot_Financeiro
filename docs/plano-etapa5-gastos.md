# Etapa 5 do dashboard v2 — "Para onde vai" (`/painel#/gastos`)

Rascunho de plano, aberto em 2026-10-13. Parte do `docs/plano-dashboard-v2.md` (§6: etapa 5,
"Para onde vai", API faixa Completo, tela faixa Leve). O plano-base quase não descreve esta
etapa; este documento é o que ele não tem. Cada PR confere se o que está aqui ainda vale.

## 1. O que a tela é

Mostra de onde o dinheiro do mês saiu. Hoje é protótipo com dados fictícios
(`webapp/src/dashboard/pages.tsx`, função `Spending`), em três blocos:

- **Categorias** (`widgets/Categories.tsx`): gasto do mês por categoria, com barra e a
  variação contra o mês anterior **no mesmo dia do mês**. O mesmo bloco aparece no Resumo.
- **Detalhe da categoria** (`widgets/CategoryDetail.tsx`): total, número de compras, valor
  médio, os últimos meses da categoria e "onde mais gasta" (cinco estabelecimentos).
- **Calendário** (`widgets/Calendar.tsx`): gasto por dia, filtrado pela categoria escolhida.

Escolher uma categoria filtra os outros blocos e leva aos Lançamentos reais
(`/api/v2/lancamentos` já aceita o filtro `categoria`).

## 2. Decisões do dono (2026-10-13)

1. O escopo é Categorias + Detalhe + Calendário. Assinaturas já têm API v2
   (`api/v2/assinaturas.py`); o Simulador não é desta etapa.
2. "Mesmo dia do mês anterior": **data da compra**, inclusive no cartão. É o que
   `db/mes_detalhes.ler` já faz (cartão pela fatura do mês, com o dia original da compra).
3. **Estabelecimento:** tentar limpar o nome por estabelecimento, não só agrupar o texto cru.
   Regra e medição no §3.1.
4. Categorias: **mantêm-se as existentes e as que o usuário criou**; o usuário personaliza.
   Já existe: `user_categories` guarda `name`, `emoji` e `color` por usuário, e o painel antigo
   tem o modal de criar/editar. A v2 lê emoji e cor dali; não inventa paleta. **Exceção única,
   para a linha sem linha no catálogo:** "Sem categoria" (`NULL`) não existe em
   `user_categories` (o seed não a tem e o importador ignora nome nulo), e uma categoria que
   aparece nos lançamentos mas não no catálogo também não tem metadado. Todas essas linhas
   saem com um par emoji/cor fixo e neutro, uma constante do servidor ao lado de
   `CAT_VAZIA_LABEL` (o valor exato é escolhido no PR 1 com a skill `pigbank-frontend`), e o
   nome é a própria chave, como já faz o app (`nomeCategoria`). O contrato garante `emoji` e
   `cor` não nulos em toda linha, para a tela nunca depender de fallback no cliente.
   **Precedência:** o catálogo do usuário sempre ganha. Se existe uma categoria real chamada
   "Sem categoria", o balde único (`cat_key_sql` junta essas linhas e as `NULL`) usa o nome, o
   emoji e a cor dela; a constante neutra só vale quando não há linha de catálogo para a chave.
5. Gasto sem categoria aparece como **"Sem categoria"**.
6. Investimento conta no orçamento geral (decisão anterior): a compra de renda variável gravada
   como despesa entra em "Para onde vai". Não entram o depósito em caixinha nem o aporte
   (`deposito_caixinha`, `aporte_investimento`): são "guardado" (§5, P8).
7. O histórico respeita a janela do plano (`history_earliest_date`, que no plano de 90 dias
   pode cair no MEIO de um mês). Regra única, a mesma de `db/resumo_mes.py` (`anterior = None`
   quando o corte toca qualquer parte do mês anterior): **mês só entra em comparação ou série se
   o intervalo inteiro dele está disponível**; senão sai `null` (nunca zero, nunca parcial com
   cara de exato) e a resposta leva `inicio_do_historico`. Vale para o mês anterior da
   comparação "até o mesmo dia" e para cada mês dos "últimos meses" de uma categoria.
8. Critério de aceite: a soma das categorias bate com o **Saiu** do Resumo do mesmo mês, **nos
   meses sem `outra_moeda`**. Com `outra_moeda`, `db/mes_detalhes._em_reais` tira a linha em
   outra moeda das categorias e `TOTAIS_SQL` ainda a soma no `saiu` (o teste
   `test_mes_moeda_diferente_tem_cobertura_explicita_sem_donut_falso` espera categorias 10
   contra `saiu` 110, de propósito): nesse caso a tela mostra só as categorias em reais, diz
   quantas linhas ficaram fora (`fora_do_total`) e levanta o motivo, e o teste compara com o
   subtotal em reais, nunca com o `saiu`. Também com o motivo `arredondamento_por_grupo` a
   igualdade vale só até o centavo de arredondamento por grupo, como no `/mes-detalhes`.

## 3. O que já existe (não recriar — CLAUDE.md §0.1)

- `db/mes_detalhes.ler(user_id, ano, mes, agora)`: o mês inteiro, sem top-N — `saiu`,
  `categorias` (`categoria`, `valor`, `quantidade`), `dias`, `origens`, `guardado`, `motivos`,
  `fora_do_total`. Tira internos e outra moeda. Hoje só o app nativo o expõe
  (`api/nativo/mes_detalhes.py`, `GET /mes-detalhes`).
- `db/mes_detalhes.ler_periodo`: categorias somadas num período, com as 5 maiores linhas de
  cada uma. Não separa por mês.
- `db/lancamentos.listar`: o leitor oficial; cada linha traz `descricao`, `categoria`,
  `origem`, `dia`, `valor`.
- `GET /api/v2/categorias`: só `{chave, nome}`. **Não devolve emoji nem cor.**
- `GET /api/v2/resumo-do-mes`: Entrou/Saiu pela regra única do mês (Q18).
- `db/analytics.compute_top_merchants`: agrupa por estabelecimento (`lower(trim(alvo/nota))`),
  mas lê `launches` e `credit_transactions` direto, com regra de mês própria (`criado_em` no
  débito) e sem tratar moeda, em vez do leitor oficial. As importações do Open Finance também
  caem nessas tabelas (`source = 'open_finance'`), então ele as enxerga; o que ele não garante
  é bater com o Saiu do Resumo (critério de aceite). **A decidir no PR 1:** agrupar por cima
  das linhas do leitor oficial (bate por construção) ou reaproveitar esta função e provar a
  igualdade com um teste.

### 3.1 O que a Pluggy manda (documentação, conferida em 2026-10-13)

Fontes: <https://docs.pluggy.ai/reference/transactions-list>,
<https://docs.pluggy.ai/docs/enrich-api>, <https://docs.pluggy.ai/docs/transactions>.

- `description` é o texto que a Pluggy tira da instituição; `descriptionRaw` é o bruto e só
  vem quando o banco o fornece (`null` em exemplos de TED).
- `merchant` é um objeto (`name`, `businessName`, `cnpj`, `cnae`, `category`) ou `null`. A
  documentação diz que depende do plano da conta Pluggy (enriquecimento habilitado, nível
  Pro). **Não está provado que a nossa conta recebe `merchant`.**
- Em Pix e TED, `paymentData.receiver.name` / `payer.name` traz a contraparte; o exemplo
  de cartão traz `creditCardMetadata.payeeMCC`.
- Já guardamos tudo isso: `open_finance_transactions.raw` é o payload cru (e
  `db/of_recurring.py` já lê `raw->'merchant'`). Hoje só `description` vira `alvo`.

**Medido em 2026-10-13**, na conta do dono (uid 88648360, produção, só leitura, só contagens;
`open_finance_transactions` ligada a contas e conexões do usuário; só gasto: saída em conta
BANK e compra em conta CREDIT). É UMA conta, 719 transações; serve para decidir a forma da
regra, não para prometer cobertura.

| | BANK, saída (589) | CREDIT, compra (130) |
|---|---|---|
| `merchant.name` | 0 | 28 |
| `merchant.businessName` | 314 (53%) | 32 (25%) |
| `merchant.cnpj` | 314 (53%) | 32 (25%) |
| `paymentData.receiver.name` | 332 (56%) | 0 |
| qualquer nome limpo (name, businessName ou receiver) | 332 (56%) | 39 (30%) |
| `descriptionRaw` preenchido | 589 | 130 |
| `descriptionRaw` igual à `description` | 589 | 130 |
| `payeeMCC` | 0 | 117 (90%) |
| grupos só pela `description` normalizada | 157 | 80 |
| grupos pela regra completa (nome limpo, senão `description`) | 141 (−10%) | 79 (−1%) |
| nas linhas com `cnpj`: descrições distintas × CNPJs distintos | 102 × 88 (−14%) | 7 × 6 |

O que isso diz:
- `merchant.name` quase não vem (nunca no banco). O que vem no banco é `businessName` + `cnpj`
  juntos (53%) e o nome do recebedor do Pix/TED (56%); no cartão o enriquecimento é raro (30%).
- `descriptionRaw` é idêntico à `description` em todas as linhas: não acrescenta nada.
- O ganho de agrupar é **pequeno nesta conta** (cerca de 10% menos grupos no banco, quase nada
  no cartão): a `description` que a Pluggy já devolve vem razoavelmente limpa.
- `businessName` é a razão social, não o nome de loja; serve de chave, mas não de rótulo.
- No cartão o `payeeMCC` (90%) dá a categoria do comerciante, não o nome; fora do escopo.

Regra decidida: a **chave** de agrupamento é o `merchant.cnpj` quando existe (o mais estável);
senão o nome do recebedor do Pix/TED (`paymentData.receiver.name`, normalizado); senão a
`description` normalizada (minúsculas, sem acento, sem espaços sobrando). O **rótulo mostrado** é a
`description` mais frequente do grupo, que é o que o usuário reconhece no extrato; `businessName`
nunca é rótulo. Cobertura em outras contas não foi medida; a tela não deve prometer
agrupamento perfeito.

## 4. O que criar

Este capítulo foi reescrito inteiro na sexta rodada de revisão do Codex, a partir de um
inventário de **tudo** o que os três blocos do protótipo fazem (§4.2), em vez de acrescentar um
apontamento por vez. `ler` devolve só agregados do mês (`categorias {categoria, valor,
quantidade}`, `dias {dia, entrou, saiu}` sem categoria, `origens`, `guardado`, `motivos`,
`fora_do_total`) e não devolve linhas; é a partir disso que o desenho abaixo foi feito.

### 4.1 O desenho do PR 1 (API, faixa **Completo**: endpoint novo na `/api/v2`, Q21)

**Duas rotas**, para o cliente pedir só o que mostra e o corpo ter tamanho limitado:

- `GET /api/v2/gastos?mes=AAAA-MM`: a visão do mês. Por categoria: `chave`, `nome`, `emoji`,
  `cor`, `valor`, `quantidade` e o gasto do mês anterior até o mesmo dia (ou `null`, decisão 7).
  Mais: o calendário de todas as categorias (por dia: total e os 3 maiores gastos do dia, com
  descrição, valor e a chave da categoria), `outro_mes` (decisão B), `hoje` (AAAA-MM-DD no fuso do
  app: o cliente não usa o relógio dele), `fora_do_total`, `motivos`. Alimenta o bloco
  Categorias e o Calendário, e o Resumo usa a mesma consulta (mesma chave de cache).
- `GET /api/v2/gastos/categoria?mes=AAAA-MM&categoria=<chave>`: o detalhe de uma categoria.
  Total, compras, valor médio, os 3 últimos meses (cada um `null` se o corte do plano o toca),
  os 5 estabelecimentos (regra do §3.1; consulta própria, porque o leitor oficial não expõe
  `raw`, extraindo só `merchant.cnpj` e `paymentData.receiver.name`) e o calendário só dessa
  categoria (por dia: total e os 3 maiores). Tudo completo e sem paginação: no máximo 31 dias
  × 3 maiores + 5 estabelecimentos + 3 meses.

**Uma leitura só.** `db/mes_detalhes.ler` abre a própria conexão a cada chamada. O PR 1 separa
dele a parte que lê as linhas do mês e tira internos e outra moeda (`listar` + `_em_reais`),
para que `ler` (rota nativa, comportamento e contrato inalterados, com os testes que já existem)
e a rota nova agreguem **as mesmas linhas**. A rota lê o mês pedido e os dois anteriores numa
**única transação repeatable read / read only**, como `ler_periodo` já faz com vários meses:
a comparação, a série e o total da categoria saem do mesmo snapshot, e o mês escolhido na série é
o mesmo valor da lista. Teste: uma conexão só para todos os meses. Limite declarado: as duas rotas
são duas respostas; se um lançamento entrar entre elas, o detalhe e a lista podem diferir por
um instante, e o aviso de escrita (SSE) refaz as duas.

**Outras mudanças do PR 1**
- `GET /api/v2/lancamentos` ganha `dia=AAAA-MM-DD` (filtra pelo `dia` devolvido), para o clique
  no dia do calendário. Teste com o dia de uma compra de cartão de outro mês.
- `GET /api/v2/categorias` passa a devolver `emoji` e `cor` (com a regra da decisão 4 para a
  linha sem catálogo).
- Arredondamento: a rota aplica `soma_arredondada_diverge` (`api/nativo/dinheiro.py`, importar
  ou extrair para um lugar comum; nunca copiar) e levanta `arredondamento_por_grupo`, com o caso
  de duas despesas de `1.005` em categorias diferentes (categorias `2.02` × `saiu` `2.01`). O
  motivo hoje é posto pela rota nativa depois do `ler`, não pelo `ler`.
- Motivos do §4 do plano: banco desatualizado, outra moeda, início do histórico.
- Sem tabela nova, então `db/privacy.py` não muda. Teste de isolamento "B não vê o que é de A".

### 4.2 Inventário do protótipo: o que cada bloco faz e de onde vem

**Categorias** (`Categories.tsx`)
| Comportamento | Fonte e decisão |
|---|---|
| uma linha por categoria com gasto, em ordem decrescente | `/gastos`; categoria sem gasto no mês não aparece |
| "R$ X gastos no mês" | a soma das categorias em reais, não o `saiu`; com `fora_do_total` o bloco avisa quantas linhas ficaram fora |
| traço e variação % contra o mês anterior no mesmo dia | `valor_ate_hoje` e `anterior_ate_hoje` por categoria, os dois de 1 até `hoje` (a saída com data depois de `hoje` fica fora; a de data anterior ao mês conta como dia 1), para comparar iguais com iguais; `null` quando o corte do plano toca o mês anterior (decisão 7) e "—" quando o anterior é zero. A barra e o `valor` são o mês inteiro, que inclui o lançamento com data futura, como o Saiu do Resumo |
| "Sem categoria" | rótulo `sem categoria`, emoji e cor da constante neutra (decisão 4); filtra com `categoria=sem categoria` (abaixo) |
| clicar filtra o resto; "Limpar" | estado do cliente |
| âncoras do guia (`data-guia="categorias.lista"` e `categorias.item`) | o PR 2 as preserva (#728) |
| nome acessível com valor e variação | derivado dos mesmos campos |

**Detalhe da categoria** (`CategoryDetail.tsx`)
| Comportamento | Fonte e decisão |
|---|---|
| categoria padrão quando nenhuma foi escolhida ("maior gasto do dia a dia") | sem a marca `variable` (decisão B), vale a **maior categoria do mês**, e o rótulo vira "maior gasto do mês" |
| No mês / Compras / Valor médio | `valor`, `quantidade` e `valor ÷ quantidade` (`null` sem compras); parcela de cartão conta uma compra por parcela do mês |
| últimos meses | `/gastos/categoria`, 3 meses, cada valor é o mês inteiro (o do mês corrente é o mesmo `valor` da lista, inclusive lançamento com data futura); o corrente é rotulado "mês em andamento", não "até hoje"; mês cortado pelo plano é `null` |
| onde mais gasta, com contagem e barra | estabelecimentos (§3.1), top 5 |
| últimas compras (6, com data, nome, mensagem, origem, valor) | `GET /api/v2/lancamentos?categoria=&mes=&tipo=saida&limite=6`, que já existe, ordena do mais novo e traz `mensagem` e `origem`; sem `tipo=saida` a lista traria também as entradas da categoria e elas tirariam compras das 6 linhas |
| "Abrir lançamentos" | link para `/lancamentos` com `mes` e `categoria` |

**Calendário** (`Calendar.tsx`)
| Comportamento | Fonte e decisão |
|---|---|
| uma célula por dia do mês, futuro desabilitado, hoje marcado | `hoje` vem do servidor. Dia futuro SEM gasto fica desabilitado; dia futuro COM lançamento agendado fica habilitado e marcado "agendado", com o total e os maiores |
| escala de cor | percentil sobre os dias do mês (decisão B); o cliente a calcula de `dias` |
| dica e nome acessível com os 3 maiores gastos do dia | `maiores` (3) por dia, em `/gastos` e em `/gastos/categoria` |
| "média R$ X/dia · N dias sem gasto" | o cliente calcula de `dias` só até `hoje` (dia agendado não entra); a API devolve só os dias com gasto e o cliente completa os zeros; o texto "Sem contas fixas" sai (agora conta todo gasto, decisão B) |
| "Dia mais caro" | o maior `total` de `dias` |
| clicar no dia filtra os lançamentos do dia | `/lancamentos?dia=` (PR 1) |
| calendário filtrado por categoria | `/gastos/categoria` devolve os dias só dessa categoria |
| saída com dia fora do mês | linha "outro mês" (decisão B), fora da grade |

**Filtro de "Sem categoria".** `categoria=None` em `listar` significa "sem filtro", mas o filtro
compara `cat_key_sql` dos dois lados (`db/connection.py`), que colapsa vazio em
`CAT_VAZIA_LABEL = "sem categoria"`. A linha "Sem categoria" abre os lançamentos com
`categoria=sem categoria`, e o contrato diz isso (o cliente não escreve o literal: recebe-o da
resposta). Uma categoria do usuário chamada "Sem categoria" cai no mesmo balde, como no donut
antigo. **Ainda não provado**: não há teste de `/api/v2/lancamentos?categoria=sem categoria`; o
PR 1 leva esse teste (linha de cartão do Open Finance com `categoria` NULO, que o
`cat_key_sql` cita como o caso real) e o de navegação do PR 2.

### 4.3 PR 2 — Tela (faixa **Leve**)
Troca o protótipo pelo real nos três blocos, no Resumo e em `/gastos`, pelo inventário do §4.2:
estados de carregando, erro, vazio e dado incerto; âncoras do guia preservadas; teclado e leitor
de tela com o mesmo conteúdo da dica (os 3 maiores); mobile e desktop conferidos no navegador
(skill `pigbank-frontend`). Fora: os blocos de exemplo do chat (`topics.tsx`), que são da Etapa 7.

## 5. Decisões de 2026-10-13 sobre o que estava aberto

- **A. Estabelecimento:** limpar por estabelecimento (decisão 3 acima), pela regra e com a
  medição do §3.1.
- **B. Calendário:** conta todo gasto, sem marca `variable`, com a escala por percentil.
  Aprovado.
  **Saída com dia fora do mês** (apontamentos do Codex; vale para cartão e para banco). O leitor
decide o mês por uma regra (fatura no cartão; `criado_em` na linha de `launches`) e devolve o
`dia` por outra: a compra de cartão guarda a data da compra, e a linha antiga do Open Finance sem
hora usa `posted_at` (`db/lancamentos._PERNA_LANCAMENTOS`; o módulo documenta que esse dia pode
cair fora do mês). Os testes `test_mes_completo_manual_cartao_parcela_interno_e_conciliacao`
(cartão) e a regra de `posted_at` (banco) mostram os dois casos. Regra proposta, a confirmar na
revisão do PR 2: o calendário só desenha dias do próprio mês; **toda saída cujo `dia` cai fora
do mês** (cartão ou banco) vai para uma linha fixa "N saídas com data de outro mês: R$ X", que
entra nas categorias e no total mas não na grade de dias. Na comparação "até o mesmo dia do mês
anterior" (decisão 2), a saída de data anterior ao mês conta como já ocorrida (dia 1) e a de
data posterior ao fim do mês conta só no total. Nenhum dia é mostrado no mês errado.
- **C. Editar categoria:** esta etapa só lê emoji e cor. Criar, renomear, trocar emoji e cor
  e arquivar categoria **não estava em nenhuma etapa do plano** (só o painel antigo faz); entra
  no plano principal como tela própria depois das etapas (§6, "Depois"), e até lá abre o
  painel antigo com o aviso discreto (Q1/Q15). Aprovado.
- **D. Cor repetida:** aceita; o nome aparece sempre ao lado da barra. Aprovado.
- **P8 (depósito em caixinha e aporte), confirmada pelo dono em 2026-10-13:** na lista
  aparecem marcados como "guardado", fora de todo total (como a P8 manda para o interno), o
  depósito/aporte como saída da conta e o saque/resgate como entrada. Isso é uma mudança da
  Etapa 2 (a lista de `db/lancamentos.py` ainda os omite), não desta; entra como PR próprio
  e a Etapa 2 só fecha com ele. Em "Para onde vai" nunca entram como categoria: o
  `guardado` de `db/mes_detalhes.ler` já os separa, e contá-los como gasto quebraria o
  critério de aceite (soma das categorias = Saiu do Resumo).

## 6. Fora desta etapa

Tela de edição de categorias na v2, orçamento por categoria, Assinaturas, Simulador, qualquer
mudança no cálculo do mês. Se aparecer divergência com o Saiu do Resumo, é gate do PR 1, não
número ajustado.

## 7. Como conferir

Pytest com Postgres real para a soma (categorias = Saiu nos meses sem `outra_moeda`;
categorias = subtotal em reais, com `fora_do_total`, nos meses com ela) e para o isolamento entre
usuários; dois casos de saída com dia fora do mês, um de cartão e um de banco (`posted_at` de
outro mês), ambos na linha "outro mês" e nunca num dia errado; gasto por categoria e dia somando
o gasto da categoria; `/api/v2/lancamentos?categoria=sem categoria` devolvendo só as sem
categoria; plano de 90 dias com o corte no meio do mês: comparação `null` e mês da série
`null` (nunca zero), com `inicio_do_historico`; linha sem categoria e categoria fora do
catálogo com `emoji` e `cor` não nulos; uma só conexão para todos os meses lidos pela rota; os 3
maiores gastos do dia (e o desempate); `/lancamentos?dia=` com o dia de uma compra de cartão de
outro mês; lançamento com data futura no mês corrente (`valor` inclui, `valor_ate_hoje` e a
comparação não, dia futuro agendado habilitado); categoria com entrada e saída (as 6 últimas
compras só de saída); usuário com categoria real "Sem categoria" e linhas `NULL` (um balde, metadado
do catálogo).
Playwright para a tela. Na conta do dono, em produção só leitura: soma das categorias do mês
contra o Saiu do Resumo e contra o "gastos em outubro" do WhatsApp (#935 alinhou a IA a essa
regra).
