/**
 * Layout da área do afiliado no /painel e na página sem plano, nos tamanhos que o plano pede. Mede caixa real; `env(safe-area-inset-*)` vale 0 no headless, então os insets NÃO são medidos aqui.
 * Em cada teste, o comentário de cima diz qual mutação o deixa vermelho.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { ATIVA, COM_SAQUE, abrirSemPlano, comAfiliado, pedirSaque } from "./_afiliado.mjs";

const browser = navegador();
const TAMANHOS = [[1280, 800], [1280, 720], [390, 844], [375, 667], [320, 568]];
const CODIGO_LONGO = "AFILIADOLONGO2026XYZW";
const LONGA = { ...ATIVA, code: CODIGO_LONGO, link: `https://pigbankai.com/r/${CODIGO_LONGO}/${"x".repeat(80)}`,
  stats: { ...ATIVA.stats, generated: 1234567.89, held: 234567.89, available: 123456.78, paid: 876543.22 },
  payouts: [{ id: 7, amount: 90, status: "rejected", requested_at: "2026-09-10T12:00:00+00:00", paid_at: null, note: "m".repeat(120) }] };

const medir = (page) => page.evaluate(() => {
  const fora = [];
  for (const panel of document.querySelectorAll(".afiliado .panel, .afiliado.panel")) {
    const p = panel.getBoundingClientRect();
    for (const e of panel.querySelectorAll("*")) {
      const r = e.getBoundingClientRect();
      if (r.width && (r.right > p.right + 0.5 || r.left < p.left - 0.5)) fora.push(`${e.tagName}.${e.className}`);
    }
  }
  const baixos = [...document.querySelectorAll("#main .btn, #main input, .afil-so-topo .btn")].filter((b) => b.getBoundingClientRect().height < 44).map((b) => b.textContent || b.id);
  const cortados = [...document.querySelectorAll(".afil-quadros dd")].filter((d) => d.scrollWidth > d.clientWidth).map((d) => d.textContent);
  return { sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth, fora, baixos, cortados, quadros: document.querySelectorAll(".afil-quadros > div").length };
});
const confere = (m, tam) => {
  assert.ok(m.sw <= m.cw, `${tam}: rolagem horizontal ${m.sw}/${m.cw}`);
  assert.deepEqual(m.fora, [], `${tam}: fora do cartão`);
  assert.deepEqual(m.baixos, [], `${tam}: controle abaixo de 44 px`);
  assert.deepEqual(m.cortados, [], `${tam}: número cortado no quadro`);
  assert.equal(m.quadros, 7, tam);
};

for (const [width, height] of TAMANHOS) {
  // mutação: tirar `word-break: break-all` de `.afil-copia code`, ou o `min-height: 44px` de `.afiliado .btn`: o link estoura ou o botão fica com 36 px
  test(`${width}x${height}: sem rolagem lateral, link longo no cartão, alvos de 44 px, 7 quadros inteiros`, async () => {
    await comAfiliado(browser(), { width, height, corpo: LONGA }, async (t) => {
      await t.page.locator(".afil-quadros").waitFor();
      const m = await medir(t.page);
      console.log(`  ${width}x${height}: scrollWidth ${m.sw}/${m.cw}, fora ${m.fora.length}, baixos ${m.baixos.length}, cortados ${m.cortados.length}`);
      confere(m, `${width}x${height}`);
    });
  });

  // mutação: tirar `overflow-wrap`/`word-break` de `.cofre p`: a chave de 140 caracteres sem espaço estoura o diálogo
  test(`${width}x${height}: o diálogo de saque, com chave de 140 caracteres, cabe na janela`, async () => {
    await comAfiliado(browser(), { width, height }, async (t) => {
      await t.page.locator("#afil-pix").waitFor();
      const dlg = await pedirSaque(t.page, "k".repeat(140));
      const caixa = await dlg.boundingBox();
      assert.ok(caixa.x >= 0 && caixa.y >= 0 && caixa.x + caixa.width <= width && caixa.y + caixa.height <= height, JSON.stringify(caixa));
      const estoura = await dlg.locator("p").evaluate((p) => p.scrollWidth > p.clientWidth);
      assert.equal(estoura, false);
      for (const b of await dlg.getByRole("button").all()) {
        const c = await b.boundingBox();
        assert.ok(c.y + c.height <= height, "botão fora da janela");
      }
    });
  });

  // mutação: o `.afil-so-topo` sem `minmax(0, 1fr)` ou o `.afil-so` sem `max-width`: a página sem plano passa da tela
  test(`${width}x${height}: a página sem plano, sem rolagem lateral e com Sair à vista`, async () => {
    const t = await abrirSemPlano(browser(), { width, height, corpo: LONGA });
    try {
      await t.page.locator(".afil-quadros").waitFor();
      const m = await medir(t.page);
      confere(m, `sem plano ${width}x${height}`);
      const sair = await t.page.getByRole("button", { name: "Sair" }).boundingBox();
      assert.ok(sair.y >= 0 && sair.x + sair.width <= width, JSON.stringify(sair));
    } finally { await t.ctx.close(); }
  });
}

// mutação: um 10º item no RAIL, ou tirar a compactação por altura do shell.css: o Sair desce para fora do trilho em 1280x720
for (const [width, height] of [[1280, 800], [1280, 720]]) {
  test(`${width}x${height}: com o item Afiliado, o Sair segue à vista no trilho`, async () => {
    await comAfiliado(browser(), { width, height }, async (t) => {
      const sair = await t.page.locator(".rail-conta button[title='Sair']").boundingBox();
      console.log(`  Sair em ${width}x${height}: y ${sair.y} + ${sair.height} = ${sair.y + sair.height}`);
      assert.ok(sair.y + sair.height <= height, JSON.stringify(sair));
      const rolou = await t.page.locator(".rail").evaluate((r) => r.scrollHeight > r.clientHeight);
      assert.equal(rolou, false, "o trilho não pode precisar rolar");
    });
  });
}

// mutação: tirar o bloco `max-width: 480px` do afiliado.css: o código volta a quebrar ao lado do botão, mesmo cabendo no cartão
for (const [width, height] of [[390, 844], [320, 568]]) {
  test(`${width}x${height}: o código curto fica numa linha só e o botão de sacar tem a linha toda`, async () => {
    await comAfiliado(browser(), { width, height }, async (t) => {
      await t.page.locator(".afil-copia code").first().waitFor();
      const linhas = await t.page.locator(".afil-copia code").first().evaluate((c) => Math.round(c.getBoundingClientRect().height / parseFloat(getComputedStyle(c).lineHeight || 20) - 0.6));
      assert.ok(linhas <= 1, `código em ${linhas} linhas`);
      const campo = await t.page.locator("#afil-pix").boundingBox(), botao = await t.page.getByRole("button", { name: /^Sacar/ }).boundingBox();
      assert.ok(botao.y >= campo.y + campo.height, "o botão desce para baixo do campo");
    });
  });
}

// mutação: voltar a `auto-fit` na `.afil-quadros`: na página sem plano, sem trilho, os quadros ficam 5+2
test("1280x800: os quadros ficam 4+3, com e sem o trilho", async () => {
  const fileiras = (page) => page.locator(".afil-quadros > div").evaluateAll((d) => { const t = {}; d.forEach((e) => { const y = Math.round(e.getBoundingClientRect().top); t[y] = (t[y] ?? 0) + 1; }); return Object.values(t); });
  await comAfiliado(browser(), {}, async (t) => { await t.page.locator(".afil-quadros").waitFor(); assert.deepEqual(await fileiras(t.page), [4, 3]); });
  const t = await abrirSemPlano(browser(), {});
  try { await t.page.locator(".afil-quadros").waitFor(); assert.deepEqual(await fileiras(t.page), [4, 3]); } finally { await t.ctx.close(); }
});

// controle: a medição enxerga o estouro quando o CSS da área sai (senão ela não mede nada)
test("controle da medição: sem o CSS da área, o link longo estoura em 320x568", async () => {
  await comAfiliado(browser(), { width: 320, height: 568, corpo: LONGA }, async (t) => {
    await t.page.locator(".afil-quadros").waitFor();
    const com = await medir(t.page);
    await t.page.evaluate(() => {
      for (const folha of document.styleSheets) for (let i = folha.cssRules.length - 1; i >= 0; i--) if (/afil|afiliado/.test(folha.cssRules[i].selectorText ?? "")) folha.deleteRule(i);
    });
    const sem = await medir(t.page);
    console.log(`  controle 320x568: com o CSS scrollWidth ${com.sw}/${com.cw}, fora ${com.fora.length}; sem ele ${sem.sw}/${sem.cw}, fora ${sem.fora.length}, baixos ${sem.baixos.length}`);
    assert.deepEqual(com.fora, []);
    assert.ok(sem.fora.length > 0 || sem.sw > sem.cw || sem.baixos.length > 0, "sem o CSS nada mudou: a medição não mede");
  });
});

test("saque aberto em 320x568: os selos e o aviso cabem", async () => {
  await comAfiliado(browser(), { width: 320, height: 568, corpo: COM_SAQUE }, async (t) => {
    await t.page.locator(".afil-lista").first().waitFor();
    confere(await medir(t.page), "saque aberto");
  });
});
