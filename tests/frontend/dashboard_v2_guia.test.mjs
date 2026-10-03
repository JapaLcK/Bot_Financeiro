// Guia do /painel (#728, PR B): parts/Guia.tsx sobre o GET/POST /api/v2/guia — o fluxo e o
// servidor. A tela (véu, anel, foco, posição, movimento) está em dashboard_v2_guia_tela.test.mjs.
//   · convite só com `oferecer`, perfil escolhido e no Resumo; `em_andamento` nunca convida;
//   · cada passo avança pela ação real (seta do mês, categoria em Gastos, pergunta ao Piggy),
//     só depois de o passo começar, e comemora só com o 200 do POST; o guia leva até a tela;
//   · 500 → "Tentar de novo"; 409 passo_indisponivel → orientação do motivo, sem "Tentar";
//   · Esc/Pular dispensam; Ajuda (menu, barra de baixo) e Cmd-K reabrem;
//   · âncora no bloco certo (data-dado = dado do roteiro).
// O roteiro vem de api_v2_respostas.json, que tests/test_api_v2_contrato.py iguala ao PASSOS
// do servidor: passo novo sem âncora na tela ou sem ação aqui fica vermelho.
import { test } from "node:test";
import assert from "node:assert/strict";
import { PAINEL, RESPOSTAS } from "./_painel.mjs";
import { ALVO, FAZER, PASSOS, ROTA, abrir, acoes, bora, esperaTitulo, irAoAlvo, naRota, navegador, piggyEm } from "./_guia.mjs";

navegador();

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
  for (const p of PASSOS) {
    await page.goto(`${PAINEL}#${ROTA[p.tela]}`);
    await page.locator(`[data-guia="${ALVO[p.acao]}"]`).first().waitFor({ timeout: 5000 }).catch(() => faltam.push(`alvo ${ALVO[p.acao]}`));
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

// O guia leva sozinho até a tela de cada passo, depois da comemoração ("Vem comigo pra X").
test("os 3 passos pela ação real, o guia levando de tela em tela: nada sai sem ela; categoria já escolhida não conta", async () => {
  // Categoria escolhida em Gastos ANTES do guia; o convite aparece ao voltar ao Resumo.
  const { ctx, page, s, erros } = await abrir({ rota: "/gastos" });
  await page.locator('[data-guia="categorias.lista"] .cat').first().click();
  await page.locator('.rail [data-guia="nav.resumo"]').click();
  await bora(page);
  const focoInicio = await page.evaluate(() => document.activeElement?.id);
  await page.waitForTimeout(1500); // sem ação, nenhum "feito"
  const semAcao = acoes(s);
  await FAZER["mes.trocado"](page);
  const focoAcao = await page.evaluate(() => document.activeElement?.getAttribute("aria-label"));
  await page.locator(".guia-balao").getByText("Passo feito. Vem comigo pra Gastos.").waitFor({ timeout: 5000 });
  const naFesta = await page.evaluate(() => location.hash); // a comemoração é a pausa antes de ir
  await esperaTitulo(page, PASSOS[1].fala.titulo);
  await naRota(page, "#/gastos");
  await page.waitForTimeout(800);
  const antes = acoes(s);
  await FAZER["categoria.aberta"](page);
  await page.locator(".guia-balao").getByText("Passo feito. Vem comigo pra Piggy.").waitFor({ timeout: 5000 });
  await esperaTitulo(page, PASSOS[2].fala.titulo);
  await naRota(page, "#/piggy"); // no desktop também: o chip mora na conversa
  await FAZER["piggy.perguntou"](page);
  await esperaTitulo(page, "Fechou!");
  await ctx.close();
  assert.equal(focoInicio, "guia-titulo");
  assert.deepEqual(semAcao, ["visto"]);
  assert.equal(focoAcao, "Mês anterior"); // o foco fica com quem agiu
  assert.equal(naFesta, "#/");
  assert.deepEqual(antes, ["visto", "feito:resumo.saiu"]); // a categoria de antes não contou
  assert.deepEqual(acoes(s), ["visto", "feito:resumo.saiu", "feito:gastos.categoria", "feito:piggy.pergunta"]);
  assert.deepEqual(erros, []);
});

test("celular: o guia leva à conversa e destaca o chip do gasto do mês, que conta como pergunta", async () => {
  const { ctx, page, s } = await abrir({ width: 375, height: 812, guia: "em_andamento" });
  s.g.passos[0].feito = true; s.g.passos[2].feito = false; s.g.passos[1].feito = true;
  await page.getByRole("button", { name: "Ajuda" }).click();
  await esperaTitulo(page, PASSOS[2].fala.titulo);
  await naRota(page, "#/piggy");
  await irAoAlvo(page, '[data-guia="piggy.chip"]');
  const chip = await page.locator('[data-guia="piggy.chip"]').textContent();
  const encosta = await piggyEm(page, '[data-guia="piggy.chip"]');
  await FAZER["piggy.perguntou"](page);
  await esperaTitulo(page, "Fechou!");
  await ctx.close();
  assert.equal(chip, "Meu gasto deste mês tá acima ou abaixo do normal?");
  assert.ok(encosta[0] && encosta.includes(8), JSON.stringify(encosta));
  assert.deepEqual(acoes(s), ["reabrir", "feito:piggy.pergunta"]);
});

test("conversa já começada (sem chips): o alvo do passo do Piggy é o campo da conversa", async () => {
  const { ctx, page, s } = await abrir({ guia: "em_andamento" });
  s.g.passos[0].feito = true; s.g.passos[2].feito = false;
  await page.locator("#askbar-input").fill("oi");
  await page.locator("#askbar-input").press("Enter");
  await naRota(page, "#/piggy");
  await page.locator(".rail").getByRole("button", { name: "Ajuda" }).click();
  await esperaTitulo(page, PASSOS[2].fala.titulo);
  await irAoAlvo(page, '[data-guia="piggy.pergunta"]');
  const r = [await page.locator('[data-guia="piggy.chip"]').count(), await piggyEm(page, '[data-guia="piggy.pergunta"]')];
  await page.locator("#askbar-input").fill("Quanto gastei este mês?");
  await page.locator("#askbar-input").press("Enter");
  await esperaTitulo(page, "Fechou!");
  await ctx.close();
  assert.equal(r[0], 0);
  assert.ok(r[1][0] && r[1].includes(8), JSON.stringify(r[1]));
  assert.deepEqual(acoes(s), ["reabrir", "feito:piggy.pergunta"]);
});

test("voltou pelo navegador: \"Volta pra Gastos\" com o Piggy na aba, sem ser puxado de novo; a aba leva de volta", async () => {
  const { ctx, page, s } = await abrir({ guia: "dispensado" }); // o 1 feito: abre no passo 2
  await page.locator(".rail").getByRole("button", { name: "Ajuda" }).click();
  await esperaTitulo(page, PASSOS[1].fala.titulo);
  await naRota(page, "#/gastos");
  await page.locator('[data-guia="categorias.item"]').waitFor();
  await page.goBack();
  await page.getByText("Volta pra Gastos.").waitFor({ timeout: 5000 });
  await page.waitForTimeout(1200);
  const r = [await page.evaluate(() => location.hash), await piggyEm(page, '.rail [data-guia="nav.gastos"]')];
  await page.locator('.rail [data-guia="nav.gastos"]').click();
  await esperaTitulo(page, PASSOS[1].fala.titulo); // a aba devolve: o balão volta ao bloco
  await FAZER["categoria.aberta"](page);
  await esperaTitulo(page, PASSOS[2].fala.titulo);
  await ctx.close();
  assert.equal(r[0], "#/");
  assert.ok(r[1][0] && r[1].includes(8), JSON.stringify(r[1]));
  assert.deepEqual(acoes(s), ["reabrir", "feito:gastos.categoria"]);
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
  const { ctx, page, s } = await abrir({
    post: (c, n, st) => { if (c.acao !== "feito") return null; st.g = { ...RESPOSTAS.guia.indisponivel, estado: "em_andamento" }; const e = RESPOSTAS.erros["409_passo_indisponivel"]; return { status: e.status, json: e.body }; },
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

test("passo 1 pulado, 2 e 3 feitos: sem \"Fechou!\" nem \"Guia concluído.\" (o servidor segue em_andamento); Fechar não dispensa", async () => {
  const { ctx, page, s } = await abrir({ guia: "indisponivel" });
  await page.locator(".rail").getByRole("button", { name: "Ajuda" }).click();
  await page.getByRole("button", { name: "Seguir" }).click();
  await esperaTitulo(page, PASSOS[1].fala.titulo);
  await FAZER["categoria.aberta"](page);
  await esperaTitulo(page, PASSOS[2].fala.titulo);
  await FAZER["piggy.perguntou"](page);
  await esperaTitulo(page, "Por agora é isso");
  const r = [await page.getByText("Fechou!").count(), s.g.estado];
  const status = await page.getByRole("status").allInnerTexts();
  await page.getByRole("button", { name: "Fechar", exact: true }).click();
  const aberto = await page.locator(".guia-balao").count();
  await ctx.close();
  assert.deepEqual(r, [0, "em_andamento"]);
  assert.ok(status.includes("Por agora é isso. O passo que ficou pra depois volta na Ajuda."), JSON.stringify(status));
  assert.ok(!status.some((t) => t.includes("concluído")), JSON.stringify(status));
  assert.equal(aberto, 0);
  assert.deepEqual(acoes(s), ["reabrir", "feito:gastos.categoria", "feito:piggy.pergunta"]);
});

// "Seguir" no último passo também acaba nesta tela, nunca fecha calado. Hoje o passo 3 do
// servidor está sempre disponível e a barra de conversa sempre na #/piggy: o caso vem da
// fixture, protegendo um roteiro em que o último passo possa ficar indisponível.
const ultimoPulavel = (estado, feitos) => {
  const g = structuredClone(RESPOSTAS.guia.oferecer);
  g.estado = estado;
  g.passos.forEach((p, i) => { p.feito = feitos[i]; if (i === 2 || estado === "concluido") Object.assign(p, { disponivel: false, motivo: "sem_dados" }); });
  return g;
};
test("1 e 2 feitos, Seguir no 3: \"Por agora é isso\" (sem \"Fechou!\"); Fechar não dispensa", async () => {
  const { ctx, page, s } = await abrir({ guia: "em_andamento" });
  s.g = ultimoPulavel("em_andamento", [true, true, false]);
  await page.locator(".rail").getByRole("button", { name: "Ajuda" }).click();
  await esperaTitulo(page, PASSOS[2].fala.titulo);
  await page.getByRole("button", { name: "Seguir" }).click();
  await esperaTitulo(page, "Por agora é isso");
  const r = [await page.getByText("Fechou!").count(), await page.evaluate(() => document.activeElement?.id)];
  const status = await page.getByRole("status").allInnerTexts();
  await page.getByRole("button", { name: "Fechar", exact: true }).click();
  const aberto = await page.locator(".guia-balao").count();
  await ctx.close();
  assert.deepEqual(r, [0, "guia-titulo"]);
  assert.ok(status.includes("Por agora é isso. O passo que ficou pra depois volta na Ajuda."), JSON.stringify(status));
  assert.ok(!status.some((t) => t.includes("concluído")), JSON.stringify(status));
  assert.equal(aberto, 0);
  assert.deepEqual(acoes(s), ["reabrir"]);
});

test("revisão com o servidor concluído, Seguir até o fim: \"Fechou!\", não \"Por agora é isso\"", async () => {
  const { ctx, page, s } = await abrir({ guia: "concluido" });
  s.g = ultimoPulavel("concluido", [true, true, true]);
  await page.locator(".rail").getByRole("button", { name: "Ajuda" }).click();
  for (const p of PASSOS) {
    await esperaTitulo(page, p.fala.titulo);
    await page.getByRole("button", { name: "Seguir" }).click();
  }
  await esperaTitulo(page, "Fechou!");
  const r = [await page.getByText("Por agora é isso").count(), await page.getByRole("status").allInnerTexts()];
  await ctx.close();
  assert.deepEqual(r, [0, ["Guia concluído."]]);
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

// Com o véu (convite e passo), só o balão e o alvo respondem: nem ⌘K nem "/" abrem o Cmd-K,
// no <dialog> nativo e no fallback do Safari 14. Esc dispensa e tira o véu; aí o ⌘K volta.
test("Cmd-K não abre com o véu do convite nem do passo (nativo e Safari 14); Esc e Pular tiram o véu", async () => {
  const r = {};
  for (const semDialog of [false, true]) {
    const { ctx, page, s } = await abrir({ semDialog });
    const tenta = async () => {
      for (const k of ["Meta+k", "/"]) await page.keyboard.press(k);
      await page.waitForTimeout(300);
      return page.locator(".cmdk[open]").count();
    };
    await page.getByRole("button", { name: "Bora", exact: true }).waitFor();
    const convite = await tenta();
    await bora(page);
    const passo = await tenta();
    await page.keyboard.press("Escape");
    await page.waitForTimeout(300);
    const esc = [await page.locator(".guia-balao, .guia-veu, .guia-anel, .guia-sombra").count(), acoes(s)];
    await page.keyboard.press("Meta+k");
    await page.locator(".cmdk[open]").waitFor({ timeout: 5000 }); // sem o véu, o ⌘K volta
    await page.keyboard.press("Escape");
    await page.locator(".rail").getByRole("button", { name: "Ajuda" }).click();
    await esperaTitulo(page, PASSOS[0].fala.titulo);
    await page.getByRole("button", { name: "Pular guia" }).click();
    const pular = await page.locator(".guia-veu").count();
    r[semDialog ? "safari14" : "nativo"] = [convite, passo, esc, pular];
    await ctx.close();
  }
  const esperado = [0, 0, [0, ["visto", "dispensar"]], 0];
  assert.deepEqual(r, { nativo: esperado, safari14: esperado });
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
