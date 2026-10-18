/**
 * Bordas do "segurar para confirmar" de settings.html (par de settings_segurar_confirma):
 * toque real arrastado para fora, script que não carrega, botão que não é o primário,
 * aviso aria-live, bloco que some no meio do hold, senha apagada até o disparo e
 * Enter/Espaço ainda apertado depois do disparo.
 *
 * Mesma regra do par: relógio falso (`page.clock`), nada de sleep real para medir 1200 ms.
 *
 * CONTROLES DO GRUPO (§3 do CLAUDE.md) — cada um aplicado à mão e medido na sessão:
 *   toque fora      sem o release no e.target (ou com o `botao.hasPointerCapture` antigo): VERMELHO
 *   script ausente  sem a guarda `window.PBHoldConfirm` no ligarHold: VERMELHO (pageerror, página inerte)
 *   botão direito/meio/2º ponteiro  sem `e.button !== 0` / sem `isPrimary`: VERMELHO
 *   click em holding  sem o `m.ocioso()` no click: VERMELHO
 *   aviso e nome    aria-live de volta DENTRO do botão: VERMELHO (o nome ganha "Cancelado")
 *   bloco que some  sem `holdX.resetar()` no hide* / sem o teste de visibilidade no concluir: VERMELHO
 *   senha no disparo  sem `senhaPreenchida` no requestAccount*: VERMELHO
 *   repeat do teclado  sem o `engolirRepeat`: VERMELHO (o alerta fecha em < 300 ms)
 *   positivos: o hold normal (toque parado 1200 ms, Enter) e o fallback por clique continuam
 *   disparando — nenhum conserto virou recusa geral.
 *
 * Rodar:  node --test tests/frontend/settings_segurar_confirma_bordas.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import {
  sobe, derruba, ACOES, abre, estado, modalAberto, confirmAberto, centro, aperta,
  assenta, waitFor,
} from "./_hold_harness.mjs";

before(sobe);
after(derruba);

const MOBILE = { width: 375, height: 700 };
const toast = (page) => page.evaluate(() => document.getElementById("toast").textContent);
const aviso = (page, a) => page.evaluate((id) => document.getElementById(id.replace("confirm-btn", "hold-aviso")).textContent, a.botao);
const nomeOk = (page, nome) => page.getByRole("button", { name: nome, exact: true }).count();
const toque = (cdp, type, pts) => cdp.send("Input.dispatchTouchEvent", { type, touchPoints: pts });

for (const a of ACOES) {
  test(`${a.nome}: toque real parado 1200 ms dispara 1 requisição; arrastado 72 px para fora cancela (0 requisições)`, async () => {
    for (const arrasta of [false, true]) {
      const { page, ctx, reqs } = await abre(a, { touch: true, viewport: MOBILE });
      try {
        const cdp = await ctx.newCDPSession(page);
        const { x, y } = await centro(page, a);
        await toque(cdp, "touchStart", [{ x, y, id: 1 }]);
        assert.equal(await estado(page, a), "holding");
        if (arrasta) {
          for (let i = 1; i <= 6; i++) await toque(cdp, "touchMove", [{ x, y: y - i * 12, id: 1 }]);
          await waitFor(async () => (await estado(page, a)) === "idle", "o dedo fora do botão tem de abortar");
        }
        await page.clock.runFor(1300);
        await assenta();
        assert.equal(reqs.length, arrasta ? 0 : 1);
        await toque(cdp, "touchEnd", []);
      } finally { await ctx.close(); }
    }
  });

  test(`${a.nome}: hold-confirm.js que não carrega — sem pageerror, página de pé, o clique abre o confirmModal`, async () => {
    const { page, ctx, reqs, errs } = await abre(a, { semHold: true });   // o abre espera a seção de dados visível
    try {
      assert.deepEqual(errs, []);
      assert.equal(await page.evaluate(() => typeof window.PBHoldConfirm), "undefined");
      await page.click(`#${a.botao}`);
      assert.equal(await confirmAberto(page), true);
      await page.clock.runFor(100);
      assert.equal(reqs.length, 0, "antes do 'sim' nada vai");
      await page.locator(".pig-modal-overlay").getByRole("button", { name: a.modal }).click();
      await waitFor(() => reqs.length === 1, "a requisição depois do 'sim'");
    } finally { await ctx.close(); }
  });

  test(`${a.nome}: botão direito, botão do meio e ponteiro não primário não armam o hold`, async () => {
    const { page, ctx, reqs } = await abre(a);
    try {
      const { x, y } = await centro(page, a);
      await page.mouse.move(x, y);
      for (const button of ["right", "middle"]) {
        await page.mouse.down({ button });
        assert.equal(await estado(page, a), "idle", `${button} não arma`);
        await page.mouse.up({ button });
      }
      await page.evaluate((id) => document.getElementById(id).dispatchEvent(
        new PointerEvent("pointerdown", { bubbles: true, button: 0, isPrimary: false, pointerId: 7 })), a.botao);
      assert.equal(await estado(page, a), "idle", "2º ponteiro não arma");
      await page.clock.runFor(1500);
      await assenta();
      assert.equal(reqs.length, 0);
    } finally { await ctx.close(); }
  });

  test(`${a.nome}: click sem gesto com o botão em holding não abre modal nem dispara`, async () => {
    const { page, ctx, reqs } = await abre(a);
    try {
      await aperta(page, a);
      await page.clock.runFor(600);
      await page.evaluate((id) => document.getElementById(id).click(), a.botao);   // detail 0
      assert.equal(await modalAberto(page), false);
      assert.equal(await estado(page, a), "holding");
      await page.mouse.up();
      assert.equal(reqs.length, 0);
    } finally { await ctx.close(); }
  });

  test(`${a.nome}: blur da JANELA (botão sem foco, como no Safari) aos 600 ms cancela o hold — 0 requisições`, async () => {
    const { page, ctx, reqs } = await abre(a);
    try {
      // Safari não foca o botão no clique de mouse: o mousedown sem foco imita isso.
      await page.evaluate((id) => document.getElementById(id).addEventListener("mousedown", (e) => e.preventDefault()), a.botao);
      await aperta(page, a);
      assert.equal(await page.evaluate((id) => document.activeElement.id === id, a.botao), false, "o botão não pode ter foco");
      await page.clock.runFor(600);
      assert.equal(await estado(page, a), "holding");
      await page.evaluate(() => window.dispatchEvent(new Event("blur")));   // Cmd-Tab: só a janela perde o foco
      assert.equal(await estado(page, a), "idle", "o blur da janela tinha que cancelar");
      await page.clock.runFor(1000);
      await assenta();
      assert.equal(reqs.length, 0);
      await page.mouse.up();
    } finally { await ctx.close(); }
  });

  test(`${a.nome}: aviso aria-live fora do botão — o nome do botão é só o rótulo, e o aviso diz Cancelado/Confirmado`, async () => {
    const preso = new Promise(() => {});   // o fetch nunca volta: o botão fica em done
    const { page, ctx, reqs } = await abre(a, { resposta: () => preso });
    try {
      assert.equal(await nomeOk(page, a.rotulo), 1, "idle");
      await aperta(page, a);
      await page.clock.runFor(600);
      await page.mouse.up();
      assert.equal(await aviso(page, a), "Cancelado");
      assert.equal(await nomeOk(page, a.rotulo), 1, "depois de soltar cedo");
      await aperta(page, a);
      await page.clock.runFor(1200);
      await waitFor(() => reqs.length === 1, "a requisição do hold");
      assert.equal(await aviso(page, a), "Confirmado");
      assert.equal(await estado(page, a), "done");
      assert.equal(await nomeOk(page, a.nome === "reset" ? "Apagando..." : "Excluindo..."), 1, "em done");
    } finally { await ctx.close(); }
  });

  test(`${a.nome}: senha apagada entre o aperto e o disparo — 0 requisições e o botão volta a idle`, async () => {
    const { page, ctx, reqs } = await abre(a);
    try {
      await aperta(page, a);
      await page.clock.runFor(600);
      await page.evaluate((id) => { document.getElementById(id).value = ""; }, a.senha);
      await page.clock.runFor(700);
      await assenta();
      assert.equal(reqs.length, 0);
      assert.equal(await estado(page, a), "idle");
      assert.equal(await toast(page), "Confirme sua senha");
    } finally { await ctx.close(); }
  });

  const SUMIR = {
    "Cancelar (hide*) no meio": (page) => page.evaluate((f) => window[f](), a.fechar),
    "seção de dados escondida (senha mantida)": (page) => page.evaluate(() => { document.getElementById("section-data").hidden = true; }),
    "bloco sem .show (senha mantida)": (page) => page.evaluate((id) => document.getElementById(id).classList.remove("show"), a.bloco),
  };
  for (const [nome, some] of Object.entries(SUMIR)) {
    test(`${a.nome}: ${nome} durante o hold — 0 requisições, sem toast de senha`, async () => {
      const { page, ctx, reqs } = await abre(a);
      try {
        await aperta(page, a);
        await page.clock.runFor(600);
        await some(page);
        if (nome.startsWith("Cancelar")) assert.equal(await estado(page, a), "idle", "o hide* zera o hold na hora");
        await page.clock.runFor(700);
        await assenta();
        assert.equal(reqs.length, 0);
        assert.equal(await toast(page), "", "nada de toast com o bloco fechado");
        assert.equal(await estado(page, a), "idle");
      } finally { await ctx.close(); }
    });
  }
}

test("delete: Enter ainda apertado depois do disparo — o repeat não fecha o alerta 'Conta excluída'; Enter novo fecha", async () => {
  const a = ACOES[1];
  const { page, ctx } = await abre(a);
  try {
    await page.focus(`#${a.botao}`);
    await page.keyboard.down("Enter");
    await page.clock.runFor(1200);
    await waitFor(() => page.evaluate(() => /Conta excluída/.test(document.querySelector(".pig-modal-overlay")?.textContent || "")),
                  "alertModal pós-sucesso da exclusão");
    for (let i = 0; i < 3; i++) await page.keyboard.down("Enter");   // auto-repeat
    await assenta();
    assert.equal(await modalAberto(page), true, "o repeat não pode fechar o alerta");
    await page.keyboard.up("Enter");
    await page.keyboard.press("Enter");   // Enter novo, depois do keyup
    await waitFor(async () => { try { return !(await modalAberto(page)); } catch { return true; } },
                  "o Enter novo fecha o alerta");
  } finally { await ctx.close(); }
});
