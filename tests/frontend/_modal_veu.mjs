/**
 * Máquinário de medição do `modal_claro_veu.test.mjs` — extração da Fonte C,
 * carga da página, leitura de estilo computado e matemática de cor.
 *
 * Mora fora do teste porque são duas responsabilidades diferentes (`CLAUDE.md`
 * §0.5) e porque juntas passavam do teto de 350 linhas do `quality/max-lines`.
 * O "por quê" de cada decisão está no cabeçalho do arquivo de teste; aqui fica
 * o "como".
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");

/* Quatro números definem esta régua, e eles vêm de DUAS procedências
   diferentes. A versão anterior mandava reproduzir os quatro pelo dump, e dois
   deles não saem de lá de jeito nenhum — a receita não reproduzia.

   (A) OS DOIS LEGÍTIMOS saem do dump. Troque `MARGEM` por 999, rode
       `node --test tests/frontend/modal_claro_veu.test.mjs` e leia os menores
       ΔE de cada passe (o `achados()` imprime `ΔE=` em cada linha):
         3,88  `#pkt-move-amount` rgba(0,0,0,.25) sobre o modal escuro
         5,01  `.bill-balance-line` rgba(255,128,184,.08) sobre #fff
       São o piso do que a régua NÃO pode reprovar.

   (B) OS DOIS QUE DEFINEM A JANELA são ANALÍTICOS, e é impossível vê-los no
       dump: `achados()` descarta todo elemento cuja tinta difere entre os
       temas, que é exatamente toda regra que este PR conserta. Reproduza com
       `dE(lab(sobre({r:15,g:23,b:42,a}, branco)), lab(branco))` deste arquivo:
         a = .03   → 2,429   menor véu que o bloco pinta (`.pkt-move-form`)
         a = .0125 → 1,011   substituto que compõe rgb(252,252,252), invisível
       A régua tem de aprovar o primeiro e reprovar o segundo, logo cabe em
       (1,011 ; 2,429]. `Math.sqrt(1.011 * 2.429)` = 1,567 — média geométrica,
       que iguala a folga PROPORCIONAL dos dois lados; arredondada para 1,6, dá
       1,58× acima do invisível e 1,52× abaixo do menor conserto.

   Limite honesto: a janela inteira mora perto do JND (~2,3 de ΔE76). O menor
   véu que este bloco pinta é, ele mesmo, quase imperceptível — a régua não pode
   subir mais sem reprovar um conserto real. Ela separa "some" de "não some",
   não "bonito" de "feio". Remedir pelos dois caminhos antes de reusar. */
export const MARGEM = 1.6;

/* ── Fonte C: markup que só existe dentro do dashboard.js ────────────────────
   `class="modal` casa 18 vezes no arquivo, e NOVE delas são `class="modal-acts`
   — a contagem crua mente; o teste trava o 18 (9 × 2 ramos) abaixo. */

/* Resolve `${ cond ? "literal" : "literal" }` escolhendo UM ramo, antes do
   `achatar()`. Sem isto o ramo perdia duas coisas de uma vez:
     - o VALOR: `border:2px solid ${sel ? "#fff" : "transparent"}` chegava ao DOM
       como `border:2px solid  ` (declaração inválida, descartada) e os 6 botões
       dos três pickers eram medidos SEM véu nenhum;
     - a CLASSE: `class="bill-opt${cond ? ' selected' : ''}"` perdia o token
       `selected` no `limparClasses` (o token vinha com aspa), então
       `.bill-opt.selected` NUNCA foi medido.
   DOIS passes, não 2ⁿ: cada elemento é medido isoladamente contra a superfície
   do modal ancestral, então combinação mista de ramos não muda nenhuma leitura.
   `[^?{}]*?` na condição limita a UM nível — ternário aninhado não resolve e
   cai no `achatar()` como antes, que é o comportamento anterior, não uma piora. */
const TERN = /\$\{\s*[^?{}]*?\?\s*(["'])((?:(?!\1)[^\\])*)\1\s*:\s*(["'])((?:(?!\3)[^\\])*)\3\s*\}/g;
export const ramificar = (src, verdadeiro) =>
  src.replace(TERN, (_, q1, a, q2, b) => (verdadeiro ? a : b));

function achatar(s) {
  // Achata template literal: tira as CRASES e os delimitadores `${` `}`,
  // guardando o conteúdo dos DOIS lados. A 1ª versão removia o `${…}` inteiro
  // e com ele o markup aninhado — foi assim que `.detail-cell` e `.bill-tx`
  // ficaram de fora, e `.bill-opt` junto (onde estava o defeito da 6ª rodada).
  let out = "", i = 0; const pilha = [];
  while (i < s.length) {
    const c = s[i];
    // Espaço no lugar do delimitador, não vazio: `class="bill-opt${…}"` colado
    // vira UMA classe inexistente (`bill-optpayBillState.selectedId…`) e a
    // `.bill-opt` deixa de ser medida — exatamente a classe do defeito da
    // 6ª rodada. Com o espaço ela sobrevive como token próprio.
    if (c === "$" && s[i + 1] === "{") { pilha.push("i"); out += " "; i += 2; continue; }
    if (c === "{") { if (pilha.length) pilha.push("{"); out += c; i++; continue; }
    if (c === "}") { if (pilha.length && pilha.pop() === "i") { out += " "; i++; continue; } out += c; i++; continue; }
    if (c === "`") { i++; continue; }
    out += c; i++;
  }
  return out;
}

/** Tira do `class="…"` o que sobrou de expressão JS: só identificador CSS
 *  válido fica. Sem isto o resto vira classe fantasma no DOM. */
const limparClasses = (html) => html.replace(/class="([^"]*)"/g, (_, v) =>
  `class="${v.trim().split(/\s+/).filter((t) => /^[A-Za-z_-][\w-]*$/.test(t)).join(" ")}"`);

/** Blocos de tag balanceados a partir de `pos`. */
function blocos(src, pos, limite) {
  const out = [], fim = Math.min(src.length, pos + limite);
  const VAZIA = /^(br|hr|img|input|meta|link|source|path|circle|rect|use)$/i;
  const re = /<(\w+)(?=[\s/>])[^>]*?(\/?)>|<\/(\w+)\s*>/g;
  re.lastIndex = pos;
  let m, prof = 0, ini = -1, raiz = null;
  while ((m = re.exec(src)) && m.index < fim) {
    if (m[1]) {
      if (m[2] === "/" || VAZIA.test(m[1])) { if (prof === 0) out.push(m[0]); continue; }
      if (prof === 0) { ini = m.index; raiz = m[1]; }
      prof++;
    } else if (m[3] && prof > 0 && --prof === 0 && m[3] === raiz) {
      out.push(src.slice(ini, m.index + m[0].length));
    }
  }
  return out;
}

/**
 * Devolve `{ raizes, alvos }` para UM ramo de ternário (ver `ramificar`):
 *  - `raizes`: os 9 `<div class="modal …">` inteiros do dashboard.js;
 *  - `alvos`: id do contêiner -> markup que ele recebe.
 * Três resoluções, cada uma paga por uma cobertura que faltou:
 *  - `X.innerHTML =` e `X.insertAdjacentHTML(...)`, com `X` por id ou variável;
 *  - HELPER: quando o markup não está na chamada e sim numa função
 *    (`cl-list.insertAdjacentHTML("beforeend", _clRowsHtml(...))`), o markup da
 *    função entra no contêiner. Sem isso `.cl-row` não existia no DOM e a
 *    mutação que apaga `body.light .cl-row:hover` passava VERDE.
 * A janela vai até o próximo alvo de id DIFERENTE, com teto de 6 KB, e ainda
 * assim SOBREPÕE: `#cl-list` recebe um `class="modal wide"` inteiro e
 * `#investment-detail-body` recebe `.rate-tile`. Isso NÃO é só duplicar
 * medição — é o modo de falha ruim: o teste pode ficar VERMELHO por um elemento
 * que nunca esteve dentro de um modal em produção. Hoje não custa nada porque
 * nenhuma dessas classes tem véu branco sem par claro, mas um vermelho daqui
 * pede conferência da containment REAL antes de virar conserto de CSS.
 * O viés é deliberado: cortar no `innerHTML` seguinte, ou na `function`
 * seguinte, PERDIA `.bill-opt` do `#pay-bill-list` (que limpa o contêiner antes
 * de preencher), e perder é o erro que custou as rodadas. Cobertura medida
 * neste corte: `.bill-opt`, `.cl-row`, `.bar-icon`, `.bill-tx`, `.detail-cell`
 * e `.pkt-hist-row`, todas presentes.
 */
function fonteJs(ramo) {
  const plano = achatar(ramificar(readFileSync(join(FRONTEND, "dashboard.js"), "utf8"), ramo));
  const raizes = [];
  for (const m of plano.matchAll(/<div class="modal[ "]/g)) {
    const b = blocos(plano, m.index, 40000);
    if (b.length) raizes.push(limparClasses(b[0]));
  }
  // `wrap`, `ePick` e `cPick` são declarados VÁRIAS vezes no arquivo, cada uma
  // apontando para um id diferente. Um `Map` nome -> id guardava só a ÚLTIMA, e o
  // efeito não era perder um pouco de cobertura: era atribuir markup ao contêiner
  // ERRADO. `wrap` resolvia para `#launches-wrap` (:7797), então o picker de cor
  // do cartão (`#card-edit-colors`, :974) caía fora de modal e era descartado; e
  // `ePick`/`cPick` resolviam para os pickers da META (:3361-3362), então os da
  // CATEGORIA (:2485-2486) nunca entravam no DOM medido. Dos três pickers, um só
  // era medido. Resolve pela declaração mais próxima ANTES do uso.
  const decls = [...plano.matchAll(
    /(?:const|let|var)\s+(\w+)\s*=\s*document\.getElementById\(["']([\w-]+)["']\)/g)]
    .map((m) => ({ nome: m[1], id: m[2], pos: m.index }));
  const varId = (nome, pos) => decls.filter((d) => d.nome === nome && d.pos < pos).at(-1)?.id;
  const fnHtml = new Map();
  for (const m of plano.matchAll(/function\s+(\w+)\s*\([^)]*\)\s*\{/g)) {
    // `style="` entra ao lado de `class="`: markup SÓ com estilo inline era
    // descartado em silêncio aqui, e os botões dos três pickers de emoji/cor não
    // têm classe nenhuma — foi por isso que o véu branco deles nunca foi medido.
    const h = blocos(plano, m.index, 4000).filter((x) => /class="|style="/.test(x)).join("");
    if (h) fnHtml.set(m[1], h);
  }
  const alvos = new Map();
  const ALVO = /(?:document\.getElementById\(["']([\w-]+)["']\)|\b(\w+))\s*\.(?:innerHTML\s*\+?=|insertAdjacentHTML\s*\()\s*/g;
  for (const m of plano.matchAll(ALVO)) {
    const id = m[1] || varId(m[2], m.index);
    if (!id) continue;
    const ini = m.index + m[0].length;
    // A janela termina no PRÓXIMO alvo de id DIFERENTE (teto de 6 KB). Cortar
    // no próximo `innerHTML` qualquer perdia `.bill-opt` do `#pay-bill-list`,
    // que limpa o contêiner antes de preencher; cortar no `function` seguinte
    // perdia o mesmo. Este corte mantém as seis coberturas medidas e elimina o
    // vazamento do `#invest-summary` (`.chip`) para o `#move-name`.
    let jan = 6000;
    { const re = new RegExp(ALVO.source, "g"); re.lastIndex = ini;
      let n;
      while ((n = re.exec(plano))) {
        const outro = n[1] || varId(n[2], n.index);
        if (!outro || outro === id) continue;
        jan = Math.min(jan, n.index - ini); break;
      } }
    const regiao = plano.slice(ini, ini + jan);
    let html = blocos(plano, ini, jan).filter((h) => /class="|style="/.test(h)).join("");
    for (const c of regiao.slice(0, 300).matchAll(/\b(\w+)\s*\(/g))
      if (fnHtml.has(c[1])) html += fnHtml.get(c[1]);
    html = limparClasses(html);
    if (!html) continue;
    alvos.set(id, (alvos.get(id) || "") + html);
  }
  return { raizes, alvos: [...alvos] };
}

/**
 * Valor COMPLETO de cada `style="…"` de um arquivo do frontend, com a linha.
 * Trata `${ … }` como nível aninhado: o `[^"]*` de um `rg` para na primeira aspa
 * DENTRO do ternário, então `border:2px solid ${sel ? "#fff" : "transparent"}`
 * nunca mostrava o `#fff` no match — e o `rg` por LINHA nem casava, porque o
 * `style="` cai numa linha e o `${` em outra. Foi assim que quatro varreduras
 * anteriores deram esta categoria como limpa.
 * Isto é medição ESTÁTICA: prova que o CONSTRUTO não existe, não prova
 * comportamento. Anda ao lado do ΔE, nunca no lugar dele.
 */
export function estilosInline(arquivo) {
  const src = readFileSync(join(FRONTEND, arquivo), "utf8"), out = [];
  for (const m of src.matchAll(/style="/g)) {
    let i = m.index + m[0].length, prof = 0, val = "";
    while (i < src.length) {
      const c = src[i];
      if (c === "$" && src[i + 1] === "{") { prof++; val += "${"; i += 2; continue; }
      if (c === "}" && prof > 0) { prof--; val += "}"; i++; continue; }
      if (c === '"' && prof === 0) break;
      val += c; i++;
    }
    out.push({ linha: src.slice(0, m.index).split("\n").length, val });
  }
  return out;
}

const PSEUDO = /:(hover|focus-visible|focus|active)\b/g;

export async function medir(browser, ORIGIN, { claro, hover, esperar }) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  // O dashboard.js sem sessão TROCA o body inteiro por uma tela de erro e o
  // documento fica com ZERO `.modal` — medição de 0 achados em 0 elementos,
  // verde e vazia. Os scripts ficam de fora; o markup estático é o alvo.
  await page.route("**/*.js", (r) => r.abort());
  await page.route("**/*.js?*", (r) => r.abort());
  if (hover) await page.route("**/*.css*", (r) => {
    const arq = new URL(r.request().url()).pathname.replace(/^\//, "");
    r.fulfill({ contentType: "text/css",
                body: readFileSync(join(FRONTEND, arq), "utf8").replace(PSEUDO, ".pb-force-$1") });
  });
  await page.goto(`${ORIGIN}/dashboard.html`);
  await page.waitForSelector(".modal", { state: "attached" });
  // Sem isto a medição lê o tema ERRADO. O `body` tem
  // `transition: background .3s, color .3s` e vários componentes têm
  // `transition: all .2s`; ligar `body.light` e ler `getComputedStyle` no mesmo
  // `evaluate` devolve o valor INTERPOLADO, que no instante do flip ainda é o
  // do tema escuro. Medido nesta árvore, dentro de `.modal`: 167 de 466
  // elementos com `color` obsoleto e 145 com `border-top-color` obsoleto
  // (`background-color` não diverge porque a `.modal` não transiciona).
  // É o mesmo veneno do `forcePseudoState` que este harness já matou uma vez —
  // valor obsoleto lido como se fosse o do tema — e desligar a transição zera
  // os três (comando em `tests/frontend/modal_claro_veu.test.mjs`, caso
  // "sem transição pendente").
  await page.addStyleTag({
    content: "*,*::before,*::after{transition:none !important;animation:none !important}" });

  // Um passe por RAMO do ternário. A chave do alvo leva o prefixo do ramo para
  // que os dois sobrevivam no Map (mesmo id nos dois passes) e para o relatório
  // dizer de qual ramo veio o achado.
  const [A, B] = [fonteJs(true), fonteJs(false)];
  const raizes = [...A.raizes, ...B.raizes];
  const alvos = [...A.alvos.map(([k, v]) => [`T:${k}`, v]),
                 ...B.alvos.map(([k, v]) => [`F:${k}`, v])];
  assert.equal(raizes.length, 18,
    "recorte dos modais do dashboard.js mudou — reveja o extrator antes de confiar no verde");

  // Montagem e LEITURA em passos separados, para o caso-guarda poder esperar
  // entre os dois e provar que a leitura já é a final.
  const usados = await page.evaluate(([claro, hover, raizes, alvos]) => {
    if (claro) document.body.classList.add("light");
    for (const o of document.querySelectorAll(".overlay")) o.classList.add("open");
    const caixa = document.createElement("div");
    caixa.id = "__fonte_js";
    caixa.innerHTML = raizes.join("");
    document.body.append(caixa);
    const ids = [];
    for (const [chave, html] of alvos) {
      const alvo = document.getElementById(chave.slice(2));   // tira o `T:`/`F:`
      if (!alvo?.closest(".modal")) continue;      // só contêiner DENTRO de modal
      // Um invólucro por ramo, em vez de `innerHTML =`: o 2º passe sobrescreveria
      // o 1º, e sem invólucro os dois ramos ficariam IRMÃOS no mesmo pai — o que
      // move `:last-child`/`:nth-child` (24 seletores estruturais na folha, entre
      // eles `border-bottom:0`, que é propriedade que este harness mede).
      const inv = document.createElement("div");
      inv.innerHTML = html;
      alvo.append(inv);
      ids.push(chave);
    }
    if (hover) for (const el of document.querySelectorAll(".modal, .modal *"))
      el.classList.add("pb-force-hover", "pb-force-focus", "pb-force-focus-visible", "pb-force-active");
    return ids;
  }, [claro, hover, raizes, alvos]);

  if (esperar) await page.waitForTimeout(1200);

  const els = await page.evaluate(() => {
    const nome = (el) => el.tagName.toLowerCase() + (el.id ? `#${el.id}` : "") +
      [...el.classList].filter((c) => !c.startsWith("pb-force-")).map((c) => `.${c}`).join("");
    const pagina = getComputedStyle(document.body).backgroundColor;
    const out = [];
    [...document.querySelectorAll("*")].forEach((el, i) => {
      const m = el.closest(".modal");
      if (!m || m === el) return;
      const s = getComputedStyle(el);
      out.push({ i, nome: nome(el), superficie: getComputedStyle(m).backgroundColor, pagina,
                 background: s.backgroundColor, imagem: s.backgroundImage,
                 // repeat/position/size entram porque o shorthand `background:`
                 // os RESSETA mesmo quando a imagem é reposta por outra regra:
                 // a seta do <select> voltou a aparecer e passou a ladrilhar.
                 pintura: [s.backgroundRepeat, s.backgroundPosition, s.backgroundSize].join(" | "),
                 // `cor` não é usada por nenhum caso hoje. Ela é medida para o
                 // caso-guarda de transição: cor de texto é justamente a
                 // categoria das rodadas 1 e 2, e era ela que lia o tema errado.
                 cor: s.color,
                 border: parseFloat(s.borderTopWidth) > 0 ? s.borderTopColor : null,
                 // Os TRÊS lados que o `border` acima não vê: ele lê só
                 // `borderTopColor` e só com `borderTopWidth > 0`, então elemento
                 // com borda apenas embaixo — e a folha tem — era invisível POR
                 // CONSTRUÇÃO.
                 lados: ["Right", "Bottom", "Left"].reduce((o, L) =>
                   (parseFloat(s[`border${L}Width`]) > 0
                     ? { ...o, [L]: s[`border${L}Color`] } : o), {}),
                 // `fill`/`stroke` só existem em SVG: 3ª rodada do Codex, nunca
                 // teve enumerador nem constava das cegueiras.
                 svg: el.ownerSVGElement || el.tagName.toLowerCase() === "svg"
                   ? { fill: s.fill, stroke: s.stroke } : null });
    });
    return out;
  });
  const dados = { els, usados };

  await page.close();
  return dados;
}


export const rgba = (c) => {
  const n = (c || "").match(/[\d.]+/g)?.map(Number);
  return !n || n.length < 3 ? null : { r: n[0], g: n[1], b: n[2], a: n.length > 3 ? n[3] : 1 };
};
export const sobre = (f, t) => f.a >= 1 ? f : ({
  r: f.a * f.r + (1 - f.a) * t.r, g: f.a * f.g + (1 - f.a) * t.g, b: f.a * f.b + (1 - f.a) * t.b, a: 1 });
/** sRGB → CIELAB (D65). */
export const lab = ({ r, g, b }) => {
  const f = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  const [R, G, B] = [f(r), f(g), f(b)];
  const k = (t) => t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116;
  const X = k((R * .4124564 + G * .3575761 + B * .1804375) / 0.95047);
  const Y = k(R * .2126729 + G * .7151522 + B * .0721750);
  const Z = k((R * .0193339 + G * .1191920 + B * .9503041) / 1.08883);
  return { L: 116 * Y - 16, A: 500 * (X - Y), B: 200 * (Y - Z) };
};
export const dE = (p, q) => Math.hypot(p.L - q.L, p.A - q.A, p.B - q.B);
/** Superfície EFETIVA do `.modal` ancestral, e ΔE de uma tinta contra ela. */
export const sup = (e) => sobre(rgba(e.superficie), rgba(e.pagina));
export const dSup = (tinta, s) => dE(lab(sobre(tinta, s)), lab(s));
