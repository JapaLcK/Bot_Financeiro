/**
 * A janela do duplo clique (#1411, `JANELA_DO_DUPLO_CLIQUE` = 400 ms) vista pela PÁGINA: com a máquina carregada o runner atrasa o
 * 2º clique/aperto além dos 400 ms, e aí confirmar é o certo. Comum a confirma_duplo_clique_app.test.mjs e _settings.test.mjs.
 * Não é `*.test.mjs` de propósito: o `node --test tests/frontend/*.test.mjs` não o roda.
 */
import assert from "node:assert/strict";

/** Os grupos com pelo menos um caso dentro da janela. Cada arquivo de teste roda no próprio processo: o estado é do arquivo. */
export const valeram = {};

/** Roda `aperta` anotando em que momento a página processa cada `tipo` (`performance.now()`, o relógio da janela). Com os dois
 *  `tipo` a ≥ 400 ms, o caso é pulado (nunca dado por bom) e devolve false. `tipo`: "click" (a trava do ponteiro decide no clique)
 *  ou "keydown" (o `cedo` do teclado). `aperta` pode devolver outro motivo para o caso não valer (texto do skip). */
export async function dentroDaJanela(t, page, tipo, grupo, aperta) {
  await page.evaluate((tp) => {
    window.__emOff?.();
    window.__em = [];
    const anota = () => window.__em.push(performance.now());
    addEventListener(tp, anota, true);
    window.__emOff = () => removeEventListener(tp, anota, true);
  }, tipo);
  const fora = await aperta();
  const em = await page.evaluate(() => window.__em);
  assert.equal(em.length, 2, `${tipo}: ${JSON.stringify(em)}`);
  const ms = em[1] - em[0];
  if (ms >= 400) { t.skip(`o 2º ${tipo} chegou ${Math.round(ms)} ms depois do 1º, fora da janela`); return false; }
  if (fora) { t.skip(fora); return false; }
  valeram[grupo] = (valeram[grupo] || 0) + 1;
  return true;
}
