/**
 * Todo handler inline (`onclick="foo()"`) resolve no escopo global?
 *
 * O bug que isto pega: o script vira módulo ou ganha IIFE, os nomes saem do escopo
 * global, e o clique passa a falhar EM SILÊNCIO — sem erro visível, sem teste
 * vermelho. É a armadilha que o `docs/armadilhas.md` descreve.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * O NAVEGADOR É A AUTORIDADE DE HTML. Esta é a decisão de desenho central, e ela
 * vem de uma medição: a versão anterior deste teste analisava o HTML por conta
 * própria e acumulou 29 apontamentos de revisão em 17 commits — 28 deles nessa
 * metade. Forma de valor de atributo, referência de caractere, comentário,
 * `data-onclick` × `onclick`, quais `<script src>` a página carrega: tudo isso é
 * um parser de HTML sendo reescrito à mão, e sempre faltava um caso.
 *
 * Aqui não há parser próprio de HTML. Os handlers saem do DOM, já decodificados,
 * por duas vias que usam o MESMO parser do navegador:
 *
 *   1. a página, por navegação real;
 *   2. o markup GERADO, jogando os trechos de template literal do `.js` num
 *      `innerHTML` descartável — assim não é preciso ter dado que faça a página
 *      renderizar aqueles controles.
 *
 * Sobra de análise estática só o mínimo que o navegador não pode fazer por mim:
 * achar os template literals dentro do `.js` e neutralizar `${...}`. E ali, falha
 * ambígua é SEGURA: o trecho é descartado e contabilizado, nunca adivinhado.
 * ────────────────────────────────────────────────────────────────────────────
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { join, dirname, basename } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import { chromium } from "playwright";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const FRONTEND = join(REPO, "frontend");
const BASELINE = join(REPO, "tests", "frontend", "handlers_inline.baseline.json");
const PORT = Number(process.env.PB_HANDLERS_TEST_PORT || 8911);
const ORIGIN = `http://127.0.0.1:${PORT}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Páginas públicas: com sessão fingida elas redirecionam para dentro do app. */
const SEM_SESSAO = new Set(["login.html", "cadastro.html", "index.html", "precos.html"]);

// ───────────────────────── análise estática, o mínimo ─────────────────────────

/**
 * As strings literais de um `.js`, com `${...}` neutralizado.
 *
 * É a ÚNICA análise de texto que sobrou, e o motivo é que o navegador não tem como
 * fazê-la: o markup gerado só existe como string dentro do JavaScript.
 *
 * O escopo é deliberadamente raso — achar aspa, contar chave — e o resultado vai
 * para o parser do navegador, não para um extrator meu. Nada aqui interpreta HTML.
 *
 * São as TRÊS aspas, não só a crase: o `precos.html` monta
 * `'<button onclick="closeChangeModal()">'` numa string comum. E a interpolação é
 * percorrida por dentro, porque `${it.x ? \`onclick="navigateToFilter(…)"\` : ""}`
 * põe markup dentro dela — o conteúdo da interpolação não vira texto do markup de
 * fora, mas um template literal aninhado ali é markup por direito próprio.
 *
 * FALHA AMBÍGUA É SEGURA. Quando o scanner não consegue terminar com confiança
 * (crase sem fechamento, interpolação sem fechamento, comentário de bloco aberto),
 * ele NÃO adivinha: para, e devolve o motivo em `perdidos`. Perder cobertura é o
 * lado seguro de errar; inventar nome reprova código correto. E a perda não fica
 * silenciosa — a comparação com a baseline, nome a nome, fica vermelha.
 */
export function markupDe(js) {
  const trechos = [];
  const dados = [];
  const perdidos = [];
  let i = 0;

  while (i < js.length) {
    const c = js[i], d = js[i + 1];

    if (c === "/" && d === "*") {
      const fim = js.indexOf("*/", i + 2);
      if (fim < 0) { perdidos.push("comentário de bloco sem fechamento"); break; }
      i = fim + 2; continue;
    }
    if (c === "/" && d === "/") {
      const fim = js.indexOf("\n", i);
      i = fim < 0 ? js.length : fim + 1; continue;
    }
    if (c === "/" && ehRegex(js, i)) {
      const fim = fimDeRegex(js, i);
      if (fim < 0) { perdidos.push("literal de expressão regular sem fechamento"); break; }
      i = fim + 1; continue;
    }
    if (c === '"' || c === "'") {
      const fim = fimDeString(js, i, c);
      if (fim < 0) { perdidos.push(`string ${c} sem fechamento`); break; }
      const texto = js.slice(i + 1, fim);
      trechos.push(texto);
      if (ehHandlerEmDado(js, i)) dados.push(texto);
      i = fim + 1; continue;
    }
    if (c === "`") {
      const r = leTemplate(js, i);
      if (!r) { perdidos.push("template literal sem fechamento"); break; }
      trechos.push(r.texto, ...r.aninhados);
      if (ehHandlerEmDado(js, i)) dados.push(r.texto);
      i = r.fim + 1; continue;
    }
    i++;
  }
  return { trechos, dados, perdidos };
}

/**
 * `/` inicia expressão regular ou é divisão?
 *
 * É a ambiguidade clássica do léxico de JavaScript, e ela importa aqui porque
 * `.replace(/[<>&"]/g, …)` tem uma ASPA dentro da classe de caracteres — sem esta
 * decisão, aquela aspa vira início de string e engole o template literal seguinte.
 * Foi o primeiro erro real que a baseline pegou.
 *
 * A heurística é a usual: depois de valor (identificador, número, `)`, `]`) o `/`
 * é divisão; depois de operador ou pontuação, é regex.
 *
 * COM UMA EXCEÇÃO QUE NÃO É OPCIONAL: palavra-chave termina em letra, e `return
 * /^x/.test(v)` é JavaScript comuníssimo. Tratá-la como valor faria o `/` virar
 * divisão, a regex virar texto, e uma aspa lá dentro abrir uma string falsa — que é
 * como o extrator INVENTARIA um handler. Falso positivo, a classe pior.
 *
 * Ainda erra em construção exótica (`a = b /c/ d`), e é por isso que a perda não é
 * silenciosa — a baseline nome a nome fica vermelha.
 */
const PALAVRA_OPERADOR = new Set([
  "return", "typeof", "case", "in", "of", "new", "delete", "void", "do", "else",
  "yield", "await", "instanceof", "throw",
]);
/** `if (x) /re/.test(y)`: o `)` fecha CABEÇALHO, não chamada — logo, operador. */
const CABECALHO = new Set(["if", "for", "while", "switch", "catch", "with"]);

/** A palavra que termina em `j`, ou "". */
function palavraAte(s, j) {
  let k = j;
  while (k >= 0 && /[\w$]/.test(s[k])) k--;
  return s.slice(k + 1, j + 1);
}

function ehRegex(s, i) {
  let j = i - 1;
  while (j >= 0 && /\s/.test(s[j])) j--;
  if (j < 0) return true;
  if (!/[\w$)\]]/.test(s[j])) return true;
  if (s[j] === ")") {
    // Volta até o `(` que casa, e pergunta o que vinha antes dele.
    let prof = 0, k = j;
    for (; k >= 0; k--) {
      if (s[k] === ")") prof++;
      else if (s[k] === "(" && --prof === 0) break;
    }
    if (k < 0) return false;
    let m = k - 1;
    while (m >= 0 && /\s/.test(s[m])) m--;
    return CABECALHO.has(palavraAte(s, m));
  }
  if (s[j] === "]") return false;
  return PALAVRA_OPERADOR.has(palavraAte(s, j));
}

/** Fim do literal de regex: `/` não escapada e fora de `[...]`. */
function fimDeRegex(s, i) {
  let classe = false;
  for (let j = i + 1; j < s.length; j++) {
    const c = s[j];
    if (c === "\\") { j++; continue; }
    if (c === "\n") return -1;
    if (c === "[") classe = true;
    else if (c === "]") classe = false;
    else if (c === "/" && !classe) return j;
  }
  return -1;
}

/** Índice da aspa de fechamento, ou -1. A barra invertida consome o próximo. */
function fimDeString(s, i, aspa) {
  for (let j = i + 1; j < s.length; j++) {
    if (s[j] === "\\") { j++; continue; }
    if (s[j] === aspa) return j;
    if (s[j] === "\n" && aspa !== "`") return -1;   // string comum não cruza linha
  }
  return -1;
}

/**
 * Lê um template literal a partir da crase de abertura.
 *
 * `${...}` sai do texto — o que roda ali é a GERAÇÃO do markup, no escopo de quem
 * gera, não o clique. Contar a chave respeitando string de dentro, senão um `{`
 * dentro de string desequilibra a conta e a interpolação vaza para o markup.
 */
function leTemplate(s, i) {
  let texto = "";
  const aninhados = [];
  let j = i + 1;
  while (j < s.length) {
    const c = s[j];
    if (c === "\\") { texto += s[j + 1] ?? ""; j += 2; continue; }
    if (c === "`") return { texto, aninhados, fim: j };
    if (c === "$" && s[j + 1] === "{") {
      const fim = fimDaInterpolacao(s, j + 2);
      if (fim < 0) return null;                    // ambíguo -> não adivinha
      // O conteúdo da interpolação NÃO entra no markup de fora (roda na geração),
      // mas string literal lá dentro pode ser markup — então recorre.
      const dentro = markupDe(s.slice(j + 2, fim));
      if (dentro.perdidos.length) return null;
      aninhados.push(...dentro.trechos);
      j = fim + 1; continue;
    }
    texto += c; j++;
  }
  return null;
}

/** Índice da `}` que fecha a interpolação, respeitando aninhamento e string. */
function fimDaInterpolacao(s, i) {
  let prof = 1;
  for (let j = i; j < s.length; j++) {
    const c = s[j];
    if (c === "\\") { j++; continue; }
    if (c === '"' || c === "'" || c === "`") {
      const fim = fimDeString(s, j, c);
      if (fim < 0) return -1;
      j = fim; continue;
    }
    if (c === "{") prof++;
    else if (c === "}" && --prof === 0) return j;
  }
  return -1;
}

// ────────────────── identificadores do valor JÁ decodificado ──────────────────

/**
 * O que o handler chama e o que ele lê.
 *
 * O insumo aqui é o valor que o NAVEGADOR entregou: sem entidade, sem
 * interpolação, sem questão de delimitador de atributo. Sobra JavaScript.
 */
const NATIVO = new Set([
  "if", "for", "while", "switch", "return", "typeof", "new", "function", "catch",
  "alert", "confirm", "prompt", "parseInt", "parseFloat", "Number", "String",
  "Boolean", "Array", "Object", "JSON", "Math", "Date", "RegExp", "Promise",
  "document", "window", "setTimeout", "setInterval", "encodeURIComponent",
  "decodeURIComponent", "console", "event", "this", "void", "delete", "require",
]);
const NAO_E_NOME = new Set(["this", "event", "true", "false", "null", "undefined"]);
const SO_NOME = /^[A-Za-z_$][\w$]*$/;

/**
 * Nome declarado DENTRO do próprio handler não é global.
 *
 * `onclick="const value = event.target.value; submit(value)"` resolve `value`
 * sozinho, e exigi-lo no escopo global reprovaria handler correto — falso positivo.
 *
 * TETO DECLARADO: pega `const`/`let`/`var` e parâmetro de função/arrow, que é o que
 * cabe num atributo. Desestruturação (`const {a} = x`) fica de fora, e o efeito aí é
 * cobrar a mais — por isso a lista é generosa: qualquer nome que apareça depois de
 * uma dessas palavras sai da conta.
 */
const DECLARACAO_LOCAL = /\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)|\bfunction\s*[A-Za-z_$\w$]*\s*\(([^)]*)\)|\(([^)]*)\)\s*=>|\b([A-Za-z_$][\w$]*)\s*=>/g;

function locaisDe(v) {
  const fora = new Set();
  for (const m of v.matchAll(DECLARACAO_LOCAL)) {
    for (const parte of [m[1], m[2], m[3], m[4]]) {
      if (!parte) continue;
      for (const tok of parte.split(",")) {
        const n = tok.trim().replace(/[=:].*$/, "").trim();
        if (SO_NOME.test(n)) fora.add(n);
      }
    }
  }
  return fora;
}

/**
 * Esvazia STRING e LITERAL DE REGEX antes de procurar chamada.
 *
 * String: `'Use 4 números (ou vazio)'` rendia o identificador `meros`, porque o `\w`
 * do JavaScript é ASCII e o `ú` corta o nome.
 *
 * Regex: `onclick="return /fake()/.test(v)"` é handler válido, e o `fake` lá dentro
 * NUNCA é chamado — cobrá-lo é inventar nome, a classe que reprova código correto.
 * Um `replace` por vez não resolve, porque uma barra dentro de string e uma aspa
 * dentro de regex se confundem; é uma passada só, com estado.
 */
function semLiterais(s) {
  let out = "";
  for (let i = 0; i < s.length; ) {
    const c = s[i];
    if (c === '"' || c === "'" || c === "`") {
      const fim = fimDeString(s, i, c);
      if (fim < 0) return out;                 // literal aberto: para, não adivinha
      out += c + c; i = fim + 1; continue;
    }
    if (c === "/" && ehRegex(s, i)) {
      const fim = fimDeRegex(s, i);
      if (fim < 0) return out;
      out += "/x/"; i = fim + 1; continue;
    }
    out += c; i++;
  }
  return out;
}

// `?.` entre o nome e o parêntese: `openDialog?.()` chama `openDialog` do mesmo
// jeito, e some do escopo global do mesmo jeito. O lookbehind continua barrando
// membro (`a?.b()` -> `b` vem depois de `.`, e não é global).
const CHAMADA = /(?<![.\w$])([A-Za-z_$][\w$]*)\s*(?:\?\.)?\s*\(/g;
/** `window.X()` É global; `this.x()` e `parcelas.find()` não são. */
const CHAMADA_WINDOW = /\bwindow\??\.([A-Za-z_$][\w$]*)\s*(?:\?\.)?\s*\(/g;

/**
 * A RAIZ de uma chamada de membro precisa existir.
 *
 * Em `onclick="dialogs?.open()"` nenhum dos dois nomes entrava: `dialogs` não é
 * seguido de `(`, e `open` vem depois de ponto. Mas o clique avalia `dialogs` no
 * escopo global, e sem ele é `ReferenceError` — o encadeamento opcional protege a
 * propriedade, nunca a raiz.
 *
 * Entra como VARIÁVEL, não função: `dialogs` só precisa existir. Os receptores
 * nativos (`this`, `event`, `JSON`, `Math`, `document`, `window`) já caem nas listas.
 */
const RAIZ_DE_MEMBRO = /(?<![.\w$])([A-Za-z_$][\w$]*)\s*\??\.\s*[A-Za-z_$][\w$]*\s*(?:\?\.)?\s*\(/g;

/**
 * Toda lista de argumento, em todo nível — regex só casa a mais interna.
 *
 * TETO DECLARADO, e escolhido com medição: só o argumento que é um identificador
 * INTEIRO entra. `submit(missing + 1)` não rende `missing`, e isso é cobertura
 * perdida de propósito.
 *
 * O motivo é que a alternativa é pior. Medi o que "todo identificador da expressão"
 * exigiria a mais neste frontend, já com string esvaziada e declaração local
 * excluída: **8 nomes, e os 8 são chave de objeto literal** —
 * `updateNotificationSettings({ daily_report_enabled: this.checked })` e irmãos no
 * settings.html. Cobrá-los reprovaria 8 handlers CORRETOS.
 *
 * Distinguir chave de referência exige análise sintática de verdade, e errar ali
 * produz nome inventado — a classe que trava PR legítimo. Perder `missing` só reduz
 * cobertura. Entre as duas, esta é a falha certa a escolher.
 */
function argumentosDe(t) {
  const fora = [], pilha = [];
  let seg = "";
  for (const c of t) {
    if (c === "(") { pilha.push([]); seg = ""; continue; }
    if (c === ")") {
      if (!pilha.length) { seg = ""; continue; }
      const lista = pilha.pop();
      lista.push(seg);
      fora.push(...lista);
      seg = ""; continue;
    }
    if (c === "," && pilha.length) { pilha[pilha.length - 1].push(seg); seg = ""; continue; }
    seg += c;
  }
  return fora;
}

/**
 * Este literal é um handler guardado como DADO?
 *
 * O `admin-dashboard.html` guarda `{ click: "openUsersModal(event)" }` numa lista de
 * cards e injeta o valor num `onclick=` só na hora de renderizar. Sem executar o
 * código com dado real não há DOM onde ler isso — é a única coisa que sobrou
 * precisando de reconhecimento de forma no texto.
 *
 * A pergunta é feita DE DENTRO do `markupDe`, no índice onde um literal de verdade
 * começa, e essa é a diferença que importa: comentário, expressão regular e string
 * aninhada já foram puladas pela varredura, então `// click: "inventado()"` nunca
 * chega aqui. Rodar um regex sobre o texto cru cobraria os três.
 */
const ANTES_DE_HANDLER = /(?:\bclick|\.on[a-z]+)\s*[:=]\s*$/i;

const ehHandlerEmDado = (js, i) =>
  ANTES_DE_HANDLER.test(js.slice(Math.max(0, i - 48), i));

export function nomesDe(valores) {
  const funcoes = new Set(), variaveis = new Set();
  for (const v of valores) {
    const limpo = semLiterais(v);
    for (const m of limpo.matchAll(CHAMADA)) if (!NATIVO.has(m[1])) funcoes.add(m[1]);
    for (const m of limpo.matchAll(CHAMADA_WINDOW)) if (!NATIVO.has(m[1])) funcoes.add(m[1]);
    for (const m of limpo.matchAll(RAIZ_DE_MEMBRO)) {
      if (!NATIVO.has(m[1]) && !NAO_E_NOME.has(m[1])) variaveis.add(m[1]);
    }
    const locais = locaisDe(limpo);
    for (const tok of argumentosDe(limpo)) {
      const n = tok.trim();
      if (SO_NOME.test(n) && !NAO_E_NOME.has(n) && !NATIVO.has(n) && !locais.has(n)) {
        variaveis.add(n);
      }
    }
  }
  return { funcoes, variaveis };
}

// ───────────────────────────── navegador ─────────────────────────────

/** Lê `on*` do DOM. Roda tanto na página quanto num fragmento descartável. */
const LE_HANDLERS = `(raiz) => {
  const out = [];
  for (const el of raiz.querySelectorAll("*")) {
    for (const a of el.attributes) if (/^on[a-z]+$/i.test(a.name)) out.push(a.value);
  }
  return out;
}`;

async function startServer() {
  const p = spawn("python3", ["-m", "http.server", String(PORT)], { cwd: FRONTEND, stdio: "ignore" });
  for (let i = 0; i < 40; i++) {
    await sleep(100);
    try { await fetch(`${ORIGIN}/login.html`); return p; } catch { /* subindo */ }
  }
  p.kill();
  throw new Error(`http.server não subiu na porta ${PORT} (ocupada?)`);
}

async function comStubs(page, pagina) {
  const autenticado = !SEM_SESSAO.has(pagina);
  await page.route("**/auth/**", (r) => r.fulfill({
    status: autenticado ? 200 : 401,
    contentType: "application/json",
    body: JSON.stringify(autenticado
      ? { ok: true, valid: true, authenticated: true, user_id: 1, user: { id: 1, name: "t", email: "t@t" } }
      : { detail: "sem sessão" }),
  }));
  await page.route("**/cdn.pluggy.ai/**", (r) => r.fulfill({
    status: 200, contentType: "application/javascript", body: "window.PluggyConnect=function(){};",
  }));
  await page.route("**/api/**", (r) => r.fulfill({ status: 200, contentType: "application/json", body: "{}" }));
}

let server, browser;
before(async () => { server = await startServer(); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

/**
 * Uma navegação por página, não duas.
 *
 * Os dois testes de cada página precisam da MESMA página carregada: um compara o
 * levantamento com a baseline, o outro pergunta `typeof` no escopo dela. Carregar
 * duas vezes dobra o trabalho de um arquivo que já é o mais pesado da suíte — e
 * medido, isso derrubava vizinhos: 3/3 execuções limpas sem este arquivo contra
 * 4/5 com ele, com as falhas sempre em `http.server` de OUTRO arquivo não subindo.
 * As páginas ficam abertas até o `after`, que fecha o navegador inteiro.
 */
const cache = new Map();
const levantar = (pagina) => {
  if (!cache.has(pagina)) cache.set(pagina, levantarUmaVez(pagina));
  return cache.get(pagina);
};

/**
 * Tudo que uma página exige do escopo global, com o navegador parseando o HTML.
 *
 * `document.scripts` é a lista que o navegador REALMENTE carregou — não uma
 * varredura minha de `<script src>`. Comentado, `data-src`, sem aspas, com espaço
 * em volta do `=`: não importa, quem responde é ele.
 */
/**
 * Handlers de um conjunto de fontes JavaScript, com o navegador parseando.
 *
 * Compartilhado entre o levantamento COM navegação (que usa `document.scripts` para
 * saber o que foi carregado) e o SEM navegação (que usa o `DOMParser` sobre o
 * arquivo cru). Os dois precisam do mesmo tratamento; ter duas cópias seria a
 * duplicação que o §0.7 proíbe.
 */
async function handlersDoMarkup(page, fontes, perdidos) {
  const out = [];
  for (const [nome, js] of fontes) {
    const { trechos, dados, perdidos: p } = markupDe(js);
    for (const m of p) perdidos.push(`${nome}: ${m}`);
    out.push(...dados);
    // Condição NECESSÁRIA para haver handler, não um parser: trecho sem `on…=`
    // não tem como conter atributo de evento. Corta ~5200 strings do dashboard.js
    // para dezenas, e o custo importa — sem isto o arquivo compete por CPU com os
    // outros testes e derruba os vizinhos. Se o filtro errar, perde-se cobertura e
    // a baseline acusa; ele não pode inventar nada.
    const candidatos = trechos.filter((t) => /on[a-z]+\s*=/i.test(t));
    out.push(...await page.evaluate(
      ([html, ler]) => {
        const f = new Function("return " + ler)();
        const t = document.createElement("template");
        const o = [];
        for (const frag of html) {
          // `<template>` e não `innerHTML` de body: o parser aceita `<tr>` solto,
          // que fora de `<table>` seria descartado. E DUAS leituras, porque metade
          // dos trechos gerados é fragmento de ATRIBUTO, sem tag nenhuma
          // (` style="…" onclick="openHistoryDetail(1)"`) — envolver num elemento
          // é o que faz o parser enxergá-los como atributos.
          t.innerHTML = frag;
          o.push(...f(t.content));
          t.innerHTML = `<i ${frag}></i>`;
          o.push(...f(t.content));
        }
        return o;
      }, [candidatos, LE_HANDLERS]));
  }
  return out;
}

/**
 * O mesmo levantamento, mas SEM navegar — só do texto do arquivo.
 *
 * Existe para a varredura de cobertura: são 26 páginas em `frontend/` e a baseline
 * cobre 7, então as outras 19 precisam ser olhadas de algum jeito, e navegar em
 * todas custaria caro demais. Aqui o `DOMParser` faz o papel do navegador — inclusive
 * entregando `doc.scripts`, que dá os `<script>` inline e os `src` sem eu parsear tag.
 */
async function levantarDoTexto(page, pagina) {
  const cru = readFileSync(join(FRONTEND, pagina), "utf-8");
  const { doDom, srcs, inline } = await page.evaluate(
    ([html, ler]) => {
      const f = new Function("return " + ler)();
      const doc = new DOMParser().parseFromString(html, "text/html");
      return {
        doDom: f(doc),
        srcs: [...doc.scripts].filter((s) => s.getAttribute("src")).map((s) => s.getAttribute("src")),
        inline: [...doc.scripts].filter((s) => !s.getAttribute("src")).map((s) => s.textContent),
      };
    }, [cru, LE_HANDLERS]);

  const fontes = [
    ...srcs
      .map((c) => [basename(c), join(FRONTEND, c.replace(/^\//, "").replace(/\?.*$/, ""))])
      .filter(([, arq]) => existsSync(arq))
      .map(([nome, arq]) => [nome, readFileSync(arq, "utf-8")]),
    ...inline.map((txt, n) => [`${pagina} <script> #${n + 1}`, txt]),
  ];
  const perdidos = [];
  const doMarkup = await handlersDoMarkup(page, fontes, perdidos);
  const { funcoes, variaveis } = nomesDe([...doDom, ...doMarkup]);
  return { funcoes: [...funcoes], variaveis: [...variaveis], perdidos };
}

async function levantarUmaVez(pagina) {
  const page = await browser.newPage();
  const perdidos = [];
  try {
    await comStubs(page, pagina);
    await page.goto(`${ORIGIN}/${pagina}`, { waitUntil: "load" }).catch(() => {});
    await sleep(700);

    const url = page.url();
    assert.ok(url.includes(pagina), `${pagina} navegou para ${url.replace(ORIGIN, "")} — mediria outra coisa`);

    // O DOM VIVO não basta: o boot com stubs `{}` troca o innerHTML de containers e
    // DESTRÓI markup estático (o `toggleHideBalance` do home.html some assim). Então
    // o arquivo cru também vai para o parser do navegador, via DOMParser — que é o
    // mesmo motor, sem executar script e sem sofrer mutação.
    const cru = readFileSync(join(FRONTEND, pagina), "utf-8");
    const doDom = await page.evaluate(
      ([html, ler]) => {
        const f = new Function("return " + ler)();
        const doc = new DOMParser().parseFromString(html, "text/html");
        return [...f(document), ...f(doc)];
      }, [cru, LE_HANDLERS]);
    // O navegador entrega as duas coisas: quais arquivos ele CARREGOU, e o texto
    // dos `<script>` inline. Nos dois casos sem eu parsear tag nenhuma — foi assim
    // que a categoria "descoberta de <script src>" deixou de existir. E o inline
    // importa: os 15 handlers do admin-dashboard nascem num `<script>` da página.
    const { scripts, inline } = await page.evaluate(() => ({
      scripts: [...document.scripts].filter((s) => s.src).map((s) => new URL(s.src).pathname),
      inline: [...document.scripts].filter((s) => !s.src).map((s) => s.text),
    }));

    const fontes = [
      ...scripts
        .map((c) => [basename(c), join(FRONTEND, c.replace(/^\//, ""))])
        .filter(([, arq]) => existsSync(arq))
        .map(([nome, arq]) => [nome, readFileSync(arq, "utf-8")]),
      ...inline.map((txt, n) => [`${pagina} <script> #${n + 1}`, txt]),
    ];

    const doMarkup = await handlersDoMarkup(page, fontes, perdidos);
    const { funcoes, variaveis } = nomesDe([...doDom, ...doMarkup]);
    return {
      page,
      funcoes: [...funcoes].sort(),
      variaveis: [...variaveis].filter((v) => !funcoes.has(v)).sort(),
      scripts, perdidos,
    };
  } catch (e) {
    await page.close();
    throw e;
  }
}

const PAGINAS = readdirSync(FRONTEND).filter((f) => f.endsWith(".html")).sort();
const baseline = JSON.parse(readFileSync(BASELINE, "utf-8"));

/**
 * Toda página com handler inline está na baseline?
 *
 * A pergunta invertida é a que importa. Conferir só que as chaves da baseline ainda
 * existem deixa uma página NOVA entrar sem entrada, e aí ela nunca é navegada nem
 * inspecionada — o handler não resolvido dela passa em silêncio, que é exatamente o
 * bug que este arquivo existe para pegar.
 *
 * São 26 páginas em `frontend/` e a baseline cobre 7; navegar em todas custaria caro
 * demais, então aqui o levantamento é feito só do TEXTO, com o `DOMParser` no papel
 * do navegador. Mais barato e suficiente: se a página tem nome a cobrar, ela precisa
 * estar na baseline — quem decide entrar é a revisão, não o silêncio.
 */
test("toda página com handler inline está na baseline", async () => {
  for (const pagina of Object.keys(baseline)) {
    assert.ok(PAGINAS.includes(pagina), `a baseline cita ${pagina}, que não existe mais em frontend/`);
  }
  const page = await browser.newPage();
  try {
    const fora = [];
    for (const pagina of PAGINAS) {
      if (baseline[pagina]) continue;
      const r = await levantarDoTexto(page, pagina);
      // O fail-safe vale aqui também: trecho que o scanner desistiu de ler pode ser
      // justamente o que tinha o handler. Zero nomes COM perda declarada não é
      // "página sem handler" — é página não medida, e deixá-la fora da baseline em
      // silêncio anularia a garantia anunciada.
      assert.deepEqual(r.perdidos, [],
        `${pagina}: o scanner desistiu de um trecho (${r.perdidos.join("; ")}), então ` +
        "não dá para afirmar que ela não tem handler inline. Conserte o scanner antes " +
        "de deixá-la fora da baseline.");
      if (r.funcoes.length || r.variaveis.length) {
        fora.push(`${pagina} (${r.funcoes.length}f/${r.variaveis.length}v: ${[...r.funcoes, ...r.variaveis].slice(0, 5).join(", ")}…)`);
      }
    }
    assert.deepEqual(fora, [],
      `${fora.length} página(s) têm handler inline e não estão na baseline: ${fora.join("; ")}. ` +
      "Sem entrada na baseline elas não são navegadas nem verificadas, e um handler " +
      "quebrado nelas passaria verde. Adicione-as a " +
      "tests/frontend/handlers_inline.baseline.json.");
  } finally {
    await page.close();
  }
});

for (const pagina of Object.keys(baseline)) {
  const esperado = baseline[pagina];

  test(`${pagina}: o levantamento não encolheu, e o que cresceu está explicado`, async () => {
    const r = await levantar(pagina);
    {
      assert.deepEqual(r.perdidos, [],
        `o scanner de template literal desistiu de um trecho: ${r.perdidos.join("; ")}. ` +
        "Isso é perda de cobertura declarada, não silenciosa — conserte o scanner ou " +
        "documente o trecho, mas não deixe passar como se estivesse coberto.");
      assert.ok(r.scripts.length > 0, `${pagina} carregou 0 scripts — nada a medir`);

      // 1. NENHUM nome da baseline pode sumir. Este é o critério, não a contagem.
      const sumiram = [
        ...esperado.funcoes.filter((n) => !r.funcoes.includes(n)),
        ...esperado.variaveis.filter((n) => !r.variaveis.includes(n) && !r.funcoes.includes(n)),
      ];
      assert.deepEqual(sumiram, [],
        `${pagina}: ${sumiram.length} nome(s) da baseline sumiram do levantamento: ` +
        `${sumiram.join(", ")}. O gate encolheu sem ninguém pedir — é exatamente o ` +
        "falso negativo silencioso que a baseline existe para pegar.");

      // 2. Nome NOVO também para o teste: tem de ser visto e justificado no diff.
      const novos = [
        ...r.funcoes.filter((n) => !esperado.funcoes.includes(n)),
        ...r.variaveis.filter((n) => !esperado.variaveis.includes(n)),
      ];
      assert.deepEqual(novos, [],
        `${pagina}: ${novos.length} nome(s) fora da baseline: ${novos.join(", ")}. ` +
        "Pode ser cobertura nova legítima — mas passa pela revisão, não em silêncio. " +
        "Atualize tests/frontend/handlers_inline.baseline.json explicando cada um.");
    }
  });

  test(`${pagina}: todo handler inline resolve no escopo global`, async () => {
    const r = await levantar(pagina);
    {
      // `typeof <nome>`, não `window[nome]`: const/let no topo de script clássico
      // não viram propriedade de window, mas o handler inline os enxerga.
      const faltando = await r.page.evaluate(
        ([fns, vars]) => {
          const resolve = (n, exigeFuncao) => {
            try {
              const t = new Function(`return typeof ${n}`)();
              return exigeFuncao ? t === "function" : t !== "undefined";
            } catch { return false; }
          };
          return [
            ...fns.filter((n) => !resolve(n, true)),
            ...vars.filter((n) => !resolve(n, false)).map((n) => `${n} (argumento)`),
          ];
        },
        [r.funcoes, r.variaveis],
      );
      assert.deepEqual(faltando, [],
        `${pagina}: de ${r.funcoes.length} funções e ${r.variaveis.length} argumentos ` +
        `verificados, estes não existem no escopo global: ${faltando.join(", ")}. ` +
        "O clique falha em silêncio. Se o script virou módulo ou ganhou IIFE, " +
        "devolva os nomes ao escopo global.");
    }
  });
}
