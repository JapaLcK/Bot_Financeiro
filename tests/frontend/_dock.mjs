/**
 * Máquinário do dock do modo app, compartilhado por `dock_quarta_aba.test.mjs`
 * (cargas MPA) e `dock_quarta_aba_spa.test.mjs` (troca de tela pelo pb-nav).
 *
 * Mora fora dos dois porque são o mesmo "como" descrito duas vezes (§0.7) e
 * porque juntos passavam do teto de 350 linhas do `quality/max-lines` (§0.5).
 * O "por quê" de cada decisão de espera/medição está no cabeçalho de cada
 * arquivo de teste; aqui fica só o "como".
 */
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

export const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
// 127.0.0.1: só nele o app-mode.js mapeia `/changelog.html` etc. A porta é
// ficção — toda requisição é atendida pela rota, nada vai para a rede.
export const ORIGIN = "http://127.0.0.1:1";
export const APP_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 PigBankApp/1.0";
export const PRO = { plan: "pro", changelog_enabled: true };
export const FREE = { plan: "free", changelog_enabled: false };
// Trecho do path de cada ícone, copiado dos TABS/NEWS_TAB de app-mode.js.
export const ICO = { "O que pedir": "M21 11.5a8.4", "Notícias": "M3 6a1 1 0 0 1 1-1h13", "Início": "M3 10.5 12 3l9 7.5" };
export const ESPERA = { timeout: 30_000 };
export const LIMITE = { timeout: 120_000 };
export const json = (b) => ({ status: 200, contentType: "application/json", body: JSON.stringify(b) });

/** Abre `pagina` no modo app com o cache do plano semeado e o /auth/me dado. */
export async function abrir(browser, pagina, cache, me) {
  const ctx = await browser.newContext({ userAgent: APP_UA, viewport: { width: 390, height: 844 },
                                         reducedMotion: "reduce", serviceWorkers: "block" });
  await ctx.route("**/*", (r) => {
    const url = new URL(r.request().url());
    if (url.origin !== ORIGIN) return r.abort();
    if (url.pathname === "/auth/me") return r.fulfill(json(me));
    if (url.pathname === "/auth/validate") return r.fulfill(json({ user_id: 1 }));
    // O motor SPA (pb-nav.js) busca a próxima página pelo caminho de PRODUÇÃO,
    // sem ".html" (é o href gravado nas TABS de app-mode.js, igual em qualquer
    // host). O warmUp() de pb-nav.js pode prefetchar por esse caminho mesmo
    // quando o tap pediu a variante ".html" (as duas mapeiam pra mesma "key"
    // em ROUTES, e o cache do motor é por key) — sem os dois aqui, um teste
    // com prefetch em voo recebia o JSON genérico `{}` do ramo seguinte e o
    // DOMParser via lixo. Produção serve o mesmo comandos-app.html nos dois
    // caminhos (routes/static_pages.py); replicamos isso aqui.
    if (url.pathname === "/home") return r.fulfill({ path: join(FRONTEND, "home.html") });
    if (url.pathname === "/comandos-app") return r.fulfill({ path: join(FRONTEND, "comandos-app.html") });
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

/**
 * Como `abrir`, mas liga o motor SPA (`?pbspa=1`, sessionStorage — pb-nav.js)
 * e confirma que ligou antes de devolver a página. As três condições do gate
 * (modo app, flag, startViewTransition/DOMParser) — ver docs/armadilhas.md —
 * já valem aqui: UA do app (`abrir` usa APP_UA) e Chromium tem as duas APIs.
 */
export async function abrirSpa(browser, pagina, cache, me) {
  const { ctx, page } = await abrir(browser, `${pagina}?pbspa=1`, cache, me);
  assert.equal(await page.evaluate(() => !!(window.PBNav && window.PBNav.enabled)), true,
    "PBNav.enabled deveria ser true com ?pbspa=1 dentro do modo app");
  return { ctx, page };
}

/**
 * Toque na aba de `href` e espera o motor SPA assentar em `caminho`: o
 * pushState (dentro do commit síncrono do swap) muda `location.pathname`
 * antes de qualquer animação — esperar por ele é esperar por ESTADO, não por
 * tempo. `assentar` cobre o resto (transições ≤1ms da barra + dois frames).
 */
export async function tocar(page, href, caminho) {
  await page.click(`.pb-tab[href="${href}"]`);
  await page.waitForFunction((p) => location.pathname === p, caminho, ESPERA)
    .catch(async (e) => {
      const onde = await page.evaluate(() => location.pathname);
      throw new Error(`toque em "${href}" não chegou a "${caminho}" (ficou em "${onde}") — ${e.message}`);
    });
  await assentar(page);
}

export const doisFrames = (page) => page.evaluate(() => new Promise((ok) =>
  requestAnimationFrame(() => requestAnimationFrame(ok))));

/**
 * Navega e espera por estado (ver o cabeçalho de cada teste). O /auth/me é
 * armado ANTES, e no mesmo Promise.all: `await` separado deixava a espera
 * rejeitar sem dono (unhandledRejection) quando a navegação sozinha passava
 * do timeout.
 */
export async function carregar(page, navegar, me) {
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
export async function assentar(page) {
  await page.waitForFunction(() => !document.querySelector(".pb-tabbar").getAnimations({ subtree: true })
    .some((a) => a.effect.getTiming().duration <= 1), null, ESPERA)
    .catch(async (e) => {
      const r = await estado(page).then(resumo, (x) => `estado ilegível em ${page.url()}: ${x.message.split("\n")[0]}`);
      throw new Error(`barra não assentou (transição de até 1 ms correndo) — ${r} — ${e.message}`);
    });
  await doisFrames(page);
}

/** Retrato do dock: abas (rótulo, classes, centro x, ícone) e a bolha. */
export const estado = (page) => page.evaluate(() => {
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
      href: a.getAttribute("href"),
      active: a.classList.contains("active"),
      live: a.classList.contains("pb-live"),
      aria: a.getAttribute("aria-current"),
      cx: cx(a),
      ico: a.querySelector(".pb-tab-ico").innerHTML,
    })),
  };
});

export const nomeIco = (h) => Object.keys(ICO).find((k) => h.includes(ICO[k])) || (h ? "outro" : "vazio");
export const resumo = (s) => `abas [${s.abas.map((a) => a.label + (a.active ? "*" : "") + (a.live ? "~" : "")).join(", ")}]` +
  ` bolha x=${s.beadCx.toFixed(1)} ícone=${nomeIco(s.beadIco)} cache=${s.cache} doc=${s.doc}`;

/** Problemas do invariante (lista vazia = ok). Não lança: cada carga é relatada. */
export function problemas(s, rotulo, onde) {
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

/** Problemas do 4º lugar sozinho: rótulo, href e ícone batendo entre si. */
export function quartaAba(rotulo, href) {
  return (s, onde) => {
    const q = s.abas[2];
    if (!q || q.label !== rotulo || q.href !== href || !q.ico.includes(ICO[rotulo])) {
      return [`${onde}: 4º lugar esperava "${rotulo}" (${href}) — ${resumo(s)}`];
    }
    return [];
  };
}

/**
 * Duas cargas (a semeada e o reload): o invariante vale nas duas. Que o
 * cache chegou ao plano do /auth/me é condição da espera, não asserção.
 */
export async function duasCargas(browser, pagina, cache, me, rotulo, extra = () => []) {
  const { ctx, page } = await abrir(browser, pagina, cache, me);
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
