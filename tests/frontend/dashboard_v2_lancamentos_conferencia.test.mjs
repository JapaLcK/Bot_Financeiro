import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { chromium } from "playwright";
import { abrirPainel, exigeArtefatoEmDia, RESPOSTAS } from "./_painel.mjs";
let browser, screenshots;
before(async () => {
  exigeArtefatoEmDia();
  screenshots = await mkdtemp(join(tmpdir(), "pigbank-pr4-conferencia-"));
  browser = await chromium.launch();
});
after(async () => {
  await browser?.close();
  if (screenshots) await rm(screenshots, { recursive: true, force: true });
});
const base = RESPOSTAS.lancamentos.estados;
const save = (page) => page.locator(".lanc-form").getByRole("button", { name: "Salvar", exact: true });
async function novo(page, data = "") {
  await page.getByRole("button", { name: "Lançar na Carteira", exact: true }).click();
  await page.locator('[name="descricao"]').fill("Depois da perda");
  await page.locator('[name="valor"]').fill("9.99");
  if (data) await page.locator('[name="data"]').fill(data);
}
async function preparado(opts = {}) {
  const p = await abrirPainel(browser, { espera: ".lancamentos", ...opts });
  const state = { posts: [], gets: [], gravou: false, falha: false, proximaFalha: false, servidor: "2026-10", segunda: false, data: "2026-10-02", liberar: null };
  await p.ctx.route("**/api/v2/lancamentos**", async (r) => {
    const u = new URL(r.request().url());
    if (r.request().method() === "POST") {
      state.posts.push(r.request().postDataJSON());
      if (state.liberar) await state.liberar;
      if (state.posts.length > 1) return r.fulfill({ json: { id: "l999" } });
      state.gravou = opts.status !== 503;
      return opts.status ? r.fulfill({ status: opts.status, body: opts.status === 200 ? "inválido" : "{}" }) : r.abort("failed");
    }
    state.gets.push(Object.fromEntries(u.searchParams));
    const cursor = u.searchParams.get("cursor");
    if (state.falha || (cursor && state.proximaFalha)) return r.fulfill({ status: 400, json: {} });
    const mes = u.searchParams.get("mes") ?? state.servidor;
    const criada = { ...base.itens[0], id: "l999", descricao: "Depois da perda", data: state.data };
    let itens = state.gravou && (!state.segunda || cursor) ? [criada] : [base.itens[0]];
    if (["origem", "conta", "categoria", "tipo", "q"].some((k) => u.searchParams.has(k))) itens = [];
    if (!opts.todasDatas) itens = itens.filter((i) => i.data.startsWith(mes));
    return r.fulfill({ json: { ...base, mes, itens, proximo: state.segunda && !cursor ? "opaco" : null } });
  });
  if (opts.fallback) await p.ctx.addInitScript(() => { delete HTMLDialogElement.prototype.showModal; delete HTMLDialogElement.prototype.close; });
  if (opts.sse) await p.ctx.addInitScript(() => { const Real = window.EventSource; window.EventSource = class extends Real { constructor(...args) { super(...args); window.pr4Evento = this; } }; });
  await p.ir("/lancamentos");
  return { ...p, state };
}
async function recuperar(page, toolbar = false) {
  if (toolbar) { await page.getByRole("button", { name: "Fechar detalhes" }).click(); await page.getByRole("button", { name: "Atualizar lista", exact: true }).click(); }
  else await page.getByRole("button", { name: "Atualizar para conferir", exact: true }).click();
  await page.locator(".lanc-form").waitFor({ state: "detached" });
  await page.waitForFunction(() => document.querySelector('.lanc-aviso')?.textContent.includes("Lista atualizada"));
  await page.waitForTimeout(80);
}
for (const filtro of ["origem", "conta", "categoria", "tipo", "q", "mes"]) test(`conferência incerta / ${filtro}: GET sem recortes, guarda até confirmação e novo POST voluntário`, async () => {
  const { ctx, page, state } = await preparado();
  try {
    if (filtro === "q") { await page.getByLabel("Buscar", { exact: true }).fill("oculto"); await page.getByText("Busca no histórico disponível:", { exact: false }).waitFor(); }
    else if (filtro === "mes") await page.getByRole("button", { name: "Mês anterior", exact: true }).click();
    else await page.getByRole("combobox", { name: new RegExp(`^${{ origem: "Origem", conta: "Conta", categoria: "Categoria", tipo: "Tipo" }[filtro]}`) }).selectOption({ origem: "banco", conta: "7", categoria: "alimentacao", tipo: "entrada" }[filtro]);
    await novo(page); await save(page).click(); await page.locator(".lanc-conferir").waitFor();
    await recuperar(page, filtro === "mes");
    assert.ok(state.gets.some((g) => !Object.keys(g).length), "data omitida consulta mês autoritativo do servidor");
    assert.deepEqual(state.gets.at(-1), { mes: "2026-10" });
    assert.equal(await page.getByLabel("Buscar", { exact: true }).inputValue(), "");
    assert.equal(await page.locator('[data-id="l999"]').count(), 1);
    await novo(page); assert.equal(await save(page).isDisabled(), true, "GET fresco não é conferência manual");
    await page.getByRole("button", { name: "Fechar detalhes" }).click();
    assert.equal(state.posts.length, 1);
    await page.getByRole("button", { name: "Conferi os lançamentos", exact: true }).click();
    await page.getByRole("button", { name: "Voltar ao rascunho" }).click();
    assert.equal(await page.locator('[name="descricao"]').inputValue(), "Depois da perda");
    await save(page).click(); await page.locator(".lanc-form").waitFor({ state: "detached" });
    assert.equal(state.posts.length, 2); assert.equal("data" in state.posts[1], false);
    assert.equal(await page.getByRole("button", { name: "Voltar ao rascunho" }).count(), 0, "sucesso limpa draft retomado");
  } finally { await ctx.close(); }
});
for (const [status, servidor, data] of [[503, "2026-09", ""], [200, "2026-11", ""], [undefined, "2026-10", "2025-09-20"]]) test(`conferência período autoritativo / ${status ?? "rede"}/${servidor}/${data || "sem data"}: mês fora seletor e descarte sem POST`, async () => {
  const { ctx, page, state, erros } = await preparado({ status, tz: "Pacific/Kiritimati", width: 390 });
  try {
    state.servidor = servidor; state.data = data || `${servidor}-02`;
    await novo(page, data); await save(page).click(); await page.locator(".lanc-conferir").waitFor(); await recuperar(page);
    const alvo = data ? data.slice(0, 7) : servidor;
    assert.deepEqual(state.gets.at(-1), { mes: alvo });
    assert.match(await page.locator('.month-title').textContent(), new RegExp(alvo.slice(0, 4)));
    if (["2025-09", "2026-11"].includes(alvo)) {
      assert.equal(await page.getByRole("button", { name: "Mês anterior", exact: true }).isDisabled(), true);
      assert.equal(await page.getByRole("button", { name: "Próximo mês", exact: true }).isDisabled(), true);
      assert.equal(await page.getByRole("button", { name: "Meses recentes" }).count(), 1);
    }
    await page.getByRole("button", { name: "Conferi os lançamentos", exact: true }).click();
    await page.getByRole("button", { name: "Descartar rascunho" }).click();
    assert.equal(await page.getByRole("button", { name: "Voltar ao rascunho" }).count(), 0);
    assert.equal(state.posts.length, 1); assert.deepEqual(erros, []);
  } finally { await ctx.close(); }
});
test("conferência página 2, GET falho/cache antigo e filtros: só confirmação visível libera", async () => {
  const { ctx, page, state } = await preparado({ width: 320 });
  try {
    state.segunda = true;
    await novo(page); await save(page).click(); await page.locator(".lanc-conferir").waitFor();
    state.falha = true;
    await page.getByRole("button", { name: "Atualizar para conferir" }).click();
    await page.getByText("Não foi possível atualizar a lista. Confira antes de salvar novamente.").waitFor();
    assert.equal(await save(page).isDisabled(), true);
    state.falha = false; await recuperar(page);
    assert.equal(await page.locator('[data-id="l999"]').count(), 0);
    state.proximaFalha = true;
    await page.getByRole("button", { name: "Carregar mais", exact: true }).click();
    await page.getByText("Não foi possível carregar mais lançamentos.").waitFor();
    assert.equal(await page.getByRole("button", { name: "Conferi os lançamentos", exact: true }).isDisabled(), true);
    state.proximaFalha = false;
    await page.locator('.lanc-mais').getByRole("button", { name: "Tentar novamente" }).click();
    await page.locator('[data-id="l999"]').waitFor();
    await page.getByRole("combobox", { name: /^Origem/ }).selectOption("banco");
    assert.equal(await page.getByRole("button", { name: "Conferi os lançamentos", exact: true }).isDisabled(), true);
    await page.getByRole("button", { name: "Retomar conferência" }).click();
    await page.waitForTimeout(100);
    await page.getByRole("button", { name: "Carregar mais", exact: true }).click();
    await page.locator('[data-id="l999"]').waitFor();
    await page.getByRole("button", { name: "Conferi os lançamentos", exact: true }).click();
    assert.equal(state.posts.length, 1);
  } finally { await ctx.close(); }
});
test("conferência persiste se remount ocorre antes do erro e após GET/SSE", async () => {
  const { ctx, page, state } = await preparado({ sse: true });
  try {
    let liberar;
    state.liberar = new Promise((resolve) => { liberar = resolve; });
    await novo(page); await save(page).click(); await page.getByText("Salvando…", { exact: true }).waitFor();
    await page.getByRole("button", { name: "Fechar detalhes" }).click();
    await page.locator('.rail-list a[href="#/"]').click(); await page.locator('.rail-list a[href="#/lancamentos"]').click();
    liberar(); await page.locator('.lanc-conferencia').waitFor();
    await novo(page); assert.equal(await save(page).isDisabled(), true);
    await recuperar(page);
    const antes = state.gets.length;
    await page.evaluate(() => window.pr4Evento.dispatchEvent(new MessageEvent("message", { data: '{"recurso":"open_finance"}' })));
    await page.waitForTimeout(100); assert.ok(state.gets.length > antes, "aviso SSE atualiza GET sem liberar guarda");
    await page.locator('.rail-list a[href="#/"]').click(); await page.locator('.rail-list a[href="#/lancamentos"]').click();
    await page.locator('.lanc-conferencia').waitFor();
    await novo(page); assert.equal(await save(page).isDisabled(), true); assert.equal(state.posts.length, 1);
  } finally { await ctx.close(); }
});
for (const cenario of ["historica", "pagina2", "fatura"]) test(`conferência edição ${cenario}: contexto enviado preservado`, async () => {
  const cartao = cenario === "fatura", pagina2 = cenario === "pagina2";
  const p = await abrirPainel(browser, { espera: ".lancamentos" });
  try {
    const item = { ...base.itens[0], id: cartao ? "c999" : "l999", data: pagina2 ? "2026-10-01" : "2025-09-20", fatura: cartao ? "2026-10" : null, origem: cartao ? "cartao" : "carteira", pode: cartao ? ["descricao", "categoria"] : ["descricao", "data"] };
    const gets = [], posts = [];
    await p.ctx.route("**/api/v2/lancamentos**", (r) => {
      if (r.request().method() === "POST") { posts.push(r.request().postDataJSON()); return r.abort("failed"); }
      const u = new URL(r.request().url()); gets.push(Object.fromEntries(u.searchParams));
      return r.fulfill({ json: { ...base, mes: u.searchParams.get("mes") ?? "2026-10", itens: pagina2 && !u.searchParams.has("cursor") ? [base.itens[0]] : [item], proximo: pagina2 && !u.searchParams.has("cursor") ? "opaco" : null } });
    });
    await p.ir("/lancamentos");
    if (!cartao && !pagina2) { await p.page.getByLabel("Buscar", { exact: true }).fill("mercado"); await p.page.getByText("Busca no histórico disponível:", { exact: false }).waitFor(); }
    if (pagina2) { await p.page.getByRole("button", { name: "Carregar mais", exact: true }).click(); await p.page.locator('[data-id="l999"]').waitFor(); }
    await p.page.locator(`[data-id="${item.id}"]`).getByRole("button").click();
    if (cartao) await p.page.locator('[name="descricao"]').fill("Compra revista");
    else await p.page.locator('[name="data"]').fill("2025-10-01");
    await save(p.page).click(); await p.page.locator('.lanc-conferir').waitFor(); await recuperar(p.page);
    assert.deepEqual(gets.at(-1), { mes: cartao || pagina2 ? "2026-10" : "2025-09" });
    if (!cartao) {
      assert.equal(await p.page.getByRole("button", { name: "Conferi os lançamentos", exact: true }).isDisabled(), true);
      await p.page.getByRole("button", { name: "Conferir 2025-10", exact: true }).click();
      await p.page.waitForTimeout(100); assert.deepEqual(gets.at(-1), { mes: "2025-10" });
    }
    assert.equal(await p.page.getByRole("button", { name: "Conferi os lançamentos", exact: true }).isEnabled(), true);
    assert.deepEqual(posts, [{ id: item.id, ...(cartao ? { descricao: "Compra revista" } : { data: "2025-10-01" }) }]);
  } finally { await p.ctx.close(); }
});
for (const width of [1440, 390, 320]) test(`anos na busca histórica efetiva / ${width}: grupos, aria e linhas; curta e mensal preservados`, async () => {
  const p = await abrirPainel(browser, { width, espera: ".lancamentos" });
  try {
    const itens = [2025, 2026].map((ano) => ({ ...base.itens[0], id: `l${ano}`, data: `${ano}-09-20`, descricao: "Mesmo mercado" }));
    await p.ctx.route("**/api/v2/lancamentos?*", (r) => r.fulfill({ json: { ...base, itens, proximo: null } }));
    await p.ir("/lancamentos");
    await p.page.getByLabel("Buscar", { exact: true }).fill("mercado"); await p.page.getByText("Busca no histórico disponível:", { exact: false }).waitFor();
    for (const ano of [2025, 2026]) {
      assert.ok((await p.page.locator('.lanc-dia h3').allTextContents()).some((s) => s.includes(String(ano))), "cabeçalho informa ano");
      assert.equal(await p.page.locator(`.lanc-dia[aria-label*="${ano}"]`).count(), 1);
      assert.match(await p.page.locator(`[data-id="l${ano}"] .lanc-abrir span`).textContent(), new RegExp(String(ano)), "linha informa ano");
    }
    const overflow = await p.page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
    assert.ok(overflow <= 0);
    await p.page.getByLabel("Buscar", { exact: true }).fill("a"); await p.page.getByText("Use pelo menos uma palavra", { exact: false }).waitFor();
    assert.ok((await p.page.locator('.lanc-dia h3').allTextContents()).every((s) => !/202[56]/.test(s)));
    await p.page.getByLabel("Buscar", { exact: true }).fill(""); await p.page.waitForTimeout(300);
    assert.ok((await p.page.locator('.lanc-abrir span').allTextContents()).every((s) => !/202[56]/.test(s)));
  } finally { await p.ctx.close(); }
});

for (const width of [1440, 390, 320]) test(`conferência visível / ${width}: foco, teclado e ${width === 390 ? "fallback Safari14" : "dialog nativo"}`, async () => {
  const { ctx, page, state, erros } = await preparado({ width, fallback: width === 390 });
  try {
    state.data = "2025-09-20";
    await novo(page, state.data);
    await page.keyboard.press("Shift+Tab"); await page.keyboard.press("Tab");
    assert.equal(await page.locator('.lanc-form').evaluate((d) => d.contains(document.activeElement)), true);
    await save(page).click(); await page.locator('.lanc-conferir').waitFor();
    await page.screenshot({ path: join(screenshots, `pr4-codex-modal-${width}.png`) });
    await recuperar(page);
    assert.equal(await page.evaluate(() => document.activeElement?.classList.contains("lanc-aviso")), true);
    assert.equal(await page.locator(".month-year").isVisible(), true, "recuperação histórica mantém ano visível no celular");
    const geometry = await page.evaluate(() => ({ overflow: document.documentElement.scrollWidth - innerWidth,
      controles: [...document.querySelectorAll('.lanc-conferencia .btn')].map((b) => b.getBoundingClientRect().height) }));
    assert.ok(geometry.overflow <= 0, JSON.stringify(geometry)); assert.ok(geometry.controles.every((h) => h >= 44));
    await page.screenshot({ path: join(screenshots, `pr4-codex-conferencia-${width}.png`), fullPage: true });
    await page.getByRole("button", { name: "Conferi os lançamentos", exact: true }).click();
    await page.getByRole("button", { name: "Voltar ao rascunho" }).click(); await page.keyboard.press("Escape");
    assert.equal(await page.locator('.lanc-form').count(), 0);
    assert.equal(await page.evaluate(() => document.activeElement?.textContent), "Voltar ao rascunho");
    assert.equal(state.posts.length, 1); assert.deepEqual(erros, []);
  } finally { await ctx.close(); }
});

test("conferência apagar incerto: ID completo e contexto permanecem sem inferir sucesso por lista vazia", async () => {
  const p = await abrirPainel(browser, { espera: ".lancamentos" });
  try {
    const item = { ...base.itens[0], id: "l999", fundido: true, pode: ["apagar"] };
    const posts = []; let enviado = false;
    await p.ctx.route("**/api/v2/lancamentos**", (r) => {
      if (r.request().method() === "POST") { posts.push(r.request().postDataJSON()); enviado = true; return r.abort("failed"); }
      return r.fulfill({ json: { ...base, itens: enviado ? [] : [item], proximo: null } });
    });
    await p.ir("/lancamentos"); await p.page.locator('[data-id="l999"] button').click();
    await p.page.locator('.lanc-form').getByRole("button", { name: "Apagar", exact: true }).click();
    await p.page.getByRole("button", { name: "Confirmar apagar", exact: true }).click();
    await p.page.locator('.lanc-conferir').waitFor(); await recuperar(p.page);
    assert.match(await p.page.locator('.lanc-conferencia').textContent(), /l999/);
    assert.equal(await p.page.locator('.lanc-linha').count(), 0);
    await novo(p.page); assert.equal(await save(p.page).isDisabled(), true);
    await p.page.getByRole("button", { name: "Fechar detalhes" }).click();
    await p.page.getByRole("button", { name: "Conferi os lançamentos", exact: true }).click();
    assert.deepEqual(posts, [{ id: "l999" }]);
  } finally { await p.ctx.close(); }
});
test("DEMO mantém busca mensal e datas sem ano; nenhum lançamento real é consultado", async () => {
  const p = await abrirPainel(browser, { demo: true, espera: ".ledger" });
  try {
    await p.ctx.addInitScript(() => localStorage.setItem("pigbank.dashboard.profile.v1", JSON.stringify("padrao")));
    const gets = [];
    p.page.on("request", (r) => { if (r.url().includes("/api/v2/lancamentos")) gets.push(r.url()); });
    await p.ir("/lancamentos");
    const antes = await p.page.locator('.day-head').allTextContents();
    assert.ok(antes.length > 0); assert.ok(antes.every((s) => !/202[56]/.test(s)));
    await p.page.getByRole("searchbox", { name: "Buscar lançamentos" }).fill("Uber");
    await p.page.waitForTimeout(100);
    assert.ok(await p.page.locator('.day-head').count() > 0, 'busca demo conserva resultado legítimo');
    assert.ok((await p.page.locator('.day-head').allTextContents()).every((s) => !/202[56]/.test(s)));
    assert.equal(await p.page.locator('.lanc-dia, .lanc-form').count(), 0); assert.deepEqual(gets, []);
  } finally { await p.ctx.close(); }
});
