/**
 * #1411 no frontend/modals.js (`.pig-modal`), o confirmModal da settings (e da home): o mesmo defeito do /app
 * (confirma_duplo_clique_app.test.mjs), em "Encerrar todas as outras" sessões. Na janela de `JANELA_DO_DUPLO_CLIQUE` os botões não
 * recebem ponteiro, o fundo não fecha e o Enter/Espaço não confirma; o Enter repetido nunca confirma.
 * Controles negativos: tirar o `cedo()` do teclado deixa vermelhos o Enter-Enter e o Espaço-Espaço; tirar o `e.repeat`, o Enter segurado;
 * tirar o `preventDefault` do mousedown na janela deixa o foco no body depois do duplo clique; torná-lo incondicional impede selecionar o texto.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { startServer } from "./_server.mjs";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const JANELA = 450; // acima da constante (400 ms)

const SESSOES = { sessions: [
  { jti: "a", is_current: true, device_label: "Este", ip: "1.1.1.1", created_at: "2026-11-01T12:00:00Z", last_seen_at: "2026-11-10T12:00:00Z" },
  { jti: "b", is_current: false, device_label: "Outro", ip: "2.2.2.2", created_at: "2026-11-01T12:00:00Z", last_seen_at: "2026-11-10T12:00:00Z" },
] };
async function naSettings() {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage();
  const deletes = [];
  const corpo = (b) => ({ status: 200, contentType: "application/json", body: JSON.stringify(b) });
  await page.route("**/*", (r) => {
    const u = new URL(r.request().url());
    if (u.origin !== ORIGIN) return r.abort();
    return /\.[a-z0-9]+$/i.test(u.pathname) ? r.continue() : r.fulfill(corpo({}));
  });
  await page.route("**/auth/validate", (r) => r.fulfill(corpo({ user_id: 1 })));
  await page.route("**/auth/me", (r) => r.fulfill(corpo({ has_password: true })));
  await page.route("**/settings/1/security", (r) => r.fulfill(corpo({ ok: true, user_id: 1, email: "a@b.com", plan: "pro", identities: [] })));
  await page.route("**/settings/1/sessions", (r) => {
    if (r.request().method() === "DELETE") { deletes.push(1); return r.fulfill(corpo({ revoked: 1 })); }
    return r.fulfill(corpo(SESSOES));
  });
  await page.goto(`${ORIGIN}/settings.html`);
  const btn = page.locator("#sessions-revoke-others-btn");
  await btn.waitFor({ state: "visible" });
  // alinha o botão ao primário do modal (no celular os botões do modal ocupam a largura toda)
  const priY = await page.evaluate(async () => {
    document.getElementById("sessions-revoke-others-btn").click(); // o modal de verdade, com o texto de verdade
    await new Promise((r) => setTimeout(r, 300)); // passada a animação de entrada (.18 s)
    const r = document.querySelector(".pig-modal-btn-destructive").getBoundingClientRect();
    document.querySelector(".pig-modal-btn-cancel").click();
    return (r.top + r.bottom) / 2;
  });
  const b0 = await btn.boundingBox();
  await page.evaluate((dy) => window.scrollBy(0, dy), b0.y + b0.height / 2 - priY);
  const b = await btn.boundingBox();
  return { ctx, page, deletes, x: b.x + b.width / 2, y: b.y + b.height / 2 };
}
const pigAberto = (page) => page.locator(".pig-modal-overlay").count();

test("settings 390x844: dois cliques em 'Encerrar todas as outras' (120 ms) não confirmam, e o 2º no fundo não fecha", async () => {
  const { ctx, page, deletes, x, y } = await naSettings();
  try {
    await page.mouse.click(x, y);
    await page.waitForTimeout(120);
    await page.mouse.click(x, y);
    await page.waitForTimeout(400);
    assert.equal(await pigAberto(page), 1, "o 2º clique sobre o primário");
    assert.deepEqual(deletes, []);
    assert.ok(await page.evaluate(() => !!document.activeElement?.closest(".pig-modal")), "o foco fica no diálogo");
    await page.keyboard.press("Escape");
    await page.mouse.click(x, y);
    await page.waitForTimeout(120);
    await page.mouse.click(5, 5);
    await page.waitForTimeout(300);
    assert.equal(await pigAberto(page), 1, "o 2º clique no fundo");
    assert.deepEqual(deletes, []);
  } finally { await ctx.close(); }
});

test("settings 390x844: passada a janela, o primário confirma (1 DELETE) e o fundo cancela", async () => {
  const { ctx, page, deletes, x, y } = await naSettings();
  try {
    await page.mouse.click(x, y);
    await page.waitForTimeout(JANELA);
    await page.mouse.click(5, 5);
    await page.waitForTimeout(200);
    assert.equal(await pigAberto(page), 0);
    assert.deepEqual(deletes, []);
    await page.mouse.click(x, y);
    await page.waitForTimeout(JANELA);
    await page.locator(".pig-modal-btn-destructive").click();
    await page.waitForTimeout(300);
    assert.deepEqual(deletes, [1]);
  } finally { await ctx.close(); }
});

test("settings 390x844: Enter, Enter (120 ms) em 'Encerrar todas as outras' não confirma; Enter passada a janela confirma", async () => {
  const { ctx, page, deletes } = await naSettings();
  try {
    await page.locator("#sessions-revoke-others-btn").focus();
    await page.keyboard.press("Enter");
    await page.waitForTimeout(120);
    await page.keyboard.press("Enter");
    await page.waitForTimeout(300);
    assert.equal(await pigAberto(page), 1);
    assert.deepEqual(deletes, []);
    await page.waitForTimeout(JANELA);
    await page.keyboard.press("Enter");
    await page.waitForTimeout(300);
    assert.deepEqual(deletes, [1]);
  } finally { await ctx.close(); }
});

// A repetição do SO começa depois da janela (≈ 500 ms no macOS e no Windows): só o `e.repeat` a segura.
test("settings 390x844: Enter segurado em 'Encerrar todas as outras' abre o modal e a repetição, passada a janela, não confirma", async () => {
  const { ctx, page, deletes } = await naSettings();
  try {
    await page.locator("#sessions-revoke-others-btn").focus();
    await page.keyboard.down("Enter");
    await page.locator(".pig-modal-overlay").waitFor();
    await page.waitForTimeout(JANELA);
    await page.keyboard.down("Enter"); // já apertada: sai com `repeat: true`
    await page.keyboard.down("Enter");
    await page.keyboard.up("Enter");
    await page.waitForTimeout(300);
    assert.equal(await pigAberto(page), 1);
    assert.deepEqual(deletes, []);
  } finally { await ctx.close(); }
});

// O primário nasce focado e o Espaço o ativa no keyup: o 2º aperto de um Espaço-Espaço rápido confirmaria sem leitura.
test("settings 390x844: Espaço, Espaço (120 ms) em 'Encerrar todas as outras' não confirma; Espaço passada a janela confirma", async () => {
  const { ctx, page, deletes } = await naSettings();
  try {
    await page.locator("#sessions-revoke-others-btn").focus();
    await page.keyboard.press(" ");
    await page.waitForTimeout(120);
    await page.keyboard.press(" ");
    await page.waitForTimeout(300);
    assert.equal(await pigAberto(page), 1);
    assert.deepEqual(deletes, []);
    await page.waitForTimeout(JANELA);
    await page.keyboard.press(" ");
    await page.waitForTimeout(300);
    assert.deepEqual(deletes, [1]);
  } finally { await ctx.close(); }
});

// O mousedown só é bloqueado na janela: depois dela, dá para selecionar e copiar o texto da confirmação.
test("settings 390x844: passada a janela, arrastar seleciona o texto da confirmação", async () => {
  const { ctx, page } = await naSettings();
  try {
    await page.locator("#sessions-revoke-others-btn").click();
    await page.waitForTimeout(JANELA);
    const b = await page.locator(".pig-modal-body").boundingBox();
    await page.mouse.move(b.x + 2, b.y + 5);
    await page.mouse.down();
    await page.mouse.move(b.x + b.width - 5, b.y + 5, { steps: 5 });
    await page.mouse.up();
    assert.ok((await page.evaluate(() => String(getSelection()))).length > 5);
  } finally { await ctx.close(); }
});
