/**
 * /painel#/simulador/compra com backend (Etapa 3, simulador de compra v2): a tela envia
 * POST /api/v2/simulador e mostra o que a API devolveu (webapp/src/dashboard/widgets/SimuladorCompra.tsx).
 *
 *   · envio só pelo botão (digitar não faz requisição); dinheiro vai e volta como texto;
 *   · Plus: convite ao Pro sem requisição; 403 pro_required: convite, sem curva;
 *   · 422: mensagem no campo e rascunho mantido; falha depois de um sucesso: a curva antiga sai;
 *   · protótipo (DEMO): a rota não existe.
 *
 * Rodar:  npm run test:frontend   (abre o artefato commitado: mudou webapp/src, rode `npm --prefix webapp run build`)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { RESPOSTAS, abrirInfo, abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const BASE = { espera: "#page-title", agora: "2026-10-06T15:00:00Z" };
const OK = RESPOSTAS.simulador.compra;
async function abrir(opts = {}, resposta = { json: OK }) {
  const r = await abrirPainel(browser, { ...BASE, ...opts });
  r.pedidos = [];
  await r.ctx.route("**/api/v2/simulador", (rt) => { r.pedidos.push(rt.request().postDataJSON()); return rt.fulfill(typeof resposta === "function" ? resposta() : resposta); });
  await r.ir("/simulador/compra");
  return r;
}
const preencher = async (page) => {
  await page.locator('[name="nome-0"]').fill("Notebook");
  await page.locator('[name="preco-0"]').fill("1200,50");
};
const SVG = "#w-compra-resultado svg[role=img]";

test("Pro: digitar não envia; o botão envia texto; a tela mostra o texto da API e a curva", async () => {
  const { page, pedidos, erros, ctx } = await abrir();
  await preencher(page);
  await page.locator('[name="parcelas-0"]').fill("12");
  await page.locator('[name="juros_mensal_pct-0"]').fill("1,99");
  assert.equal(pedidos.length, 0, "nenhuma requisição antes do botão");
  await page.getByRole("button", { name: "Calcular" }).click();
  await page.locator(SVG).waitFor();
  assert.deepEqual(pedidos, [{ cenarios: [{ nome: "Notebook", preco: "1200.50", juros_mensal_pct: "1.99", parcelas: 12 }] }]);
  const txt = await page.locator("#w-compra-resultado").innerText();
  assert.match(txt, /Saldo no dia 90\s*R\$ 600,00/);
  assert.match(txt, /Diferença sem a compra\s*−R\$ 100,00/);
  assert.match(txt, /Menor saldo\s*R\$ 600,00 em 08\/10/);
  assert.match(txt, /Sem a compra/);
  assert.match(txt, /Com a compra/);
  assert.deepEqual(erros, []);
  await ctx.close();
});

test("Plus: convite ao Pro, sem formulário e sem requisição", async () => {
  const { page, pedidos, ctx } = await abrir({ plano: "plus" });
  await page.locator(".convite").waitFor();
  assert.equal(await page.locator(".comp-form").count(), 0);
  assert.equal(pedidos.length, 0);
  await ctx.close();
});

test("403 pro_required do servidor: convite, nenhuma curva", async () => {
  const e = RESPOSTAS.erros["403_pro_required"];
  const { page, ctx } = await abrir({}, { status: e.status, json: e.body });
  await preencher(page);
  await page.getByRole("button", { name: "Calcular" }).click();
  await page.getByRole("link", { name: "Ver planos" }).waitFor();
  assert.equal(await page.locator(SVG).count(), 0);
  await ctx.close();
});

test("422: a mensagem aparece no campo, o rascunho fica e não há curva", async () => {
  const corpo = { error: { code: "validation_error", message: "Dados inválidos.", details: [{ loc: ["body", "cenarios", 0, "preco"], msg: "String should match pattern", type: "string_pattern_mismatch" }] } };
  const { page, ctx } = await abrir({}, { status: 422, json: corpo });
  await preencher(page);
  await page.getByRole("button", { name: "Calcular" }).click();
  const campo = page.locator('[name="preco-0"]');
  await page.locator("#comp-0-preco").waitFor();
  assert.equal(await campo.getAttribute("aria-invalid"), "true");
  assert.equal(await campo.inputValue(), "1200,50");
  assert.equal(await page.locator(SVG).count(), 0);
  await ctx.close();
});

const env = (code, details) => ({ error: { code, message: "Payment Required", details } });
const MSG = {
  "422 reserva: texto PT, nunca a regex do pydantic": [{ status: 422, json: env("validation_error", [{ loc: ["body", "reserva_minima"], msg: "String should match pattern '^x$'", type: "string_pattern_mismatch" }]) }, /Use só números, com até 2 casas/],
  "422 loc desconhecido: texto genérico PT": [{ status: 422, json: env("validation_error", [{ loc: ["body", "cenarios", 0, "coisa"], msg: "Extra inputs are not permitted", type: "extra_forbidden" }]) }, /Confira os dados deste cenário/],
  "422 loc fora dos cenários: texto genérico PT": [{ status: 422, json: env("validation_error", [{ loc: ["body"], msg: "JSON decode error", type: "json_invalid" }]) }, /Confira os campos indicados/],
  "422 value_error desconhecido: sem o msg do servidor, texto genérico": [{ status: 422, json: env("validation_error", [{ loc: ["body", "cenarios", 0], msg: "Valor inválido.", type: "valor_invalido" }]) }, /Confira os dados deste cenário/],
  "422 value_error cru (sem código) nunca vai à tela": [{ status: 422, json: env("validation_error", [{ loc: ["body", "cenarios", 0], msg: "Value error, juros sem parcelamento: pergunte ao usuário em quantas parcelas", type: "value_error" }]) }, /Confira os dados deste cenário/],
  "429: espere, não recarregue": [{ status: 429, json: env("rate_limited") }, /Muitas simulações seguidas\. Espere um pouco/],
  "404: genérico, rascunho mantido": [{ status: 404, json: env("not_found") }, /Não deu para calcular agora\. Seu rascunho foi mantido/],
  "401: sessão terminou": [{ status: 401, json: env("unauthenticated") }, /Sua sessão terminou/],
  "402 subscription_required": [{ status: 402, json: env("subscription_required") }, /assinatura não está ativa/],
  "403 onboarding do Open Finance": [{ status: 403, json: env("open_finance_onboarding_required") }, /Termine a conexão com seu banco/],
  "403 CSRF (sem envelope)": [{ status: 403, json: { detail: "CSRF token missing" } }, /Recarregue a página antes de calcular/],
  "200 com HTML": [{ status: 200, contentType: "text/html", body: "<html>proxy</html>" }, /Não deu para calcular agora/],
};
for (const [nome, [resp, esperado]] of Object.entries(MSG)) {
  test(`mensagem: ${nome}`, async () => {
    const { page, ctx } = await abrir({}, resp);
    await preencher(page);
    await page.getByRole("button", { name: "Calcular" }).click();
    const alvo = nome.startsWith("422 reserva") ? "#comp-reserva" : nome.startsWith("422 loc desc") ? ".comp-cen p.warn" : "[role=alert]";
    const txt = await page.locator(alvo).first().innerText();
    assert.match(txt, esperado);
    const tela = await page.locator(".comp-form").innerText();
    assert.doesNotMatch(tela, /should match|not permitted|decode|Payment|Value error|pergunte ao usuário/);
    if (!nome.startsWith("403 CSRF")) assert.doesNotMatch(tela, /Recarregue a página antes de calcular de novo/);
    assert.equal(await page.locator(SVG).count(), 0);
    await ctx.close();
  });
}

// Cada código estável do servidor mostra o texto da tela e marca o campo certo (o `loc` é o que a API devolve).
// `codigos_422` da fixture é conferido contra `CODIGOS_ERRO` em tests/test_api_v2_contrato.py: código novo no servidor
// quebra lá, e sem linha aqui quebra no teste de cobertura abaixo.
const C = (campo) => ["body", "cenarios", 0, campo];
const POR_CODIGO = {
  entrada_maior_que_preco: [C("entrada"), "entrada", /entrada não pode ser maior que o preço/],
  entrada_parcial_sem_parcelas: [C("parcelas"), "parcelas", /Informe em quantas parcelas ou tire a entrada/],
  entrada_igual_ao_preco: [C("entrada"), "entrada", /Entrada igual ao preço: não sobra nada para parcelar/],
  juros_sem_parcelas: [C("parcelas"), "parcelas", /Informe em quantas parcelas para usar juros/],
  data_no_passado: [C("data_compra"), "data_compra", /data da compra não pode ser anterior a hoje/],
  data_fora_do_calendario: [C("data_compra"), "data_compra", /fora do calendário/],
  financiado_pequeno_demais: [C("parcelas"), "parcelas", /financiado é pequeno demais/],
  dinheiro_casas: [C("preco"), "preco", /com até 2 casas/],
  nomes_repetidos: [["body"], null, /mesmo nome/],
  valor_invalido: [["body", "cenarios", 0], null, /Confira os dados deste cenário/],
};
const SEM_VAZAMENTO = /[a-z]+_[a-z_]+|pergunte ao usuário|Value error|should match|not permitted/;

test("os códigos de 422 da fixture são exatamente os cobertos aqui", () => {
  assert.deepEqual(Object.keys(POR_CODIGO).sort(), RESPOSTAS.simulador.codigos_422);
});

for (const [codigo, [loc, campo, texto]] of Object.entries(POR_CODIGO)) {
  test(`422 ${codigo}: texto em português${campo ? ` e ${campo} marcado` : ""}`, async () => {
    const { page, ctx } = await abrir({}, { status: 422, json: env("validation_error", [{ loc, msg: "Valor inválido.", type: codigo }]) });
    await preencher(page);
    await page.getByRole("button", { name: "Calcular" }).click();
    await page.locator("[role=alert], .comp-campo .warn").first().waitFor();
    const tela = await page.locator(".comp-form").innerText();
    assert.match(tela, texto);
    assert.doesNotMatch(tela, SEM_VAZAMENTO);
    if (campo) {
      const input = page.locator(`[name="${campo}-0"]`);
      assert.equal(await input.getAttribute("aria-invalid"), "true");
      const id = await input.getAttribute("aria-describedby");
      assert.match(await page.locator(`#${id}`).innerText(), texto);
    }
    assert.equal(await page.locator(SVG).count(), 0);
    await ctx.close();
  });
}

// Zero vale nestes campos: a mensagem de formato não pode dizer "maior que zero" (só o preço, que recusa zero).
for (const [campo, loc] of [["reserva_minima", ["body", "reserva_minima"]], ["entrada", C("entrada")], ["custos_unicos", C("custos_unicos")], ["despesa_mensal_nova", C("despesa_mensal_nova")], ["preco", C("preco")]]) {
  test(`422 formato de ${campo}: ${campo === "preco" ? "diz maior que zero" : "não diz maior que zero"}`, async () => {
    const { page, ctx } = await abrir({}, { status: 422, json: env("validation_error", [{ loc, msg: "String should match pattern", type: "string_pattern_mismatch" }]) });
    await preencher(page);
    await page.getByRole("button", { name: "Calcular" }).click();
    await page.locator(".comp-campo .warn").first().waitFor();
    const tela = await page.locator(".comp-form").innerText();
    if (campo === "preco") assert.match(tela, /maior que zero/);
    else { assert.doesNotMatch(tela, /maior que zero/); assert.match(tela, /Use só números, com até 2 casas/); }
    await ctx.close();
  });
}

test("data da compra: o calendário não oferece dias passados (min = hoje no fuso do app)", async () => {
  const { page, ctx } = await abrir();
  assert.equal(await page.locator('[name="data_compra-0"]').getAttribute("min"), "2026-10-06");
  await ctx.close();
});

test("a fixture completa (premissas reais da API) não leva identificador de código à tela", async () => {
  const { page, ctx } = await abrir();
  await preencher(page);
  await page.getByRole("button", { name: "Calcular" }).click();
  await page.locator(SVG).waitFor();
  const card = await page.locator("#w-compra-resultado").innerText();
  assert.doesNotMatch(card, /Premissas|Parcelado com juros usa a tabela Price/); // a explicação mora no ⓘ
  const tela = await (await abrirInfo(page, "#w-compra-resultado")).innerText();
  assert.match(tela, /Como esta simulação foi feita/);
  assert.match(tela, /Premissas/);
  assert.match(tela, /Parcelado com juros usa a tabela Price/);
  assert.doesNotMatch(tela, SEM_VAZAMENTO);
  assert.doesNotMatch(tela, /pago_na_compra|total_pago/);
  await ctx.close();
});

// 401/402: o portão decide pelo `/me`, relido exatamente uma vez; com `/me` 200 o rascunho continua na tela.
for (const status of [401, 402]) {
  test(`${status}: relê o /me uma vez e o rascunho continua preenchido`, async () => {
    const { page, ctx } = await abrir({}, { status, json: env(status === 401 ? "unauthenticated" : "subscription_required") });
    let me = 0;
    ctx.on("request", (rq) => { if (new URL(rq.url()).pathname === "/api/v2/me") me++; });
    await preencher(page);
    await page.getByRole("button", { name: "Calcular" }).click();
    await page.getByRole("alert").waitFor();
    await page.waitForTimeout(600);
    assert.equal(me, 1, `uma requisição a /me depois do ${status}`);
    assert.equal(await page.locator('[name="nome-0"]').inputValue(), "Notebook");
    assert.equal(await page.locator('[name="preco-0"]').inputValue(), "1200,50");
    await ctx.close();
  });
}

test("falha depois de um sucesso: a curva antiga sai, nunca uma curva zerada", async () => {
  let n = 0;
  const { page, ctx } = await abrir({}, () => (n++ === 0 ? { json: OK } : { status: 500, json: { error: { code: "internal_error", message: "x" } } }));
  await preencher(page);
  await page.getByRole("button", { name: "Calcular" }).click();
  await page.locator(SVG).waitFor();
  await page.getByRole("button", { name: "Calcular" }).click();
  await page.getByRole("alert").filter({ hasText: "Não deu para calcular" }).waitFor();
  assert.equal(await page.locator(SVG).count(), 0);
  await ctx.close();
});

test("celular: sem rolagem horizontal, três cenários cabem", async () => {
  const { page, ctx } = await abrir({ width: 390 });
  await page.getByRole("button", { name: "Comparar com outro cenário" }).click();
  await page.getByRole("button", { name: "Comparar com outro cenário" }).click();
  assert.equal(await page.getByRole("button", { name: "Comparar com outro cenário" }).count(), 0);
  await preencher(page);
  await page.getByRole("button", { name: "Calcular" }).click();
  await page.locator(SVG).waitFor();
  const { largura, tela } = await page.evaluate(() => ({ largura: document.documentElement.scrollWidth, tela: innerWidth }));
  assert.ok(largura <= tela, `rolagem horizontal: ${largura} > ${tela}`);
  await ctx.close();
});

test("celular: nome de 60 letras sem espaço quebra e não estoura a largura", async () => {
  const longo = "A".repeat(60);
  const json = { ...OK, cenarios: OK.cenarios.map((c, i) => (i === 0 ? { ...c, nome: longo } : c)) };
  const { page, ctx } = await abrir({ width: 390 }, { json });
  await page.locator('[name="nome-0"]').fill(longo);
  await page.locator('[name="preco-0"]').fill("1200,50");
  await page.getByRole("button", { name: "Calcular" }).click();
  await page.locator(SVG).waitFor();
  assert.ok(await page.getByRole("heading", { name: longo }).count());
  const { largura, tela } = await page.evaluate(() => ({ largura: document.documentElement.scrollWidth, tela: innerWidth }));
  assert.ok(largura <= tela, `rolagem horizontal: ${largura} > ${tela}`);
  await ctx.close();
});

test("ferramentas leva à página; o protótipo não tem a rota", async () => {
  const real = await abrir({ espera: null });
  await real.ir("/ferramentas");
  await real.page.getByRole("link", { name: /Vale a pena comprar/ }).click();
  await real.page.locator("#w-compra-form").waitFor();
  await real.ctx.close();
  const demo = await abrirPainel(browser, { ...BASE, demo: true });
  await demo.ctx.addInitScript(() => localStorage.setItem("pigbank.dashboard.profile.v1", '"padrao"'));
  await demo.ir("/simulador/compra");
  assert.equal(await demo.page.evaluate(() => location.hash), "#/");
  await demo.ctx.close();
});
