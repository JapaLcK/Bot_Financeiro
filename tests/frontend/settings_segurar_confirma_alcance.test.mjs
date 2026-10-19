/**
 * Alcance do "segurar para confirmar" de settings.html (par de settings_segurar_confirma*):
 * o botão tem de estar ALCANÇÁVEL no disparo, o repeat da tecla só é engolido até o keyup/blur,
 * PBHoldConfirm sem `.ligar` cai no fallback e chamada sem argumento NÃO apaga sem confirmar.
 *
 * CONTROLES DO GRUPO (§3 do CLAUDE.md), medidos na sessão:
 *   alcançável  sem o `alcancavel()` no concluir (só isConnected): VERMELHO nos casos de cobertura
 *   engolirRepeat  fim() virando no-op: VERMELHO (o repeat depois do keyup/blur é engolido)
 *   PBHoldConfirm = {}  guarda antiga `!window.PBHoldConfirm`: VERMELHO (pageerror, página inerte)
 *   default seguro  `origem === "clique"` no lugar de `!== "segurar"`: VERMELHO (1 req sem modal)
 *   positivos: hold normal confirma 1 vez; o 'sim' do modal envia; keyup e blur ainda liberam o repeat.
 * Limite declarado: opacidade fracionária (0 < o < 1) não aborta; só opacity:0.
 *
 * Rodar:  node --test tests/frontend/settings_segurar_confirma_alcance.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import {
  sobe, derruba, ACOES, abre, estado, confirmAberto, centro, aperta, assenta, waitFor, json, sleep,
} from "./_hold_harness.mjs";

before(sobe);
after(derruba);

const MOBILE = { width: 375, height: 700 };
const toast = (page) => page.evaluate(() => document.getElementById("toast").textContent);
const toque = (cdp, type, pts) => cdp.send("Input.dispatchTouchEvent", { type, touchPoints: pts });

const COBERTURAS = {
  "visibility:hidden no bloco": (page, a) => page.evaluate((id) => { document.getElementById(id).style.visibility = "hidden"; }, a.bloco),
  "opacity:0 no bloco": (page, a) => page.evaluate((id) => { document.getElementById(id).style.opacity = "0"; }, a.bloco),
  "pointer-events:none no botão": (page, a) => page.evaluate((id) => { document.getElementById(id).style.pointerEvents = "none"; }, a.botao),
  "overlay fixo cobrindo a tela": (page) => page.evaluate(() => {
    const d = document.createElement("div");
    d.style.cssText = "position:fixed;inset:0;background:#000a;z-index:99999";
    document.body.appendChild(d);
  }),
  "confirmModal de outro fluxo": (page) => page.evaluate(() => { window.confirmModal("outra coisa", { title: "X" }); }),
};

for (const a of ACOES) {
  for (const [nome, cobre] of Object.entries(COBERTURAS)) {
    for (const modo of ["mouse", "toque"]) {
      test(`${a.nome}: ${nome} (${modo}) no meio do hold — 0 requisições, idle, sem toast`, async () => {
        const { page, ctx, reqs } = await abre(a, { touch: modo === "toque", viewport: MOBILE });
        try {
          let solta;
          if (modo === "mouse") { await aperta(page, a); solta = () => page.mouse.up(); }
          else {
            const cdp = await ctx.newCDPSession(page);
            const { x, y } = await centro(page, a);
            await toque(cdp, "touchStart", [{ x, y, id: 1 }]);
            solta = () => toque(cdp, "touchEnd", []);
          }
          await page.clock.runFor(500);
          await cobre(page, a);
          await page.clock.runFor(800);
          await assenta();
          assert.equal(reqs.length, 0);
          assert.equal(await estado(page, a), "idle");
          assert.equal(await toast(page), "");
          await solta();
        } finally { await ctx.close(); }
      });
    }
  }

  for (const viewport of [{ width: 1280, height: 800 }, MOBILE, { width: 844, height: 390 }]) {
    test(`${a.nome}: positivo — hold normal em ${viewport.width}x${viewport.height} confirma 1 vez`, async () => {
      const { page, ctx, reqs } = await abre(a, { viewport });
      try {
        await aperta(page, a);
        await page.clock.runFor(1200);
        await waitFor(() => reqs.length === 1, "a requisição do hold");
        await assenta();
        assert.equal(reqs.length, 1);
      } finally { await ctx.close(); }
    });
  }

  test(`${a.nome}: hold-confirm.js com PBHoldConfirm = {} (sem .ligar) — página de pé, o clique abre o confirmModal`, async () => {
    const { page, ctx, reqs, errs } = await abre(a, { holdStub: "window.PBHoldConfirm = {};" });
    try {
      assert.deepEqual(errs, []);
      await page.click(`#${a.botao}`);
      assert.equal(await confirmAberto(page), true);
      await page.clock.runFor(100);   // anima o modal; o relógio está pausado
      await assenta();
      assert.equal(reqs.length, 0, "antes do 'sim' nada vai");
      await page.locator(".pig-modal-overlay").getByRole("button", { name: a.modal }).click();
      await waitFor(() => reqs.length === 1, "a requisição depois do 'sim'");
    } finally { await ctx.close(); }
  });

  test(`${a.nome}: chamada sem argumento abre o confirmModal e dá 0 requisições até o 'sim'`, async () => {
    const { page, ctx, reqs } = await abre(a);
    try {
      await page.evaluate((f) => { window[f](); }, a.nome === "reset" ? "requestAccountReset" : "requestAccountDeletion");
      await waitFor(() => confirmAberto(page), "o confirmModal");
      await page.clock.runFor(100);   // anima o modal; o relógio está pausado
      await assenta();
      assert.equal(reqs.length, 0);
      await page.locator(".pig-modal-overlay").getByRole("button", { name: a.modal }).click();
      await waitFor(() => reqs.length === 1, "a requisição depois do 'sim'");
    } finally { await ctx.close(); }
  });
}

// engolirRepeat: disparo por teclado com a tecla ainda apertada. Um keydown repeat sintético
// da mesma tecla, em `body`, só pode ser engolido ENQUANTO a tecla segue apertada.
const TECLAS = [["Enter", "Enter"], ["Space", " "]];
const instala = (page) => page.evaluate(() => {
  window.__vistos = [];
  window.addEventListener("keydown", (e) => window.__vistos.push([e.key, e.repeat]));
});
/** true = o repeat chegou à bolha em window (não foi engolido). */
const chega = async (page, key) => {
  await page.evaluate(() => { window.__vistos.length = 0; });
  await page.evaluate((k) => document.body.dispatchEvent(
    new KeyboardEvent("keydown", { key: k, repeat: true, bubbles: true, cancelable: true })), key);
  return (await page.evaluate(() => window.__vistos.length)) === 1;
};

for (const [tecla, key] of TECLAS) {
  for (const fim of ["keyup", "blur"]) {
    test(`delete: ${tecla} — o repeat é engolido com a tecla apertada e NÃO depois do ${fim}`, async () => {
      const a = ACOES[1];
      const { page, ctx, reqs } = await abre(a, { resposta: () => json({ detail: "senha" }, 400) });
      try {
        await instala(page);
        await page.focus(`#${a.botao}`);
        await page.keyboard.down(tecla);
        await page.clock.runFor(1200);
        await waitFor(() => reqs.length === 1, "a requisição do hold");
        await sleep(200);
        assert.equal(await chega(page, key), false, "com a tecla apertada o repeat é engolido");
        if (fim === "keyup") await page.keyboard.up(tecla);
        else await page.evaluate(() => window.dispatchEvent(new Event("blur")));
        assert.equal(await chega(page, key), true, `depois do ${fim} o repeat passa`);
        if (fim === "blur") await page.keyboard.up(tecla);
      } finally { await ctx.close(); }
    });
  }
}
