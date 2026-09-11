/**
 * O motor SPA (pb-nav.js) e o fetch do tap: `AbortSignal.timeout` fora, e o
 * teto de 5s que entrou no lugar dele.
 *
 * Por que a troca — e por que hoje ela é INALCANÇÁVEL — está no comentário de
 * "NÃO volte para `AbortSignal.timeout`" do `navigate` (pb-nav.js) e não se
 * repete aqui. O que este arquivo mede: o caminho do
 * fetch do motor NÃO depende de uma API acima do alvo do app
 * (IPHONEOS_DEPLOYMENT_TARGET = 14.0), e o teto de 5s que a substituiu AINDA
 * EXISTE E FUNCIONA. Irmão do WP18
 * (WP18, em tests/frontend/welcome_pro_pix.test.mjs), onde o caminho ERA
 * alcançável.
 *
 * A degradação apaga SÓ `AbortSignal.timeout`: o iOS 14 completo desligaria o
 * motor e o teste mediria zero. Três asserts de sanidade impedem a tautologia:
 * a API ausente, o motor LIGADO e a View Transition presente.
 *
 * Os controles do CLAUDE.md §3, medidos por mutação:
 *   · negativo — repondo `signal: AbortSignal.timeout(5000)`, o caso degradado
 *     fica vermelho (o `navigate` cai no `hard()`);
 *   · negativo — `mountNew` no-op, ou `html` trocado por OUTRA página: os dois
 *     primeiros casos ficam vermelhos (o assert é sobre o title montado);
 *   · negativo — teto removido (`const timer = 0`) ou elevado (5000 → 12000):
 *     o 3º caso fica vermelho;
 *   · negativo — `ctrl.abort()` fora do `finally` do ramo fetch do `navigate`
 *     (pb-nav.js): o 4º caso
 *     fica vermelho. O discriminador é a MEDIDA de dentro da página (ms do
 *     início do fetch até o abort), não o relógio do Playwright: só o
 *     `setTimeout` de 5s sobra abortando, e ele dá ~5000ms;
 *   · positivo — o 2º caso, sem degradar nada, prova que o navegador moderno
 *     continua navegando pelo motor.
 *
 * ponytail: `finally { }` (timer vazado, com o `ctrl.abort()` junto) passa
 * VERDE nos 3 primeiros casos — quem o mata é o 4º, e só pela parte do abort; o
 * timer vazado sozinho continua invisível daqui. Para cobrir, instrumentar o
 * próprio pb-nav.js.
 *
 * O que este teste NÃO alcança: um WKWebView de iOS 14 de verdade. Chromium
 * com a API apagada reproduz o TypeError, não o motor de renderização dele.
 *
 * Rodar:  node --test tests/frontend/pb_nav_ios14.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// §0.7: o title é do HTML, não desta string — renomear a página não vira
// vermelho aqui.
const TITULO_COMANDOS = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend", "comandos-app.html"),
  "utf8").match(/<title>(.*?)<\/title>/)[1];

// `PigBankApp` no UA é o que faz o app-mode.js pôr `html.pb-app`.
const UA_APP = "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) PigBankApp/1.0";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

/**
 * `waitForFunction` com teto e com mensagem.
 *
 * Sem isto a falha sai como `Timeout 30000ms exceeded` — 30s por vermelho e
 * sem dizer o que a página nunca fez. Quem quebrar o `endsWith("/comandos-app")`
 * dos stubs (mexendo em ROUTES, por exemplo) lê a causa no texto.
 */
const esperar = (page, fn, msg, timeout = 5000) =>
  page.waitForFunction(fn, null, { timeout })
      .catch(() => assert.fail(`${msg} (esperei ${timeout}ms)`));

/**
 * Queima o prefetch DENTRO da página, e avisa num macrotask.
 *
 * O filtro casa o path do PREFETCH (`/comandos-app`, sem `.html`), não o do
 * tap: o `warmUp` itera as chaves de ROUTES e a primeira do par já marca
 * `warm.comandos`, então a variante `.html` nunca é aquecida — e o tap, que vai
 * para `/comandos-app.html`, passa direto pelo `orig`. Se um dia ROUTES ganhar
 * outro path terminando em `/comandos-app`, o stub pega os dois junto.
 *
 * Rejeitar (em vez de segurar) é obrigatório: com o prefetch preso,
 * `warm.comandos` fica `null` para sempre e o tap pendura no ramo warm, que não
 * é o caminho alterado.
 *
 * A bandeira sobe num `setTimeout(0)` — MACROtask — e a cadeia
 * `.then().then().catch()` do `warmUp` (frontend/pb-nav.js) é toda de
 * MICROtasks. O event loop drena a fila de microtasks inteira antes de qualquer
 * macrotask, então quando `__prefetchMorreu` fica true o `delete warm[key]` já
 * aconteceu POR ESPECIFICAÇÃO. Era isto que o `page.route` não dava: o abort
 * viajava CDP → browser → rede → renderer enquanto o `evaluate` seguinte falava
 * direto com o renderer, sem ordenação nenhuma entre os dois.
 */
function queimaPrefetch() {
  const orig = window.fetch;
  window.fetch = function (u) {
    if (String(u).endsWith("/comandos-app")) {
      setTimeout(() => { window.__prefetchMorreu = true; }, 0);
      return Promise.reject(new Error("prefetch queimado"));
    }
    return orig.apply(this, arguments);
  };
}

/**
 * Abre a /home.html dentro do "app" com o motor ligado e o prefetch da
 * comandos-app.html queimado, e devolve o que o tap produziu.
 *
 * `degradar` apaga `AbortSignal.timeout` antes de qualquer script rodar — é o
 * iOS 14. Sem ele, o mesmo caminho num navegador de hoje (o controle positivo).
 * `pendurar` segura o fetch do motor para sempre: é assim que o teto de 5s
 * ganha um caso que o exercita.
 */
async function tap(degradar, pendurar) {
  // `?pbspa=1` liga a flag na sessão; sem ela e sem o UA, `PBNav.enabled` é false.
  const ctx = await browser.newContext({ userAgent: UA_APP });
  const page = await ctx.newPage();
  const erros = [];
  page.on("pageerror", e => erros.push(String(e)));

  if (degradar) await page.addInitScript(() => { delete AbortSignal.timeout; });

  await page.addInitScript(queimaPrefetch);

  const tipos = [];
  if (pendurar) {
    // Segura o fetch do motor sem responder nunca. O `document` passa: é a
    // navegação do `hard()`, o resultado que este caso quer medir.
    await page.route("**/comandos-app.html",
                     r => { if (r.request().resourceType() === "document") r.continue(); });
  }
  page.on("request", r => {
    if (r.url().includes("comandos-app.html")) tipos.push(r.resourceType());
  });

  await page.goto(`${ORIGIN}/home.html?pbspa=1`);
  await esperar(page, () => window.PBNav !== undefined,
                "o pb-nav.js não expôs window.PBNav");

  const sanidade = await page.evaluate(() => ({
    semTimeout: typeof AbortSignal.timeout === "undefined",
    enabled: window.PBNav.enabled,
    vt: typeof document.startViewTransition === "function",
  }));

  // O warmUp roda 1200ms após o boot; esperar o prefetch queimar. Quando a
  // bandeira sobe, o `delete warm[key]` JÁ aconteceu (ver `queimaPrefetch`).
  await esperar(page, () => window.__prefetchMorreu,
                "o prefetch da comandos-app nunca foi disparado pelo warmUp");

  tipos.length = 0;                       // do tap pra frente é o que importa
  const t0 = Date.now();
  // Armado ANTES do go(): é o `hard()` do fallback de timeout chegando.
  const hardEm = pendurar
    ? page.waitForRequest(
        r => r.url().includes("comandos-app.html") && r.resourceType() === "document",
        // 20s (e não 15) para caber o teto MUTADO para 12s: assim quem reporta
        // um teto errado é o assert da janela, não o estouro do relógio.
        { timeout: 20_000 }).then(() => Date.now() - t0, () => null)
    : null;
  const assumiu = await page.evaluate(() => window.PBNav.go("/comandos-app.html"));
  const msAteHard = hardEm ? await hardEm : null;
  // O mount troca a body class; se não trocar (motor que não monta), o
  // waitForFunction estoura e os asserts abaixo é que reportam.
  await page.waitForFunction(
    () => document.body.className.includes("pb-page-comandos"), null, { timeout: 8000 },
  ).catch(() => {});

  const montado = await page.evaluate(() => ({
    pathname: location.pathname,
    bodyClass: document.body.className,
    titulo: document.title,
  })).catch(() => null);
  await ctx.close();
  return { sanidade, assumiu, tipos, montado, msAteHard, erros };
}

function conferirMontagem(r) {
  assert.ok(r.montado, "não deu para ler o estado da página depois do tap");
  assert.match(r.montado.bodyClass, /pb-page-comandos/,
               `a tela nova não montou: ${JSON.stringify(r.montado)}`);
  // O title vem do HTML BAIXADO (`doc.title` no mountNew); a body class vem da
  // CHAVE da rota (o `pb-page-` + key que o `mountNew` escreve na body) e
  // sairia igual com qualquer HTML — é o title
  // que discrimina. Não dá para asserir um seletor do corpo: sem sessão, o init
  // da comandos apaga o `#page-root` inteiro (`showAccessError` da
  // comandos-app.html), e essa marcação também existe na home.html.
  assert.equal(r.montado.titulo, TITULO_COMANDOS,
               `montou OUTRA tela — o title não é o da comandos-app.html: ${JSON.stringify(r.montado)}`);
  assert.equal(r.montado.pathname, "/comandos-app.html",
               `a URL não avançou: ${JSON.stringify(r.montado)}`);
}

test("sem AbortSignal.timeout: o tap sai pelo motor e MONTA a tela nova", async () => {
  const r = await tap(true);
  assert.equal(r.sanidade.semTimeout, true, "AbortSignal.timeout devia estar apagado");
  assert.equal(r.sanidade.enabled, true, "o motor SPA precisa estar LIGADO");
  assert.equal(r.sanidade.vt, true, "sem startViewTransition o motor se desliga sozinho");
  assert.equal(r.assumiu, true, "PBNav.go devia assumir a navegação");
  // Pelo motor: `fetch`. Pelo hard() (o bug): `document`.
  assert.equal(r.tipos.includes("fetch"), true,
               `o fetch do tap não saiu — tipos: ${JSON.stringify(r.tipos)}`);
  assert.equal(r.tipos.includes("document"), false,
               `caiu em location.href — tipos: ${JSON.stringify(r.tipos)}`);
  // Resultado, não só tráfego: sem isto um `mountNew` no-op passa verde.
  conferirMontagem(r);
  assert.deepEqual(r.erros, []);
});

test("navegador moderno: o mesmo tap continua montando pelo motor", async () => {
  const r = await tap(false);
  assert.equal(r.sanidade.semTimeout, false, "aqui a API existe de propósito");
  assert.equal(r.sanidade.enabled, true, "o motor SPA precisa estar LIGADO");
  assert.equal(r.assumiu, true, "PBNav.go devia assumir a navegação");
  assert.equal(r.tipos.includes("fetch"), true,
               `o fetch do tap não saiu — tipos: ${JSON.stringify(r.tipos)}`);
  assert.equal(r.tipos.includes("document"), false,
               `caiu em location.href — tipos: ${JSON.stringify(r.tipos)}`);
  // Resultado, não só tráfego: sem isto um `mountNew` no-op passa verde.
  conferirMontagem(r);
  assert.deepEqual(r.erros, []);
});

// A RAZÃO DE SER da troca é o teto continuar existindo. Custa ~5s de relógio:
// é o próprio teto sendo esperado.
test("fetch que nunca responde: o teto de 5s derruba o tap no hard()", async () => {
  const r = await tap(true, true);
  assert.equal(r.assumiu, true, "PBNav.go devia assumir a navegação");
  assert.notEqual(r.msAteHard, null,
    "o fetch pendurado não virou navegação de documento em 20s: sem teto o motor deixa o usuário preso na tela velha");
  // Janela, não piso: sem o limite de cima, elevar o teto (5s → 12s) passava
  // verde. Limpo dá ~5,0s e o teto mutado para 12s dá ~13,4s; 4–10s absorve
  // runner lento sem aceitar os 12s.
  assert.equal(r.msAteHard >= 4000 && r.msAteHard <= 10_000, true,
    `caiu no hard() em ${r.msAteHard}ms — fora da janela de 4–10s do teto de 5s`);
});

/**
 * O 4º caso, e o único que mata o `ctrl.abort()`.
 *
 * O guard de geração (`if (my !== seq) return;`) fica ENTRE os headers e o
 * corpo. Nesse `return` o `finally` cancela o único relógio agendado; sem o
 * `abort()` junto, ninguém mais aborta e o corpo não lido fica pendurado até
 * EOF/GC — o teto de 5s prometido no cabeçalho do pb-nav.js deixa de existir
 * nesse ramo. Alcançável sem corrida exótica: `PBNav.go` só recusa quando
 * `key === currentKey`, e `currentKey` só muda dentro do `commit()`, que roda no
 * swap — enquanto o 1º fetch está em voo a aba corrente ainda é a velha, então
 * um SEGUNDO toque na MESMA aba (duplo toque no dock, o gesto mais comum de
 * mobile) passa o guard e leva `seq` a 2. O passo 3 abaixo prova isso de quebra.
 *
 * Os dois `go()` são para a mesma URL de propósito. O discriminador NÃO é o teto
 * de 1s do `waitForFunction` — esse é de relógio, e um estol basta para
 * confundi-lo: com `finally { }` (timer vazado) mais ~4,5s de atraso antes do
 * release, o abort do `setTimeout` de 5s cai dentro da janela e o caso passaria.
 * Quem separa é `__abortado[0]`, medido DENTRO da página a partir do início do
 * fetch: o abort do conserto dá 7–24ms (medido) e o do relógio dá ~5000ms,
 * qualquer que seja o instante do release.
 *
 * O que este caso NÃO cobre: ele para antes do `mountNew` — sem swap, sem
 * commit — então mede exclusivamente "o `AbortSignal` do navigate #1 disparou".
 * Um `mountNew` no-op ou um `html` de outra página passam verde AQUI; quem os
 * mata são os casos 1–3.
 *
 * E ele amarra a FORMA atual, não só o contrato: mover o guard `my !== seq` para
 * DEPOIS de `html = await r.text()` deixa este caso vermelho. O alternativo é
 * defensável em contrato (o corpo não fica pendurado) mas baixa o download
 * inteiro de uma navegação já superada e só abortaria aos 5s — quem refatorar
 * precisa decidir isso de olho aberto, não descobrir pelo vermelho.
 */
test("navegação superada: o corpo pendurado é abortado, não esquecido", async () => {
  const ctx = await browser.newContext({ userAgent: UA_APP });
  const page = await ctx.newPage();
  const erros = [];
  page.on("pageerror", e => erros.push(String(e)));

  await page.addInitScript(() => {
    // ms do INÍCIO do fetch até o abort, por navegação (o índice tira a
    // ambiguidade). É medida relativa ao fetch e não ao release de propósito:
    // o abort do `setTimeout` de 5s dá SEMPRE ~5000 aqui, qualquer que seja o
    // instante em que o teste solta a resposta — é isso que separa o abort do
    // conserto do abort do relógio (ver o cabeçalho do caso).
    window.__abortado = [];
    window.__lib = [];           // resolvedores, um por fetch do tap
    const orig = window.fetch;
    window.fetch = function (u, o) {
      const s = String(u);
      // o prefetch do warmUp, queimado como nos outros casos
      if (s.endsWith("/comandos-app")) return Promise.reject(new Error("prefetch queimado"));
      if (s.endsWith("/comandos-app.html")) {
        const i = window.__lib.length, t = Date.now();
        o.signal.addEventListener("abort", () => { window.__abortado[i] = Date.now() - t; });
        return new Promise(ok => { window.__lib.push(ok); });
      }
      return orig.apply(this, arguments);
    };
  });

  await page.goto(`${ORIGIN}/home.html?pbspa=1`);
  await esperar(page, () => window.PBNav !== undefined,
                "o pb-nav.js não expôs window.PBNav");
  assert.equal(await page.evaluate(() => window.PBNav.enabled), true,
               "o motor SPA precisa estar LIGADO");

  // 1º toque: navigate #1 preso ANTES dos headers.
  assert.equal(await page.evaluate(() => window.PBNav.go("/comandos-app.html")), true);
  await esperar(page, () => window.__lib.length === 1,
                "o 1º toque não chegou a chamar fetch");

  // 2º toque na MESMA aba: passa o guard do go() e leva seq a 2.
  assert.equal(await page.evaluate(() => window.PBNav.go("/comandos-app.html")), true,
               "o duplo toque na mesma aba devia passar o guard do go()");
  await esperar(page, () => window.__lib.length === 2,
                "o 2º toque não chegou a chamar fetch", 2000);

  // Solta o #1 com headers prontos e o CORPO ABERTO: ele cai exatamente no
  // `if (my !== seq) return;` de pb-nav.js, com o body por ler.
  await page.evaluate(() => window.__lib[0](
    new Response(new ReadableStream({ start() {} }), { status: 200 })));

  const abortou = await page
    .waitForFunction(() => typeof window.__abortado[0] === "number", null, { timeout: 1000 })
    .then(() => true, () => false);
  assert.equal(abortou, true,
    "o fetch superado não foi abortado em 1s: sem o abort() no finally o corpo não lido fica pendurado até EOF/GC, sem teto nenhum");
  // O discriminador de verdade, medido DENTRO da página: o abort do conserto
  // chega junto com o `return` do guard (7–24ms limpo); o do `setTimeout`
  // chegaria aos ~5000ms contados do mesmo zero. O corte de 1s é ~40× o medido
  // e 5× abaixo do teto: estol vira falso VERMELHO, nunca falso verde.
  const msAteAbort = await page.evaluate(() => window.__abortado[0]);
  assert.equal(msAteAbort < 1000, true,
    `o abort chegou ${msAteAbort}ms após o início do fetch: é o teto de 5s disparando, não o abort() do finally`);
  assert.deepEqual(erros, []);
  await ctx.close();
});

/**
 * O 5º caso: o PREFETCH também precisa de teto.
 *
 * O fetch do `warmUp` não passava `signal` nenhum, e o tap espera o pedido em
 * voo (`await Promise.resolve(warm[key] || inflight[key])`, no ramo warm do
 * `navigate`) sem
 * timeout próprio. Um prefetch pendurado prendia o tap até o timeout de rede do
 * navegador SEM cair no `hard()` — o oposto do "Fallback sempre-navega … nunca
 * tela travada" do cabeçalho do arquivo. Falta de teto, não `finally` cedo
 * demais: por isso a forma do conserto aqui é `AbortController` + `setTimeout`
 * no prefetch, e não uma guarda no `finally` como nos outros quatro sites.
 *
 * Um teto SÓ, e não dois: o abort cai no `.catch` do `warmUp`, que já devolve
 * `null`, e o ramo warm já trata `null` com `hard(path)`. Por isso a espera
 * máxima é ≈5 s contados do INÍCIO DO PREFETCH — logo ≤5 s contados do tap.
 *
 * Este caso NÃO usa o `tap()` acima de propósito: aquele helper espera
 * `__prefetchMorreu`, e aqui o prefetch é justamente o que NÃO termina. Segue a
 * forma do 4º caso, que também monta a própria página.
 *
 * O discriminador é a medida DE DENTRO da página (ms do início do prefetch até
 * o abort), pelo mesmo motivo do 4º caso: o relógio do Playwright conta do tap,
 * e o intervalo entre o começo do prefetch e o tap não é fixo.
 *
 * Controles do CLAUDE.md §3, MEDIDOS:
 *   · negativo — `const t = 0` (teto removido): nunca aborta, `__preAbort` fica
 *     null e o tap não vira navegação de documento em 20 s;
 *   · negativo — teto elevado (5000 → 12000): `__preAbort` sai ~12 s, fora da
 *     janela de 4–10 s;
 *   · positivo — os casos 1 e 2, que continuam montando pelo motor: o teto novo
 *     não atropelou o prefetch que RESPONDE.
 */
test("prefetch que nunca responde: o teto de 5s do warmUp derruba o tap no hard()",
  async () => {
    const ctx = await browser.newContext({ userAgent: UA_APP });
    const page = await ctx.newPage();

    // A medida sai da página por BINDING, e não por `page.evaluate` depois: o
    // `hard()` navega logo em seguida ao abort e o contexto de execução morre
    // junto ("Execution context was destroyed" — foi o primeiro jeito tentado).
    // O binding entrega o número no instante do abort, antes da navegação.
    let msAbort = null;
    await page.exposeFunction("__anotaAbort", (ms) => { msAbort = ms; });

    await page.addInitScript(() => {
      window.__preEmVoo = false;
      const orig = window.fetch;
      window.fetch = function (u, o) {
        // Só o path do PREFETCH (`/comandos-app`, sem `.html`) — ver o
        // `queimaPrefetch` acima para por que é só ele que o warmUp aquece.
        if (String(u).endsWith("/comandos-app")) {
          const t = Date.now();
          setTimeout(() => { window.__preEmVoo = true; }, 0);
          // Pendurado para sempre, MAS rejeitando no abort — é o que o `fetch`
          // de verdade faz, e sem isso o `.catch` do warmUp nunca rodaria e o
          // caso ficaria vermelho por defeito do stub, não do código.
          // `o.signal` some quando a mutação tira o teto: aí nada rejeita, que
          // é exatamente o comportamento sem teto que se quer medir.
          return new Promise((_, rej) => {
            if (!(o && o.signal)) return;
            o.signal.addEventListener("abort", () => {
              window.__anotaAbort(Date.now() - t);
              rej(new DOMException("Aborted", "AbortError"));
            });
          });
        }
        return orig.apply(this, arguments);
      };
    });

    await page.goto(`${ORIGIN}/home.html?pbspa=1`);
    await esperar(page, () => window.PBNav !== undefined,
                  "o pb-nav.js não expôs window.PBNav");
    assert.equal(await page.evaluate(() => window.PBNav.enabled), true,
                 "o motor SPA precisa estar LIGADO");
    await esperar(page, () => window.__preEmVoo,
                  "o prefetch da comandos-app nunca começou");

    const hardEm = page.waitForRequest(
      (r) => r.url().includes("comandos-app.html") && r.resourceType() === "document",
      // 20s (e não 15) pelo mesmo motivo do 3º caso: cabe o teto MUTADO para
      // 12s, então quem reporta teto errado é o assert da janela.
      { timeout: 20_000 }).then(() => true, () => false);
    assert.equal(await page.evaluate(() => window.PBNav.go("/comandos-app.html")), true,
                 "PBNav.go devia assumir a navegação");

    assert.equal(await hardEm, true,
      "o tap com o prefetch pendurado não virou navegação de documento em 20s:"
      + " sem teto no warmUp o tap espera o timeout de rede do navegador, preso"
      + " na tela velha e sem cair no hard()");
    const ms = msAbort;
    assert.notEqual(ms, null, "o prefetch pendurado não foi abortado");
    // Janela, não piso: sem o limite de cima, elevar o teto (5s → 12s) passava
    // verde. Contada do INÍCIO DO PREFETCH, que é onde o relógio nasce.
    assert.equal(ms >= 4000 && ms <= 10_000, true,
      `o prefetch foi abortado ${ms}ms depois de começar — fora da janela de`
      + " 4–10s do teto de 5s");
    await ctx.close();
  });

/**
 * O 6º caso: o PREFETCH que RESPONDE e é descartado (`!r.ok` ou `r.redirected`).
 *
 * Irmão exato do 4º caso, no outro site. A cadeia do `warmUp` faz
 * `Promise.reject()` ENTRE os headers e a leitura do corpo, e o `.finally`
 * cancela o único relógio armado — sem o `abort()` junto, ninguém aborta e o
 * corpo fica pendurado até EOF/GC. Não é teórico: `r.redirected` é o desfecho
 * NORMAL de um prefetch com sessão vencida (redirect para /entrar ou /precos),
 * e o ramo fetch de baixo já chama isso de "fluxo de verdade" — sessão vencida
 * = N prefetches redirecionados baixando página de login sem teto.
 *
 * Os DOIS ramos são medidos porque compartilham a mesma expressão: um `500`
 * (`!r.ok`) e um `200` com `redirected` forjado (`defineProperty` sobre o
 * getter do protótipo — em Chromium o `Response` construído à mão vem sempre
 * com `redirected === false`).
 *
 * Controles do CLAUDE.md §3, MEDIDOS:
 *   · negativo — tirando só o `abort()` do `.finally` do warmUp
 *     (`.finally(() => clearTimeout(t))`, a forma exata que este PR escreveu
 *     antes da rodada 2): os dois ramos ficam vermelhos, `__preAbort` fica
 *     null. Provado rodando pelo Tester com este mesmo probe;
 *   · positivo — os casos 1 e 2, que montam pelo motor com o prefetch
 *     respondendo 200: o abort novo não atropela o prefetch que serve.
 *
 * O corte de 1s é o mesmo discriminador do 4º caso: o abort do conserto chega
 * no instante da rejeição, e o único outro abortador possível é o `setTimeout`
 * de 5s. Estol vira falso VERMELHO, nunca falso verde.
 */
for (const cenario of [{ nome: "500", status: 500, redirected: false },
                       { nome: "redirect (sessão vencida)", status: 200, redirected: true }]) {
  test(`prefetch descartado por ${cenario.nome}: o corpo por ler é abortado`, async () => {
    const ctx = await browser.newContext({ userAgent: UA_APP });
    const page = await ctx.newPage();

    await page.addInitScript((c) => {
      window.__preAbort = null;      // ms do início do prefetch até o abort
      window.__preEmVoo = false;
      const orig = window.fetch;
      window.fetch = function (u, o) {
        // só o path do PREFETCH (`/comandos-app`, sem `.html`) — ver `queimaPrefetch`
        if (String(u).endsWith("/comandos-app")) {
          const t = Date.now();
          setTimeout(() => { window.__preEmVoo = true; }, 0);
          if (o && o.signal) {
            o.signal.addEventListener("abort", () => { window.__preAbort = Date.now() - t; });
          }
          // Corpo ABERTO e nunca fechado: se alguém LER isto o teste pendura,
          // e é justamente o corpo que o ramo descartado deixa para trás.
          const r = new Response(new ReadableStream({ start() {} }), { status: c.status });
          if (c.redirected) Object.defineProperty(r, "redirected", { value: true });
          return Promise.resolve(r);
        }
        return orig.apply(this, arguments);
      };
    }, cenario);

    await page.goto(`${ORIGIN}/home.html?pbspa=1`);
    await esperar(page, () => window.PBNav !== undefined,
                  "o pb-nav.js não expôs window.PBNav");
    assert.equal(await page.evaluate(() => window.PBNav.enabled), true,
                 "o motor SPA precisa estar LIGADO");
    // O warmUp do boot só sai aos 1200ms (o `setTimeout(warmUp, 1200)` do
    // `boot`, pb-nav.js): sem esperar o
    // prefetch COMEÇAR, a janela de 1s abaixo estoura antes de haver fetch e o
    // caso ficaria vermelho por relógio, não por defeito.
    await esperar(page, () => window.__preEmVoo,
                  "o prefetch da comandos-app nunca começou");

    const abortou = await page
      .waitForFunction(() => window.__preAbort !== null, null, { timeout: 1000 })
      .then(() => true, () => false);
    assert.equal(abortou, true,
      "o prefetch descartado não foi abortado em 1s: sem o abort() no .finally do"
      + " warmUp o clearTimeout tira o único relógio e o corpo não lido fica"
      + " pendurado até EOF/GC");
    const ms = await page.evaluate(() => window.__preAbort);
    assert.equal(ms < 1000, true,
      `o abort chegou ${ms}ms depois do início do prefetch: é o teto de 5s`
      + " disparando, não o abort() do .finally");
    await ctx.close();
  });
}
