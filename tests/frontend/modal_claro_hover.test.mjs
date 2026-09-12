/**
 * Hover que MUDA no tema escuro tem de mudar no CLARO também.
 *
 * Assunto diferente do `modal_claro_veu.test.mjs`, e por isso arquivo próprio: lá a
 * pergunta é "o véu SOME sobre a superfície?", aqui é "o FEEDBACK existe?". As duas
 * não se substituem, e esta alcança um ponto cego estrutural da outra — se a
 * regressão foi escrita JÁ dentro do ramo `body.light`, o valor DIFERE do escuro e o
 * filtro de "tinta ignorante de tema" descarta o achado por construção.
 *
 * Foi cegueira declarada como teórica e era real: `body.light .mock-cta.outline:hover
 * { background:#fff }` saltava ΔE 32,34 enquanto o modal era escuro e ZERO sobre o
 * branco, em 4 botões, mais os dois `.hbtn` da navegação de fatura. Apagar
 * `body.light .btn-cancel:hover` não deixa nada branco (a base clara tem
 * especificidade maior e ganha), então o caso de "véu sumido" fica cego; o que se
 * perde é o hover ficar IGUAL ao repouso. Foi uma das 3 mutações que passavam verdes.
 *
 * Rodar:  npm run test:frontend
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";
import { MARGEM, medir as medirPagina, rgba, sobre, lab, dE, sup } from "./_modal_veu.mjs";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });
const medir = (opts) => medirPagina(browser, ORIGIN, opts);

/** ESCURO muda de aparência no hover e o CLARO não muda (item 5 do cabeçalho).
 *  Não é sobre sumir, é sobre o feedback existir: apagar
 *  `body.light .btn-cancel:hover` não deixa nada branco (a base clara tem
 *  especificidade maior), então "véu sumido" fica cego e o que se perde é o hover
 *  ficar IGUAL ao repouso. Foi uma das 3 mutações que passavam verdes. */
function hoverMudo(repouso, hover, rotulo) {
  const rc = new Map(repouso.claro.els.map((e) => [e.i, e]));
  const re = new Map(repouso.escuro.els.map((e) => [e.i, e]));
  const he = new Map(hover.escuro.els.map((e) => [e.i, e]));
  const out = [];
  // Quanto o hover MOVE a cor, em ΔE — não se o TEXTO da cor mudou.
  // `rgba(255,255,255,.7)` → `rgb(255,255,255)` sobre modal branco são strings
  // diferentes e o MESMO pixel: comparar string dava o hover da `.hbtn` como
  // "mudou" quando ele não muda nada.
  const salto = (antes, depois, prop) => {
    const s = sup(antes);
    const [x, y] = [antes[prop], depois[prop]].map((v) => rgba(v));
    return !x || !y ? 0 : dE(lab(sobre(x, s)), lab(sobre(y, s)));
  };
  for (const h of hover.claro.els) {
    const [a, b, c] = [rc.get(h.i), re.get(h.i), he.get(h.i)];
    if (!a || !b || !c) continue;
    const noEscuro = Math.max(salto(b, c, "background"), salto(b, c, "border"));
    if (noEscuro < MARGEM) continue;             // o escuro nem dá retorno aqui
    const noClaro = Math.max(salto(a, h, "background"), salto(a, h, "border"));
    if (noClaro >= MARGEM) continue;
    out.push(`${h.nome}  salto no hover: escuro ΔE=${noEscuro.toFixed(2)}, claro ΔE=${noClaro.toFixed(2)}`);
  }
  const l = [...new Set(out)];
  assert.deepEqual(l, [], `${rotulo}: ${l.length} elemento(s) com hover MUDO no tema claro\n  ` + l.join("\n  "));
}

test("modal claro: hover que muda no escuro tem de mudar no claro também", async () => {
  const repouso = { claro: await medir({ claro: true }), escuro: await medir({ claro: false }) };
  const hover = { claro: await medir({ claro: true, hover: true }), escuro: await medir({ claro: false, hover: true }) };
  hoverMudo(repouso, hover, "hover");
});
