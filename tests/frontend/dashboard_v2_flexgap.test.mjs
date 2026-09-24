/**
 * Protótipo dashboard-v2: o Safari 14.0 (iOS 14.0–14.4, dentro do alvo `safari14` do
 * build) lê `gap` e o ignora em contêiner flex — grid gap ele tem. O fallback mora em
 * webapp/src/dashboard/styles/flexgap.css, dentro de `@supports not (inset: 0)`, e repete
 * à mão parte dos valores de `gap` dos outros arquivos (CLAUDE.md §0.7): este teste é a
 * comparação entre as cópias.
 *
 * Como: em cada página, mede a posição de todo elemento e todo texto no Chromium normal;
 * depois simula o Safari 14.0 — liga as regras do bloco @supports e zera o gap de todo
 * contêiner que continua flex — e mede de novo. Tem de dar a mesma geometria (±1 px).
 * Controle negativo: a mesma simulação SEM o fallback tem de mexer na geometria.
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
const ORIGIN = "http://127.0.0.1:1"; // fictícia: a rota atende da raiz do repositório
const PAGES = ["/", "/previsao", "/gastos", "/simulador", "/metas", "/patrimonio", "/lancamentos"];

let browser;
before(async () => {
  execSync("npm --prefix webapp run build:dashboard", { cwd: ROOT, stdio: "pipe" });
  browser = await chromium.launch();
});
after(() => browser?.close());

// Geometria de todo elemento e todo nó de texto visível do painel, em ordem de documento.
const snapshot = (page) => page.evaluate(() => {
  const out = [];
  const walk = document.createTreeWalker(document.getElementById("pigbank-dashboard"), NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
  for (let n = walk.nextNode(); n; n = walk.nextNode()) {
    let r;
    if (n.nodeType === 3) { if (!n.textContent.trim()) continue; const g = document.createRange(); g.selectNodeContents(n); r = g.getBoundingClientRect(); }
    else r = n.getBoundingClientRect();
    const el = n.nodeType === 3 ? n.parentElement : n;
    const name = el.tagName.toLowerCase() + (typeof el.className === "string" && el.className.trim() ? "." + el.className.trim().split(/\s+/).join(".") : "");
    const cs = n.nodeType === 1 && getComputedStyle(n);
    out.push({
      name: n.nodeType === 3 ? `${name} "${n.textContent.trim().slice(0, 20)}"` : name,
      r: [r.left, r.top, r.width, r.height].map(Math.round),
      // no fallback o contêiner com quebra recua (margem negativa) e a caixa dele cresce
      // para trás; os filhos seguem medidos, e a caixa só não conta se ele não pinta nada
      recua: !!cs && cs.flexWrap === "wrap" && /flex/.test(cs.display),
      pinta: !!cs && (cs.backgroundColor !== "rgba(0, 0, 0, 0)" || cs.boxShadow !== "none" || cs.borderStyle !== "none"),
    });
  }
  return out;
});

// Simula o Safari 14.0: (opcional) liga o bloco @supports e zera o gap de quem segue flex.
const simulate = (page, withFallback) => page.evaluate((withFallback) => {
  // a troca de estilo no meio da página rolada faz a âncora de rolagem do Chromium andar a
  // página, às vezes; o Safari aplica o fallback desde o começo, não troca
  document.documentElement.style.overflowAnchor = "none";
  const sheet = [...document.styleSheets].find((s) => s.href?.includes("dashboard-app.css"));
  const sup = [...sheet.cssRules].filter((r) => r instanceof CSSSupportsRule && /inset/.test(r.conditionText));
  if (withFallback) {
    const st = document.createElement("style");
    st.textContent = sup.flatMap((s) => [...s.cssRules].map((r) => r.cssText)).join("\n");
    document.head.append(st);
  }
  for (const el of document.querySelectorAll("#pigbank-dashboard *")) {
    if (!/flex/.test(getComputedStyle(el).display)) continue;
    el.style.setProperty("row-gap", "0", "important");
    el.style.setProperty("column-gap", "0", "important");
  }
  return sup.map((s) => s.conditionText.replace(/\s/g, ""));
}, withFallback);

async function measure(width, hash, withFallback, setup) {
  const ctx = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: "reduce" });
  await ctx.route("**/*", (r) => {
    const url = new URL(r.request().url());
    if (url.origin !== ORIGIN) return r.abort();
    const path = decodeURIComponent(url.pathname).replace(/\/$/, "/index.html");
    return r.fulfill({ path: join(ROOT, path) }).catch(() => r.fulfill({ status: 404, body: "" }));
  });
  const page = await ctx.newPage();
  await page.goto(`${ORIGIN}/dashboard-v2/#${hash}`);
  await page.locator("#page-title").waitFor({ state: "attached" });
  if (setup) await setup(page);
  await page.waitForTimeout(700);
  const native = await snapshot(page);
  const overflow = await page.evaluate(() => document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth);
  const conditions = await simulate(page, withFallback);
  await page.waitForTimeout(300);
  const sim = await snapshot(page);
  await ctx.close();
  assert.equal(sim.length, native.length, `${width} ${hash}: o DOM mudou durante a medida`);
  const diffs = native.flatMap((a, i) => {
    const b = sim[i];
    if (b.recua && !b.pinta) return [];
    return a.r.some((v, k) => Math.abs(v - b.r[k]) > 1) ? [`${a.name} ${a.r} → ${b.r}`] : [];
  });
  return { diffs, conditions, overflow };
}

// Estados que só aparecem depois de um gesto: paleta, Organizar, filtros, simulação, dica do gráfico.
const paleta = async (page) => { await page.keyboard.press("Control+k"); await page.locator(".cmdk[open]").waitFor(); };
const organizar = (page) => page.getByRole("button", { name: "Organizar" }).click();
const dica = async (page) => { await page.locator(".nw-chart").first().hover(); await page.locator(".tip-row").first().waitFor(); };
const filtros = async (page) => {
  await page.locator(".cat").first().click();
  await page.locator(".cal-day:not([disabled])").first().click();
  await page.evaluate(() => { location.hash = "#/lancamentos"; });
  await page.locator(".ledger-chips").waitFor();
};
const simular = async (page) => { await page.locator(".presets .chip").first().click(); await page.locator(".sim-facts").waitFor(); };
// mensagem que precisa de reticência em qualquer largura
const textoLongo = (page) => page.locator(".row-msg").evaluateAll((qs) => qs.forEach((q) => { q.textContent = "pagamento da viagem de formatura dividido com a galera toda do terceirão"; }));
const CASES = [
  // 320: título que quebra linha e reticência; 1100: bloco estreito que o @container esconde
  ...[1440, 1100, 1024, 390, 320].flatMap((w) => PAGES.map((p) => [w, p])),
  ...[1440, 390, 320].flatMap((w) => [paleta, organizar, filtros].map((f) => [w, "/", f])),
  ...[1440, 390, 320].map((w) => [w, "/simulador", simular]),
  [390, "/lancamentos", textoLongo],
  [1440, "/", dica],
];

test("com o fallback, o Safari 14.0 simulado tem a geometria do Chromium normal", async () => {
  const falhas = [];
  for (const [w, p, setup] of CASES) {
    const { diffs, conditions, overflow } = await measure(w, p, true, setup);
    // no Chromium normal, sem simulação: nada pode passar da largura da tela
    if (overflow > 0) falhas.push(`${w} ${p}${setup ? ` (${setup.name})` : ""}: rolagem horizontal de ${overflow} px`);
    assert.deepEqual(conditions, ["not(inset:0)"], "o minificador não pode reescrever a condição do @supports");
    if (diffs.length) falhas.push(`${w} ${p}${setup ? ` (${setup.name})` : ""}: ${diffs.length} diferenças\n  ${diffs.slice(0, 8).join("\n  ")}`);
  }
  assert.deepEqual(falhas, []);
});

test("controle negativo: sem o fallback a simulação gruda os itens", async () => {
  for (const [w, p] of [[1440, "/"], [390, "/lancamentos"], [390, "/metas"]]) {
    const { diffs } = await measure(w, p, false);
    assert.ok(diffs.length > 10, `${w} ${p}: a simulação sem fallback deu só ${diffs.length} diferenças`);
  }
});
