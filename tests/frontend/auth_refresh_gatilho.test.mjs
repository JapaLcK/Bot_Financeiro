/**
 * O GATILHO da renovação do `auth-refresh.js` (issue #176).
 *
 * Até aqui o único critério era o status: QUALQUER 401 disparava
 * `/auth/refresh` + retry da request original. Medido num Chromium com
 * `POST /auth/mfa/setup` respondendo 401 "Senha incorreta.":
 *
 *     POST /auth/mfa/setup
 *     POST /auth/refresh
 *     POST /auth/mfa/setup      <- a senha errada ia DE NOVO
 *
 * Duas vezes, ou seja DOIS slots do `@limiter.limit` da rota por digitação
 * errada. `/auth/mfa/regenerate-backup-codes` e `/auth/account/export` são
 * 3/hour: o segundo erro já tomava 429 e trancava o usuário por uma hora.
 *
 * Desde o #176 quem classifica é o SERVIDOR, pelo `WWW-Authenticate`
 * (`WWW_AUTHENTICATE_401` em `frontend/routes/shared.py`, nos 8 `raise` de falha
 * de access/dashboard token). O interceptor renova só quando o header vem.
 *
 * CONTROLES do grupo (§3):
 *   - NEGATIVO — "senha errada não renova": desligando a linha nova do
 *     interceptor este caso vira 2 chamadas à rota e 1 refresh. Verificado por
 *     mutação nesta sessão.
 *   - POSITIVO — "401 de autenticação AINDA renova": sem ele o grupo passaria
 *     num interceptor que nunca renova, que é PIOR que o bug — a mudança faz o
 *     gatilho falhar FECHADO e o usuário cairia no login a cada 15 min.
 *
 * O lado SERVIDOR desta costura (que o header sai vivo de um 401 real) é o
 * `test_401_de_autenticacao_manda_www_authenticate_e_o_de_aplicacao_nao` em
 * `tests/test_auth_cookie.py` — aqui as respostas são mockadas, então este
 * arquivo sozinho só prova o lado do JS. Quem prende as duas listas é o gate
 * `test_401_de_autenticacao_declara_familia` (tests/test_static_pages_routes.py).
 *
 * Rodar:  npm run test:frontend
 *         (um arquivo só: node --test tests/frontend/auth_refresh_gatilho.test.mjs)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

// A marca de família. Valor igual ao do `WWW_AUTHENTICATE_401` do Python — mas o
// que o interceptor testa é a PRESENÇA do header, nunca este texto.
const MARCA = 'Bearer realm="pigbank", error="invalid_token"';

/** 401 de AUTENTICAÇÃO (access token morto): leva a marca, o interceptor renova. */
const r401Auth = {
  status: 401, contentType: "application/json",
  headers: { "WWW-Authenticate": MARCA },
  body: JSON.stringify({ detail: "Token inválido ou expirado." }),
};
/** 401 de APLICAÇÃO (senha errada): sem marca, o token está ótimo. */
const r401App = {
  status: 401, contentType: "application/json",
  body: JSON.stringify({ detail: "Senha incorreta." }),
};
const r200 = (corpo) => ({
  status: 200, contentType: "application/json", body: JSON.stringify(corpo),
});

/**
 * Página mínima que carrega o `auth-refresh.js` REAL (o do disco, servido pelo
 * http.server em /static/) e mais nada — sem dashboard, sem boot, sem corrida.
 *
 * `mapa` responde por pathname; o valor pode ser uma resposta de `route.fulfill`
 * ou uma função `(route, chamadas) => ...` para o caso que muda a cada tentativa.
 * Devolve também a lista de chamadas interceptadas, em ordem.
 */
async function abrir(mapa) {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  const chamadas = [];

  await page.route(`${ORIGIN}/__gatilho.html`, (route) => route.fulfill({
    status: 200, contentType: "text/html",
    body: '<!doctype html><meta charset="utf-8"><title>gatilho</title>'
        + '<script src="/static/auth-refresh.js"></script>',
  }));

  for (const caminho of Object.keys(mapa)) {
    await page.route(`${ORIGIN}${caminho}`, (route) => {
      chamadas.push(`${route.request().method()} ${caminho}`);
      const r = mapa[caminho];
      return typeof r === "function" ? r(route, chamadas) : route.fulfill(r);
    });
  }

  await page.goto(`${ORIGIN}/__gatilho.html`);
  // Sem isto o `window.fetch` do teste podia ser o ORIGINAL: o <script> é
  // clássico e síncrono, mas o `goto` resolve no `load` — provar que o wrapper
  // existe custa uma linha e evita um verde que não mediu nada.
  assert.equal(await page.evaluate(() => typeof window.pbCsrfHeaders), "function",
               "o auth-refresh.js não carregou — o teste não mede o caminho certo");

  return { page, ctx, chamadas };
}

/** `fetch` na página, devolvendo status + corpo (ou a rejeição). */
const chamar = (page, url, init) => page.evaluate(
  ([u, i]) => window.fetch(u, i)
    .then((r) => r.text().then((t) => ({ status: r.status, corpo: t })))
    .catch((e) => ({ rejeitou: String(e && e.message || e) })),
  [url, init || {}]);

test("401 de APLICAÇÃO (senha errada) não renova e não reenvia a senha", async () => {
  const { page, ctx, chamadas } = await abrir({
    "/auth/mfa/setup": r401App,
    "/auth/refresh": r200({ ok: true }),
  });

  const resp = await chamar(page, "/auth/mfa/setup", { method: "POST" });

  // O número que a issue mediu: era 2, tem de ser 1.
  assert.deepEqual(chamadas, ["POST /auth/mfa/setup"],
                   `a senha errada foi reenviada — chamadas: ${JSON.stringify(chamadas)}`);
  assert.equal(resp.status, 401, "o 401 da senha errada tem que chegar ao chamador");
  assert.ok(resp.corpo.includes("Senha incorreta"), "o corpo original não chegou ao chamador");

  await ctx.close();
});

test("401 de AUTENTICAÇÃO ainda renova, refaz a request e devolve o RETRY", async () => {
  // Controle POSITIVO do grupo. Sem ele, um interceptor que parou de renovar
  // passaria nos outros casos — e é a regressão pior que o bug.
  let tentativas = 0;
  const { page, ctx, chamadas } = await abrir({
    "/data/42": (route) => route.fulfill(
      ++tentativas === 1 ? r401Auth : r200({ valor: "depois-do-retry" })),
    "/auth/refresh": r200({ ok: true }),
  });

  const resp = await chamar(page, "/data/42");

  assert.deepEqual(chamadas, ["GET /data/42", "POST /auth/refresh", "GET /data/42"],
                   `esperado 1 refresh + 1 retry — chamadas: ${JSON.stringify(chamadas)}`);
  assert.equal(resp.status, 200);
  assert.ok(resp.corpo.includes("depois-do-retry"),
            "o chamador recebeu a resposta do 401, não a do retry");

  await ctx.close();
});

test("o RETRY continua passando pela limpeza — DELETE /auth/account renovado", async () => {
  // O retry sai por `_requestComLimpeza`, não por `_origFetch`: o access token
  // vence no meio da exclusão, o interceptor renova, o retry dá 200 — e é o
  // RETRY que encerra a sessão. A linha nova entrou ABAIXO do
  // `_requestComLimpeza`, nunca acima, exatamente para não pular isto.
  let tentativas = 0;
  const { page, ctx, chamadas } = await abrir({
    "/auth/account": (route) => route.fulfill(
      ++tentativas === 1 ? r401Auth : r200({ scheduled: true })),
    "/auth/refresh": r200({ ok: true }),
  });

  // Estado de aparelho REAL para a limpeza ter o que apagar: o service worker
  // do repo (que cria o cache `pigbank-vN` no install dele).
  await page.evaluate(() => navigator.serviceWorker.register("/service-worker.js"));
  await page.waitForFunction(async () => (await caches.keys()).length > 0, null,
                             { timeout: 15000 });

  const resp = await chamar(page, "/auth/account", { method: "DELETE" });

  assert.deepEqual(chamadas,
                   ["DELETE /auth/account", "POST /auth/refresh", "DELETE /auth/account"],
                   `chamadas: ${JSON.stringify(chamadas)}`);
  assert.equal(resp.status, 200, "o retry não chegou ao chamador");
  assert.deepEqual(await page.evaluate(() => caches.keys()), [],
                   "o retry encerrou a sessão e o Cache Storage sobreviveu no aparelho");
  assert.deepEqual(
    await page.evaluate(() => navigator.serviceWorker.getRegistrations().then((r) => r.length)),
    0, "o service worker continuou registrado depois da exclusão da conta");

  await ctx.close();
});

test("logout que REJEITA (offline) ainda limpa, e a rejeição sobe ao chamador", async () => {
  // A limpeza central roda no `catch` do `_requestComLimpeza`. A linha nova lê
  // `resp.headers` e só é alcançada quando HÁ resposta — um `return` posto acima
  // do `_requestComLimpeza` teria matado este caminho inteiro.
  const { page, ctx, chamadas } = await abrir({
    "/auth/logout": (route) => route.abort(),
  });

  await page.evaluate(() => navigator.serviceWorker.register("/service-worker.js"));
  await page.waitForFunction(async () => (await caches.keys()).length > 0, null,
                             { timeout: 15000 });

  const resp = await chamar(page, "/auth/logout", { method: "POST" });

  assert.deepEqual(chamadas, ["POST /auth/logout"]);
  assert.ok(resp.rejeitou, `o fetch resolveu em vez de rejeitar: ${JSON.stringify(resp)}`);
  assert.deepEqual(await page.evaluate(() => caches.keys()), [],
                   "logout offline deixou o Cache Storage no aparelho");

  await ctx.close();
});
