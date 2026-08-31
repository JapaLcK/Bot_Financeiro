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
import { readFileSync, readdirSync } from "node:fs";
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

const HANDLER = /\bon[a-z]+\s*=\s*"([^"]*)"/gi;
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

function nomesChamados(html) {
  const nomes = new Set();
  for (const m of html.matchAll(HANDLER)) {
    for (const c of semStrings(m[1]).matchAll(CHAMADA)) {
      if (!NATIVO.has(c[1])) nomes.add(c[1]);
    }
  }
  return [...nomes].sort();
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
  test(`${pagina}: os ${nomes.length} nomes dos handlers inline existem no escopo global`, async () => {
    const page = await browser.newPage();
    const autenticado = !SEM_SESSAO.has(pagina);
    // O alvo é o ESCOPO, não a autenticação nem a rede: sem estes stubs a página
    // navega para o login e o teste mediria uma página que nem carregou.
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

      // `typeof <nome>`, e não `window[nome]`: const/let no topo de script
      // clássico não viram propriedade de window, mas o handler inline os vê.
      const faltando = await page.evaluate(
        (ns) => ns.filter((n) => {
          try { return new Function(`return typeof ${n}`)() !== "function"; } catch { return true; }
        }),
        nomes,
      );
      assert.deepEqual(faltando, [],
        `handler inline de ${pagina} chama nome que não existe no escopo global: ${faltando.join(", ")}. ` +
        "O clique falha em silêncio. Se o script virou módulo ou ganhou IIFE, devolva os nomes ao escopo global.");
    } finally {
      await page.close();
    }
  });
}
