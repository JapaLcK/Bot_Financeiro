/**
 * #1411 no /painel (conta/Confirma.tsx): o 2º clique do duplo clique (ou duplo toque) no "Sacar" caía no "Confirmar saque" que o 1º
 * tinha acabado de abrir. Na janela de `JANELA_DO_DUPLO_CLIQUE` o rodapé do diálogo não recebe ponteiro: o clique atravessa e cai no
 * `.cofre`. Vale para todo usuário do `Confirma` (o "Encerrar" dos dispositivos conectados também).
 * Controle negativo: tirar o `style` do <footer> do Confirma deixa vermelhos os "alinhado" (320 cai no Confirmar, 390 no Cancelar),
 * o toque, o sem <dialog> e o "Encerrar".
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { abrirAfiliado, confirmar, pedirSaque } from "./_afiliado.mjs";
import { abrirSeguranca } from "./_conta_seguranca.mjs";

const browser = navegador();
const CHAVE = "ana@pix.com";
const JANELA = 450; // acima da constante (400 ms)
const sacar = (page) => page.getByRole("button", { name: /^Sacar/ });
const dialogo = (page) => page.locator("dialog[open], .lanc-form-fb[open]");

/** Abre a confirmação pelo `gatilho`, mede o botão `nome`, cancela e rola a página para o centro do gatilho ficar na altura dele.
 *  Devolve o centro do gatilho e o que fica sob ele com a confirmação aberta (o rótulo do botão ou a classe). */
async function alinhar(page, gatilho, nome) {
  await gatilho.click();
  const dlg = dialogo(page);
  const alvo = await dlg.getByRole("button", { name: nome, exact: true }).boundingBox();
  await dlg.getByRole("button", { name: "Cancelar" }).click();
  await dlg.waitFor({ state: "detached" });
  const b0 = await gatilho.boundingBox();
  await page.evaluate((dy) => window.scrollBy(0, dy), b0.y + b0.height / 2 - (alvo.y + alvo.height / 2));
  const b = await gatilho.boundingBox();
  return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
}
/** O que recebe o 2º clique: anotado em captura, antes do React. */
const anotar = (page) => page.evaluate(() => { window.__alvos = []; document.addEventListener("click", (e) => window.__alvos.push(e.target.textContent?.trim().slice(0, 20) || e.target.className), true); });
const alvos = (page) => page.evaluate(() => window.__alvos);

async function comSaque(opts, fn) {
  const t = await abrirAfiliado(browser(), opts);
  try {
    await t.page.locator("#afil-pix").fill(CHAVE);
    await fn(t);
    assert.deepEqual(t.erros, [], "pageerror");
  } finally { await t.ctx.close(); }
}

for (const [w, h] of [[1280, 800], [390, 844], [320, 568]]) {
  test(`${w}x${h}: duplo clique no Sacar alinhado ao "Confirmar saque" deixa o diálogo aberto, sem POST`, async () => {
    await comSaque({ width: w, height: h }, async (t) => {
      const { x, y } = await alinhar(t.page, sacar(t.page), "Confirmar saque");
      await anotar(t.page);
      await t.page.mouse.dblclick(x, y);
      await t.page.waitForTimeout(500);
      const onde = JSON.stringify(await alvos(t.page));
      assert.equal(t.s.posts.length, 0, onde);
      assert.equal(await dialogo(t.page).count(), 1, onde);
      assert.equal(await t.page.getByText("Saque solicitado").count(), 0);
    });
  });

  test(`${w}x${h}: duplo clique no Sacar sem alinhar deixa o diálogo aberto, sem POST`, async () => {
    await comSaque({ width: w, height: h }, async (t) => {
      await sacar(t.page).dblclick();
      await t.page.waitForTimeout(500);
      assert.equal(t.s.posts.length, 0);
      assert.equal(await dialogo(t.page).count(), 1);
    });
  });
}

test("320x568 toque: dois toques no Sacar (120 ms) alinhado deixam o diálogo aberto, sem POST", async () => {
  const t = await abrirAfiliado(browser(), { width: 320, height: 568, contexto: { isMobile: true, hasTouch: true } });
  try {
    await t.page.locator("#afil-pix").fill(CHAVE);
    const { x, y } = await alinhar(t.page, sacar(t.page), "Confirmar saque");
    await t.page.touchscreen.tap(x, y);
    await t.page.waitForTimeout(120);
    await t.page.touchscreen.tap(x, y);
    await t.page.waitForTimeout(500);
    assert.equal(t.s.posts.length, 0);
    assert.equal(await dialogo(t.page).count(), 1);
  } finally { await t.ctx.close(); }
});

// Safari < 15.4: sem showModal, o diálogo abre pelo atributo, com o scrim do `Confirma`.
test("320x568 sem <dialog>: duplo clique alinhado deixa o diálogo aberto, sem POST", async () => {
  await comSaque({ width: 320, height: 568, init: () => { delete HTMLDialogElement.prototype.showModal; } }, async (t) => {
    const { x, y } = await alinhar(t.page, sacar(t.page), "Confirmar saque");
    assert.equal(await t.page.locator(".lanc-scrim").count(), 0, "fechado, sem scrim");
    await t.page.mouse.dblclick(x, y);
    await t.page.waitForTimeout(500);
    assert.equal(t.s.posts.length, 0);
    assert.equal(await t.page.locator(".lanc-form-fb[open]").count(), 1);
  });
});

test("390x844: duplo clique no 'Encerrar' de um dispositivo alinhado ao 'Encerrar' do diálogo não encerra", async () => {
  const p = await abrirSeguranca({ width: 390, height: 844 });
  try {
    const gatilho = p.sess.locator("li").filter({ hasText: "Safari" }).getByRole("button", { name: "Encerrar", exact: true });
    await gatilho.waitFor();
    const { x, y } = await alinhar(p.page, gatilho, "Encerrar");
    await p.page.mouse.dblclick(x, y);
    await p.page.waitForTimeout(500);
    assert.deepEqual(p.s.dels, []);
    assert.equal(await p.dialogo.count(), 1);
  } finally { await p.ctx.close(); }
});

// ── positivos: o conserto não virou recusa ──
test("passada a janela, o 'Confirmar saque' manda 1 POST", async () => {
  await comSaque({}, async (t) => {
    await pedirSaque(t.page, CHAVE);
    await t.page.waitForTimeout(JANELA);
    await confirmar(t.page).click();
    await t.page.getByText("Saque solicitado.").waitFor();
    assert.deepEqual(t.s.posts.map((p) => p.corpo), [JSON.stringify({ pix_key: CHAVE })]);
  });
});

test("Esc dentro da janela fecha, sem POST; Cancelar passada a janela fecha, sem POST", async () => {
  await comSaque({}, async (t) => {
    await pedirSaque(t.page, CHAVE);
    await t.page.keyboard.press("Escape");
    await dialogo(t.page).waitFor({ state: "detached" });
    await pedirSaque(t.page, CHAVE);
    await t.page.waitForTimeout(JANELA);
    await dialogo(t.page).getByRole("button", { name: "Cancelar" }).click();
    await dialogo(t.page).waitFor({ state: "detached" });
    assert.equal(t.s.posts.length, 0);
    assert.equal(await t.page.locator("#afil-pix").inputValue(), CHAVE);
  });
});
