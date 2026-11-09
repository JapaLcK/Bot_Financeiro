// A tela Ajuda do /painel (#/ajuda, parts/AjudaTela.tsx): contato, "Aprenda a usar" e o feedback (POST /api/v2/feedback,
// api/v2/feedback.py, coberto em tests/test_api_v2_feedback.py). O servidor de mentira e o `abrir` vêm de _guia.mjs.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { abrir, acoes, naRota, navegador } from "./_guia.mjs";
import { RAIZ } from "./_painel.mjs";

navegador();

// `resposta`: o que o POST responde (ou uma função do nº do POST); `lento`: ms antes de responder; `posts`: os corpos recebidos.
const servidor = ({ resposta = { json: { enviado: true } }, lento = 0 } = {}) => {
  const f = { posts: [] };
  f.antes = (ctx) => ctx.route("**/api/v2/feedback", async (r) => {
    f.posts.push(r.request().postDataJSON());
    if (lento) await new Promise((ok) => setTimeout(ok, lento));
    return r.fulfill(typeof resposta === "function" ? resposta(f.posts.length) : resposta);
  });
  return f;
};
const campo = (page) => page.getByLabel("Sua mensagem");
const enviar = (page) => page.getByRole("button", { name: "Enviar feedback" });
const grupoTipo = (page) => page.getByRole("radiogroup", { name: "Tipo de feedback" });
const noTrilho = (page) => page.locator(".rail").getByRole("link", { name: "Ajuda" }).click();
const noMenu = async (page) => {
  await page.locator(".tabbar").getByRole("button", { name: "Menu" }).click();
  await page.locator(".menu-telas").getByRole("link", { name: "Ajuda" }).click();
};
const linhaDeDica = (page) => page.locator("main").getByRole("button", { name: /^Como funciona/ });
const ERRO = (status, code, message) => ({ status, json: { error: { code, message } } });

test("1280: a Ajuda do trilho abre a tela; título, apoio, links certos, grade 7/5 e sem rolagem lateral", async () => {
  const { ctx, page, erros } = await abrir({ guia: "concluido", rota: "/" });
  await noTrilho(page);
  await naRota(page, "#/ajuda");
  const r = await page.evaluate(() => {
    const links = Object.fromEntries([...document.querySelectorAll("main .ajuda-contato a, main .ajuda-aprender a")].map((a) => [a.querySelector(".ajuda-txt > span").textContent, [a.getAttribute("href"), a.target, a.rel]]));
    const rect = (q) => document.querySelector(q).getBoundingClientRect();
    const [c, a, fb] = [".ajuda-contato", ".ajuda-aprender", ".ajuda-feedback"].map(rect);
    const noTrilho = document.querySelector('.rail a[href="#/ajuda"]');
    return {
      titulo: document.querySelector("#page-title").textContent, apoio: document.querySelector(".page-lede").textContent,
      links, aprender: [...document.querySelectorAll("main .ajuda-aprender .ajuda-txt > span")].map((e) => e.textContent),
      colunas: [c.left < fb.left, a.left === c.left, a.top >= c.bottom, Math.abs(c.width / fb.width - 7 / 5) < 0.1],
      atual: noTrilho.getAttribute("aria-current"), doc: document.title, foco: document.activeElement.id,
      lateral: document.documentElement.scrollWidth - innerWidth,
    };
  });
  await ctx.close();
  assert.equal(r.titulo, "Ajuda");
  assert.equal(r.apoio, "Fale com a gente ou conte o que achou do PigBank.");
  assert.deepEqual(r.links, {
    "Converse com o Piggy": ["#/piggy", "", ""],
    "WhatsApp do Piggy": ["/wa", "_blank", "noopener"],
    "suporte@pigbankai.com": ["mailto:suporte@pigbankai.com", "", ""],
    "Formulário de contato": ["/suporte/contato", "", ""],
    "@pigbank.oficial": ["https://instagram.com/pigbank.oficial", "_blank", "noopener"],
    "Central de ajuda": ["/suporte", "", ""],
    "O que pedir ao Piggy": ["/comandos", "", ""],
  });
  assert.deepEqual(r.aprender, ["Guia do painel", "Central de ajuda", "O que pedir ao Piggy"]); // sem origem com dica: a linha "Como funciona" não existe
  assert.deepEqual(r.colunas, [true, true, true, true]);
  assert.deepEqual([r.atual, r.doc, r.foco, r.lateral], ["page", "PigBank · Ajuda", "page-title", 0]);
  assert.deepEqual(erros, []);
});

test("390: pelo Menu; ordem contato, feedback, aprender; textarea de 16px, alvos de 44px e nada estoura", async () => {
  const { ctx, page, erros } = await abrir({ width: 390, height: 844, guia: "concluido", rota: "/" });
  await noMenu(page);
  await naRota(page, "#/ajuda");
  const r = await page.evaluate(() => {
    const top = (q) => document.querySelector(q).getBoundingClientRect().top;
    const alvo = (e) => e.getBoundingClientRect().height;
    return {
      ordem: top(".ajuda-contato") < top(".ajuda-feedback") && top(".ajuda-feedback") < top(".ajuda-aprender"),
      fonte: getComputedStyle(document.querySelector("textarea")).fontSize,
      alvos: [...document.querySelectorAll(".ajuda-tipos span, .ajuda-btn, .ajuda-linha")].every((e) => alvo(e) >= 44),
      lateral: document.documentElement.scrollWidth - innerWidth,
      folha: document.querySelectorAll(".menu-telas[open]").length,
    };
  });
  await ctx.close();
  assert.deepEqual(r, { ordem: true, fonte: "16px", alvos: true, lateral: 0, folha: 0 });
  assert.deepEqual(erros, []);
});

for (const [rotulo, opcoes, abrirAjuda] of [["1280", {}, noTrilho], ["390", { width: 390, height: 844 }, noMenu]]) {
  test(`${rotulo}: feedback envia o tipo e o texto, mostra o sucesso e "Enviar outro" volta ao formulário vazio`, async () => {
    const f = servidor();
    const { ctx, page, erros } = await abrir({ guia: "concluido", rota: "/", antes: f.antes, ...opcoes });
    await abrirAjuda(page);
    const grupo = grupoTipo(page);
    const inicial = {
      tipos: await grupo.getByRole("radio").evaluateAll((rs) => rs.map((r) => [r.parentElement.textContent, r.checked])),
      dica: await campo(page).getAttribute("placeholder"), desabilitado: await enviar(page).isDisabled(), maximo: await campo(page).getAttribute("maxlength"),
    };
    await campo(page).fill("   \n  ");
    const soEspacos = await enviar(page).isDisabled();
    await grupo.getByRole("radio", { name: "Problema" }).check();
    const dicaProblema = await campo(page).getAttribute("placeholder");
    await grupo.getByRole("radio", { name: "Elogio" }).check();
    const dicaElogio = await campo(page).getAttribute("placeholder");
    await grupo.getByRole("radio", { name: "Problema" }).check();
    await campo(page).fill("  A tela de metas travou ao salvar  ");
    const habilitado = await enviar(page).isEnabled();
    await enviar(page).click();
    await page.getByRole("heading", { name: "Recebemos, valeu" }).waitFor();
    const sucesso = [await page.getByText("Seu feedback chegou no time do PigBank.").count(), await campo(page).count(), await page.evaluate(() => document.activeElement?.textContent)];
    await page.getByRole("button", { name: "Enviar outro" }).click();
    await page.waitForFunction(() => document.activeElement?.tagName === "TEXTAREA", null, { timeout: 3000 }); // o foco vai ao campo
    const volta = [await campo(page).inputValue(), await grupo.getByRole("radio", { name: "Ideia" }).isChecked(), await enviar(page).isDisabled(), await page.evaluate(() => document.activeElement?.tagName)];
    await ctx.close();
    assert.deepEqual(inicial, { tipos: [["Ideia", true], ["Problema", false], ["Elogio", false]], dica: "O que faria o PigBank ficar melhor pra você?", desabilitado: true, maximo: "4000" });
    assert.equal(soEspacos, true);
    assert.equal(dicaProblema, "O que aconteceu, e em qual tela?");
    assert.equal(dicaElogio, "O que você curtiu?");
    assert.equal(habilitado, true);
    assert.deepEqual(f.posts, [{ tipo: "problema", mensagem: "A tela de metas travou ao salvar" }]);
    assert.deepEqual(sucesso, [1, 0, "Recebemos, valeu"]);
    assert.deepEqual(volta, ["", true, true, "TEXTAREA"]);
    assert.deepEqual(erros, []);
  });
}

test("falha do servidor (502): alerta visível, texto preservado, nada de sucesso; o 429 mostra o texto do front, não o `message` do servidor; tentar de novo envia", async () => {
  const f = servidor({ resposta: (n) => n === 1 ? ERRO(502, "feedback_nao_enviado", "Bad Gateway") : n === 2 ? ERRO(429, "rate_limited", "TEXTO-DO-SERVIDOR-IGNORADO") : { json: { enviado: true } } });
  const { ctx, page } = await abrir({ guia: "concluido", rota: "/", antes: f.antes });
  await noTrilho(page);
  await campo(page).fill("Meu texto que não pode sumir");
  await grupoTipo(page).getByRole("radio", { name: "Elogio" }).check();
  await enviar(page).click();
  const alerta = page.getByRole("alert");
  await alerta.waitFor();
  const falha = { texto: await alerta.textContent(), campo: await campo(page).inputValue(), sucesso: await page.getByText("Recebemos, valeu").count(), pode: await enviar(page).isEnabled(), tipo: await grupoTipo(page).getByRole("radio", { name: "Elogio" }).isChecked() };
  await enviar(page).click();
  await page.getByRole("alert").filter({ hasText: "Muitas tentativas. Aguarde alguns minutos e tente novamente." }).waitFor();
  const corpoAlerta = await page.locator("main").textContent();
  await enviar(page).click();
  await page.getByRole("heading", { name: "Recebemos, valeu" }).waitFor();
  await ctx.close();
  assert.match(falha.texto, /Não foi possível enviar agora/);
  assert.deepEqual([falha.campo, falha.sucesso, falha.pode, falha.tipo], ["Meu texto que não pode sumir", 0, true, true]);
  assert.equal(f.posts.length, 3);
  assert.ok(f.posts.every((p) => p.mensagem === "Meu texto que não pode sumir" && p.tipo === "elogio"));
  assert.ok(!corpoAlerta.includes("TEXTO-DO-SERVIDOR-IGNORADO"));
});

test("o foco vai ao título do sucesso uma vez só: um re-render depois não o rouba da barra do Piggy; novo sucesso volta a focar", async () => {
  const f = servidor();
  const { ctx, page, s } = await abrir({ guia: "concluido", rota: "/", antes: f.antes });
  await noTrilho(page);
  const ativo = () => page.evaluate(() => document.activeElement?.id);
  const sucesso = async () => { await campo(page).fill("oi"); await enviar(page).click(); await page.getByRole("heading", { name: "Recebemos, valeu" }).waitFor(); };
  await sucesso();
  const noTitulo = await ativo();
  const barra = page.locator("#askbar-input");
  await barra.click();
  s.g = { ...s.g, dicas: (s.g.dicas ?? []).map((d) => ({ ...d, titulo: d.titulo + " x" })) }; // o refetch do guia re-renderiza o App
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
  await page.waitForResponse("**/api/v2/guia", { timeout: 4000 });
  await page.waitForTimeout(400);
  const depois = await ativo();
  await page.getByRole("button", { name: "Enviar outro" }).click();
  await sucesso();
  const noSegundo = await ativo();
  await ctx.close();
  assert.deepEqual([noTitulo, depois, noSegundo], ["ajuda-t-fb", "askbar-input", "ajuda-t-fb"]);
});

test("envio lento: o botão fica desabilitado e o duplo clique manda um POST só", async () => {
  const f = servidor({ lento: 600 });
  const { ctx, page } = await abrir({ guia: "concluido", rota: "/", antes: f.antes });
  await noTrilho(page);
  await campo(page).fill("oi");
  await enviar(page).dblclick();
  await page.waitForSelector("button.ajuda-btn:disabled", { timeout: 2000 });
  const durante = await enviar(page).isDisabled();
  await page.getByRole("heading", { name: "Recebemos, valeu" }).waitFor();
  await ctx.close();
  assert.equal(durante, true);
  assert.equal(f.posts.length, 1);
});

test("\"Como funciona <tela>\": aparece vindo de tela com dica, ao tocar volta à origem e abre a dica; some sem origem, vindo de tela sem dica e depois de passar por outra", async () => {
  const { ctx, page } = await abrir({ guia: "concluido", rota: "/metas" });
  await noTrilho(page);
  const linha = page.locator("main").getByRole("button", { name: "Como funciona Metas" });
  await linha.waitFor();
  const ordem = await page.locator("main .ajuda-aprender .ajuda-txt > span").allTextContents();
  await linha.click();
  await naRota(page, "#/metas");
  await page.waitForFunction(() => document.activeElement?.id === "dica-titulo", null, { timeout: 5000 });
  const card = await page.locator(".dica-tela h2").textContent();
  // Origem sem dica (Resumo): a linha não existe.
  await page.evaluate(() => { location.hash = "#/"; });
  await naRota(page, "#/");
  await noTrilho(page);
  const doResumo = await linhaDeDica(page).count();
  // Passou por outra tela (a Ajuda → Piggy → Ajuda): a origem é a última tela, não a das dicas.
  await page.getByRole("link", { name: "Converse com o Piggy" }).click();
  await naRota(page, "#/piggy");
  await noTrilho(page);
  const doPiggy = await linhaDeDica(page).count();
  await ctx.close();
  // Recarregou em #/ajuda: sem origem.
  const direto = await abrir({ guia: "concluido", rota: "/ajuda" });
  await direto.page.locator("main .ajuda-aprender").waitFor();
  const semOrigem = [await linhaDeDica(direto.page).count(), await direto.page.getByRole("button", { name: "Guia do painel" }).count()];
  await direto.ctx.close();
  assert.deepEqual(ordem, ["Guia do painel", "Como funciona Metas", "Central de ajuda", "O que pedir ao Piggy"]);
  assert.match(card, /\S/);
  assert.deepEqual([doResumo, doPiggy, semOrigem], [0, 0, [0, 1]]);
});

test("a dica pedida pela Ajuda abre mesmo com o guia ainda carregando devagar e uma única vez (sem POST de carimbo repetido)", async () => {
  const { ctx, page, s } = await abrir({ guia: "concluido", dica: "nova", dicaLenta: 400, rota: "/assinaturas" });
  await page.locator(".dica-tela").waitFor();
  await page.getByRole("button", { name: "Entendi" }).click();
  await noTrilho(page);
  await page.locator("main").getByRole("button", { name: "Como funciona Assinaturas" }).click();
  await page.locator(".dica-tela").waitFor();
  const cards = await page.locator(".dica-tela").count();
  await page.waitForTimeout(600);
  await ctx.close();
  assert.equal(cards, 1);
  assert.deepEqual(s.dicas, ["assinaturas.marcas"]);
});

for (const [rotulo, opcoes, abrirAjuda] of [["1280", {}, noTrilho], ["390", { width: 390, height: 844 }, noMenu]]) {
  test(`${rotulo}: o "Guia do painel" da Ajuda abre o guia, que pede a aba do passo 1 (a Ajuda não é a tela dele)`, async () => {
    const { ctx, page, s } = await abrir({ guia: "concluido", rota: "/", ...opcoes });
    await abrirAjuda(page);
    await page.locator("main").getByRole("button", { name: "Guia do painel" }).click();
    await page.locator("#guia-titulo", { hasText: "Agora toca" }).waitFor({ timeout: 5000 });
    const r = [await page.locator(".guia-balao").count(), await page.evaluate(() => location.hash)];
    await ctx.close();
    assert.deepEqual(r, [1, "#/ajuda"]);
    assert.deepEqual(acoes(s), ["reabrir"]);
  });
}

test("o bundle do painel não carrega o e-mail do time (lucas@pigbankai.com), só o público", () => {
  const arquivos = readdirSync(join(RAIZ, "frontend")).filter((n) => n.startsWith("dashboard-app."));
  assert.ok(arquivos.length >= 2, arquivos.join());
  const texto = arquivos.map((n) => readFileSync(join(RAIZ, "frontend", n), "utf8")).join("\n");
  assert.ok(!/lucas@/i.test(texto), "o e-mail do time está no bundle");
  assert.ok(texto.includes("suporte@pigbankai.com"));
});
