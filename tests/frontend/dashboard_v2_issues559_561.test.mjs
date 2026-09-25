/**
 * Protótipo dashboard-v2, issues #559, #560 e #561 (apontamentos do Codex no PR #546):
 *  - #559: /simulador, /metas e /patrimonio ignoram o mês escolhido; o topbar esconde o
 *    seletor ali (router.ts NO_MONTH) sem perder o mês de quem volta ao Resumo.
 *  - #560: o lançamento escolhido na paleta leva também o DIA ao extrato, não só o nome
 *    (antes "iFood" abria todo iFood, iFood · Poke… do mês).
 *  - #561: os valores mensais do patrimônio (e a divisão) existiam só no hover; agora há
 *    uma tabela sr-only, igual à do TrajectoryChart.
 *
 * Rodar:  npm run test:frontend   (o `before` gera o bundle, gitignored, em dashboard-v2/)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { execSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const ORIGIN = "http://127.0.0.1:1"; // fictícia: a rota atende da raiz do repositório

let browser;
before(async () => {
  execSync("npm --prefix webapp run build:dashboard", { cwd: ROOT, stdio: "pipe" });
  browser = await chromium.launch();
});
after(() => browser?.close());

async function abrir(hash, width = 1440) {
  const ctx = await browser.newContext({ viewport: { width, height: 1000 }, reducedMotion: "reduce" });
  await ctx.route("**/*", (r) => {
    const url = new URL(r.request().url());
    if (url.origin !== ORIGIN) return r.abort();
    const path = decodeURIComponent(url.pathname).replace(/\/$/, "/index.html");
    return r.fulfill({ path: join(ROOT, path) }).catch(() => r.fulfill({ status: 404, body: "" }));
  });
  // já escolheu o perfil (Pular): sem isso o modal da 1ª visita cobre o Resumo
  await ctx.addInitScript(() => localStorage.setItem("pigbank.dashboard.profile.v1", '"padrao"'));
  const page = await ctx.newPage();
  await page.goto(`${ORIGIN}/dashboard-v2/#${hash}`);
  await page.locator("#page-title").waitFor();
  return { ctx, page };
}

const ir = async (page, hash) => {
  await page.evaluate((h) => { location.hash = h; }, hash);
  await page.waitForFunction((h) => location.hash === `#${h}` && document.querySelector(".page")?.dataset.page === h, hash);
};

test("#559: sem seletor de mês nas páginas só-presente, e o mês escolhido sobrevive à volta", async () => {
  const { ctx, page } = await abrir("/");
  const titulo = () => page.locator(".month-title").textContent();
  const atual = await titulo();
  await page.getByRole("button", { name: "Mês anterior", exact: true }).click();
  const escolhido = await titulo();
  const seletor = {};
  for (const p of ["/simulador", "/metas", "/patrimonio", "/previsao", "/gastos", "/lancamentos"]) {
    await ir(page, p);
    seletor[p] = await page.locator(".month-switch").count();
  }
  await ir(page, "/");
  const volta = await titulo();
  await ctx.close();
  assert.notEqual(escolhido, atual);
  assert.deepEqual(seletor, { "/simulador": 0, "/metas": 0, "/patrimonio": 0, "/previsao": 1, "/gastos": 1, "/lancamentos": 1 });
  assert.equal(volta, escolhido);
});

test("#559: sem o seletor, o topbar mantém a altura e os botões à direita (1440 e 390)", async () => {
  for (const width of [1440, 390]) {
    const { ctx, page } = await abrir("/", width);
    const medir = () => page.evaluate(() => {
      const bar = document.querySelector(".topbar").getBoundingClientRect();
      const btn = document.querySelector(".topbar .btn-primary").getBoundingClientRect();
      return { h: bar.height, folgaDireita: Math.round(bar.right - btn.right), rolagem: document.documentElement.scrollWidth - innerWidth };
    });
    const com = await medir();
    await ir(page, "/patrimonio");
    const sem = await medir();
    await ctx.close();
    assert.equal(sem.h, com.h, `${width}: altura`);
    assert.equal(sem.folgaDireita, com.folgaDireita, `${width}: Simular saiu do lugar (${JSON.stringify({ com, sem })})`);
    assert.ok(sem.rolagem <= 0, `${width}: rolagem horizontal ${sem.rolagem}`);
  }
});

test("#560: o iFood de 23/09 escolhido na paleta abre só aquele dia no extrato", async () => {
  const { ctx, page } = await abrir("/lancamentos");
  await page.keyboard.press("Control+k");
  await page.locator(".cmdk input").fill("ifood");
  const opcoes = await page.locator(".cmdk [role=option]").allTextContents();
  await page.locator(".cmdk [role=option]").filter({ hasText: /^iFood23 set/ }).click();
  // a busca do extrato passa por useDeferredValue: espera a lista assentar antes de ler
  await page.waitForFunction(() => [...document.querySelectorAll(".ledger .row-label")].every((l) => /ifood/i.test(l.textContent)), null, { timeout: 2000 }).catch(() => {});
  const r = {
    dias: await page.locator(".ledger .day-head span:first-child").allTextContents(),
    linhas: await page.locator(".ledger .row-label").allTextContents(),
    chips: await page.locator(".ledger-chips .chip").allTextContents(),
    busca: await page.locator(".ledger .search input").inputValue(),
  };
  await ctx.close();
  assert.ok(opcoes.filter((o) => /^iFood/.test(o)).length > 1, `a busca tem de achar mais de um iFood no mês: ${JSON.stringify(opcoes)}`);
  assert.equal(r.dias.length, 1, JSON.stringify(r.dias));
  assert.match(r.dias[0], /\b23\b/);
  assert.deepEqual(r.linhas, ["iFood"]); // nos dados, 23/09 tem um iFood só
  assert.deepEqual(r.chips.map((c) => c.replace("remover filtro", "").trim()), ["dia 23"]);
  assert.equal(r.busca, "iFood");
});

test("#561: tabela acessível do patrimônio, uma linha por mês, igual ao tooltip do gráfico", async () => {
  const { ctx, page } = await abrir("/patrimonio");
  const tabela = await page.locator(".w-nw table tbody tr").evaluateAll((trs) =>
    trs.map((tr) => [...tr.children].map((c) => c.textContent.trim())));
  const box = await page.locator(".w-nw .nw-chart").boundingBox();
  const n = tabela.length;
  const tooltip = [];
  for (let i = 0; i < n; i++) {
    await page.mouse.move(box.x + 6 + (i / (n - 1)) * (box.width - 12), box.y + box.height / 2);
    tooltip.push(await page.locator(".tip").evaluate((t) => [
      t.querySelector(".tip-title").textContent,
      ...[...t.querySelectorAll(".tip-row b")].map((b) => b.textContent),
      t.querySelector(".tip-value").textContent,
    ]));
  }
  const cabecalho = await page.locator(".w-nw table thead th").allTextContents();
  await ctx.close();
  assert.equal(n, 12); // NET_WORTH (lib/data.js) gera 12 meses
  assert.deepEqual(cabecalho, ["Mês", "Conta", "Caixinhas", "Investimentos", "Total"]);
  assert.deepEqual(tabela, tooltip);
});

test("#559: mês pela paleta numa página sem seletor leva ao Resumo; com seletor, fica onde está", async () => {
  const escolher = async (page) => {
    await page.keyboard.press("Control+k");
    await page.locator(".cmdk input").fill("agosto");
    await page.locator(".cmdk [role=option]").filter({ hasText: /^Agosto 2026$/ }).click();
  };
  const r = {};
  for (const p of ["/metas", "/gastos"]) {
    const { ctx, page } = await abrir(p);
    await escolher(page);
    // hashchange é assíncrono: espera o seletor mostrar agosto (sem o conserto, em /metas ele nunca aparece)
    await page.waitForFunction(() => document.querySelector(".month-title")?.textContent === "Agosto 2026", null, { timeout: 2000 }).catch(() => {});
    r[p] = await page.evaluate(() => ({ hash: location.hash, mes: document.querySelector(".month-title")?.textContent ?? null }));
    await ctx.close();
  }
  assert.deepEqual(r, { "/metas": { hash: "#/", mes: "Agosto 2026" }, "/gastos": { hash: "#/gastos", mes: "Agosto 2026" } });
});

test("#561 (irmão): o nome acessível do dia no calendário traz os mesmos maiores gastos do tooltip", async () => {
  const { ctx, page } = await abrir("/gastos");
  const dia = page.locator(".cal-day[data-step='6']").first(); // o dia mais caro: vários lançamentos
  const label = await dia.getAttribute("aria-label");
  await dia.hover();
  const tip = await page.locator(".tip").evaluate((t) =>
    [...t.querySelectorAll(".tip-row")].map((r) => `${r.querySelector("span:not(.tip-key)").textContent} ${r.querySelector("b").textContent}`));
  const futuro = await page.locator(".cal-day[data-future]").first().getAttribute("aria-label").catch(() => null);
  await ctx.close();
  assert.ok(tip.length >= 2, `o dia escolhido precisa ter mais de um item no tooltip: ${JSON.stringify(tip)}`);
  assert.equal(label.split(". Maiores: ")[1], tip.join(", "), label);
  if (futuro) assert.doesNotMatch(futuro, /Maiores/);
});
