/**
 * /painel#/metas com backend (escrita): criar meta e reserva, editar só o que mudou, tirar o prazo,
 * converter a reserva, apagar, o duplo clique, cada erro da API em texto humano, fechar com o POST
 * em voo e o modal nativo × o fallback do Safari 14. A leitura é de dashboard_v2_metas.test.mjs.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { RESPOSTAS, abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());
const ERROS = RESPOSTAS.erros;
const demora = (ms) => new Promise((r) => setTimeout(r, ms));
const ate = async (cond) => { for (let i = 0; i < 100 && !cond(); i++) await demora(50); assert.ok(cond(), "a recarga de /metas não veio"); };

// `resp(r, body)` responde o POST (padrão: `{id}`); os POSTs e as leituras de /metas ficam em `posts` e `gets`.
async function abrir({ metas = "varios", fallback = false, resp, ...opts } = {}) {
  const p = await abrirPainel(browser, { espera: null, metas, ...opts });
  if (fallback) await p.ctx.addInitScript(() => { delete HTMLDialogElement.prototype.showModal; delete HTMLDialogElement.prototype.close; });
  const posts = [], gets = { n: 0 };
  await p.ctx.route(/\/api\/v2\/metas(\/editar|\/apagar)?$/, (r) => {
    const req = r.request();
    if (req.method() === "GET") { gets.n++; return r.fallback(); }
    const body = req.postDataJSON();
    posts.push({ path: new URL(req.url()).pathname.replace("/api/v2", ""), body, csrf: req.headers()["x-csrf-token"] });
    return resp ? resp(r, body) : r.fulfill({ json: { id: body.id ?? 99 } });
  });
  await p.ir("/metas");
  await p.page.locator("#w-metas-painel .metas-corpo").waitFor();
  return { ...p, posts, gets, form: p.page.locator(".meta-form"), salvar: p.page.locator(".meta-form").getByRole("button", { name: /^(Salvar|Salvando…)$/ }) };
}
const editar = (page, nome) => page.getByRole("button", { name: `Editar ${nome}` }).click();
const fechado = (page) => page.locator(".meta-form[open]").waitFor({ state: "detached" });
const respostaDe = (e) => (r) => r.fulfill({ status: e.status, json: e.body });

test("criar meta: corpo exato (alvo com vírgula vira ponto, data opcional), x-csrf-token e recarga da lista", async () => {
  const { ctx, page, posts, gets, form, salvar } = await abrir();
  const antes = gets.n;
  await page.getByRole("button", { name: "Nova meta" }).click();
  await form.getByLabel("Nome").fill("Carro");
  await form.getByLabel("Quanto quer juntar").fill("1500,5");
  await salvar.click(); await fechado(page);
  await page.getByRole("button", { name: "Nova meta" }).click();
  await form.getByLabel("Nome").fill("Moto 2");
  await form.getByLabel("Quanto quer juntar").fill("999999999.99");
  await form.getByLabel("Data-alvo (opcional)").fill("2027-01-15");
  await salvar.click(); await fechado(page);
  assert.deepEqual(posts.map((p) => [p.path, p.body, p.csrf]), [
    ["/metas", { nome: "Carro", tipo: "meta", fonte: { tipo: "investido" }, alvo: "1500.5" }, "tok-123"],
    ["/metas", { nome: "Moto 2", tipo: "meta", fonte: { tipo: "investido" }, alvo: "999999999.99", data_alvo: "2027-01-15" }, "tok-123"],
  ]);
  await ate(() => gets.n > antes);
  await ctx.close();
});

test("reserva: pelo convite já vem em Reserva (nome e 6 meses); com meses 8 o corpo leva meses e não alvo; com reserva a opção some", async () => {
  const { ctx, page, posts, form, salvar } = await abrir({ metas: "vazio" });
  await page.getByRole("button", { name: "Montar reserva" }).click();
  assert.equal(await form.getByLabel("Nome").inputValue(), "Reserva de emergência");
  assert.equal(await form.getByLabel("Meses das suas contas fixas").inputValue(), "6");
  assert.match(await form.innerText(), /Hoje suas contas fixas somam R\$ 3\.000,00 por mês\./);
  await form.getByLabel("Meses das suas contas fixas").fill("8");
  await salvar.click(); await fechado(page);
  assert.deepEqual(posts.map((p) => p.body), [{ nome: "Reserva de emergência", tipo: "reserva", fonte: { tipo: "investido" }, meses: 8 }]);
  // pelo Nova meta: o seletor de tipo oferece a reserva só enquanto ela não existe
  await page.getByRole("button", { name: "Nova meta" }).click();
  assert.deepEqual(await form.getByLabel("Tipo").locator("option").allTextContents(), ["Meta", "Reserva de emergência"]);
  await ctx.close();
  const com = await abrir();
  await com.page.getByRole("button", { name: "Nova meta" }).click();
  assert.equal(await com.form.getByLabel("Tipo").count(), 0);
  await com.ctx.close();
});

test("fonte: banco do /investido e conta BRL do /contas vão no corpo; conta em dólar nem aparece", async () => {
  const { ctx, page, posts, form, salvar } = await abrir();
  await page.getByRole("button", { name: "Nova meta" }).click();
  await form.getByLabel("Nome").fill("Banco");
  await form.getByLabel("Quanto quer juntar").fill("100");
  await form.getByLabel("Investido em um banco").check();
  await form.getByLabel("Banco", { exact: true }).selectOption("Nubank");
  await salvar.click(); await fechado(page);
  await page.getByRole("button", { name: "Nova meta" }).click();
  await form.getByLabel("Nome").fill("Conta");
  await form.getByLabel("Quanto quer juntar").fill("100");
  await form.getByLabel("Saldo de uma conta").check();
  const contas = await form.getByLabel("Conta", { exact: true }).locator("option").allTextContents();
  assert.ok(!contas.some((c) => /Wise/.test(c)), contas.join("|"));
  await form.getByLabel("Conta", { exact: true }).selectOption({ label: "Nubank · Conta" });
  await salvar.click(); await fechado(page);
  assert.deepEqual(posts.map((p) => p.body.fonte), [{ tipo: "banco", banco: "Nubank" }, { tipo: "conta", conta: 1 }]);
  await ctx.close();
});

// O grupo "só o que mudou": reenviar o objeto do GET dá 422 no servidor (`meses: null` numa meta).
test("editar manda SÓ o que mudou: nome; alvo; meses; tirar prazo; meta vencida e banco que sumiu não reenviam o que não mexeu", async () => {
  const { ctx, page, posts, form, salvar } = await abrir();
  await editar(page, "Notebook novo");
  await form.getByLabel("Nome").fill("Notebook 2");
  await salvar.click(); await fechado(page);
  await editar(page, "Notebook novo");
  await form.getByLabel("Quanto quer juntar").fill("9000");
  await salvar.click(); await fechado(page);
  await editar(page, "Reserva de emergência");
  await form.getByLabel("Meses das suas contas fixas").fill("8");
  await salvar.click(); await fechado(page);
  await editar(page, "Viagem pro Japão");
  await form.getByRole("button", { name: "Tirar prazo" }).click();
  await salvar.click(); await fechado(page);
  await editar(page, "Intercâmbio"); // prazo que passou: só o nome, sem reenviar data_alvo
  await form.getByLabel("Nome").fill("Intercâmbio 2");
  await salvar.click(); await fechado(page);
  await editar(page, "Dinheiro que sumiu do banco"); // banco que não aparece mais: a fonte fica como está
  assert.equal(await form.getByLabel("Banco", { exact: true }).inputValue(), "Banco Sumiu");
  await form.getByLabel("Nome").fill("Sumiu 2");
  await salvar.click(); await fechado(page);
  assert.deepEqual(posts.map((p) => [p.path, p.body]), [
    ["/metas/editar", { id: 3, nome: "Notebook 2" }],
    ["/metas/editar", { id: 3, alvo: "9000" }],
    ["/metas/editar", { id: 1, meses: 8 }],
    ["/metas/editar", { id: 2, data_alvo: null }],
    ["/metas/editar", { id: 8, nome: "Intercâmbio 2" }],
    ["/metas/editar", { id: 10, nome: "Sumiu 2" }],
  ]);
  await ctx.close();
});

test("editar sem mudar nada não faz POST; trocar a fonte manda só a fonte", async () => {
  const { ctx, page, posts, form, salvar } = await abrir();
  await editar(page, "Notebook novo");
  await salvar.click();
  await form.getByText("Nenhum campo foi alterado.").waitFor();
  await form.getByLabel("Nome").fill("Notebook novo  "); // só espaço de sobra: continua igual
  await salvar.click();
  await form.getByText("Nenhum campo foi alterado.").waitFor();
  assert.deepEqual(posts, []);
  await form.getByLabel("Saldo de uma conta").check();
  await form.getByLabel("Conta", { exact: true }).selectOption({ label: "Nubank · Conta" });
  await salvar.click(); await fechado(page);
  assert.deepEqual(posts.map((p) => p.body), [{ id: 3, fonte: { tipo: "conta", conta: 1 } }]);
  await ctx.close();
});

test("converter a reserva sem custo em meta comum: {id, tipo, alvo}, sem meses; alvo inválido não escreve e põe o foco no campo", async () => {
  const { ctx, page, posts, form, salvar } = await abrir({ metas: "sem_custo" });
  await page.getByRole("button", { name: "Definir o valor eu mesmo" }).click();
  assert.match(await form.innerText(), /vira uma meta comum[\s\S]*criar outra reserva depois/);
  for (const ruim of ["", "0", "1.234,56", "1e3", "1000000000"]) {
    await form.getByLabel("Quanto quer juntar").fill(ruim);
    await salvar.click();
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute("name")), "alvo", ruim);
  }
  assert.deepEqual(posts, []);
  await form.getByLabel("Quanto quer juntar").fill("5000");
  await salvar.click(); await fechado(page);
  assert.deepEqual(posts.map((p) => p.body), [{ id: 1, tipo: "meta", alvo: "5000" }]);
  await ctx.close();
});

test("apagar: segundo passo no diálogo, só depois do Confirmar apagar vai o POST; Voltar não apaga", async () => {
  const { ctx, page, posts, form } = await abrir();
  await editar(page, "Notebook novo");
  await form.getByRole("button", { name: "Apagar", exact: true }).click();
  await form.getByRole("heading", { name: "Apagar Notebook novo?" }).waitFor();
  await form.getByRole("button", { name: "Voltar" }).click();
  await form.getByRole("heading", { name: "Editar meta" }).waitFor();
  assert.deepEqual(posts, []);
  await form.getByRole("button", { name: "Apagar", exact: true }).click();
  await form.getByRole("button", { name: "Confirmar apagar" }).click(); await fechado(page);
  assert.deepEqual(posts.map((p) => [p.path, p.body, p.csrf]), [["/metas/apagar", { id: 3 }, "tok-123"]]);
  await ctx.close();
});

// O grupo do duplo clique: a guarda síncrona (useRef) é a única que vale no mesmo tick, antes do re-render.
test("duplo clique e duplo Enter no mesmo instante = 1 POST; depois de terminar, o próximo salvar passa", async () => {
  const { ctx, page, posts, form, salvar } = await abrir({ resp: async (r, b) => { await demora(300); return r.fulfill({ json: { id: b.id ?? 99 } }); } });
  await page.getByRole("button", { name: "Nova meta" }).click();
  await form.getByLabel("Nome").fill("Duplo");
  await form.getByLabel("Quanto quer juntar").fill("10");
  await page.evaluate(() => { const f = document.querySelector(".meta-form form"); f.requestSubmit(); f.requestSubmit(); });
  assert.equal(await salvar.textContent(), "Salvando…");
  await fechado(page);
  assert.equal(posts.length, 1);
  await page.getByRole("button", { name: "Nova meta" }).click();
  await form.getByLabel("Nome").fill("Duplo 2");
  await form.getByLabel("Quanto quer juntar").fill("10");
  await salvar.dblclick(); await fechado(page);
  await demora(400);
  assert.deepEqual(posts.map((p) => p.body.nome), ["Duplo", "Duplo 2"]);
  await ctx.close();
});

test("422: foco no primeiro campo do loc, rascunho mantido, texto humano (sem o JSON do servidor)", async () => {
  const e = ERROS["422_validacao"];
  const { ctx, page, form, salvar } = await abrir({ resp: respostaDe(e) });
  await page.getByRole("button", { name: "Nova meta" }).click();
  await form.getByLabel("Nome").fill("Carro");
  await form.getByLabel("Quanto quer juntar").fill("100");
  await salvar.click();
  await form.getByText("Confira os campos indicados. Seu rascunho foi mantido.").waitFor();
  assert.equal(await page.evaluate(() => document.activeElement?.getAttribute("name")), "alvo");
  assert.equal(await form.getByLabel("Quanto quer juntar").inputValue(), "100");
  assert.equal(await form.getByLabel("Nome").inputValue(), "Carro");
  assert.doesNotMatch(await form.innerText(), /string_pattern|body|String should/);
  await ctx.close();
});

for (const [nome, resp, esperado, extra] of [
  ["409 reserva_ja_existe", respostaDe(ERROS["409_reserva_ja_existe"]), "Você já tem uma reserva de emergência."],
  ["404 meta_nao_encontrada", respostaDe(ERROS["404_meta_nao_encontrada"]), "Essa meta não existe mais."],
  ["403 pro_required", respostaDe(ERROS["403_pro_required"]), "Seu plano atual não inclui investimentos. Dá para usar o saldo de uma conta.", { link: "Ver planos", href: "/precos" }],
  ["403 password_required", respostaDe(ERROS["403_password_required"]), "Crie sua senha para salvar suas metas. Depois de criar, volte para o painel novo.", { link: "Criar senha", href: "/home" }],
  ["403 do CSRF ({detail})", respostaDe(ERROS["403_csrf"]), "Recarregue a página antes de salvar de novo."],
  ["rede caída", (r) => r.abort(), "A resposta se perdeu. A meta pode ter sido salva: confira a lista antes de tentar de novo."],
  ["500", respostaDe(ERROS["500"]), "A resposta se perdeu. A meta pode ter sido salva: confira a lista antes de tentar de novo."],
  ["200 com corpo inválido", (r) => r.fulfill({ status: 200, body: "<html>" }), "A resposta se perdeu. A meta pode ter sido salva: confira a lista antes de tentar de novo."],
]) test(`erro ${nome}: texto da tabela, sem JSON nem inglês, uma recarga e nenhuma repetição sozinha`, async () => {
  const { ctx, page, posts, gets, form, salvar } = await abrir({ resp });
  await page.getByRole("button", { name: "Nova meta" }).click();
  await form.getByLabel("Nome").fill("Carro");
  await form.getByLabel("Quanto quer juntar").fill("100");
  const antes = gets.n;
  await salvar.click();
  await form.getByText(esperado).waitFor();
  await ate(() => gets.n > antes); // a lista recarrega
  await demora(1500); // nada de repetição sozinha
  assert.equal(posts.length, 1);
  assert.equal(gets.n - antes, 1);
  assert.doesNotMatch(await form.innerText(), /detail|error|Forbidden|Conflict|Not Found|Payment|[{}]/);
  if (extra) assert.equal(await form.getByRole("link", { name: extra.link }).getAttribute("href"), extra.href);
  assert.equal(await form.locator('input[name="nome"]').inputValue(), "Carro"); // rascunho mantido
  await ctx.close();
});

test("fechar o diálogo com o POST em voo: 1 POST, a lista recarrega e a tela segue sem erro", async () => {
  const { ctx, page, posts, gets, erros, form, salvar } = await abrir({ resp: async (r) => { await demora(500); return r.fulfill({ json: { id: 99 } }); } });
  await page.getByRole("button", { name: "Nova meta" }).click();
  await form.getByLabel("Nome").fill("Carro");
  await form.getByLabel("Quanto quer juntar").fill("100");
  const antes = gets.n;
  await salvar.click();
  await form.getByRole("button", { name: "Cancelar" }).click(); await fechado(page);
  await ate(() => gets.n > antes);
  assert.equal(posts.length, 1);
  assert.equal(await page.getByRole("button", { name: "Nova meta" }).isEnabled(), true);
  assert.deepEqual(erros, []);
  await ctx.close();
});

for (const fallback of [false, true]) test(`modal ${fallback ? "Safari 14 (fallback)" : "nativo"}: Esc e clique no fundo fecham, o foco volta ao botão e o rascunho some`, async () => {
  const { ctx, page, form } = await abrir({ fallback, width: 390 });
  const nova = page.getByRole("button", { name: "Nova meta" });
  await nova.click();
  assert.equal(await page.evaluate(() => !!document.activeElement?.closest(".meta-form")), true);
  assert.equal(await form.getAttribute("role"), fallback ? "dialog" : null);
  await form.getByLabel("Nome").fill("Rascunho");
  await page.keyboard.press("Escape"); await fechado(page);
  assert.equal(await page.evaluate(() => document.activeElement?.textContent), "Nova meta");
  await nova.click();
  assert.equal(await form.getByLabel("Nome").inputValue(), "");
  await demora(500); // o clique no fundo logo depois de abrir é ignorado (2º clique do duplo clique): grupo em dashboard_v2_metas_estados.test.mjs
  await page.mouse.click(4, 500); await fechado(page); // o fundo (a barra do topo fica acima do scrim): ::backdrop no nativo, o scrim no fallback
  assert.equal(await page.evaluate(() => document.activeElement?.textContent), "Nova meta");
  await ctx.close();
});

test("data-alvo: o min é hoje no fuso do app, não o dia do aparelho nem do UTC (22h30 em SP já é o dia seguinte no UTC)", async () => {
  const { ctx, page, posts, form, salvar } = await abrir({ agora: "2026-10-03T01:30:00Z", tz: "UTC" });
  await page.getByRole("button", { name: "Nova meta" }).click();
  assert.equal(await form.getByLabel("Data-alvo (opcional)").getAttribute("min"), "2026-10-02");
  await form.getByLabel("Nome").fill("Ontem");
  await form.getByLabel("Quanto quer juntar").fill("10");
  await form.getByLabel("Data-alvo (opcional)").fill("2026-10-01");
  await salvar.click();
  await form.getByText("Escolha uma data de hoje em diante.").waitFor();
  assert.deepEqual(posts, []);
  await form.getByLabel("Data-alvo (opcional)").fill("2026-10-02"); // hoje vale
  await salvar.click(); await fechado(page);
  assert.equal(posts[0].body.data_alvo, "2026-10-02");
  await ctx.close();
});
