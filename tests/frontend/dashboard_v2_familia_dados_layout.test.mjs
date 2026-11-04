/**
 * /painel#/conta/familia, as medidas do PR 6b (webapp/src/dashboard/styles/familia.css): desktop 1280x800 e celular 390x844 e 375x667.
 *   · sem rolagem horizontal; nada passa da borda do cartão com o pior caso (R$ 999.999.999,99, nome de 60 caracteres sem espaço, categoria longa);
 *   · botões e interruptores com 44px ou mais; o seletor de mês inteiro à vista.
 * Os números saem no console (a tabela do PR). O controle "quanto daria sem a correção": o mesmo caso com o CSS novo desligado estoura.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { F, MARIA, abrirFamilia, titular } from "./_familia.mjs";

const browser = navegador();
void browser;

const LONGO = "Zx9_".repeat(15); // 60 caracteres, sem espaço nem hífen: o pior caso de quebra
const MAX = "999999999.99";
const NOME = { ...MARIA, nome: LONGO, permissoes: ["agregados", "lancamentos"] };
const orc = { ...F.agregados.orcamentos.itens[0], categoria: LONGO, teto: MAX, gasto: MAX, restante: "0.00", usado_pct: "100.00", status: "vermelho" };
const AGREGADOS = { ...F.agregados, entrou: MAX, saiu: MAX, categorias: [{ ...F.agregados.categorias[0], nome: LONGO, valor: MAX }], orcamentos: { ...F.agregados.orcamentos, itens: [orc] } };
const LANCAMENTOS = { ...F.lancamentos, proximo: null, itens: [{ ...F.lancamentos.itens[0], descricao: LONGO, valor: MAX, categoria: LONGO }] };

const medir = (page) => page.evaluate(() => {
  const doc = document.documentElement, cartao = document.querySelector(".conta-familia").getBoundingClientRect();
  const fora = [...document.querySelectorAll(".conta-familia *")].filter((e) => e.getClientRects().length && (e.getBoundingClientRect().right > cartao.right + 0.5 || (e.clientWidth > 0 && e.scrollWidth > e.clientWidth + 1))).map((e) => e.className || e.tagName);
  const alvos = [...document.querySelectorAll(".conta-familia button, .conta-familia .icon-btn, .conta-familia input[role=switch]")].filter((e) => e.getClientRects().length).map((e) => Math.round(e.getBoundingClientRect().height));
  const mes = document.querySelector(".fam-ver .month-switch")?.getBoundingClientRect();
  return { sw: doc.scrollWidth, cw: doc.clientWidth, bsw: document.body.scrollWidth, fora, menor: Math.min(...alvos), alvos: alvos.length, mes: mes && { l: Math.round(mes.left), r: Math.round(mes.right) }, cartao: { l: Math.round(cartao.left), r: Math.round(cartao.right) } };
});
const aberto = async (width, height) => {
  const t = await abrirFamilia({ width, height, visao: titular({ capacidade: 5, membros: [NOME] }) });
  t.s.agregados = (r) => r.fulfill({ json: AGREGADOS });
  t.s.lancamentos = (r) => r.fulfill({ json: LANCAMENTOS });
  await t.bt("Ver").click();
  await t.page.getByRole("heading", { name: "Lançamentos" }).waitFor();
  await t.page.locator(".fam-lanc").waitFor();
  return t;
};

for (const [width, height] of [[1280, 800], [390, 844], [375, 667]]) {
  test(`${width}x${height}: o painel do membro cabe no cartão com o pior caso, alvos >= 44px, seletor de mês à vista`, async () => {
    const t = await aberto(width, height);
    try {
      const m = await medir(t.page);
      console.log(`  ${width}x${height}: scrollWidth ${m.sw} <= clientWidth ${m.cw}; cartão ${m.cartao.l}..${m.cartao.r}, seletor de mês ${m.mes.l}..${m.mes.r}; ${m.alvos} alvos, menor ${m.menor}px; fora do cartão: ${m.fora.length}`);
      assert.ok(m.sw <= m.cw && m.bsw <= m.cw, JSON.stringify(m));
      assert.deepEqual(m.fora, []);
      assert.ok(m.menor >= 44, `alvo de ${m.menor}px`);
      assert.ok(m.mes.l >= m.cartao.l && m.mes.r <= m.cartao.r + 0.5, "seletor de mês fora do cartão");
      assert.deepEqual(t.erros, []);
    } finally { await t.ctx.close(); }
  });

  test(`${width}x${height}: a lista do titular e a visão do membro (interruptores, histórico) cabem`, async () => {
    const lista = await abrirFamilia({ width, height, visao: titular({ capacidade: 5, membros: [NOME] }) });
    try {
      const m = await medir(lista.page);
      console.log(`  ${width}x${height}: lista do titular: scrollWidth ${m.sw} <= ${m.cw}; fora do cartão ${m.fora.length}; menor alvo ${m.menor}px`);
      assert.ok(m.sw <= m.cw && m.fora.length === 0 && m.menor >= 44, JSON.stringify(m));
    } finally { await lista.ctx.close(); }
    const t = await abrirFamilia({ width, height, visao: { papel: "membro", permissoes: ["agregados"] }, antes: (_c, s) => { s.historico = (r) => r.fulfill({ json: { itens: [{ quando: "2026-11-02T17:30:00+00:00", quem: LONGO, escopo: "lancamentos", mes: "2026-10" }], proximo: "41" } }); } });
    try {
      await t.page.locator(".fam-hist li").waitFor();
      const m = await medir(t.page);
      console.log(`  ${width}x${height}: membro: scrollWidth ${m.sw} <= ${m.cw}; fora do cartão ${m.fora.length}; ${m.alvos} alvos, menor ${m.menor}px`);
      assert.ok(m.sw <= m.cw && m.fora.length === 0 && m.menor >= 44, JSON.stringify(m));
    } finally { await t.ctx.close(); }
  });
}

// o controle: sem as regras novas de familia.css o mesmo caso passa da borda (senão a medição acima não mede nada)
test("controle: 375x667 sem o CSS novo da Família, o caso longo estoura", async () => {
  const t = await aberto(375, 667);
  try {
    const com = await medir(t.page);
    await t.page.evaluate(() => {
      for (const folha of document.styleSheets) {
        for (let i = folha.cssRules.length - 1; i >= 0; i--) if (/fam-(ver|valores|cats|lanc|hist|perm|acoes-membro|corpo)|conta-familia h[23]/.test(folha.cssRules[i].selectorText ?? "")) folha.deleteRule(i);
      }
    });
    const sem = await medir(t.page);
    console.log(`  controle 375x667: com o CSS novo scrollWidth ${com.sw}/${com.cw}, fora ${com.fora.length}; sem ele scrollWidth ${sem.sw}/${sem.cw}, fora ${sem.fora.length} (${sem.fora.slice(0, 4).join(", ")})`);
    assert.deepEqual(com.fora, []);
    assert.ok(sem.fora.length > 0 || sem.sw > sem.cw, "sem o CSS novo nada estourou: a medição não mede");
  } finally { await t.ctx.close(); }
});
