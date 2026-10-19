import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { abrirPainel, exigeArtefatoEmDia, RESPOSTAS } from "./_painel.mjs";
let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());
const fixture = RESPOSTAS.lancamentos.estados;
async function abrir({ fallback, ...opts } = {}) {
  const p = await abrirPainel(browser, { espera: '[data-id="l901"]', ...opts });
  if (fallback) await p.ctx.addInitScript(() => { delete HTMLDialogElement.prototype.showModal; delete HTMLDialogElement.prototype.close; }); // Safari 14: sem <dialog>
  await p.ir("/lancamentos");
  return p;
}
async function novo(page, valor = "0,01") {
  await page.getByRole("button", { name: "Lançar na Carteira", exact: true }).click();
  await page.locator('.lanc-form input[name="descricao"]').fill("Teste carteira");
  await page.locator('.lanc-form input[name="valor"]').fill(valor);
}
const salvar = (page) => page.locator(".lanc-form").getByRole("button", { name: "Salvar", exact: true });
const row = (page, id) => page.locator(`[data-id="${id}"] button`).click();
const esperarFechar = (page) => page.locator(".lanc-form[open]").waitFor({ state: "detached" });

test("criar entrada e saída: string decimal, automático/null, categoria, data, CSRF e recarga de resumo/contas", async () => {
  const { ctx, page } = await abrir();
  const posts = [], reads = [];
  page.on("request", (r) => { if (r.method() === "GET" && r.url().includes("/api/v2/")) reads.push(new URL(r.url()).pathname); });
  await ctx.route("**/api/v2/lancamentos/carteira", (r) => {
    posts.push({ body: r.request().postDataJSON(), headers: r.request().headers() });
    return r.fulfill({ json: { id: "l999" } });
  });
  await novo(page);
  await salvar(page).click();
  await esperarFechar(page);
  assert.deepEqual(posts[0].body, { tipo: "saida", valor: "0.01", descricao: "Teste carteira", categoria: null });
  assert.equal(posts[0].headers["x-csrf-token"], "tok-123");
  assert.equal(posts[0].headers["content-type"], "application/json");
  for (const path of ["/api/v2/lancamentos", "/api/v2/categorias", "/api/v2/contas"]) assert.ok(reads.includes(path), path);
  await novo(page, "999999999.99");
  await page.locator(".lanc-form").getByRole("combobox", { name: /^Tipo/ }).selectOption("entrada");
  await page.locator(".lanc-form").getByRole("combobox", { name: /^Categoria/ }).selectOption("casa");
  await page.locator(".lanc-form").getByLabel("Data (vazia usa hoje)").fill("2026-10-01");
  await salvar(page).click();
  await esperarFechar(page);
  assert.deepEqual(posts[1].body, { tipo: "entrada", valor: "999999999.99", descricao: "Teste carteira", categoria: "casa", data: "2026-10-01" });
  await ctx.close();
});

test("valor inválido não escreve nem arredonda; rascunho preservado e foco no campo", async () => {
  const { ctx, page } = await abrir();
  let posts = 0;
  await ctx.route("**/api/v2/lancamentos/carteira", (r) => { posts++; return r.fulfill({ json: { id: "l1" } }); });
  await novo(page);
  for (const valor of ["0", "1.234,56", "1e3", "1.999", "1000000000"]) {
    await page.locator('.lanc-form input[name="valor"]').fill(valor);
    await salvar(page).click();
    assert.equal(await page.locator('.lanc-form input[name="valor"]').inputValue(), valor);
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute("name")), "valor");
  }
  assert.equal(posts, 0);
  await ctx.close();
});

test("pode governa cada campo: antigo, OF, fundida, par pendente, espécie e pagamentos; somente alterados no body", async () => {
  const { ctx, page } = await abrir();
  const exemplos = [
    ["antigo", []], ["OF", ["categoria", "descricao"]], ["fundida", ["categoria", "descricao", "apagar"]],
    ["par pendente", ["categoria", "descricao", "apagar"]], ["espécie", ["descricao", "apagar"]],
    ["pagamento", ["categoria", "data"]], ["pagamento fundido", ["categoria"]], ["carteira", ["categoria", "descricao", "data", "valor", "apagar"]],
  ];
  let atual = fixture.itens[0];
  await ctx.route("**/api/v2/lancamentos?*", (r) => r.fulfill({ json: { ...fixture, itens: [atual], proximo: null } }));
  const bodies = [];
  await ctx.route("**/api/v2/lancamentos/editar", (r) => { bodies.push(r.request().postDataJSON()); return r.fulfill({ json: { id: atual.id } }); });
  for (const [nome, pode] of exemplos) {
    atual = { ...fixture.itens[0], descricao: nome, pode, origem: nome === "antigo" ? "registro_antigo" : "carteira" };
    await page.getByRole("button", { name: "Atualizar lista", exact: true }).click();
    await page.getByRole("button", { name: `Detalhes: ${nome}`, exact: true }).click();
    for (const campo of ["categoria", "descricao", "data", "valor"]) assert.equal(await page.locator(`.lanc-form [name="${campo}"]`).isDisabled(), !pode.includes(campo), `${nome}/${campo}`);
    assert.equal(await page.locator(".lanc-form").getByRole("button", { name: "Apagar", exact: true }).count(), pode.includes("apagar") ? 1 : 0);
    if (nome === "OF") {
      await page.locator('.lanc-form input[name="descricao"]').fill("Descrição nova");
      await salvar(page).click(); await esperarFechar(page);
      assert.deepEqual(bodies.at(-1), { id: "l901", descricao: "Descrição nova" });
    } else await page.getByRole("button", { name: "Fechar detalhes", exact: true }).click();
  }
  await ctx.close();
});

test("edição sem mudança não faz POST; valor editável preserva texto original e atualiza somente valor", async () => {
  const { ctx, page } = await abrir();
  const bodies = [];
  await ctx.route("**/api/v2/lancamentos/editar", (r) => { bodies.push(r.request().postDataJSON()); return r.fulfill({ json: { id: "l901" } }); });
  await row(page, "l901");
  assert.equal(await page.locator('.lanc-form input[name="valor"]').inputValue(), "42.90");
  await salvar(page).click();
  await page.getByText("Nenhum campo foi alterado.").waitFor();
  assert.equal(bodies.length, 0);
  await page.locator('.lanc-form input[name="valor"]').fill("50,01");
  await salvar(page).click(); await esperarFechar(page);
  assert.deepEqual(bodies, [{ id: "l901", valor: "50.01" }]);
  await ctx.close();
});

test("409 relê pode, guarda rascunho e próximo POST omite permissão perdida", async () => {
  const { ctx, page } = await abrir();
  let atual = fixture.itens[0];
  const bodies = [];
  await ctx.route("**/api/v2/lancamentos?*", (r) => r.fulfill({ json: { ...fixture, itens: [atual], proximo: null } }));
  await ctx.route("**/api/v2/lancamentos/editar", (r) => {
    bodies.push(r.request().postDataJSON()); atual = { ...atual, pode: ["descricao"] };
    return bodies.length === 1 ? r.fulfill({ status: 409, json: { error: { code: "nao_editavel", message: "Conflict" } } }) : r.fulfill({ json: { id: atual.id } });
  });
  await row(page, "l901");
  await page.locator('.lanc-form input[name="valor"]').fill("60.00");
  await page.locator('.lanc-form input[name="descricao"]').fill("Rascunho");
  await salvar(page).click();
  await page.getByText("A permissão para alterar este lançamento mudou. Confira os campos disponíveis.").waitFor();
  assert.equal(await page.locator('.lanc-form input[name="valor"]').isDisabled(), true);
  assert.equal(await page.locator('.lanc-form input[name="descricao"]').inputValue(), "Rascunho");
  await salvar(page).click(); await esperarFechar(page);
  assert.deepEqual(bodies[1], { id: "l901", descricao: "Rascunho" });
  await ctx.close();
});

test("apagar fundida anuncia banco permanece, refetch traz sombra; aviso/foco sobrevivem à linha removida", async () => {
  const { ctx, page } = await abrir();
  let item = { ...fixture.itens[0], fundido: true };
  await ctx.route("**/api/v2/lancamentos?*", (r) => r.fulfill({ json: { ...fixture, itens: [item], proximo: null } }));
  await page.getByRole("button", { name: "Atualizar lista", exact: true }).click();
  await ctx.route("**/api/v2/lancamentos/apagar", (r) => {
    assert.deepEqual(r.request().postDataJSON(), { id: "l901" });
    item = fixture.itens[1]; return r.fulfill({ json: { id: "l901" } });
  });
  await row(page, "l901");
  await page.locator(".lanc-form").getByRole("button", { name: "Apagar", exact: true }).click();
  assert.match(await page.locator(".lanc-form").textContent(), /A transação do banco continua na sua lista; apagar este lançamento desfaz a junção/);
  await page.getByRole("button", { name: "Confirmar apagar", exact: true }).click();
  await esperarFechar(page);
  await page.locator('[data-id="l900"]').waitFor();
  assert.match(await page.locator(".lanc-aviso").textContent(), /Lançamento apagado/);
  await page.waitForFunction(() => document.activeElement?.classList.contains("lanc-aviso"));
  await ctx.close();
});

for (const fallback of [false, true]) test(`modal ${fallback ? "Safari14 fallback" : "nativo"}: Tab/Shift+Tab/Esc, foco volta e Cmd-K não empilha`, async () => {
  const p = await abrirPainel(browser, { espera: '[data-id="l901"]', width: 390 });
  if (fallback) await p.page.addInitScript(() => { delete HTMLDialogElement.prototype.showModal; delete HTMLDialogElement.prototype.close; });
  await p.ir("/lancamentos");
  await row(p.page, "l901");
  await p.page.keyboard.press("Control+k");
  await p.page.evaluate(() => window.dispatchEvent(new Event("dash:command")));
  assert.equal(await p.page.locator("dialog[open]").count(), 1);
  for (const key of [...Array(15).fill("Tab"), ...Array(15).fill("Shift+Tab")]) {
    await p.page.keyboard.press(key);
    assert.equal(await p.page.evaluate(() => !!document.activeElement?.closest(".lanc-form")), true, key);
  }
  await p.page.keyboard.press("Escape"); await esperarFechar(p.page);
  assert.equal(await p.page.evaluate(() => document.activeElement?.getAttribute("aria-label")), "Detalhes: mercado");
  await p.ctx.close();
});

test("SSE/refetch durante formulário não transforma campo intocado em alteração; somente descrição entra no POST", async () => {
  const { ctx, page } = await abrir();
  await ctx.addInitScript(() => {
    window.EventSource = class {
      static CLOSED = 2;
      constructor() { window.eventosDoTeste = this; }
      close() {}
    };
  });
  await page.reload();
  await page.locator('[data-id="l901"]').waitFor();
  let item = fixture.itens[0];
  await ctx.route("**/api/v2/lancamentos?*", (r) => r.fulfill({ json: { ...fixture, itens: [item], proximo: null } }));
  const bodies = [];
  await ctx.route("**/api/v2/lancamentos/editar", (r) => { bodies.push(r.request().postDataJSON()); return r.fulfill({ json: { id: item.id } }); });
  await row(page, "l901");
  await page.locator('.lanc-form input[name="descricao"]').fill("Só a descrição");
  item = { ...item, valor: "80.01", data: "2026-10-01" };
  await page.evaluate(() => window.eventosDoTeste.onmessage(new MessageEvent("message", { data: '{"recurso":"tudo"}' })));
  await page.waitForFunction(() => document.querySelector('[data-id="l901"] .lanc-valor')?.textContent.includes("80,01"));
  await salvar(page).click(); await esperarFechar(page);
  assert.deepEqual(bodies, [{ id: "l901", descricao: "Só a descrição" }]);
  await ctx.close();
});

// O 2º clique de um duplo clique em "Lançar na Carteira" cai no fundo do diálogo recém-aberto (::backdrop nativo,
// scrim no fallback) e o fecharia na hora: o fundo só vale passada a janela do useDialogo (400 ms).
// O 2º clique só sai com o diálogo já aberto, no ponto do botão (fora dele): sem o conserto fecha, por mais lenta que esteja a máquina.
for (const fallback of [false, true]) test(`${fallback ? "Safari 14 (fallback)" : "nativo"}: duplo clique em Lançar na Carteira deixa o diálogo aberto; o clique no fundo depois da janela fecha`, async () => {
  const { ctx, page } = await abrir({ fallback });
  const box = await page.getByRole("button", { name: "Lançar na Carteira", exact: true }).boundingBox();
  const [x, y] = [box.x + 10, box.y + 10];
  await page.mouse.click(x, y);
  await page.locator(".lanc-form[open]").waitFor();
  const t0 = await page.evaluate(() => performance.now());
  const dentro = await page.locator(".lanc-form").boundingBox();
  assert.ok(x < dentro.x || x > dentro.x + dentro.width || y < dentro.y || y > dentro.y + dentro.height, "o ponto do botão tem de cair no fundo, não no diálogo");
  await page.mouse.click(x, y);
  assert.equal(await page.locator(".lanc-form[open]").count(), 1); // o fechar é síncrono: sem sleep para "esperar que não feche"
  await page.waitForFunction(([t, janela]) => performance.now() - t > janela, [t0, 450]);
  await page.mouse.click(x, y); // o caso legítimo: clique no fundo, passada a janela
  await esperarFechar(page);
  await ctx.close();
});

// Apertar o mouse DENTRO do painel (selecionar o texto de um campo) e soltar no fundo não é clique no fundo: o diálogo (useDialogo, o
// mesmo dos outros 7) segue aberto e o digitado fica. O clique que nasce e acaba no fundo continua fechando.
test("arrastar a seleção de dentro do painel e soltar no fundo não fecha o diálogo nem perde o digitado; o clique simples no fundo fecha", async () => {
  const { ctx, page } = await abrir();
  await novo(page);
  await page.waitForTimeout(500); // a janela do clique no fundo
  const campo = await page.locator('.lanc-form input[name="descricao"]').boundingBox();
  const fora = await page.locator(".lanc-form").boundingBox();
  const [fx, fy] = [Math.max(5, fora.x / 2), fora.y + fora.height / 2]; // à esquerda do painel: o ::backdrop
  await page.mouse.move(campo.x + campo.width - 5, campo.y + campo.height / 2);
  await page.mouse.down();
  await page.mouse.move(fx, fy, { steps: 8 });
  await page.mouse.up();
  assert.equal(await page.locator(".lanc-form[open]").count(), 1); // antes de ler o campo: fechado, o inputValue() esperaria o timeout
  assert.equal(await page.locator('.lanc-form input[name="descricao"]').inputValue(), "Teste carteira");
  await page.mouse.click(fx, fy);
  await esperarFechar(page);
  await ctx.close();
});
