/**
 * /painel#/casa, as medidas do PR 6c (webapp/src/dashboard/styles/familia.css, bloco "PR 6c"): desktop 1280x800 e celular 390x844 e 375x667.
 *   · sem rolagem horizontal; nada passa da borda do cartão com o pior caso (R$ 999.999.999,99, nome de meta de 60 caracteres sem espaço, categoria de 80, autor longo);
 *   · botões com 44px ou mais; o diálogo (com erro na tela) cabe na janela, sem rolagem horizontal, com os botões à vista.
 * Os números saem no console (a tabela do PR). O controle "quanto daria sem a correção": o mesmo caso com o CSS novo desligado estoura.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { F, comCasa } from "./_familia.mjs";

const browser = navegador();
void browser;

const L60 = "Zx9_".repeat(15), L80 = "Qw8_".repeat(20), MAX = "999999999.99";
const CASA = {
  ...F.casa,
  metas: [{ ...F.casa.metas[0], nome: L60, alvo: MAX, guardado: MAX, falta: "0.00", percentual: 100, autor: L60 }, F.casa.metas[1]],
  orcamentos: [{ ...F.casa.orcamentos[0], categoria: L80, limite: MAX, gasto: MAX, restante: "-" + MAX, autor: L60 }, F.casa.orcamentos[1]],
};

const medir = (page) => page.evaluate(() => {
  const doc = document.documentElement, cartao = document.querySelector(".fam-casa").getBoundingClientRect();
  const fora = [...document.querySelectorAll(".fam-casa *")].filter((e) => e.getClientRects().length && !e.closest("dialog") && (e.getBoundingClientRect().right > cartao.right + 0.5 || (e.clientWidth > 0 && e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).textOverflow !== "ellipsis"))).map((e) => e.className || e.tagName);
  const alvos = [...document.querySelectorAll(".fam-casa button, .fam-casa input")].filter((e) => e.getClientRects().length).map((e) => Math.round(e.getBoundingClientRect().height));
  return { sw: doc.scrollWidth, cw: doc.clientWidth, bsw: document.body.scrollWidth, fora, menor: Math.min(...alvos), alvos: alvos.length, cartao: { l: Math.round(cartao.left), r: Math.round(cartao.right) } };
});
const medirDialogo = (page) => page.evaluate(() => {
  const d = document.querySelector("dialog[open]"), r = d.getBoundingClientRect();
  const botoes = [...d.querySelectorAll("button")].map((b) => b.getBoundingClientRect());
  return { l: Math.round(r.left), t: Math.round(r.top), r: Math.round(r.right), b: Math.round(r.bottom), w: innerWidth, h: innerHeight, sw: d.scrollWidth, cw: d.clientWidth,
    botoesDentro: botoes.every((x) => x.right <= r.right + 0.5 && x.left >= r.left - 0.5), menor: Math.round(Math.min(...[...d.querySelectorAll("button, input")].map((e) => e.getBoundingClientRect().height))) };
});

for (const [width, height] of [[1280, 800], [390, 844], [375, 667]]) {
  test(`${width}x${height}: a casa cabe no cartão com o pior caso, alvos >= 44px; o diálogo com erro cabe na janela`, async () => {
    await comCasa({ width, height, casa: CASA }, async (t) => {
      await t.page.locator(".casa-lista li").first().waitFor();
      const m = await medir(t.page);
      console.log(`  ${width}x${height}: scrollWidth ${m.sw} <= clientWidth ${m.cw}; cartão ${m.cartao.l}..${m.cartao.r}; ${m.alvos} alvos, menor ${m.menor}px; fora do cartão: ${m.fora.length}`);
      assert.ok(m.sw <= m.cw && m.bsw <= m.cw, JSON.stringify(m));
      assert.deepEqual(m.fora, []);
      assert.ok(m.menor >= 44, `alvo de ${m.menor}px`);
      // o diálogo de edição, com o erro mais comprido (conflito + Recarregar) na tela
      t.s.casaEditar = (r) => r.fulfill({ status: 409, json: { error: { code: "edit_conflict", message: "x" } } });
      await t.page.locator(".casa-lista li").first().getByRole("button", { name: "Editar" }).click();
      await t.dlg().getByRole("button", { name: "Salvar" }).click();
      await t.dlg().getByRole("button", { name: "Recarregar" }).waitFor();
      const d = await medirDialogo(t.page);
      console.log(`  ${width}x${height}: diálogo ${d.l},${d.t}..${d.r},${d.b} na janela ${d.w}x${d.h}; scrollWidth ${d.sw} <= ${d.cw}; botões dentro ${d.botoesDentro}; menor alvo ${d.menor}px`);
      assert.ok(d.l >= 0 && d.t >= 0 && d.r <= d.w && d.b <= d.h, JSON.stringify(d));
      assert.ok(d.sw <= d.cw && d.botoesDentro && d.menor >= 44, JSON.stringify(d));
    });
  });
}

// (o selo do autor corta com reticências de propósito: é o único texto que `fora` não conta por scrollWidth)
// o controle: sem as regras novas de familia.css o mesmo caso passa da borda (senão a medição acima não mede nada)
test("controle: 375x667 sem o CSS novo da casa, o caso longo estoura", async () => {
  await comCasa({ width: 375, height: 667, casa: CASA }, async (t) => {
    await t.page.locator(".casa-lista li").first().waitFor();
    const com = await medir(t.page);
    await t.page.evaluate(() => {
      for (const folha of document.styleSheets) {
        for (let i = folha.cssRules.length - 1; i >= 0; i--) if (/casa-|fam-casa/.test(folha.cssRules[i].selectorText ?? "")) folha.deleteRule(i);
      }
    });
    const sem = await medir(t.page);
    console.log(`  controle 375x667: com o CSS novo scrollWidth ${com.sw}/${com.cw}, fora ${com.fora.length}; sem ele scrollWidth ${sem.sw}/${sem.cw}, fora ${sem.fora.length} (${sem.fora.slice(0, 4).join(", ")})`);
    assert.deepEqual(com.fora, []);
    assert.ok(sem.fora.length > 0 || sem.sw > sem.cw, "sem o CSS novo nada estourou: a medição não mede");
  });
});
