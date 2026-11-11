/**
 * #1412 no /painel (afiliado/Saque.tsx): a troca de conta recarrega a página (lib/sessao.ts) e a confirmação do saque sumia sem
 * aviso. Com ela aberta, uma marca em sessionStorage guarda a etapa (nunca a chave Pix); a página recarregada explica no alerta do Saque.
 * Controle negativo: tirar as duas chamadas de `marcar` deixa vermelhos P1, P2 e P3.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { abrirAfiliado, confirmar, pedirSaque } from "./_afiliado.mjs";

const browser = navegador();
const CHAVE = "ana@pix.com";
const ANTES = "Sua sessão mudou e a página foi recarregada. Esta solicitação de saque não foi enviada. Confira a conta antes de tentar novamente.";
const DURANTE = "Sua sessão mudou durante a solicitação de saque. Não foi possível confirmar o resultado. Acesse a conta usada na solicitação e confira o histórico de saques antes de tentar novamente.";
const MARCA = "pb_saque_interrompido";
const alerta = (page) => page.locator("#afil-saque").locator("..").locator("[role=alert]");

async function comSaque(opts, fn) {
  const t = await abrirAfiliado(browser(), opts);
  let cargas = 0;
  t.page.on("load", () => { cargas += 1; });
  try { await fn({ ...t, cargas: () => cargas }); } finally { await t.ctx.close(); }
}
/** Espera a página recarregada mostrar o Saque de novo. */
async function recarregou(t) {
  const fim = Date.now() + 10000;
  while (t.cargas() < 1) { if (Date.now() > fim) throw new Error("não recarregou"); await new Promise((r) => setTimeout(r, 50)); }
  await t.page.locator("#afil-pix").waitFor();
}
const dentro = async (page, loc, w, h) => {
  await loc.scrollIntoViewIfNeeded();
  const b = await loc.boundingBox();
  assert.ok(b.x >= 0 && b.x + b.width <= w + 0.5 && b.y >= 0 && b.y + b.height <= h, JSON.stringify(b));
};

// P1: a outra aba entrou noutra conta com a confirmação aberta.
for (const [w, h] of [[1280, 800], [390, 844], [320, 568]]) {
  test(`P1 ${w}x${h}: o marcador muda com a confirmação aberta: recarrega e avisa que o saque não foi enviado`, async () => {
    await comSaque({ width: w, height: h }, async (t) => {
      await pedirSaque(t.page, CHAVE);
      await t.page.evaluate(() => { document.cookie = "pb_conta=2; path=/"; });
      await recarregou(t);
      await alerta(t.page).waitFor();
      assert.equal(await alerta(t.page).innerText(), ANTES);
      await dentro(t.page, alerta(t.page), w, h);
      assert.equal(t.s.posts.length, 0);
      assert.equal(await t.page.evaluate((m) => sessionStorage.getItem(m), MARCA), null, "lida, a marca sai");
      const tudo = await t.page.evaluate(() => JSON.stringify([{ ...sessionStorage }, { ...localStorage }, document.cookie, location.href]));
      assert.ok(!tudo.includes(CHAVE), tudo);
    });
  });
}

test("P2: o marcador some com a confirmação aberta: o mesmo aviso", async () => {
  await comSaque({}, async (t) => {
    await pedirSaque(t.page, CHAVE);
    await t.page.evaluate(() => { document.cookie = "pb_conta=; path=/; max-age=0"; });
    await recarregou(t);
    await alerta(t.page).waitFor();
    assert.equal(await alerta(t.page).innerText(), ANTES);
    assert.equal(t.s.posts.length, 0);
  });
});

test("P3: o marcador muda com o POST em voo: avisa que o resultado não pôde ser confirmado", async () => {
  await comSaque({}, async (t) => {
    let solta;
    const preso = new Promise((r) => { solta = r; });
    t.s.post = async () => { await preso; };
    await pedirSaque(t.page, CHAVE);
    await t.page.waitForTimeout(450);
    await confirmar(t.page).click();
    await t.page.getByRole("button", { name: "Solicitando…" }).waitFor();
    await t.page.evaluate(() => { document.cookie = "pb_conta=2; path=/"; });
    solta();
    await recarregou(t);
    await alerta(t.page).waitFor();
    assert.equal(await alerta(t.page).innerText(), DURANTE);
    assert.equal(t.s.posts.length, 1);
  });
});

// ── positivos: sem troca de conta, nada de aviso ──
test("P4: cancelar e recarregar não avisa; o saque que deu certo não deixa marca", async () => {
  await comSaque({}, async (t) => {
    await pedirSaque(t.page, CHAVE);
    await t.page.waitForTimeout(450);
    await t.page.getByRole("dialog").getByRole("button", { name: "Cancelar" }).click();
    assert.equal(await t.page.evaluate((m) => sessionStorage.getItem(m), MARCA), null);
    await t.page.reload();
    await t.page.locator("#afil-pix").waitFor();
    await t.page.waitForTimeout(300);
    assert.equal(await alerta(t.page).count(), 0);
    await pedirSaque(t.page, CHAVE);
    await t.page.waitForTimeout(450);
    await confirmar(t.page).click();
    await t.page.getByText("Saque solicitado.").waitFor();
    assert.equal(await t.page.evaluate((m) => sessionStorage.getItem(m), MARCA), null);
  });
});

test("P5: a marca de mais de 60 s não avisa (e sai)", async () => {
  await comSaque({}, async (t) => {
    await t.page.evaluate((m) => sessionStorage.setItem(m, JSON.stringify({ etapa: "confirmando", em: Date.now() - 120000 })), MARCA);
    await t.page.reload();
    await t.page.locator("#afil-pix").waitFor();
    await t.page.waitForTimeout(300);
    assert.equal(await alerta(t.page).count(), 0);
    assert.equal(await t.page.evaluate((m) => sessionStorage.getItem(m), MARCA), null);
  });
});
