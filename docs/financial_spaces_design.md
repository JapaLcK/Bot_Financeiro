# Espaços Financeiros (design, não implementado)

Objetivo: uma conta gerencia **vários contextos financeiros separados** sob o
mesmo login — o exemplo clássico é 🏠 Casa · 🌾 Fazenda · 🏡 Casa de campo. Cada
espaço tem seus próprios lançamentos, saldos, caixinhas e cartões, isolados
entre si, mas todos acessíveis com a mesma conta.

Status: **puro design**. `grep` por `financial_spaces`, `spaces_max` e
`workspace_user_id` no repositório volta vazio. `plan_limits.py` ainda não tem o
campo `spaces_max`. Toda conta existente já tem, implicitamente, **1 espaço** —
o `user_id` que ela já usa.

---

## A sacada da arquitetura (por que é barato)

**Espaço = um `user_id` interno (workspace).** É o mesmo truque que o código já
usa em `db/users.py::get_or_create_canonical_user`, que sintetiza um `user_id`
determinístico e chama `ensure_user_tx`. Um espaço novo é só **mais um `user_id`
interno** — nada de discord/whatsapp por trás dele, é um workspace puro.

Como **todas** as tabelas de dados já são keyed por `user_id`
(`launches`, `accounts`, `pockets`, `credit_cards`, … — ver
`docs/CLAUDE.md`), criar um espaço é criar um `user_id` e pronto: **zero
migração de dados, zero backfill**. O espaço principal é o próprio `user_id` que
a conta já tem.

A única tabela nova é fininha:

```sql
create table financial_spaces (
    id                bigserial primary key,
    owner_user_id     bigint not null,          -- a conta dona (o user_id "raiz")
    workspace_user_id bigint not null unique     -- o user_id interno que guarda os dados
                      references users(id),
    name              text not null,             -- "Fazenda"
    emoji             text,                       -- "🌾"
    created_at        timestamptz not null default now()
);
create index on financial_spaces (owner_user_id);
```

Convenção do espaço principal: ele **não precisa** de linha nessa tabela (o
`owner_user_id` já é um workspace válido por si só), ou opcionalmente ganha uma
linha "seed" com `workspace_user_id = owner_user_id` pra aparecer no dropdown
com nome/emoji custom. Decisão pendente abaixo.

---

## Como funciona em cada superfície

### Dashboard (fácil)

- **Dropdown de troca** no header — ex.: `🏠 Casa · 🌾 Fazenda`.
- O front pede os dados do **workspace ativo** (o `user_id` do espaço), usando
  os endpoints que já existem: `/data/{user_id}`, `/history/{user_id}`,
  `/export/{user_id}`, `/ws/{user_id}`.
- **Autorização é o ponto-chave.** Hoje `authorize_dashboard_access`
  (`frontend/routes/shared.py:325`) faz igualdade estrita:

  ```python
  if current_user_id != int(user_id):
      raise HTTPException(status_code=403, ...)
  ```

  Precisa virar "**o `user_id` pedido é um espaço que pertence à conta
  logada?**" — ou seja, `user_id == current_user_id` **OU** existe
  `financial_spaces(owner_user_id=current_user_id, workspace_user_id=user_id)`.
  Um único JOIN na tabela nova. Todos os ~20 endpoints que chamam
  `_authorize_dashboard_access` herdam o comportamento de graça.

### WhatsApp (o desafio — 1 telefone = 1 conta)

O `user_id` no WhatsApp é resolvido pelo telefone
(`user_identities`), então não dá pra trocar de conta. Duas mecânicas
complementares resolvem, roteadas em `core/handle_incoming.py`:

1. **Espaço ativo (comando):** `piggy espaço fazenda` grava o espaço ativo
   daquele usuário. Toda mensagem seguinte cai nesse espaço até trocar de novo.
2. **Prefixo pontual:** `fazenda: gastei 200 em ração` lança **naquele espaço**
   sem mexer no espaço ativo. Volta ao ativo na próxima mensagem.
3. Qualquer outra mensagem cai no **espaço ativo** (default = principal).

Na prática: resolve o `user_id` do telefone → mapeia pro `workspace_user_id` do
espaço (ativo ou do prefixo) → o resto do pipeline não muda, porque já opera por
`user_id`.

Onde guardar o espaço ativo: coluna em `user_identities` ou tabela
`active_space(owner_user_id, workspace_user_id)`. Decisão pendente.

---

## Gate por plano + a regra de custo

Campo novo em `PlanLimits` (`core/services/plan_limits.py`):

```python
spaces_max: int | None   # espaços financeiros por conta (1 = só o principal)
```

Valores na escada v3 atual (Grátis · Essencial · Plus · Pro):

| Tier      | `spaces_max` |
|-----------|--------------|
| Grátis    | 1            |
| Essencial | 1            |
| Plus      | 2            |
| Pro       | 3            |
| Premium   | mais (engavetado) |

Criar espaço além do teto → levanta `PlanLimitExceeded("spaces", msg)`, o mesmo
padrão de erro que `pockets`/`cards` já usam.

> ### ⚠️ Regra dura de custo (não negociável)
>
> O teto de **bancos Open Finance** (`of_banks_max`: 0/1/2/5) conta **por conta
> — soma de todos os espaços**, NUNCA por espaço. Se contasse por espaço, 3
> espaços = 3× o teto de conexões Pluggy, e o custo de Open Finance comeria a
> margem do plano. A checagem de `of_banks_max` tem que somar as conexões de
> **todos os `workspace_user_id` da conta** antes de comparar com o limite.

---

## Bônus que nasce de graça

**"Visão consolidada do patrimônio"** (card Premium): como todos os espaços da
conta são `user_id`s conhecidos via `financial_spaces.owner_user_id`, o
consolidado é um `SELECT` somando os workspaces da conta. Sai da arquitetura sem
esforço extra — nenhum modelo de dados novo.

---

## Relação com "Família" (fora do v1)

Mesmo alicerce: uma tabela futura `space_members(space_id, member_user_id,
role)` permite **convidar outra pessoa** pra um espaço = modo família/participantes.
Espaços e família são a **mesma fundação** — o v1 entrega só a parte
"multi-contexto de uma conta"; convidar gente é incremento depois. Isso conversa
com o item "Plano Família" da Fase 3 no roadmap (`docs/CLAUDE.md`).

---

## Arquivos a criar/tocar quando for implementar

- **novo** `db/spaces.py` — CRUD de `financial_spaces` + criação do
  `workspace_user_id` (reusando `ensure_user_tx`) + resolução "espaço ativo".
- `db/schema.py` — tabela `financial_spaces` (+ índice) e, se for por coluna, o
  campo de espaço ativo.
- `core/services/plan_limits.py` — campo `spaces_max` no `TypedDict` e nos 4
  dicts de tier. **Regra de custo:** a soma de `of_banks_max` continua por conta.
- `frontend/routes/shared.py` — `authorize_dashboard_access` aceita
  `workspace_user_id`s da conta (JOIN em `financial_spaces`).
- `frontend/dashboard.html` — dropdown de troca de espaço no header.
- `core/handle_incoming.py` — comando `piggy espaço <nome>` + prefixo pontual
  `<nome>: ...` + resolução do espaço ativo.
- `tests/` — autorização cross-espaço (não vazar entre contas), gate de
  `spaces_max`, e o **teste crítico**: `of_banks_max` somando espaços.

## Decisões pendentes suas

- Espaço principal: linha "seed" em `financial_spaces` (nome/emoji custom no
  dropdown) ou implícito (sem linha)?
- Espaço ativo do WhatsApp: coluna em `user_identities` ou tabela própria?
- `spaces_max` 1/1/2/3 fecha, ou Essencial já ganha 2 pra diferenciar do Grátis?
- Nome do prefixo pontual: `fazenda: ...` (dois-pontos) ou `#fazenda ...`?
