import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { abrirBrowser, fecharBrowser, loadDashboardJs, DASHBOARD_JS } from "./_dashboard_loader.mjs";

const ROOT = join(DASHBOARD_JS, "..");
const HTML = readFileSync(join(ROOT, "dashboard.html"), "utf8").match(/<dialog id="xerife-dialog"[\s\S]*?<\/dialog>/)[0];
const SHOTS = process.env.PIGBANK_SHOTS || "/tmp/pl04-prb-shots";
before(abrirBrowser);
after(fecharBrowser);

async function mount({ width = 1280, light = false } = {}) {
  const page = await loadDashboardJs({ pageOptions: { viewport: { width, height: 900 } } });
  await page.addStyleTag({ path: join(ROOT, "dashboard.css") });
  await page.evaluate(({ html, light }) => {
    document.head.insertAdjacentHTML("beforeend", '<meta name="viewport" content="width=device-width, initial-scale=1">');
    document.body.classList.toggle("light", light);
    document.body.insertAdjacentHTML("beforeend", '<div id="agentes-counters"></div><div id="agentes-shelf" class="ag-shelf"></div><div id="agentes-feed"></div>' + html);
    USER_ID = 77;
    window.csrfHeaders = h => ({ ...h, "X-CSRF-Token": "test-token" });
    window.loadAgentesView = () => {};
    window.__calls = [];
    window.__data = {
      config: { multiplicador: 2.5, minimo: 50, email_enabled: false }, has_more: false,
      lancamentos: [{ id: 101, descricao: '<img src=x onerror="window.__xss=1"> Aluguel', categoria: "Moradia", valor: 1200, criado_em: "2026-10-09T12:00:00Z" }],
      regras: [{ id: "abc-123", descricao: "Academia", categoria: "Saúde", teto: 120, data_fim: null }],
    };
    window.fetch = async (url, opt) => {
      const call = { url: String(url), method: opt.method, body: opt.body && JSON.parse(opt.body), csrf: opt.headers?.["X-CSRF-Token"] };
      window.__calls.push(call);
      if (window.__failure) return new Response(JSON.stringify({ detail: "Erro temporário" }), { status: window.__failure });
      if (opt.method === "GET") return new Response(JSON.stringify(window.__data));
      if (opt.method === "PATCH") Object.assign(window.__data.config, call.body);
      if (opt.method === "PUT") window.__data.lancamentos = [];
      if (opt.method === "DELETE") window.__data.regras = [];
      if (opt.method === "POST") window.__data.regras.push({ id: "new-rule", ...call.body });
      return new Response('{"ok":true}');
    };
    _renderAgentes({ catalog: [{ kind: "xerife", nome: "Xerife", desc: "Alerta gastos", status: "active", disponivel: true }], summary: {} });
  }, { html: HTML, light });
  await page.addScriptTag({ path: join(ROOT, "dashboard-xerife.js") });
  await page.locator("[data-xerife-config]").click();
  await page.waitForFunction(() => !document.getElementById("xerife-content").hidden);
  return page;
}

test("salva sensibilidade e canal com CSRF; reabrir lê os valores persistidos", async () => {
  const page = await mount();
  assert.equal(await page.inputValue('[name="multiplicador"]'), "2.5");
  await page.fill('[name="multiplicador"]', "1.5");
  await page.fill('[name="minimo"]', "75.25");
  await page.check('[name="email_enabled"]');
  await page.locator('#xerife-config-form button[type="submit"]').click();
  await page.waitForFunction(() => document.getElementById("xerife-status").textContent.includes("Configurações salvas"));
  const call = await page.evaluate(() => window.__calls.find(c => c.method === "PATCH"));
  assert.match(call.url, /\/agents\/77\/xerife\/config$/);
  assert.equal(call.csrf, "test-token");
  assert.deepEqual(call.body, { multiplicador: 1.5, minimo: 75.25, email_enabled: true });
  await page.locator("[data-xerife-close]").click();
  await page.locator("[data-xerife-config]").click();
  await page.waitForFunction(() => !document.getElementById("xerife-content").hidden);
  assert.equal(await page.inputValue('[name="multiplicador"]'), "1.5");
  assert.equal(await page.isChecked('[name="email_enabled"]'), true);
  assert.deepEqual(page.__errs, []);
  await page.close();
});

test("desfaz esperado sem perder rascunho, cria e desfaz regra, sem executar HTML do usuário", async () => {
  const page = await mount();
  assert.equal(await page.locator("#xerife-esperados-list img").count(), 0);
  assert.equal(await page.evaluate(() => window.__xss), undefined);
  await page.locator("[data-xerife-rule-from]").click();
  assert.equal(await page.inputValue('#xerife-rule-form [name="categoria"]'), "Moradia");
  assert.equal(await page.inputValue('[name="teto"]'), "1200");
  await page.fill('[name="multiplicador"]', "3");
  await page.locator("[data-xerife-undo]").click();
  await page.waitForFunction(() => !document.querySelector("[data-xerife-undo]"));
  assert.equal(await page.inputValue('[name="multiplicador"]'), "3", "Desfazer apagou o rascunho");
  await page.fill('[name="descricao"]', "Aluguel");
  await page.fill('[name="data_fim"]', "2027-01-31");
  await page.locator('#xerife-rule-form button[type="submit"]').click();
  await page.waitForFunction(() => document.querySelectorAll("[data-xerife-delete]").length === 2);
  const calls = await page.evaluate(() => window.__calls.filter(c => c.method !== "GET"));
  assert.deepEqual(calls[0].body, { esperado: false });
  assert.deepEqual(calls[1].body, { descricao: "Aluguel", categoria: "Moradia", teto: 1200, data_fim: "2027-01-31" });
  assert.ok(calls.every(c => c.csrf === "test-token"));
  await page.locator('[data-xerife-delete="new-rule"]').click();
  await page.waitForFunction(() => !document.querySelector("[data-xerife-delete]"));
  assert.equal(await page.evaluate(() => window.__calls.find(c => c.method === "DELETE").url.endsWith("/regras/new-rule")), true);
  await page.close();
});

test("falha mantém dados, reabilita controles e oferece nova tentativa", async () => {
  const page = await mount();
  await page.evaluate(() => { window.__failure = 500; });
  await page.locator("[data-xerife-undo]").click();
  await page.waitForFunction(() => document.getElementById("xerife-status").textContent.includes("Erro temporário"));
  assert.equal(await page.locator("[data-xerife-undo]").count(), 1);
  assert.equal(await page.locator("[data-xerife-close]").isDisabled(), false);
  await page.locator("[data-xerife-close]").click();
  await page.locator("[data-xerife-config]").click();
  await page.locator("#xerife-retry").waitFor({ state: "visible" });
  await page.evaluate(() => { window.__failure = null; });
  await page.locator("#xerife-retry").click();
  await page.waitForFunction(() => !document.getElementById("xerife-content").hidden);
  await page.keyboard.press("Escape");
  assert.equal(await page.locator("#xerife-dialog").isVisible(), false);
  assert.equal(await page.evaluate(() => document.activeElement.hasAttribute("data-xerife-config")), true);
  await page.close();
});

test("limites nativos impedem envio inválido e paginação usa offset", async () => {
  const page = await mount();
  await page.fill('[name="multiplicador"]', "0.5");
  await page.locator('#xerife-config-form button[type="submit"]').click();
  assert.equal(await page.evaluate(() => window.__calls.some(c => c.method === "PATCH")), false);
  await page.evaluate(() => { window.__data.has_more = true; });
  await page.locator("[data-xerife-close]").click();
  await page.locator("[data-xerife-config]").click();
  await page.locator('[data-xerife-page="next"]').click();
  await page.waitForFunction(() => window.__calls.some(c => c.url.includes("offset=50")));
  await page.locator('[data-xerife-page="prev"]').click();
  await page.waitForFunction(() => window.__calls.at(-1).url.includes("offset=0"));
  await page.close();
});

test("falha ao avançar não pula página; retry repete o destino que falhou", async () => {
  const page = await mount();
  await page.evaluate(() => { window.__data.has_more = true; });
  await page.locator("[data-xerife-close]").click();
  await page.locator("[data-xerife-config]").click();
  await page.locator('[data-xerife-page="next"]').waitFor({ state: "visible" });
  await page.evaluate(() => { window.__failure = 500; });
  await page.locator('[data-xerife-page="next"]').click();
  await page.locator("#xerife-retry").waitFor({ state: "visible" });
  await page.locator('[data-xerife-page="next"]').click();
  await page.waitForFunction(() => window.__calls.filter(c => c.url.includes("offset=50")).length === 2);
  assert.equal(await page.evaluate(() => window.__calls.some(c => c.url.includes("offset=100"))), false);
  await page.evaluate(() => { window.__failure = null; });
  await page.locator("#xerife-retry").click();
  await page.waitForFunction(() => window.__calls.filter(c => c.url.includes("offset=50")).length === 3);
  await page.close();
});

test("fechar depois de salvar devolve foco ao card recriado pela atualização real", async () => {
  const page = await mount();
  await page.evaluate(() => {
    window.loadAgentesView = () => new Promise(resolve => {
      window.__refresh = () => {
        _renderAgentes({ catalog: [{ kind: "xerife", nome: "Xerife", desc: "Alerta gastos", status: "active", disponivel: true }], summary: {} });
        resolve();
      };
    });
  });
  await page.locator('#xerife-config-form button[type="submit"]').click();
  await page.waitForFunction(() => document.getElementById("xerife-status").textContent.includes("Configurações salvas"));
  assert.equal(await page.locator("[data-xerife-close]").isDisabled(), true);
  await page.keyboard.press("Escape");
  assert.equal(await page.locator("#xerife-dialog").isVisible(), true);
  await page.evaluate(() => window.__refresh());
  await page.locator("[data-xerife-close]").click();
  await page.waitForFunction(() => document.activeElement.hasAttribute("data-xerife-config"));
  await page.close();
});

for (const width of [1280, 390, 320]) {
  for (const light of [true, false]) {
    test(`layout ${width}px tema ${light ? "claro" : "escuro"}: dialog legível e sem overflow`, async () => {
      const page = await mount({ width, light });
      await page.locator("#xerife-new-rule summary").click();
      const measure = await page.locator("#xerife-dialog").evaluate(el => ({
        sw: el.scrollWidth, cw: el.clientWidth, w: el.getBoundingClientRect().width,
        controls: [...el.querySelectorAll("button, input")].filter(b => b.getClientRects().length).map(b => b.getBoundingClientRect().height),
      }));
      assert.ok(measure.sw <= measure.cw + 1, JSON.stringify(measure));
      assert.ok(measure.w < width);
      assert.ok(measure.controls.every(h => h >= 44 || h === 20));
      mkdirSync(SHOTS, { recursive: true });
      await page.locator("#xerife-dialog").evaluate(el => { el.scrollTop = 0; });
      await page.screenshot({ path: join(SHOTS, `xerife-${width}-${light ? "light" : "dark"}.png`) });
      assert.deepEqual(page.__errs, []);
      await page.close();
    });
  }
}
