/**
 * O motor SPA (pb-nav.js) e o fetch do tap: `AbortSignal.timeout` fora, e o
 * teto de 5s que entrou no lugar dele.
 *
 * Por que a troca — e por que hoje ela é INALCANÇÁVEL — está no comentário de
 * pb-nav.js:~365 e não se repete aqui. O que este arquivo mede: o caminho do
 * fetch do motor NÃO depende de uma API acima do alvo do app
 * (IPHONEOS_DEPLOYMENT_TARGET = 14.0), e o teto de 5s que a substituiu AINDA
 * EXISTE E FUNCIONA. Irmão do WP18
 * (tests/frontend/welcome_pro_pix.test.mjs:496), onde o caminho ERA alcançável.
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
 *   · negativo — `ctrl.abort()` fora do `finally` (pb-nav.js:~392): o 4º caso
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
 * `.then().then().catch()` do `warmUp` (frontend/pb-nav.js:~330) é toda de
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
  await page.waitForFunction(() => window.PBNav !== undefined);

  const sanidade = await page.evaluate(() => ({
    semTimeout: typeof AbortSignal.timeout === "undefined",
    enabled: window.PBNav.enabled,
    vt: typeof document.startViewTransition === "function",
  }));

  // O warmUp roda 1200ms após o boot; esperar o prefetch queimar. Quando a
  // bandeira sobe, o `delete warm[key]` JÁ aconteceu (ver `queimaPrefetch`).
  // O teto de 30s é o padrão do Playwright, e não é enfeite: `node --test` não
  // tem timeout por caso, então uma bandeira que nunca sobe penduraria o
  // processo inteiro, sem sumário e sem nome de teste.
  await page.waitForFunction(() => window.__prefetchMorreu);

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
  // CHAVE da rota (pb-nav.js:308) e sairia igual com qualquer HTML — é o title
  // que discrimina. Não dá para asserir um seletor do corpo: sem sessão, o init
  // da comandos apaga o `#page-root` inteiro (showAccessError,
  // comandos-app.html:134), e essa marcação também existe na home.html.
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
  await page.waitForFunction(() => window.PBNav !== undefined);
  assert.equal(await page.evaluate(() => window.PBNav.enabled), true,
               "o motor SPA precisa estar LIGADO");

  // 1º toque: navigate #1 preso ANTES dos headers.
  assert.equal(await page.evaluate(() => window.PBNav.go("/comandos-app.html")), true);
  await page.waitForFunction(() => window.__lib.length === 1);

  // 2º toque na MESMA aba: passa o guard do go() e leva seq a 2.
  assert.equal(await page.evaluate(() => window.PBNav.go("/comandos-app.html")), true,
               "o duplo toque na mesma aba devia passar o guard do go()");
  await page.waitForFunction(() => window.__lib.length === 2,
                             null, { timeout: 2000 });

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
