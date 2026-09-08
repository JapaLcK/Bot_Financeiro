/**
 * Concorrência do fan-out em frontend/settings.html.
 *
 * UM invariante, e cada teste abaixo existe pra discriminar uma forma de
 * quebrá-lo (rodar contra a versão sabotada correspondente tem que dar VERMELHO
 * — asserção que passa nas duas versões é peso morto):
 *
 *   FAN-OUT: o bumper MAIS RECENTE da geração é dono dos filhos (_securityGen →
 *   atividade + sessões; _dataGen → caixinhas) E do desfecho. Um load superado
 *   pula o fan-out porque quem o superou é o dono — então a geração corrente NÃO
 *   pode sair por throw antes de disparar os filhos, e tem que ESPERAR eles
 *   assentarem antes de soltar o PTR, propagando a falha (do pai, se houver) sem
 *   rejeição solta. A recíproca vale: uma geração superada DURANTE o fan-out
 *   também não propaga — quem a superou é o dono do desfecho.
 *
 * Fora de escopo de propósito: o placeholder estático ("Carregando…") dos
 * painéis quando o GET falha com propagate. O comportamento aceito é preservar o
 * conteúdo atual — que no primeiro load É o placeholder.
 *
 * Rodar:  npm run test:frontend
 * Precisa de `npm ci` na raiz (playwright) + `npx playwright install chromium`.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const FRONTEND = join(REPO, "frontend");
const PORT = Number(process.env.PB_TEST_PORT || 8899);
const ORIGIN = `http://127.0.0.1:${PORT}`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const json = (body) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const err500 = (detail) => ({ status: 500, contentType: "application/json", body: JSON.stringify({ detail }) });

async function startServer() {
  const proc = spawn("python3", ["-m", "http.server", String(PORT), "--bind", "127.0.0.1", "--directory", FRONTEND],
    { stdio: "ignore" });
  for (let i = 0; i < 100; i++) {
    await sleep(100);
    // Porta ocupada por outro processo: o python morre na hora ("Address already
    // in use"). Sem esta checagem a sonda adotava o servidor alheio em silêncio.
    if (proc.exitCode !== null) throw new Error(`http.server morreu (porta ${PORT} ocupada?)`);
    try { if ((await fetch(`${ORIGIN}/settings.html`)).ok) return proc; } catch { /* ainda subindo */ }
  }
  proc.kill();
  throw new Error(`http.server não subiu em ${ORIGIN}`);
}

/** Espera cond() virar true; estoura em vez de pendurar o teste pra sempre. */
async function waitFor(cond, what, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await cond()) return;
    await sleep(50);
  }
  throw new Error(`timeout esperando: ${what}`);
}

let server, browser;
before(async () => { server = await startServer(); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

/**
 * Página com o baseline de rotas. Catch-all registrado PRIMEIRO = menor
 * prioridade no Playwright (casa na ordem inversa de registro): asset com
 * extensão vai pro http.server, o resto é API e vira {} — assim nenhum 404 gera
 * ruído. Cada teste registra DEPOIS as rotas que quer controlar.
 */
async function newPage() {
  const page = await browser.newPage();
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return route.abort();          // CDN (pluggy) bloqueado
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    return route.fulfill(json({}));
  });
  // /static/auth-refresh.js: o path servido é /static/, o arquivo mora na raiz de frontend/.
  await page.route("**/static/auth-refresh.js", (route) =>
    route.fulfill({ status: 200, contentType: "application/javascript",
                    body: readFileSync(join(FRONTEND, "auth-refresh.js"), "utf8") }));
  await page.route("**/auth/validate", (route) => route.fulfill(json({ user_id: 1 })));
  await page.route("**/auth/me", (route) => route.fulfill(json({})));
  await page.addInitScript(() => {
    window.__unhandled = [];
    window.addEventListener("unhandledrejection", (e) =>
      window.__unhandled.push(String((e.reason && e.reason.message) || e.reason)));
  });
  return page;
}

/** Dispara o PTR guardando o desfecho em window.__done (null = ainda em voo). */
const startPtr = (page) => page.evaluate(() => {
  window.__done = null;
  window.PBRefresh().then(() => "resolved", (e) => "rejected:" + ((e && e.message) || e))
    .then((v) => { window.__done = v; });
});

const SECURITY_OK = { email: "a@b.com", phone: "5511999999999", plan: "pro", display_name: "Japa" };
const ONE_EVENT = { events: [{ event_type: "login_from_new_ip", created_at: new Date().toISOString() }] };
const ONE_SESSION = { sessions: [{ jti: "a", is_current: true, user_agent: "Chrome", created_at: new Date().toISOString() }] };

/**
 * O cenário do relato original — pai falhando COM um load anterior em voo:
 *   1. GET /security da abertura da aba (geração 1) fica SEGURADO;
 *   2. PBRefresh() dispara a geração 2 com propagate:true;
 *   3. o GET da geração 2 responde 500;
 *   4. só então a geração 1 é liberada com 200 (chega superada, não pinta).
 *
 * Sem o `ownErr` (a geração 2 saindo por throw direto do catch): 0 chamadas a
 * /activity e /sessions, e os dois painéis ficam órfãos no "Carregando…" —
 * ninguém mais vai refazer o fan-out, porque a geração 1 é a superada.
 */
test("segurança: GET que falha durante um load em voo não deixa os filhos órfãos", async () => {
  const page = await newPage();
  const counts = { activity: 0, sessions: 0 };
  const securityCalls = [];   // route objects, na ordem de chegada
  try {
    await page.route("**/auth/mfa/status", (route) => route.fulfill(json({ enabled: false })));
    await page.route("**/settings/1/activity*", (route) => { counts.activity++; route.fulfill(json(ONE_EVENT)); });
    await page.route("**/settings/1/sessions", (route) => { counts.sessions++; route.fulfill(json(ONE_SESSION)); });
    // Fila: 1ª chamada segurada (liberada à mão lá embaixo), 2ª responde 500.
    await page.route("**/settings/1/security", (route) => {
      securityCalls.push(route);
      if (securityCalls.length === 2) route.fulfill(err500("boom"));
    });

    await page.goto(`${ORIGIN}/settings.html?view=security`);

    // (1) geração 1 em voo, segurada.
    await waitFor(() => securityCalls.length === 1, "o GET de segurança da abertura da aba");
    assert.equal(counts.activity, 0, "fan-out não pode ter rodado antes do GET de segurança assentar");

    // (2) pull-to-refresh → geração 2.
    await startPtr(page);

    // (3) geração 2 chega e leva 500 do handler acima.
    await waitFor(() => securityCalls.length === 2, "o GET de segurança do pull-to-refresh");

    // (4) só agora libera a geração 1 com sucesso — chega por último e é superada.
    await securityCalls[0].fulfill(json(SECURITY_OK));

    await waitFor(() => page.evaluate(() => window.__done !== null), "PBRefresh assentar");
    await sleep(300);   // deixa a geração 1 (superada) terminar de não fazer nada

    const dom = await page.evaluate(() => {
      const txt = (id) => (document.getElementById(id) || {}).textContent || "";
      return {
        activity: txt("activity-list"), sessions: txt("sessions-list"),
        done: window.__done, unhandled: window.__unhandled,
      };
    });

    // alguém chamou os filhos, exatamente uma vez — sem duplicar.
    assert.equal(counts.activity, 1, "GETs de /activity (esperado 1, o fan-out da geração dona)");
    assert.equal(counts.sessions, 1, "GETs de /sessions (esperado 1, o fan-out da geração dona)");
    assert.ok(!dom.activity.includes("Carregando"), `#activity-list preso: ${dom.activity.trim()}`);
    assert.ok(!dom.sessions.includes("Carregando"), `#sessions-list preso: ${dom.sessions.trim()}`);

    // contrato do PTR: continua rejeitando pro indicador virar âmbar.
    assert.match(dom.done, /^rejected:/, `PBRefresh devia rejeitar, deu: ${dom.done}`);
    assert.deepEqual(dom.unhandled, [], "rejeição solta na página");
  } finally { await page.close(); }
});

/**
 * Com o FILHO falhando — o furo que a versão anterior deste arquivo não
 * cobria. Pai 200, /sessions do PTR segurado e depois 500.
 *
 * Contra a sabotagem "fire-and-forget" (fan-out sem await, `if (ownErr) throw`):
 *   - o PTR settla com o filho ainda em voo  → assert de __done === null falha;
 *   - o PTR reporta SUCESSO                  → assert /^rejected:/ falha;
 *   - a falha do filho vira rejeição solta   → assert de __unhandled falha.
 */
test("segurança: PTR não solta o indicador com filho em voo e propaga a falha dele", async () => {
  const page = await newPage();
  const counts = { security: 0, activity: 0, sessions: 0 };
  const heldSessions = [];
  try {
    await page.route("**/auth/mfa/status", (route) => route.fulfill(json({ enabled: false })));
    await page.route("**/settings/1/security", (route) => { counts.security++; route.fulfill(json(SECURITY_OK)); });
    await page.route("**/settings/1/activity*", (route) => { counts.activity++; route.fulfill(json(ONE_EVENT)); });
    // 1ª (load inicial) OK; 2ª (PTR) fica segurada e depois falha.
    await page.route("**/settings/1/sessions", (route) => {
      counts.sessions++;
      if (counts.sessions === 1) return route.fulfill(json(ONE_SESSION));
      heldSessions.push(route);
    });

    await page.goto(`${ORIGIN}/settings.html?view=security`);
    await waitFor(() => counts.sessions === 1, "load inicial da aba");

    await startPtr(page);
    // Tudo do PTR já assentou, MENOS o filho segurado: se o indicador soltar
    // agora, soltou com um GET em voo (que podia repintar por cima depois).
    await waitFor(() => counts.security === 2 && counts.activity === 2 && heldSessions.length === 1,
      "o PTR chegar em todos os GETs, com /sessions segurado");
    await sleep(250);
    assert.equal(await page.evaluate(() => window.__done), null,
      "PBRefresh settlou com /sessions ainda em voo");

    await heldSessions[0].fulfill(err500("FILHO-SESSIONS-500"));
    await waitFor(() => page.evaluate(() => window.__done !== null), "PBRefresh assentar após o filho");
    await sleep(200);

    const out = await page.evaluate(() => ({ done: window.__done, unhandled: window.__unhandled }));
    assert.equal(out.done, "rejected:FILHO-SESSIONS-500",
      `falha do filho tinha que virar rejeição do PTR, deu: ${out.done}`);
    assert.deepEqual(out.unhandled, [], "rejeição solta na página");
  } finally { await page.close(); }
});

/**
 * Regra "erro do pai ganha do erro do filho": pai 500 E filho 500 juntos.
 * Contra a sabotagem `ownErr = childErr` (sem o `if (!ownErr)`) o PTR rejeita
 * com FILHO-SESSIONS-500 e este assert fica vermelho.
 */
test("segurança: com pai e filho falhando, o PTR rejeita com o erro do pai", async () => {
  const page = await newPage();
  const counts = { security: 0, sessions: 0 };
  try {
    await page.route("**/auth/mfa/status", (route) => route.fulfill(json({ enabled: false })));
    await page.route("**/settings/1/activity*", (route) => route.fulfill(json(ONE_EVENT)));
    await page.route("**/settings/1/security", (route) => {
      counts.security++;
      route.fulfill(counts.security === 1 ? json(SECURITY_OK) : err500("PAI-SECURITY-500"));
    });
    await page.route("**/settings/1/sessions", (route) => {
      counts.sessions++;
      route.fulfill(counts.sessions === 1 ? json(ONE_SESSION) : err500("FILHO-SESSIONS-500"));
    });

    await page.goto(`${ORIGIN}/settings.html?view=security`);
    await waitFor(() => counts.sessions === 1, "load inicial da aba");

    await startPtr(page);
    await waitFor(() => page.evaluate(() => window.__done !== null), "PBRefresh assentar");
    await sleep(200);

    const out = await page.evaluate(() => ({
      done: window.__done, unhandled: window.__unhandled,
      sessions: document.getElementById("sessions-list").textContent,
    }));
    assert.equal(out.done, "rejected:PAI-SECURITY-500", `erro do pai devia ganhar, deu: ${out.done}`);
    assert.deepEqual(out.unhandled, [], "rejeição solta na página");
    // propagate preserva conteúdo real: a sessão do load inicial continua lá.
    assert.ok(out.sessions.includes("Esta sessão"), `filho apagou conteúdo bom: ${out.sessions.trim()}`);
  } finally { await page.close(); }
});

/**
 * No Open Finance — mesmo defeito de forma do fan-out de segurança, outro
 * loader: loadData é o único que bumpa _dataGen, logo é dono do loadCaixinhas.
 * Com o loadCaixinhas DENTRO do try, a geração que falha com propagate sai por
 * throw antes dele e a geração superada cai no guard: ninguém o chama.
 */
test("open finance: caixinhas não fica órfão quando o loadData falha com um load em voo", async () => {
  const page = await newPage();
  const counts = { caixinhas: 0 };
  const ofCalls = [];
  try {
    // of_ui_enabled: sem isso o initSettings tira "open-finance" das views
    // permitidas e reescreve a URL pra ?view=security — o PBRefresh lê a URL e
    // cairia na branch errada (o loadData nem seria chamado).
    await page.route("**/auth/me", (route) => route.fulfill(json({ of_ui_enabled: true })));
    await page.route("**/auth/mfa/status", (route) => route.fulfill(json({ enabled: false })));
    await page.route("**/open-finance/1/caixinhas", (route) => { counts.caixinhas++; route.fulfill(json({ caixinhas: [], metas: [] })); });
    // Fila igual à do teste de segurança: 1ª segurada, 2ª (a do PTR) 500.
    await page.route("**/open-finance/1", (route) => {
      ofCalls.push(route);
      if (ofCalls.length === 2) route.fulfill(err500("of boom"));
    });

    await page.goto(`${ORIGIN}/settings.html?view=open-finance`);
    await waitFor(() => ofCalls.length === 1, "o GET de open-finance da abertura");
    assert.equal(counts.caixinhas, 0, "caixinhas não pode rodar antes do loadData assentar");

    await startPtr(page);
    await waitFor(() => ofCalls.length === 2, "o GET de open-finance do pull-to-refresh");
    await ofCalls[0].fulfill(json({ connections: [], accounts: [], transactions: [] }));

    await waitFor(() => page.evaluate(() => window.__done !== null), "PBRefresh assentar");
    await sleep(300);

    assert.equal(counts.caixinhas, 1, "GETs de /caixinhas (esperado 1, o da geração dona)");
    const out = await page.evaluate(() => ({ done: window.__done, unhandled: window.__unhandled }));
    assert.match(out.done, /^rejected:/, `PBRefresh devia rejeitar, deu: ${out.done}`);
    assert.deepEqual(out.unhandled, [], "rejeição solta na página");
  } finally { await page.close(); }
});

/**
 * A recíproca: geração superada DURANTE o fan-out não tinge o PTR.
 *   1. abertura da aba (geração 1) OK;
 *   2. PBRefresh → geração 2, /security 500 (erro guardado), filhos SEGURADOS;
 *   3. usuário clica "↻ Atualizar" → geração 3, /security 200 com e-mail novo,
 *      pinta a tela e supera a geração 2 no meio do await dela;
 *   4. libera os filhos da geração 2.
 * Sem a re-checagem de geração antes do throw, o PTR rejeita com o erro da
 * geração 2 (morta) enquanto a tela mostra o dado fresco da 3 — indicador âmbar
 * com dado certo. Com ela, o PTR resolve.
 */
test("segurança: load superado durante o fan-out não tinge o PTR de âmbar", async () => {
  const page = await newPage();
  const c = { security: 0, activity: 0, sessions: 0 };
  const held = [];   // filhos da geração 2
  const FRESCO = { ...SECURITY_OK, email: "fresco@b.com" };
  try {
    await page.route("**/auth/mfa/status", (route) => route.fulfill(json({ enabled: false })));
    await page.route("**/settings/1/security", (route) => {
      c.security++;
      if (c.security === 2) return route.fulfill(err500("PAI-GERACAO-2-500"));   // o GET do PTR
      route.fulfill(json(c.security === 1 ? SECURITY_OK : FRESCO));              // abertura / ↻
    });
    await page.route("**/settings/1/activity*", (route) => {
      c.activity++;
      if (c.activity === 2) return held.push(route);
      route.fulfill(json(ONE_EVENT));
    });
    await page.route("**/settings/1/sessions", (route) => {
      c.sessions++;
      if (c.sessions === 2) return held.push(route);
      route.fulfill(json(ONE_SESSION));
    });

    await page.goto(`${ORIGIN}/settings.html?view=security`);
    await waitFor(() => c.sessions === 1, "load inicial da aba");

    await startPtr(page);
    await waitFor(() => held.length === 2, "os filhos da geração 2 segurados");
    await sleep(200);
    assert.equal(await page.evaluate(() => window.__done), null,
      "PBRefresh settlou com o fan-out ainda em voo");

    // (3) o ↻ Atualizar: geração 3 completa inteira (pai + filhos) e pinta.
    await page.evaluate(() => window.loadSecuritySettings());
    await waitFor(() => c.security === 3 && c.activity === 3 && c.sessions === 3, "a geração 3 do ↻");

    // (4) só agora os filhos da geração 2 respondem — ela já está superada.
    for (const route of held) await route.fulfill(json(ONE_EVENT));
    await waitFor(() => page.evaluate(() => window.__done !== null), "PBRefresh assentar");
    await sleep(200);

    const out = await page.evaluate(() => ({
      done: window.__done, unhandled: window.__unhandled,
      email: document.getElementById("security-email-value").textContent,
    }));
    assert.equal(out.email, "fresco@b.com", `a geração 3 devia estar pintada, tela mostra: ${out.email}`);
    assert.equal(out.done, "resolved",
      `geração superada não pode tingir o PTR com dado fresco na tela, deu: ${out.done}`);
    assert.deepEqual(out.unhandled, [], "rejeição solta na página");
  } finally { await page.close(); }
});

/**
 * Mesma recíproca no outro loader (a mesma classe, não a mesma instância):
 * dois pulls seguidos. O 1º pega 500 no /open-finance e fica preso no await do
 * caixinhas; o 2º responde 200 inteiro e supera o 1º nesse meio. Sem a
 * re-checagem de _dataGen antes do throw, o 1º PTR rejeita com o erro da
 * geração morta.
 */
test("open finance: loadData superado durante o caixinhas não tinge o PTR", async () => {
  const page = await newPage();
  const c = { of: 0, caix: 0 };
  const held = [];   // caixinhas da geração 2
  try {
    await page.route("**/auth/me", (route) => route.fulfill(json({ of_ui_enabled: true })));
    await page.route("**/auth/mfa/status", (route) => route.fulfill(json({ enabled: false })));
    await page.route("**/open-finance/1/caixinhas", (route) => {
      c.caix++;
      if (c.caix === 2) return held.push(route);
      route.fulfill(json({ caixinhas: [], metas: [] }));
    });
    await page.route("**/open-finance/1", (route) => {
      c.of++;
      if (c.of === 2) return route.fulfill(err500("OF-GERACAO-2-500"));   // o GET do 1º pull
      route.fulfill(json({ connections: [], accounts: [], transactions: [] }));
    });

    await page.goto(`${ORIGIN}/settings.html?view=open-finance`);
    await waitFor(() => c.caix === 1, "load inicial da aba");

    await page.evaluate(() => {
      window.__p1 = null;
      window.PBRefresh().then(() => "resolved", (e) => "rejected:" + ((e && e.message) || e))
        .then((v) => { window.__p1 = v; });
    });
    await waitFor(() => held.length === 1, "o caixinhas da geração 2 segurado");
    await sleep(200);
    assert.equal(await page.evaluate(() => window.__p1), null, "o 1º PTR settlou com o caixinhas em voo");

    // 2º pull: geração 3 completa e supera a 2 no meio do await dela.
    await page.evaluate(() => {
      window.__p2 = null;
      window.PBRefresh().then(() => "resolved", (e) => "rejected:" + ((e && e.message) || e))
        .then((v) => { window.__p2 = v; });
    });
    await waitFor(() => c.of === 3 && c.caix === 3, "a geração 3 do 2º pull");

    for (const route of held) await route.fulfill(json({ caixinhas: [], metas: [] }));
    await waitFor(() => page.evaluate(() => window.__p1 !== null && window.__p2 !== null), "os dois PTR assentarem");
    await sleep(200);

    const out = await page.evaluate(() => ({ p1: window.__p1, p2: window.__p2, unhandled: window.__unhandled }));
    assert.equal(out.p2, "resolved", `o 2º PTR (a geração viva) devia resolver, deu: ${out.p2}`);
    assert.equal(out.p1, "resolved", `geração superada não pode tingir o PTR, deu: ${out.p1}`);
    assert.deepEqual(out.unhandled, [], "rejeição solta na página");
  } finally { await page.close(); }
});
