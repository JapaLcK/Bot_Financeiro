/**
 * Protótipo dashboard-v2, paleta ⌘K (webapp/src/dashboard/parts/Command.tsx):
 *
 *   · sem <dialog> (Safari < 15.4, alvo do build): o fallback abre pelo atributo `open`.
 *     Enquanto aberta, Tab/Shift+Tab e foco programático não saem do campo, e o fundo
 *     (mês, navegação) não muda; Esc devolve o foco a quem abriu, Enter executa, ⌘K fecha.
 *     O <dialog> é anulado por init script (sem showModal/close), como o Safari 14 o vê;
 *   · com <dialog> nativo: o mesmo roteiro, sem os atributos do fallback.
 *
 * 1440 e 390: o gatilho muda de forma no celular, a paleta é a mesma.
 *
 * Rodar:  npm run test:frontend   (o `before` gera o bundle, gitignored, em dashboard-v2/)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { execSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const ORIGIN = "http://127.0.0.1:1"; // atendida da raiz do repositório pela rota, como no organizar

let browser;
before(async () => {
  execSync("npm --prefix webapp run build:dashboard", { cwd: ROOT, stdio: "pipe" });
  browser = await chromium.launch();
});
after(() => browser?.close());

async function abrir(width, semDialog) {
  const ctx = await browser.newContext({ viewport: { width, height: width > 500 ? 1000 : 844 }, reducedMotion: "reduce" });
  await ctx.route("**/*", (r) => {
    const url = new URL(r.request().url());
    if (url.origin !== ORIGIN) return r.abort();
    const path = decodeURIComponent(url.pathname).replace(/\/$/, "/index.html");
    return r.fulfill({ path: join(ROOT, path) }).catch(() => r.fulfill({ status: 404, body: "" }));
  });
  // já escolheu o perfil (Pular): sem isso o modal da 1ª visita cobre o Resumo
  await ctx.addInitScript(() => localStorage.setItem("pigbank.dashboard.profile.v1", '"padrao"'));
  const page = await ctx.newPage();
  if (semDialog) await page.addInitScript(() => { delete HTMLDialogElement.prototype.showModal; delete HTMLDialogElement.prototype.close; });
  await page.goto(`${ORIGIN}/dashboard-v2/#/`);
  await page.locator(".cmd-trigger").waitFor();
  return { ctx, page };
}

// Onde está o foco: "campo" (input da paleta), "paleta" (outro ponto dela), ou o fundo.
const foco = (page) => page.evaluate(() => {
  const a = document.activeElement;
  if (a?.matches(".cmdk input")) return "campo";
  if (a?.closest(".cmdk")) return "paleta";
  return a === document.body ? "body" : `FUNDO:${a?.getAttribute("aria-label") || a?.textContent.trim().slice(0, 20) || a?.tagName}`;
});
const fundo = (page) => page.evaluate(() => `${location.hash} | ${document.querySelector(".month-title").textContent}`);
const aberta = (page) => page.locator(".cmdk").evaluate((d) => d.hasAttribute("open"));

for (const semDialog of [true, false]) for (const width of [1440, 390]) {
  test(`${semDialog ? "fallback sem <dialog>" : "<dialog> nativo"} ${width}: foco fica na paleta, Esc/Enter/⌘K`, async () => {
    const { ctx, page } = await abrir(width, semDialog);
    const d = page.locator(".cmdk");
    assert.equal(await d.evaluate((el) => el.classList.contains("cmdk-fb")), semDialog); // a simulação pegou
    const antes = await fundo(page);

    await page.locator(".cmd-trigger").focus();
    await page.keyboard.press("Enter");
    assert.ok(await aberta(page));
    assert.deepEqual(await d.evaluate((el) => [el.getAttribute("role"), el.getAttribute("aria-modal")]), semDialog ? ["dialog", "true"] : [null, null]);

    const seq = [];
    for (const k of [...Array(10).fill("Tab"), ...Array(10).fill("Shift+Tab")]) { await page.keyboard.press(k); seq.push(await foco(page)); }
    await page.evaluate(() => document.querySelector('[aria-label="Mês anterior"]').focus()); // leitor de tela / script
    seq.push(await foco(page));
    await page.keyboard.press(" "); // ativaria o botão de mês se o foco tivesse ficado nele
    assert.deepEqual(seq.filter((s) => s.startsWith("FUNDO")), [], seq.join(" "));
    if (semDialog) assert.deepEqual([...new Set(seq)], ["campo"], seq.join(" "));
    assert.equal(await fundo(page), antes);

    await page.keyboard.press("Escape");
    assert.equal(await aberta(page), false);
    assert.equal(await page.evaluate(() => document.activeElement?.className), "cmd-trigger");

    await page.keyboard.press("Enter"); // reabre pelo gatilho, que recebeu o foco de volta
    await page.keyboard.press("ArrowDown"); // 2º item: Ir para Previsão
    await page.keyboard.press("Enter");
    assert.equal(await aberta(page), false);
    assert.equal(await page.evaluate(() => location.hash), "#/previsao");

    await page.keyboard.press("Control+k");
    assert.ok(await aberta(page));
    await page.keyboard.press("Control+k");
    assert.equal(await aberta(page), false);
    await page.keyboard.press("Tab"); // fechada, o Tab volta a andar pela página
    assert.notEqual(await foco(page), "campo");
    await ctx.close();
  });
}

test("fallback sem <dialog>: dash:command com a paleta já aberta não rouba a volta do foco", async () => {
  const { ctx, page } = await abrir(1440, true);
  await page.locator(".cmd-trigger").focus();
  await page.keyboard.press("Enter");
  await page.evaluate(() => window.dispatchEvent(new Event("dash:command"))); // 2º disparo, ela aberta
  assert.ok(await aberta(page));
  assert.equal(await foco(page), "campo");
  await page.keyboard.press("Escape");
  assert.equal(await aberta(page), false);
  assert.equal(await page.evaluate(() => document.activeElement?.className), "cmd-trigger");
  await ctx.close();
});
