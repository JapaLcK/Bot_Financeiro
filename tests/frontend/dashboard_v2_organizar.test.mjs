/**
 * Protótipo dashboard-v2, modo Organizar (webapp/src/components/ui/draggable-widget-grid.tsx):
 * enquanto os widgets são arrastáveis, o conteúdo deles não é interativo pelo teclado.
 *
 *   · com `inert` (Chromium, Safari >= 15.5): o Tab passa pelos itens e sai do grid
 *     sem entrar em controle interno;
 *   · sem `inert` (Safari 14 / 15.0–15.4, alvo do build — webapp/vite.config.js): o
 *     Tab também não entra (o grid tira os controles internos da ordem de Tab e os
 *     devolve no "Pronto"); por clique ou script eles ainda recebem foco, e as teclas
 *     e a digitação neles são recusadas pelos handlers de captura do item. O `inert` é
 *     anulado por init script: o React o põe via setAttribute, e o Chromium não tem
 *     como desligar o nativo; sai também do protótipo, que é onde o grid o detecta;
 *   · controle POSITIVO: Organizar desligado, a mesma tecla no mesmo controle funciona.
 *
 * Só 1440: é no desktop que o teclado é a entrada principal; o grid e os handlers
 * são os mesmos no celular.
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
// Porta fictícia: toda requisição é atendida da raiz do repositório pela rota (como _dock.mjs);
// a página carrega ../frontend/fonts, então servir só dashboard-v2/ não basta.
const ORIGIN = "http://127.0.0.1:1";
const HERO = '[data-widget-id="hero"] [role=radio]';
const NUM = '[data-widget-id="simulador"] input[type=number]';

let browser;
before(async () => {
  execSync("npm --prefix webapp run build:dashboard", { cwd: ROOT, stdio: "pipe" });
  browser = await chromium.launch();
});
after(() => browser?.close());

async function abrir({ semInert = false, organizar = false } = {}) {
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
  if (semInert) await page.addInitScript(() => {
    const sa = Element.prototype.setAttribute, ta = Element.prototype.toggleAttribute;
    Element.prototype.setAttribute = function (n, v) { if (n !== "inert") return sa.call(this, n, v); };
    Element.prototype.toggleAttribute = function (n, f) { return n === "inert" ? false : ta.call(this, n, f); };
    delete HTMLElement.prototype.inert;
  });
  await page.goto(`${ORIGIN}/dashboard-v2/#/`);
  await page.locator(HERO).first().waitFor();
  if (organizar) {
    await page.getByRole("button", { name: "Organizar" }).click();
    await page.locator('[data-widget-id][tabindex="0"]').first().waitFor();
  }
  return { ctx, page };
}

// Foca o seletor como o Tab faria (sem `inert` ele é alcançável) e confirma que focou.
async function focar(page, sel) {
  assert.ok(await page.evaluate((s) => { const el = document.querySelector(s); el?.focus(); return document.activeElement === el; }, sel), `não focou ${sel}`);
}
// Onde o foco para em 24 Tabs a partir do botão de Organizar/Pronto. ROLAGEM é uma lista rolável
// sem controle focável dentro: o Chromium a põe na ordem de Tab, o Safari não (e com `inert` ela não entra).
// O ✕ de esconder (remover:<id>) fica fora do conteúdo inerte: no Organizar ele vem depois de cada item.
async function tabs(page, nome) {
  await page.getByRole("button", { name: nome }).focus();
  const seq = [];
  for (let i = 0; i < 24; i++) {
    await page.keyboard.press("Tab");
    seq.push(await page.evaluate(() => {
      const a = document.activeElement, item = a.closest("[data-widget-id]");
      if (!item) return "fora";
      if (a === item) return `item:${item.dataset.widgetId}`;
      if (a.matches("[data-slot=widget-remove]")) return `remover:${item.dataset.widgetId}`;
      return `${a.matches("a[href], button, input, select, textarea, [tabindex]") ? "INTERNO" : "ROLAGEM"}:${item.dataset.widgetId}`;
    }));
  }
  return seq;
}
const internos = (seq) => seq.filter((s) => s.startsWith("INTERNO"));
const horizonte = (page) => page.locator(`${HERO}[aria-checked=true]`).textContent();
const numero = (page) => page.locator(NUM).inputValue();

test("Organizar: o Tab percorre os itens e nunca entra num controle de widget", async () => {
  const { ctx, page } = await abrir({ organizar: true });
  const seq = await tabs(page, "Pronto");
  await ctx.close();
  assert.deepEqual(seq.filter((s) => /^(INTERNO|ROLAGEM)/.test(s)), [], seq.join(" "));
  assert.ok(seq.filter((s) => s.startsWith("item:")).length >= 9, seq.join(" ")); // os itens seguem alcançáveis
  assert.ok(seq.filter((s) => s.startsWith("remover:")).length >= 9, seq.join(" ")); // e o ✕ de cada um
});

test("Organizar sem inert (Safari < 15.5): teclas e digitação em controle interno não mudam nada", async () => {
  const { ctx, page } = await abrir({ organizar: true, semInert: true });
  assert.equal(await page.locator("[data-widget-id] [inert]").count(), 0); // a simulação pegou
  // Cada ação medida sozinha: em sequência, seta para um lado e para o outro se anulariam.
  const acoes = [
    ...["ArrowRight", "ArrowLeft"].map((k) => [`radio marcado + ${k}`, `${HERO}[aria-checked=true]`, () => page.keyboard.press(k)]),
    ...["Enter", " "].map((k) => [`radio desmarcado + "${k}"`, `${HERO}[aria-checked=false]`, () => page.keyboard.press(k)]),
    ["campo + digitação", NUM, () => page.keyboard.type("123")],
    ["campo + colar/IME (beforeinput sem keydown)", NUM, () => page.keyboard.insertText("77")],
  ];
  const mudou = [];
  for (const [nome, sel, agir] of acoes) {
    const antes = [await horizonte(page), await numero(page)];
    await focar(page, sel);
    await agir();
    await page.waitForTimeout(100); // o Seg troca e refoca num requestAnimationFrame
    if (String([await horizonte(page), await numero(page)]) !== String(antes)) mudou.push(nome);
  }
  await ctx.close();
  assert.deepEqual(mudou, []);
});

test("Organizar sem inert: o Tab não entra em controle interno, e no Pronto eles voltam à ordem de Tab", async () => {
  const { ctx, page } = await abrir({ organizar: true, semInert: true });
  const organizando = await tabs(page, "Pronto");
  // Trocar o mês pela paleta (o ⌘K passa no Organizar) re-renderiza o conteúdo com nós novos.
  await page.keyboard.press("Control+k");
  await page.locator(".cmdk input").fill("Agosto");
  await page.keyboard.press("Enter");
  await page.locator(".cmdk").waitFor({ state: "hidden" });
  organizando.push(...await tabs(page, "Pronto"));
  await page.getByRole("button", { name: "Pronto" }).click();
  await page.locator('[data-widget-id][tabindex="0"]').first().waitFor({ state: "detached" });
  const depois = await tabs(page, "Organizar");
  await ctx.close();
  assert.deepEqual(internos(organizando), [], organizando.join(" "));
  assert.ok(organizando.filter((s) => s.startsWith("item:")).length >= 9, organizando.join(" "));
  assert.ok(organizando.filter((s) => s.startsWith("remover:")).length >= 9, organizando.join(" ")); // sem inert o ✕ segue na ordem de Tab
  assert.ok(internos(depois).length >= 3, depois.join(" ")); // positivo: os tabindex foram devolvidos
});

test("positivo: fora do Organizar a seta muda o horizonte e o campo aceita digitação e colagem", async () => {
  const { ctx, page } = await abrir();
  const h0 = await horizonte(page), n0 = await numero(page);
  await focar(page, `${HERO}[aria-checked=true]`);
  await page.keyboard.press("ArrowRight");
  await focar(page, NUM);
  await page.keyboard.type("123");
  await page.waitForTimeout(100);
  const [h1, n1] = [await horizonte(page), await numero(page)];
  await page.keyboard.insertText("77");
  const n2 = await numero(page);
  await ctx.close();
  assert.notEqual(h1, h0);
  assert.equal(n0, "");
  assert.equal(n1, "123");
  assert.equal(n2, "12377");
});
