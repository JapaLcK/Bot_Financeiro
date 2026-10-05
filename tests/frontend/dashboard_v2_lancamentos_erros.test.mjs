import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { abrirPainel, exigeArtefatoEmDia, RESPOSTAS } from "./_painel.mjs";
let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());
async function abrir() {
  const p = await abrirPainel(browser, { espera: '[data-id="l901"]' });
  await p.ir("/lancamentos");
  return p;
}
async function novo(page) {
  await page.getByRole("button", { name: "Lançar na Carteira", exact: true }).click();
  await page.locator('.lanc-form input[name="descricao"]').fill("Rascunho preservado");
  await page.locator('.lanc-form input[name="valor"]').fill("9,99");
}
const salvar = (page) => page.locator(".lanc-form").getByRole("button", { name: "Salvar", exact: true });

for (const [status, body, texto] of [
  [422, { error: { code: "validation_error", message: "Bad", details: [{ loc: ["body", "data"], msg: "Bad", type: "value_error" }] } }, "Confira os campos indicados"],
  [403, { detail: "CSRF" }, "Recarregue a página antes de salvar novamente"],
  [403, { error: { code: "plan_limit", message: "Forbidden" } }, "Você atingiu o limite"],
  [404, { error: { code: "lancamento_nao_encontrado", message: "Not Found" } }, "Este lançamento não está mais disponível"],
]) test(`erro ${status}/${body.error?.code ?? "CSRF fora envelope"}: mantém rascunho e mensagem local`, async () => {
  const { ctx, page } = await abrir();
  let posts = 0;
  await ctx.route("**/api/v2/lancamentos/carteira", (r) => { posts++; return r.fulfill({ status, json: body }); });
  await novo(page); await salvar(page).click();
  await page.getByText(texto, { exact: false }).waitFor();
  assert.equal(await page.locator('.lanc-form input[name="descricao"]').inputValue(), "Rascunho preservado");
  assert.equal(posts, 1);
  if (status === 422) { await page.waitForFunction(() => document.activeElement?.getAttribute("name") === "data"); assert.equal(await page.evaluate(() => document.activeElement?.getAttribute("name")), "data"); }
  if (body.error?.code === "plan_limit") assert.equal(await page.locator('.lanc-form a[href="/precos"]').count(), 1);
  await ctx.close();
});

for (const rede of [true, false]) test(`${rede ? "POST grava/perde resposta" : "POST 503"}: não repete; bloqueia até atualização/conferência explícita`, async () => {
  const { ctx, page } = await abrir();
  let posts = 0, gravou = false, getFalha = false;
  await ctx.route("**/api/v2/lancamentos?*", (r) => getFalha ? r.fulfill({ status: 400, json: {} }) : r.fulfill({ json: { ...RESPOSTAS.lancamentos.estados, proximo: null, itens: gravou ? [...RESPOSTAS.lancamentos.estados.itens, { ...RESPOSTAS.lancamentos.estados.itens[0], id: "l999", descricao: "Rascunho preservado" }] : RESPOSTAS.lancamentos.estados.itens } }));
  await ctx.route("**/api/v2/lancamentos/carteira", (r) => {
    posts++; gravou = true;
    return rede ? r.abort("failed") : r.fulfill({ status: 503, json: {} });
  });
  await novo(page); await salvar(page).click();
  await page.locator(".lanc-conferir").waitFor();
  assert.equal(await salvar(page).isDisabled(), true);
  assert.equal(posts, 1);
  assert.equal(await page.locator('.lanc-form input[name="descricao"]').inputValue(), "Rascunho preservado");
  getFalha = true;
  await page.getByRole("button", { name: "Atualizar para conferir" }).click();
  await page.getByText("Não foi possível atualizar a lista. Confira antes de salvar novamente.").waitFor();
  assert.equal(await salvar(page).isDisabled(), true);
  getFalha = false;
  await page.getByRole("button", { name: "Atualizar para conferir" }).click();
  await page.locator(".lanc-form").waitFor({ state: "detached" });
  assert.equal(await page.getByRole("button", { name: "Conferi os lançamentos", exact: true }).isEnabled(), true);
  await page.getByRole("button", { name: "Lançar na Carteira", exact: true }).click();
  assert.equal(await salvar(page).isDisabled(), true); // GET não libera a guarda persistente
  await page.getByRole("button", { name: "Fechar detalhes" }).click();
  await page.locator('[data-id="l999"]').waitFor();
  assert.equal(posts, 1);
  await ctx.close();
});

test("dois cliques no mesmo tique, cancelar e sair/voltar com POST pendente não repetem; refetch recomeça sem cursor", async () => {
  const { ctx, page } = await abrir();
  let posts = 0, liberar;
  const atraso = new Promise((resolve) => { liberar = resolve; });
  const urls = [];
  page.on("request", (r) => { if (r.url().includes("/api/v2/lancamentos?")) urls.push(new URL(r.url())); });
  await ctx.route("**/api/v2/lancamentos/carteira", async (r) => { posts++; await atraso; return r.fulfill({ json: { id: "l999" } }); });
  await page.getByRole("button", { name: "Carregar mais", exact: true }).click();
  await page.locator('[data-id="l55"]').waitFor();
  await novo(page);
  await salvar(page).evaluate((b) => { b.click(); b.click(); });
  await page.getByText("Salvando…", { exact: true }).waitFor();
  assert.equal(posts, 1);
  await page.getByRole("button", { name: "Fechar detalhes" }).click();
  await page.locator('.rail-list a[href="#/"]').click();
  await page.locator('.rail-list a[href="#/lancamentos"]').click();
  await page.locator('[data-id="l901"]').waitFor();
  assert.equal(await page.getByRole("button", { name: "Lançar na Carteira", exact: true }).isDisabled(), true);
  liberar();
  await page.waitForFunction(() => ![...document.querySelectorAll("button")].find((b) => b.textContent === "Lançar na Carteira")?.disabled);
  await page.waitForTimeout(100);
  assert.equal(posts, 1);
  assert.equal(urls.at(-1).searchParams.has("cursor"), false);
  assert.equal(await page.locator('[data-id="l55"]').count(), 0);
  await ctx.close();
});

test("POST salvo e GET falho anunciam estados distintos, mantendo dados antigos explicitamente", async () => {
  const { ctx, page } = await abrir();
  let falha = false;
  await ctx.route("**/api/v2/lancamentos?*", (r) => falha ? r.fulfill({ status: 400, json: {} }) : r.fulfill({ json: RESPOSTAS.lancamentos.estados }));
  await ctx.route("**/api/v2/lancamentos/editar", (r) => { falha = true; return r.fulfill({ json: { id: "l901" } }); });
  await page.locator('[data-id="l901"] button').click();
  await page.locator('.lanc-form input[name="descricao"]').fill("Salvo no servidor");
  await salvar(page).click();
  await page.locator(".lanc-form[open]").waitFor({ state: "detached" });
  assert.match(await page.locator(".lanc-aviso").textContent(), /Lançamento salvo.*Não foi possível atualizar a lista/);
  assert.equal(await page.locator('[data-id="l901"] b').textContent(), "mercado");
  await page.getByText("Não foi possível atualizar os lançamentos.", { exact: false }).waitFor();
  await ctx.close();
});

test("GET antigo em voo é cancelado antes do POST e não sobrescreve atualização posterior", async () => {
  const { ctx, page } = await abrir();
  let primeiro = true, gravou = false, liberar;
  const atraso = new Promise((resolve) => { liberar = resolve; });
  await ctx.route("**/api/v2/lancamentos?*", async (r) => {
    const antigo = !gravou;
    if (primeiro) { primeiro = false; await atraso; }
    const item = { ...RESPOSTAS.lancamentos.estados.itens[0], descricao: antigo ? "Resposta antiga" : "Atualizado após POST" };
    await r.fulfill({ json: { ...RESPOSTAS.lancamentos.estados, itens: [item], proximo: null } }).catch(() => {});
  });
  await ctx.route("**/api/v2/lancamentos/editar", (r) => { gravou = true; return r.fulfill({ json: { id: "l901" } }); });
  await page.locator('[data-id="l901"] button').click();
  await page.locator('.lanc-form input[name="descricao"]').fill("Alteração");
  await page.evaluate(() => [...document.querySelectorAll("button")].find((b) => b.textContent === "Atualizar lista").click());
  await page.waitForTimeout(50);
  await salvar(page).click();
  await page.locator(".lanc-form[open]").waitFor({ state: "detached" });
  await page.getByRole("button", { name: "Detalhes: Atualizado após POST", exact: true }).waitFor();
  liberar(); await page.waitForTimeout(100);
  assert.equal(await page.locator('[data-id="l901"] b').textContent(), "Atualizado após POST");
  await ctx.close();
});

test("fechar formulário pendente e abrir outros detalhes: resposta anterior não fecha a nova modal", async () => {
  const { ctx, page } = await abrir();
  let liberar;
  const atraso = new Promise((resolve) => { liberar = resolve; });
  await ctx.route("**/api/v2/lancamentos/carteira", async (r) => { await atraso; return r.fulfill({ json: { id: "l999" } }); });
  await novo(page); await salvar(page).click();
  await page.getByText("Salvando…", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Fechar detalhes" }).click();
  await page.locator('[data-id="l900"] button').click();
  liberar();
  await page.waitForFunction(() => ![...document.querySelectorAll("button")].find((b) => b.textContent === "Lançar na Carteira")?.disabled);
  await page.waitForTimeout(50);
  assert.equal(await page.locator(".lanc-form[open]").count(), 1);
  assert.equal(await page.locator('.lanc-form input[name="descricao"]').inputValue(), "PADARIA DO ZE");
  await ctx.close();
});

for (const [status, width] of [[409, 1440], [422, 390], [404, 320]]) test(`recuperação página 2 / ${status}: refetch governa rascunho e próximo envio`, async () => {
  const p = await abrirPainel(browser, { width, espera: '[data-id="l901"]' });
  const { ctx, page, erros } = p;
  try {
    await p.ir("/lancamentos");
    let item = { ...RESPOSTAS.lancamentos.segunda.itens[0], pode: ["descricao", "valor", "data"] };
    const bodies = [], leituras = [];
    await ctx.route("**/api/v2/lancamentos?*", (r) => {
      const cursor = new URL(r.request().url()).searchParams.get("cursor");
      leituras.push({ cursor, depois: bodies.length });
      return r.fulfill({ json: cursor ? { ...RESPOSTAS.lancamentos.segunda, itens: status === 404 && bodies.length ? [] : [item] } : RESPOSTAS.lancamentos.estados });
    });
    await ctx.route("**/api/v2/lancamentos/editar", (r) => {
      bodies.push(r.request().postDataJSON());
      if (bodies.length > 1) return r.fulfill({ json: { id: item.id } });
      if (status === 409) item = { ...item, pode: ["descricao"] };
      const code = status === 409 ? "nao_editavel" : status === 422 ? "validation_error" : "lancamento_nao_encontrado";
      return r.fulfill({ status, json: { error: { code, message: "Bad", ...(status === 422 ? { details: [{ loc: ["body", "data"], msg: "Bad", type: "value_error" }] } : {}) } } });
    });
    const mais = page.getByRole("button", { name: "Carregar mais", exact: true });
    await mais.scrollIntoViewIfNeeded();
    if (width <= 760) {
      const livre = await mais.evaluate((e) => {
        const b = e.getBoundingClientRect(), nav = document.querySelector(".tabbar").getBoundingClientRect();
        return b.bottom <= nav.top && document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2) === e;
      });
      assert.equal(livre, true, "paginação acima da tabbar após rolagem legítima");
    }
    await mais.click();
    await page.locator(`[data-id="${item.id}"] button`).click();
    const descricao = page.locator('.lanc-form input[name="descricao"]');
    const data = page.locator('.lanc-form input[name="data"]');
    await descricao.fill("Correção preservada");
    if (status === 409) await page.locator('.lanc-form input[name="valor"]').fill("9.99");
    if (status === 422) await data.fill("2026-10-03"); // futura para o relógio congelado
    await salvar(page).click();
    const texto = status === 409 ? "A permissão para alterar" : status === 422 ? "Confira os campos indicados" : "Este lançamento não está mais disponível";
    await page.locator('.lanc-form [role="alert"]').waitFor();
    assert.equal(await descricao.inputValue(), "Correção preservada");
    assert.equal(await descricao.isDisabled(), status === 404);
    assert.equal((await page.locator('.lanc-form [role="alert"]').textContent()).includes(texto), true);
    assert.equal(bodies.length, 1);
    assert.equal(leituras.some((l) => l.depois === 1 && l.cursor === RESPOSTAS.lancamentos.estados.proximo), true);
    assert.deepEqual(erros, []);
    await page.screenshot({ path: `/private/tmp/pigbank-pr4-recuperacao-${status}-${width}.png` });
    if (status === 404) {
      assert.equal(await salvar(page).isDisabled(), true);
      assert.equal(await page.locator(`[data-id="${item.id}"]`).count(), 0);
      return;
    }
    if (status === 409) {
      assert.equal(await page.locator('.lanc-form input[name="valor"]').isDisabled(), true);
      assert.equal(await page.locator('.lanc-form input[name="valor"]').inputValue(), "9.99");
    } else {
      assert.equal(await data.isDisabled(), false);
      assert.equal(await data.getAttribute("aria-invalid"), "true");
      await data.fill("2026-10-01");
    }
    await salvar(page).click();
    await page.locator(".lanc-form").waitFor({ state: "detached" });
    assert.deepEqual(bodies[1], { id: item.id, descricao: "Correção preservada", ...(status === 422 ? { data: "2026-10-01" } : {}) });
    assert.equal(bodies.length, 2);
    assert.equal(leituras.at(-1).cursor, null); // sucesso mantém reset aprovado
    assert.equal(await page.locator(`[data-id="${item.id}"]`).count(), 0);
  } finally { await ctx.close(); }
});
