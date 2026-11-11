/**
 * #1411 no /app (frontend/dashboard.js, `#generic-confirm-overlay`): o 2º clique do duplo clique (ou duplo toque) no "Sacar" caía no
 * "Confirmar saque" da confirmação que o 1º clique tinha acabado de abrir, ou no fundo, que a fechava. Na janela de
 * `JANELA_DO_DUPLO_CLIQUE` os botões não recebem ponteiro e o fundo não fecha; o Enter segurado (repetição) não confirma.
 * Controle negativo: tirar o `pointerEvents = "none"` de `_genericModalAbrir` deixa vermelhos os "alinhado" de 390 e 320 e os toques;
 * tirar a conta de tempo do clique no fundo deixa vermelhos o "sem alinhar" de 1280 e o alerta; tirar o `e.repeat` deixa vermelho o Enter segurado;
 * tirar a janela do teclado (`cedo`) deixa vermelhos o Enter-Enter e o Espaço-Espaço; tirar o `clearTimeout(_genericModalTrava)` deixa vermelho
 * o "fechado por Esc"; tirar o `preventDefault` do mousedown na janela deixa vermelhos os seis duplos cliques com chave (o foco cai no body);
 * torná-lo incondicional deixa vermelho o "arrastar seleciona o texto do saque".
 * O `alinhar` 150 px aquém deixa vermelhos os três "alinhado" e os toques (o `SOB`); antes do `SOB` eles passavam sem tocar o OK.
 * O mesmo defeito no frontend/modals.js: confirma_duplo_clique_settings.test.mjs.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { chromium } from "playwright";
import { startServer } from "./_server.mjs";
import { CHAVE, abrirApp, ate, naArea } from "./_afiliado.mjs";
import { dentroDaJanela, valeram } from "./_janela.mjs";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const TAMANHOS = [[1280, 800], [390, 844], [320, 568]];
const ABERTO = "#generic-confirm-overlay.open";
const JANELA = 450; // acima da constante (400 ms)

/** Na área do afiliado com a chave digitada, anotando os POSTs do saque. */
async function naTela(opts) {
  const a = await abrirApp(browser, ORIGIN, opts);
  const posts = [];
  await a.ctx.route("**/api/affiliate/payout", (r) => { posts.push(r.request().postData()); return r.fulfill({ json: { ok: true, payout: { id: 9 } } }); });
  await naArea(a.page);
  // O dashboard.css rola suave: com a animação, o `alinhar` e o `sobOSacar` mediam a tela no meio da rolagem.
  await a.page.addStyleTag({ content: "html { scroll-behavior: auto !important; }" });
  await a.page.locator("#affiliate-pix-input").fill(CHAVE);
  return { ...a, posts };
}

/** O centro do Sacar e o que fica sob ele com a confirmação aberta. Abre e fecha a confirmação de verdade (sem ponteiro) antes,
 *  para medir com o texto real do saque. */
async function sobOSacar(page) {
  await page.evaluate(() => { document.getElementById("affiliate-payout-btn").click(); document.getElementById("generic-confirm-cancel").click(); });
  const b = await page.locator("#affiliate-payout-btn").boundingBox();
  const x = b.x + b.width / 2, y = b.y + b.height / 2;
  const sob = await page.evaluate(([px, py]) => {
    const ov = document.getElementById("generic-confirm-overlay");
    ov.classList.add("open");
    ov.querySelector(".modal-acts").style.pointerEvents = ""; // a geometria, não a janela
    const el = document.elementFromPoint(px, py);
    ov.classList.remove("open");
    return el?.id || el?.className || el?.tagName;
  }, [x, y]);
  return { x, y, sob };
}

/** Rola a página para o centro do Sacar ficar na altura do centro do "Confirmar saque" (a página pode não rolar o bastante). */
async function alinhar(page) {
  const okY = await page.evaluate(() => {
    document.getElementById("affiliate-payout-btn").click();
    const r = document.getElementById("generic-confirm-ok").getBoundingClientRect();
    document.getElementById("generic-confirm-cancel").click();
    return (r.top + r.bottom) / 2;
  });
  const b0 = await page.locator("#affiliate-payout-btn").boundingBox();
  await page.evaluate((dy) => window.scrollBy(0, dy), b0.y + b0.height / 2 - okY);
  return sobOSacar(page);
}

// O que fica sob o centro do Sacar com a confirmação aberta: o OK, o Cancelar e o fundo são os caminhos do defeito. Em 1280 a página
// não rola o bastante (cai na caixa) e em 320 o Sacar, mais à esquerda, cai no Cancelar. Se a medida mudar, o caso não mede mais o que diz.
const SOB = { 1280: ["modal", "generic-confirm-overlay"], 390: ["generic-confirm-ok", "generic-confirm-overlay"], 320: ["generic-confirm-cancel", "generic-confirm-body"] };
for (const [w, h] of TAMANHOS) {
  test(`${w}x${h}: duplo clique no Sacar alinhado ao "Confirmar saque" deixa a confirmação aberta, sem POST`, async (t) => {
    const { ctx, page, posts } = await naTela({ width: w, height: h });
    try {
      const { x, y, sob } = await alinhar(page);
      assert.equal(sob, SOB[w][0], "o que fica sob o Sacar alinhado");
      if (!await dentroDaJanela(t, page, "click", "duplo clique", async () => {
        await page.mouse.dblclick(x, y);
        await page.waitForTimeout(500);
      })) return;
      assert.deepEqual(posts, [], `sob o Sacar: ${sob}`);
      assert.equal(await page.locator(ABERTO).count(), 1, `sob o Sacar: ${sob}`);
      assert.ok(await page.evaluate(() => !!document.activeElement?.closest("#generic-confirm-overlay")), "o foco fica no diálogo");
    } finally { await ctx.close(); }
  });

  test(`${w}x${h}: duplo clique no Sacar sem alinhar deixa a confirmação aberta, sem POST`, async (t) => {
    const { ctx, page, posts } = await naTela({ width: w, height: h });
    try {
      await page.locator("#affiliate-payout-btn").scrollIntoViewIfNeeded();
      const { x, y, sob } = await sobOSacar(page);
      assert.equal(sob, SOB[w][1], "o que fica sob o Sacar");
      if (!await dentroDaJanela(t, page, "click", "duplo clique", async () => {
        await page.mouse.dblclick(x, y);
        await page.waitForTimeout(500);
      })) return;
      assert.deepEqual(posts, [], `sob o Sacar: ${sob}`);
      assert.equal(await page.locator(ABERTO).count(), 1);
      assert.ok(await page.evaluate(() => !!document.activeElement?.closest("#generic-confirm-overlay")), "o foco fica no diálogo");
    } finally { await ctx.close(); }
  });

  test(`${w}x${h}: duplo clique no Sacar sem chave deixa o alerta aberto`, async (t) => {
    const { ctx, page } = await naTela({ width: w, height: h });
    try {
      await page.locator("#affiliate-pix-input").fill("");
      if (!await dentroDaJanela(t, page, "click", "duplo clique", async () => {
        await page.locator("#affiliate-payout-btn").dblclick();
        await page.waitForTimeout(500);
      })) return;
      assert.equal(await page.locator(ABERTO).count(), 1);
      assert.match(await page.locator("#generic-confirm-body").innerText(), /Informe sua chave Pix/);
    } finally { await ctx.close(); }
  });
}

for (const ms of [0, 120, 250]) {
  test(`390x844 toque: dois toques no Sacar com ${ms} ms deixam a confirmação aberta, sem POST`, async (t) => {
    const { ctx, page, posts } = await naTela({ width: 390, height: 844, contexto: { isMobile: true, hasTouch: true, deviceScaleFactor: 3 } });
    try {
      const { x, y, sob } = await alinhar(page);
      assert.equal(sob, "generic-confirm-ok", "o 2º toque cai no OK");
      // o clique é o que a trava do ponteiro (o `setTimeout` da janela) decide
      if (!await dentroDaJanela(t, page, "click", "toque", async () => {
        await page.touchscreen.tap(x, y);
        if (ms) await page.waitForTimeout(ms);
        await page.touchscreen.tap(x, y);
        await page.waitForTimeout(500);
      })) return;
      assert.deepEqual(posts, []);
      assert.equal(await page.locator(ABERTO).count(), 1);
    } finally { await ctx.close(); }
  });
}

// ── positivos: o conserto não virou recusa ──
for (const [w, h] of TAMANHOS) {
  test(`${w}x${h}: passada a janela, o "Confirmar saque" manda 1 POST com {pix_key}`, async () => {
    const { ctx, page, posts } = await naTela({ width: w, height: h });
    try {
      await page.locator("#affiliate-payout-btn").click();
      await page.waitForTimeout(JANELA);
      await page.locator("#generic-confirm-ok").click();
      await page.waitForFunction(() => !document.querySelector("#generic-confirm-overlay.open"));
      await ate(() => posts.length > 0); // o validate e o POST, sem prazo fixo
      await page.waitForTimeout(200); // e nenhum 2º
      assert.deepEqual(posts, [JSON.stringify({ pix_key: CHAVE })]);
    } finally { await ctx.close(); }
  });
}

test("Esc dentro da janela fecha, sem POST", async () => {
  const { ctx, page, posts } = await naTela({});
  try {
    await page.locator("#affiliate-payout-btn").click();
    await page.keyboard.press("Escape");
    await page.waitForTimeout(200);
    assert.equal(await page.locator(ABERTO).count(), 0);
    assert.deepEqual(posts, []);
  } finally { await ctx.close(); }
});

test("Cancelar passada a janela fecha, mantém a chave e libera o Sacar", async () => {
  const { ctx, page, posts } = await naTela({});
  try {
    await page.locator("#affiliate-payout-btn").click();
    await page.waitForTimeout(JANELA);
    await page.locator("#generic-confirm-cancel").click();
    await page.waitForTimeout(200);
    assert.equal(await page.locator(ABERTO).count(), 0);
    assert.deepEqual(posts, []);
    assert.equal(await page.locator("#affiliate-pix-input").inputValue(), CHAVE);
    assert.equal(await page.locator("#affiliate-payout-btn").isEnabled(), true);
  } finally { await ctx.close(); }
});

test("Enter no 'Confirmar saque' passada a janela confirma", async () => {
  const { ctx, page, posts } = await naTela({});
  try {
    await page.locator("#affiliate-payout-btn").click();
    assert.equal(await page.evaluate(() => document.activeElement?.id), "generic-confirm-ok");
    await page.waitForTimeout(JANELA);
    await page.keyboard.press("Enter");
    await ate(() => posts.length > 0);
    await page.waitForTimeout(200);
    assert.deepEqual(posts, [JSON.stringify({ pix_key: CHAVE })]);
  } finally { await ctx.close(); }
});

// O mousedown só é bloqueado na janela: depois dela, dá para selecionar e copiar o texto da confirmação, onde está a chave Pix.
test("passada a janela, arrastar seleciona o texto do saque", async () => {
  const { ctx, page } = await naTela({});
  try {
    await page.locator("#affiliate-payout-btn").click();
    await page.waitForTimeout(JANELA);
    const b = await page.locator("#generic-confirm-body").boundingBox();
    await page.mouse.move(b.x + 2, b.y + 5);
    await page.mouse.down();
    await page.mouse.move(b.x + b.width - 5, b.y + 5, { steps: 5 });
    await page.mouse.up();
    assert.match(await page.evaluate(() => String(getSelection())), /^Saque de R\$/);
  } finally { await ctx.close(); }
});

// O OK nasce focado: o 2º aperto de um Enter-Enter (ou Espaço-Espaço) rápido no Sacar o confirmaria sem leitura.
for (const tecla of ["Enter", "Space"]) for (const ms of [0, 120]) {
  test(`${tecla}, ${tecla} (${ms} ms) no Sacar abre a confirmação e o 2º aperto não confirma`, async (t) => {
    const { ctx, page, posts } = await naTela({});
    try {
      await page.locator("#affiliate-payout-btn").focus();
      if (!await dentroDaJanela(t, page, "keydown", tecla, async () => {
        await page.keyboard.press(tecla);
        if (ms) await page.waitForTimeout(ms);
        await page.keyboard.press(tecla);
        await page.waitForTimeout(400);
      })) return;
      assert.equal(await page.locator(ABERTO).count(), 1);
      assert.deepEqual(posts, []);
    } finally { await ctx.close(); }
  });
}

test("o duplo clique, o toque, o Enter e o Espaço tiveram cada um um caso dentro da janela", () => assert.deepEqual(Object.keys(valeram).sort(), ["Enter", "Space", "duplo clique", "toque"]));

// A trava de um modal fechado (Esc) antes da janela não pode soltar os botões do seguinte antes da janela DELE: o OK do 2º, aos 250 ms
// dele, já passou dos 400 ms do 1º.
test("modal fechado por Esc dentro da janela e outro aberto: o OK do novo não recebe clique antes da janela dele", async (t) => {
  const { ctx, page } = await abrirApp(browser, ORIGIN, {});
  try {
    const { x, y } = await page.evaluate(async () => {
      const dorme = (ms) => new Promise((ok) => setTimeout(ok, ms));
      confirmModal("Primeiro");
      await dorme(100);
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      await dorme(150);
      window.__res = "pendente";
      window.__abriu = performance.now();
      addEventListener("click", () => { window.__clicou ??= performance.now(); }, true);
      confirmModal("Segundo").then((v) => { window.__res = v; });
      await dorme(250);
      const b = document.getElementById("generic-confirm-ok").getBoundingClientRect();
      return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
    });
    await page.mouse.click(x, y);
    await page.waitForTimeout(50);
    const ms = await page.evaluate(() => window.__clicou - window.__abriu); // o clique tem de cair na janela do 2º, medida na página
    if (ms >= 400) { t.skip(`o clique chegou ${Math.round(ms)} ms depois do 2º modal, fora da janela dele`); return; }
    assert.equal(await page.evaluate(() => window.__res), "pendente");
    await page.waitForTimeout(JANELA);
    await page.mouse.click(x, y);
    await page.waitForTimeout(50);
    assert.equal(await page.evaluate(() => window.__res), true, "passada a janela, o OK confirma");
  } finally { await ctx.close(); }
});

// A repetição do SO começa depois da janela (≈ 500 ms no macOS e no Windows): só o `e.repeat` a segura.
test("Enter segurado no Sacar abre a confirmação e a repetição, passada a janela, não confirma", async () => {
  const { ctx, page, posts } = await naTela({});
  try {
    await page.locator("#affiliate-payout-btn").focus();
    await page.keyboard.down("Enter");
    await page.locator(ABERTO).waitFor();
    await page.waitForTimeout(JANELA);
    await page.keyboard.down("Enter"); // já apertada: sai com `repeat: true`
    await page.keyboard.down("Enter");
    await page.keyboard.up("Enter");
    await page.waitForTimeout(300);
    assert.equal(await page.locator(ABERTO).count(), 1);
    assert.deepEqual(posts, []);
  } finally { await ctx.close(); }
});

// §0.7: a janela tem três cópias (o JS clássico não importa o TS do /painel).
test("a janela é a mesma no /app, no modals.js e no /painel", () => {
  const ler = (f) => Number(readFileSync(new URL(`../../${f}`, import.meta.url), "utf8").match(/JANELA_DO_DUPLO_CLIQUE = (\d+)/)?.[1]);
  const v = ["frontend/dashboard.js", "frontend/modals.js", "webapp/src/dashboard/parts/dialog.ts"].map(ler);
  assert.deepEqual(v, [400, 400, 400]);
});

// ── o rótulo: o confirmModal do /app lia só `okText`/`danger`, e 6 chamadores passam os nomes do modals.js (`confirmText`/`destructive`) ──
// Controle negativo: voltar a `opts.okText || "OK"` e `!!opts.danger` deixa vermelho o primeiro (o botão sai "OK", verde).
test("o botão do confirmModal do /app segue o que os chamadores reais pedem", async () => {
  const { ctx, page } = await abrirApp(browser, ORIGIN, {});
  try {
    const botao = () => page.evaluate(() => { const b = document.getElementById("generic-confirm-ok"); const r = [b.textContent, b.className]; document.getElementById("generic-confirm-cancel").click(); return r; });
    await page.evaluate(() => { deleteInvestment("Tesouro"); }); // confirmText + destructive
    assert.deepEqual(await botao(), ["Excluir", "inst-delete-btn"]);
    await page.evaluate(() => { _catEditState = { id: 7, is_system: false, original_name: "Mercado" }; categoryDeleteFromModal(); }); // okText + danger
    assert.deepEqual(await botao(), ["Excluir", "inst-delete-btn"]);
    await page.evaluate(() => { // confirmText + destructive: false (antecipar fatura futura)
      payBillState = { balance: 0, selectedId: 1, submitting: false, bills: [{ id: 1, due_amount: 100, period_end: "2099-12-31", card_name: "Nubank", label: "dez" }] };
      document.getElementById("pay-bill-amount").value = "10";
      submitPayBill();
    });
    assert.deepEqual(await botao(), ["Sim, pagar", "btn-save"]);
  } finally { await ctx.close(); }
});
