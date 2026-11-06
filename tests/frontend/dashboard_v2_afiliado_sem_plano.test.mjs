/**
 * A página sem plano (GET /afiliado, webapp/src/dashboard/afiliado/SemPlano.tsx): só a área, sem o portão, o App e os eventos. As portas do servidor estão em tests/test_pagina_afiliado.py.
 * Em cada teste, o comentário de cima diz qual mutação o deixa vermelho.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { COM_SAQUE, DESATIVADA, abrirSemPlano, confirmar, pedirSaque, quadros } from "./_afiliado.mjs";

const browser = navegador();
const com = async (opts, fn) => {
  const t = await abrirSemPlano(browser(), opts);
  try { await fn(t); assert.deepEqual(t.erros, [], "pageerror"); } finally { await t.ctx.close(); }
};

// mutação: montar <Entrada> em /afiliado (main.tsx): o /api/v2/me sai, dá 402 e a tela vira "Não deu para carregar o painel"
test("monta só a área: nenhuma /api/v2, sem trilho nem barra de baixo, com os números e o saque", async () => {
  await com({ corpo: DESATIVADA }, async (t) => {
    await t.page.locator(".afil-quadros").waitFor();
    assert.equal((await quadros(t.page))["Disponível"], "R$ 120,00");
    await t.page.waitForTimeout(200);
    assert.deepEqual(t.v2, []);
    assert.equal(await t.page.locator(".rail, .tabbar, .topbar").count(), 0);
    assert.match(await t.page.locator(".afil-aviso").innerText(), /desativado/);
    assert.equal(await t.page.title(), "PigBank · Afiliado");
  });
});

// mutação: tirar `afiliado` dos eventos ou chamar a /api/v2 no saque: a mesma escrita precisa funcionar aqui
test("o saque funciona na página sem plano, com confirmação e releitura", async () => {
  await com({}, async (t) => {
    await t.page.locator("#afil-pix").waitFor();
    await pedirSaque(t.page);
    await confirmar(t.page).click();
    await t.page.getByText("Saque solicitado.").waitFor();
    assert.equal(t.s.posts[0].corpo, JSON.stringify({ pix_key: "ana@pix.com" }));
    assert.equal(t.s.corpo, COM_SAQUE);
    await t.page.getByText("Você já tem um saque em análise.").waitFor();
    assert.deepEqual(t.v2, []);
  });
});

// mutação: trocar o href de "Ver planos" por um que não comece em /precos: o auth-refresh.js não o esconde no app (diretriz 3.1.1)
test("'Ver planos' aparece no navegador e some no app iOS", async () => {
  await com({}, async (t) => {
    assert.equal(await t.page.getByRole("link", { name: "Ver planos" }).isVisible(), true);
    assert.match(await t.page.getByRole("link", { name: "Ver planos" }).getAttribute("href"), /^\/precos/);
  });
  await com({ userAgent: "Mozilla/5.0 (iPhone) AppleWebKit/605.1.15 PigBankApp/1.0" }, async (t) => {
    await t.page.locator(".afil-so-topo").waitFor();
    assert.equal(await t.page.locator(".afil-so-topo a").count(), 1, "o link existe no DOM");
    assert.equal(await t.page.locator(".afil-so-topo a").isVisible(), false);
    assert.equal(await t.page.getByRole("button", { name: "Sair" }).isVisible(), true);
  });
});

// mutação: tirar o `onClick={sair}`: o botão não encerra a sessão
test("Sair manda o POST de logout", async () => {
  await com({}, async (t) => {
    let saiu = 0;
    await t.ctx.route("**/auth/logout", (r) => { saiu += 1; return r.fulfill({ json: { ok: true } }); });
    await t.page.getByRole("button", { name: "Sair" }).click();
    await t.page.waitForURL(/logout=1/).catch(() => {});
    assert.equal(saiu, 1);
  });
});
