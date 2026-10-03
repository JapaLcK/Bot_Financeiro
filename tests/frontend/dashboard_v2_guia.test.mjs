// Guia do /painel (#728, PR B): parts/Guia.tsx sobre o GET/POST /api/v2/guia.
//   · convite só com `oferecer`, perfil escolhido e no Resumo; `em_andamento` nunca convida;
//   · cada passo avança pela ação real (seta do mês, categoria em Gastos, pergunta ao Piggy),
//     só depois de o passo começar, e comemora só com o 200 do POST;
//   · 500 → "Tentar de novo"; 409 passo_indisponivel → orientação do motivo, sem "Tentar";
//   · Esc/Pular dispensam; Ajuda (menu, barra de baixo) e Cmd-K reabrem;
//   · âncora no bloco certo (data-dado = dado do roteiro), Piggy encostado nela;
//   · barra de baixo com 6 itens e folga ≥ 12 px do maior rótulo de 320 a 375.
// O roteiro vem de api_v2_respostas.json, que tests/test_api_v2_contrato.py iguala ao PASSOS
// do servidor: passo novo sem âncora na tela ou sem ação aqui fica vermelho.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { PAINEL, PROTOTIPO, RAIZ, RESPOSTAS, exigeArtefatoEmDia, servir } from "./_painel.mjs";

let browser;
before(async () => {
  exigeArtefatoEmDia();
  browser = await chromium.launch();
});
after(() => browser?.close());

const PASSOS = RESPOSTAS.guia.oferecer.passos;
const ROTA = { resumo: "/", gastos: "/gastos", piggy: "/piggy" };

// O servidor de mentira: aplica visto/feito/dispensar/reabrir como o db/guia.py; `post`
// pode responder outra coisa (status e corpo) para o POST de número `n`.
function aplicar(g, c) {
  const n = structuredClone(g);
  if (c.acao === "feito") n.passos.find((p) => p.id === c.passo).feito = true;
  n.estado = c.acao === "dispensar" ? "dispensado" : n.passos.every((p) => p.feito) ? "concluido" : "em_andamento";
  return n;
}
async function abrir({ width = 1280, height = 800, guia = "oferecer", perfil = "padrao", motion = "reduce", post, rota = "/", perfilLento = 0 } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height }, reducedMotion: motion, timezoneId: "America/Sao_Paulo" });
  await servir(ctx, undefined, { perfil });
  // Perfil chegando depois do guia: a corrida em que o convite brigaria com o modal de perfil.
  if (perfilLento) await ctx.route("**/api/v2/perfil", async (r) => { await new Promise((ok) => setTimeout(ok, perfilLento)); return r.fallback(); });
  const s = { g: structuredClone(RESPOSTAS.guia[guia]), posts: [] };
  await ctx.route("**/api/v2/guia", async (r) => {
    if (r.request().method() === "GET") return r.fulfill({ json: s.g });
    const c = r.request().postDataJSON();
    s.posts.push(c);
    const outro = await post?.(c, s.posts.length, s);
    if (outro) return r.fulfill({ status: outro.status, json: outro.json });
    s.g = aplicar(s.g, c);
    return r.fulfill({ json: s.g });
  });
  await ctx.addCookies([{ name: "csrf_token", value: "tok-123", url: "http://127.0.0.1:1" }]);
  const page = await ctx.newPage();
  await page.clock.setFixedTime(new Date("2026-10-02T15:00:00Z"));
  const erros = [];
  page.on("pageerror", (e) => erros.push(e.message));
  await page.goto(`${PAINEL}#${rota}`);
  await page.locator("#page-title").waitFor();
  return { ctx, page, s, erros };
}
const acoes = (s) => s.posts.map((c) => c.passo ? `${c.acao}:${c.passo}` : c.acao);
const titulo = (page) => page.locator("#guia-titulo").innerText();
const esperaTitulo = (page, t) => page.locator("#guia-titulo", { hasText: t }).waitFor({ timeout: 5000 });
const bora = async (page) => { await page.getByRole("button", { name: "Bora", exact: true }).click(); await esperaTitulo(page, PASSOS[0].fala.titulo); };

// A ação real de cada passo, por `acao` do roteiro.
const FAZER = {
  "mes.trocado": (page) => page.getByRole("button", { name: "Mês anterior", exact: true }).click(),
  "categoria.aberta": async (page) => { await page.locator('.rail [data-guia="nav.gastos"]').click(); await page.locator('[data-guia="categorias.lista"] .cat').nth(1).click(); },
  "piggy.perguntou": async (page) => { await page.locator("#askbar-input").fill("Quanto gastei este mês?"); await page.locator("#askbar-input").press("Enter"); },
};

test("fonte única: cada passo do roteiro tem âncora na sua tela, aba nav.<tela> e ação tratada aqui", async () => {
  assert.deepEqual(PASSOS.map((p) => p.acao).filter((a) => !FAZER[a]), []);
  const { ctx, page } = await abrir({ guia: "concluido" });
  const faltam = [];
  for (const p of PASSOS) {
    await page.goto(`${PAINEL}#${ROTA[p.tela]}`);
    await page.locator("#page-title").waitFor();
    await page.locator(`[data-guia="${p.ancora}"]`).first().waitFor({ timeout: 5000 }).catch(() => faltam.push(p.ancora));
    const dado = await page.locator(`[data-guia="${p.ancora}"]`).first().evaluate((e) => e.closest("[data-dado]")?.dataset.dado).catch(() => null);
    if (dado !== p.dado) faltam.push(`${p.ancora}: data-dado ${dado} ≠ ${p.dado}`);
  }
  await page.setViewportSize({ width: 375, height: 812 });
  for (const p of PASSOS) if (!(await page.locator(`.tabbar [data-guia="nav.${p.tela}"]`).count())) faltam.push(`nav.${p.tela}`);
  await ctx.close();
  assert.deepEqual(faltam, []);
});

test("convite: com `oferecer` e perfil no Resumo aparece e grava `visto`; sem perfil, em_andamento ou dispensado, não", async () => {
  const sim = await abrir();
  await sim.page.getByRole("button", { name: "Bora", exact: true }).waitFor({ timeout: 5000 });
  const foco = await sim.page.evaluate(() => document.activeElement?.id ?? "");
  await sim.ctx.close();
  assert.deepEqual(acoes(sim.s), ["visto"]);
  assert.notEqual(foco, "guia-titulo"); // o convite não rouba o foco
  for (const caso of [{ perfil: null, perfilLento: 800 }, { guia: "em_andamento" }, { guia: "dispensado" }, { rota: "/gastos" }]) {
    const { ctx, page, s } = await abrir(caso);
    await page.waitForTimeout(1200);
    const r = [await page.locator(".guia-balao").count(), acoes(s)];
    await ctx.close();
    assert.deepEqual(r, [0, []], JSON.stringify(caso));
  }
});

test("os 3 passos pela ação real: nada sai sem ela; categoria já escolhida não conta; comemora no fim", async () => {
  const { ctx, page, s, erros } = await abrir();
  await bora(page);
  const focoInicio = await page.evaluate(() => document.activeElement?.id);
  await page.waitForTimeout(1500); // sem ação, nenhum "feito"
  const semAcao = acoes(s);
  // Categoria escolhida em Gastos ANTES do passo 2: troca o mês lá, o que conclui o passo 1.
  await page.locator('.rail [data-guia="nav.gastos"]').click();
  await page.locator('[data-guia="categorias.lista"] .cat').first().click();
  await page.getByRole("button", { name: "Mês anterior", exact: true }).click();
  const focoAcao = await page.evaluate(() => document.activeElement?.getAttribute("aria-label"));
  await esperaTitulo(page, "Isso aí!");
  await esperaTitulo(page, PASSOS[1].fala.titulo);
  await page.waitForTimeout(800);
  const antes = acoes(s);
  await page.locator('[data-guia="categorias.lista"] .cat').nth(1).click();
  await esperaTitulo(page, PASSOS[2].fala.titulo);
  await FAZER["piggy.perguntou"](page);
  await esperaTitulo(page, "Fechou!");
  await ctx.close();
  assert.equal(focoInicio, "guia-titulo");
  assert.deepEqual(semAcao, ["visto"]);
  assert.equal(focoAcao, "Mês anterior"); // o foco fica com quem agiu
  assert.deepEqual(antes, ["visto", "feito:resumo.saiu"]); // a categoria de antes não contou
  assert.deepEqual(acoes(s), ["visto", "feito:resumo.saiu", "feito:gastos.categoria", "feito:piggy.pergunta"]);
  assert.deepEqual(erros, []);
});

test("celular: o chip da conversa conta como pergunta; o passo do Piggy aponta para a aba dele", async () => {
  const { ctx, page, s } = await abrir({ width: 375, height: 812, guia: "em_andamento" });
  s.g.passos[0].feito = true; s.g.passos[2].feito = false; s.g.passos[1].feito = true;
  await page.getByRole("button", { name: "Ajuda" }).click();
  await esperaTitulo(page, PASSOS[2].fala.titulo);
  const alvo = await page.evaluate(() => { const p = document.querySelector(".guia-piggy").getBoundingClientRect(); const a = document.querySelector('.tabbar [data-guia="nav.piggy"]').getBoundingClientRect(); return p.left < a.right && p.right > a.left && Math.round(p.bottom - a.top); });
  await page.locator('.tabbar [data-guia="nav.piggy"]').click();
  await page.locator(".chat-empty .chip").first().click();
  await esperaTitulo(page, "Fechou!");
  await ctx.close();
  assert.equal(alvo, 8);
  assert.deepEqual(acoes(s), ["reabrir", "feito:piggy.pergunta"]);
});

test("POST 500: sem comemoração, \"Tentar de novo\"; o 200 depois comemora", async () => {
  const { ctx, page, s } = await abrir({ post: (c, n, st) => (c.acao === "feito" && st.posts.filter((x) => x.acao === "feito").length === 1 ? { status: 500, json: RESPOSTAS.erros["500"].body } : null) });
  await bora(page);
  // Registra se a comemoração aparece em algum momento antes do 200.
  await page.evaluate(() => new MutationObserver(() => { if (document.getElementById("guia-titulo")?.textContent.includes("Isso aí")) window.__festa = 1; })
    .observe(document.body, { subtree: true, childList: true, characterData: true }));
  await FAZER["mes.trocado"](page);
  await page.getByText("Não consegui salvar seu progresso").waitFor({ timeout: 5000 });
  const festa = await page.evaluate(() => window.__festa ?? 0);
  await page.getByRole("button", { name: "Tentar de novo" }).click();
  await esperaTitulo(page, "Isso aí!");
  await ctx.close();
  assert.equal(festa, 0);
  assert.deepEqual(acoes(s), ["visto", "feito:resumo.saiu", "feito:resumo.saiu"]);
});

test("POST 409 passo_indisponivel: relê o guia e mostra a orientação, sem \"Tentar de novo\"; Seguir vai ao passo 2", async () => {
  const sem = RESPOSTAS.guia.indisponivel;
  const { ctx, page, s } = await abrir({
    post: (c, n, st) => { if (c.acao !== "feito") return null; st.g = { ...sem, estado: "em_andamento" }; const e = RESPOSTAS.erros["409_passo_indisponivel"]; return { status: e.status, json: e.body }; },
  });
  await bora(page);
  await FAZER["mes.trocado"](page);
  const link = page.locator('.guia-balao a[href="/settings?view=open-finance"]');
  await link.waitFor({ timeout: 5000 });
  const r = [await page.getByRole("button", { name: "Tentar de novo" }).count(), await page.getByText("Isso aí!").count()];
  await page.getByRole("button", { name: "Seguir" }).click();
  await esperaTitulo(page, PASSOS[1].fala.titulo);
  await ctx.close();
  assert.deepEqual(r, [0, 0]);
  assert.deepEqual(acoes(s), ["visto", "feito:resumo.saiu"]);
});

test("sem dados, pela Ajuda: orientação com o link do Open Finance e segue para o passo 2", async () => {
  const { ctx, page, s } = await abrir({ guia: "indisponivel" });
  await page.waitForTimeout(800);
  const convite = await page.locator(".guia-balao").count();
  await page.locator(".rail").getByRole("button", { name: "Ajuda" }).click();
  await page.locator('.guia-balao a[href="/settings?view=open-finance"]').waitFor({ timeout: 5000 });
  await page.getByRole("button", { name: "Seguir" }).click();
  await esperaTitulo(page, PASSOS[1].fala.titulo);
  await ctx.close();
  assert.equal(convite, 0);
  assert.deepEqual(acoes(s), ["reabrir"]);
});

test("Esc e Pular dispensam; Ajuda (menu, barra de baixo) e Cmd-K reabrem", async () => {
  const esc = await abrir();
  await esc.page.getByRole("button", { name: "Bora", exact: true }).waitFor();
  await esc.page.keyboard.press("Escape");
  await esc.page.waitForTimeout(300);
  const r1 = [await esc.page.locator(".guia-balao").count(), acoes(esc.s)];
  await esc.page.reload(); await esc.page.locator("#page-title").waitFor(); await esc.page.waitForTimeout(800);
  const r2 = [await esc.page.locator(".guia-balao").count(), acoes(esc.s)];
  await esc.page.locator(".rail").getByRole("button", { name: "Ajuda" }).click();
  await esperaTitulo(esc.page, PASSOS[0].fala.titulo);
  await esc.page.getByRole("button", { name: "Pular guia" }).click();
  await esc.page.keyboard.press("Meta+k");
  await esc.page.locator(".cmdk input").fill("guia");
  await esc.page.keyboard.press("Enter");
  await esperaTitulo(esc.page, PASSOS[0].fala.titulo);
  await esc.ctx.close();
  assert.deepEqual(r1, [0, ["visto", "dispensar"]]);
  assert.deepEqual(r2, [0, ["visto", "dispensar"]]);
  assert.deepEqual(acoes(esc.s), ["visto", "dispensar", "reabrir", "dispensar", "reabrir"]);
  const cel = await abrir({ width: 375, height: 812, guia: "dispensado" });
  await cel.page.locator(".tabbar").getByRole("button", { name: "Ajuda" }).click();
  await esperaTitulo(cel.page, PASSOS[1].fala.titulo); // o 1 já estava feito na fixture `dispensado`
  await cel.ctx.close();
  assert.deepEqual(acoes(cel.s), ["reabrir"]);
});

test("movimento: com `reduce` o Piggy fica parado; sem a preferência, entra pulando", async () => {
  const nomes = [];
  for (const motion of ["reduce", "no-preference"]) {
    const { ctx, page } = await abrir({ motion });
    await page.locator(".guia-piggy").waitFor();
    nomes.push(await page.locator(".guia-piggy").evaluate((e) => getComputedStyle(e).animationName));
    await ctx.close();
  }
  assert.deepEqual(nomes, ["none", "guia-entra"]);
});

test("barra de baixo: 6 itens; folga ≥ 12 px do maior rótulo em 320, 340, 360 e 375", async () => {
  const r = {};
  for (const width of [320, 340, 359, 360, 375]) {
    const { ctx, page } = await abrir({ width, height: 812, guia: "concluido" });
    r[width] = await page.evaluate(() => {
      const itens = [...document.querySelectorAll(".tabbar a, .tabbar button")];
      const folgas = itens.map((e) => { const s = e.querySelector("span"); if (!s?.getClientRects().length) return null; const g = document.createRange(); g.selectNodeContents(s); return e.getBoundingClientRect().width - g.getBoundingClientRect().width; }).filter((f) => f != null);
      const ajuda = document.querySelector(".tabbar button");
      return { n: itens.length, folga: Math.round(Math.min(...folgas) * 10) / 10, rotuloAjuda: !!ajuda.querySelector("span").getClientRects().length, nome: ajuda.getAttribute("aria-label"), cabe: document.querySelector(".tabbar").scrollWidth <= innerWidth };
    });
    await ctx.close();
  }
  console.log("# barra de baixo:", JSON.stringify(r));
  for (const [w, x] of Object.entries(r)) {
    assert.equal(x.n, 6, w); assert.ok(x.folga >= 12, `${w}: folga ${x.folga}`); assert.equal(x.nome, "Ajuda"); assert.ok(x.cabe, w);
    assert.equal(x.rotuloAjuda, Number(w) >= 360, w);
  }
});

test("convite com o Cmd-K aberto: espera, aparece quando ele fecha e grava `visto` uma vez; Mostrar o guia no Cmd-K não convida", async () => {
  const r = [];
  for (const fechar of ["Escape", "guia"]) {
    const { ctx, page, s } = await abrir({ perfilLento: 800 });
    await page.keyboard.press("Meta+k");
    await page.locator(".cmdk[open]").waitFor();
    await page.waitForTimeout(1500); // o perfil já chegou: só o Cmd-K segura o convite
    const aberto = [await page.locator(".guia-balao").count(), acoes(s)];
    if (fechar === "Escape") {
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "Bora", exact: true }).waitFor({ timeout: 5000 });
    } else {
      await page.locator(".cmdk input").fill("guia");
      await page.keyboard.press("Enter");
      await esperaTitulo(page, PASSOS[0].fala.titulo);
    }
    await page.waitForTimeout(500);
    r.push([fechar, aberto, acoes(s)]);
    await ctx.close();
  }
  assert.deepEqual(r, [["Escape", [0, []], ["visto"]], ["guia", [0, []], ["reabrir"]]]);
});

// Posição, em cada passo (e na fase em que ele aponta para a aba), da tela larga à de 320:
//   (a) o balão inteiro na tela;
//   (b) o balão não intercepta o toque em nenhum alvo da ação visível (o seletor de mês e o
//       Saiu; cada linha de categoria; a barra de conversa) nem na navegação (menu, abas):
//       `elementFromPoint` no centro e área de interseção, que pega a linha coberta só na
//       ponta (o centro da linha larga fica livre e o nome dela, não);
//   (c) o Piggy encosta 8 px na quina de cima ou de baixo da âncora (ou da aba).
const ALVOS = {
  "mes.trocado": ['[data-guia="mes.seletor"] button', '[data-guia="resumo.saiu"]'],
  "categoria.aberta": ['[data-guia="categorias.lista"] .cat'],
  "piggy.perguntou": ['[data-guia="piggy.pergunta"] input', '[data-guia="piggy.pergunta"] button'],
};
const NAV = [".rail a", ".rail button", ".tabbar a", ".tabbar button"];
for (const [width, height] of [[1280, 800], [1024, 768], [900, 600], [700, 600], [375, 812], [320, 640]]) {
  test(`posição ${width}×${height}: balão na tela, fora dos alvos do passo e da navegação; Piggy na âncora`, async () => {
    const { ctx, page, s } = await abrir({ width, height });
    await bora(page);
    const nav = width > 760 ? ".rail" : ".tabbar";
    const medidas = [];
    const medir = async (p, fase, ancora) => {
      await page.waitForTimeout(300);
      medidas.push([p.id, fase, await page.evaluate(([sels, ancora]) => {
        const b = document.querySelector(".guia-balao"), bb = b.getBoundingClientRect();
        const pg = document.querySelector(".guia-piggy").getBoundingClientRect();
        const a = [...document.querySelectorAll(ancora)].find((e) => e.getClientRects().length).getBoundingClientRect();
        const cobertos = [];
        let alvos = 0;
        for (const e of document.querySelectorAll(sels.join(","))) {
          const r = e.getBoundingClientRect();
          const x = (r.left + r.right) / 2, y = (r.top + r.bottom) / 2;
          if (!e.getClientRects().length || x < 0 || y < 0 || x > innerWidth || y > innerHeight) continue;
          alvos++;
          const area = Math.max(0, Math.min(r.right, bb.right) - Math.max(r.left, bb.left)) * Math.max(0, Math.min(r.bottom, bb.bottom) - Math.max(r.top, bb.top));
          if (b.contains(document.elementFromPoint(x, y)) || area > 0) cobertos.push(`${e.className || e.tagName} ${Math.round(area)}px²`);
        }
        const fora = bb.left < 0 || bb.top < 0 || bb.right > innerWidth || bb.bottom > innerHeight;
        const cola = [Math.round(pg.bottom - a.top), Math.round(a.bottom - pg.top)];
        const encosta = pg.left < a.right && pg.right > a.left && cola.includes(8);
        return { balao: [bb.left, bb.top, bb.right, bb.bottom].map(Math.round), alvos, cobertos, fora, cola, encosta };
      }, [[...ALVOS[p.acao], `[data-guia="nav.${p.tela}"]`, ...NAV], ancora])]);
    };
    for (const [i, p] of PASSOS.entries()) {
      if (i) await esperaTitulo(page, p.fala.titulo);
      const naTela = p.tela === "resumo" || (p.tela === "piggy" && width > 760);
      if (!naTela) {
        await medir(p, "aba", `${nav} [data-guia="nav.${p.tela}"]`);
        await page.locator(`${nav} [data-guia="nav.${p.tela}"]`).click();
        await page.locator(`[data-guia="${p.ancora}"]`).first().waitFor();
      }
      await medir(p, "âncora", `[data-guia="${p.ancora}"]`);
      await (p.acao === "categoria.aberta" ? page.locator('[data-guia="categorias.lista"] .cat').nth(1).click() : FAZER[p.acao](page));
    }
    await esperaTitulo(page, "Fechou!");
    await ctx.close();
    console.log(`# posição ${width}×${height}:`, JSON.stringify(medidas));
    assert.deepEqual(medidas.filter(([, , m]) => m.fora || m.cobertos.length || !m.encosta), []);
    assert.equal(acoes(s).length, 4);
  });
}

// A altura da página muda com o passo aberto (o refetch do SSE, lib/eventos.ts): sem a pessoa
// ter rolado, o guia volta para a âncora; depois de ela rolar com a roda, não a puxa de volta.
// `overflow-anchor: none` desliga a âncora de rolagem do Chromium, que senão compensaria o
// bloco injetado sozinha e o caso sem rolagem passaria sem o guia fazer nada.
test("altura muda com o passo aberto: re-rola até a âncora só se a pessoa não rolou", async () => {
  const r = {};
  for (const roda of [false, true]) {
    const { ctx, page } = await abrir({ width: 1024, height: 600 });
    await bora(page);
    await page.waitForTimeout(300);
    await page.evaluate(() => { document.documentElement.style.overflowAnchor = "none"; });
    if (roda) { await page.mouse.move(600, 400); await page.mouse.wheel(0, 3000); await page.waitForTimeout(300); }
    const antes = await page.evaluate(() => scrollY);
    await page.evaluate(() => {
      const d = document.createElement("div"); d.style.height = "2000px";
      document.querySelector("#main").prepend(d); // no topo da página, acima de todo bloco
    });
    await page.waitForTimeout(400);
    r[roda ? "rolou" : "parada"] = await page.evaluate((antes) => {
      const a = document.querySelector('[data-guia="resumo.saiu"]').getBoundingClientRect();
      return { antes, depois: scrollY, naTela: a.top >= 0 && a.bottom <= innerHeight };
    }, antes);
    await ctx.close();
  }
  console.log("# altura muda:", JSON.stringify(r));
  assert.equal(r.parada.naTela, true); // o layout que se mexe continua defendido
  assert.ok(r.rolou.antes > 0, "a roda não rolou a página");
  assert.ok(Math.abs(r.rolou.depois - r.rolou.antes) < 100, "voltou para a âncora"); // a rolagem da pessoa ficou
  assert.equal(r.rolou.naTela, false);
});

test("\"Tentar de novo\" com duplo clique manda um POST só", async () => {
  const { ctx, page, s } = await abrir({
    post: async (c, n, st) => {
      if (c.acao !== "feito") return null;
      await new Promise((ok) => setTimeout(ok, 300));
      return st.posts.filter((x) => x.acao === "feito").length === 1 ? { status: 500, json: RESPOSTAS.erros["500"].body } : null;
    },
  });
  await bora(page);
  await FAZER["mes.trocado"](page);
  await page.getByRole("button", { name: "Tentar de novo" }).dblclick();
  await esperaTitulo(page, "Isso aí!");
  await page.waitForTimeout(800);
  await ctx.close();
  assert.deepEqual(acoes(s), ["visto", "feito:resumo.saiu", "feito:resumo.saiu"]);
});

test("teclado: do topo da página, o [Bora] do convite chega em poucos Tabs", async () => {
  const { ctx, page } = await abrir();
  await page.getByRole("button", { name: "Bora", exact: true }).waitFor();
  let tabs = 0;
  while (tabs < 150 && (await page.evaluate(() => document.activeElement?.textContent)) !== "Bora") { await page.keyboard.press("Tab"); tabs++; }
  await ctx.close();
  console.log(`# Tabs até o [Bora]: ${tabs}`);
  assert.ok(tabs <= 3, `${tabs} Tabs`);
});

test("barra de baixo do protótipo (sem Ajuda): 5 colunas e o Piggy no centro; no /painel, 6", async () => {
  const r = [];
  for (const [nome, url, raiz] of [["protótipo", PROTOTIPO, RAIZ], ["painel", PAINEL, undefined]]) {
    for (const width of [320, 375]) {
      const ctx = await browser.newContext({ viewport: { width, height: 812 }, reducedMotion: "reduce" });
      await servir(ctx, raiz);
      await ctx.addCookies([{ name: "csrf_token", value: "tok-123", url: "http://127.0.0.1:1" }]);
      const page = await ctx.newPage();
      await page.goto(`${url}#/`);
      await page.locator("#page-title").waitFor();
      r.push([nome, width, await page.evaluate(() => {
        const bar = document.querySelector(".tabbar");
        const pig = bar.querySelector('[data-tab="piggy"]').getBoundingClientRect();
        return [getComputedStyle(bar).gridTemplateColumns.split(" ").length, Math.round(pig.left + pig.width / 2 - innerWidth / 2) || 0]; // || 0: -0.4 arredonda para -0
      })]);
      await ctx.close();
    }
  }
  console.log("# barra de baixo [colunas, Piggy − centro px]:", JSON.stringify(r));
  assert.deepEqual(r.filter(([n]) => n === "protótipo").map(([, , [c, d]]) => [c, d]), [[5, 0], [5, 0]]);
  assert.deepEqual(r.filter(([n]) => n === "painel").map(([, , [c]]) => c), [6, 6]);
});
