// A dica de primeiro uso de Assinaturas (#728, parts/Dica.tsx) e a Ajuda que vira menu nas telas
// com dica (parts/Ajuda.tsx). O servidor de mentira e as opções `dica`/`dicaLenta` estão em
// _guia.mjs; o carimbo e o portão de plano de verdade, em tests/test_api_v2_guia_dica.py.
import { test } from "node:test";
import assert from "node:assert/strict";
import { abrir, acoes, naRota, navegador } from "./_guia.mjs";

navegador();

const CARD = ".dica-tela";
const TITULO = "Como eu acho suas assinaturas";
const ir = (page, rota) => page.evaluate((h) => { location.hash = h; }, `#${rota}`);
const focoNoTitulo = (page) => page.waitForFunction(() => document.activeElement?.id === "dica-titulo", null, { timeout: 5000 });
const menu = (page, onde) => page.locator(`${onde} .ajuda > button`);

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

test("dica já vista: recarrega sem card e sem POST", async () => {
  const { ctx, page, s } = await abrir({ guia: "concluido", rota: "/assinaturas" });
  await page.locator(".panel").first().waitFor();
  await page.waitForTimeout(400);
  const n = await page.locator(CARD).count();
  await ctx.close();
  assert.equal(n, 0);
  assert.deepEqual(s.dicas, []);
});

test("trilho 1280: Ajuda é menu (guia ou dica); a dica reabre com o foco no título; Tab e Enter; Esc fecha e devolve o foco; o guia esconde o card", async () => {
  const { ctx, page, s } = await abrir({ guia: "concluido", rota: "/assinaturas" });
  const ajuda = menu(page, ".rail");
  await ajuda.waitFor();
  const fechado = await ajuda.getAttribute("aria-expanded");
  await ajuda.click();
  const itens = await page.locator(".rail .ajuda-menu button").allTextContents();
  // O menu fica por cima da página (o trilho sobe acima do conteúdo).
  const porCima = await page.evaluate(() => {
    const b = document.querySelector(".rail .ajuda-menu button").getBoundingClientRect();
    return document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2)?.closest(".ajuda-menu") != null;
  });
  await page.getByRole("button", { name: "Como funciona esta tela" }).click();
  await focoNoTitulo(page);
  const aberto = [await ajuda.getAttribute("aria-expanded"), await page.locator(".ajuda-menu").count()];
  await page.locator(CARD).getByRole("button", { name: "Entendi" }).click();
  const depois = [await page.locator(CARD).count(), await page.evaluate(() => document.activeElement?.id)];
  // Teclado: Enter abre, Tab vai aos itens, Enter no 2º reabre a dica.
  await ajuda.focus();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Enter");
  await focoNoTitulo(page);
  // Esc: fecha o menu e o foco volta à Ajuda.
  await ajuda.click();
  await page.keyboard.press("Escape");
  const esc = [await page.locator(".ajuda-menu").count(), await page.evaluate(() => document.activeElement?.closest(".ajuda") != null)];
  // Toque fora fecha.
  await ajuda.click();
  await page.mouse.click(900, 300);
  const fora = await page.locator(".ajuda-menu").count();
  // "Guia do painel": reabre o guia; com o balão aberto, o card some.
  await ajuda.click();
  await page.getByRole("button", { name: "Guia do painel" }).click();
  await page.locator(".guia-balao").waitFor();
  const comGuia = await page.locator(CARD).count();
  await ctx.close();
  assert.equal(fechado, "false");
  assert.deepEqual(itens, ["Guia do painel", "Como funciona esta tela"]);
  assert.ok(porCima);
  assert.deepEqual(aberto, ["false", 0]);
  assert.deepEqual(depois, [0, "page-title"]);
  assert.deepEqual(esc, [0, true]);
  assert.equal(fora, 0);
  assert.equal(comGuia, 0);
  assert.deepEqual(acoes(s), ["reabrir"]);
  assert.deepEqual(s.dicas, []); // reabrir a dica não grava nada
});

test("telas sem dica (/ e /gastos): a Ajuda é o botão de sempre e abre o guia direto", async () => {
  for (const rota of ["/", "/gastos"]) {
    const { ctx, page, s } = await abrir({ guia: "concluido", rota });
    const ajuda = menu(page, ".rail");
    const exp = await ajuda.getAttribute("aria-expanded");
    await ajuda.click();
    await page.locator(".guia-balao").waitFor();
    await ctx.close();
    assert.equal(exp, null, rota);
    assert.deepEqual(acoes(s), ["reabrir"], rota);
  }
});

test("390: o menu da barra de baixo abre acima dela e dentro da tela; o item reabre o card; nada estoura", async () => {
  const { ctx, page } = await abrir({ width: 390, height: 844, guia: "concluido", rota: "/assinaturas" });
  const ajuda = menu(page, ".tabbar");
  await ajuda.click();
  const r = await page.evaluate(() => {
    const m = document.querySelector(".tabbar .ajuda-menu").getBoundingClientRect(), t = document.querySelector(".tabbar").getBoundingClientRect();
    return { dentro: m.left >= 0 && m.right <= innerWidth, acima: m.bottom <= t.top, cabe: document.documentElement.scrollWidth <= innerWidth };
  });
  await page.locator(".tabbar .ajuda-menu").getByRole("button", { name: "Como funciona esta tela" }).click();
  await focoNoTitulo(page);
  const card = await page.evaluate((sel) => {
    const b = document.querySelector(sel).getBoundingClientRect();
    return b.left >= 0 && b.right <= innerWidth && document.documentElement.scrollWidth <= innerWidth;
  }, CARD);
  await ctx.close();
  assert.deepEqual(r, { dentro: true, acima: true, cabe: true });
  assert.ok(card);
});

// Decisão do dono (2026-10-04): abaixo de 360 a Ajuda sai da barra e a dica não reabre por ela.
test("320: a Ajuda fica fora da barra; a dica nova aparece inteira na tela", async () => {
  const { ctx, page } = await abrir({ width: 320, height: 640, guia: "concluido", dica: "nova", rota: "/assinaturas" });
  await page.locator(CARD).waitFor();
  const r = await page.evaluate((sel) => {
    const b = document.querySelector(sel).getBoundingClientRect();
    return { ajuda: document.querySelector(".tabbar .ajuda").getClientRects().length, card: b.left >= 0 && b.right <= innerWidth,
      cabe: document.documentElement.scrollWidth <= innerWidth };
  }, CARD);
  await ctx.close();
  assert.deepEqual(r, { ajuda: 0, card: true, cabe: true });
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

test("Essencial (sem a dica): nem card nem menu; a Ajuda abre o guia", async () => {
  const { ctx, page } = await abrir({ guia: "concluido", plano: "essencial", dica: "sem", rota: "/assinaturas" });
  await page.waitForTimeout(400);
  const r = [await page.locator(CARD).count(), await menu(page, ".rail").getAttribute("aria-expanded")];
  await ctx.close();
  assert.deepEqual(r, [0, null]);
});

// Rollback: o navegador guarda o dashboard-app.js novo (nome fixo) e o servidor volta a responder
// o guia sem `dicas`. A tela abre como antes do #728, sem quebrar o render.
test("guia sem a chave dicas (servidor antigo): Assinaturas abre, a Ajuda é o botão de sempre, sem card nem POST", async () => {
  const { ctx, page, s, erros } = await abrir({ guia: "concluido", dica: "ausente", rota: "/assinaturas" });
  await page.locator(".panel").first().waitFor();
  await page.waitForTimeout(400);
  const r = [await page.locator("#page-title").isVisible(), await menu(page, ".rail").getAttribute("aria-expanded"), await page.locator(CARD).count()];
  await ctx.close();
  assert.deepEqual(r, [true, null, 0]);
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
