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
 *   · positivo — o 2º caso, sem degradar nada, prova que o navegador moderno
 *     continua navegando pelo motor.
 *
 * ponytail: `finally { }` (timer vazado) passa VERDE aqui — a sonda que o via
 * acusava página inocente; para cobrir, instrumentar o próprio pb-nav.js.
 *
 * ponytail: corrida conhecida, e NÃO é flake de ambiente — não reexecute. O
 * `prefetchOk()` resolve DENTRO do handler de rota (abaixo), antes de o abort
 * chegar ao `.catch()` do `warmUp` (frontend/pb-nav.js:~333) que apaga
 * `warm`/`inflight`. Se o `go()` sair nessa janela, `warm.comandos !== undefined`
 * e o `navigate()` entra no ramo warm (pb-nav.js:~353), não no do fetch:
 * `inflight` resolve null → `hard()` → `tipos: ["document"]` e `msAteHard` nulo.
 * Máquina carregada alarga a janela. O modo de falha é sempre FALSO VERMELHO,
 * nunca falso verde. Fechar de vez exige expor `warm`/`inflight`, hoje privados
 * do closure; resolver no evento `requestfailed` só aperta, não fecha.
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

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

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
  // `PigBankApp` no UA é o que faz o app-mode.js pôr `html.pb-app`; `?pbspa=1`
  // liga a flag na sessão. Sem os dois, `PBNav.enabled` é false.
  const ctx = await browser.newContext({
    userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) PigBankApp/1.0",
  });
  const page = await ctx.newPage();
  const erros = [];
  page.on("pageerror", e => erros.push(String(e)));

  if (degradar) await page.addInitScript(() => { delete AbortSignal.timeout; });

  // O prefetch do warmUp vai para `/comandos-app` SEM `.html`: o `warmUp`
  // itera as chaves de ROUTES e a primeira do par (`/comandos-app`) já marca
  // `warm.comandos`, então a variante `.html` nunca é aquecida. Abortá-lo faz o
  // `.catch()` apagar warm/inflight, e o tap em `/comandos-app.html` cai no
  // fetch — que é a linha alterada. (Sem este route ele 404aria no
  // `http.server` e daria no mesmo por acidente; explícito não depende disso.)
  const tipos = [];
  let prefetchOk;
  const prefetch = new Promise(ok => { prefetchOk = ok; });
  await page.route("**/comandos-app", r => { prefetchOk(); r.abort(); });
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

  // O warmUp roda 1200ms após o boot; esperar o prefetch queimar (por condição,
  // não por relógio — espera fixa aperta em runner lento). O TETO não é
  // enfeite: `node --test` não tem timeout por caso, então um prefetch que
  // nunca sai penduraria o processo inteiro, sem sumário e sem nome de teste.
  let tTeto;
  await Promise.race([
    prefetch,
    new Promise((_, no) => { tTeto = setTimeout(
      () => no(new Error("o prefetch de /comandos-app não saiu em 10s")), 10_000); }),
  ]).finally(() => clearTimeout(tTeto));

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
