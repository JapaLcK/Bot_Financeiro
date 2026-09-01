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
      trechos.push(js.slice(i + 1, fim));
      i = fim + 1; continue;
    }
    if (c === "`") {
      const r = leTemplate(js, i);
      if (!r) { perdidos.push("template literal sem fechamento"); break; }
      trechos.push(r.texto, ...r.aninhados);
      i = r.fim + 1; continue;
    }
    i++;
  }
  return { trechos, perdidos };
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
 * é divisão; depois de operador ou pontuação, é regex. Ela erra em construção
 * exótica (`a = b /c/ d`), e é por isso que a perda não é silenciosa — a baseline
 * nome a nome fica vermelha.
 */
function ehRegex(s, i) {
  let j = i - 1;
  while (j >= 0 && /\s/.test(s[j])) j--;
  if (j < 0) return true;
  return !/[\w$)\]]/.test(s[j]);
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

/** Esvazia string antes de procurar chamada: `'4 números (ou vazio)'` rendia `meros`. */
const semStrings = (s) => s
  .replace(/'(?:\\.|[^'\\])*'/g, "''")
  .replace(/"(?:\\.|[^"\\])*"/g, '""')
  .replace(/`(?:\\.|[^`\\])*`/g, "``");

const CHAMADA = /(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(/g;
/** `window.X()` É global; `this.x()` e `parcelas.find()` não são. */
const CHAMADA_WINDOW = /\bwindow\.([A-Za-z_$][\w$]*)\s*\(/g;

/** Toda lista de argumento, em todo nível — regex só casa a mais interna. */
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
 * Handler que só existe como DADO, e por isso o navegador não pode vê-lo.
 *
 * O `admin-dashboard.html` guarda `{ click: "openUsersModal(event)" }` numa lista de
 * cards e injeta esse valor num `onclick=` só na hora de renderizar. Sem executar o
 * código com dado real, não há DOM onde ler isso — é a única coisa que sobrou
 * precisando de reconhecimento de forma no texto.
 *
 * O modo de falha é DEIXAR DE ACHAR, nunca inventar: se a forma mudar, o nome some
 * do levantamento e a baseline fica vermelha nomeando-o.
 */
const HANDLER_EM_DADO = /(?:\bclick|\.on[a-z]+)\s*[:=]\s*(["'`])((?:\\.|(?!\1)[\s\S])*)\1/gi;

export const handlersEmDado = (js) => [...js.matchAll(HANDLER_EM_DADO)].map((m) => m[2]);

export function nomesDe(valores) {
  const funcoes = new Set(), variaveis = new Set();
  for (const v of valores) {
    const limpo = semStrings(v);
    for (const m of limpo.matchAll(CHAMADA)) if (!NATIVO.has(m[1])) funcoes.add(m[1]);
    for (const m of limpo.matchAll(CHAMADA_WINDOW)) if (!NATIVO.has(m[1])) funcoes.add(m[1]);
    for (const tok of argumentosDe(limpo)) {
      const n = tok.trim();
      if (SO_NOME.test(n) && !NAO_E_NOME.has(n) && !NATIVO.has(n)) variaveis.add(n);
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

    const doMarkup = [];
    for (const [nome, js] of fontes) {
      const { trechos, perdidos: p } = markupDe(js);
      for (const m of p) perdidos.push(`${nome}: ${m}`);
      doMarkup.push(...handlersEmDado(js));
      // Condição NECESSÁRIA para haver handler, não um parser: trecho sem `on…=`
      // não tem como conter atributo de evento. Corta ~5200 strings do dashboard.js
      // para dezenas, e o custo importa — sem isto o arquivo compete por CPU com os
      // outros testes e derruba os vizinhos. Se o filtro errar, perde-se cobertura e
      // a baseline acusa; ele não pode inventar nada.
      const candidatos = trechos.filter((t) => /on[a-z]+\s*=/i.test(t));
      // O parser do navegador, no markup gerado — mesmo motor, sem precisar de dado.
      doMarkup.push(...await page.evaluate(
        ([html, ler]) => {
          const f = new Function("return " + ler)();
          const t = document.createElement("template");
          const out = [];
          for (const frag of html) {
            // `<template>` e não `innerHTML` de body: o parser aceita `<tr>` solto,
            // que fora de `<table>` seria descartado. E DUAS leituras, porque metade
            // dos trechos gerados é fragmento de ATRIBUTO, sem tag nenhuma
            // (` style="…" onclick="openHistoryDetail(1)"`) — envolver num elemento
            // é o que faz o parser enxergá-los como atributos.
            t.innerHTML = frag;
            out.push(...f(t.content));
            t.innerHTML = `<i ${frag}></i>`;
            out.push(...f(t.content));
          }
          return out;
        }, [candidatos, LE_HANDLERS]));
    }

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

test("a baseline cobre as páginas que o repositório tem", () => {
  for (const pagina of Object.keys(baseline)) {
    assert.ok(PAGINAS.includes(pagina), `a baseline cita ${pagina}, que não existe mais em frontend/`);
  }
  assert.ok(Object.keys(baseline).length >= 5, "baseline pequena demais para provar alguma coisa");
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
