/**
 * /painel#/conta/familia, os lançamentos do membro e as bordas do contrato (webapp/src/dashboard/familia/MembroLancamentos.tsx, dados.ts).
 * Em cada teste, o comentário de cima diz qual mutação o deixa vermelho.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { E422, ERRO, F, MARIA, abrirFamilia, abrirVer, titular } from "./_familia.mjs";

const browser = navegador();
void browser;

const TUDO = { ...MARIA, permissoes: ["agregados", "lancamentos"] };
const CUR = "Z0FB+/=_-x"; // o alfabeto do Fernet tem os caracteres que a URL precisa codificar
const com = async (abrir, fn) => {
  const t = await abrir();
  try { await fn(t); assert.deepEqual(t.erros, [], "pageerror"); } finally { await t.ctx.close(); }
};
const ver = (fn, opts = {}) => com(() => abrirVer({ membro: TUDO, ...opts }), fn);
const pausa = (page) => page.waitForTimeout(400);
const url = (q) => new URL(q.url).searchParams;
const maisUma = (r, extra = {}) => r.fulfill({ json: { ...F.lancamentos, proximo: null, ...extra } });

// mutação: cursor montado sem URLSearchParams; laço automático em página vazia; esconder o "Ver mais" quando a página vem vazia com `proximo`
test("paginação: página vazia com 'proximo' mantém o 'Ver mais' sem laço; o cursor volta exatamente como veio; mês novo começa sem cursor", async () => {
  const antes = (_ctx, s) => { s.lancamentos = (r) => (url(s.reqs.at(-1)).get("cursor") ? maisUma(r) : r.fulfill({ json: { ...F.lancamentos, itens: [], proximo: CUR } })); };
  await ver(async ({ page, secao, dados, bt }) => {
    await bt("Ver mais").waitFor();
    await pausa(page);
    assert.equal(dados("/lancamentos").length, 1); // sem laço
    assert.ok(!(await secao.innerText()).includes("Nenhum lançamento neste mês."));
    await bt("Ver mais").click();
    await secao.getByText("Compra no mercado").waitFor();
    const g = dados("/lancamentos");
    assert.equal(g.length, 2);
    assert.equal(url(g[1]).get("cursor"), CUR);
    assert.match(g[1].url, /cursor=Z0FB%2B%2F%3D_-x/);
    const txt = await secao.innerText();
    for (const t of ["−R$ 89,90", "+R$ 1.234,56", "Mercado", "Sem categoria", "01/11"]) assert.ok(txt.includes(t), t); // "Mercado": o nome vem dos resumos DO MEMBRO
    assert.equal(await bt("Ver mais").count(), 0);
    await page.getByRole("button", { name: "Mês anterior" }).click();
    await page.waitForFunction(() => document.body.innerText.includes("Lançamentos"));
    await pausa(page);
    const ultimo = dados("/lancamentos").at(-1);
    assert.equal(url(ultimo).get("cursor"), null);
    assert.notEqual(url(ultimo).get("mes"), url(g[0]).get("mes"));
  }, { antes });
});

test("lançamentos vazios sem 'proximo': 'Nenhum lançamento neste mês.'", async () => {
  await ver(async ({ secao }) => { await secao.getByText("Nenhum lançamento neste mês.").waitFor(); },
    { antes: (_c, s) => { s.lancamentos = (r) => r.fulfill({ json: { ...F.lancamentos, itens: [], proximo: null } }); } });
});

// mutação: tirar o `reset` do cursor recusado (MembroLancamentos.tsx), ou deixá-lo sem o freio de uma vez
test("422 'cursor' descarta as páginas e relê a partir da primeira, UMA vez", async () => {
  const antes = (_c, s) => { s.lancamentos = (r) => (url(s.reqs.at(-1)).get("cursor") ? E422("cursor")(r) : r.fulfill({ json: { ...F.lancamentos, proximo: CUR } })); };
  await ver(async ({ page, secao, dados, bt }) => {
    await bt("Ver mais").click();
    await pausa(page);
    assert.equal(dados("/lancamentos").length, 3);
    assert.equal(url(dados("/lancamentos")[2]).get("cursor"), null);
    await bt("Ver mais").click(); // a 2ª recusa não relê de novo: vira erro com "Tentar de novo"
    await secao.getByText("Não foi possível carregar.").waitFor();
    await pausa(page);
    assert.equal(dados("/lancamentos").length, 4);
  }, { antes });
});

// mutação: tratar o 403 dos lançamentos como o dos resumos (o painel inteiro some), ou tirar o reler() do aoParar (VerMembro.tsx)
test("403 só nos lançamentos: o resumo fica, a seção vira o texto e a família é relida", async () => {
  await ver(async ({ page, s, secao, bt }) => {
    const familias = () => s.reqs.filter((q) => q.metodo === "GET" && q.caminho === "").length;
    await secao.getByText("Maria parou de compartilhar os lançamentos.").waitFor();
    await pausa(page);
    assert.ok((await secao.innerText()).includes("R$ 1.234,56"));
    assert.equal(familias(), 2);
    assert.equal(await bt("Voltar").count(), 1);
  }, { antes: (_c, s) => { s.lancamentos = (r) => { s.visao = titular({ membros: [MARIA] }); return ERRO(403, "consent_required")(r); }; } }); // a releitura já vem sem o escopo
});

// mutação: Number() no dinheiro; aceitar `entrou` numérico; não ignorar campo extra
test("contrato: valor em número vira erro com 'Tentar de novo' (sem pageerror); campo extra é ignorado", async () => {
  await com(() => abrirFamilia({ visao: titular({ membros: [MARIA] }) }), async ({ page, s, bt, secao }) => {
    s.agregados = (r) => r.fulfill({ json: { ...F.agregados, entrou: 1234.56 } });
    await bt("Ver").click();
    await secao.getByText("Não foi possível carregar.").waitFor();
    assert.ok(!(await secao.innerText()).includes("1234"));
    s.agregados = (r) => r.fulfill({ json: { ...F.agregados, campo_novo: { a: 1 }, categorias: F.agregados.categorias.map((c) => ({ ...c, outro: 1 })) } });
    await bt("Tentar de novo").click();
    await page.getByText("R$ 1.234,56").first().waitFor();
  });
  await com(() => abrirFamilia({ visao: titular({ membros: [TUDO] }) }), async ({ bt, secao, s }) => {
    s.lancamentos = (r) => r.fulfill({ json: { mes: "2026-10", proximo: null } }); // sem `itens`
    await bt("Ver").click();
    await secao.getByText("R$ 1.234,56").first().waitFor();
    await secao.getByText("Não foi possível carregar.").waitFor(); // só a seção de lançamentos erra; o resumo fica
  });
});

// mutação: deixar passar o escopo desconhecido para o Titular.tsx
test("escopo desconhecido nunca mostra o 'Ver'; campo 'permissoes' ausente é lista vazia", async () => {
  const membros = [{ ref: "a1", nome: "Ana", status: "ativo", permissoes: ["foo", "lancamentos"] }, { ref: "b1", nome: "Bia", status: "ativo", permissoes: ["foo", "agregados"] }, { ref: "c1", nome: "Caio", status: "ativo" }];
  await com(() => abrirFamilia({ visao: titular({ membros }) }), async ({ bt, secao }) => {
    assert.equal(await bt("Ver").count(), 1); // só a Bia
    assert.equal((await secao.innerText()).match(/Não compartilha/g).length, 2);
  });
});
