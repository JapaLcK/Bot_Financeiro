/**
 * Protótipo dashboard-v2, perfis do Resumo (parts/Board.tsx, ProfilePicker, BoardControls e
 * o ✕ do components/ui/draggable-widget-grid.tsx):
 *
 *   · 1ª visita: o modal abre; a escolha (ou Pular, ou Esc) monta o preset e fica lembrada;
 *   · o ajuste é por perfil e "Restaurar padrão" volta ao preset do perfil atual;
 *   · o ✕ esconde por clique e por Enter, com e sem `inert` (Safari < 15.5): sem inert ele
 *     segue alcançável pelo Tab; depois o foco fica num bloco, e o catálogo o devolve;
 *   · o plano (?plano=) tira os blocos pagos do painel e os mostra com cadeado no catálogo;
 *   · storage que lança não impede de montar nem de escolher;
 *   · 390: modal, seletor e catálogo sem rolagem horizontal, ✕ com 44 × 44.
 *
 * Com 4 colunas o ladrilhador reordena o preset para fechar buracos: a ORDEM do preset se
 * confere a 390 (uma coluna, sem reordenação); a 1440 confere-se o conjunto.
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
const PERFIL = "pigbank.dashboard.profile.v1";
const PADRAO = ["hero", "resumo", "categorias", "calendario", "simulador", "compromissos", "piggy", "metas", "patrimonio"];
const INVESTIR = ["patrimonio", "wealth", "simulador", "metas", "resumo", "piggy"]; // sem "rendimento" (PR B)
const ECONOMIZAR = ["resumo", "metas", "piggy", "categorias", "simulador", "compromissos"];

let browser;
before(async () => {
  execSync("npm --prefix webapp run build:dashboard", { cwd: ROOT, stdio: "pipe" });
  browser = await chromium.launch();
});
after(() => browser?.close());

async function abrir({ width = 1440, qs = "", perfil = null, semInert = false, semStorage = false } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height: width > 500 ? 1000 : 844 }, reducedMotion: "reduce" });
  await ctx.route("**/*", (r) => {
    const url = new URL(r.request().url());
    if (url.origin !== ORIGIN) return r.abort();
    const path = decodeURIComponent(url.pathname).replace(/\/$/, "/index.html");
    return r.fulfill({ path: join(ROOT, path) }).catch(() => r.fulfill({ status: 404, body: "" }));
  });
  // só na primeira carga: o reload tem de ler o que a página salvou
  if (perfil) await ctx.addInitScript(([k, v]) => { if (!localStorage.getItem(k)) localStorage.setItem(k, v); }, [PERFIL, JSON.stringify(perfil)]);
  if (semStorage) await ctx.addInitScript(() => {
    Object.defineProperty(window, "localStorage", { configurable: true, get() { throw new DOMException("bloqueado", "SecurityError"); } });
  });
  if (semInert) await ctx.addInitScript(() => { // o mesmo de dashboard_v2_organizar
    const sa = Element.prototype.setAttribute, ta = Element.prototype.toggleAttribute;
    Element.prototype.setAttribute = function (n, v) { if (n !== "inert") return sa.call(this, n, v); };
    Element.prototype.toggleAttribute = function (n, f) { return n === "inert" ? false : ta.call(this, n, f); };
    delete HTMLElement.prototype.inert;
  });
  const page = await ctx.newPage();
  const erros = [];
  page.on("pageerror", (e) => erros.push(e.message));
  await page.goto(`${ORIGIN}/dashboard-v2/${qs}#/`);
  await page.locator("#board-profile").waitFor();
  return { ctx, page, erros };
}

// Blocos na ordem de leitura (aria-posinset), a que o leitor de tela e o Tab seguem.
const painel = (page) => page.evaluate(() => [...document.querySelectorAll("[data-widget-id]")]
  .sort((a, b) => a.getAttribute("aria-posinset") - b.getAttribute("aria-posinset"))
  .map((w) => w.dataset.widgetId));
const modalAberto = (page) => page.locator(".picker[open]").count();
const salvo = (page, k) => page.evaluate((k) => localStorage.getItem(k), k);
const organizar = async (page) => {
  await page.getByRole("button", { name: "Organizar" }).click();
  await page.locator("[data-slot=widget-remove]").first().waitFor();
};
const catalogo = async (page) => {
  await page.getByRole("button", { name: "Adicionar bloco" }).click();
  await page.locator("#board-catalog").waitFor();
};
const ordenado = (a) => [...a].sort();

test("1ª visita: modal abre; Investir monta o preset na ordem, lembrado no reload", async () => {
  const { ctx, page, erros } = await abrir({ width: 390 });
  assert.equal(await modalAberto(page), 1);
  await page.getByRole("button", { name: /^Investir/ }).click();
  const escolhido = await painel(page);
  const foco = await page.evaluate(() => document.activeElement?.id);
  await page.reload();
  await page.locator("[data-widget-id]").first().waitFor();
  const r = [await modalAberto(page), await painel(page), await salvo(page, PERFIL)];
  await ctx.close();
  assert.deepEqual(escolhido, INVESTIR);
  assert.equal(foco, "board-profile");
  assert.deepEqual(r, [0, INVESTIR, '"investir"']);
  assert.deepEqual(erros, []);
});

for (const [nome, sair] of [
  ["Pular", (page) => page.getByRole("button", { name: "Pular, ver painel padrão" }).click()],
  ["Esc", (page) => page.keyboard.press("Escape")], // sem gesto antes: o Chrome fecha sem `cancel`
]) {
  test(`1ª visita: ${nome} dá os 9 blocos de sempre e o modal não volta`, async () => {
    const { ctx, page } = await abrir({ width: 390 });
    assert.equal(await modalAberto(page), 1);
    await sair(page);
    await page.locator(".picker[open]").waitFor({ state: "detached" });
    const antes = await painel(page);
    await page.reload();
    await page.locator("[data-widget-id]").first().waitFor();
    const r = [await modalAberto(page), await painel(page), await page.locator("#board-profile").inputValue()];
    await ctx.close();
    assert.deepEqual(antes, PADRAO);
    assert.deepEqual(r, [0, PADRAO, "padrao"]);
  });
}

test("o ajuste é por perfil: esconder em Investir não mexe em Economizar e fica ao voltar", async () => {
  const { ctx, page } = await abrir({ perfil: "investir" });
  await organizar(page);
  await page.getByRole("button", { name: "Esconder Onde está o dinheiro" }).click();
  const investir = await painel(page);
  await page.selectOption("#board-profile", "economizar");
  const economizar = await painel(page);
  await page.selectOption("#board-profile", "investir");
  const volta = await painel(page);
  await ctx.close();
  assert.deepEqual(ordenado(investir), ordenado(INVESTIR.filter((id) => id !== "wealth")));
  assert.deepEqual(ordenado(economizar), ordenado(ECONOMIZAR));
  assert.deepEqual(volta, investir);
});

test("Restaurar padrão volta ao preset do perfil atual, não aos 9", async () => {
  const { ctx, page } = await abrir({ perfil: "investir" });
  await organizar(page);
  await page.getByRole("button", { name: "Esconder Metas e caixinhas" }).click();
  const mexido = await painel(page);
  await page.getByRole("button", { name: "Restaurar padrão" }).click();
  const r = [await painel(page), await salvo(page, "pigbank.dashboard.layout.v1.investir")];
  await ctx.close();
  assert.ok(!mexido.includes("metas"));
  assert.deepEqual(ordenado(r[0]), ordenado(INVESTIR));
  assert.equal(r[1], null);
});

for (const semInert of [false, true]) for (const modo of ["clique", "Enter"]) {
  test(`✕ por ${modo} ${semInert ? "sem" : "com"} inert: esconde, foco num bloco, catálogo devolve, reload mantém`, async () => {
    const { ctx, page } = await abrir({ perfil: "padrao", semInert });
    await organizar(page);
    if (semInert) assert.equal(await page.locator("[data-widget-id] [inert]").count(), 0); // a simulação pegou
    const x = page.locator('[data-widget-id="calendario"] [data-slot=widget-remove]');
    if (modo === "clique") await x.click();
    else {
      // pelo Tab, como o teclado chega nele (sem inert, o grid tira da ordem de Tab o que é interno)
      await page.locator('[data-widget-id="calendario"]').focus();
      await page.keyboard.press("Tab");
      assert.equal(await page.evaluate(() => document.activeElement?.getAttribute("aria-label")), "Esconder Dia a dia");
      await page.keyboard.press("Enter");
    }
    const escondido = await painel(page);
    const foco = await page.evaluate(() => document.activeElement?.matches("[data-widget-id]") && document.activeElement.dataset.widgetId);
    await catalogo(page);
    await page.locator("#board-catalog").getByRole("button", { name: "Dia a dia" }).click();
    await page.waitForTimeout(50); // o foco volta ao catálogo num requestAnimationFrame
    const devolvido = await painel(page);
    const r = [
      await page.evaluate(() => !!document.activeElement?.closest(".catalog")),
      await page.locator(".board .sr-only[aria-live]").textContent(),
    ];
    await page.reload();
    await page.locator("[data-widget-id]").first().waitFor();
    const recarregado = await painel(page);
    await ctx.close();
    assert.deepEqual(ordenado(escondido), ordenado(PADRAO.filter((id) => id !== "calendario")));
    assert.ok(foco && foco !== "calendario", `foco: ${foco}`);
    assert.deepEqual(ordenado(devolvido), ordenado(PADRAO));
    assert.deepEqual(r, [true, "Dia a dia adicionado ao fim do painel"]);
    assert.deepEqual(recarregado, devolvido);
  });
}

test("?plano=plus: Investir sem o simulador; no catálogo ele tem cadeado e não entra", async () => {
  const { ctx, page } = await abrir({ qs: "?plano=plus", perfil: "investir" });
  const antes = await painel(page);
  await organizar(page);
  await catalogo(page);
  const item = page.locator("#board-catalog button", { hasText: "E se…" });
  const r = [await item.getAttribute("aria-disabled"), await item.textContent(), await item.locator("i.ph-lock").count()];
  await item.click({ force: true }); // o Playwright não clica em aria-disabled; o usuário clica
  const depois = await painel(page);
  await ctx.close();
  assert.deepEqual(ordenado(antes), ordenado(INVESTIR.filter((id) => id !== "simulador")));
  assert.deepEqual(r, ["true", "E se…No Pro", 1]);
  assert.deepEqual(depois, antes);
});

test("?plano=essencial: previsão, Piggy e simulador fora do painel em todos os perfis", async () => {
  const { ctx, page } = await abrir({ qs: "?plano=essencial", perfil: "padrao" });
  const vistos = {};
  for (const p of ["padrao", "economizar", "investir", "controlar", "dividas", "autonomo"]) {
    await page.selectOption("#board-profile", p);
    vistos[p] = (await painel(page)).filter((id) => ["hero", "piggy", "simulador"].includes(id));
  }
  await ctx.close();
  for (const [p, pagos] of Object.entries(vistos)) assert.deepEqual(pagos, [], p);
});

test("esvaziar no essencial salva [] e o upgrade não põe o travado de volta: ele fica no catálogo", async () => {
  const { ctx, page } = await abrir({ qs: "?plano=essencial", perfil: "investir" });
  await organizar(page);
  const restaurar = await page.getByRole("button", { name: "Restaurar padrão" }).count(); // sem ajuste: 0
  const x = page.locator("[data-slot=widget-remove]");
  while (await x.count()) await x.first().click();
  const vazio = await salvo(page, "pigbank.dashboard.layout.v1.investir");
  await page.goto(`${ORIGIN}/dashboard-v2/?plano=pro#/`);
  await page.locator("#board-profile").waitFor();
  const depois = await painel(page);
  await page.getByRole("button", { name: "Organizar" }).click();
  await catalogo(page);
  const livres = await page.locator("#board-catalog button:not([aria-disabled])").allTextContents();
  await ctx.close();
  assert.equal(restaurar, 0);
  assert.equal(vazio, "[]");
  assert.deepEqual(depois, []);
  assert.ok(livres.includes("E se…") && livres.includes("Piggy notou"), JSON.stringify(livres));
});

test("positivo: ?plano=pro mostra previsão, Piggy e simulador", async () => {
  const { ctx, page } = await abrir({ qs: "?plano=pro", perfil: "padrao" });
  const r = (await painel(page)).filter((id) => ["hero", "piggy", "simulador"].includes(id));
  await ctx.close();
  assert.deepEqual(ordenado(r), ["hero", "piggy", "simulador"]);
});

test("storage que lança: monta, o modal abre e a escolha vale em memória", async () => {
  const { ctx, page, erros } = await abrir({ semStorage: true });
  assert.equal(await modalAberto(page), 1);
  await page.getByRole("button", { name: /^Investir/ }).click();
  const r = [await modalAberto(page), ordenado(await painel(page))];
  await ctx.close();
  assert.deepEqual(r, [0, ordenado(INVESTIR)]);
  assert.deepEqual(erros, []);
});

test("390: modal, seletor e catálogo sem rolagem horizontal; ✕ com 44 × 44", async () => {
  const { ctx, page } = await abrir({ width: 390 });
  const larg = () => page.evaluate(() => document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth);
  const dentro = (sel) => page.locator(sel).evaluate((el) => { const r = el.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth; });
  const modal = [await larg(), await dentro(".picker")];
  await page.getByRole("button", { name: /^Controlar gastos/ }).click();
  const seletor = [await larg(), await dentro(".board-profile")];
  await organizar(page);
  await catalogo(page);
  const cat = [await larg(), await dentro("#board-catalog")];
  const xs = await page.locator("[data-slot=widget-remove]").evaluateAll((bs) => bs.map((b) => { const r = b.getBoundingClientRect(); return [r.width, r.height]; }));
  await ctx.close();
  assert.deepEqual([modal, seletor, cat], [[0, true], [0, true], [0, true]]);
  assert.ok(xs.length >= 5 && xs.every(([w, h]) => w >= 44 && h >= 44), JSON.stringify(xs));
});
