/**
 * /painel#/conta/familia, as medidas (webapp/src/dashboard/styles/familia.css): desktop 1280x800 e celular 390x844 e 375x667.
 *   · sem rolagem horizontal; o link do convite (longo, sem espaço) fica dentro do cartão; botões de 44px ou mais;
 *   · o diálogo do Confirma cabe na janela; o "Sair" do menu lateral segue à vista com 5 itens na Conta.
 * Os números saem no console (a tabela do relato do PR). O comportamento: dashboard_v2_familia.test.mjs.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { F, abrirFamilia, titular } from "./_familia.mjs";

const browser = navegador();
void browser;

const NOME_LONGO = "Maria-Aparecida-de-Albuquerque-Cavalcanti-Souza-Bragança";
const MEMBROS = [{ ref: "r1", nome: NOME_LONGO, status: "suspenso" }, { ref: "r2", nome: "João", status: "ativo" }];
const LONGO = "Zx9_".repeat(16) + "end"; // 67 caracteres, sem espaço nem hífen (o hífen já é ponto de quebra): o pior caso do link
const caixa = (page, sel) => page.locator(sel).first().evaluate((e) => { const r = e.getBoundingClientRect(); return { l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height }; });

for (const [width, height] of [[1280, 800], [390, 844], [375, 667]]) {
  test(`${width}x${height}: sem rolagem lateral, link dentro do cartão, botões >= 44px, diálogo na janela`, async () => {
    const t = await abrirFamilia({ width, height, visao: titular({ capacidade: 5, membros: MEMBROS }) });
    try {
      const { page, bt, dlg, s } = t;
      s.convidar = (r) => r.fulfill({ status: 201, json: { ...F.convite, token: LONGO, link: `https://pigbankai.com/familia/entrar#t=${LONGO}` } });
      await bt("Gerar link de convite").click();
      await page.locator(".fam-link").waitFor();
      const largura = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth, bsw: document.body.scrollWidth }));
      const cartao = await caixa(page, ".conta-familia"), link = await caixa(page, ".fam-link");
      const botoes = await page.locator(".conta-familia button, .conta-familia a.btn").evaluateAll((es) => es.map((e) => Math.round(e.getBoundingClientRect().height)));
      const menor = Math.min(...botoes);
      await page.getByRole("button", { name: "Remover" }).first().click();
      await dlg().waitFor();
      const d = await caixa(page, "dialog[open]");
      console.log(`  ${width}x${height}: scrollWidth ${largura.sw} <= clientWidth ${largura.cw}; cartão ${Math.round(cartao.l)}..${Math.round(cartao.r)}, link ${Math.round(link.l)}..${Math.round(link.r)} (${Math.round(link.h)}px de altura); ${botoes.length} botões, menor ${menor}px; diálogo ${Math.round(d.l)}..${Math.round(d.r)} x ${Math.round(d.t)}..${Math.round(d.b)}`);
      assert.ok(largura.sw <= largura.cw && largura.bsw <= largura.cw, JSON.stringify(largura));
      assert.ok(link.l >= cartao.l && link.r <= cartao.r + 0.5, `link ${link.l}..${link.r} fora do cartão ${cartao.l}..${cartao.r}`);
      assert.ok(menor >= 44, `botão de ${menor}px`);
      assert.ok(d.l >= 0 && d.t >= 0 && d.r <= width && d.b <= height, "o diálogo passa da janela");
      assert.deepEqual(t.erros, []);
    } finally { await t.ctx.close(); }
  });
}

for (const [width, height] of [[1280, 800], [1280, 720]]) {
  test(`${width}x${height}: com 5 itens na Conta o "Sair" do menu lateral continua à vista e clicável`, async () => {
    const t = await abrirFamilia({ width, height });
    try {
      const { page } = t;
      const sair = page.locator('.rail-conta button[title="Sair"]');
      await sair.waitFor();
      const c = await caixa(page, '.rail-conta button[title="Sair"]');
      // o ponto central do botão é o próprio botão (nada o cobre) e está dentro da janela
      const topo = await sair.evaluate((e) => { const r = e.getBoundingClientRect(), x = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return e.contains(x); });
      console.log(`  ${width}x${height}: "Sair" em y ${Math.round(c.t)}..${Math.round(c.b)} (janela ${height}), descoberto: ${topo}; itens na Conta: ${await page.locator(".rail-conta li a").count()}`);
      assert.equal(await page.locator(".rail-conta li a").count(), 5);
      assert.ok(c.b <= height && c.t >= 0, `Sair em ${c.t}..${c.b}, janela ${height}`);
      assert.equal(topo, true);
    } finally { await t.ctx.close(); }
  });
}

test("celular: o Menu lista a Família junto da Conta", async () => {
  const t = await abrirFamilia({ width: 390, height: 844 });
  try {
    await t.page.getByRole("button", { name: /^Menu/ }).click();
    await t.page.getByRole("link", { name: "Família" }).first().waitFor();
    assert.equal(await t.page.getByRole("link", { name: "Família" }).count(), 1);
  } finally { await t.ctx.close(); }
});
