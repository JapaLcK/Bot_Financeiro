/**
 * INVARIANTE do dock do modo app: em toda carga, a aba da página é a única
 * `active` e a única `pb-live`, tem `aria-current="page"`, a bolha está em
 * cima dela e mostra o ícone QUE ESTÁ DESENHADO nela.
 *
 * O 4º lugar da barra é disputado: "O que pedir" (/comandos-app) ou
 * "Notícias" (/changelog, Pro). Três defeitos quebravam o invariante, os três
 * vindos de a barra guardar à parte "qual aba é a da página" e "qual ícone
 * mostrar", separado das abas que ela desenhou:
 *   (a) ícone por POSIÇÃO FIXA (`TABS[i].icon`): com Notícias desenhada no 4º
 *       lugar a bolha mostrava o ícone de "O que pedir" — em /changelog a toda
 *       carga, e no arrasto sobre Notícias;
 *   (b) o 4º lugar decidia só pelo plano: Pro em /comandos-app ficava sem aba
 *       da página e a bolha caía em Início;
 *   (c) a troca do /auth/me reescreve a aba no lugar e não avisa a bolha nem
 *       o `pb-live` — com (b) consertado ela nunca tira a aba da página.
 * Decisão de produto (opção A): na página "O que pedir" o 4º lugar é "O que
 * pedir" mesmo para quem tem Notícias, e na changelog é Notícias mesmo para
 * quem a barra não trata como Pro; nas outras páginas vale o plano.
 *
 * O cache do plano (`localStorage.pbNewsTab`) é semeado por /manifest.json e
 * NÃO por addInitScript: o init script regravaria o valor antes do reload e
 * apagaria o cenário da carga seguinte, que é metade do bug.
 *
 * `/auth/me` manda `plan` e `changelog_enabled` sempre coerentes (pro com true,
 * free com false): o cache final que a espera exige sai de `me.plan`, e um
 * fixture que separe os dois pode travar a espera.
 *
 * Os arquivos saem do DISCO pela rota do Playwright, não do `_server.mjs`: com
 * suítes em paralelo o `http.server` resetava a conexão do `/app-mode.js`, a
 * barra não montava e o caso ficava vermelho por transporte — mesmo motivo de
 * `toast_fab_app_375.test.mjs`.
 *
 * Service worker BLOQUEADO e espera por ESTADO, não `networkidle` (mesma lição
 * do #447). A home.html registra o /service-worker.js; na carga 2 o
 * `app-mode.js` vinha por ele, o `networkidle` não enxerga pedido que passa
 * pelo worker e o reload voltava com `readyState=interactive`, sem barra —
 * vermelho intermitente sob carga. A espera é: resposta do /auth/me, documento
 * `complete`, 4 abas montadas e o cache com o plano do /auth/me. O cache só
 * PROVA que o syncNewsTab rodou na carga que o troca (a 1ª, com cache semeado
 * diferente do plano): ele grava o cache e troca a aba no mesmo callback
 * síncrono. Nas outras cargas o cache já está no valor final antes de carregar
 * e a espera não garante o callback; ali ele não mexe na barra
 * (`isPro === prev`), então o retrato é o mesmo antes ou depois dele. Um
 * travamento vira vermelho pelo `timeout` de cada teste.
 * E a barra tem de estar ASSENTADA: o bloco `@media (prefers-reduced-motion:
 * reduce)` do `site.css` põe `transition-duration: .01ms` em tudo — só a
 * changelog carrega esse CSS — e o
 * `transform` que o JS escreve na bolha vira transição. Logo após a espera
 * acima a bolha ainda fica alguns quadros em x=10 (sem translate) com o
 * `style.transform` já certo; dois rAF sozinhos não bastam. Só as transições
 * de até 1 ms são esperadas: esperar QUALQUER animação escondia uma bolha que
 * desliza devagar até a aba e chega lá antes da medição.
 *
 * Cego a: WKWebView real (o arrasto aqui é mouse, lá é toque); a
 * `env(safe-area-inset-*)`, que vale 0 no headless — nenhuma asserção abaixo
 * depende de inset, só de posição relativa bolha × aba; à troca de tela do
 * pb-nav (SPA, desligado por padrão): todo caso aqui é carga de documento; a
 * uma troca da barra feita DEPOIS de gravar o cache (um passo assíncrono a
 * mais no syncNewsTab): a espera termina no cache e o retrato sai antes dela;
 * à navegação AGENDADA ao soltar: o T4 roda com reduced-motion, onde soltar
 * navega na hora, e a do caminho animado (por `setTimeout`) não é vista aqui —
 * soltar na própria aba com movimento padrão foi conferido fora deste arquivo;
 * e à recarga da própria URL: o href da aba (`/changelog`) difere do caminho
 * servido aqui (`/changelog.html`), então navegação indevida aparece como
 * troca de caminho, e a recarga da própria URL — o que aconteceria em
 * produção — não é vista.
 *
 * Rodar: node --test tests/frontend/dock_quarta_aba.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
// 127.0.0.1: só nele o app-mode.js mapeia `/changelog.html` etc. A porta é
// ficção — toda requisição é atendida pela rota, nada vai para a rede.
const ORIGIN = "http://127.0.0.1:1";
const APP_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 PigBankApp/1.0";
const PRO = { plan: "pro", changelog_enabled: true };
const FREE = { plan: "free", changelog_enabled: false };
// Trecho do path de cada ícone, copiado dos TABS/NEWS_TAB de app-mode.js.
const ICO = { "O que pedir": "M21 11.5a8.4", "Notícias": "M3 6a1 1 0 0 1 1-1h13", "Início": "M3 10.5 12 3l9 7.5" };
const ESPERA = { timeout: 30_000 };
const LIMITE = { timeout: 120_000 };
const json = (b) => ({ status: 200, contentType: "application/json", body: JSON.stringify(b) });

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

/** Abre `pagina` no modo app com o cache do plano semeado e o /auth/me dado. */
async function abrir(pagina, cache, me) {
  const ctx = await browser.newContext({ userAgent: APP_UA, viewport: { width: 390, height: 844 },
                                         reducedMotion: "reduce", serviceWorkers: "block" });
  await ctx.route("**/*", (r) => {
    const url = new URL(r.request().url());
    if (url.origin !== ORIGIN) return r.abort();
    if (url.pathname === "/auth/me") return r.fulfill(json(me));
    if (url.pathname === "/auth/validate") return r.fulfill(json({ user_id: 1 }));
    if (!/\.[a-z0-9]+$/i.test(url.pathname)) return r.fulfill(json({}));
    return r.fulfill({ path: join(FRONTEND, decodeURIComponent(url.pathname)) })
      .catch(() => r.fulfill({ status: 404, body: "" }));
  });
  const page = await ctx.newPage();
  await page.goto(`${ORIGIN}/manifest.json`);
  await page.evaluate((v) => localStorage.setItem("pbNewsTab", v), cache);
  await carregar(page, () => page.goto(`${ORIGIN}${pagina}`, ESPERA), me);
  return { ctx, page };
}

const doisFrames = (page) => page.evaluate(() => new Promise((ok) =>
  requestAnimationFrame(() => requestAnimationFrame(ok))));

/**
 * Navega e espera por estado (ver o cabeçalho). O /auth/me é armado ANTES, e
 * no mesmo Promise.all: `await` separado deixava a espera rejeitar sem dono
 * (unhandledRejection) quando a navegação sozinha passava do timeout.
 */
async function carregar(page, navegar, me) {
  const cacheFinal = me.plan === "pro" ? "1" : "0";
  await Promise.all([
    page.waitForResponse((r) => new URL(r.url()).pathname === "/auth/me", ESPERA),
    navegar(),
  ]);
  await page.waitForFunction((c) => document.readyState === "complete"
    && document.querySelectorAll(".pb-tabbar .pb-tab:not(.pb-tab-fab)").length === 4
    && localStorage.getItem("pbNewsTab") === c, cacheFinal, ESPERA)
    .catch(async (e) => {
      const r = await estado(page).then(resumo, (x) => `estado ilegível em ${page.url()}: ${x.message.split("\n")[0]}`);
      throw new Error(`carga sem complete + 4 abas + cache=${cacheFinal} — ${r} — ${e.message}`);
    });
  await assentar(page);
}

/** Nenhuma transição de até 1 ms correndo na barra (ver o cabeçalho), e dois quadros. */
async function assentar(page) {
  await page.waitForFunction(() => !document.querySelector(".pb-tabbar").getAnimations({ subtree: true })
    .some((a) => a.effect.getTiming().duration <= 1), null, ESPERA)
    .catch(async (e) => {
      const r = await estado(page).then(resumo, (x) => `estado ilegível em ${page.url()}: ${x.message.split("\n")[0]}`);
      throw new Error(`barra não assentou (transição de até 1 ms correndo) — ${r} — ${e.message}`);
    });
  await doisFrames(page);
}

/** Retrato do dock: abas (rótulo, classes, centro x, ícone) e a bolha. */
const estado = (page) => page.evaluate(() => {
  const cx = (el) => { const r = el.getBoundingClientRect(); return r.left + r.width / 2; };
  const bead = document.querySelector(".pb-dock-bead");
  return {
    path: location.pathname,
    doc: `${location.pathname} ${document.readyState} [${document.documentElement.className}]`,
    cache: localStorage.getItem("pbNewsTab"),
    beadCx: bead ? cx(bead) : NaN,
    beadIco: document.querySelector(".pb-dock-bead-ico")?.innerHTML ?? "",
    abas: [...document.querySelectorAll(".pb-tabbar .pb-tab:not(.pb-tab-fab)")].map((a) => ({
      label: a.querySelector("span:last-child").textContent,
      active: a.classList.contains("active"),
      live: a.classList.contains("pb-live"),
      aria: a.getAttribute("aria-current"),
      cx: cx(a),
      ico: a.querySelector(".pb-tab-ico").innerHTML,
    })),
  };
});

const nomeIco = (h) => Object.keys(ICO).find((k) => h.includes(ICO[k])) || (h ? "outro" : "vazio");
const resumo = (s) => `abas [${s.abas.map((a) => a.label + (a.active ? "*" : "") + (a.live ? "~" : "")).join(", ")}]` +
  ` bolha x=${s.beadCx.toFixed(1)} ícone=${nomeIco(s.beadIco)} cache=${s.cache} doc=${s.doc}`;

/** Problemas do invariante (lista vazia = ok). Não lança: cada carga é relatada. */
function problemas(s, rotulo, onde) {
  const erros = [];
  const ativas = s.abas.filter((a) => a.active);
  if (ativas.length !== 1 || ativas[0].label !== rotulo) {
    erros.push(`${onde}: esperava só "${rotulo}" active — ${resumo(s)}`);
    return erros;
  }
  const a = ativas[0];
  const vivas = s.abas.filter((x) => x.live).map((x) => x.label);
  if (vivas.length !== 1 || vivas[0] !== rotulo) erros.push(`${onde}: pb-live em [${vivas}] — ${resumo(s)}`);
  if (a.aria !== "page") erros.push(`${onde}: "${rotulo}" sem aria-current=page — ${resumo(s)}`);
  if (!(Math.abs(s.beadCx - a.cx) <= 2)) erros.push(`${onde}: bolha x=${s.beadCx} longe da aba x=${a.cx} — ${resumo(s)}`);
  if (s.beadIco !== a.ico || !s.beadIco.includes(ICO[rotulo])) {
    erros.push(`${onde}: ícone da bolha "${nomeIco(s.beadIco)}" ≠ ícone de "${rotulo}" — ${resumo(s)}`);
  }
  return erros;
}

/**
 * Duas cargas (a semeada e o reload): o invariante vale nas duas. Que o
 * cache chegou ao plano do /auth/me é condição da espera, não asserção.
 */
async function duasCargas(pagina, cache, me, rotulo, extra = () => []) {
  const { ctx, page } = await abrir(pagina, cache, me);
  const erros = [];
  try {
    for (const carga of ["carga 1", "carga 2"]) {
      if (carga === "carga 2") await carregar(page, () => page.reload(ESPERA), me);
      const s = await estado(page);
      const onde = `${pagina} cache ${cache} → ${me.plan}, ${carga}`;
      erros.push(...problemas(s, rotulo, onde), ...extra(s, onde));
    }
  } finally { await ctx.close(); }
  return erros;
}

test("T1: Pro em O que pedir com cache 0 — a troca do plano não tira a aba da página", LIMITE, async () => {
  assert.deepEqual(await duasCargas("/comandos-app.html", "0", PRO, "O que pedir"), []);
});

test("T2: Pro em Notícias com cache 1 — a bolha mostra o ícone de Notícias", LIMITE, async () => {
  assert.deepEqual(await duasCargas("/changelog.html", "1", PRO, "Notícias"), []);
});

test("T3: cache velho nas duas direções, e não-Pro na changelog — a página vem antes do plano", LIMITE, async () => {
  const erros = [
    ...await duasCargas("/changelog.html", "0", PRO, "Notícias"),
    ...await duasCargas("/comandos-app.html", "1", FREE, "O que pedir"),
    // Quem o servidor deixa abrir a changelog sem ser `plan === "pro"` (Plus,
    // trial): a página vem antes do plano, então Notícias fica ativa.
    ...await duasCargas("/changelog.html", "0", FREE, "Notícias"),
  ];
  assert.deepEqual(erros, []);
});

test("T4: arrasto da bolha em Notícias — o ícone é o da aba sob ela, e soltar na página não navega", LIMITE, async () => {
  const { ctx, page } = await abrir("/changelog.html", "1", PRO);
  const erros = [];
  try {
    const s0 = await estado(page);
    const box = await page.locator(".pb-dock-bead").boundingBox();
    const y = box.y + box.height / 2;
    await page.mouse.move(box.x + box.width / 2, y);
    await page.mouse.down();
    for (let i = 0; i < s0.abas.length; i++) {
      await page.mouse.move(s0.abas[i].cx, y, { steps: 8 });
      await doisFrames(page);
      const s = await estado(page);
      const alvo = s.abas[i];
      const vivas = s.abas.filter((x) => x.live).map((x) => x.label);
      if (vivas.length !== 1 || vivas[0] !== alvo.label) {
        erros.push(`arrasto sobre "${alvo.label}": pb-live em [${vivas}] — ${resumo(s)}`);
      }
      if (s.beadIco !== alvo.ico) {
        erros.push(`arrasto sobre "${alvo.label}": bolha com ícone "${nomeIco(s.beadIco)}" — ${resumo(s)}`);
      }
    }
    const casa = s0.abas.find((a) => a.label === "Notícias");
    await page.mouse.move(casa.cx, y, { steps: 8 });
    // Dois frames parados antes de soltar: o tick zera a velocidade, senão a
    // projeção do soltar (x + vel*5) pode encaixar na vizinha.
    await doisFrames(page);
    await page.mouse.up();
    // Se soltar navegou, a página nova não tem barra e o assentar lança antes
    // da checagem de caminho: o motivo real sai do catch, pela URL.
    await assentar(page).catch((e) => {
      const path = new URL(page.url()).pathname;
      throw path === "/changelog.html" ? e : new Error(`soltar na aba da página navegou para ${path} — ${e.message}`);
    });
    const fim = await estado(page);
    if (fim.path !== "/changelog.html") erros.push(`soltar na aba da página navegou para ${fim.path}`);
    erros.push(...problemas(fim, "Notícias", "depois de soltar"));
  } finally { await ctx.close(); }
  assert.deepEqual(erros, []);
});

test("T5 (positivos): Free em O que pedir; fora da disputa o plano decide — Pro ganha Notícias, Free não", LIMITE, async () => {
  const quarta = (rotulo) => (s, onde) => (s.abas[2]?.label === rotulo ? []
    : [`${onde}: 4º lugar "${s.abas[2]?.label}", esperava "${rotulo}" — ${resumo(s)}`]);
  const erros = [
    ...await duasCargas("/comandos-app.html", "0", FREE, "O que pedir"),
    ...await duasCargas("/home.html", "0", PRO, "Início", quarta("Notícias")),
    // Cache velho de Pro num Free: sem este caso "Notícias para todo mundo" passava.
    ...await duasCargas("/home.html", "1", FREE, "Início", quarta("O que pedir")),
  ];
  assert.deepEqual(erros, []);
});
