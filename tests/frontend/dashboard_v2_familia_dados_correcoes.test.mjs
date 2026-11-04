/**
 * /painel#/conta/familia, correções do PR 6b: foco ao abrir o painel do membro, perfil que falha, leitura atrasada ao trocar de mês,
 * uid antes do GET e isolamento da direção do texto. Em cada teste, o comentário de cima diz qual mutação o deixa vermelho.
 * NÃO medido aqui (defesas sem teste que as isole, ver VerMembro.tsx): `q.isError ? undefined : q.data`, o `removeQueries` de `parou` e o `key={mes}` do Resumo.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { ERRO, F, MARIA, abrirFamilia, abrirVer, titular } from "./_familia.mjs";

const browser = navegador();
void browser;

const TUDO = { ...MARIA, permissoes: ["agregados", "lancamentos"] };
const PERFIL = { user_id: 1, display_name: null, email: null, whatsapp_linked: true };
const com = async (abrir, fn) => {
  const t = await abrir();
  try { await fn(t); assert.deepEqual(t.erros, [], "pageerror"); } finally { await t.ctx.close(); }
};
const perfil = (resp) => (ctx) => ctx.route((u) => u.pathname === "/auth/dashboard-profile", (r) => resp(r));
const mesAnterior = (page) => page.getByRole("button", { name: "Mês anterior" });
const foco = (page) => page.evaluate(() => { const a = document.activeElement; return `${a.tagName}.${a.className}#${a.id}`; });
const ATRASO = 450; // o debounce é de 300 ms

// mutação: tirar o `titulo.current?.focus()` do VerMembro.tsx (o foco cai no <body>: o "Ver" fica na lista hidden)
for (const [w, h] of [[1280, 800], [768, 1024], [390, 844]]) {
  test(`foco: depois de 'Ver' vai ao título do painel e, depois de 'Voltar', ao h1 (${w}x${h})`, async () => {
    await com(() => abrirVer({ width: w, height: h }), async ({ page, bt }) => {
      assert.equal(await foco(page), "H2.fam-ver-nome#");
      await bt("Voltar").click();
      assert.equal(await foco(page), "H1.#page-title");
    });
  });
}

// mutação: tirar o ramo `perfilFalhou` (Familia.tsx / VerMembro.tsx / Historico.tsx); positivo: com o perfil de volta, "Tentar de novo" carrega
test("perfil 500: 'Quem viu' e o painel do titular mostram o erro, sem GET de membro; 'Tentar de novo' refaz o perfil e carrega", async () => {
  let ok = false;
  const antes = perfil((r) => (ok ? r.fulfill({ json: PERFIL }) : r.fulfill({ status: 500, body: "x" })));
  await com(() => abrirFamilia({ visao: { papel: "membro", permissoes: [] }, antes, esperar: false }), async ({ page, s, bt, secao }) => {
    await secao.getByText("Não foi possível carregar.").waitFor({ timeout: 7000 }); // 2 retentativas (1 s + 2 s)
    assert.ok(!(await secao.innerText()).includes("Carregando"));
    assert.equal(s.reqs.filter((q) => q.caminho === "/historico").length, 0);
    ok = true;
    await bt("Tentar de novo").click();
    await secao.getByText("Ninguém viu nada ainda.").waitFor();
    assert.equal(s.reqs.filter((q) => q.caminho === "/historico").length, 1);
  });
  ok = false;
  await com(() => abrirFamilia({ visao: titular({ membros: [MARIA] }), antes }), async ({ page, s, bt, secao }) => {
    await bt("Ver").click();
    await secao.getByText("Não foi possível carregar.").waitFor({ timeout: 7000 });
    assert.ok(!(await secao.innerText()).includes("Carregando"));
    assert.equal(s.reqs.filter((q) => q.caminho.startsWith("/membros/")).length, 0);
    ok = true;
    await bt("Tentar de novo").click();
    await page.getByText("R$ 1.234,56").first().waitFor();
    assert.equal(s.reqs.filter((q) => q.caminho.startsWith("/membros/") && q.caminho.endsWith("/agregados")).length, 1);
  });
});

// mutação: tirar o debounce (`lido` = `mes`): 4 cliques viram 4 GETs de agregados
test("4 cliques rápidos em 'Mês anterior': 1 GET de agregados e 1 de lançamentos, do mês final; um clique: 1 GET depois do atraso; sem dado do mês anterior sob o rótulo novo", async () => {
  await com(() => abrirVer({ membro: TUDO }), async ({ page, secao, dados }) => {
    await secao.getByText("Compra no mercado").waitFor();
    await page.waitForTimeout(100);
    const base = dados("/agregados").length, baseL = dados("/lancamentos").length; // a abertura é imediata
    assert.deepEqual([base, baseL], [1, 1]);
    for (let n = 0; n < 4; n++) await mesAnterior(page).click();
    const t = await secao.innerText();
    assert.ok(t.includes("Carregando…") && !t.includes("1.234,56") && !t.includes("Compra no mercado"), "o resumo do mês anterior não fica sob o rótulo do mês novo");
    await secao.getByText("R$ 1.234,56").first().waitFor();
    await page.waitForTimeout(ATRASO);
    const a = dados("/agregados"), l = dados("/lancamentos");
    assert.equal(a.length, 2);
    assert.equal(l.length, 2);
    assert.equal(new URL(a[1].url).searchParams.get("mes"), "2026-06");
    assert.equal(new URL(l[1].url).searchParams.get("mes"), "2026-06");
    await mesAnterior(page).click();
    await secao.getByText("R$ 1.234,56").first().waitFor();
    await page.waitForTimeout(ATRASO);
    assert.equal(dados("/agregados").length, 3);
  });
});

// mutação: tirar `uid != null` do `enabled` de agregadosQuery (dados.ts): o "Ver" antes do perfil lê duas vezes (a chave muda com o uid) e o log do membro ganha duas linhas
test("'Ver' antes de o perfil chegar: 1 GET de agregados só, depois que o perfil chega", async () => {
  let soltar; const preso = new Promise((r) => (soltar = r));
  const antes = perfil(async (r) => { await preso; await r.fulfill({ json: PERFIL }); });
  await com(() => abrirFamilia({ visao: titular({ membros: [MARIA] }), antes, esperar: false }), async ({ page, s, bt }) => {
    await bt("Ver").click();
    await page.waitForTimeout(500);
    assert.equal(s.reqs.filter((q) => q.caminho.startsWith("/membros/")).length, 0);
    soltar();
    await page.getByText("R$ 1.234,56").first().waitFor();
    await page.waitForTimeout(500);
    assert.equal(s.reqs.filter((q) => q.caminho.startsWith("/membros/")).length, 1);
  });
});

// mutação: tirar a regra `unicode-bidi: isolate` do familia.css (ou os <bdi>)
test("texto do servidor isolado na direção: nome, quem viu, categoria e descrição", async () => {
  const isolado = (page, sel) => page.locator(sel).first().evaluate((e) => getComputedStyle(e).unicodeBidi);
  await com(() => abrirVer({ membro: TUDO }), async ({ page, secao }) => {
    await secao.getByText("Compra no mercado").waitFor();
    for (const sel of [".fam-ver-nome", ".fam-cats li > span:first-child", ".fam-lanc-txt b", ".fam-lanc-txt .faint bdi"]) assert.equal(await isolado(page, sel), "isolate", sel);
  });
  await com(() => abrirFamilia({ visao: titular({ membros: [MARIA] }) }), async ({ page }) => assert.equal(await isolado(page, ".fam-lista b"), "isolate"));
  const hist = { itens: [{ quando: "2026-10-01T12:00:00Z", quem: "مريم", escopo: "agregados", mes: "2026-10" }], proximo: null };
  await com(() => abrirFamilia({ visao: { papel: "membro", permissoes: ["agregados"] }, antes: (_c, s) => { s.historico = (r) => r.fulfill({ json: hist }); } }), async ({ page, secao }) => {
    await secao.getByText("مريم").waitFor();
    assert.equal(await isolado(page, ".fam-hist b"), "isolate");
  });
});

// mutação: voltar a desmontar o Resumo durante o atraso (`lido !== mes ? Carregando… : <Resumo key={mes}>`): a ida e volta relê o mês (2 GETs de cada)
test("ida e volta rápida no mês: nenhum GET novo, e durante o atraso o dado não fica à vista", async () => {
  await com(() => abrirVer({ membro: TUDO }), async ({ page, secao, dados }) => {
    await secao.getByText("Compra no mercado").waitFor();
    await page.waitForTimeout(100);
    assert.deepEqual([dados("/agregados").length, dados("/lancamentos").length], [1, 1]);
    await mesAnterior(page).click();
    const t = await secao.innerText();
    assert.ok(t.includes("Carregando…") && !t.includes("1.234,56") && !t.includes("Compra no mercado"));
    await page.getByRole("button", { name: "Próximo mês" }).click();
    await secao.getByText("Compra no mercado").waitFor();
    await page.waitForTimeout(ATRASO);
    assert.deepEqual([dados("/agregados").length, dados("/lancamentos").length], [1, 1]);
    assert.ok(!(await secao.innerText()).includes("Carregando"));
  });
});

// mutação: tirar o replace de controles bidi do primeiroNome (api.ts); positivo: o nome limpo continua no diálogo
test("controles de direção no nome não chegam ao diálogo 'Remover'", async () => {
  const sujo = { ...MARIA, nome: "‮abc Costa" };
  await com(() => abrirFamilia({ visao: titular({ membros: [sujo] }) }), async ({ page, bt, dlg }) => {
    await bt("Remover").click();
    await dlg().getByRole("heading", { name: "Remover abc?" }).waitFor();
    assert.ok(!/[‪-‮⁦-⁩]/.test(await dlg().innerText()));
  });
});
