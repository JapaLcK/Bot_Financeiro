# Briefing — Refactor de lifecycle dos loaders do dashboard

> Documento de trabalho para a sessão "Unificar falha de refresh nos loaders
> do dashboard". Autossuficiente: dá pra agir sem o histórico da conversa que o
> gerou. Origem: achados do Codex no PR #60 (puxar pra atualizar).

## 1. O problema, em uma frase

Os 9 loaders secundários do dashboard deduplicam requests com uma trava caseira
(`_xxxFetchInFlight`) que **não cancela o pedido velho e não guarda geração** —
então um fetch pendurado trava a aba (hang-strand) e um pedido velho pode
renderizar por cima de um novo (stale-overwrite). É **código pré-existente**, não
introduzido pelo PR do gesto; o wedge já ocorre na navegação normal hoje.

Dois bugs concretos que isso causa:

- **Hang-strand:** se um fetch fica preso sem assentar (conexão engolida, sem
  resposta e sem erro), o `finally` que zera `_xxxFetchInFlight` nunca roda. Toda
  chamada seguinte àquele loader devolve a promise morta em vez de pedir de novo.
  A aba para de atualizar até a página recarregar ou o socket estourar no OS.
- **Stale-overwrite:** se dois requests do mesmo loader correm juntos (ex.: uma
  revalidação stale-while-revalidate em voo quando o usuário força um refresh), o
  que terminar por último renderiza — e o velho pode sobrescrever o novo com dado
  financeiro desatualizado. **Foi exatamente o P1 que uma tentativa de stopgap
  (commit e7badca, depois revertido em f89bcbf) introduziu** ao zerar as refs sem
  parar os requests. Não repita esse caminho: zerar a ref não para o pedido.

## 2. A chave: o padrão correto JÁ EXISTE no repositório

Não invente mecânica nova. A aba **Visão geral** (`fetchMonthHttp` em
`frontend/dashboard.js`, ~linha 6598) já faz certo e está no ar:

```js
const seq = ++monthRequestSeq;          // 1. carimba cada pedido
if (monthAbortController) monthAbortController.abort();   // 2. cancela o anterior
monthAbortController = new AbortController();
// ...
const r = await fetch(url, { signal: monthAbortController.signal });  // 3. passa o signal
// ...
if (seq !== monthRequestSeq) return;    // 4. superado? não renderiza
```

O refactor é **fazer os 9 stragglers se parecerem com isto**. Propagar código
provado, não escrever novo — de propósito, pra derrubar o risco.

## 3. Alvos (confirme os números de linha; eles mudam)

Enumere com: `grep -n "InFlight\b" frontend/dashboard.js`

| Loader (view) | var de dedup | helper de fetch | quirk a preservar |
|---|---|---|---|
| Cartões | `_cardsFetchInFlight` | `_fetchCardsSummary` | **SWR** (cache + revalida) |
| Parcelas | `_instFetchInFlight` | `_fetchInstallments` | **SWR** |
| Categorias | `_categoriesFetchInFlight` | (inline no load) | cache + skeleton |
| Orçamentos | `_budgetsFetchInFlight` | (inline no load) | cache + skeleton |
| Metas | `_goalsFetchInFlight` | (inline no load) | cache + skeleton |
| Recorrentes (gastos) | `_recurringFetchInFlight` | (inline no load) | cache + skeleton |
| Recorrentes (receitas) | `_recurringIncomeFetchInFlight` | (inline) | cache + skeleton |
| Análises | `_analyticsFetchInFlight` | (inline) | **5 fetches paralelos** — abort tem que cancelar os 5 |
| Histórico | `_historyListInFlight` | `_fetchHistoryList` | caso **`allowParallel`** (busca por digitação) NÃO pode ser estrangulado |

Fora de escopo (já corretos, não tocar): `fetchMonthHttp` (referência),
`loadHomeData` (home.html, fetch direto), afiliado e `loadRecurringOverview`
(fetch direto, sem dedup).

## 4. Desenho: um "canal de fetch" compartilhado

Um helper único, não 9 cópias — a duplicação foi o que deixou esses 9 divergirem
do `fetchMonthHttp`. Esboço (ajustar à realidade do arquivo):

```js
// Dedup + abort do pedido velho + guarda de geração, num lugar só.
// Espelha o que fetchMonthHttp faz à mão (monthRequestSeq + monthAbortController).
function makeFetchChannel() {
  let inFlight = null, controller = null, gen = 0;
  return {
    // fetcher(signal) -> Promise<data>. force = puxar pra atualizar.
    // Resolve { data } se ainda for o pedido mais novo, ou { stale: true }.
    run(fetcher, { force = false } = {}) {
      if (inFlight && !force) return inFlight;      // dedup normal
      if (controller) controller.abort();            // cancela o velho DE VERDADE
      const myGen = ++gen;
      controller = new AbortController();
      const p = (async () => {
        try {
          const data = await fetcher(controller.signal);
          return (myGen === gen) ? { data } : { stale: true };
        } finally {
          if (myGen === gen) { inFlight = null; controller = null; }  // não zera a ref do mais novo
        }
      })();
      inFlight = p;
      return p;
    }
  };
}
```

Cada loader ganha seu canal (`const cardsChannel = makeFetchChannel()`) e troca a
trava caseira por `cardsChannel.run(signal => fetch(url, { signal }), { force })`.
O loader renderiza só quando `!result.stale`, e trata `AbortError` como "superado,
não é erro de verdade" (não pinta a tela de erro num abort).

Decisão em aberto pra sessão avaliar: helper compartilhado (recomendado, mata a
classe pra sempre) vs. replicar o padrão inline nos 9 (menos abstração, mais
duplicação). Recomendo o compartilhado.

## 5. Armadilhas específicas

- **AbortError não é erro de tela.** Ao abortar o pedido velho, ele rejeita com
  `AbortError`. O `catch` do loader NÃO pode renderizar "Erro ao carregar" nesse
  caso — senão um puxão que cancela o anterior pinta erro à toa. Filtrar
  `err.name === "AbortError"` e sair quieto (igual `fetchMonthHttp` faz).
- **SWR (Cartões, Parcelas):** a revalidação de fundo e o forceFresh do puxão
  passam pelo mesmo canal — o abort/geração já resolve a corrida entre eles. Não
  crie um segundo caminho.
- **Análises (5 fetches):** todos os 5 têm que receber o MESMO signal, pra um
  abort cancelar o conjunto. Cuidar do `Promise.all` com um fetch abortado.
- **Histórico `allowParallel`:** hoje a busca por digitação passa `allowParallel`
  pra não esperar o anterior. Preservar essa semântica — o canal precisa de um
  modo que não dedupe/aborte nesse caso, ou a busca trava.
- **`AbortSignal` no WKWebView:** suportado no alvo (iOS 16+; o app roda iOS 26).
  OK usar `AbortController`. Não depender de `AbortSignal.timeout` sem checar.

## 6. Verificação — OBRIGATÓRIA, não por raciocínio

Este é o ponto onde o PR do gesto falhou (não tinha dashboard logado; verificou
por leitura). O refactor NÃO pode repetir. Duas camadas:

1. **Dashboard real logado.** Subir `dashboard_dev.py` local contra o banco de
   staging (`.env.staging`; `DASHBOARD_URL=http://<ip-lan>:8000` pra o cookie não
   sair `secure`; `RUN_BACKGROUND_TASKS=0`). Logar por e-mail/senha (o Google
   OAuth não conhece o IP local). Exercitar cada uma das 9 abas.

2. **Playwright** (`/opt/pw-browsers`, `NODE_PATH=$(npm root -g)`) interceptando o
   fetch (`page.route`) pra forçar os QUATRO cenários que quebram, por loader:
   - resposta **normal** → renderiza fresco;
   - resposta **lenta** (delay) → não trava, dedup segura;
   - resposta **pendurada** (nunca resolve) → aba NÃO estranha; um segundo
     forceFresh re-emite (abort mata o pendurado);
   - **SWR em voo + forceFresh** → o fresco ganha, o velho NUNCA sobrescreve
     (asserir a ordem de render e o valor final na tela).

   O 4º cenário é a corrida do P1 — é o teste que raciocínio não pega e que
   precisa existir automatizado.

3. **Baseline da suíte** antes e depois, comparando por NOME de teste, não por
   contagem (CLAUDE.md §3 e §6). Guardar a lista antes de tocar no código.

## 7. Processo e escopo

- **PR separado** do #60 (gesto). Não depende dele.
- Fluxo do repo: abrir PR, esperar o Codex, responder cada thread citando commit +
  o que foi medido, `@codex review` no head novo, repetir até aprovar. Merge só
  com autorização explícita do dono.
- **Não** expandir o escopo pra "melhorar os loaders" além do lifecycle
  (abort + geração + AbortError-quiet). Mudança de comportamento de cache/SWR/erro
  visível fora disso é outro PR.

## 8. Definition of done

- Os 9 loaders usam o canal compartilhado (ou o padrão inline espelhando
  `fetchMonthHttp`), com abort + guarda de geração.
- `AbortError` nunca pinta erro de tela.
- Os quirks (SWR, 5-paralelos das Análises, `allowParallel` do Histórico) preservados.
- Harness Playwright cobrindo os 4 cenários × os loaders, verde.
- Baseline da suíte pytest sem regressão nova (por nome).
- Codex aprovou ("Didn't find any major issues").
