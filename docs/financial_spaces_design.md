# Espaços Financeiros (design, não implementado)

Objetivo: uma conta gerencia **vários perfis financeiros separados** dentro do
mesmo login — 👤 Pessoal · 🏠 Casa · 🌾 Fazenda · 🏖️ Casa de fim de semana ·
💼 Empresa. Cada espaço tem seus próprios lançamentos, saldos, categorias e
metas, mas o usuário **nunca precisa pensar em espaços**: quem não cria um
segundo espaço nem vê o recurso.

> **Gate de planos (decidido):** Espaços Financeiros é **exclusivo do plano
> Premium** (R$ 150/mês). **Todos os outros planos** (Grátis, Essencial, Plus,
> Pro) têm **apenas 1 espaço — o Pessoal** — e nem veem o seletor. **Não existe
> escada `spaces_max` 1/1/2/3**; o gate é binário (`spaces_enabled`), e no
> Premium os espaços são ilimitados. Ver seção "Plano".

Alinha com a promessa do produto ("fale como você fala", sem planilha nem
comando): a Piggy identifica o espaço pela própria frase e organiza sozinha.

Status: **design congelado, não implementado.** `grep` por `space_id`,
`financial_spaces` e `reallocation_id` no repositório volta vazio.

Previews navegáveis (privados):
- Fluxos (WhatsApp + dashboard + criação): `claude.ai/code/artifact/c68ab510-d317-475b-881f-47704a487d7f`
- Seletor de espaço ao vivo na Visão Geral: `claude.ai/code/artifact/8e8f9ae7-e621-4c91-9b16-d61bb08ee9ca`
- Dividir uma compra entre espaços: `claude.ai/code/artifact/09ced5a6-3327-478a-bb32-5ff57f311456`

> **Onde paramos:** design de produto + arquitetura congelados (Modelo B,
> `launch_allocations`, ordem de implementação aprovada). A UX da divisão foi
> resolvida no preview: **digitar N−1 valores, o último espaço é o "restante"
> automático**. **Planos: decidido — Espaços é EXCLUSIVO do Premium (R$150),
> ilimitado; todos os outros planos = 1 espaço (Pessoal). Não há escada
> `spaces_max`** (gate binário `spaces_enabled`). Nada implementado ainda.
> **Próximo passo:** criar o tier Premium + a espinha dorsal (item 1 do MVP:
> `financial_spaces` + Pessoal + `space_id NOT NULL` + backfill).

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
| Mover/dividir/realocar | ❌ reescreve dono da linha | ✅ operação de coluna |
| Plano/billing | precisa "subir pro dono" | ✅ trivial — só há 1 `user_id` |
| `of_banks_max` por conta | precisa somar silos | ✅ automático (OF já é keyed por `user_id`) |
| Deleção de conta (LGPD) | precisa fan-out pros silos | ✅ automático (`cascade` do `user_id`) |
| Custo | zero migração de dados | +coluna `space_id` + filtro nas queries |

**Por que B ganhou:** além de resolver cartão de fatura única e tornar
mover/realocar triviais, escolher B **dissolveu os três nós transversais** que
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

#### O que recebe `space_id` (por-espaço) vs. o que fica na conta

Isolamento total = **os dados financeiros** ganham `space_id`; **identidade,
plano e os "contêineres" físicos** ficam na conta.

| Recebe `space_id` (por-espaço) | Fica na conta (sem `space_id`) |
|---|---|
| `launches` (o núcleo) | `credit_cards` / `credit_bills` / `credit_transactions` — 1 fatura por cartão; a *compra* (`launch`) carrega o espaço |
| `pockets` (caixinhas/metas) | `accounts` — o cartão/conta tem `default_space_id` opcional (espaço-casa) |
| `recurring_*` (recorrências) | `auth_accounts`, `user_identities`, sessões — identidade/plano |
| `bill_instances` (contas a pagar) | `open_finance_connections` — conexão é por conta (regra de custo) |
| `category_budgets` (orçamentos) | `investments` — **fica na conta** (decidido: a fazenda em si já é o investimento; raro ter investimento escopado a um espaço) |
| `user_categories` / `user_category_rules` | |

#### Migração e backfill (a ordem importa)

O `NOT NULL default Pessoal` **não** é um literal — o default é o id do Pessoal
*de cada usuário*. Então a migração é em 4 passos, idempotentes:

```
1. criar financial_spaces + semear 1 Pessoal (is_primary) pra CADA usuário existente
2. alter table launches add column space_id bigint  (NULLABLE, sem default)
3. backfill: update launches set space_id = <pessoal daquele user_id>  (em lotes)
4. alter table launches alter column space_id set not null
   + add constraint FK → financial_spaces(id)
```

Mesmo padrão pras outras tabelas por-espaço. Rodar atrás do flag (ver Segurança).

#### Índices

Hoje o índice quente é `idx_launches_user_time (user_id, criado_em desc)`. As
queries agora filtram por espaço, então precisa do **composto**:

```sql
create index idx_launches_user_space_time on launches (user_id, space_id, criado_em desc);
```
(idem nas outras tabelas por-espaço que o dashboard fatia).

### O que é o "saldo de um espaço" — dois conceitos (decidido)

Problema real: as **contas/cartões ficam no nível da conta**, mas os espaços têm
"saldo próprio". Se o Nubank tem R$ 10.000 e a pessoa cria 👤 Pessoal + 🌾 Fazenda,
o PigBank **não tem como adivinhar** quanto é de cada um. Isso aparece **antes**
de qualquer realocação. Solução: separar explicitamente dois conceitos na UI —

- **Saldo bancário** — o real, nas contas: "Você tem R$ 10.000 nas suas contas."
- **Saldo organizado por espaços** — **alocação interna do patrimônio**, não
  dinheiro de banco: 👤 R$ 6.500 · 🌾 R$ 3.500. O espaço **não tem conta própria**;
  o saldo dele é um recorte virtual do total.

**Invariante de reconciliação:** `Σ(saldo dos espaços) == patrimônio total`
(= saldo bancário consolidado). Os espaços **repartem** o mesmo dinheiro; nunca
criam nem somem com dinheiro.

**Saldo inicial de um espaço novo = recorte do Pessoal.** Ao criar um espaço, o
PigBank pergunta quanto separar, e esse valor **sai do Pessoal** (uma realocação),
mantendo a soma constante — Pessoal R$ 10.000 → R$ 7.000, Fazenda R$ 0 → R$ 3.000
(total ainda R$ 10.000):

```
🌾 Fazenda criada
Quer separar algum dinheiro pra ela?   R$ ______   [ Agora não ]
```

Assim o espaço nasce com saldo explícito (nunca um número que o app adivinha), e
"Agora não" = nasce zerado (a pessoa realoca depois). É **alocação de patrimônio,
não saldo bancário real** — exatamente como você preferiu.

### Realocar entre espaços — `reallocation_id` (NÃO é transferência bancária)

Mover R$ 2.000 do Pessoal pra Fazenda **não move dinheiro de banco nenhum** — é
uma **realocação contábil interna** do PigBank. Por isso a UI **não** chama de
"Transferência" (evita a pessoa achar que o app fez uma TED/PIX): usar
**"Realocar entre espaços"** ou, mais simples, **"Mover R$ 2.000 para Fazenda"**.

Mecânica: **duas pernas ligadas pelo mesmo `reallocation_id`**, ambas
`is_internal_movement=true` (coluna que **já existe** em `launches`):

```
Pessoal  →  perna A: -R$ 2.000  "Realocação enviada"
Fazenda  ←  perna B: +R$ 2.000  "Realocação recebida"
```

- Muda o **saldo organizado** de cada espaço; **não** toca no saldo bancário.
- **Não** entra em "Gastos do mês" (Pessoal) nem "Receitas do mês" (Fazenda).
- No **consolidado o par se anula** — não altera receita/gasto/resultado.
- **Par atômico:** editar, excluir ou desfazer uma perna trata as **duas**.

**Aporte em caixinha ≠ realocação (decidido).** Aportar numa caixinha/meta —
mesmo que ela seja de outro espaço (ex.: caixinha "trator" na Fazenda) — é um
**aporte simples**: um movimento interno de **uma perna só** (`is_internal_movement`,
não conta como gasto/receita), **não** cria par `reallocation_id`.

Irmã da `user_category_rules`. Campo de maturidade (`confidence_hits`): regra
**provisória** roteia com eco+desfazer; após N acertos sem correção vira
**confiante** e roteia calada. `source` = `seed` (semeada na criação) | `learned`
(de correção).

**Regra por COMBINAÇÃO, não só merchant→espaço (decidido).** "Posto Shell →
Fazenda" é perigoso: amanhã a pessoa abastece o carro pessoal no mesmo posto. As
regras usam **combinações de sinais** quando dá — `estabelecimento + cartão +
descrição + histórico` — não só o merchant. Assim `Posto X + cartão empresarial
→ Fazenda` fica **altíssima confiança**, enquanto `Posto X + Nubank pessoal`
segue ambíguo (não cria regra automática, ou cai em confirmação). Modelagem:
`match_kind` composto e/ou colunas opcionais (`card_id`, trecho da descrição) na
chave da regra; quanto mais sinais batem, maior a confiança.

---

## Comportamentos

### Espaço padrão e descoberta progressiva
- Conta nova → só **Pessoal**; **nenhum seletor aparece** no dashboard.
- Ao criar o 2º espaço → surge `Todos | Pessoal | Fazenda`.
- **"Todos"** = só visão consolidada (ver invariante).
- Descoberta do recurso (sem poluir a tela): entrada `Menu → Espaços
  financeiros [Premium]`.
- **Sugestão proativa da Piggy (upsell — decidido).** Fluxo:
  1. **Ensinar a IA o conceito** de Espaço Financeiro (camada de conhecimento —
     o que é, quando faz sentido separar).
  2. **Detectar nas categorias/gastos** um **cluster que parece um contexto
     próprio** (ração/trator/insumo → "fazenda"; energia/condomínio de outro
     lugar → "imóvel"). A detecção usa os dados que a pessoa já tem, não pede nada.
  3. **Sugerir no máximo 1×/mês**, ancorado na **renovação do plano** (o momento
     natural de decidir o upgrade): *"Notei gastos que parecem de uma fazenda.
     Com o Premium você separa isso num espaço próprio. Quer ver?"*
  Frequência dura (1/mês perto da renovação) evita spam e cai na hora da decisão
  de billing. Pra quem já é Premium, a mesma detecção vira só descoberta de
  feature ("quer criar um espaço pra isso?"), sem upsell.

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

### Espaço ativo temporário (lembrete discreto, não burocrático)
*"Quero organizar as despesas da fazenda"* → entra em modo Fazenda, próximos
registros vão pra lá. **Anuncia uma vez na ativação** (*"🌾 Vou considerar seus
próximos lançamentos como Fazenda."*) e depois **não fica repetindo "Espaço
ativo"** a cada mensagem — senão vira a burocracia que a gente quer evitar. Em
vez disso, cada confirmação **mostra o espaço discretamente** junto do lançamento:

```
🐷 R$ 350 · Veterinário
🌾 Fazenda
```
**Expira sozinho** (algumas horas) ou com *"voltar ao pessoal"*.

### Espaço visível ao lançar
No "+ Lançar" (e no eco do WhatsApp), mostrar claramente onde vai ser
registrado — mesmo estado do espaço ativo, espelhado na UI. Prevenção de erro.

### Mover / reclassificar
Trocar um lançamento de espaço = `update launches set space_id`. Um campo.

### Desfazer (ação de segurança — MVP)
Depois de **registrar / mover / realocar**, oferecer *"Desfazer"* (botão no
dashboard, resposta no WhatsApp). Reusa `pending_actions` / `ai_pending_actions`.
Corrigir tem que ser **mais fácil que abrir tela de edição** — crítico porque a
IA pode interpretar o espaço errado. **Desfazer realocação reverte as duas
pernas do `reallocation_id`.**

### Arquivar espaço
`archived_at` no `financial_spaces`: some do seletor, não recebe lançamento
novo, mas o histórico continua contando no consolidado dos meses passados.
Preserva tudo sem obrigar a excluir. O Pessoal (`is_primary`) não arquiva.

### Deletar espaço (decidido)
Deletar **não perde dado**: tudo do espaço (lançamentos, alocações, caixinhas,
recorrências…) é **reatribuído ao Pessoal** numa transação —
`update ... set space_id = <pessoal>` — e só então a linha do `financial_spaces`
é removida. Confirmação obrigatória ("os lançamentos vão pro Pessoal"). O Pessoal
(`is_primary`) nunca é deletável. Assim nunca há `space_id` órfão nem dado
sumindo. (Arquivar continua sendo a opção "guardar separado"; deletar é "fundir
no Pessoal".)

### Downgrade de Premium → espaços dormentes, não deletados (decidido)
Requisito: downgrade seguido de upgrade tem que **restaurar a separação
automaticamente**. Isso é **incompatível com deletar/fundir de verdade** (a fusão
apaga o `space_id` de cada lançamento — não há como reconstruir). A solução
aproveita o Modelo B: **espaço é só uma coluna**, então "perder os espaços" =
**desligar o seletor**, não mexer nos dados.

1. **Carência até a próxima cobrança** — ao cair o Premium tudo segue funcionando
   normal (o período corrente está pago).
2. **Cobrança falhou → dormência** — o seletor e as visões por espaço são
   **desligados**; o app volta à visão única/consolidada (a pessoa vê todo o
   dinheiro, só não separado). **Nada se move** — o `space_id` continua em cada
   lançamento, dormente. Novos lançamentos no período sem Premium caem no Pessoal.
3. **Re-upgrade → religa automático** — o seletor volta e a separação reaparece
   instantânea, **zero migração**. Lançamentos do período dormente ficam no
   Pessoal (a pessoa reclassifica se quiser).

Fusão-de-verdade (merge irreversível no Pessoal) fica **só** para a ação manual
de **deletar espaço** — intencional e explícita (ver acima).

---

## Dashboard

Seletor compacto na Visão Geral (só a partir do 2º espaço). Clicar num espaço
refiltra tudo — saldo, sobrou, gastos, receitas, categorias, gráficos. "Todos"
= consolidado, com tabela comparativa (receitas/gastos/resultado por espaço).
`authorize_dashboard_access` (`frontend/routes/shared.py:325`) **não muda** — a
autorização continua por `user_id`; o espaço é filtro de dados (`?space_id=`),
validado contra os espaços daquele usuário.

---

## Endpoints / API

Novos (CRUD de espaço + operações):

| Método | Rota | Função |
|---|---|---|
| GET | `/spaces/{user_id}` | lista os espaços da conta (+ resultado do mês de cada) |
| POST | `/spaces/{user_id}` | cria espaço (nome, emoji, kind) — gate Premium |
| PATCH | `/spaces/{user_id}/{space_id}` | renomeia / troca emoji / arquiva (`archived_at`) |
| DELETE | `/spaces/{user_id}/{space_id}` | deleta → reatribui lançamentos ao Pessoal |
| POST | `/launches/{id}/move` | reclassifica um lançamento (`set space_id`) |
| POST | `/spaces/reallocations` | realocação entre espaços (2 pernas, `reallocation_id`) — **não** é "transfer" (evita ambiguidade com TED/PIX) |
| POST | `/launches/{id}/split` | divisão (`launch_allocations`) — fase da divisão |

Existentes ganham `?space_id=` **opcional** (ausente = consolidado):
`/data/{user_id}`, `/history/{user_id}`, `/export/{user_id}`, `/ws/{user_id}`.
Modelos Pydantic pra todo body (convenção do projeto).

## Segurança e rollout

- **`space_id` sempre validado como do dono no write** (garantia decidida): todo
  endpoint/handler que grava confere que o `space_id` pertence ao `user_id`
  autenticado — `where space_id = %s and user_id = %s` (ou um
  `assert_space_owned(user_id, space_id)` central). Sem isso, alguém forjaria um
  request com `space_id` de outra conta. O filtro de leitura já é seguro (roda
  dentro do `user_id`), mas o write precisa da checagem explícita.
- **Feature flag `SPACES_ENABLED`** — sobe "dark" (schema/migração aplicados,
  UI e roteamento desligados), liga sem deploy, e dá rollback seguro. Gate de
  plano (`spaces_enabled` do Premium) é ortogonal ao flag global.
- Só WhatsApp (o **Discord foi descontinuado** — não há superfície Discord a
  cobrir).

## Agentes + Espaços (a decidir — impacta a experiência do cliente)

**Deixado para depois, de propósito.** Como agentes e espaços convivem afeta
diretamente a experiência, então merece decisão dedicada — **não entra no MVP**.
Opções em aberto (a avaliar):
- agentes rodam na conta com a **saída segmentada por espaço** (space-aware);
- **instância de agente por espaço**, com agenda/config próprias (Premium, slots
  no `agents_max`);
- **criação livre de agentes por espaço**.
Decidir antes de implementar qualquer cruzamento agentes × espaços.

## Identificação do espaço — o roteador `infer_space`

Como a IA decide o espaço **não é adivinhação solta**: é a **gêmea** de
`infer_category` (`core/services/category_service.py:162`), que já roda em
produção pra categoria. Cascata de prioridade determinística-primeiro,
IA-último, cada resultado com um `reason` (espelha o `InferResult`):

```python
def infer_space(user_id, texto, *, active_space) -> SpaceResult:
    # A) menção explícita → "...na fazenda", "fazenda: ...", "#fazenda"
    if s := explicit_space_mention(user_id, texto):
        return SpaceResult(s, reason="explicit")
    # B) regra aprendida/semeada → space_routing_rules (merchant | keyword | categoria)
    if s := memorized_space(user_id, normalize(texto)):
        return SpaceResult(s, reason="rule")
    # C) contexto ativo da conversa (só WhatsApp; decai sozinho)
    if active_space:
        return SpaceResult(active_space, reason="active")
    # D) IA (LLM) — SÓ se A–C falharem; classificação FECHADA nos espaços da conta
    if allow_ai:
        s, conf = classify_space_with_gpt(texto, spaces=list_spaces(user_id))
        if conf >= LIMIAR: return SpaceResult(s, reason="ai")
        return SpaceResult(None, reason="ambiguous")   # → pergunta ao usuário
    # default → espaço primário (Pessoal)
    return SpaceResult(primary_space(user_id), reason="default")
```

Princípio: **a IA é o fallback, não o padrão.** A maioria das mensagens resolve
em A–C (barato, rápido, previsível). A camada D recebe **os espaços da conta**
(nome, emoji, tipo, palavras-semente) como contexto — é classificação fechada,
não aberta. Confiança baixa → **não chuta, pergunta** (botões `👤 · 🌾 · 🏖️`) e
**aprende** com a resposta.

**Aprendizado (o que melhora com o uso):** toda correção/resposta vira linha em
`space_routing_rules` — mesmo mecanismo do `user_category_rules` de hoje. A regra
tem maturidade (`confidence_hits`): provisória ecoa com desfazer; após N acertos
vira confiante e roteia calada. Quanto mais uso, menos a IA precisa pensar.

**Rapidez e custo da camada de IA (decidido: otimizar por embeddings).** O
objetivo é a IA quase **não** ser chamada — 80–90% resolve em A–C
(determinístico, instantâneo, grátis). Pra os casos novos (camada D), em vez de
um LLM proponho **embeddings de similaridade**: pré-computa o embedding das
palavras-semente de cada espaço uma vez, e compara por cosseno com o embedding da
descrição — **milissegundos, ~10× mais barato que LLM**, e melhora conforme
semeia. LLM (gpt-4o-mini) fica só pro empate genuíno. No OF, **classificar em
lote** (várias transações numa chamada). **Custo:** desprezível — cache-miss de
LLM ≈ US$0,003/usuário/mês (~20 chamadas de ~500 tokens); embeddings ≈ 10× menos.
Perto da IA conversacional do app, é ruído.

## Open Finance + Espaços

Com OF o fluxo é o **inverso** do WhatsApp: a transação chega sozinha (vira
`launch` com `imported_launch_id`), sem ninguém digitando. Regras:

1. **Nunca bloqueia.** Todo lançamento importado cai num espaço na hora, com o
   melhor palpite; nada fica em limbo, ninguém "espera categorização".
2. **Espaço-casa do cartão/conta.** Cartão/conta são do nível da conta (Modelo
   B) e ganham um `default_space_id` opcional. A conta do banco da fazenda →
   nasce na Fazenda. Elimina a maioria das dúvidas **sem IA e sem pergunta**.
3. **`infer_space` sem contexto de conversa.** Pro OF, roda merchant/descrição
   pela cascata **B → espaço-casa → D**, defaultando ao primário. A camada C
   (ativo) não se aplica (não há conversa).
4. **Ambíguos → revisão em LOTE, não pergunta a pergunta.** Marcados "a revisar"
   (o `reconciliation_status` do OF já modela estados). A pessoa resolve num
   badge no dashboard ("🌾 4 pra confirmar") ou num resumo semanal no WhatsApp.
   Cada correção vira regra → o monte encolhe sozinho.

**Cartão/conta compartilhado entre 2 espaços** (ex.: Pessoal + Casa de campo) — o
caso mais difícil. O espaço-casa vira só o *piso*; a separação real vem dos
**estabelecimentos**, que diferem entre os espaços (o mercado da cidade do sítio,
o condomínio, a luz daquele imóvel são um conjunto próprio e estável). A
**semeadura na criação** do espaço (dar merchants/keywords típicos) front-carrega
as regras e faz o cartão compartilhado já sortear bem desde a 1ª fatura. O resto
cai no piso "a revisar" e treina em 2–3 faturas. Compra genuinamente mista →
**divisão** (`launch_allocations`). Lever futuro (não-MVP): "espaço ativo por
janela de tempo" (fim de semana no sítio → gastos da janela vão pra Casa de
campo).

**Sinal de região via CNPJ (não é GPS).** A Pluggy/Open Finance **não** manda
localização física da compra (sem lat/long, sem endereço do ponto de venda). O
que vem no objeto `merchant` é `name`, `businessName`, **`cnpj`** e `cnae` — e
tudo isso **já está armazenado** no `raw jsonb` de `open_finance_transactions`
(hoje não é extraído pra colunas). O caminho pra "cidade" é o **CNPJ →
município**: cada filial tem CNPJ próprio (raiz + sufixo `/0001`, `/0002`…), e um
lookup (Receita/BrasilAPI, ou a Merchants API / Enrichment da própria Pluggy)
devolve cidade/UF. Serve pra separar cartão compartilhado (merchants da cidade do
sítio → Casa de campo) e alimenta o lever "contexto de região". Ressalvas: PIX/PF
e saques não têm CNPJ; cobertura do enrichment varia (pode ser paga); endereço
registrado ≠ ponto exato, mas acerta a cidade na maioria; é chamada extra
(cachear por CNPJ). **Evolução, não MVP** — o roteamento por merchant/nome já
separa bem sem depender de resolver localização.

**Efeito estratégico:** OF **reduz** as mensagens no WhatsApp — o extrato
substitui a digitação. O WhatsApp deixa de ser canal de registro e vira
correção + confirmação em lote + conversa. Menos volume no WhatsApp não-oficial
(menos risco de bloqueio) e menos custo de LLM por mensagem.

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

### MVP — ordem por dependência (fundação primeiro)
Reordenado pra **não existir período em que parte do sistema conhece Espaços e
parte não**. Não muda o produto, só a sequência de execução:

1. **Banco** — `financial_spaces` + Pessoal automático + `space_id NOT NULL` +
   backfill (a espinha dorsal; ver Migração).
2. **Todos os writes** passam a gravar `space_id`.
3. **Todas as queries** sabem filtrar por espaço (default = consolidado).
4. **CRUD de espaços** (criar/renomear/arquivar/deletar→Pessoal).
5. **Seletor** no dashboard (só a partir do 2º espaço).
6. **Mover lançamento** (reclassificar — `update space_id`).
7. **Realocar entre espaços** (`reallocation_id`, par atômico, consolidado anula).
8. **WhatsApp inteligente** (roteador `infer_space` + espaço ativo + confirmação).
9. **Desfazer** (ação de segurança sobre registrar/mover/realocar).

> **Este documento é o contrato do MVP.** Escopo congelado — seguir a ordem
> acima, sem aumentar. Novas ideias vão pra "Primeira evolução"/"Mais tarde",
> não pro MVP.

### Gate do 1º merge (definição de pronto)
Nada entra antes de uma bateria de testes cobrindo os **invariantes que não podem
quebrar** (detalhe dos casos na lista de `tests/` em "Arquivos a tocar"):
- `space_id` **sempre** pertence ao usuário correto (não vaza entre contas);
- **nenhum lançamento órfão** (`space_id NOT NULL`);
- **consolidado sem dupla contagem** (divisão conta 1×, realocação se anula);
- **realocações atômicas** (as duas pernas juntas);
- **`Σ(saldos organizados) == patrimônio total`** reconciliando sempre;
- **downgrade preserva** os espaços (dormência) e **re-upgrade restaura** tudo.

### Fora de escopo AGORA (não mexer no MVP)
Agentes × Espaços · compartilhamento/permissões · divisão avançada de compra ·
região por CNPJ. São boas evoluções, mas distraem do núcleo — ficam pra depois.

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
    id          bigserial primary key,
    launch_id   bigint not null references launches(id) on delete cascade,
    space_id    bigint not null references financial_spaces(id),
    amount      numeric not null check (amount > 0),
    category_id bigint,             -- categoria DA alocação (categorias são por-espaço)
    split_seq   smallint not null check (split_seq > 0),
    created_at  timestamptz not null default now(),
    unique (launch_id, split_seq),
    unique (launch_id, space_id)
);
```

**Por que `category_id` na alocação (decidido, mesmo a divisão sendo "mais
tarde"):** categorias são **por-espaço**, então cada pedaço da divisão pode ter
categoria própria — R$ 300 Pessoal = *Alimentação*, R$ 700 Fazenda = *Insumos*.
A categoria do `launch` original pode nem existir no outro espaço. Deixar o
campo pronto agora (nullable → cai na categoria do `launch` quando vazio) evita
migrar a tabela depois, quando os splits ficarem mais sofisticados.

**Cálculos** — uma view unifica lançamento normal e dividido no mesmo formato,
sem reescrever os relatórios:

```sql
create view launch_space_entries as
-- sem divisão: o espaço é o do próprio lançamento
select l.id as launch_id, l.space_id, l.valor as amount, l.tipo, l.categoria, l.criado_em
  from launches l
 where not exists (select 1 from launch_allocations a where a.launch_id = l.id)
union all
-- dividido: uma linha por alocação (categoria da alocação, senão a do launch)
select l.id, a.space_id, a.amount, l.tipo, coalesce(a.category_id::text, l.categoria), l.criado_em
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
tocam: mover é `update space_id` de um registro; realocação interna cria
pernas novas (`source='manual'`, `external_id` nulo).

---

## Plano — Espaços é exclusivo do Premium (decidido)

Espaços Financeiros vão para um **plano novo, Premium — R$ 150/mês** (a ser
criado), que é o **topo da escada = superset do Pro** (tudo do Pro + Espaços
ilimitados + consolidado; Pro é R$ 49,90).

> ⚠️ **Composição do Premium ainda em definição.** Espaços Financeiros é **UMA**
> das funcionalidades do Premium, **não o plano inteiro**. O conjunto completo de
> benefícios do Premium (e o posicionamento/preço final) ainda está sendo
> pensado — este doc trata só da parte "Espaços". O gate (`spaces_enabled` só no
> Premium) vale independente do resto da composição.

Decisões (sobre Espaços):

- **Pré-requisito:** o Premium **ainda não existe no código** (`plan_limits.py`
  diz "Premium engavetado, sem tier no código"; `TIER_ORDER` só vai até `pro`).
  Criar Espaços depende de nascer o tier: `premium` no `TIER_ORDER` (rank 4),
  `PREMIUM_LIMITS`, produto/preço no Stripe.
- **Gate binário (`spaces_enabled`), sem escada por plano.** Não há `spaces_max`
  por tier. Só Premium = `true` → espaços **ilimitados** (sem limite comercial
  aparente; só um **teto técnico alto anti-abuso**, ex. 50). Grátis, Essencial,
  Plus e Pro = `false` → **só o Pessoal**, sem seletor, apenas o badge
  `[Premium]` de descoberta/upsell.
- Regra de custo do Open Finance continua automática no Modelo B (conexões já
  são por conta, não por espaço).

## Arquivos a tocar quando for implementar
- **novo** `db/spaces.py` — CRUD de `financial_spaces`, resolução do espaço
  ativo, regras de roteamento.
- `db/schema.py` — `financial_spaces`, `space_routing_rules`, coluna `space_id`
  (+ `reallocation_id` já cabe no uso de `is_internal_movement`);
  `launch_allocations` + view `launch_space_entries` (fase da divisão).
- `core/handle_incoming.py` — roteador de espaço (`infer_space`, camadas A–D) + espaço ativo.
- `core/services/quick_entry.py` — grava `space_id`; realocação de duas pernas.
- `frontend/finance_bot_websocket_custom.py` — endpoints filtram por `space_id`;
  consolidado ignora `is_internal_movement`.
- `frontend/dashboard.html` — seletor (a partir do 2º espaço) + "+ Lançar" com
  espaço visível + Desfazer.
- `core/services/plan_limits.py` — tier `premium` + `spaces_enabled`.
- `tests/` — **os dois invariantes críticos** (nunca podem quebrar):
  **(a)** `space_id NOT NULL`; **(b)** `Σ(saldos dos espaços) == patrimônio total`.
  Suíte pesada pra (b) — cada caso confere que a soma continua batendo:
  1. criar espaço com R$ 0;
  2. criar espaço separando R$ 3.000 (recorte do Pessoal);
  3. realocar Pessoal → Fazenda;
  4. realocar Fazenda → Pessoal;
  5. desfazer realocação (reverte as duas pernas);
  6. excluir espaço (funde no Pessoal);
  7. downgrade (dormência — soma inalterada);
  8. upgrade (religa — soma inalterada);
  9. importação OF durante a dormência;
  10. saldo bancário muda via Open Finance;
  11. compra reclassificada de espaço.
  Mais: consolidado anula realocação; write rejeita `space_id` de outra conta;
  cascata do `infer_space`. (Rodar quando tudo estiver pronto.)

## A detalhar depois (pendências abertas)
- **Comandos do bot** (WhatsApp): "criar espaço X", "espaço fazenda", "voltar ao
  pessoal", "mover último pra fazenda", "dividir…" — mapear todos os comandos e
  seus sub-termos/sinônimos.
- **Agentes × Espaços** — decisão dedicada (impacta a experiência; ver seção).
- **Espaço ativo (WhatsApp):** storage (coluna vs tabela) + timeout exato.
- **Região por CNPJ→município** (evolução, ver OF + Espaços).
- Validar na prática **embeddings vs LLM** no roteamento.
