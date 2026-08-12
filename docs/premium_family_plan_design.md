# Design — Plano Premium com Modo Família

> Status: **proposta** (2026-08-12). Ainda não implementado. O Premium hoje é só
> um card "Em breve" na `/precos` (`frontend/precos.html:122-135`) com o bullet
> "Modo família", sem tier no código nem modelo de dados.
>
> Este documento tem duas partes: **(1) design de produto** — como funciona pra
> quem usa — e **(2) esboço de implementação** — onde encosta no código atual.

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
- **Gates**: `feature_enabled`, `require_min_tier`, `agent_kind_allowed`,
  `check_can_create_*` — todos derivam de `get_user_limits(user_id)`.
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
2. **Visão consolidada opcional** das finanças da casa (patrimônio, gastos).
3. **Privacidade preservada** — cada membro continua dono dos seus próprios
   dados; compartilhar é opt-in e granular.

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
  daquele membro (volta pro plano próprio dele, ou Grátis).

### 2.2 Tamanho da família

- Premium inclui o titular **+ até 4 membros** (5 pessoas no total). Número vira
  o limite `family_members_max` no `PlanLimits` — fácil de ajustar.
- Convite excedente é bloqueado com mensagem clara ("Sua família está cheia").

### 2.3 Fluxo de convite

1. Titular assina o Premium → família é criada automaticamente (ele vira owner).
2. No dashboard (aba **Família**), o titular convida por **e-mail** ou
   **telefone (E.164)** — as duas chaves que já identificam contas
   (`auth_accounts.email`, `auth_accounts.phone_e164`).
3. Gera um **convite com token** (expira em 7 dias). Envio por e-mail
   (`core/services/email_service.py` já tem templates) e/ou link no WhatsApp.
4. Convidado:
   - **Já tem conta PigBank** → aceita com 1 clique; o `user_id` dele entra como
     membro e o tier vira Premium na hora.
   - **Não tem conta** → cadastra primeiro (fluxo normal), aí o convite vincula.
5. Convite tem estados: `pending → accepted | declined | expired | revoked`.

### 2.4 Privacidade dos dados — o ponto central

**Princípio: dados são privados por padrão.** Entrar numa família **não** expõe
lançamentos, saldos ou bancos de ninguém automaticamente. O que a família
habilita é uma **camada de compartilhamento opt-in**, escolhida por cada membro.

Dois modos de compartilhamento (o membro escolhe o dele, e pode mudar quando
quiser):

| Modo | O que o resto da família vê |
|------|------------------------------|
| **Privado** (default) | Nada. O membro só ganha o tier Premium; dados dele ficam invisíveis pra família. |
| **Compartilhar visão** | Números **consolidados/somados** entram no painel da família (patrimônio total, gastos por categoria). Sem detalhe transação-a-transação por padrão. |

Nível de detalhe do "Compartilhar visão" (a definir com produto, sugestão pro MVP):
- **Compartilha:** saldo consolidado, total de gastos/receitas do mês, gastos
  agregados por categoria, progresso de metas.
- **Não compartilha (nunca no MVP):** transações individuais, nomes de
  estabelecimentos, credenciais de banco, cartões específicos.

> ⚠️ **Decisão pendente de produto:** o painel da família mostra números
> **agregados** (recomendado pro MVP — mais simples e mais seguro) ou permite
> **drill-down transação-a-transação** entre membros? O MVP deste design assume
> **só agregados**. Drill-down vira fase 2 com consentimento explícito por membro.

Essa camada se apoia no que já existe: `db/privacy.py` (LGPD/PII) e o gate
`consolidated_balance_enabled` (que já soma Carteira + bancos OF numa visão só).

### 2.5 Limites do tier Premium

Premium = superset do Pro, mais o Modo Família:

| Limite/feature | Pro (hoje) | **Premium (novo)** |
|----------------|-----------|--------------------|
| Bancos Open Finance | 5 | **Ilimitado** (`None`) |
| Agentes | 6 | **Todos + custom** (fase 2) |
| Histórico | 24 meses | **Ilimitado** (`None`) |
| Membros de família | — | **4** (+titular) |
| Painel da família | — | **Sim** |
| Suporte | padrão | **Prioritário** |

Tudo o mais herda do Pro (`PRO_LIMITS`).

### 2.6 Billing

- **Modelo escolhido: assinatura única do titular** (flat) que cobre todos os
  membros — **não** cobra por assento. Mais simples de comunicar ("um preço,
  família inteira") e de implementar no Stripe (um `price_id` novo).
- Preço: **a definir** (placeholder na `/precos` é "Aguarde"). Sugestão de
  ancoragem: acima do Pro (R$ 49,90), já que substitui várias assinaturas.
- **Só o titular tem assinatura Stripe.** Membros ganham o tier Premium por
  **direito derivado** da família, não por assinatura própria.
- Cancelou / não pagou (`last_payment_status`, `plan_expires_at` vencido) →
  a família inteira rebaixa junto: titular e membros voltam ao tier próprio de
  cada um (o que cada um tinha antes, ou Grátis).
- Trial: reusa a mecânica atual (`plan_trials`, 1 por telefone). Trial de
  Premium dá acesso Premium ao titular; convidar membros durante o trial é
  permitido (eles herdam enquanto o trial vive).

---

## 3. Esboço de implementação

### 3.1 Novo tier no código (menor mudança, maior alcance)

**`core/services/plan_limits.py`:**

```python
# TIER_ORDER ganha o topo
TIER_ORDER = {"free": 0, "essencial": 1, "plus": 2, "pro": 3, "premium": 4}

# Novo campo no PlanLimits (TypedDict)
class PlanLimits(TypedDict):
    ...
    family_members_max: int | None   # 0 = sem família; N = titular + N convidados

# Backfill de todos os tiers existentes com family_members_max: 0
# e a nova entrada:
PREMIUM_LIMITS: PlanLimits = {
    **PRO_LIMITS,
    "history_days": None,        # ilimitado
    "of_banks_max": None,        # bancos ilimitados
    "agents_max": None,          # todos
    "family_members_max": 4,     # + titular = 5 pessoas
}

_TIER_LIMITS = {..., "premium": PREMIUM_LIMITS}
```

**`core/services/plan_service.py`:**

```python
_STORED_PLAN_TO_TIER = {
    ...,
    "premium": "premium",        # novo valor aceito na coluna auth_accounts.plan
}
```

`get_plan_tier` precisa de UM ajuste: um **membro** de família não tem `plan =
premium` na própria linha `auth_accounts` (só o titular assina). Então a
resolução vira:

```python
def get_plan_tier(user_id):
    # 1) tier próprio (assinatura direta) — lógica atual
    own_tier = _resolve_own_tier(user)          # free|essencial|plus|pro|premium
    # 2) tier derivado da família (se for membro de um titular Premium ativo)
    family_tier = _resolve_family_tier(user_id) # 'premium' ou None
    # o MAIOR ganha
    return max([own_tier, family_tier or "free"], key=lambda t: TIER_ORDER[t])
```

`_resolve_family_tier` = "existe família ativa onde sou membro E o titular tem
Premium vigente?". Cacheável por request pra não pesar (é chamado bastante).

### 3.2 Novas tabelas (`db/schema.py`)

Seguindo a convenção `create table if not exists` idempotente do arquivo:

```sql
-- A família. Uma linha por grupo; owner é quem assina.
create table if not exists families (
  id           bigserial primary key,
  owner_user_id bigint not null references users(id) on delete cascade,
  status       text not null default 'active',   -- active | disbanded
  created_at   timestamptz not null default now(),
  disbanded_at timestamptz
);
create unique index if not exists idx_families_owner_active
  on families (owner_user_id) where status = 'active';

-- Vínculo membro↔família + preferência de compartilhamento DELE.
create table if not exists family_members (
  id           bigserial primary key,
  family_id    bigint not null references families(id) on delete cascade,
  user_id      bigint not null references users(id) on delete cascade,
  role         text not null default 'member',   -- owner | member
  share_mode   text not null default 'private',  -- private | shared_summary
  joined_at    timestamptz not null default now()
);
-- 1 usuário em no máximo 1 família:
create unique index if not exists idx_family_members_user_unique
  on family_members (user_id);

-- Convites pendentes.
create table if not exists family_invites (
  id           bigserial primary key,
  family_id    bigint not null references families(id) on delete cascade,
  invited_email text,
  invited_phone text,               -- E.164; um dos dois é obrigatório
  token_hash   text not null unique,
  status       text not null default 'pending',  -- pending|accepted|declined|expired|revoked
  expires_at   timestamptz not null,
  created_at   timestamptz not null default now(),
  accepted_by_user_id bigint references users(id) on delete set null
);
create index if not exists idx_family_invites_status on family_invites (status);
```

Camada de acesso nova: **`db/families.py`** (espelha o padrão de `db/plans.py`),
com funções puras de SQL:
`create_family(owner_id)`, `create_invite(...)`, `accept_invite(token, user_id)`,
`list_members(family_id)`, `remove_member(...)`, `set_share_mode(user_id, mode)`,
`get_family_for_user(user_id)`, `disband_family(family_id)`.

### 3.3 Billing / Stripe

- Novo `price_id` de Premium (mensal + anual) via env, no mesmo mapa que os
  outros planos já usam no checkout.
- Webhook de assinatura (o que já grava `plan`/`plan_expires_at`): ao virar
  `premium` ativo → garante `families` row do owner (idempotente). Ao
  cancelar/expirar → `disband_family` (ou marca inativa) e os membros
  naturalmente reavaliam tier na próxima chamada de `get_plan_tier` (o
  `_resolve_family_tier` para de retornar premium).
- **Nenhuma cobrança por membro** — membros não tocam no Stripe.

### 3.4 Camada de compartilhamento (visão da família)

- MVP = **agregados**. Novo módulo `core/services/family_view.py` que, dado um
  `family_id`, soma os números **só dos membros com `share_mode='shared_summary'`**:
  reusa os agregadores existentes (`db/analytics.py`, saldo consolidado) por
  `user_id` e soma. Nunca lê transação individual de outro membro no MVP.
- Blindagem: todo acesso à visão da família **verifica pertencimento** (o
  requester está na mesma `family_id`) antes de qualquer leitura — mesma
  disciplina dos gates atuais. Sem isso, vira IDOR entre famílias.

### 3.5 Superfícies (frontend / API / bot)

- **`/precos`** (`frontend/precos.html`): tirar "Em breve" do card Premium,
  ligar preço e botão de checkout (`startCheckout(..., 'premium')`).
- **Dashboard**: nova aba **Família** (convidar, listar membros, escolher
  `share_mode`, ver painel agregado). Novas rotas em `frontend/routes/`
  (ex.: `family.py`) protegidas por `require_min_tier(user_id, 'premium')` pro
  titular e por pertencimento pros membros.
- **Bot (WhatsApp/Discord)**: comandos opcionais fase 2 ("família", "convidar
  fulano"). `core/services/billing_commands.py` já centraliza os verbos de plano.
- **E-mail**: template de convite reusando `core/services/email_service.py`.

### 3.6 Testes

Seguindo `tests/test_plan_tiers.py` e `tests/test_billing_checkout.py`:
- `get_plan_tier` derivado: membro de titular Premium ativo → `premium`;
  titular cancela → membro volta ao tier próprio.
- Convite: aceitar move `pending→accepted` e cria `family_members`; expira após
  7 dias; excedente ao `family_members_max` é rejeitado.
- Isolamento: membro em `share_mode='private'` **não** aparece nos agregados da
  família; requester de outra família recebe 403 na visão.
- Billing: webhook de Premium cria família; cancelamento a desfaz.

---

## 4. Fases de entrega sugeridas

| Fase | Escopo | Entrega |
|------|--------|---------|
| **0 — Fundação** | Tier `premium` em `plan_limits`/`plan_service`, `PREMIUM_LIMITS`, testes de tier. Sem família ainda. | Premium vendável como "Pro turbinado" (bancos/histórico ilimitados). |
| **1 — Família MVP** | Tabelas, `db/families.py`, convites por e-mail, aba Família no dashboard, tier derivado, billing flat. Compartilhamento **agregado** opt-in. | Modo Família funcional. |
| **2 — Refinos** | Drill-down com consentimento, agentes custom, convite/comandos no bot, transferência de titularidade. | — |

---

## 5. Decisões em aberto (precisam de produto)

1. **Preço** do Premium (mensal/anual).
2. **Tamanho** da família (assumido 4 membros + titular).
3. **Nível de compartilhamento** no MVP: só agregados (recomendado) vs.
   drill-down.
4. **Nome comercial** ("Modo Família" já está na `/precos`).
5. **Rebaixamento**: ao cancelar, membro volta pro **Grátis** ou pro **plano que
   tinha antes**? (Este design assume: volta ao tier próprio dele, se houver
   assinatura direta ativa; senão, Grátis.)
