/**
 * scripts/smoke_prod_ui.mjs ignora o AbortError "Transition was skipped" do /login
 * (app-mode.css:23 opt-in de view transition; o destino do login não tem) e SÓ ele.
 * Roda o script de verdade contra um servidor falso local — nunca contra produção.
 *
 * Rodar: node --test tests/frontend/smoke_prod_ui.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const SCRIPT = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "scripts", "smoke_prod_ui.mjs");
const VT = "<style>@view-transition { navigation: auto; }</style>";
const STATS = '<div id="categories-stats"><b class="stat-value">0</b></div><div id="budgets-stats"><b class="stat-value">0</b></div>';

let servidor, base, extra; // extra: { caminho: js injetado }

const pagina = (caminho, corpo, head = "") =>
  `<!doctype html><html><head>${head}</head><body>${corpo}<script>${extra[caminho] || ""}</script></body></html>`;

before(async () => {
  servidor = createServer((req, res) => {
    const c = req.url.split("?")[0];
    res.setHeader("content-type", "text/html");
    if (c === "/login") {
      res.end(pagina(c, `<input id="email"><input id="senha"><button id="btn-login" onclick="location.href='/painel?next=/x#h'">x</button>`, VT));
    } else if (c === "/app") {
      res.end(pagina(c, STATS + "<script>function navigateTo(){}</script>"));
    } else {
      res.end(pagina(c, "ok"));
    }
  });
  await new Promise((r) => servidor.listen(0, "127.0.0.1", r));
  base = `http://127.0.0.1:${servidor.address().port}`;
});
after(() => servidor.close());

const rodar = (js = {}) => {
  extra = js;
  return new Promise((resolve) => {
    const p = spawn(process.execPath, [SCRIPT], {
      env: { ...process.env, SMOKE_BASE: base, SMOKE_EMAIL: "smoke@teste.invalid", SMOKE_PASSWORD: "senha-inventada" },
      timeout: 45000, // regressão que pendura reprova em vez de travar o comando: SIGTERM, o Playwright fecha o navegador e o script sai com 1
    });
    let out = "";
    p.stdout.on("data", (d) => (out += d));
    p.stderr.on("data", (d) => (out += d));
    p.on("close", (code) => resolve({ code, out }));
  });
};
const abort = (msg) => `setTimeout(() => { throw new DOMException(${JSON.stringify(msg)}, "AbortError"); }, 0);`;
const SKIP = "Transition was skipped";

test("a. /login com view-transition e destino sem opt-in passa", async () => {
  const r = await rodar();
  assert.equal(r.code, 0, r.out);
  assert.match(r.out, /login foi para \/painel$/m); // sem query nem hash no log
});
test("a2. rAF parado no destino não pendura o smoke (teto de 3 s)", async () => {
  // retém o callback: se descartar, o GC rejeita a promise aos 30 s e mascara o resultado
  const r = await rodar({ "/painel": `window.__r = []; requestAnimationFrame = (f) => window.__r.push(f);` });
  assert.equal(r.code, 0, r.out);
});
test("b. AbortError sintético em /login passa", async () => {
  const r = await rodar({ "/login": abort(SKIP) });
  assert.equal(r.code, 0, r.out);
});
test("c. a mesma em /home reprova", async () => {
  const r = await rodar({ "/home": abort(SKIP) });
  assert.equal(r.code, 1, r.out);
  assert.match(r.out, /FALHA: \[\/home\] erro de JS: Transition was skipped/);
});
test("d. Error comum em /login reprova", async () => {
  const r = await rodar({ "/login": `setTimeout(() => { throw new Error("x"); }, 0);` });
  assert.equal(r.code, 1, r.out);
  assert.match(r.out, /FALHA: \[\/login\] erro de JS: x/);
});
test("e. Error com a mesma mensagem em /login reprova", async () => {
  const r = await rodar({ "/login": `setTimeout(() => { throw new Error("${SKIP}"); }, 0);` });
  assert.equal(r.code, 1, r.out);
  assert.match(r.out, /FALHA: \[\/login\] erro de JS: Transition was skipped/);
});
test("f. AbortError com outra mensagem em /login reprova", async () => {
  const r = await rodar({ "/login": abort("outra coisa") });
  assert.equal(r.code, 1, r.out);
  assert.match(r.out, /FALHA: \[\/login\] erro de JS: outra coisa/);
});
