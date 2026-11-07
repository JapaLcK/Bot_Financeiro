// A dica de primeiro uso de Assinaturas, Previsão, Metas, Patrimônio e Simulador (#728, parts/Dica.tsx) e a linha "Como funciona <tela>"
// da tela Ajuda (parts/AjudaTela.tsx), que reabre a dica na tela de origem. O servidor de mentira e as opções `dica`/`dicaLenta` estão em
// _guia.mjs; o carimbo e o portão de plano de verdade, em tests/test_api_v2_guia_dica.py.
import { test } from "node:test";
import assert from "node:assert/strict";
import { RESPOSTAS } from "./_painel.mjs";
import { FAZER, PASSOS, abrir, acoes, ajudaDoTrilho, esperaTitulo, naRota, navegador } from "./_guia.mjs";

navegador();

const CARD = ".dica-tela";
const TITULO = "Como eu acho suas assinaturas";
const ir = (page, rota) => page.evaluate((h) => { location.hash = h; }, `#${rota}`);
const focoNoTitulo = (page) => page.waitForFunction(() => document.activeElement?.id === "dica-titulo", null, { timeout: 5000 });
const irParaAjuda = (page) => page.locator(".rail").getByRole("link", { name: "Ajuda" }).click();
const irParaAjudaCel = async (page) => {
  await page.locator(".tabbar").getByRole("button", { name: "Menu" }).click();
  await page.locator(".menu-telas").getByRole("link", { name: "Ajuda" }).click();
};
const comoFunciona = (page, tela) => page.locator("main").getByRole("button", { name: `Como funciona ${tela}` });

test("1280, dica nova: o card entra entre o título da página e os blocos, sem mexer no foco; um POST só, mesmo com o POST lento, o foco na aba e sair e voltar", async () => {
  const { ctx, page, s, erros } = await abrir({ guia: "concluido", dica: "nova", dicaLenta: 300, rota: "/assinaturas" });
  await page.locator(CARD).waitFor();
  const r = await page.evaluate((sel) => {
    const c = document.querySelector(sel), head = document.querySelector(".page-head"), panel = document.querySelector(".panel");
    const [bc, bh, bp] = [c, head, panel].map((e) => e.getBoundingClientRect());
    return { antes: c.previousElementSibling === head, img: !!c.querySelector("img[alt='']"), entre: bc.top >= bh.bottom && bc.bottom <= bp.top,
      foco: document.activeElement === document.body, titulo: c.querySelector("h2").textContent };
  }, CARD);
  // Antes de o 1º POST responder: o foco volta à aba (refetch) e a pessoa sai e volta.
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await ir(page, "/");
  await naRota(page, "#/");
  await ir(page, "/assinaturas");
  await naRota(page, "#/assinaturas");
  await page.waitForTimeout(800);
  await ctx.close();
  assert.deepEqual(r, { antes: true, img: true, entre: true, foco: true, titulo: TITULO });
  assert.deepEqual(s.dicas, ["assinaturas.marcas"]);
  assert.deepEqual(erros, []);
});

// A resposta lenta do POST da dica é o guia de quando ele chegou ao servidor: ela não pode
// desfazer no cache o que o guia gravou depois (reabrir e o último passo feito).
test("POST da dica lento: a resposta não volta o guia ao estado de antes (o \"Fechou!\" fica)", async (t) => {
  let soltar;
  const portao = new Promise((ok) => { soltar = ok; });
  t.after(() => soltar());
  const { ctx, page, s, erros } = await abrir({ guia: "em_andamento", dica: "nova", dicaLenta: portao, rota: "/assinaturas",
    antes: (_, s) => { Object.assign(s.g.passos[0], { disponivel: true, motivo: null }); } }); // só o 1º passo falta
  await page.locator(CARD).waitFor();
  const volta = page.waitForResponse("**/api/v2/guia/dica", { timeout: 15000 });
  let voltou = false;
  volta.then(() => { voltou = true; });
  await ajudaDoTrilho(page);
  await esperaTitulo(page, PASSOS[0].fala.titulo);
  await FAZER["mes.trocado"](page);
  await page.locator("#guia-titulo", { hasText: "Fechou!" }).waitFor({ timeout: 5000 });
  const antesDaVolta = !voltou;
  soltar();
  await volta;
  await page.waitForTimeout(300);
  const titulo = await page.locator("#guia-titulo").textContent();
  await ctx.close();
  assert.ok(antesDaVolta, "o guia terminou depois da resposta da dica: o teste não mede a corrida");
  assert.equal(titulo, "Fechou! O painel é seu.");
  assert.deepEqual(acoes(s), ["reabrir", `feito:${PASSOS[0].id}`]);
  assert.deepEqual(s.dicas, ["assinaturas.marcas"]);
  assert.deepEqual(erros, []);
});

test("dica já vista: recarrega sem card e sem POST", async () => {
  const { ctx, page, s } = await abrir({ guia: "concluido", rota: "/assinaturas" });
  await page.locator(".panel").first().waitFor();
  await page.waitForTimeout(400);
  const n = await page.locator(CARD).count();
  await ctx.close();
  assert.equal(n, 0);
  assert.deepEqual(s.dicas, []);
});

test("1280, vindo de Assinaturas: a Ajuda tem a linha \"Como funciona Assinaturas\"; ao tocar volta à tela e reabre o card com o foco no título; o guia esconde o card", async () => {
  const { ctx, page, s } = await abrir({ guia: "concluido", rota: "/assinaturas" });
  await irParaAjuda(page);
  const linha = comoFunciona(page, "Assinaturas");
  await linha.waitFor();
  const naAjuda = [await page.evaluate(() => location.hash), await page.locator(CARD).count()];
  await linha.click();
  await naRota(page, "#/assinaturas");
  await focoNoTitulo(page);
  const reaberta = await page.locator(`${CARD} h2`).textContent();
  await page.locator(CARD).getByRole("button", { name: "Entendi" }).click();
  const depois = [await page.locator(CARD).count(), await page.evaluate(() => document.activeElement?.id)];
  // Teclado: Enter na linha faz o mesmo.
  await irParaAjuda(page);
  await comoFunciona(page, "Assinaturas").focus();
  await page.keyboard.press("Enter");
  await focoNoTitulo(page);
  // "Guia do painel": reabre o guia; com o balão aberto, o card some.
  await ajudaDoTrilho(page);
  await page.locator(".guia-balao").waitFor();
  const comGuia = await page.locator(CARD).count();
  await ctx.close();
  assert.deepEqual(naAjuda, ["#/ajuda", 0]);
  assert.equal(reaberta, TITULO);
  assert.deepEqual(depois, [0, "page-title"]);
  assert.equal(comGuia, 0);
  assert.deepEqual(acoes(s), ["reabrir"]);
  assert.deepEqual(s.dicas, []); // reabrir a dica não grava nada
});

test("telas sem dica (/ e /gastos): a Ajuda não tem a linha \"Como funciona\" e abre o guia; no celular a Ajuda também não tem", async () => {
  for (const rota of ["/", "/gastos"]) {
    const { ctx, page, s } = await abrir({ guia: "concluido", rota });
    await irParaAjuda(page);
    await page.locator("main").getByRole("button", { name: "Guia do painel" }).waitFor();
    const linhas = await page.locator("main").getByRole("button", { name: /^Como funciona/ }).count();
    await page.locator("main").getByRole("button", { name: "Guia do painel" }).click();
    await page.locator(".guia-balao").waitFor();
    await ctx.close();
    assert.equal(linhas, 0, rota);
    assert.deepEqual(acoes(s), ["reabrir"], rota);
    const cel = await abrir({ width: 375, height: 812, guia: "concluido", rota });
    await irParaAjudaCel(cel.page);
    await cel.page.locator("main").getByRole("button", { name: "Guia do painel" }).waitFor();
    const celLinhas = await cel.page.locator("main").getByRole("button", { name: /^Como funciona/ }).count();
    await cel.ctx.close();
    assert.equal(celLinhas, 0, rota);
  }
});

test("390: vindo de Assinaturas pelo Menu, a Ajuda tem a linha; tocada, o card reabre dentro da tela; nada estoura", async () => {
  const { ctx, page } = await abrir({ width: 390, height: 844, guia: "concluido", rota: "/assinaturas" });
  await irParaAjudaCel(page);
  const linha = comoFunciona(page, "Assinaturas");
  await linha.waitFor();
  const aberta = await page.locator(".menu-telas[open]").count();
  await linha.click();
  await focoNoTitulo(page);
  const card = await page.evaluate((sel) => {
    const b = document.querySelector(sel).getBoundingClientRect();
    return b.left >= 0 && b.right <= innerWidth && document.documentElement.scrollWidth <= innerWidth;
  }, CARD);
  await ctx.close();
  assert.equal(aberta, 0);
  assert.ok(card);
});

// Antes (decisão de 2026-10-04) abaixo de 360 a Ajuda saía da barra e a dica não reabria por ela;
// agora a Ajuda mora no Menu, que cabe em 320.
test("320: a dica nova aparece inteira na tela e a Ajuda, pelo Menu, a reabre", async () => {
  const { ctx, page } = await abrir({ width: 320, height: 640, guia: "concluido", dica: "nova", rota: "/assinaturas" });
  await page.locator(CARD).waitFor();
  const r = await page.evaluate((sel) => {
    const b = document.querySelector(sel).getBoundingClientRect();
    return { card: b.left >= 0 && b.right <= innerWidth, cabe: document.documentElement.scrollWidth <= innerWidth };
  }, CARD);
  await irParaAjudaCel(page);
  await comoFunciona(page, "Assinaturas").click();
  await focoNoTitulo(page);
  const volta = await page.evaluate(() => [location.hash, document.documentElement.scrollWidth <= innerWidth]);
  await ctx.close();
  assert.deepEqual(r, { card: true, cabe: true });
  assert.deepEqual(volta, ["#/assinaturas", true]);
});

test("guia aberto: o convite segue na tela de Assinaturas e a dica espera; \"Agora não\" e ela entra, com um POST", async () => {
  const { ctx, page, s } = await abrir({ guia: "oferecer", dica: "nova" });
  await page.locator(".guia-balao").waitFor();
  await ir(page, "/assinaturas");
  await naRota(page, "#/assinaturas");
  await page.waitForTimeout(500);
  const comGuia = [await page.locator(CARD).count(), s.dicas.length];
  await page.getByRole("button", { name: "Agora não" }).click();
  await page.locator(CARD).waitFor();
  await page.waitForTimeout(300);
  await ctx.close();
  assert.deepEqual(comGuia, [0, 0]);
  assert.deepEqual(s.dicas, ["assinaturas.marcas"]);
});

test("Essencial (sem a dica): nem card nem a linha \"Como funciona\" na Ajuda", async () => {
  const { ctx, page } = await abrir({ guia: "concluido", plano: "essencial", dica: "sem", rota: "/assinaturas" });
  await page.waitForTimeout(400);
  await irParaAjuda(page);
  await page.locator("main").getByRole("button", { name: "Guia do painel" }).waitFor();
  const r = [await page.locator(CARD).count(), await page.locator("main").getByRole("button", { name: /^Como funciona/ }).count()];
  await ctx.close();
  assert.deepEqual(r, [0, 0]);
});

// Rollback: o navegador guarda o dashboard-app.js novo (nome fixo) e o servidor volta a responder
// o guia sem `dicas`. A tela abre como antes do #728, sem quebrar o render.
test("guia sem a chave dicas (servidor antigo): Assinaturas abre, a Ajuda não tem a linha \"Como funciona\", sem card nem POST", async () => {
  const { ctx, page, s, erros } = await abrir({ guia: "concluido", dica: "ausente", rota: "/assinaturas" });
  await page.locator(".panel").first().waitFor();
  await page.waitForTimeout(400);
  const titulo = await page.locator("#page-title").isVisible(), card = await page.locator(CARD).count();
  await irParaAjuda(page);
  await page.locator("main").getByRole("button", { name: "Guia do painel" }).waitFor();
  const r = [titulo, await page.locator("main").getByRole("button", { name: /^Como funciona/ }).count(), card];
  await ctx.close();
  assert.deepEqual(r, [true, 0, 0]);
  assert.deepEqual(s.dicas, []);
  assert.deepEqual(erros, []);
});

test("Cmd-K: \"Como funciona esta tela\" só na tela com dica; escolhido, mostra o card com o foco no título", async () => {
  const { ctx, page } = await abrir({ guia: "concluido" });
  const lista = async () => {
    await page.keyboard.press("Control+k");
    await page.locator(".cmdk-list").waitFor();
    return page.locator(".cmdk-item").allTextContents();
  };
  const naHome = (await lista()).includes("Como funciona esta tela");
  await page.keyboard.press("Escape");
  await ir(page, "/assinaturas");
  await naRota(page, "#/assinaturas");
  const naTela = (await lista()).includes("Como funciona esta tela");
  await page.locator(".cmdk-item", { hasText: "Como funciona esta tela" }).click();
  await focoNoTitulo(page);
  await ctx.close();
  assert.deepEqual([naHome, naTela], [false, true]);
});

// O duplo toque no "Menu": a folha abre embaixo do dedo e o 2º toque cairia num item da folha que ficou ali. O "Fechar" da folha ocupa essa faixa (parts/MenuTelas.tsx): o duplo toque vira abrir+fechar, sem efeito colateral.
for (const [semDialog, rota, w, h] of [[false, "/", 375, 812], [true, "/", 375, 812], [false, "/assinaturas", 375, 812], [true, "/assinaturas", 320, 568]]) {
  test(`${w}×${h} ${rota} (${semDialog ? "fallback sem <dialog>" : "<dialog> nativo"}): duplo toque no Menu abre e fecha a folha, sem abrir guia nem dica nem POST`, async () => {
    const { ctx, page, s } = await abrir({ width: w, height: h, guia: "concluido", rota, semDialog });
    await page.locator(".tabbar").getByRole("button", { name: "Menu" }).dblclick();
    await page.waitForTimeout(400);
    const r = await page.evaluate((sel) => ({
      folha: document.querySelectorAll(".menu-telas[open]").length, guia: document.querySelectorAll(".guia-balao, .guia-piggy").length, dica: document.querySelectorAll(sel).length, hash: location.hash,
    }), CARD);
    await ctx.close();
    assert.deepEqual(r, { folha: 0, guia: 0, dica: 0, hash: `#${rota}` }, `${rota}`); // hash: o 2º toque não navegou para a tela de baixo
    assert.deepEqual(acoes(s), [], `${rota}`);
  });
}

// As outras quatro telas com dica. Os textos vêm da fixture, que tests/test_api_v2_contrato.py prende
// ao catálogo `DICAS` do servidor: tirar a dica de lá (ou da TELA em parts/Dica.tsx) deixa isto vermelho.
const ROTULO = { "/previsao": "Previsão", "/metas": "Metas", "/patrimonio": "Patrimônio", "/simulador": "Simulador" };
const OUTRAS = [["/previsao", "previsao.saldo"], ["/metas", "metas.ritmo"], ["/patrimonio", "patrimonio.soma"], ["/simulador", "simulador.corte"]];
for (const [rota, id] of OUTRAS) {
  test(`${rota}: a dica aparece na 1ª visita com um POST, não volta ao sair e voltar, reabre pela tela Ajuda (1280 e 390) e some com o guia aberto`, async () => {
    const titulo = RESPOSTAS.guia.concluido.dicas.find((d) => d.id === id).titulo;
    const { ctx, page, s, erros } = await abrir({ guia: "concluido", dica: "nova", rota });
    await page.locator(CARD).waitFor();
    const r = await page.evaluate((sel) => {
      const c = document.querySelector(sel).getBoundingClientRect();
      return { titulo: document.querySelector(`${sel} h2`).textContent, dentro: c.left >= 0 && c.right <= innerWidth && document.documentElement.scrollWidth <= innerWidth };
    }, CARD);
    await page.waitForTimeout(300);
    await ir(page, "/");
    await naRota(page, "#/");
    await ir(page, rota);
    await naRota(page, `#${rota}`);
    await page.waitForTimeout(500);
    const volta = await page.locator(CARD).count(); // a mesma carga de página: o card montou de novo? não
    // Pela Ajuda do trilho: a linha da tela reabre a dica com o foco no título.
    await irParaAjuda(page);
    await comoFunciona(page, ROTULO[rota]).click();
    await naRota(page, `#${rota}`);
    await focoNoTitulo(page);
    const reaberta = await page.locator(`${CARD} h2`).textContent();
    // Com o guia aberto (Ajuda > Guia do painel) o card some.
    await ajudaDoTrilho(page);
    await page.locator(".guia-balao").waitFor();
    const comGuia = await page.locator(CARD).count();
    await ctx.close();
    // Celular: o Menu leva à Ajuda, que tem a mesma linha.
    const cel = await abrir({ width: 390, height: 844, guia: "concluido", rota });
    await irParaAjudaCel(cel.page);
    await comoFunciona(cel.page, ROTULO[rota]).click();
    await cel.page.locator(CARD).waitFor();
    const noCel = await cel.page.evaluate((sel) => { const b = document.querySelector(sel).getBoundingClientRect(); return b.left >= 0 && b.right <= innerWidth && document.documentElement.scrollWidth <= innerWidth; }, CARD);
    await cel.ctx.close();
    assert.deepEqual(r, { titulo, dentro: true });
    assert.deepEqual(s.dicas, [id]); // um POST só, mesmo saindo e voltando
    assert.equal(volta, 0);
    assert.equal(reaberta, titulo);
    assert.equal(comGuia, 0);
    assert.ok(noCel);
    assert.deepEqual(erros, []);
  });
}
