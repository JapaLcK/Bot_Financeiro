/** Onboarding real: contrato de perfil/preferências e PATCH recusado pelo plano. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { startServer } from "./_server.mjs";

let server, origin, browser;
before(async () => {
  ({ proc: server, origin } = await startServer());
  browser = await chromium.launch();
});
after(async () => { await browser?.close(); server?.kill(); });
const json = body => ({ contentType: "application/json", body: JSON.stringify(body) });
const denied = () => ({ status: 403, ...json({ detail: { error: "pro_required", feature: "weekly_report" } }) });

async function wizard({ tier = "essencial", available = false, profileAvailable = available, stale = false, width = 1280 } = {}) {
  const page = await browser.newPage({ viewport: { width, height: 900 } });
  const state = { available, rejectPatch: false, failRead: false, patches: [] };
  await page.route("**/*", route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    return route.fulfill(json({}));
  });
  await page.route("**/auth/dashboard-profile", route => route.fulfill(json({
    user_id: 1, display_name: "Ana", plan: tier, feature_gates: { weekly_report: profileAvailable },
  })));
  await page.route("**/onboarding/state", route => route.fulfill(json({ step: 4, completed: false, total_steps: 5 })));
  await page.route("**/settings/1/notifications", route => {
    if (route.request().method() === "PATCH") {
      const body = route.request().postDataJSON();
      state.patches.push(body);
      if (body.weekly_report_enabled && (!state.available || state.rejectPatch)) return route.fulfill(denied());
      return route.fulfill(json({ ok: true }));
    }
    if (state.failRead) return route.fulfill({ status: 503, ...json({}) });
    return route.fulfill(json({ weekly_report_available: state.available,
      weekly_report_enabled: stale || state.available, daily_report_enabled: true,
      monthly_report_enabled: true, daily_report_hour: 9 }));
  });
  await page.goto(`${origin}/comecar.html`);
  await page.waitForSelector('[data-choice="semanal"]');
  return { page, state };
}

async function save(page, choice) {
  await page.click(`[data-choice="${choice}"]`);
  await page.click('[data-role="save-report"]');
  await page.waitForFunction(() => !document.querySelector('[data-role="save-report"]').hasAttribute("aria-busy"));
}

for (const [tier, available, choice] of [
  ["essencial", false, "diario"], ["plus", true, "semanal"], ["pro", true, "semanal"],
  // O modo legado vem no booleano do servidor, inclusive para o rótulo free.
  ["free", true, "semanal"], ["free", false, "mensal"],
]) {
  test(`${tier}, semanal=${available}: conclui resumo com os três booleanos`, async () => {
    const { page, state } = await wizard({ tier, available });
    assert.equal(await page.locator('[data-choice="semanal"]').isDisabled(), !available);
    await save(page, choice);
    assert.equal(await page.locator('.onb-step[data-step="5"]').isVisible(), true);
    assert.equal(state.patches.length, 1);
    const body = state.patches[0];
    assert.equal(body.daily_report_enabled, choice === "diario");
    assert.equal(body.weekly_report_enabled, choice === "semanal");
    assert.equal(body.monthly_report_enabled, choice === "mensal");
    assert.equal(await page.locator('[data-role="error"]').textContent(), "");
    await page.close();
  });
}

test("preferências novas prevalecem sobre perfil obtido antes do downgrade", async () => {
  const { page, state } = await wizard({ tier: "plus", available: false, profileAvailable: true });
  assert.equal(await page.locator('[data-choice="semanal"]').isDisabled(), true);
  await save(page, "diario");
  assert.equal(state.patches[0].weekly_report_enabled, false);
  assert.equal(await page.locator('.onb-step[data-step="5"]').isVisible(), true);
  await page.close();
});

test("Essencial ignora preferência semanal antiga e salva mensal no celular", async () => {
  const { page, state } = await wizard({ stale: true, width: 390 });
  assert.equal(await page.locator('[data-choice="semanal"]').isDisabled(), true);
  assert.match(await page.locator('[data-choice="semanal"]').textContent(), /Plus e Pro/);
  assert.doesNotMatch(await page.locator('[data-role="report-current"]').textContent(), /semanal/);
  // Mesmo uma ativação sintética não deve marcar uma opção negada.
  await page.locator('[data-choice="semanal"]').dispatchEvent("click");
  assert.equal(await page.locator('[data-role="save-report"]').isDisabled(), true);
  await save(page, "mensal");
  assert.equal(state.patches[0].weekly_report_enabled, false);
  assert.equal(await page.locator('.onb-step[data-step="5"]').isVisible(), true);
  await page.close();
});

for (const race of [false, true]) {
  test(`downgrade ${race ? "entre GET e PATCH" : "antes de salvar"} permite escolher outro resumo`, async () => {
    const { page, state } = await wizard({ tier: "plus", available: true });
    await page.click('[data-choice="semanal"]');
    if (race) state.rejectPatch = true;
    else state.available = false;
    await page.click('[data-role="save-report"]');
    await page.waitForFunction(() => !document.querySelector('[data-role="save-report"]').hasAttribute("aria-busy"));
    assert.equal(state.patches.length, race ? 1 : 0);
    assert.equal(await page.locator('[data-choice="semanal"]').isDisabled(), true);
    assert.equal(await page.locator('[data-choice="semanal"]').getAttribute("aria-checked"), "false");
    assert.equal(await page.locator('[data-role="save-report"]').isDisabled(), true);
    assert.match(await page.locator('[data-role="report-current"]').textContent(), /Escolha outra frequência/);
    assert.equal(await page.locator('[data-role="error"]').textContent(), "");
    await save(page, "nenhum");
    assert.deepEqual(state.patches.at(-1), { daily_report_enabled: false, weekly_report_enabled: false, monthly_report_enabled: false });
    assert.equal(await page.locator('.onb-step[data-step="5"]').isVisible(), true);
    await page.close();
  });
}

test("falha na revalidação não envia semanal e pular não altera preferências", async () => {
  const { page, state } = await wizard({ tier: "plus", available: true });
  state.failRead = true;
  await save(page, "semanal");
  assert.equal(state.patches.length, 0);
  assert.equal(await page.locator('[data-choice="semanal"]').isDisabled(), true);
  await page.click('.onb-step[data-step="4"] [data-action="skip"]');
  assert.equal(await page.locator('.onb-step[data-step="5"]').isVisible(), true);
  assert.equal(state.patches.length, 0);
  await page.close();
});
