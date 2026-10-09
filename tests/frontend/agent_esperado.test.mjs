/**
 * PL-04: botão "Era esperado" no feed do Xerife.
 *
 * Roda o dashboard.js REAL (o template do feed, `_renderAgentes`) e o dashboard-agent-esperado.js
 * REAL, com o CSS real. O `fetch` é um stub que registra o PUT. Controle negativo: sem o arquivo
 * novo carregado, o clique não chama a API e o item não sai (teste "clique chama o PUT").
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { abrirBrowser, fecharBrowser, loadDashboardJs, DASHBOARD_JS } from "./_dashboard_loader.mjs";

const FRONTEND = join(DASHBOARD_JS, "..");
const CSS = join(FRONTEND, "dashboard.css");
const ESPERADO_JS = join(FRONTEND, "dashboard-agent-esperado.js");
const SHOTS = process.env.PIGBANK_SHOTS || mkdtempSync(join(tmpdir(), "pb-esperado-"));

before(abrirBrowser);
after(fecharBrowser);

const XSS = `<img src=x onerror="window.__pwn=1"> Alimentação`;
const ev = (id, p, kind = "xerife") => ({ id, kind, fired_at: "2026-10-09T10:00:00Z", seen_at: null, channel: "dashboard", payload: p });
const EVENTOS = [
  ev(1, { tipo: "anomalia", launch_id: 101, categoria: XSS, titulo: "t", mensagem: `R$ 480,00 em ${XSS} — 3,2x a sua média. Era esperado? Marque no painel que eu deixo de contar esse gasto.` }),
  ev(2, { tipo: "anomalia", launch_id: 102, categoria: "Lazer", titulo: "t", mensagem: "segundo alerta" }),
  ev(3, { tipo: "limite", titulo: "Limite", mensagem: "limite estourado" }),
  ev(4, { tipo: "anomalia", titulo: "sem id", mensagem: "alerta antigo sem launch_id" }),
  ev(5, { tipo: "anomalia", launch_id: 105, mensagem: "de outro agente" }, "detetive"),
];

/** `fetch` que só responde 200 quando o teste chama `window.__soltar()`. Roda dentro da página. */
const FETCH_PENDENTE = () => {
  window.__soltar = null;
  window.fetch = (url, opt) => {
    window.__calls.push({ url: String(url), method: opt.method, body: opt.body, csrf: opt.headers["X-CSRF-Token"] });
    return new Promise((ok) => { window.__soltar = () => ok(new Response("{}", { status: 200 })); });
  };
};

/** Monta a página: dashboard.js real + CSS real + feed renderizado + (opcional) o script novo. */
async function montar({ viewport = { width: 1280, height: 900 }, mobile = false, comScript = true, fetchImpl } = {}) {
  const page = await loadDashboardJs({ pageOptions: { viewport, hasTouch: mobile, isMobile: mobile } });
  await page.evaluate(() => document.head.insertAdjacentHTML("beforeend", '<meta name="viewport" content="width=device-width, initial-scale=1">'));
  await page.addStyleTag({ path: CSS });
  await page.evaluate(() => {
    document.body.insertAdjacentHTML("beforeend",
      '<div id="toast"></div><div id="agentes-counters"></div><div id="agentes-shelf" class="ag-shelf"></div><div id="agentes-feed" class="ag-feed"></div>');
    USER_ID = 77;
    window.__calls = []; window.__upgrade = 0; window.__loads = 0;
    // about:blank não lê cookie: o token entra pelo mesmo ponto que o dashboard.js usa (csrfHeaders)
    window.csrfHeaders = (extra = {}) => ({ ...extra, "X-CSRF-Token": "tok" });
    window.showUpgradeModal = () => { window.__upgrade += 1; };
    window.loadAgentesView = () => { window.__loads += 1; };
  });
  await page.evaluate(fetchImpl || (() => {
    window.fetch = (url, opt) => { window.__calls.push({ url: String(url), method: opt && opt.method, body: opt && opt.body, csrf: opt && opt.headers && opt.headers["X-CSRF-Token"] }); return Promise.resolve(new Response("{}", { status: 200 })); };
  }));
  await page.evaluate((events) => { _agentesCache = { catalog: [], summary: {}, events }; _renderAgentes({ catalog: [], summary: {}, events }); }, EVENTOS);
  if (comScript) await page.addScriptTag({ path: ESPERADO_JS });
  return page;
}

test("o botão só aparece em anomalia do Xerife com launch_id inteiro; texto de usuário não vira HTML", async () => {
  const page = await montar();
  const ids = await page.$$eval("[data-esperado-lancamento]", (bs) => bs.map((b) => b.dataset.esperadoLancamento));
  assert.deepEqual(ids, ["101", "102"]);
  assert.equal(await page.locator(".ag-event").count(), 5);
  assert.equal(await page.evaluate(() => window.__pwn), undefined, "o onerror da categoria executou");
  assert.equal(await page.locator(".ag-event-msg img").count(), 0);
  assert.deepEqual(page.__errs, []);
  await page.close();
});

test("clique chama o PUT certo, com CSRF, e o item só sai depois do 200", async () => {
  const page = await montar({ fetchImpl: FETCH_PENDENTE });
  await page.locator('[data-esperado-lancamento="101"]').click();
  await page.waitForFunction(() => window.__calls.length === 1);
  const call = await page.evaluate(() => window.__calls[0]);
  assert.match(call.url, /\/agents\/77\/xerife\/lancamentos\/101\/esperado$/);
  assert.equal(call.method, "PUT");
  assert.equal(call.csrf, "tok");
  assert.deepEqual(JSON.parse(call.body), { esperado: true });
  assert.equal(await page.locator(".ag-event").count(), 5, "o item saiu antes do 200");
  assert.equal(await page.locator('[data-esperado-lancamento="101"]').isDisabled(), true, "sem trava contra duplo clique");
  await page.evaluate(() => window.__soltar());
  await page.waitForFunction(() => document.querySelectorAll(".ag-event").length === 4);
  assert.equal(await page.locator('[data-esperado-lancamento="101"]').count(), 0);
  assert.equal(await page.evaluate(() => _agentesCache.events.some((e) => e.payload.launch_id === 101)), false, "cache da view ficou com o item");
  await page.close();
});

test("falha de rede mantém o item, avisa e reabilita o botão", async () => {
  const page = await montar({ fetchImpl: () => { window.fetch = () => Promise.reject(new TypeError("Failed to fetch")); } });
  const btn = page.locator('[data-esperado-lancamento="101"]');
  await btn.click();
  await page.waitForFunction(() => /Não deu pra marcar/.test(document.getElementById("toast").textContent));
  assert.equal(await page.locator(".ag-event").count(), 5);
  assert.equal(await btn.isDisabled(), false);
  await page.close();
});

test("404 do lançamento (apagado) tira o item do feed e do cache, com o toast novo", async () => {
  const page = await montar({ fetchImpl: () => { window.fetch = () => Promise.resolve(new Response('{"detail":"Lançamento não encontrado."}', { status: 404 })); } });
  await page.locator('[data-esperado-lancamento="102"]').click();
  await page.waitForFunction(() => /não existe mais; tirei o alerta/.test(document.getElementById("toast").textContent));
  assert.equal(await page.locator(".ag-event").count(), 4);
  assert.equal(await page.locator('[data-esperado-lancamento="102"]').count(), 0);
  assert.equal(await page.evaluate(() => _agentesCache.events.some((e) => e.payload.launch_id === 102)), false);
  await page.close();
});

for (const [nome, resp] of [
  ["404 de feature indisponível", () => { window.fetch = () => Promise.resolve(new Response('{"detail":"Feature indisponível."}', { status: 404 })); }],
  ["500", () => { window.fetch = () => Promise.resolve(new Response('{"detail":"boom"}', { status: 500 })); }],
]) {
  test(`${nome} mantém o item e avisa`, async () => {
    const page = await montar({ fetchImpl: resp });
    await page.locator('[data-esperado-lancamento="102"]').click();
    await page.waitForFunction(() => /Não deu pra marcar/.test(document.getElementById("toast").textContent));
    assert.equal(await page.locator(".ag-event").count(), 5);
    await page.close();
  });
}

test("403 pro_required abre o upgrade e mantém o item", async () => {
  const page = await montar({ fetchImpl: () => { window.fetch = () => Promise.resolve(new Response('{"detail":{"error":"pro_required"}}', { status: 403 })); } });
  await page.locator('[data-esperado-lancamento="101"]').click();
  await page.waitForFunction(() => window.__upgrade === 1);
  assert.equal(await page.locator(".ag-event").count(), 5);
  await page.close();
});

test("último item removido recarrega a view (estado vazio)", async () => {
  const page = await montar();
  // deixa só o item 101 no feed: ao removê-lo o feed fica sem `.ag-event`
  await page.evaluate(() => { document.querySelectorAll(".ag-event").forEach((e) => { if (!e.querySelector('[data-esperado-lancamento="101"]')) e.remove(); }); });
  await page.locator('[data-esperado-lancamento="101"]').click();
  await page.waitForFunction(() => window.__loads === 1);
  await page.close();
});

test("sem o arquivo novo o clique não faz nada (controle negativo do grupo)", async () => {
  const page = await montar({ comScript: false });
  await page.locator('[data-esperado-lancamento="101"]').click();
  await page.waitForTimeout(150);
  assert.equal(await page.evaluate(() => window.__calls.length), 0);
  assert.equal(await page.locator(".ag-event").count(), 5);
  await page.close();
});

for (const [nome, viewport, mobile] of [["desktop", { width: 1280, height: 900 }, false], ["mobile", { width: 390, height: 844 }, true]]) {
  test(`layout ${nome}: sem overflow horizontal e botão tocável`, async () => {
    const page = await montar({ viewport, mobile });
    const m = await page.evaluate(() => {
      const b = document.querySelector("[data-esperado-lancamento]").getBoundingClientRect();
      const feed = document.getElementById("agentes-feed").getBoundingClientRect();
      return { sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
               w: b.width, h: b.height, dentro: b.left >= feed.left - 0.5 && b.right <= feed.right + 0.5 };
    });
    console.log(`[${nome}]`, JSON.stringify(m));
    assert.ok(m.sw <= m.cw, `overflow horizontal: ${m.sw} > ${m.cw}`);
    assert.ok(m.dentro, "botão fora do card");
    assert.ok(m.h >= (mobile ? 44 : 36), `alvo de toque pequeno: ${m.h}`);
    await page.waitForTimeout(600);                   // deixa a animação de entrada do feed terminar
    await page.locator("#agentes-feed").screenshot({ path: join(SHOTS, `esperado-${nome}.png`) });
    await page.close();
  });
}

test("acessibilidade: o botão diz de qual gasto é e o foco não cai no BODY ao remover o item", async () => {
  const page = await montar();
  const rotulos = await page.$$eval("[data-esperado-lancamento]", (bs) => bs.map((b) => b.getAttribute("aria-label")));
  assert.equal(rotulos[1], "Marcar como esperado: Lazer");
  assert.equal(rotulos[0], `Marcar como esperado: ${XSS}`.slice(0, "Marcar como esperado: ".length + 40));
  assert.equal(await page.locator(".ag-event-msg img, [data-esperado-lancamento] img").count(), 0);

  // remove o 101: o foco vai para o próximo botão (102)
  await page.locator('[data-esperado-lancamento="101"]').click();
  await page.waitForFunction(() => document.querySelectorAll("[data-esperado-lancamento]").length === 1);
  assert.equal(await page.evaluate(() => document.activeElement.dataset.esperadoLancamento), "102");

  // remove o 102 (último botão): o foco vai para o container do feed
  await page.locator('[data-esperado-lancamento="102"]').click();
  await page.waitForFunction(() => document.querySelectorAll("[data-esperado-lancamento]").length === 0);
  assert.equal(await page.evaluate(() => document.activeElement.id), "agentes-feed");
  await page.close();
});

test("foco: quem digitou em outro campo durante o PUT não tem o foco roubado", async () => {
  const page = await montar({ fetchImpl: FETCH_PENDENTE });
  await page.evaluate(() => document.body.insertAdjacentHTML("beforeend", '<input id="outro">'));
  await page.locator('[data-esperado-lancamento="101"]').click();
  await page.waitForFunction(() => window.__calls.length === 1);
  await page.locator("#outro").focus();
  await page.evaluate(() => window.__soltar());
  await page.waitForFunction(() => document.querySelectorAll("[data-esperado-lancamento]").length === 1);
  assert.equal(await page.evaluate(() => document.activeElement.id), "outro");
  await page.close();
});

test("foco: feed redesenhado durante o PUT não leva o foco para o primeiro botão; o item some", async () => {
  const page = await montar({ fetchImpl: FETCH_PENDENTE });
  await page.locator('[data-esperado-lancamento]').nth(1).click();          // 102, com foco nele
  await page.waitForFunction(() => window.__calls.length === 1);
  await page.evaluate((events) => _renderAgentes({ catalog: [], summary: {}, events }), EVENTOS);
  await page.evaluate(() => window.__soltar());
  await page.waitForFunction(() => !document.querySelector('[data-esperado-lancamento="102"]'));
  assert.equal(await page.evaluate(() => !!document.activeElement.closest("[data-esperado-lancamento]")), false);
  assert.equal(await page.locator('[data-esperado-lancamento="101"]').count(), 1);
  await page.close();
});
