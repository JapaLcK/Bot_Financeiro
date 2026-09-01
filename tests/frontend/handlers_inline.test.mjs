/**
 * Handler inline só funciona se o nome existir no escopo global.
 *
 * `onclick="abrirX()"` não enxerga escopo de módulo nem de IIFE: o navegador
 * resolve `abrirX` pelo escopo global. Trocar um `<script>` clássico por
 * `<script type="module">`, ou embrulhar o arquivo num IIFE, faz **o botão parar
 * de funcionar sem erro nenhum** — nada no console, nada vermelho, só o clique
 * que não faz nada. É a armadilha que o `docs/armadilhas.md` já descrevia e
 * pedia teste: "qualquer divisão precisa devolver os nomes ao `window` e ter
 * teste que compare a lista com o HTML".
 *
 * Este é esse teste, e ele NÃO lê o JS: sobe a página no Chromium e pergunta ao
 * próprio motor se o nome resolve. Duas armadilhas de medição, as duas pagas na
 * construção:
 *
 * 1. **`typeof window[nome]` está errado.** `const $id = ...` no topo de um
 *    script clássico NÃO vira propriedade de `window`, mas o handler inline o
 *    enxerga pelo escopo global léxico. A pergunta certa é `typeof <nome>`.
 * 2. **Página que redireciona mede nada.** Sem backend, `settings.html` saía
 *    para o login antes de carregar os scripts, e as 31 funções "sumiam". Por
 *    isso o teste REPROVA se a página navegou ou se não carregou script nenhum,
 *    em vez de aprovar em silêncio.
 *
 * Rodar:  npm run test:frontend
 *         (ou: node --test tests/frontend/handlers_inline.test.mjs)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const FRONTEND = join(REPO, "frontend");
// Porta PRÓPRIA: `node --test tests/frontend/*.test.mjs` roda os arquivos em
// paralelo, e o padrão daqui é uma porta ímpar por teste (8899, 8901 … 8909, todas
// tomadas). Reusar uma derruba o OUTRO teste com "http.server morreu" — foi o que
// aconteceu quando este arquivo nasceu com a 8907, a mesma do onboarding_visibility.
const PORT = Number(process.env.PB_HANDLERS_TEST_PORT || 8911);
const ORIGIN = `http://127.0.0.1:${PORT}`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Páginas públicas: fingir sessão VÁLIDA manda o login embora para /home. */
const SEM_SESSAO = new Set(["login.html", "cadastro.html", "index.html", "precos.html"]);

// Onde um handler COMEÇA. O valor não sai daqui — sai do `valorDoHandler`, que
// varre até o delimitador de fechamento tratando `${...}` como opaco.
const INICIO_HANDLER = /\bon[a-z]+\s*=\s*(\\?["'`]|(?=[A-Za-z_$]))/gi;

/**
 * O valor do handler que começa em `i`, **sem** as interpolações.
 *
 * Um scanner, não regex, e cada detalhe aqui foi pago numa rodada de revisão:
 *
 * - varrer até o delimitador de fechamento, porque regex por classe de aspas cortava
 *   na primeira aspa de dentro, e regex por delimitador cortava quando a aspa de
 *   dentro era igual à de fora — o caso real é
 *   `openCardDeleteModal(${escapeHtmlSafe(JSON.stringify(c.name || ""))})`;
 * - DESCARTAR `${...}`: o que está lá dentro roda na GERAÇÃO do markup, no escopo de
 *   quem gera, e não no clique. Cobrar isso é falso positivo;
 * - contar chave respeitando STRING dentro da interpolação, senão um `{` de dentro de
 *   uma string desequilibra a conta e a interpolação "vaza" para o texto do handler;
 * - e respeitar a BARRA INVERTIDA dentro dessa string, senão `\"` fecha a string cedo,
 *   o `}` seguinte zera a profundidade e o resto do handler é perdido.
 *
 * TETO DECLARADO: isto é um scanner de uma forma literal, não um lexer de JavaScript.
 * Ele trata escape um nível fundo, dentro de string dentro de interpolação — o que
 * cobre o que um gerador de markup escreve. Interpolação aninhada dentro de template
 * literal dentro de interpolação está fora, e o modo de falha nesse caso é DEIXAR DE
 * COBRAR um nome, nunca inventar um: o teste não trava PR legítimo por este caminho.
 *
 * Depois disto o que sobra é literal, e TUDO que sobra roda no clique — inclusive o
 * `inner` de `outer(inner())`. Por isso a extração seguinte não precisa (e não deve)
 * filtrar por posição: uma versão anterior filtrava por profundidade de parênteses e
 * perdia exatamente esse caso.
 */
function valorDoHandler(texto, i, semAspas = false) {
  const escapado = !semAspas && texto[i] === "\\";
  // HTML aceita valor sem aspas (`onclick=doLogin(event)`), e o navegador executa
  // igual. Sem este ramo, trocar a forma citada pela não-citada tirava o handler da
  // extração sem uma linha vermelha. Aí o fim do valor é espaço em branco ou `>`.
  const delim = semAspas ? null : texto[escapado ? i + 1 : i];
  let j = semAspas ? i : escapado ? i + 2 : i + 1;
  let prof = 0, aspa = null, out = "";
  while (j < texto.length) {
    const c = texto[j];
    if (prof > 0) {
      if (aspa && c === "\\") { j += 2; continue; }   // \" não fecha a string
      if (aspa) { if (c === aspa) aspa = null; }
      else if (c === '"' || c === "'" || c === "`") aspa = c;
      else if (c === "{") prof++;
      else if (c === "}") prof--;
      j++;
      continue;                       // conteúdo da interpolação NÃO entra
    }
    if (c === "$" && texto[j + 1] === "{") { prof = 1; j += 2; continue; }
    if (delim === null ? /[\s>]/.test(c) : c === delim) break;
    out += c; j++;
  }
  return out;
}

// Handler MONTADO, fora de atributo literal: `{ click: "openUsersModal(event)" }`
// no admin-dashboard.html e `el.onclick = "..."`. São 10 nomes hoje, todos em
// código que gera markup a partir de dados. Isto NÃO é parsear JS — é casar uma
// forma literal específica, e o modo de falha é deixar de achar, nunca inventar.
const HANDLER_MONTADO = /(?:\bclick|\.on[a-z]+)\s*[:=]\s*(["'`])((?:\\.|(?!\1)[\s\S])*)\1/gi;
const CHAMADA = /(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(/g;

/**
 * `window.algumaCoisa()` num handler É um global, e o `CHAMADA` o descartava junto com
 * `event.stopPropagation()` — o lookbehind não distingue receptor nativo de `window`.
 *
 * Só `window.`, de propósito: cobrar toda chamada de membro acusaria
 * `this.setCustomValidity()`, `parcelas.find()` e `document.getElementById()`, que são
 * 22 dos 24 membros chamados nos handlers de hoje e não são globais nenhum. O ganho
 * está em `window.X`, onde o `X` é exatamente o que some quando o script vira módulo.
 */
const CHAMADA_WINDOW = /\bwindow\.([A-Za-z_$][\w$]*)\s*\(/g;

/**
 * Identificador NU passado como argumento: `startCheckout(currentCycle, this, 'x')`.
 *
 * Ele não é chamado, mas é lido no clique — e some do escopo global pelo mesmo
 * caminho que as funções. Uma instância hoje, no `precos.html`. Só precisa EXISTIR,
 * não ser função, então a verificação é `typeof !== "undefined"`.
 */
/**
 * Percorre TODA lista de argumento, em todo nível de aninhamento, e devolve os
 * trechos separados por vírgula do nível de cada uma.
 *
 * Era um regex `/\(([^()]*)\)/g`, que só casa a lista mais interna: em
 * `outer(currentCycle, inner())` ele achava o `()` do `inner` e perdia o
 * `currentCycle` — justamente o argumento nu que o teste existe para cobrar.
 * Aninhamento não se resolve com expressão regular; resolve-se com uma pilha.
 */
function argumentosDe(t) {
  const fora = [];
  const pilha = [];
  let seg = "";
  for (const c of t) {
    if (c === "(") { pilha.push([]); seg = ""; continue; }
    if (c === ")") {
      if (!pilha.length) { seg = ""; continue; }
      const lista = pilha.pop();
      lista.push(seg);
      fora.push(...lista);
      seg = "";
      continue;
    }
    if (c === "," && pilha.length) { pilha[pilha.length - 1].push(seg); seg = ""; continue; }
    seg += c;
  }
  return fora;
}
const SO_NOME = /^[A-Za-z_$][\w$]*$/;
const NAO_E_NOME = new Set(["this", "event", "true", "false", "null", "undefined"]);

/** Nome que o navegador já traz, ou que não é chamada de função do autor. */
const NATIVO = new Set([
  "if", "for", "while", "switch", "catch", "return", "typeof", "new", "function",
  "alert", "confirm", "prompt", "parseInt", "parseFloat", "Number", "String",
  "Boolean", "Array", "Object", "JSON", "Math", "Date", "setTimeout",
  "setInterval", "encodeURIComponent", "decodeURIComponent", "console", "event",
  "this", "void", "delete", "require",
]);

/**
 * Esvazia as strings do handler ANTES de procurar chamada.
 *
 * Sem isto, `'Use exatamente 4 números (ou deixe vazio).'` rendia o
 * "identificador" `meros`: o `\w` do JavaScript é ASCII, então o `ú` corta o
 * nome e o `(` logo depois completa o engano.
 *
 * As TRÊS aspas, não só a simples: um handler delimitado por `'` pode conter `"`
 * à vontade, e `onclick='alert("Press (Y)")'` rendia a função `Press` e a variável
 * `Y`. Esse é o modo de falha que importa — nome INVENTADO reprova handler correto,
 * enquanto nome perdido só reduz cobertura. O `dashboard.js` já usa handler de aspa
 * simples com `"` dentro (linhas 1464 e 1863).
 *
 * `\\.` cobre o delimitador escapado dentro da própria string.
 *
 * ENTIDADE VIRA DELIMITADOR ANTES DE TUDO. O navegador decodifica a referência de
 * caractere antes de compilar o handler, então `&#39;` e `'` são a mesma coisa para
 * ele — e reconhecer só algumas formas rendia nome inventado nas outras.
 *
 * ENUMERAR AS FORMAS FALHOU DUAS VEZES. Primeiro listei só `&quot;`; depois listei as
 * seis de `"` e `'` e esqueci a CRASE, apesar de o código tratar três delimitadores.
 * Então aqui não há lista: decodifica-se QUALQUER referência numérica e, se o
 * resultado for um dos três delimitadores, ela vira o caractere. O resto fica intacto.
 * É a regra, não a enumeração — e não tem como esquecer um membro dela.
 */
const DELIMITADORES = new Set(['"', "'", "`"]);
const NOMEADAS = { quot: '"', apos: "'", DiacriticalGrave: "`" };
const REFERENCIA = /&(?:([A-Za-z][A-Za-z0-9]*)|#(\d+)|#[xX]([0-9a-fA-F]+));/g;

const decodificaDelimitador = (s) =>
  s.replace(REFERENCIA, (inteira, nome, dec, hex) => {
    const c = nome ? NOMEADAS[nome] : String.fromCharCode(parseInt(dec ?? hex, dec ? 10 : 16));
    return c && DELIMITADORES.has(c) ? c : inteira;
  });

const semStrings = (s) => decodificaDelimitador(s)
  .replace(/'(?:\\.|[^'\\])*'/g, "''")
  .replace(/"(?:\\.|[^"\\])*"/g, '""')
  .replace(/`(?:\\.|[^`\\])*`/g, "``");


function nomesDe(trechos) {
  const funcoes = new Set();
  const variaveis = new Set();
  for (const t of trechos) {
    const limpo = semStrings(t);
    for (const c of limpo.matchAll(CHAMADA)) if (!NATIVO.has(c[1])) funcoes.add(c[1]);
    for (const c of limpo.matchAll(CHAMADA_WINDOW)) if (!NATIVO.has(c[1])) funcoes.add(c[1]);
    for (const tok of argumentosDe(limpo)) {
      const n = tok.trim();
      if (SO_NOME.test(n) && !NAO_E_NOME.has(n) && !NATIVO.has(n)) variaveis.add(n);
    }
  }
  return { funcoes, variaveis };
}

const trechosDe = (texto) => [
  ...[...texto.matchAll(INICIO_HANDLER)].map((m) =>
    valorDoHandler(texto, m.index + m[0].length - m[1].length, m[1] === "")),
  ...[...texto.matchAll(HANDLER_MONTADO)].map((m) => m[2]),
];

/** `<script src="/x.js">` -> os arquivos de `frontend/` que a página carrega. */
function scriptsDaPagina(html) {
  // As DUAS aspas: `src='...'` é HTML equivalente, e reconhecer só a dupla deixaria
  // uma troca neutra de marcação desligar o teste em silêncio — some a cobertura dos
  // nomes que só existem no .js, e a página continua verde.
  // As TRÊS formas de valor de atributo que o HTML aceita — dupla, simples e SEM
  // aspas —, as mesmas que o INICIO_HANDLER reconhece. Faltar qualquer uma deixa uma
  // edição de formatação neutra desligar a cobertura em silêncio, e foi assim que a
  // aspa simples e o espaço em volta do `=` chegaram aqui, um apontamento por vez.
  return [...html.matchAll(/<script[^>]*\ssrc\s*=\s*(?:"\/?([^"?]+\.js)|'\/?([^'?]+\.js)|\/?([^\s"'>?]+\.js))/gi)]
    .map((m) => join(FRONTEND, m[1] ?? m[2] ?? m[3]))
    .filter((f) => existsSync(f));
}

/**
 * Do FONTE: o HTML **e os .js que ele carrega**.
 *
 * Ler só o HTML deixava de fora a maior lista de todas: o `dashboard.js` EMITE 72
 * nomes em markup gerado a partir de dados (`onclick=\"payBill(...)\"`), que o HTML
 * não mostra e que o DOM também não, porque sem dados aquilo não renderiza. Era o
 * maior ponto cego do teste, maior que o próprio HTML da página.
 */
/**
 * Remove COMENTÁRIO antes de procurar handler.
 *
 * `trechosDe` varre o .js inteiro, e não sabe distinguir markup gerado de prosa: o
 * `comecar.js:6` já tem `` `onclick=` `` num comentário de bloco, e um exemplo de
 * documentação como `onclick="privateHelper()"` faria o teste exigir `privateHelper`
 * no escopo global sem que exista handler nenhum. É falso positivo — a classe que
 * reprova código correto.
 *
 * A armadilha aqui, e a razão de isto ser um scanner: **o markup gerado mora dentro
 * de template literal**. Tratar a crase como string a ser descartada apagaria os 72
 * nomes que o `dashboard.js` emite. Então o scanner só usa o estado de string para
 * decidir se um `/` inicia comentário — nunca para descartar o conteúdo.
 *
 * TETO: literal de expressão regular contendo aspa (`/["']/g`) confunde o estado de
 * string, e comentário dentro de `${...}` de um template não é visto. Os dois são
 * raros, e o conjunto de nomes das 7 páginas é idêntico com e sem esta função —
 * medido, não suposto.
 */
function semComentarios(txt) {
  let out = "", i = 0, aspa = null;
  while (i < txt.length) {
    const c = txt[i], d = txt[i + 1];
    if (aspa) {
      if (c === "\\") { out += c + (d ?? ""); i += 2; continue; }
      if (c === aspa) aspa = null;
      out += c; i++; continue;
    }
    if (c === "'" || c === '"' || c === "`") { aspa = c; out += c; i++; continue; }
    if (c === "/" && d === "*") { i = txt.indexOf("*/", i + 2); i = i < 0 ? txt.length : i + 2; continue; }
    if (c === "/" && d === "/") { const n = txt.indexOf("\n", i); i = n < 0 ? txt.length : n; continue; }
    if (txt.startsWith("<!--", i)) { const n = txt.indexOf("-->", i); i = n < 0 ? txt.length : n + 3; continue; }
    out += c; i++;
  }
  return out;
}

function nomesChamados(html) {
  const limpo = semComentarios(html);
  const trechos = [...trechosDe(limpo)];
  // `scriptsDaPagina(limpo)`, não `(html)`: um `<script src>` dentro de comentário HTML
  // o navegador NÃO carrega, e ler o arquivo mesmo assim cobraria nomes que a página
  // não tem — falso positivo. Era regressão minha, introduzida junto com o próprio
  // `semComentarios`.
  for (const js of scriptsDaPagina(limpo)) trechos.push(...trechosDe(semComentarios(readFileSync(js, "utf-8"))));
  const { funcoes, variaveis } = nomesDe(trechos);
  return { funcoes: [...funcoes].sort(), variaveis: [...variaveis].sort() };
}

async function startServer() {
  const proc = spawn(
    "python3",
    ["-m", "http.server", String(PORT), "--bind", "127.0.0.1", "--directory", FRONTEND],
    { stdio: "ignore" },
  );
  for (let i = 0; i < 100; i++) {
    await sleep(100);
    if (proc.exitCode !== null) throw new Error(`http.server morreu (porta ${PORT} ocupada?)`);
    try { if ((await fetch(`${ORIGIN}/index.html`)).ok) return proc; } catch { /* subindo */ }
  }
  proc.kill();
  throw new Error(`http.server não subiu em ${ORIGIN}`);
}

let server, browser;
before(async () => { server = await startServer(); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

/** Só as páginas que TÊM handler inline; as outras não têm o que provar. */
const PAGINAS = readdirSync(FRONTEND)
  .filter((f) => f.endsWith(".html"))
  .map((f) => [f, nomesChamados(readFileSync(join(FRONTEND, f), "utf-8"))])
  .filter(([, { funcoes, variaveis }]) => funcoes.length || variaveis.length);

test("há páginas com handler inline para verificar", () => {
  // Se o extrator quebrar, os testes abaixo passariam medindo lista vazia.
  assert.ok(PAGINAS.length >= 5, `esperava várias páginas com handler inline, achei ${PAGINAS.length}`);
});

for (const [pagina, { funcoes, variaveis }] of PAGINAS) {
  test(`${pagina}: todo handler inline resolve no escopo global`, async () => {
    const page = await browser.newPage();
    const autenticado = !SEM_SESSAO.has(pagina);
    // O alvo é o ESCOPO, não a autenticação nem a rede: sem estes stubs a página
    // navega para o login e o teste mediria uma página que nem carregou.
    //
    // Os stubs são MÍNIMOS de propósito, e o teto disso está medido: com `{}` em
    // toda API, o `admin-dashboard.html` renderiza 3 handlers no DOM contra os 27
    // que o fonte tem — quase tudo dele é gerado a partir de dados. Isso não
    // enfraquece o teste, porque a lista principal vem do FONTE; a leitura do DOM
    // é adição. Devolver dados plausíveis por página seria fixture por página, e o
    // que ela compraria é só a parte que já está coberta pelo outro lado.
    // (Medido também: com estes stubs, zero erro de página nas três maiores.)
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

    try {
      await page.goto(`${ORIGIN}/${pagina}`, { waitUntil: "load" }).catch(() => {});
      await sleep(700);

      // Premissa antes da medição: página que saiu daqui, ou que não rodou script
      // nenhum, não prova nada sobre escopo — e aprovaria em silêncio.
      const url = page.url();
      assert.ok(url.includes(pagina), `a página navegou para ${url.replace(ORIGIN, "")} — o teste mediria outra coisa`);
      const scripts = await page.evaluate(() => document.scripts.length);
      assert.ok(scripts > 0, `${pagina} carregou 0 scripts — nada a medir`);

      // E o que o DOM tiver DEPOIS de renderizar, que o fonte não mostra: handler
      // posto por script em elemento criado na hora. Só soma — o que não renderizou
      // com estes stubs mínimos continua coberto pela leitura do fonte.
      const doDom = await page.evaluate(() => {
        const out = [];
        for (const el of document.querySelectorAll("*")) {
          for (const a of el.attributes) if (/^on[a-z]+$/i.test(a.name)) out.push(a.value);
        }
        return out;
      });
      const doDomNomes = nomesDe(doDom);
      const todasFuncoes = [...new Set([...funcoes, ...doDomNomes.funcoes])].sort();
      const todasVariaveis = [...new Set([...variaveis, ...doDomNomes.variaveis])]
        .filter((v) => !todasFuncoes.includes(v)).sort();

      // `typeof <nome>`, e não `window[nome]`: const/let no topo de script
      // clássico não viram propriedade de window, mas o handler inline os vê.
      const faltando = await page.evaluate(
        ([fns, vars]) => {
          const resolve = (n, tipo) => {
            try {
              const t = new Function(`return typeof ${n}`)();
              return tipo === "function" ? t === "function" : t !== "undefined";
            } catch { return false; }
          };
          return [
            ...fns.filter((n) => !resolve(n, "function")),
            // Argumento nu só precisa EXISTIR — é lido, não chamado.
            ...vars.filter((n) => !resolve(n, "qualquer")).map((n) => `${n} (argumento)`),
          ];
        },
        [todasFuncoes, todasVariaveis],
      );
      assert.deepEqual(faltando, [],
        `${pagina}: de ${todasFuncoes.length} funções e ${todasVariaveis.length} ` +
        `argumentos verificados, estes não existem no escopo global: ` +
        `${faltando.join(", ")}. ` +
        "O clique falha em silêncio. Se o script virou módulo ou ganhou IIFE, devolva os nomes ao escopo global.");
    } finally {
      await page.close();
    }
  });
}
