/**
 * sidenav_grupos.test.mjs — menus colapsáveis da barra lateral do dashboard.
 *
 * A redução da sidenav (15 itens soltos → Início/Visão Geral/Agentes + 4
 * menus) trouxe uma mecânica nova: grupos fechados por padrão, estado
 * persistido em localStorage e o menu da view ativa abrindo sozinho. O
 * harness do chat (`agent_chat_fixture.mjs`) NÃO carrega o dashboard.js —
 * lá os grupos são abertos à força; aqui o dashboard.js roda de verdade
 * (mesmo arranjo do `_dashboard_loader.mjs`) sobre a sidenav REAL extraída
 * do dashboard.html.
 *
 * Cobre: boot fechado, toggle com persistência, auto-abertura do menu da
 * view ativa (senão o item ativo ficaria invisível) e boot com estado
 * persistido.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import {
  abrirBrowser, fecharBrowser, DASHBOARD_JS, LABELS_JS, SIDENAV_GROUPS_JS, IDS,
} from "./_dashboard_loader.mjs";

const DASHBOARD_HTML = join(
  dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend", "dashboard.html",
);
const fonte = await readFile(DASHBOARD_HTML, "utf8");
const sidenav = fonte.match(/<aside class="sidenav"[\s\S]*?<\/aside>/)?.[0];
if (!sidenav) throw new Error("dashboard.html sem <aside class=\"sidenav\">.");

before(abrirBrowser);
after(fecharBrowser);

/** Página com os IDs que o dashboard.js toca + a sidenav real; `estado`
 *  pré-grava o localStorage pb_sidenav_groups antes do script rodar.
 *  Serve numa origem de verdade (route+goto): em about:blank o Chromium
 *  nega localStorage e a persistência não é medível. */
async function paginaComSidenav(estado = null) {
  const { novaPagina } = await import("./_dashboard_loader.mjs");
  const page = await novaPagina();
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));
  const corpo = IDS.map((i) => `<div id="${i}"></div>`).join("") + sidenav;
  await page.route("https://dash.test/", (route) =>
    route.fulfill({ contentType: "text/html", body: `<!doctype html><body>${corpo}</body>` }),
  );
  await page.goto("https://dash.test/");
  await page.evaluate(() => { window.fetch = () => new Promise(() => {}); });
  if (estado) {
    await page.evaluate(
      (s) => localStorage.setItem("pb_sidenav_groups", JSON.stringify(s)), estado,
    );
  }
  await page.addScriptTag({ path: LABELS_JS });
  await page.addScriptTag({ path: SIDENAV_GROUPS_JS });
  await page.addScriptTag({ path: DASHBOARD_JS });
  assert.deepEqual(errs, [], "dashboard.js não executou até o fim");
  return page;
}

const GRUPOS = ["acompanhamento", "planejamento", "ferramentas", "credito"];

test("boot sem estado persistido deixa todos os menus fechados", async () => {
  const page = await paginaComSidenav();
  try {
    const abertos = await page.evaluate(() =>
      [...document.querySelectorAll(".sidenav-group")].map((g) => ({
        grupo: g.dataset.group,
        open: g.classList.contains("open"),
        aria: g.querySelector(".sidenav-group-toggle").getAttribute("aria-expanded"),
      })),
    );
    assert.deepEqual(abertos, GRUPOS.map((grupo) => ({ grupo, open: false, aria: "false" })));
  } finally { await page.close(); }
});

test("toggle pelo clique abre, fecha e persiste em localStorage", async () => {
  const page = await paginaComSidenav();
  try {
    await page.click('.sidenav-group-toggle[aria-controls="sn-sub-ferramentas"]');
    assert.equal(
      await page.evaluate(() =>
        document.querySelector('.sidenav-group[data-group="ferramentas"]').classList.contains("open")),
      true,
    );
    assert.equal(
      await page.evaluate(() =>
        JSON.parse(localStorage.getItem("pb_sidenav_groups")).ferramentas),
      true,
    );
    // o subitem do menu aberto ficou visível
    assert.equal(
      await page.evaluate(() =>
        getComputedStyle(document.querySelector('#sn-sub-ferramentas')).display),
      "block",
    );

    await page.click('.sidenav-group-toggle[aria-controls="sn-sub-ferramentas"]');
    assert.equal(
      await page.evaluate(() =>
        JSON.parse(localStorage.getItem("pb_sidenav_groups")).ferramentas),
      false,
    );
  } finally { await page.close(); }
});

test("menu da view ativa abre sozinho ao navegar", async () => {
  const page = await paginaComSidenav();
  try {
    // Orçamentos vive em Ferramentas: com o menu fechado, navegar pra view
    // tem que abri-lo — senão o item ativo fica invisível.
    await page.evaluate(() => setMainView("budgets"));
    assert.equal(
      await page.evaluate(() =>
        document.querySelector('.sidenav-group[data-group="ferramentas"]').classList.contains("open")),
      true,
    );
    // e o estado aberto persiste, como num toggle manual
    assert.equal(
      await page.evaluate(() =>
        JSON.parse(localStorage.getItem("pb_sidenav_groups")).ferramentas),
      true,
    );
    // view de topo (fora de menu) não abre nada
    await page.evaluate(() => setMainView("agentes"));
    const demais = await page.evaluate(() =>
      ["acompanhamento", "planejamento", "credito"].map((n) =>
        document.querySelector(`.sidenav-group[data-group="${n}"]`).classList.contains("open")),
    );
    assert.deepEqual(demais, [false, false, false]);
  } finally { await page.close(); }
});

test("boot com estado persistido reabre o menu salvo", async () => {
  const page = await paginaComSidenav({ credito: true });
  try {
    const abertos = await page.evaluate(() =>
      [...document.querySelectorAll(".sidenav-group")].map((g) => g.classList.contains("open")),
    );
    assert.deepEqual(abertos, [false, false, false, true]);
    assert.equal(
      await page.evaluate(() =>
        document.querySelector('.sidenav-group-toggle[aria-controls="sn-sub-credito"]')
          .getAttribute("aria-expanded")),
      "true",
    );
  } finally { await page.close(); }
});
