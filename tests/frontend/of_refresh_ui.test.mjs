/**
 * Onda 1 do Open Finance, lado do navegador. Dois invariantes:
 *
 *  1. O botão "↻ Atualizar" do Open Finance some SÓ onde o gesto de puxar
 *     existe (html.pb-app = app iOS ou PWA instalada). No mobile web ele
 *     continua sendo a única forma de atualizar — some lá e o usuário fica sem
 *     ação nenhuma.
 *  2. O veredito do refresh só fica verde para estado que ele CONHECE como bom.
 *     Estado desconhecido, pausado, removido ou sem dados nunca vira
 *     "Tudo em dia!" — no gesto do celular isso é a diferença entre o indicador
 *     verde e o âmbar com a mensagem certa.
 *  3. O dashboard reage ao `open_finance_synced` que o backend JÁ mandava e
 *     NENHUM arquivo do front tratava — e reage UMA vez por rajada: a Pluggy
 *     manda item/updated e transactions/created com segundos de diferença.
 *
 * Cada teste discrimina: sem a regra do app-mode.css o 1º dá visível/visível;
 * com o antigo `return {tone:"ok"}` de default o 2º volta "Tudo em dia!" nos
 * cinco estados; sem a branch nova do ws.onmessage o 3º conta 0 `get_month`
 * (e sem o debounce, conta 3).
 *
 * Rodar:  npm run test:frontend
 *         (um arquivo só: node --test tests/frontend/of_refresh_ui.test.mjs)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const FRONTEND = join(REPO, "frontend");
// porta própria: 8899 é do fanout, 8901 do hiw_rail, 8903 dos modais — o
// `node --test` roda os arquivos em PARALELO e duas suítes na mesma porta se matam.
const PORT = Number(process.env.PB_OF_TEST_PORT || 8905);
const ORIGIN = `http://127.0.0.1:${PORT}`;

const SAFARI = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1";
const APP_UA = SAFARI + " PigBankApp/1.0";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const json = (body) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

async function startServer() {
  const proc = spawn("python3", ["-m", "http.server", String(PORT), "--bind", "127.0.0.1",
                                 "--directory", FRONTEND], { stdio: "ignore" });
  for (let i = 0; i < 100; i++) {
    await sleep(100);
    if (proc.exitCode !== null) throw new Error(`http.server morreu (porta ${PORT} ocupada?)`);
    try { if ((await fetch(`${ORIGIN}/settings.html`)).ok) return proc; } catch { /* subindo */ }
  }
  proc.kill();
  throw new Error(`http.server não subiu em ${ORIGIN}`);
}

async function waitFor(cond, what, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await cond()) return;
    await sleep(50);
  }
  throw new Error(`timeout esperando: ${what}`);
}

let server, browser;
before(async () => { server = await startServer(); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

/** Página com as rotas de API neutralizadas (asset vai pro http.server). */
async function newPage(contextOpts = {}) {
  const ctx = await browser.newContext({ ...contextOpts });
  const page = await ctx.newPage();
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return route.abort();     // CDN (pluggy, chart.js) fora
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    return route.fulfill(json({}));
  });
  await page.route("**/static/auth-refresh.js", (route) =>
    route.fulfill({ status: 200, contentType: "application/javascript",
                    body: readFileSync(join(FRONTEND, "static", "auth-refresh.js"), "utf8") }));
  await page.route("**/auth/validate", (route) => route.fulfill(json({ user_id: 1 })));
  await page.route("**/auth/me", (route) => route.fulfill(json({ app_access: true, plan_tier: "pro" })));
  page.__ctx = ctx;
  return page;
}

async function botaoVisivel(page) {
  return page.evaluate(() => {
    const b = document.getElementById("of-refresh-btn");
    if (!b) return null;
    return getComputedStyle(b).display !== "none";
  });
}

test("botão Atualizar do OF: some no modo app, fica no mobile web", async () => {
  // No app (UA PigBankApp) o app-mode.js liga html.pb-app + body.pb-page-settings,
  // que é exatamente o que a regra do app-mode.css exige.
  const noApp = await newPage({ userAgent: APP_UA, viewport: { width: 390, height: 844 },
                                hasTouch: true, isMobile: true });
  try {
    await noApp.goto(`${ORIGIN}/settings.html?view=open-finance`);
    await waitFor(() => noApp.evaluate(() => document.documentElement.classList.contains("pb-app")),
                  "html.pb-app no modo app");
    assert.equal(await botaoVisivel(noApp), false, "no app o botão tem que sumir (o gesto substitui)");
  } finally { await noApp.__ctx.close(); }

  // Mesmo aparelho, navegador comum: o botão é a única forma de atualizar.
  const noWeb = await newPage({ userAgent: SAFARI, viewport: { width: 390, height: 844 },
                               hasTouch: true, isMobile: true });
  try {
    await noWeb.goto(`${ORIGIN}/settings.html?view=open-finance`);
    await sleep(300);
    assert.equal(await noWeb.evaluate(() => document.documentElement.classList.contains("pb-app")), false);
    assert.equal(await botaoVisivel(noWeb), true, "no mobile web o botão CONTINUA");
  } finally { await noWeb.__ctx.close(); }

  // ONDA 2: as outras duas superfícies da lista. Desktop web é a mais óbvia e a
  // que ninguém media; a PWA instalada entra pelo `display-mode: standalone` do
  // app-mode.js, um ramo do gate que o caso do UA acima NUNCA exercita —
  // esconder o botão lá depende dele, e só dele.
  const desktop = await newPage({ viewport: { width: 1440, height: 900 } });
  try {
    await desktop.goto(`${ORIGIN}/settings.html?view=open-finance`);
    await sleep(300);
    assert.equal(await desktop.evaluate(() => document.documentElement.classList.contains("pb-app")), false);
    assert.equal(await botaoVisivel(desktop), true, "no desktop web o botão CONTINUA");
  } finally { await desktop.__ctx.close(); }

  const pwa = await newPage({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  try {
    // `navigator.standalone` é EXATAMENTE o que o Safari do iOS expõe numa PWA
    // na tela de início — não é um mock meu, é o sinal real que o gate lê.
    // A outra porta do gate, `matchMedia("(display-mode: standalone)")`, NÃO é
    // alcançável aqui: medido, o Chromium não emula `display-mode` nem por CDP
    // (`Emulation.setEmulatedMedia` devolve `matches: false` nas duas formas).
    // Sem UA de app de propósito — a PWA é Safari puro, e é esse o ponto.
    await pwa.addInitScript(() =>
      Object.defineProperty(window.navigator, "standalone", { value: true, configurable: true }));
    await pwa.goto(`${ORIGIN}/settings.html?view=open-finance`);
    await waitFor(() => pwa.evaluate(() => document.documentElement.classList.contains("pb-app")),
                  "html.pb-app na PWA instalada");
    assert.equal(await botaoVisivel(pwa), false, "na PWA o botão some igual ao app (o gesto substitui)");
  } finally { await pwa.__ctx.close(); }
});

/**
 * O veredito é uma tabela de permissão: `OF_VERDICT_OK` decide quem pode ser
 * verde. O teste discrimina — com o `default: return {tone:"ok"}` que existia
 * antes, os quatro casos aqui voltavam "Tudo em dia!".
 */
test("veredito do refresh: só estado conhecido-bom fica verde", async () => {
  const page = await newPage({ viewport: { width: 390, height: 844 } });
  try {
    await page.goto(`${ORIGIN}/settings.html?view=open-finance`);
    await waitFor(() => page.evaluate(() => typeof window.refreshVerdict === "function"),
                  "refreshVerdict existir na página");

    // NUNCA verde — inclusive o estado inventado, que é o ponto do teste: o
    // backend pode ganhar um caso novo antes desta tela saber dele.
    for (const state of ["no_accounts", "paused", "removed", "item_missing",
                         "estado_que_ninguem_implementou_ainda"]) {
      const v = await page.evaluate((s) =>
        window.refreshVerdict({ ok: true, still_updating: 0, items: [{
          item_id: "i1", institution: "Nubank", state: s, label: s, detail: null }] }), state);
      assert.notEqual(v.tone, "ok", `${state} não pode ser verde (veio "${v.msg}")`);
      assert.notEqual(v.msg, "Tudo em dia!", `${state} devolveu "Tudo em dia!"`);
    }

    // Um item bom + um desconhecido: o desconhecido manda.
    const misto = await page.evaluate(() => window.refreshVerdict({ ok: true, items: [
      { item_id: "a", state: "updated" }, { item_id: "b", state: "coisa_nova" }] }));
    assert.notEqual(misto.tone, "ok", "um item desconhecido derruba o verde do outro");

    // CONTROLE POSITIVO: o caminho legítimo continua verde — sem isto o teste
    // passaria num código que recusa tudo.
    for (const bons of [["updated"], ["updated", "rate_limited"], ["rate_limited"]]) {
      const v = await page.evaluate((estados) => window.refreshVerdict({
        ok: true, still_updating: 0,
        items: estados.map((s, n) => ({ item_id: `i${n}`, state: s })) }), bons);
      assert.equal(v.tone, "ok", `${bons.join("+")} tinha que ficar verde`);
      assert.match(v.msg, /tudo em dia/i, v.msg);
    }
    // Codex #166, 3ª rodada: a INSTRUÇÃO vem do backend, não desta tabela.
    // `WAITING_USER_ACTION` pede autorizar o dispositivo / ler o QR antes do
    // `userAction.expiresAt`; mandar "reautorize" faz perder essa janela.
    //
    // UM CASO POR ESTADO DE DETALHE VARIÁVEL, e é isto que a paridade de
    // `tests/test_of_health.py` NÃO alcança: lá basta o JS MENCIONAR
    // `i.detail`, aqui a mensagem tem de CONTER o detalhe do item. Uma entrada
    // que devolve a mesma frase fixa nos dois ramos do ternário passa lá e
    // falha aqui — os três detalhes abaixo são valores REAIS do backend
    // (`_DETALHE_POR_STATUS`, `_FIXED_DETAIL` e `_stale_detail`).
    for (const [state, detail, proibido] of [
      ["needs_user_action", "Autorize o acesso no app do banco", /reautoriz/i],
      // CONTROLE POSITIVO: não trocamos uma frase fixa errada por outra — o
      // detalhe do caso comum é este, e é ele que sai.
      ["needs_user_action", "Reautorize o banco", null],
      ["updating",          "Ainda não sincronizou", null],
      ["partial",           "Cartão desatualizado desde 12/08", null],
    ]) {
      const v = await page.evaluate(([s, d]) => window.refreshVerdict({ ok: false, items: [{
        item_id: "a", institution: "Nubank", state: s, label: "x", detail: d }] }), [state, detail]);
      assert.notEqual(v.tone, "ok", v.msg);
      // O JS só minúscula a 1ª letra, para o detalhe entrar no meio da frase.
      const esperado = detail.charAt(0).toLowerCase() + detail.slice(1);
      assert.ok(v.msg.includes(esperado),
                `${state}: o detalhe "${detail}" sumiu da mensagem → ${v.msg}`);
      // ...e o detalhe SOZINHO não basta: com dois bancos conectados, "Ação
      // necessária: autorize o acesso no app do banco" não diz em QUAL. Sem
      // esta linha o grupo inteiro passa numa tabela que apagou o `ofNome(i)`
      // de todas as entradas (medido: 5 pass / 0 fail).
      assert.ok(v.msg.includes("Nubank"),
                `${state}: a mensagem não diz de qual banco → ${v.msg}`);
      if (proibido) assert.doesNotMatch(v.msg, proibido, v.msg);
    }

    // `detail` não-string: o `ofInstrucao` chamava `d.charAt` direto e um
    // número/objeto/array estourava `TypeError` subindo pelo `refreshVerdict`,
    // que não tem `try` em nenhum ponto do caminho — o toast sumia inteiro.
    // Não é alcançável pelo backend de hoje; a exposição TRIPLICOU nesta onda.
    for (const lixo of [42, { a: 1 }, ["x"], true]) {
      const v = await page.evaluate((d) => {
        try {
          return { msg: window.refreshVerdict({ ok: false, items: [{
            item_id: "a", institution: "Nubank", state: "partial", detail: d }] }).msg };
        } catch (e) { return { erro: String(e) }; }
      }, lixo);
      assert.ok(!v.erro, `detail=${JSON.stringify(lixo)} derrubou o veredito: ${v.erro}`);
      assert.ok(v.msg.includes("Nubank"), v.msg);
    }

    // Sem detalhe, a frase genérica continua — e é UMA só: o ramo
    // `still_updating > 0` e o fallback do `updating` compartilham a constante
    // (eram duas cópias literais; editar uma deixava a outra para trás).
    const semDetalhe = await page.evaluate(() => window.refreshVerdict({
      ok: false, still_updating: 0,
      items: [{ item_id: "a", state: "updating", detail: null }] }));
    assert.match(semDetalhe.msg, /ainda está atualizando/i, semDetalhe.msg);
    const soContador = await page.evaluate(() => window.refreshVerdict({
      ok: false, still_updating: 2, items: [] }));
    assert.equal(soContador.msg, semDetalhe.msg, "as duas frases de 'atualizando' divergiram");

    // Só cooldown: verde, mas dizendo que não pediu coleta nova.
    const cooldown = await page.evaluate(() => window.refreshVerdict({
      ok: true, items: [{ item_id: "a", state: "rate_limited" }] }));
    assert.equal(cooldown.tone, "ok");
    assert.match(cooldown.msg, /acabou de atualizar/i, cooldown.msg);
  } finally { await page.__ctx.close(); }
});

/**
 * O toast é a ÚNICA superfície dessas mensagens (mobile/desktop web; no app e
 * na PWA o gesto descarta a string) — e ele CORTAVA: `white-space:nowrap` com
 * `position:fixed; left:50%` e sem teto de largura punha o fim da frase fora da
 * viewport, com `scrollWidth` igual ao da tela (não havia scroll que revelasse).
 * Medido antes: 11 das 12 mensagens cortavam a 390.
 *
 * `isMobile: true` NÃO é decoração. Esta página tem overflow horizontal
 * pré-existente (`.top-actions`), e com a meta viewport respeitada o
 * bloco-contêiner do `fixed` vira 379px numa tela de 320 — é o único jeito de o
 * teste ver o defeito que `margin-inline:auto` entre left/right deixava (medido:
 * 45..333, 13px fora). Sem `isMobile` o Chromium dá innerWidth = clientWidth e o
 * caso de 320 não discrimina nada.
 *
 * O invariante é "o texto não está cortado NEM espremido", e não "a caixa tem
 * ≥300px": uma copy curta e correta ("Tudo em dia!", 138.8px) reprovaria numa
 * asserção de largura mínima sem ter defeito nenhum. Por isso são duas medidas
 * observáveis: a caixa cabe na tela, e o texto cabe na caixa.
 *
 * ESTE TESTE MEDE UM EIXO SÓ, e o nome dele diz isso de propósito. No eixo
 * VERTICAL o mesmo overflow horizontal da página estica o bloco-contêiner do
 * `fixed` para 1000px de altura contra um `clientHeight` de 844, e o
 * `bottom: 28px` passa a medir de 1000: medido, a 320 o toast fica em
 * y=914..972 (fora da tela inteiro) e a 360 em y=803..861 (17px cortados
 * embaixo). A partir de 375 ele aparece. **Isso NÃO é regressão desta onda** —
 * a baseline `9cec25c` dá o mesmo y a 320 — e a causa é o overflow da página
 * (`.top-actions` / `.btn-topbar.logout`), que é pré-existente e está fora do
 * escopo desta onda de validação.
 *
 * Não escrevi asserção de Y porque ela reprovaria hoje, e um teste que reprova
 * por um defeito que a onda decidiu não consertar vira um vermelho que se
 * aprende a ignorar. O número está aqui para quem for consertar o overflow da
 * página: acrescente a asserção de Y no mesmo laço, ela é uma linha.
 *
 * E o de sempre: tudo isto é Chromium emulado. O Safari real pode encolher a
 * página em vez de esticar o layout viewport, e aí o toast estaria visível a
 * 320. Ninguém mediu — não leia daqui um veredito sobre o aparelho.
 */
test("toast do refresh: cabe na LARGURA da tela e o texto cabe na caixa, de 320 a 1440", async () => {
  const page = await newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  try {
    await page.goto(`${ORIGIN}/settings.html?view=open-finance`);
    await waitFor(() => page.evaluate(() => typeof window.refreshVerdict === "function"),
                  "refreshVerdict existir na página");

    const CASOS = [
      // A instrução mais longa do Open Finance, a que mais cortava.
      ["needs_user_action", "Autorize o acesso no app do banco", null],
      ["paused", null, null],
      // CONTROLE POSITIVO da asserção: copy curta é legítima e não pode reprovar.
      [null, null, "Tudo em dia!"],
      // `err.message` do servidor cai no toast em 21 chamadas desta página, e
      // traz o `detail` cru. Token SEM oportunidade de quebra, de propósito:
      // uma URL longa NÃO serve aqui — o UA quebra depois de "/" sozinho e o
      // caso passa com e sem `overflow-wrap` (medido: 6 pass / 0 fail com a
      // regra removida). Com o JWT abaixo, sem a regra: scrollWidth 508 numa
      // caixa de 356 a 390 — e a CAIXA continua em 16..374, ou seja a asserção
      // de "cabe na tela" não vê nada; quem vê é a de "texto cabe na caixa".
      [null, null, "Erro: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9eyJzdWIiOiIxMjM0NTY3ODkwIn0"],
    ];

    // 320 é o piso real (iPhone SE 1ª geração / Android pequeno); 1440, o desktop.
    for (const larg of [320, 360, 375, 390, 768, 1440]) {
      await page.setViewportSize({ width: larg, height: 844 });
      for (const [estado, detail, literal] of CASOS) {
        const r = await page.evaluate(([s, d, lit]) => {
          const msg = lit ?? window.refreshVerdict({ ok: false, items: [{
            item_id: "a", institution: "Nubank", state: s, label: "x", detail: d }] }).msg;
          window.showToast(msg, "error");
          const el = document.getElementById("toast");
          const b = el.getBoundingClientRect();
          return { msg, left: b.left, right: b.right,
                   scrollW: el.scrollWidth, clientW: el.clientWidth,
                   vw: document.documentElement.clientWidth, icb: window.innerWidth };
        }, [estado, detail, literal]);

        const rot = `${larg}px / ${estado || "literal"}`;
        // 1) a CAIXA cabe na tela visível. `clientWidth` do <html>, não
        //    `innerWidth`: com o overflow da página os dois divergem (320 × 379),
        //    e é o de 320 que o usuário enxerga.
        assert.ok(r.left >= 0 && r.right <= r.vw,
                  `${rot}: toast fora da tela — left=${r.left} right=${r.right} `
                  + `vw=${r.vw} (icb=${r.icb}) | ${r.msg}`);
        // 2) o TEXTO cabe na caixa: sem isto, "cabe na tela" é satisfeito por
        //    uma caixa pequena com a frase transbordando por dentro.
        assert.ok(r.scrollW <= r.clientW,
                  `${rot}: texto transbordando a caixa — scrollWidth=${r.scrollW} `
                  + `clientWidth=${r.clientW} | ${r.msg}`);
      }
    }

  } finally { await page.__ctx.close(); }
});

/**
 * ONDA 2, regressão dos dois invariantes do gesto que ainda não tinham teste:
 *
 *  a) puxar a tela em `?view=open-finance` chama o REFRESH (POST .../refresh =
 *     PATCH na Pluggy + sync), não só o `loadData` que relê o snapshot. Antes da
 *     Onda 1 era loadData: o gesto repintava dado velho e parecia ter funcionado.
 *  b) `settings.html` NÃO abre WebSocket. O auto-update ao vivo é só do
 *     dashboard, que já tem a conexão; abrir uma segunda aqui dobra socket por
 *     usuário sem ninguém escutar do outro lado.
 *
 * Discrimina: trocar o corpo do PBRefresh de open-finance por `loadData(...)`
 * deixa (a) vermelho; um `new WebSocket(...)` em qualquer script da página
 * deixa (b) vermelho.
 */
test("PTR do OF chama o refresh real, e settings não abre WebSocket", async () => {
  const page = await newPage({ userAgent: APP_UA, viewport: { width: 390, height: 844 },
                               hasTouch: true, isMobile: true });
  try {
    await page.addInitScript(() => {
      window.__ws = [];
      const Real = window.WebSocket;
      window.WebSocket = function (url, ...rest) { window.__ws.push(String(url)); return new Real(url, ...rest); };
      window.WebSocket.prototype = Real.prototype;
    });
    // A aba de OF só existe com a flag de rollout ligada; sem ela o
    // `showSettingsSection` cai em `security` e o gesto não tem o que atualizar.
    await page.route("**/auth/me", (route) =>
      route.fulfill(json({ app_access: true, plan_tier: "pro", of_ui_enabled: true })));
    const chamadas = [];
    await page.route("**/open-finance/**", (route) => {
      const req = route.request();
      chamadas.push(`${req.method()} ${new URL(req.url()).pathname}`);
      return route.fulfill(json({ sync: { ok: true, still_updating: 0, items: [] },
                                  connections: [], accounts: [], transactions: [] }));
    });

    await page.goto(`${ORIGIN}/settings.html?view=open-finance`);
    await waitFor(() => page.evaluate(() => typeof window.PBRefresh === "function"),
                  "PBRefresh existir");
    await waitFor(() => page.evaluate(() =>
      new URLSearchParams(location.search).get("view") === "open-finance"),
      "a aba de Open Finance ficar ativa");
    await sleep(300);
    chamadas.length = 0;                       // ignora o load inicial
    await page.evaluate(() => window.PBRefresh());

    const refresh = chamadas.filter((c) => c.startsWith("POST") && c.endsWith("/refresh"));
    assert.equal(refresh.length, 1, `o gesto tem que pedir refresh real, veio: ${chamadas.join(", ")}`);

    // (b) nenhuma das duas portas: nem WebSocket nativo, nem socket.io.
    assert.deepEqual(await page.evaluate(() => window.__ws), [],
                     "settings.html não pode abrir WebSocket — o ao vivo é do dashboard");
  } finally { await page.__ctx.close(); }
});

/**
 * ONDA 2: o backend já prova que `connection_ui_state` devolve
 * `updating`/"Ainda não sincronizou"; isto prova o que o USUÁRIO vê. A linha da
 * conexão mostra `Última sync: pendente` (fmtDate(null)) e a pílula tem que
 * concordar com ela — dizer "Tudo em dia!" logo acima de "pendente" foi
 * exatamente o sintoma desta onda.
 *
 * Discrimina: com `OF_PILL_CLASS[ui.state]` caindo em "ok" para `updating`, ou
 * com a pílula pintada de verde, a 2ª asserção fica vermelha.
 */
test("linha da conexão sem sync: pílula pendente e 'Última sync: pendente'", async () => {
  const page = await newPage({ viewport: { width: 390, height: 844 } });
  try {
    await page.goto(`${ORIGIN}/settings.html?view=open-finance`);
    await waitFor(() => page.evaluate(() => typeof window.renderConnections === "function"),
                  "renderConnections existir");

    const linha = await page.evaluate(() => {
      window.renderConnections([{
        institution_name: "Nubank", provider_item_id: "i1", last_sync_at: null,
        ui: { state: "updating", label: "Atualizando…", detail: "Ainda não sincronizou" },
      }]);
      const row = document.querySelector("#connections-list .connection-row");
      const pill = row.querySelector(".pill");
      return { texto: row.innerText, pill: pill.className, label: pill.textContent.trim() };
    });

    assert.match(linha.texto, /Última sync: pendente/, linha.texto);
    assert.match(linha.texto, /Ainda não sincronizou/, linha.texto);
    assert.equal(linha.label, "Atualizando…");
    // "active" é a classe VERDE do mapa (OF_PILL_CLASS, settings.html:2792) — a
    // única que o estado sem sync não pode receber.
    assert.ok(!/\bactive\b/.test(linha.pill),
              `a pílula não pode ser verde ao lado de "pendente" (veio "${linha.pill}")`);
    assert.match(linha.pill, /pending/, linha.pill);
  } finally { await page.__ctx.close(); }
});

test("dashboard: rajada de open_finance_synced vira UM get_month", async () => {
  const page = await newPage();
  try {
    // WebSocket falso: guarda o que a página manda e deixa a gente empurrar
    // mensagens do servidor. Chart é stub porque o CDN está bloqueado aqui.
    await page.addInitScript(() => {
      window.__sent = [];
      window.Chart = function () { return { destroy() {}, update() {}, data: {}, options: {} }; };
      window.Chart.register = () => {};
      class FakeWS {
        constructor(url) {
          this.url = url; this.readyState = 1; window.__ws = this;
          setTimeout(() => this.onopen && this.onopen(), 0);
        }
        send(data) { window.__sent.push(JSON.parse(data)); }
        close() {}
      }
      FakeWS.OPEN = 1;
      window.WebSocket = FakeWS;
    });

    await page.goto(`${ORIGIN}/dashboard.html`);
    await waitFor(() => page.evaluate(() => !!window.__ws), "o dashboard abrir o WebSocket");

    const antes = await page.evaluate(() =>
      window.__sent.filter((m) => m.type === "get_month").length);

    // três eventos em 200ms, como a Pluggy manda
    await page.evaluate(async () => {
      const msg = JSON.stringify({ type: "open_finance_synced", item_id: "item-1" });
      for (let i = 0; i < 3; i++) {
        window.__ws.onmessage({ data: msg });
        await new Promise((r) => setTimeout(r, 70));
      }
    });

    await sleep(2500);   // debounce de 1,5s + folga
    const depois = await page.evaluate(() =>
      window.__sent.filter((m) => m.type === "get_month").length);

    assert.equal(depois - antes, 1,
      `esperava 1 get_month para a rajada (antes=${antes}, depois=${depois})`);
  } finally { await page.__ctx.close(); }
});
