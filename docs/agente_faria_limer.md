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

## Arte que falta (a "sua parte")

Enquanto não chega, o **SVG de fallback** (`#ag-pig-faria_limer` — porquinho com
tela de trading em alta) e o gradiente `.ag-bg-faria_limer` já funcionam no app,
e o e-mail cai no medalhão-logo.

Para plugar a arte final:

1. Subir `frontend/brand/agents/faria_limer.png` (sticker do medalhão) e
   `faria_limer_hero.png` (banner 1200×600 do e-mail/card).
2. Adicionar `"faria_limer"` ao set `_AGENT_ART` em `frontend/dashboard.js` e
   `frontend/preview_agentes.html`.
3. Adicionar `"faria_limer"` a `_AGENT_ART_KINDS` e o hero a `_AGENT_HERO` em
   `core/services/email_service.py` (o label "Faria Limer" já está lá).
