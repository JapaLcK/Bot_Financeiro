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

// Um grupo POR DELIMITADOR — aspa dupla, simples, crase, e a ESCAPADA
// (`onclick=\\"x()\\"`, que é como um .js escreve handler dentro de string).
//
// Uma classe única com os três cortaria o handler na primeira aspa DE DENTRO:
// `onclick="foo(${bar('x')})"` viraria `foo(${bar(`, e aí o
// `semInterpolacao` não teria mais o `}` para casar — a interpolação ficaria, e o
// `bar` seria cobrado como se fosse ponto de entrada. É o mesmo erro que o extrator
// de URL do gate de rotas cometeu, pela mesma razão.
const HANDLER = /\bon[a-z]+\s*=\s*(?:\\"((?:[^"\\]|\\[^"])*)\\"|"([^"]*)"|'([^']*)'|`([^`]*)`)/gi;
// Handler MONTADO, fora de atributo literal: `{ click: "openUsersModal(event)" }`
// no admin-dashboard.html e `el.onclick = "..."`. São 10 nomes hoje, todos em
// código que gera markup a partir de dados. Isto NÃO é parsear JS — é casar uma
// forma literal específica, e o modo de falha é deixar de achar, nunca inventar.
const HANDLER_MONTADO = /(?:\bclick|\.on[a-z]+)\s*[:=]\s*["'`]([^"'`]+)["'`]/gi;
const CHAMADA = /(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(/g;

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
 */
const semStrings = (s) => s.replace(/'[^']*'/g, "''").replace(/&quot;[^&]*&quot;/g, "");

/**
 * Tira as interpolações `${...}`: o que está DENTRO delas roda na GERAÇÃO do
 * markup, no escopo de quem gera, e não no clique.
 *
 * Em `onclick="openCardEditModal(${escapeHtmlSafe(JSON.stringify(c))})"` só
 * `openCardEditModal` precisa existir no escopo global; o `escapeHtmlSafe` é
 * ajudante do gerador. Sem tirar, o dia em que o `dashboard.js` virar módulo com os
 * pontos de entrada exportados, o teste reprovaria por causa dos ajudantes — que é
 * falso positivo, e falso positivo trava PR legítimo.
 */
const semInterpolacao = (s) => s.replace(/\$\{[^}]*\}/g, "");

function nomesDe(trechos) {
  const nomes = new Set();
  for (const t of trechos) {
    for (const c of semInterpolacao(semStrings(t)).matchAll(CHAMADA)) {
      if (!NATIVO.has(c[1])) nomes.add(c[1]);
    }
  }
  return nomes;
}

const trechosDe = (texto) => [
  // `m.slice(1).find(Boolean)`: só um dos quatro grupos casa; os outros vêm
  // undefined, e um `m[1]` cego pegaria sempre o da aspa escapada.
  ...[...texto.matchAll(HANDLER)].map((m) => m.slice(1).find(Boolean) || ""),
  ...[...texto.matchAll(HANDLER_MONTADO)].map((m) => m[1]),
];

/** `<script src="/x.js">` -> os arquivos de `frontend/` que a página carrega. */
function scriptsDaPagina(html) {
  return [...html.matchAll(/<script[^>]+src="\/?([^"?]+\.js)/gi)]
    .map((m) => join(FRONTEND, m[1]))
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
function nomesChamados(html) {
  const trechos = [...trechosDe(html)];
  for (const js of scriptsDaPagina(html)) trechos.push(...trechosDe(readFileSync(js, "utf-8")));
  return [...nomesDe(trechos)].sort();
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
  .filter(([, nomes]) => nomes.length);

test("há páginas com handler inline para verificar", () => {
  // Se o extrator quebrar, os testes abaixo passariam medindo lista vazia.
  assert.ok(PAGINAS.length >= 5, `esperava várias páginas com handler inline, achei ${PAGINAS.length}`);
});

for (const [pagina, nomes] of PAGINAS) {
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
      const todos = [...new Set([...nomes, ...nomesDe(doDom)])].sort();

      // `typeof <nome>`, e não `window[nome]`: const/let no topo de script
      // clássico não viram propriedade de window, mas o handler inline os vê.
      const faltando = await page.evaluate(
        (ns) => ns.filter((n) => {
          try { return new Function(`return typeof ${n}`)() !== "function"; } catch { return true; }
        }),
        todos,
      );
      assert.deepEqual(faltando, [],
        `${pagina}: dos ${todos.length} nomes verificados (${nomes.length} do fonte, ` +
        `${todos.length - nomes.length} só no DOM), estes não existem no escopo global: ` +
        `${faltando.join(", ")}. ` +
        "O clique falha em silêncio. Se o script virou módulo ou ganhou IIFE, devolva os nomes ao escopo global.");
    } finally {
      await page.close();
    }
  });
}
