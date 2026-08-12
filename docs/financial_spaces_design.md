# Espaços Financeiros (design, não implementado)

Objetivo: uma conta gerencia **vários perfis financeiros separados** dentro do
mesmo login — 👤 Pessoal · 🏠 Casa · 🌾 Fazenda · 🏖️ Casa de fim de semana ·
💼 Empresa. Cada espaço tem seus próprios lançamentos, saldos, categorias e
metas, mas o usuário **nunca precisa pensar em espaços**: quem não cria um
segundo espaço nem vê o recurso.

Alinha com a promessa do produto ("fale como você fala", sem planilha nem
comando): a Piggy identifica o espaço pela própria frase e organiza sozinha.

Status: **design congelado, não implementado.** `grep` por `space_id`,
`financial_spaces` e `internal_transfer_id` no repositório volta vazio.

Previews navegáveis (privados):
- Fluxos (WhatsApp + dashboard + criação): `claude.ai/code/artifact/c68ab510-d317-475b-881f-47704a487d7f`
- Seletor de espaço ao vivo na Visão Geral: `claude.ai/code/artifact/8e8f9ae7-e621-4c91-9b16-d61bb08ee9ca`
- Dividir uma compra entre espaços: `claude.ai/code/artifact/09ced5a6-3327-478a-bb32-5ff57f311456`

> **Onde paramos (12/08/2026):** design de produto + arquitetura congelados
> (Modelo B, `launch_allocations`, ordem de implementação aprovada). A UX da
> divisão foi resolvida no preview: **digitar N−1 valores, o último espaço é o
> "restante" automático** (a barra arrastável foi descartada — não escala pra
> 3+ espaços). Nada implementado ainda. **Próximo passo amanhã:** decidir os
> números do `spaces_max` por plano e começar pela espinha dorsal (item 4 do
> MVP: coluna `space_id NOT NULL default Pessoal` + backfill).

---

## O invariante (a decisão mais importante do projeto)

> **"Todos os espaços" é só um agrupamento visual. Toda movimentação pertence a
> um espaço real.**

Garantido no banco, não na aplicação: a coluna `space_id` em `launches` nasce
**`NOT NULL` com default = espaço Pessoal**. Assim "Todos" **nunca é um valor
gravável** — é a *ausência* de filtro numa query (`WHERE space_id = X` some).
Nenhum caminho (WhatsApp, IA, OFX, import, dashboard) consegue criar um
lançamento órfão ou "no Todos". Isso elimina a classe inteira de bug de cálculo
antes dela existir.

---

## Arquitetura: espaço = dimensão (Modelo B), não silo

Espaço é uma **etiqueta na transação** (coluna `space_id`), não um `user_id`
separado. Duas arquiteturas foram consideradas:

| | A) Espaço = `user_id` silo | **B) Espaço = coluna `space_id`** ✅ |
|---|---|---|
| Isola | dados em user_ids separados | `WHERE space_id` |
| Cartão compartilhado | ❌ fatura fragmenta entre silos | ✅ cartão é da conta; cada compra carrega o espaço; fatura inteira |
| Mover/dividir/transferir | ❌ reescreve dono da linha | ✅ operação de coluna |
| Plano/billing | precisa "subir pro dono" | ✅ trivial — só há 1 `user_id` |
| `of_banks_max` por conta | precisa somar silos | ✅ automático (OF já é keyed por `user_id`) |
| Deleção de conta (LGPD) | precisa fan-out pros silos | ✅ automático (`cascade` do `user_id`) |
| Custo | zero migração de dados | +coluna `space_id` + filtro nas queries |

**Por que B ganhou:** além de resolver cartão de fatura única e tornar
mover/transferir triviais, escolher B **dissolveu os três nós transversais** que
o modelo-silo criava — porque em B tudo continua sob **um único `user_id`** (o
espaço é sub-partição). Plano, teto de bancos OF e deleção de conta continuam
sendo "por `user_id` = por conta" **automaticamente**. O único código
transversal que resta é: **ler filtrando por `space_id` (default = todos) e
gravar `space_id` no write.**

Confirmação no schema (`db/schema.py`):
- `credit_bills` agrega `credit_transactions` **por `card_id`** — cartão tem uma
  fatura só. No Modelo A isso quebraria; em B o cartão é da conta e a compra
  leva o espaço.
- `auth_accounts` (plano, Stripe, trial) referencia `users(id)` — plano é da
  conta, e em B só existe um `user_id`, então nenhuma resolução especial.
- Tudo faz `references users(id) on delete cascade` — deleção de conta já
  alcança todo `space_id`.

---

## Modelo de dados

### Tabela nova — `financial_spaces`

```sql
create table financial_spaces (
    id            bigserial primary key,
    user_id       bigint not null references users(id) on delete cascade,
    name          text not null,            -- "Fazenda"
    emoji         text,                     -- "🌾"
    kind          text not null default 'pessoal',  -- pessoal|imovel|negocio|compartilhado (só rótulo no MVP)
    is_primary    boolean not null default false,   -- o "Pessoal", especial: não arquiva/exclui
    archived_at   timestamptz,              -- arquivado preserva histórico
    created_at    timestamptz not null default now()
);
create index on financial_spaces (user_id);
```

O espaço **Pessoal** é criado automaticamente com a conta (`is_primary=true`).

### Coluna nova — `space_id`

Em `launches` (obrigatória) e nas tabelas que o dashboard fatia por espaço:
`pockets`, `recurring_*`, `bill_instances`, `category_budgets`, categorias/regras
(isolamento total — cada espaço tem as suas). Sempre:

```sql
alter table launches
  add column space_id bigint not null
  references financial_spaces(id)
  default <id do Pessoal do usuário>;   -- backfill: tudo aponta pro Pessoal
```

**Contas e cartões continuam no nível da conta** (não recebem `space_id`): um
cartão físico tem uma fatura; a *compra* é que carrega o espaço. Um cartão pode
ter um "espaço-casa" opcional só como chute inicial do roteador.

### Transferência interna — `internal_transfer_id`

Transferir R$ 2.000 do Pessoal pra Fazenda = **duas pernas ligadas pelo mesmo
`internal_transfer_id`**, ambas `is_internal_movement=true` (coluna que **já
existe** em `launches`):

```
Pessoal  →  perna A: -R$ 2.000  "Transferência enviada"
Fazenda  ←  perna B: +R$ 2.000  "Transferência recebida"
```

- Saldo do Pessoal cai R$ 2.000; saldo da Fazenda sobe R$ 2.000.
- **Não** entra em "Gastos do mês" (Pessoal) nem "Receitas do mês" (Fazenda).
- No **consolidado o par se anula** — não altera receita/gasto/resultado.
- Afeta fluxo de caixa e saldo, **não** o desempenho financeiro.
- **Par atômico:** editar, excluir ou desfazer uma perna trata as **duas**.

### Regras aprendidas — `space_routing_rules`

Irmã da `user_category_rules`. Aprende "estabelecimento/cartão/descrição →
espaço". Campo de maturidade (`confidence_hits`): regra **provisória** roteia
com eco+desfazer; após N acertos sem correção vira **confiante** e roteia calada.
`source` = `seed` (semeada na criação) | `learned` (de correção).

---

## Comportamentos

### Espaço padrão e descoberta progressiva
- Conta nova → só **Pessoal**; **nenhum seletor aparece** no dashboard.
- Ao criar o 2º espaço → surge `Todos | Pessoal | Fazenda`.
- **"Todos"** = só visão consolidada (ver invariante).
- Descoberta do recurso (sem poluir a tela): entrada `Menu → Espaços
  financeiros [Premium]`.
- A Piggy pode **sugerir na hora certa**: *"Você mencionou despesas da fazenda
  algumas vezes. Quer separá-las num espaço próprio?"*

### Registro pelo WhatsApp (sem comando)
Roteador em camadas, para na 1ª com confiança:
0. **Override explícito** ("manda pra fazenda") — escape hatch raro.
1. **Regra aprendida/semeada** (`space_routing_rules`) — alta confiança.
2. **Contexto da conversa** (rajada de mensagens do mesmo espaço) — decai sozinho.
3. **IA por conteúdo** ("ração/diesel/veterinário" ≈ Fazenda).
4. **Padrão** → espaço ativo (default = Pessoal).

Exemplo: *"Gastei R$ 500 de combustível na fazenda"* → 🌾 Fazenda automaticamente.

### Confirmação na dúvida
Sem confiança **e** valor material → pergunta antes de registrar
(*"Esse gasto pertence a qual espaço?"* com botões), e **aprende** com a
resposta. Ambíguo e barato → cai no padrão, não enche o saco.

### Espaço ativo temporário
*"Quero organizar as despesas da fazenda"* → entra em modo Fazenda, próximos
registros vão pra lá, faixa visível `🌾 Espaço ativo: Fazenda`, **expira sozinho**
(algumas horas) ou com *"voltar ao pessoal"*.

### Espaço visível ao lançar
No "+ Lançar" (e no eco do WhatsApp), mostrar claramente onde vai ser
registrado — mesmo estado do espaço ativo, espelhado na UI. Prevenção de erro.

### Mover / reclassificar
Trocar um lançamento de espaço = `update launches set space_id`. Um campo.

### Desfazer (ação de segurança — MVP)
Depois de **registrar / mover / transferir**, oferecer *"Desfazer"* (botão no
dashboard, resposta no WhatsApp). Reusa `pending_actions` / `ai_pending_actions`.
Corrigir tem que ser **mais fácil que abrir tela de edição** — crítico porque a
IA pode interpretar o espaço errado. **Desfazer transferência reverte as duas
pernas do `internal_transfer_id`.**

### Arquivar espaço
`archived_at` no `financial_spaces`: some do seletor, não recebe lançamento
novo, mas o histórico continua contando no consolidado dos meses passados.
Preserva tudo sem obrigar a excluir. O Pessoal (`is_primary`) não arquiva.

---

## Dashboard

Seletor compacto na Visão Geral (só a partir do 2º espaço). Clicar num espaço
refiltra tudo — saldo, sobrou, gastos, receitas, categorias, gráficos. "Todos"
= consolidado, com tabela comparativa (receitas/gastos/resultado por espaço).
`authorize_dashboard_access` (`frontend/routes/shared.py:325`) **não muda** — a
autorização continua por `user_id`; o espaço é filtro de dados (`?space_id=`),
validado contra os espaços daquele usuário.

---

## Não confundir

| Camada | Responde | Exemplo |
|--------|----------|---------|
| **Espaço** | qual parte da vida | Fazenda |
| **Categoria** | com o quê gastou | Manutenção |
| **Conta / cartão** | de onde saiu | Nubank |
| **Caixinha / meta** | dinheiro guardado pra objetivo | Comprar um trator |

---

## Ordem de implementação (aprovada)

### MVP
1. Espaço **Pessoal** automático.
2. Criação e **arquivamento** de espaços.
3. Seletor **só a partir do 2º** espaço.
4. **`space_id` obrigatório** em toda movimentação (a espinha dorsal — migração
   `NOT NULL default Pessoal` + backfill; tudo de 5–8 se apoia nela).
5. **Reclassificação** para outro espaço.
6. **Transferência interna** entre espaços (`internal_transfer_id`, par atômico).
7. **Consolidado anulando transferências.**
8. Registro e **identificação pelo WhatsApp**.
- **+ Desfazer** (ação de segurança transversal a registrar/mover/transferir).

### Primeira evolução
1. Teto mensal por espaço (reusa `category_budgets` + `budget_alert_sent`).
2. Avisos da Piggy (teto estourado etc.).
3. Auto-roteamento de Open Finance / OFX pros espaços (aplica `space_routing_rules`).
4. Resumo diário/semanal/mensal por espaço (opt-in).
5. Regras sugeridas após correções repetidas.

### Mais tarde
- **Divisão de uma compra entre espaços** — via `launch_allocations` (ver abaixo).
- Espaços compartilhados (familiares/sócios).
- Permissões de visualização/edição; acesso específico pra contador.
- Agentes configurados individualmente por espaço.
- Comparações avançadas de desempenho entre espaços.

---

## Divisão de compra — `launch_allocations` (só quando for implementar)

Caso real: uma compra de R$ 1.000 no cartão em que R$ 300 são do Pessoal e
R$ 700 da Fazenda. A regra de ouro: **não** transformar a divisão em dois
`launches`. Isso duplicaria a compra, aplicaria o efeito no saldo duas vezes,
quebraria o 1:1 do `imported_launch_id` (`db/schema.py:395` —
`open_finance_transactions.imported_launch_id` referencia **um** `launches(id)`)
e espalharia a identidade do OFX/OF.

O `launch` original continua **um só** (R$ 1.000, com seu `external_id` intacto);
a divisão vira **alocações** que só descrevem *como* aquele valor se distribui
entre espaços:

```sql
create table launch_allocations (
    id         bigserial primary key,
    launch_id  bigint not null references launches(id) on delete cascade,
    space_id   bigint not null references financial_spaces(id),
    amount     numeric not null check (amount > 0),
    split_seq  smallint not null check (split_seq > 0),
    created_at timestamptz not null default now(),
    unique (launch_id, split_seq),
    unique (launch_id, space_id)
);
```

**Cálculos** — uma view unifica lançamento normal e dividido no mesmo formato,
sem reescrever os relatórios:

```sql
create view launch_space_entries as
-- sem divisão: o espaço é o do próprio lançamento
select l.id as launch_id, l.space_id, l.valor as amount, l.tipo, l.categoria, l.criado_em
  from launches l
 where not exists (select 1 from launch_allocations a where a.launch_id = l.id)
union all
-- dividido: uma linha por alocação
select l.id, a.space_id, a.amount, l.tipo, l.categoria, l.criado_em
  from launches l join launch_allocations a on a.launch_id = l.id;
```

Assim: visão Pessoal vê R$ 300, Fazenda vê R$ 700, consolidado vê R$ 1.000
(nunca R$ 2.000), e o saldo do cartão sofre o efeito **uma vez** — as alocações
mudam só *a qual espaço* o gasto pertence, não repetem o pagamento.

**Invariante:** `sum(allocation.amount) == launch.valor`, sempre. A operação roda
numa única transação com `SELECT ... FOR UPDATE`, validando (1) espaços pertencem
ao dono, (2) sem espaço repetido, (3) soma bate; depois apaga e reinsere as
alocações. Sempre `Decimal`, nunca `float`.

**Ciclo de vida** — reimportar OFX acha o mesmo `external_id` e não duplica
(só há um `launch`); desfazer divisão apaga as alocações e o lançamento volta
inteiro ao espaço original; excluir a compra apaga o `launch` e as alocações
somem por `on delete cascade`; editar substitui as alocações na transação.

Nota: como a divisão nunca cria um 2º `launch`, o índice de dedupe
`uq_launches_user_source_external (user_id, source, external_id)` **nunca é
desafiado** — o `external_id` segue identificando a compra real, e
`launch_allocations` representa só a distribuição. As ações do MVP também não o
tocam: mover é `update space_id` de um registro; transferência interna cria
pernas novas (`source='manual'`, `external_id` nulo).

---

## Plano (a confirmar)

Design anterior: campo `spaces_max` em `PlanLimits` (`core/services/plan_limits.py`)
com escada 1/1/2/3 (Free/Essencial/Plus/Pro). Free/Essencial = 1 = sem
multi-espaço (veem o badge `[Premium]` como upsell); Plus/Pro criam 2/3.
**Confirmar os números exatos antes de implementar.** Regra de custo do Open
Finance é automática no Modelo B (conexões já são por conta).

## Arquivos a tocar quando for implementar
- **novo** `db/spaces.py` — CRUD de `financial_spaces`, resolução do espaço
  ativo, regras de roteamento.
- `db/schema.py` — `financial_spaces`, `space_routing_rules`, coluna `space_id`
  (+ `internal_transfer_id` já cabe no uso de `is_internal_movement`);
  `launch_allocations` + view `launch_space_entries` (fase da divisão).
- `core/handle_incoming.py` — roteador de espaço (camadas 0–4) + espaço ativo.
- `core/services/quick_entry.py` — grava `space_id`; transferência de duas pernas.
- `frontend/finance_bot_websocket_custom.py` — endpoints filtram por `space_id`;
  consolidado ignora `is_internal_movement`.
- `frontend/dashboard.html` — seletor (a partir do 2º espaço) + "+ Lançar" com
  espaço visível + Desfazer.
- `core/services/plan_limits.py` — `spaces_max`.
- `tests/` — invariante `space_id NOT NULL`; consolidado anula transferência;
  Desfazer reverte as duas pernas; não vazar espaço entre contas.
