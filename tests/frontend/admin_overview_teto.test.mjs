/**
 * O teto do `/admin/api/overview` e as duas saídas que o cancelavam sozinhas.
 *
 * `loadOverview` arma um `AbortController` + `setTimeout` de 15 s e, logo depois
 * do `fetch`, fazia `clearTimeout(refreshTimeout)` e `refreshController = null`
 * ANTES dos dois desvios: o `return` do 401 e o `throw` do `!res.ok`. Nesses
 * ramos a função sai ENTRE os headers e a leitura do corpo — o `clearTimeout`
 * tira o único abort agendado, o `= null` ainda impede que o `abort()` da
 * iteração seguinte alcance aquele controller, e o corpo não lido segue
 * baixando até EOF/GC, sem teto nenhum.
 *
 * Por que este é o pior dos cinco sites do PR: aqui há um `setInterval` de 60 s
 * (`startAutoRefresh`) numa aba que o dono deixa aberta por horas. Cada erro do
 * poll deixava mais um corpo pendurado. Irmão do conserto de `pb-nav.js` (#367);
 * o conserto é o mesmo: uma guarda no `finally` cobrindo TODAS as saídas.
 *
 * A medida é os ms DE DENTRO da página, do início do fetch até o abort — nunca
 * o relógio do Playwright:
 *
 *   | versão                                | `__abortado`        |
 *   |---------------------------------------|---------------------|
 *   | com o conserto                        | poucos ms (< 1000)  |
 *   | sem o `refreshController?.abort()`    | NUNCA (fica null)   |
 *
 * O caso usa o caminho do POLL (`refresh(true)`, o mesmo que o `setInterval`
 * chama) e não o do boot, de propósito: o `.catch` do boot manda para
 * `/admin/login` e levaria a página embora antes de dar para medir. No `refresh`
 * um erro que não é `AbortError` só escreve na barra de status.
 *
 * Os dois controles do CLAUDE.md §3:
 *   · negativo — tire o `refreshController?.abort()` do `finally` do
 *     `loadOverview`: o caso fica VERMELHO (`__abortado` nunca vira número);
 *   · positivo — o mesmo caso exige, ANTES da mutação de rede, que o boot com
 *     200 tenha rendido "Atualizado:" na barra. Sem essa metade o teste passaria
 *     numa versão que aborta tudo, inclusive o caminho feliz — que é pior que o
 *     bug, e foi ele que pegou a mutação "abortar ANTES do `res.json()`".
 *
 * O que este arquivo NÃO alcança: as horas de aba aberta que fazem o acúmulo
 * doer, e o 401 de verdade (aqui ele redirecionaria a página).
 *
 * Rodar:  npm run test:frontend
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

/**
 * O corpo do overview com a FORMA que a cadeia de render exige — `{}` não
 * serve: `renderSummary(data.summary)` estoura em `undefined.total_accounts` e
 * o boot cai no "Falha ao atualizar" antes de o caso medir coisa alguma. Os
 * valores são vazios de propósito; o que importa aqui é a forma.
 */
const OVERVIEW = {
  generated_at: "2026-09-10T12:00:00+00:00",
  summary: {}, ops_summary: {}, open_finance: {}, email_stats: {},
  recent_errors: [], recent_ops: [], recent_ai_claims: [], recent_emails: [],
  recent_signups: [], recent_logins: [], top_users: [], time_series: [],
  monthly_financials: [],
};

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

/**
 * Abre o painel com o boot saudável e devolve a página já medida.
 *
 * O stub in-page fica ANTES de todo script: a 1ª chamada ao overview segue para
 * o `page.route` (boot verde), e só a partir de `window.__armar()` a próxima
 * responde com o `status` pedido e o CORPO ABERTO — um `ReadableStream` que
 * nunca empurra nada, que é o que deixa a resposta "nos headers".
 */
async function abrirPainel(status) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const erros = [];
  page.on("pageerror", (e) => erros.push(String(e)));

  await page.addInitScript((st) => {
    window.__abortado = null;
    window.__armado = false;
    window.__armar = () => { window.__armado = true; };
    const orig = window.fetch;
    window.fetch = function (u, o) {
      if (!window.__armado || !String(u).includes("/admin/api/overview")) {
        return orig.apply(this, arguments);
      }
      const t = Date.now();
      // `o.signal` some se alguém tirar o `signal:` do fetch — sem a guarda o
      // stub lançaria e o caso ficaria vermelho pelo motivo errado.
      if (o && o.signal) {
        o.signal.addEventListener("abort", () => { window.__abortado = Date.now() - t; });
      }
      return Promise.resolve(new Response(new ReadableStream({ start() {} }), { status: st }));
    };
  }, status);

  // Um 401 em QUALQUER rota manda a página para /admin/login e ela some debaixo
  // do teste — tudo 200, como no admin_trial_lock_row.test.mjs.
  await page.route("**/admin/api/**", (r) =>
    r.fulfill({ contentType: "application/json", body: "{}" }));
  // Depois do catch-all, de propósito: no Playwright a rota mais recente ganha.
  await page.route("**/admin/api/overview**", (r) =>
    r.fulfill({ contentType: "application/json", body: JSON.stringify(OVERVIEW) }));

  await page.goto(`${ORIGIN}/admin-dashboard.html`, { waitUntil: "domcontentloaded" });
  return { page, erros };
}

/** Espera a barra de status parar em algo que não seja "Buscando dados…". */
const status = (page) => page.waitForFunction(() => {
  const t = document.getElementById("refresh-status").textContent;
  return t && !t.includes("Buscando") ? t : false;
}, null, { timeout: 10_000 }).then((h) => h.jsonValue());

test("overview !ok com o corpo aberto é ABORTADO, não esquecido", async () => {
  const { page, erros } = await abrirPainel(500);

  // POSITIVO, e âncora do caso: o boot com 200 completou a cadeia de render
  // inteira e escreveu na barra. Se o conserto tivesse quebrado o caminho
  // feliz, o teste morria aqui — antes de medir o teto.
  assert.match(await status(page), /Atualizado:/,
    "o boot saudável não completou: o caminho legítimo do loadOverview quebrou");

  // Agora o poll — o MESMO `refresh(true)` que o setInterval de 60 s chama.
  await page.evaluate(() => { window.__armar(); return window.refresh(true); });

  const ms = await page.evaluate(() => window.__abortado);
  assert.notEqual(ms, null,
    "o overview !ok com corpo aberto não foi abortado: o clearTimeout tira o"
    + " único relógio ANTES do throw, e o `refreshController = null` ainda impede"
    + " que a iteração seguinte do poll alcance este controller");
  assert.equal(ms < 1000, true,
    `o abort chegou ${ms}ms depois do início do fetch: é o teto de 15 s`
    + " disparando, não o abort() do finally");

  // O `abort()` do finally não passa razão, e o `abort('timeout')` do teto
  // passa; o catch do `refresh` separa os textos da tela só por
  // `e?.name === 'AbortError'`. Aqui dentro, e não num caso próprio: um caso
  // separado para isto ficava VERDE com e sem o conserto (medido), ou seja não
  // media nada. Como assert deste caso, ele custa uma linha e prende o texto.
  const txt = await status(page);
  assert.match(txt, /Falha ao atualizar/,
    `o 500 virou outro texto na tela do admin: "${txt}"`);

  assert.deepEqual(erros, []);
  await page.close();
});
