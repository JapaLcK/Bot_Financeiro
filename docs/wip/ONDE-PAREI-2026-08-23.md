# Onde parei — 2026-08-23 (fim do dia)

Substitui a versão da madrugada. Um PR mergeado, um no meio da rodada 10.

---

## Feito hoje

**PR #121 MERGEADO** — `9346ddf` na `main`, squash, branch deletado.
Generation guards nos loaders de `frontend/settings.html`, fan-out órfão,
harness Playwright em `tests/frontend/`, job de CI `frontend` **bloqueante**.

Resultado que importa: **o job `frontend` passou no runner do GitHub**.
`npx playwright install --with-deps chromium` funciona no `ubuntu-latest`.

Efeito colateral bom: o job agora existe na `main`, que era o pré-requisito
para ligar os `required_status_checks`.

---

## PR #123 — parado na rodada 10

Head remoto `d1732a3`. CI verde (`pytest` ✓ `audit` ✓). **2 threads do Codex
ainda sem resposta** (os dois P2 sobre a delegação por receita).

### Onde o trabalho vive (NÃO é commit)

```
docs/wip/pr123-rodada9-completo.patch    <- rodada 9 FECHADA, 1308 passed
docs/wip/pr123-rodada10-PARCIAL.patch    <- rodada 10 no meio, 1314 passed
```

O worktree do scratchpad **evapora entre sessões** (já aconteceu uma vez).
Os patches são a única cópia. Para retomar:

```bash
git fetch origin && git checkout fix/categorizacao-categoria-custom-roubada
git reset --hard origin/fix/categorizacao-categoria-custom-roubada
git apply docs/wip/pr123-rodada10-PARCIAL.patch
```

### O que a rodada 10 estava fazendo (interrompida)

O Manager reprovou a rodada 9 com 4 bloqueantes. O Coder tinha feito as
mudanças de código e ia escrever os testes quando foi parado. **Verde não
significa fechado** — os testes que provariam os fixes é que faltavam.

- **B4** — a alegação central da rodada 9 é FALSA. `is_internal_category` NÃO é
  o predicado que escreve `is_internal_movement`: há 5+ escritores não citados,
  2 com predicado diferente. Medido: `categoria="ajuste"` grava a flag `True`
  mas `is_internal_category("ajuste")` é `False` → bug intacto.
- **B5** — o fix do B2 só dispara em `total <= 0`. Com linhas dos dois lados da
  flag, o total sai parcial e **calado**: total R$ 100 vs lista R$ 600. Número
  errado com cara de certo — pior que a negação que o B2 corrigiu.
- **B6** — metade do par-espelho do B3 é tautológica (passa com o B3 revertido).
- **B7** — `_limit_pedido` aceita 100 linhas = **6436 chars** contra o teto de
  **4096** do WhatsApp. Sem chunking em lugar nenhum. A capacidade de estourar
  é NOVA (antes o caminho devolvia total + top 5). Decisão já tomada: truncar e
  anunciar, sem construir chunking.

Mais: comentário falso em `tests/test_custom_category_infer.py:421` (descreve
string que nunca existiu); guarda de `RECEITA_START_VERBS` viável e barata; e
`find_poisoned` sem teste, sendo testável aqui.

---

## PERGUNTAR AO DONO AMANHÃ

**Separar `scripts/cleanup_poisoned_category_rules.py` para PR próprio?**

Ele pediu para eu perguntar de novo amanhã. Contexto já explicado a ele:

- O script apaga **regras de aprendizado** (`user_category_rules`), não
  lançamento, não categoria, não dinheiro.
- Já é seguro: dry-run é o default e `--apply` **exige** `--user` (achado do
  Codex numa rodada anterior — uma regra pode ter sido criada de propósito).
- É ferramenta que o dono roda **à mão, cliente por cliente, depois do deploy**
  — em qualquer cenário, junto ou separado.
- Recomendação do Manager: separar, porque só é verificável em produção e
  prende a metade verificável do PR (CLAUDE.md §4).

---

## Decisões já tomadas (não redecidir)

- Dois PRs, separados por verificabilidade — feito
- `required_status_checks` só quando a fila de PRs esvaziar (hoje 3: #119,
  #122, #123) — pendente **de propósito**
- Eixo TIPO e eixo FORMATO separados (lista vs total)
- Total de categoria de movimento interno **explica** em vez de negar
- Listagem sem janela; total no mês corrente, com escopo no rótulo

---

## Pendências não bloqueantes

- `package-lock.json` sem vigia: dependabot cobre `pip` e `github-actions`, o
  job `audit` escaneia Python. Ninguém olha npm.
- Working tree local: `mobile/` traz `IPHONEOS_DEPLOYMENT_TARGET 14→15` e
  `MARKETING_VERSION 1.0→1.1`, mais `.claude/launch.json` e `.DS_Store` — tudo
  de ANTES desta sessão, intocado. **Todo commit tem que ser path-scoped.**
- Card no chip: bug pré-existente do `mercado extra` (categoria custom `extra`
  vence `mercado`). Task foi iniciada em sessão separada, não gerou PR.
- Card separado a criar: `add_launch_and_update_balance` não deriva
  `is_internal_movement` da categoria — pré-existente, e é o mecanismo que
  torna o B5 alcançável.

---

## O padrão desta sequência, para não repetir

Dez rodadas no mesmo subsistema. A raiz nunca mudou: **tratar "achei um caso"
como "resolvi a categoria"**. Mas o que mais custou foi outra coisa —
**afirmar como medido um número ou predicado que não foi enumerado até o fim**:

- "8 read paths" → corrigido para 14 → o real são 25+
- "`is_internal_category` é o mesmo predicado que escreve a flag" → não é
- "revert derruba 4 testes" → derruba 3
- 8 comentários que afirmavam o falso ao longo do ciclo

Cada um desses passou por mim e eu repassei. A correção que funcionou foi
trocar número por comando reproduzível, e referência de linha por nome de
função.
