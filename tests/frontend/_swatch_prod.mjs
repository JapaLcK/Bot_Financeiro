/**
 * Máquinário do `swatch_anel_selecao.test.mjs`: injeção do markup de PRODUÇÃO,
 * amostragem de pixel e as duas piores cores de cada paleta.
 *
 * Mora fora do teste pelo mesmo motivo do `_modal_veu.mjs` (`CLAUDE.md` §0.5): são
 * duas responsabilidades e juntas passavam do teto de 350 do `quality/max-lines`.
 * O "por quê" de cada decisão está no cabeçalho do arquivo de teste; aqui o "como".
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { rgba, sobre } from "./_modal_veu.mjs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

export const PISO = 3;                // WCAG 2.2 SC 1.4.11, Non-text Contrast
const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
const SRC = readFileSync(join(FRONTEND, "dashboard.js"), "utf8");

/** Corpo de `function nome(…)`, por contagem de chaves. */
export function fn(nome) {
  const i = SRC.indexOf(`function ${nome}(`);
  assert.ok(i >= 0, `função ${nome} não achada no dashboard.js`);
  let prof = 0, k = SRC.indexOf("{", i);
  for (; k < SRC.length; k++) {
    if (SRC[k] === "{") prof++;
    else if (SRC[k] === "}" && --prof === 0) break;
  }
  return SRC.slice(i, k + 1);
}
/** `const NOME = [ … ];` literal. */
export function arr(nome) {
  const i = SRC.indexOf(`const ${nome} = [`);
  assert.ok(i >= 0, `const ${nome} não achada`);
  return SRC.slice(i, SRC.indexOf("];", i) + 2);
}
const linha = (ini) => SRC.slice(SRC.indexOf(ini), SRC.indexOf("\n", SRC.indexOf(ini)));
/** `let NOME = { … };` de uma linha, VERBATIM. Estado copiado à mão diverge em
 *  silêncio: a 1ª versão daqui injetava `{ emoji, color }` enquanto a produção já
 *  usava `{ id, emoji, color, is_system }`. */
export function decl(nome) {
  const i = SRC.indexOf(`let ${nome} = {`);
  assert.ok(i >= 0, `let ${nome} não achado no dashboard.js`);
  const fim = SRC.indexOf("};", i) + 2;
  assert.ok(SRC.slice(i, fim).split("\n").length === 1, `let ${nome} deixou de ser uma linha`);
  return SRC.slice(i, fim);
}
/** O `<div id="…">` do contêiner, VERBATIM do `dashboard.js`. O `gap` dele é a
 *  folga de que o anel externo de 2px depende — copiado à mão, uma mudança de
 *  `gap` em produção passaria despercebida e o anel encavalaria o vizinho. */
export function contDiv(id) {
  const re = new RegExp(`<div id="${id}"[^>]*></div>`);
  const m = SRC.match(re);
  assert.ok(m, `contêiner #${id} não achado no dashboard.js`);
  return m[0];
}

export const CODIGO = [
  arr("CARD_COLOR_OPTIONS"), arr("CATEGORY_EMOJI_OPTIONS"), arr("CATEGORY_COLOR_OPTIONS"),
  arr("GOAL_EMOJI_OPTIONS"), arr("GOAL_COLOR_OPTIONS"), linha("const EMOJI_TO_PH = {"),
  fn("phIcon"), fn("_rerenderPicker"),
  fn("_renderCardColorPicker"), fn("_pickCardColor"),
  fn("_renderCategoryPickers"), fn("_setCatEmoji"), fn("_setCatColor"),
  fn("_renderGoalPickers"), fn("_setGoalEmoji"), fn("_setGoalColor"),
  decl("_cardEditState"), decl("_catEditState"), decl("_goalEditState"),
  `window.pb = { card: _renderCardColorPicker, cat: _renderCategoryPickers,
                 goal: _renderGoalPickers, _catEditState, _goalEditState,
                 CARD_COLOR_OPTIONS, CATEGORY_COLOR_OPTIONS, GOAL_COLOR_OPTIONS };
   window._pickCardColor = _pickCardColor;
   window._setCatEmoji = _setCatEmoji; window._setCatColor = _setCatColor;
   window._setGoalEmoji = _setGoalEmoji; window._setGoalColor = _setGoalColor;`,
].join("\n");

// Contêineres EXTRAÍDOS do `dashboard.js`, não recopiados — ver `contDiv`.
// `#card-edit-colors` não entra: ele é estático no `dashboard.html` e já está na
// página que o `montar()` carrega.
const CONTS = ["cat-emoji-picker", "cat-color-picker",
               "goal-emoji-picker", "goal-color-picker"].map(contDiv).join("\n");


export async function montar(browser, ORIGIN) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 },
                                       deviceScaleFactor: 1 });
  // Sem sessão o `dashboard.js` troca o body por uma tela de erro e o documento
  // fica sem `.modal` nenhum: os scripts ficam de fora e o markup estático é o alvo.
  await page.route("**/*.js", (r) => r.abort());
  await page.route("**/*.js?*", (r) => r.abort());
  await page.goto(`${ORIGIN}/dashboard.html`);
  await page.waitForSelector("#card-edit-colors", { state: "attached" });
  await page.addStyleTag({
    content: "*,*::before,*::after{transition:none !important;animation:none !important}" });
  await page.evaluate((conts) => {
    document.getElementById("card-edit-overlay").classList.add("open");
    const caixa = document.createElement("div");
    caixa.innerHTML = conts;
    document.getElementById("card-edit-colors").closest(".modal").append(caixa);
  }, CONTS);
  await page.addScriptTag({ content: CODIGO });
  await render(page);
  return page;
}
/** Renderiza os três pickers pelo caminho de produção, no estado default. */
export const render = (page) => page.evaluate(() => {
  window.pb.card("purple");        // 1º da paleta, default de todo cartão novo
  window.pb.cat(); window.pb.goal();
});
/** Só a cor de um `box-shadow` computado: `rgba()` do `_modal_veu` lê os números
 *  crus e pegaria o `0px` do offset como alpha, dando "anel ausente". */
export const soCor = (s) => (String(s).match(/rgba?\([^)]*\)/) || [])[0] || "";
/* Cor resolvida do INDICADOR, lida nas MESMAS propriedades que os casos leem: a
   borda do emoji selecionado e o `box-shadow` do swatch de cor selecionado. É
   string de expressão porque serve tanto ao `evaluate` quanto ao `waitForFunction`. */
const INDICADOR = `(() => {
  const rgb = (s) => (String(s).match(/\\d+,\\s*\\d+,\\s*\\d+/) || [""])[0].replace(/\\s/g, "");
  const e = document.querySelector("#cat-emoji-picker .sw-emoji.selected");
  const c = document.querySelector("#cat-color-picker .sw-color.selected");
  return (e ? rgb(getComputedStyle(e).borderTopColor) : "?") + "|" +
         (c ? rgb(getComputedStyle(c).boxShadow) : "?");
})()`;

/**
 * Troca o tema no documento JÁ MONTADO — é isso que os casos provam: o indicador
 * acompanha o flip sem re-render — e só volta quando a cor do INDICADOR mudou.
 *
 * O que ela espera, e por que é o valor certo: exatamente as duas propriedades que
 * os casos medem (`border-top-color` do emoji, `box-shadow` do swatch de cor), no
 * elemento real. Uma versão anterior esperava o `--purple-ink` do `body` e chamava
 * isso de "barreira determinística" — era proxy, e sustentada por uma afirmação
 * mais forte do que a medição: `waitForFunction` é determinístico sobre a
 * PRIMITIVA, não sobre a medição. Medido depois: em 3000 toggles o token do `body`
 * e a borda do swatch NUNCA divergem, então aquela barreira era no-op no plano
 * observável, e 7 execuções verdes limitam a taxa de flake a ≤35% — não distinguem
 * "consertado" de "intacto" contra os ~10% observados.
 * Esta versão não depende dessa estatística: ela lê o que o caso vai ler.
 *
 * Espera COM TETO e sem estourar de propósito. Se o indicador não mudar — mutação
 * que tirou a dependência de tema, por exemplo — quem reprova é o caso, com a
 * mensagem dele, em vez de um timeout opaco. Sem `sleep`: o teto só existe para o
 * caso em que não há mudança nenhuma a esperar.
 */
export async function tema(page, claro) {
  const antes = await page.evaluate(INDICADOR);
  const trocou = await page.evaluate((c) => {
    const ja = document.body.classList.contains("light") === c;
    document.body.classList.toggle("light", c);
    return !ja;
  }, claro);
  if (!trocou) return;                       // já estava no tema pedido
  await page.waitForFunction(`${INDICADOR} !== ${JSON.stringify(antes)}`,
                             { timeout: 2000 }).catch(() => {});
}

/** Faixa horizontal de 1px cruzando a borda ESQUERDA do elemento. */
export async function faixa(page, sel, antes, largura) {
  const cx = await page.evaluate((sel) => {
    const el = document.querySelector(sel);
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return { x: Math.round(r.x), y: Math.round(r.y + r.height / 2),
             borda: Math.round(parseFloat(s.borderLeftWidth)),
             // cor do anel, sem os offsets do `box-shadow`, para o caso do vão
             // poder exigir que ele apareça em PIXEL e não só no computado
             anel: ((s.boxShadow.match(/\d+,\s*\d+,\s*\d+/) || [""])[0] || "").replace(/\s/g, "") };
  }, sel);
  const buf = await page.screenshot(
    { clip: { x: cx.x - antes, y: cx.y, width: largura, height: 1 } });
  const px = await page.evaluate(async (b64) => {
    const img = new Image(); img.src = `data:image/png;base64,${b64}`; await img.decode();
    const cv = document.createElement("canvas"); [cv.width, cv.height] = [img.width, 1];
    const c = cv.getContext("2d"); c.drawImage(img, 0, 0);
    const d = c.getImageData(0, 0, img.width, 1).data;
    return [...Array(img.width)].map((_, k) => `${d[k * 4]},${d[k * 4 + 1]},${d[k * 4 + 2]}`);
  }, buf.toString("base64"));
  return { px, ...cx };
}
export const perto = (a, b) => a.split(",").every((v, j) => Math.abs(+v - +b.split(",")[j]) <= 2);

/* As duas piores cores de CADA paleta, e elas não são as mesmas nas três: o
   `GOAL_COLOR_OPTIONS` tem 10 cores e NÃO contém `#eab308`, então uma lista única
   deixava o picker de meta sem nenhum selecionado e o caso morria em `null` — o
   teste achou isso sozinho. `#FF2D8E` é o pior caso universal (no escuro é a
   própria tinta do anel, 1,00:1 encostado) e o 2º é o âmbar/ouro de cada paleta,
   contra o qual a borda `#fff` ANTERIOR já dava só 1,92:1 no escuro. */
export const CORES = [
  { sel: "#card-edit-colors .sw-card", valores: ["purple", "gold"],
    aplica: (page, v) => page.evaluate((v) => window.pb.card(v), v),
    tem: (page, v) => page.evaluate((v) => window.pb.CARD_COLOR_OPTIONS.some((o) => o.key === v), v) },
  { sel: "#cat-color-picker .sw-color", valores: ["#FF2D8E", "#eab308"],
    aplica: (page, v) => page.evaluate((v) => {
      window.pb._catEditState.color = v; window.pb.cat(); }, v),
    tem: (page, v) => page.evaluate((v) => window.pb.CATEGORY_COLOR_OPTIONS.includes(v), v) },
  { sel: "#goal-color-picker .sw-color", valores: ["#FF2D8E", "#BE8200"],
    aplica: (page, v) => page.evaluate((v) => {
      window.pb._goalEditState.color = v; window.pb.goal(); }, v),
    tem: (page, v) => page.evaluate((v) => window.pb.GOAL_COLOR_OPTIONS.includes(v), v) },
];
export const CORES_SEL = CORES.map((c) => c.sel);
export const EMOJIS = ["#cat-emoji-picker .sw-emoji", "#goal-emoji-picker .sw-emoji"];

/**
 * Razão de contraste WCAG entre duas cores JÁ OPACAS. Piso 3:1 para componente e
 * objeto gráfico (WCAG 2.2 SC 1.4.11, Non-text Contrast) — não 4,5:1, que é texto.
 *
 * A GUARDA de alpha não é zelo: a 1ª versão desta função consumia só `r,g,b` e
 * jogava o `a` fora, e com isso `box-shadow:0 0 0 2px rgba(199,24,107,.06)` — anel
 * praticamente invisível nos 31 swatches de cor — passava com 5,57:1 e a suíte
 * inteira ficava VERDE. Pior: a pendência que o cabeçalho do teste registra
 * (`.tipo-opt.active` e irmãs, com `rgba(255,45,142,.45)`) seria lida como
 * `#FF2D8E` opaco e APROVARIA, então o plano de "apontar a mesma régua para os
 * três" não fecharia nada. Componha com `sobre()` antes de chamar; a régua se
 * recusa a adivinhar qual é o fundo.
 */
export function contraste(a, b) {
  for (const [c, qual] of [[a, "tinta"], [b, "fundo"]])
    assert.ok(c && (c.a === undefined || c.a >= 1),
      `contraste() recebeu ${qual} com alpha ${c?.a} — componha com sobre(tinta, fundo) antes`);
  const Y = ({ r, g, b: z }) => { const f = (v) => { v /= 255;
    return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(z); };
  const [x, y] = [Y(a), Y(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}
/** Contraste de uma tinta possivelmente TRANSLÚCIDA contra um fundo opaco. */
export const contrasteSobre = (tinta, fundo) => contraste(sobre(tinta, fundo), fundo);
export { rgba, sobre };
