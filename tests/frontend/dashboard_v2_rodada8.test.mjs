/**
 * Protótipo dashboard-v2, três correções da revisão do PR #546:
 *
 *   · Simulador (widgets/Simulator.tsx): desligar um chip de simulação pronta desfaz só
 *     as alavancas daquele chip, e ligar um chip soma às alavancas já mexidas (à mão ou
 *     por outro chip) em vez de zerá-las (idem "Simular: <pronta>" da paleta e a ação do Piggy);
 *   · Detalhe da categoria (widgets/CategoryDetail.tsx): a barra do mês corrente diz
 *     "até <dia>" qualquer que seja o mês selecionado;
 *   · Organizar (components/ui/draggable-widget-grid.tsx): depois de mover um item,
 *     a ordem do DOM (a que o Tab e o leitor de tela seguem) é a ordem da tela.
 *
 * Só 1440: a lógica não depende da largura.
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
const ORIGIN = "http://127.0.0.1:1"; // fictícia: a rota atende da raiz do repositório (como em dashboard_v2_organizar)

let browser;
before(async () => {
  execSync("npm --prefix webapp run build:dashboard", { cwd: ROOT, stdio: "pipe" });
  browser = await chromium.launch();
});
after(() => browser?.close());

async function abrir(hash = "#/") {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  await ctx.route("**/*", (r) => {
    const url = new URL(r.request().url());
    if (url.origin !== ORIGIN) return r.abort();
    const path = decodeURIComponent(url.pathname).replace(/\/$/, "/index.html");
    return r.fulfill({ path: join(ROOT, path) }).catch(() => r.fulfill({ status: 404, body: "" }));
  });
  // já escolheu o perfil (Pular): sem isso o modal da 1ª visita cobre o Resumo
  await ctx.addInitScript(() => localStorage.setItem("pigbank.dashboard.profile.v1", '"padrao"'));
  const page = await ctx.newPage();
  await page.goto(`${ORIGIN}/dashboard-v2/${hash}`);
  await page.locator("[data-widget-id], .months").first().waitFor();
  return { ctx, page };
}

const SIM = '[data-widget-id="simulador"]';
const alavanca = (page, nome) => page.locator(`${SIM} .lever`, { hasText: nome }).locator("input[type=range]");

test("Simulador: desligar o chip desfaz só o chip, o ajuste em outra alavanca fica", async () => {
  const { ctx, page } = await abrir();
  const chip = page.locator(`${SIM} .chip`, { hasText: "Delivery pela metade" });
  await chip.click();
  await alavanca(page, "Lazer").focus();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  const lazerAntes = await alavanca(page, "Lazer").inputValue();
  await chip.click();
  const r = [await chip.getAttribute("aria-pressed"), await alavanca(page, "Delivery").inputValue(), await alavanca(page, "Lazer").inputValue()];
  await ctx.close();
  assert.equal(lazerAntes, "10");
  assert.deepEqual(r, ["false", "0", "10"]);
});

test("positivo: o chip sozinho liga e desliga", async () => {
  const { ctx, page } = await abrir();
  const chip = page.locator(`${SIM} .chip`, { hasText: "Delivery pela metade" });
  await chip.click();
  const ligado = [await chip.getAttribute("aria-pressed"), await alavanca(page, "Delivery").inputValue()];
  await chip.click();
  const desligado = [await chip.getAttribute("aria-pressed"), await alavanca(page, "Delivery").inputValue()];
  await ctx.close();
  assert.deepEqual(ligado, ["true", "50"]);
  assert.deepEqual(desligado, ["false", "0"]);
});

test("Simulador: ligar um chip mantém o ajuste feito à mão em outra alavanca", async () => {
  const { ctx, page } = await abrir();
  await alavanca(page, "Lazer").focus();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  const chip = page.locator(`${SIM} .chip`, { hasText: "Delivery pela metade" });
  await chip.click();
  const r = [await chip.getAttribute("aria-pressed"), await alavanca(page, "Delivery").inputValue(), await alavanca(page, "Lazer").inputValue()];
  await ctx.close();
  assert.deepEqual(r, ["true", "50", "10"]);
});

test("Simulador: dois chips ligados juntos; desligar um mantém o outro", async () => {
  const { ctx, page } = await abrir();
  const delivery = page.locator(`${SIM} .chip`, { hasText: "Delivery pela metade" });
  const role = page.locator(`${SIM} .chip`, { hasText: "Um rolê a menos" });
  await delivery.click();
  await role.click();
  const ambos = [await delivery.getAttribute("aria-pressed"), await role.getAttribute("aria-pressed")];
  await delivery.click();
  const r = [await delivery.getAttribute("aria-pressed"), await role.getAttribute("aria-pressed"), await alavanca(page, "Delivery").inputValue(), await alavanca(page, "Lazer").inputValue()];
  await ctx.close();
  assert.deepEqual(ambos, ["true", "true"]);
  assert.deepEqual(r, ["false", "true", "0", "25"]);
});

test("Paleta: 'Simular: <pronta>' mantém o ajuste feito à mão em outra alavanca", async () => {
  const { ctx, page } = await abrir();
  await alavanca(page, "Lazer").focus();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("Control+k");
  await page.keyboard.type("delivery pela metade");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => location.hash === "#/simulador");
  const naPagina = (nome) => page.locator(".lever", { hasText: nome }).locator("input[type=range]"); // /simulador não tem data-widget-id
  const r = [await naPagina("Delivery").inputValue(), await naPagina("Lazer").inputValue()];
  await ctx.close();
  assert.deepEqual(r, ["50", "10"]);
});

test("Piggy: 'Simular delivery −30%' mantém o ajuste feito à mão em outra alavanca", async () => {
  const { ctx, page } = await abrir();
  await alavanca(page, "Lazer").focus();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  await page.locator(".insights").getByRole("button", { name: /Simular delivery/ }).click(); // mês atual: Delivery subiu 20%
  await page.waitForFunction(() => location.hash === "#/simulador");
  const naPagina = (nome) => page.locator(".lever", { hasText: nome }).locator("input[type=range]");
  const r = [await naPagina("Delivery").inputValue(), await naPagina("Lazer").inputValue()];
  await ctx.close();
  assert.deepEqual(r, ["30", "10"]);
});

const rotulos = (page) => page.locator(".months li .faint").allTextContents();

test("Detalhe da categoria: com agosto selecionado, setembro segue 'até 23' (e setembro selecionado também)", async () => {
  const { ctx, page } = await abrir("#/gastos");
  const setembro = await rotulos(page);
  await page.locator('.month-switch button[aria-label="Mês anterior"]:visible').click();
  const agosto = await rotulos(page);
  await ctx.close();
  for (const r of [setembro, agosto]) {
    assert.equal(r.length, 3, String(r));
    assert.match(r[2], / até 23$/, String(r));
    assert.ok(!r[0].includes("até") && !r[1].includes("até"), String(r)); // meses fechados, sem sufixo
  }
});

// Ordem do DOM × ordem da tela (aria-posinset) dos itens do painel.
const ordens = (page) => page.evaluate(() => {
  const ws = [...document.querySelectorAll("[data-widget-id]")];
  return {
    dom: ws.map((w) => w.dataset.widgetId),
    tela: [...ws].sort((a, b) => a.getAttribute("aria-posinset") - b.getAttribute("aria-posinset")).map((w) => w.dataset.widgetId),
  };
});

test("Organizar: depois de Alt+↓ o foco fica no item, o Tab segue a ordem nova e o DOM bate com a tela", async () => {
  const { ctx, page } = await abrir();
  await page.getByRole("button", { name: "Organizar" }).click();
  const primeiro = page.locator('[data-widget-id][tabindex="0"]').first();
  await primeiro.waitFor();
  const inicio = await ordens(page);
  assert.deepEqual(inicio.dom, inicio.tela); // ponto de partida já alinhado
  const id = inicio.tela[0];
  await page.locator(`[data-widget-id="${id}"]`).focus();
  await page.keyboard.press("Alt+ArrowDown");
  await page.waitForTimeout(100);
  const depois = await ordens(page);
  const focado = await page.evaluate(() => document.activeElement?.dataset.widgetId);
  await page.keyboard.press("Tab"); // o ✕ do próprio item vem logo depois dele
  const x = await page.evaluate(() => document.activeElement?.matches("[data-slot=widget-remove]") && document.activeElement.closest("[data-widget-id]").dataset.widgetId);
  await page.keyboard.press("Tab");
  const tab = await page.evaluate(() => document.activeElement?.dataset.widgetId);
  await page.getByRole("button", { name: "Pronto" }).click();
  const fora = await ordens(page);
  await ctx.close();
  assert.notDeepEqual(depois.tela, inicio.tela); // a seta moveu de fato
  assert.equal(focado, id);
  assert.equal(x, id);
  assert.equal(tab, depois.tela[depois.tela.indexOf(id) + 1]);
  assert.deepEqual(depois.dom, depois.tela);
  assert.deepEqual(fora.dom, fora.tela);
});
