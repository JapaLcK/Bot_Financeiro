# Design — Plano Premium com Modo Família

> Status: **proposta** (rev. 2026-08-12). Ainda não implementado. O Premium hoje
> é só um card "Em breve" na `/precos` (`frontend/precos.html:122-135`) com o
> bullet "Modo família", sem tier no código nem modelo de dados.
>
> Este documento tem duas partes: **(1) design de produto** — como funciona pra
> quem usa — e **(2) esboço de implementação** — onde encosta no código atual.
> Ao final, **(3) análise de custo** de Open Finance por família.

---

## 1. Contexto

### O que já existe hoje

- **Escada de planos v2** (`core/services/plan_limits.py`): `free < essencial <
  plus < pro`, ranqueada por `TIER_ORDER`. Cada tier é um `PlanLimits` (TypedDict)
  com limites numéricos e flags de feature. Fonte única de verdade.
- **Resolução de tier** (`core/services/plan_service.py`): `get_plan_tier(user_id)`
  lê `auth_accounts.plan` (coluna texto) → mapeia via `_STORED_PLAN_TO_TIER` →
  confere vigência com `plan_expires_at`. O trial é uma assinatura Stripe
  `trialing` do próprio plano.
- **Gate de Open Finance** (`frontend/routes/open_finance.py:121-160`): o teto
  `of_banks_max` do tier é comparado com `count_open_finance_connections(user_id)`
  — ou seja, **conta bancos POR usuário**. É aqui que o pool por família mexe.
- **Gate de plano-selecionado** (`plan_service.needs_plan_selection`): depois de
  criar a conta, o usuário é **obrigado a passar pela `/precos`** e escolher um
  plano antes de entrar no dashboard, a menos que `plan_selected_at` esteja
  preenchido ou ele já tenha assinatura vigente. **Este gate é o que precisa ser
  desviado pro convidado de família.**
- **Dados por usuário**: TODA tabela de dados financeiros é keyed por `user_id`
  (`launches`, `pockets`, `credit_cards`, `open_finance_connections`, etc.).
  **Não existe conceito de conta compartilhada.** Uma conta = um `user_id` = um
  titular.
- **Billing**: Stripe. `auth_accounts` tem `stripe_customer_id`,
  `plan_expires_at`, `last_payment_status`. Webhooks atualizam a coluna `plan`.

### O problema que o Modo Família resolve

Casais/famílias hoje precisam de **uma conta por pessoa** e pagam **N
assinaturas**, sem nenhuma visão conjunta do dinheiro da casa. O Modo Família
entrega, num único plano Premium:

1. **Uma assinatura** que cobre o titular + membros convidados.
2. **Visão consolidada opcional** das finanças da casa (só agregados).
3. **Privacidade preservada** — cada membro continua dono dos seus próprios
   dados; compartilhar é opt-in, explícito e granular.

---

## 2. Design de produto

### 2.1 Papéis

| Papel | Quem é | Pode |
|-------|--------|------|
| **Titular** (owner) | Quem assina o Premium e paga | Convidar/remover membros, ver o painel da família, encerrar a família, gerenciar billing |
| **Membro** (member) | Convidado que aceitou | Usar o Premium na conta dele, escolher o que compartilha, sair da família |

Regras:
- **1 titular por família.** Transferência de titularidade é fase 2 (não no MVP).
- Um `user_id` participa de **no máximo uma família** por vez (como titular OU
  como membro, nunca os dois).
- Sair/ser removido **não apaga dados** — só desfaz o vínculo e rebaixa o tier
  daquele membro (ver 2.6: **volta pro Grátis**).

### 2.2 Tamanho da família

- Premium é uma família de **3 pessoas no total** — o titular **+ até 2 membros**
  convidados.
- Vira o limite `family_members_max = 3` (assentos totais, titular incluído) no
  `PlanLimits`. Convite excedente é bloqueado com mensagem clara ("Sua família
  já tem 3 pessoas").

### 2.3 Fluxo de convite — e o redirecionamento pós-cadastro

1. Titular assina o Premium → família é criada automaticamente (ele vira owner).
2. No dashboard (aba **Família**), o titular convida por **e-mail** ou
   **telefone (E.164)** — as chaves que já identificam contas.
3. Gera um **convite com token** (expira em 7 dias). Envio por e-mail
   (`core/services/email_service.py` já tem templates) e/ou link no WhatsApp.
4. Convidado:
   - **Já tem conta PigBank** → aceita com 1 clique; o `user_id` dele entra como
     membro e o tier vira Premium na hora.
   - **Não tem conta** → cadastra a partir do link do convite. **Aqui vale a
     regra crítica abaixo.**
5. Convite tem estados: `pending → accepted | declined | expired | revoked`.

> 🚩 **Regra crítica (pedido do produto): o convidado que se cadastra NÃO pode
> cair na `/precos`.** No fluxo normal, todo cadastro novo é forçado à seleção de
> plano (`needs_plan_selection` → `/precos`). Pro convidado de família isso está
> **errado** — ele não vai escolher plano nenhum, o Premium vem da família.
>
> **Solução:** ao aceitar o convite (inclusive no cadastro vindo do link), a
> aceitação **carimba `plan_selected_at = now()`** na `auth_accounts` do membro.
> Com isso `needs_plan_selection` retorna `False` e ele é **redirecionado direto
> pro `/dashboard`**, já com o tier Premium derivado ativo. O token do convite
> viaja pelo cadastro (querystring/estado) e é consumido no fim do signup.

### 2.4 Privacidade dos dados — escolha clara e desde o começo

**Princípio: dados são privados por padrão.** Entrar numa família **não** expõe
nada automaticamente. O que a família habilita é uma **camada de
compartilhamento opt-in** — e essa escolha tem que ser **explícita, clara e
apresentada logo na entrada**, não escondida em configurações.

**Onde a escolha aparece:** é um **passo obrigatório do onboarding na família**
(na hora de aceitar o convite, antes de concluir). Uma tela objetiva, sem
juridiquês, mostrando exatamente o que entra e o que nunca entra:

> **Compartilhar sua visão com a família?**
>
> ✅ **Se você ativar, a família vê (só números somados):**
> - Seu saldo total e patrimônio consolidado
> - Total de gastos e receitas do mês
> - Gastos agregados por categoria (ex.: "Alimentação: R$ 800")
> - Progresso das suas metas
>
> 🔒 **A família NUNCA vê, em nenhum caso:**
> - Suas transações individuais (nome de loja, valor exato de cada compra)
> - Seus cartões e credenciais de banco
> - Suas conversas com a Piggy
>
> [ Manter privado ]   [ Compartilhar visão ]

Modos (o membro escolhe o dele e **pode mudar quando quiser** na aba Família):

| Modo | O que o resto da família vê |
|------|------------------------------|
| **Privado** (default) | Nada. Só ganha o tier Premium; dados invisíveis pra família. |
| **Compartilhar visão** | Apenas os **agregados** acima entram no painel da família. |

**Somente agregados são compartilhados — sem exceção no produto.** Não há
drill-down transação-a-transação entre membros (nem em fase futura, salvo
decisão explícita de produto). Essa camada se apoia em `db/privacy.py` (LGPD/PII)
e no gate `consolidated_balance_enabled` (que já soma Carteira + bancos OF).

### 2.5 Limites do tier Premium

Premium herda tudo do Pro (`PRO_LIMITS`); em relação ao Pro, mudam **duas**
coisas: ganha **família** e o Open Finance passa a ser um **pool por família**
(ver 2.5.1 e a análise de custo na seção 3):

| Limite/feature | Pro (hoje) | **Premium (novo)** |
|----------------|-----------|--------------------|
| Bancos Open Finance | 5 por conta | **Pool de 10 por família** (compartilhado; ~3 por pessoa) |
| Membros de família | — | **3 pessoas** (titular + 2) |
| Painel da família | — | **Sim** (agregados) |
| Agentes | 6 | 6 (igual ao Pro) |
| Histórico | 24 meses | 24 meses (igual ao Pro) |
| Suporte | padrão | **Prioritário** |

> Nota: no doc anterior o Premium tinha bancos/histórico/agentes "ilimitados".
> **Removido.** O único ganho de limite sobre o Pro é a **família**, e o Open
> Finance vira um **teto de pool** (10) — não ilimitado por membro, pra custo
> ficar previsível.

#### 2.5.1 Open Finance: pool de 10 por família

- O teto de **10 conexões OF é da família inteira**, não de cada membro.
- Guia (não trava rígida): **~3 bancos por pessoa**. Com 3 pessoas × 3 = 9, ainda
  **sobra 1** conexão pra quem precisar de um quarto banco. O pool é flexível: se
  um membro conecta 2 e outro 4, tudo bem, desde que a soma da família ≤ 10.
- **Muda o gate**: hoje `of_banks_max` é comparado com
  `count_open_finance_connections(user_id)` (por usuário). Pro Premium, a
  contagem passa a ser **a soma das conexões de todos os membros da família**, e
  o teto vem de um limite de família (10), não do `of_banks_max` por usuário.
- Membro que sai perde o Premium → suas conexões deixam de contar no pool e são
  pausadas pelo gate do tier dele (Grátis, `of_banks_max = 0`).

### 2.6 Billing

- **Modelo: assinatura única do titular** (flat) cobrindo a família — **não**
  cobra por assento. Um `price_id` novo no Stripe.
- Preço: **a definir** (placeholder na `/precos` é "Aguarde"). Ancoragem: acima
  do Pro (R$ 49,90), já que substitui várias assinaturas. A seção 3 mostra que o
  custo de OF cabe folgado nessa faixa.
- **Só o titular tem assinatura Stripe.** Membros ganham Premium por **direito
  derivado** da família, não por assinatura própria.
- **Cancelou / não pagou** (`last_payment_status`, `plan_expires_at` vencido) →
  a família é desfeita e **todos voltam pro Grátis** — titular e membros. (Se um
  membro tinha assinatura própria paga antes de entrar, ela não existe mais: ao
  aceitar o convite ele não assinou nada; então o piso pós-cancelamento é o
  **Grátis** pra todo mundo.)
- Trial: reusa `plan_trials` (1 por telefone). Trial de Premium dá Premium ao
  titular; convidar membros durante o trial é permitido (herdam enquanto vive).

---

## 3. Análise de custo — Open Finance por família

O provedor de Open Finance (Pluggy) cobra, em regra, **por conexão (item) ativa
por mês**. Chame esse valor de **`C` = custo mensal por conexão ativa**.

> ⚠️ **`C` precisa vir do contrato Pluggy** — não está no código. A tabela abaixo
> usa uma faixa ilustrativa (R$ 0,80 / R$ 1,50 / R$ 2,50 por conexão/mês) só pra
> dimensionar a decisão. Troque pelos números reais antes de fechar preço.

### 3.1 Os dois cenários

- **Cenário A — Ilimitado por membro** (como estava no doc antigo): sem teto. O
  custo acompanha o uso real e **o pior caso é ilimitado** — uma família com um
  membro "colecionador" de bancos (10, 15 conexões) estoura a margem sem aviso.
- **Cenário B — Pool de 10 por família** (proposto): teto rígido de 10 conexões
  na família toda. **O pior caso é conhecido: `10 × C`.**

### 3.2 Custo esperado vs. pior caso

Uso realista: cada pessoa ativa em OF mantém ~2–3 conexões. Numa família de 3,
o **esperado** gira em torno de **~7–8 conexões** (usei 7,5 na conta).

| `C` (por conexão/mês) | A: esperado (~7,5 conx) | A: **pior caso** | B: esperado (~7,5) | B: **pior caso (10)** |
|---|---|---|---|---|
| R$ 0,80 | ~R$ 6,00 | **ilimitado** ⚠️ | ~R$ 6,00 | **R$ 8,00** |
| R$ 1,50 | ~R$ 11,25 | **ilimitado** ⚠️ | ~R$ 11,25 | **R$ 15,00** |
| R$ 2,50 | ~R$ 18,75 | **ilimitado** ⚠️ | ~R$ 18,75 | **R$ 25,00** |

**Leitura:**
- No **caso médio, A e B custam praticamente o mesmo** — a maioria das famílias
  nem encosta em 10. O pool não encarece o dia a dia.
- A diferença está no **pior caso**: A não tem teto (margem imprevisível); B
  trava em `10 × C` (no máximo **R$ 8–25 por família/mês** dependendo de `C`).
- Com o Premium ancorado **acima de R$ 49,90**, mesmo o pior caso do pool (R$ 25
  com `C` alto) consome **≤ 50%** da receita **só de OF** — e o caso médio fica
  em **~12–25%**. Sobra margem pra Stripe (~4% + R$ 0,39/cobrança), IA e infra.

### 3.3 Recomendação

**Adotar o Cenário B (pool de 10 por família).** Praticamente não muda o custo
médio, mas transforma o custo de OF de **imprevisível** (ilimitado) em **teto
conhecido**, o que é o que permite precificar o Premium com margem garantida. A
guia de ~3 por pessoa é comunicação/UX; a trava real é a soma ≤ 10 na família.

> **Ação pendente:** substituir `C` pelo valor real do contrato Pluggy e, se
> houver faixas por volume, recalcular. A estrutura de decisão (pool > ilimitado)
> não muda com o valor de `C` — só a magnitude.

---

## 4. Esboço de implementação

### 4.1 Novo tier no código

**`core/services/plan_limits.py`:**

```python
# TIER_ORDER ganha o topo
TIER_ORDER = {"free": 0, "essencial": 1, "plus": 2, "pro": 3, "premium": 4}

# Dois campos novos no PlanLimits (TypedDict):
class PlanLimits(TypedDict):
    ...
    family_members_max: int | None       # assentos totais (titular incluído); None/0 = sem família
    family_of_connections_max: int | None # pool de conexões OF da família inteira

# Backfill: todos os tiers atuais recebem family_members_max: 0
# e family_of_connections_max: None (não se aplica). Nova entrada:
PREMIUM_LIMITS: PlanLimits = {
    **PRO_LIMITS,                     # herda agentes(6), histórico(24m), etc.
    "of_banks_max": None,             # não usado no Premium; o teto vem do pool da família
    "family_members_max": 3,          # titular + 2
    "family_of_connections_max": 10,  # pool compartilhado
}

_TIER_LIMITS = {..., "premium": PREMIUM_LIMITS}
```

**`core/services/plan_service.py`:**

```python
_STORED_PLAN_TO_TIER = {..., "premium": "premium"}  # novo valor na coluna plan

def get_plan_tier(user_id):
    own_tier = _resolve_own_tier(user)          # lógica atual: free|essencial|plus|pro|premium
    family_tier = _resolve_family_tier(user_id) # 'premium' se sou membro de titular Premium ativo, senão None
    return max([own_tier, family_tier or "free"], key=lambda t: TIER_ORDER[t])
```

`_resolve_family_tier` = "existe família ativa onde sou membro E o titular tem
Premium vigente?". Cacheável por request (é chamado bastante).

### 4.2 Gate de Open Finance por família

Em `frontend/routes/open_finance.py`, onde hoje se lê `of_banks_max` e
`count_open_finance_connections(user_id)`:

```python
fam = get_family_for_user(user_id)              # None se não é família
if fam:
    limit = get_user_limits(user_id)["family_of_connections_max"]  # 10
    count = count_open_finance_connections_for_family(fam["id"])   # SOMA dos membros
else:
    limit = get_user_limits(user_id)["of_banks_max"]               # caminho atual
    count = count_open_finance_connections(user_id)
```

Nova função em `db/open_finance.py`:
`count_open_finance_connections_for_family(family_id)` = soma das conexões
não-pausadas de todos os `user_id` da família.

### 4.3 Bypass do gate `/precos` pro convidado

Em `db/families.py` (ver 4.5), a aceitação do convite carimba
`plan_selected_at`:

```python
def accept_invite(token, user_id):
    ...  # valida token, cria family_members
    # crítico: evita o redirect forçado pra /precos
    cur.execute(
        "update auth_accounts set plan_selected_at = now() "
        "where user_id = %s and plan_selected_at is null",
        (user_id,),
    )
```

No fluxo de **cadastro vindo do link de convite**, o token viaja pelo signup e é
consumido no fim → `needs_plan_selection` já retorna `False` → redirect direto
pro `/dashboard`. (Nenhuma mudança na lógica de `needs_plan_selection`; ela já
respeita `plan_selected_at`.)

### 4.4 Novas tabelas (`db/schema.py`)

Padrão `create table if not exists` idempotente do arquivo:

```sql
create table if not exists families (
  id            bigserial primary key,
  owner_user_id bigint not null references users(id) on delete cascade,
  status        text not null default 'active',   -- active | disbanded
  created_at    timestamptz not null default now(),
  disbanded_at  timestamptz
);
create unique index if not exists idx_families_owner_active
  on families (owner_user_id) where status = 'active';

create table if not exists family_members (
  id         bigserial primary key,
  family_id  bigint not null references families(id) on delete cascade,
  user_id    bigint not null references users(id) on delete cascade,
  role       text not null default 'member',   -- owner | member
  share_mode text not null default 'private',  -- private | shared_summary
  joined_at  timestamptz not null default now()
);
create unique index if not exists idx_family_members_user_unique
  on family_members (user_id);              -- 1 usuário em no máx. 1 família

create table if not exists family_invites (
  id            bigserial primary key,
  family_id     bigint not null references families(id) on delete cascade,
  invited_email text,
  invited_phone text,                       -- E.164; um dos dois obrigatório
  token_hash    text not null unique,
  status        text not null default 'pending', -- pending|accepted|declined|expired|revoked
  expires_at    timestamptz not null,
  created_at    timestamptz not null default now(),
  accepted_by_user_id bigint references users(id) on delete set null
);
create index if not exists idx_family_invites_status on family_invites (status);
```

### 4.5 Camada de acesso — `db/families.py`

Espelha `db/plans.py`, SQL puro:
`create_family(owner_id)`, `create_invite(...)`, `accept_invite(token, user_id)`,
`list_members(family_id)`, `remove_member(...)`, `set_share_mode(user_id, mode)`,
`get_family_for_user(user_id)`, `disband_family(family_id)`,
`count_members(family_id)` (trava dos 3 assentos).

### 4.6 Billing / Stripe

- Novo `price_id` de Premium (mensal + anual) via env (padrão do
  `STRIPE_PRICE_ID_*`), no mesmo mapa de checkout dos outros planos.
- Webhook de assinatura (o que já grava `plan`/`plan_expires_at`): virou
  `premium` ativo → garante `families` do owner (idempotente). Cancelou/expirou →
  `disband_family` → membros reavaliam tier na próxima `get_plan_tier`
  (`_resolve_family_tier` para de devolver premium) → **todos caem pro Grátis**.
- **Nenhuma cobrança por membro.**

### 4.7 Camada de compartilhamento (visão agregada)

- Novo `core/services/family_view.py`: dado `family_id`, soma os agregados
  **só dos membros com `share_mode='shared_summary'`**, reusando os agregadores
  por `user_id` (`db/analytics.py`, saldo consolidado). **Nunca** lê transação
  individual de outro membro.
- Blindagem anti-IDOR: todo acesso à visão da família confere **pertencimento**
  (requester na mesma `family_id`) antes de qualquer leitura.

### 4.8 Superfícies (frontend / API / bot)

- **`/precos`** (`frontend/precos.html`): tirar "Em breve", ligar preço e
  `startCheckout(..., 'premium')`. Ajustar o card ("Pool de 10 bancos",
  "3 pessoas").
- **Dashboard**: aba **Família** (convidar, listar membros, escolher
  `share_mode` com a tela clara da 2.4, ver painel agregado). Rotas novas em
  `frontend/routes/family.py`, protegidas por `require_min_tier('premium')` (owner)
  e por pertencimento (membros).
- **Cadastro** (`frontend/cadastro.html` + rota de signup): aceitar `?invite=`,
  levar o token até o fim, aceitar o convite e **redirecionar pro `/dashboard`**.
- **E-mail**: template de convite reusando `core/services/email_service.py`.
- **Bot**: comandos ("família", "convidar") ficam pra fase 2.

### 4.9 Testes

Seguindo `tests/test_plan_tiers.py` e `tests/test_billing_checkout.py`:
- Tier derivado: membro de titular Premium ativo → `premium`; titular cancela →
  **todos → Grátis**.
- Convite: aceitar move `pending→accepted`, cria `family_members` e **carimba
  `plan_selected_at`** (→ convidado não vê `/precos`); expira em 7 dias;
  4º convite (excede 3 assentos) é rejeitado.
- OF pool: soma de 10 conexões na família libera; a 11ª é bloqueada
  independentemente de qual membro tenta; membro que sai libera o pool.
- Isolamento: membro `private` não entra nos agregados; requester de outra
  família recebe 403.

---

## 5. Fases de entrega sugeridas

| Fase | Escopo | Entrega |
|------|--------|---------|
| **0 — Fundação** | Tier `premium` em `plan_limits`/`plan_service`, `PREMIUM_LIMITS`, testes de tier. Sem família. | Premium vendável como "Pro + pool OF". |
| **1 — Família MVP** | Tabelas, `db/families.py`, convites por e-mail, bypass do `/precos`, aba Família, tier derivado, OF pool (gate por família), billing flat, compartilhamento **agregado** opt-in com a tela clara da 2.4. | Modo Família completo (3 pessoas). |
| **2 — Refinos** | Convite/comandos no bot, transferência de titularidade, ajustes de UX. | — |

---

## 6. Decisões em aberto (produto)

1. **Preço** do Premium (mensal/anual) — a seção 3 mostra que o OF cabe folgado
   acima de R$ 49,90.
2. **Valor real de `C`** (custo Pluggy por conexão) — para fechar a margem.
3. **Nome comercial** ("Modo Família" já está na `/precos`).

### Decisões já fechadas nesta revisão
- Tamanho da família: **3 pessoas** (titular + 2).
- Convidado que se cadastra vai **direto pro dashboard** (carimba
  `plan_selected_at`), **nunca** pra `/precos`.
- Compartilhamento: **só agregados**, escolha **explícita e clara na entrada**.
- Único ganho de limite sobre o Pro: **família**. OF vira **pool de 10 por
  família** (não ilimitado por membro).
- Cancelou/expirou: **todos voltam pro Grátis**.
