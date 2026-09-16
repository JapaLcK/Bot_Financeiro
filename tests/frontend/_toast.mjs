/**
 * Esperas do `#toast` POR ESTADO, não por relógio — compartilhadas pelos testes
 * que acendem o toast do dashboard, do settings e do precos.
 *
 * Um `waitForTimeout(300)` fixo media o toast no meio da animação sob carga
 * (runner do CI; aqui, `PB_TEST_CPU_THROTTLE=50`): `top` 622 = 612 + os 10px
 * do translateY inicial, e a folga de 8px do FAB virava 5,5–6,9. O critério
 * de `toastVisivel` é `opacity === "1"` (assentou VISÍVEL) + zero animações
 * pendentes — e NÃO o `transform` final, porque cada página assenta numa
 * matriz diferente (dashboard.css:1625 `translateY(0)`; settings.html:1056
 * `translateX(-50%) translateY(0)`; precos.html:27 idem). `getAnimations()
 * .length === 0` cobre as propriedades sem conhecer o valor final. Não use
 * `getAnimations().map(a => a.finished)` como o bank_movements.test.mjs: o
 * `.finished` REJEITA se o timer do showToast cancelar a transição.
 *
 * `toastAssentado` é a metade sem visibilidade: só "nenhuma transição em
 * curso". Serve para os "pós" em que o toast está ESCONDENDO (o `showToast("")`
 * do pix-checkout.js:263) e o assert seguinte é que decide se ele sumiu —
 * esperar `opacity === "0"` aqui faria o vermelho de uma regressão ser timeout
 * em vez do assert de opacidade.
 */

/** Nenhuma transição pendente no `#toast`. Não afirma visibilidade. */
export const toastAssentado = (page) => page.waitForFunction(
  () => document.getElementById("toast").getAnimations().length === 0,
  null, { timeout: 5_000 });

/** Toast assentado E visível. */
export const toastVisivel = (page) => page.waitForFunction(() => {
  const t = document.getElementById("toast");
  return getComputedStyle(t).opacity === "1" && t.getAnimations().length === 0;
}, null, { timeout: 5_000 });

/**
 * Acende o toast pelo CAMINHO REAL (`window.showToast`) e espera assentar.
 * `type` vai adiante: o dashboard.js (~:7578) o ignora; o settings.html
 * (~:1956, default "ok") o escreve em `className = "show <type>"`; o
 * precos.html (:758) só reconhece "err".
 *
 * Escrever `textContent` + `.show` na mão parecia equivalente e não é: para
 * mensagem que começa com ✓ — que é o DEFAULT do dashboard — o showToast monta
 * `innerHTML` com um `<img class="toast-sticker">` de 22px, e a caixa passa de
 * 35px para 42px de altura. Medir a caixa errada é medir outro elemento.
 *
 * O `setInterval` existe porque o toast se apaga sozinho, e cada página tem o
 * seu timer: `toastT` no dashboard.js (~:7577, 2s), `_toastTimer` no
 * settings.html (~:1955, 2,4s), `showToast._t` no precos.html (:769, 3,8s). O
 * interval serve a todas sem conhecer nenhum. Sob paralelismo uma medição
 * podia cair depois do apagão e ler a caixa já escondida. Re-adicionar `.show`
 * já presente não cria animação, então ele não atrapalha a espera. Morre com
 * a página.
 */
export async function comToast(page, msg, type) {
  await page.evaluate(([m, t]) => {
    window.showToast(m, t);
    const el = document.getElementById("toast");
    clearInterval(window.__mantemToast);
    window.__mantemToast = setInterval(() => el.classList.add("show"), 100);
  }, [msg, type]);
  await toastVisivel(page);
}
