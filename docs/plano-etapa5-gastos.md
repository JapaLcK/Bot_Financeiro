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
   tem o modal de criar/editar. A v2 lê emoji e cor dali; não inventa paleta.
5. Gasto sem categoria aparece como **"Sem categoria"**.
6. Investimento conta no orçamento geral (decisão anterior): a compra de renda variável gravada
   como despesa entra em "Para onde vai". Não entram o depósito em caixinha nem o aporte
   (`deposito_caixinha`, `aporte_investimento`): são "guardado" (§5, P8).
7. O histórico respeita a janela do plano (`history_earliest_date`); o corte aparece com o
   motivo `inicio_do_historico`, não como mês zerado.
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

PR 1 — API (faixa **Completo**: endpoint novo na `/api/v2`, Q21).
- Rota de gastos do mês que reaproveita `db/mes_detalhes.ler` (a mesma função do app nativo;
  nenhuma segunda regra do mês). O `ler` sozinho não basta: o motivo
  `arredondamento_por_grupo` é posto pela ROTA nativa (`api/nativo/mes_detalhes.py`), depois do
  `ler`, com `soma_arredondada_diverge` (`api/nativo/dinheiro.py`) sobre categorias, dias e
  origens. A rota v2 aplica a mesma checagem pelo mesmo helper (importar ou extrair para um
  lugar comum; nunca copiar) e leva o caso de regressão do `/mes-detalhes` (duas despesas de
  `1.005` em categorias diferentes: categorias `2.02` × `saiu` `2.01`, com o motivo).
- Do mês anterior, o gasto por categoria **até o mesmo dia** (limitado ao último dia do mês
  anterior quando ele for mais curto).
- Por categoria: série dos últimos meses (hoje não existe) e os estabelecimentos, pela regra
  do §3.1 sobre as linhas do leitor oficial (que passam a expor `merchant` e `paymentData`
  já extraídos de `raw`, nunca o `raw` inteiro).
- `/api/v2/categorias` passa a devolver `emoji` e `cor`.
- Mesma regra de motivos do §4 do plano: banco desatualizado, outra moeda, início do
  histórico. Nada de número incerto com cara de exato.
- Sem tabela nova, então `db/privacy.py` não muda. Teste de isolamento "B não vê o que é de A".

PR 2 — Tela (faixa **Leve**). Troca o protótipo pelo real nos três blocos, no Resumo e em
`/gastos`; o Calendário passa a usar os dias reais; filtro por categoria leva aos
Lançamentos reais; estados de carregando, erro, vazio e dado incerto.

## 5. Decisões de 2026-10-13 sobre o que estava aberto

- **A. Estabelecimento:** limpar por estabelecimento (decisão 3 acima), pela regra e com a
  medição do §3.1.
- **B. Calendário:** conta todo gasto, sem marca `variable`, com a escala por percentil.
  Aprovado. **Compra de cartão fora do mês** (apontamento do Codex, confirmado em
  `test_mes_completo_manual_cartao_parcela_interno_e_conciliacao`, onde um dia volta antes do
  mês pedido): o leitor põe a compra na fatura do mês escolhido e guarda a data original, que
  pode cair no mês anterior. Regra proposta, a confirmar na revisão do PR 2: o calendário só
  desenha dias do próprio mês; a compra de cartão com data fora do mês não vai para nenhum dia
  e aparece numa linha fixa "N compras do cartão com data de outro mês: R$ X", que entra nas
  categorias e no total mas não na grade de dias. Na comparação "até o mesmo dia do mês
  anterior" (decisão 2), a compra de data anterior ao mês conta como já ocorrida (dia 1), e a
  de data posterior ao fim do mês conta só no total. Nenhum dia é mostrado no mês errado.
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
usuários; um caso com compra de cartão de data fora do mês (a linha "outro mês", sem dia
errado).
Playwright para a tela. Na conta do dono, em produção só leitura: soma das categorias do mês
contra o Saiu do Resumo e contra o "gastos em outubro" do WhatsApp (#935 alinhou a IA a essa
regra).
