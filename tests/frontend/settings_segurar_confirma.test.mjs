/**
 * "Recomeçar do zero" e "Excluir conta" (frontend/settings.html) confirmam SEGURANDO
 * o botão do bloco inline por 1,2 s (frontend/hold-confirm.js). Soltar antes cancela
 * e nada dispara; clique rápido não faz nada; clique SEM gesto (event.detail === 0:
 * leitor de tela, switch control) cai no confirmModal de antes.
 *
 * O tempo é o `page.clock` do Playwright, instalado DEPOIS do boot (o timer do hold
 * nasce no aperto): nada de sleep real para medir 1200 ms. O sleep real que sobra é
 * só para "esperar o que NÃO deve acontecer" chegar à rede.
 *
 * CONTROLES DO GRUPO (§3 do CLAUDE.md), aplicados e medidos na sessão:
 *   negativo A — timer do hold com 0 ms (confirma no aperto): ficam vermelhos soltar
 *     em 1199 ms, o grupo dos seis cancelamentos, o hold por Enter e o hold por Espaço
 *     (nas duas ações). Contagem: rode o arquivo com o timer em 0 e leia `ℹ fail`.
 *   negativo B — o caso "tecla ainda apertada depois do erro" (Enter) só fica vermelho
 *     removendo JUNTOS o `e.preventDefault()` do keydown e a chamada de `engolirRepeat`
 *     (aí falham os dois do Enter, um por ação: o repeat em idle gera click com
 *     detail 0 e abre o modal). Removendo SÓ o preventDefault do keydown a suíte segue
 *     verde: o `engolirRepeat` engole o repeat antes de ele virar clique. No Chromium
 *     nenhum teste protege o preventDefault do keydown sozinho, nem o do keyup (Espaço:
 *     com o keydown já prevenido ele não clica de qualquer jeito). O "Enter rápido"
 *     também NÃO discrimina: o click do Enter chega com o estado em holding e é ignorado.
 *   positivo — "segurar 1200 ms dispara 1 requisição" e "erro 400 → novo hold dispara
 *     a 2ª": provam que o conserto não virou uma recusa geral.
 *
 * Rodar:  node --test tests/frontend/settings_segurar_confirma.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import {
  sobe, derruba, ACOES, abre, estado, modalAberto, confirmAberto, rotulo, centro, aperta,
  assenta, waitFor, json,
} from "./_hold_harness.mjs";

before(sobe);
after(derruba);

for (const a of ACOES) {
  test(`${a.nome}: segurar 1200 ms dispara exatamente 1 requisição, sem confirmModal`, async () => {
    const { page, ctx, reqs } = await abre(a);
    try {
      await aperta(page, a);
      assert.equal(await estado(page, a), "holding");
      await page.clock.runFor(1200);
      await waitFor(() => reqs.length === 1, "a requisição do hold");
      await assenta();
      assert.equal(reqs.length, 1);
      assert.equal(reqs[0].metodo, a.metodo);
      assert.deepEqual(reqs[0].corpo, a.corpo);
      assert.equal(await confirmAberto(page), false, "o caminho do hold não passa pelo confirmModal");
      if (a.nome === "delete") {   // fluxo de sempre: alertModal pós-sucesso
        await waitFor(() => page.evaluate(() => /Conta excluída/.test(document.querySelector(".pig-modal-overlay")?.textContent || "")),
                      "alertModal pós-sucesso da exclusão");
      }
    } finally { await ctx.close(); }
  });

  test(`${a.nome}: o anel enche na duração do hold (--hold-ms = 1200 ms, a mesma do timer)`, async () => {
    const { page, ctx } = await abre(a);
    try {
      const ms = await page.evaluate((id) => document.getElementById(id).style.getPropertyValue("--hold-ms"), a.botao);
      assert.equal(ms, "1200ms");
      assert.equal(await page.evaluate((id) => getComputedStyle(document.querySelector(`#${id} .hold-ring-fg`)).transitionDuration, a.botao), "0s",
                   "em idle não há transição: soltar zera na hora");
      await aperta(page, a);
      assert.equal(await page.evaluate((id) => getComputedStyle(document.querySelector(`#${id} .hold-ring-fg`)).transitionDuration, a.botao), "1.2s");
    } finally { await ctx.close(); }
  });

  test(`${a.nome}: soltar em 1199 ms cancela, 0 requisições, e o resto do tempo não dispara`, async () => {
    const { page, ctx, reqs } = await abre(a);
    try {
      await aperta(page, a);
      await page.clock.runFor(1199);
      await page.mouse.up();
      assert.equal(await estado(page, a), "idle");
      await page.clock.runFor(2000);
      await assenta();
      assert.equal(reqs.length, 0);
    } finally { await ctx.close(); }
  });

  test(`${a.nome}: clique rápido de mouse e toque não faz nada (0 requisições, nenhum modal)`, async () => {
    const { page, ctx, reqs } = await abre(a, { touch: true });
    try {
      const { x, y } = await centro(page, a);
      await page.mouse.click(x, y);
      await page.tap(`#${a.botao}`);
      await page.clock.runFor(2000);
      await assenta();
      assert.equal(reqs.length, 0);
      assert.equal(await modalAberto(page), false);
      assert.equal(await estado(page, a), "idle");
    } finally { await ctx.close(); }
  });

  for (const tecla of ["Enter", "Space"]) {
    test(`${a.nome}: teclado — segurar ${tecla} 1200 ms dispara 1 requisição; repeat no meio não reinicia`, async () => {
      const { page, ctx, reqs } = await abre(a);
      try {
        await page.focus(`#${a.botao}`);
        await page.keyboard.down(tecla);
        await page.clock.runFor(600);
        await page.keyboard.down(tecla);   // 2º keydown sem keyup = repeat
        assert.equal(await estado(page, a), "holding");
        await page.clock.runFor(600);      // 600 + 600 = 1200 desde o PRIMEIRO aperto
        await waitFor(() => reqs.length === 1, "a requisição do hold por teclado");
        await page.keyboard.up(tecla);
        await assenta();
        assert.equal(reqs.length, 1);
        assert.equal(await confirmAberto(page), false, "o caminho do hold não passa pelo confirmModal");
      } finally { await ctx.close(); }
    });

    test(`${a.nome}: teclado — ${tecla} rápido não faz nada (0 requisições, nenhum modal)`, async () => {
      const { page, ctx, reqs } = await abre(a);
      try {
        await page.focus(`#${a.botao}`);
        await page.keyboard.press(tecla);
        await page.clock.runFor(2000);
        await assenta();
        assert.equal(reqs.length, 0);
        assert.equal(await modalAberto(page), false, "o Enter/Espaço rápido não pode virar clique com detail 0");
        assert.equal(await estado(page, a), "idle");
      } finally { await ctx.close(); }
    });
  }

  for (const tecla of ["Enter", "Space"]) {
    test(`${a.nome}: teclado — ${tecla} ainda apertada depois do erro não vira clique (repeat/keyup caem em idle)`, async () => {
      const { page, ctx, reqs } = await abre(a, { resposta: () => json({ detail: "Senha incorreta" }, 400) });
      try {
        await page.focus(`#${a.botao}`);
        await page.keyboard.down(tecla);
        await page.clock.runFor(1200);
        await waitFor(() => reqs.length === 1, "a requisição do hold");
        await waitFor(async () => (await estado(page, a)) === "idle", "voltar a idle depois do erro");
        await page.focus(`#${a.botao}`);   // o disabled do envio tirou o foco do botão
        await page.keyboard.down(tecla);   // repeat com o botão já em idle
        await page.keyboard.up(tecla);     // o Espaço clicaria aqui
        await assenta();
        assert.equal(await modalAberto(page), false, "repeat/keyup em idle não pode virar clique com detail 0");
        assert.equal(reqs.length, 1);
      } finally { await ctx.close(); }
    });
  }

  const ABORTOS = {
    pointerleave: (page, a) => page.dispatchEvent(`#${a.botao}`, "pointerleave"),
    pointercancel: (page, a) => page.dispatchEvent(`#${a.botao}`, "pointercancel"),
    touchcancel: (page, a) => page.dispatchEvent(`#${a.botao}`, "touchcancel"),
    blur: (page, a) => page.evaluate((id) => document.getElementById(id).blur(), a.botao),
    Esc: (page) => page.keyboard.press("Escape"),
    "aba oculta": (page) => page.evaluate(() => {
      Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
      document.dispatchEvent(new Event("visibilitychange"));
    }),
  };
  test(`${a.nome}: pointerleave, pointercancel, touchcancel, blur, Esc e aba oculta aos 600 ms cancelam`, async () => {
    const { page, ctx, reqs } = await abre(a);
    try {
      for (const [nome, aborta] of Object.entries(ABORTOS)) {
        await aperta(page, a);
        await page.clock.runFor(600);
        assert.equal(await estado(page, a), "holding", `${nome}: antes de abortar`);
        await aborta(page, a);
        assert.equal(await estado(page, a), "idle", `${nome} tinha que cancelar`);
        await page.mouse.up();
        await page.clock.runFor(1000);
        await assenta();
        assert.equal(reqs.length, 0, `${nome}: o timer velho disparou`);
      }
    } finally { await ctx.close(); }
  });

  test(`${a.nome}: senha vazia — o aperto não arma, mostra o toast e não faz requisição`, async () => {
    const { page, ctx, reqs } = await abre(a, { senha: "" });
    try {
      await aperta(page, a);
      assert.equal(await estado(page, a), "idle");
      assert.equal(await page.evaluate(() => document.getElementById("toast").textContent), "Confirme sua senha");
      await page.clock.runFor(1500);
      await assenta();
      assert.equal(reqs.length, 0);
    } finally { await ctx.close(); }
  });

  test(`${a.nome}: erro 400 — toast, botão volta a idle com o rótulo e o anel, e um novo hold faz a 2ª requisição`, async () => {
    const { page, ctx, reqs } = await abre(a, {
      resposta: (n) => (n === 1 ? json({ detail: "Senha incorreta" }, 400) : json(a.sucesso)),
    });
    try {
      await aperta(page, a);
      await page.clock.runFor(1200);
      await page.mouse.up();
      await waitFor(() => reqs.length === 1, "a 1ª requisição");
      await waitFor(async () => (await estado(page, a)) === "idle", "voltar a idle depois do erro");
      assert.match(await page.evaluate(() => document.getElementById("toast").textContent), /Senha incorreta/);
      assert.equal(await rotulo(page, a), a.rotulo, "o rótulo original volta");
      assert.equal(await page.locator(`#${a.botao} svg.hold-ring`).count(), 1, "o anel não pode sumir do DOM");
      assert.equal(await page.locator(`#${a.botao}`).isDisabled(), false);
      await aperta(page, a);
      await page.clock.runFor(1200);
      await waitFor(() => reqs.length === 2, "a 2ª requisição, do novo hold");
    } finally { await ctx.close(); }
  });

  test(`${a.nome}: duplo disparo — segurar de novo durante o fetch lento não faz a 2ª requisição`, async () => {
    let solta;
    const preso = new Promise((r) => { solta = r; });
    const { page, ctx, reqs } = await abre(a, { resposta: () => preso.then(() => json({ detail: "lento" }, 400)) });
    try {
      await aperta(page, a);
      await page.clock.runFor(1200);
      await page.mouse.up();
      await waitFor(() => reqs.length === 1, "a requisição em voo");
      assert.equal(await page.locator(`#${a.botao}`).isDisabled(), true, "disabled é síncrono no caminho do hold");
      await aperta(page, a);
      await page.clock.runFor(1500);
      await page.mouse.up();
      await assenta();
      assert.equal(reqs.length, 1);
      solta();
    } finally { solta(); await ctx.close(); }
  });

  test(`${a.nome}: contextmenu no botão sai com defaultPrevented`, async () => {
    const { page, ctx } = await abre(a);
    try {
      const cancelado = await page.evaluate((id) =>
        !document.getElementById(id).dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true })), a.botao);
      assert.equal(cancelado, true);
    } finally { await ctx.close(); }
  });

  test(`${a.nome}: fallback assistivo — clique sem gesto (detail 0) abre o confirmModal e só o "sim" faz a requisição`, async () => {
    const { page, ctx, reqs } = await abre(a);
    try {
      await page.evaluate((id) => document.getElementById(id).click(), a.botao);   // el.click(): detail 0
      assert.equal(await modalAberto(page), true);
      await page.clock.runFor(100);
      await assenta();
      assert.equal(reqs.length, 0, "antes do 'sim' nada vai");
      await page.locator(".pig-modal-overlay").getByRole("button", { name: a.modal }).click();
      await waitFor(() => reqs.length === 1, "a requisição depois do 'sim'");
      assert.deepEqual(reqs[0].corpo, a.corpo);
    } finally { await ctx.close(); }
  });
}
