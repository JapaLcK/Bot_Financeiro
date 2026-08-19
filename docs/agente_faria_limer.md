# 📈 Agente Faria Limer

O investidor da Faria Lima da equipe de porquinhos. Acompanha sua **renda
variável** (ações e FIIs) pela corretora, via Open Finance, e todo mês te
entrega o **retrato da carteira** — sempre em fatos, **nunca em recomendação**.
A decisão é sempre sua.

## Ficha

| Campo | Valor |
|---|---|
| **kind** (interno) | `faria_limer` |
| **Nome** | Faria Limer |
| **Emoji** | 📈 |
| **Frequência** | Mensal · Open Finance |
| **Custo de energia** | ⚡2 (mensal + dependência de Open Finance, igual ao Barão) |
| **Planos** | Plus/Pro (modelo de energia; Pro tem orçamento pra rodar os 7 agentes) |
| **Fonte** | `db/rv.py` — posições de RV derivadas do espelho do sync (read-only) |

## Copy

**One-liner (prateleira / catálogo):**
> Acompanha sua renda variável (ações e FIIs): o retrato do mês e a
> concentração da carteira — só fatos, nunca recomendação.

**Parágrafo (página de agentes):**
> Fica de olho na sua renda variável (ações e FIIs) pela sua corretora: todo
> mês te entrega o retrato da carteira — valor, resultado e concentração. Só
> fatos, nunca recomendação: a decisão é sempre sua.

## O que ele dispara (eventos, dedupe mensal)

1. **Retrato do mês** (`rv_retrato:YYYY-MM`) — valor de mercado, resultado em
   aberto (P&L) e a variação do mês (média ponderada por posição, quando a
   corretora manda a taxa). Ex.: *"Sua renda variável: R$ 11.000,00 em 2 ativos."*
2. **Concentração** (`rv_concentracao:YYYY-MM`) — dispara quando um único ativo
   passa de 40% da carteira. Ex.: *"PETR4 é 80% da sua carteira de RV."*

Toda mensagem é descritiva e devolve a decisão ao usuário ("é só o retrato — não
é recomendação", "decisão sua"). Posicionamento + regulatório: sem palpite.

## Arte (ligada)

A arte final já está plugada e é o que aparece em produção:

- **Assets:** `frontend/brand/agents/faria_limer_hero.png` (banner do e-mail/card) e
  `frontend/brand/agents/faria_limer.png` (medalhão do feed).
- **App:** `"faria_limer"` está no set `_AGENT_ART` em `frontend/dashboard.js` e
  `frontend/preview_agentes.html`.
- **E-mail:** `"faria_limer"` está em `_AGENT_ART_KINDS` (medalhão) e mapeado em
  `_AGENT_HERO` → `faria_limer_hero` em `core/services/email_service.py` (o label
  "Faria Limer" também está lá).

O **SVG de fallback** (`#ag-pig-faria_limer` — porquinho com tela de trading em
alta) e o gradiente `.ag-bg-faria_limer` seguem no código como rede de segurança
para quando o PNG não carregar.

> **Nota de design (pendência):** o `faria_limer.png` hoje é uma redução do hero
> (paisagem ~300×150), enquanto os medalhões dos outros agentes são retratos
> (~290×440, sticker vertical). Vale gerar um sticker vertical dedicado quando
> houver uma arte de origem em retrato.
