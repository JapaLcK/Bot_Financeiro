/**
 * /painel#/conta/familia (webapp/src/dashboard/familia/*): grupo e convites do Plano Família, sobre a api/v2/familia.py.
 *   · as três visões (sem família, membro, titular) e o item "Família" na Conta; resposta fora do contrato = erro sem quebrar a tela;
 *   · criar (402 com link para a Cobrança), convite (gerar, validade, copiar, compartilhar, revogar o recém-gerado, cheio, 409/429/402),
 *     remover (ref codificado na URL), desfazer em 2 passos (nonce exato; 409 relê), sair (204 é sucesso e navega);
 *   · toda escrita leva x-csrf-token e content-type json; clique duplo = 1 pedido.
 * Medidas de layout (desktop e celular): dashboard_v2_familia_layout.test.mjs. A página de aceite: familia_entrar.test.mjs.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { ERRO, F, NONCE, abrirFamilia, titular } from "./_familia.mjs";

const browser = navegador();
void browser;

const com = async (opts, fn) => {
  const t = await abrirFamilia(opts);
  try { await fn(t); assert.deepEqual(t.erros, [], "pageerror"); } finally { await t.ctx.close(); }
};
const MARIA = { ref: "9f1c2a40-7b1e-4a53-8d3a-0c5d6e7f8a91", nome: "Maria Aparecida Costa", status: "ativo" };
const JOAO = { ref: "b2d4f6a8-1c3e-4a5b-9d7f-123456789abc", nome: "João Pedro", status: "suspenso" };
const clipboard = (ctx) => ctx.addInitScript(() => { Object.defineProperty(navigator, "clipboard", { value: { writeText: async (t) => { window.__copiado = t; } }, configurable: true }); });

test("as três visões", async () => {
  await com({}, async ({ texto, bt }) => {
    assert.equal(await texto(), "Criar família");
    await bt("Criar família").waitFor();
  });
  await com({ visao: F.membro }, async ({ texto, bt }) => {
    assert.equal(await texto(), "Você está numa família. O que o titular vê Resumo do mês Lançamentos Saldo, contas, Pix e conversas nunca aparecem. Quem viu Ninguém viu nada ainda. Sair da família");
    await bt("Sair da família").waitFor();
  });
  await com({ visao: titular({ membros: [MARIA, JOAO] }) }, async ({ texto, bt, page }) => {
    // só o primeiro nome; selo no suspenso; os lugares contam o titular (2 membros + 1 = 3 de 4)
    assert.equal(await texto(), "3 de 4 lugares Maria Não compartilha Remover João Suspenso Não compartilha Remover Gerar link de convite Desfazer família");
    assert.equal(await page.getByText("Aparecida").count(), 0);
    await bt("Gerar link de convite").waitFor();
  });
});

test("o item Família está na Conta, na barra lateral e no Menu do celular", async () => {
  await com({}, async ({ page }) => {
    const itens = await page.locator(".rail-conta li a").evaluateAll((es) => es.map((e) => e.textContent.trim()));
    assert.deepEqual(itens, ["Perfil", "Cobrança", "Configurações", "Bancos conectados", "Família"]);
    assert.equal(await page.locator('.rail-conta a[aria-current="page"]').textContent(), "Família");
    assert.equal(await page.locator("h1#page-title").textContent(), "Família");
  });
});

test("criar família: um POST, com CSRF e content-type json; clique duplo = 1 pedido; a tela vira a do titular", async () => {
  await com({}, async ({ s, bt, texto, escritas }) => {
    s.criar = async () => { await new Promise((ok) => setTimeout(ok, 300)); };
    await bt("Criar família").dblclick();
    await bt("Gerar link de convite").waitFor();
    assert.equal(await texto(), "1 de 4 lugares Gerar link de convite Desfazer família");
    const e = escritas();
    assert.equal(e.length, 1);
    assert.deepEqual([e[0].metodo, e[0].caminho, e[0].csrf, e[0].tipo], ["POST", "", "tok-123", "application/json"]);
  });
});

test("criar sem plano que cubra: card de upgrade com o CTA para /precos", async () => {
  await com({}, async ({ s, bt, secao }) => {
    s.criar = ERRO(402, "family_coverage_required");
    await bt("Criar família").click();
    await secao.getByText("A família é do plano Pro").waitFor();
    const l = secao.getByRole("link", { name: "Ver o Pro" });
    assert.equal(await l.getAttribute("href"), "/precos");
    await bt("Criar família").waitFor(); // o botão volta
  });
  await com({}, async ({ s, bt, secao }) => { // o 402 do gate de plano (free) tem outro código e o mesmo texto
    s.criar = ERRO(402, "plan_selection_required");
    await bt("Criar família").click();
    await secao.getByText("A família é do plano Pro").waitFor();
  });
});

test("convite: gerar, ver link e validade, copiar, revogar só o recém-gerado", async () => {
  await com({ visao: titular({ membros: [MARIA] }), antes: clipboard }, async ({ s, bt, texto, page, escritas, secao }) => {
    await bt("Gerar link de convite").click();
    await secao.getByText("https://pigbankai.com/familia/entrar#t=tok-1").waitFor();
    assert.match(await texto(), /Vale até 08\/11\./); // 2026-11-08T15:00Z, dd/mm no fuso do painel
    assert.equal(await bt("Gerar link de convite").count(), 0); // com o link à vista não há 2º botão: um convite por vez
    await bt("Copiar").click();
    await secao.getByText("Link copiado").waitFor();
    assert.equal(await page.evaluate(() => window.__copiado), "https://pigbankai.com/familia/entrar#t=tok-1");
    await bt("Revogar").click();
    await bt("Gerar link de convite").waitFor();
    const e = escritas();
    assert.deepEqual(e.map((x) => [x.metodo, x.caminho]), [["POST", "/convites"], ["DELETE", "/convites"]]);
    assert.deepEqual(JSON.parse(e[1].corpo), { token: "tok-1" });
    assert.equal(await page.getByText("tok-1").count(), 0); // o link sumiu da tela
    // o token não vai para o armazenamento do aparelho
    assert.equal(await page.evaluate(() => JSON.stringify([{ ...localStorage }, { ...sessionStorage }]).includes("tok-1")), false);
    void s;
  });
});

test("revogar com 404 (convite já aceito ou inexistente): o link sai da tela, a família é relida e dá para gerar outro", async () => {
  await com({ visao: titular({ membros: [MARIA] }), antes: clipboard }, async ({ s, bt, secao, escritas }) => {
    await bt("Gerar link de convite").click();
    await secao.getByText("https://pigbankai.com/familia/entrar#t=tok-1").waitFor();
    s.revogar = ERRO(404, "not_found");
    const gets = () => s.reqs.filter((q) => q.metodo === "GET" && q.caminho === "").length;
    const antes = gets();
    await bt("Revogar").click();
    await bt("Gerar link de convite").waitFor();
    assert.equal(await secao.getByText("tok-1").count(), 0);
    assert.ok(gets() > antes, "releu a família");
    assert.equal(escritas().filter((q) => q.metodo === "DELETE").length, 1);
  });
});

test("revogar com 500 mantém o link na tela e avisa", async () => {
  await com({ visao: titular({ membros: [MARIA] }), antes: clipboard }, async ({ s, bt, secao }) => {
    await bt("Gerar link de convite").click();
    await secao.getByText("https://pigbankai.com/familia/entrar#t=tok-1").waitFor();
    s.revogar = ERRO(500, "internal");
    await bt("Revogar").click();
    await secao.getByRole("alert").waitFor();
    assert.equal(await secao.getByText("tok-1").count(), 1);
  });
});

test("copiar sem permissão avisa; Compartilhar só existe com navigator.share", async () => {
  await com({ visao: titular({ membros: [] }) }, async ({ bt, secao, page }) => {
    await page.evaluate(() => { Object.defineProperty(navigator, "clipboard", { value: { writeText: () => Promise.reject(new Error("negado")) }, configurable: true }); });
    await bt("Gerar link de convite").click();
    await bt("Copiar").click();
    await secao.getByText("Não foi possível copiar").waitFor();
    assert.equal(await page.evaluate(() => typeof navigator.share), "undefined"); // o Chromium do teste não tem
    assert.equal(await bt("Compartilhar").count(), 0);
  });
  await com({ visao: titular({ membros: [] }), antes: (ctx) => ctx.addInitScript(() => { navigator.share = async (d) => { window.__compartilhado = d; }; }) }, async ({ bt, page }) => {
    await bt("Gerar link de convite").click();
    await bt("Compartilhar").click();
    assert.deepEqual(await page.evaluate(() => window.__compartilhado), { url: "https://pigbankai.com/familia/entrar#t=tok-1" });
  });
});

test("convite: família cheia desabilita o botão; os erros do servidor viram texto", async () => {
  const cheia = titular({ capacidade: 3, membros: [MARIA, JOAO] }); // 2 membros + o titular = 3 de 3
  await com({ visao: cheia }, async ({ bt, texto, escritas }) => {
    assert.match(await texto(), /3 de 3 lugares/);
    assert.equal(await bt("Gerar link de convite").isDisabled(), true);
    assert.equal(escritas().length, 0);
  });
  for (const [status, code, esperado] of [[409, "full", "Todos os lugares estão ocupados."], [429, "rate_limited", "Muitas tentativas. Tente de novo mais tarde."], [402, "family_coverage_required", "A família é do plano Pro"]]) {
    await com({ visao: titular({ membros: [] }) }, async ({ s, bt, secao }) => {
      s.convidar = ERRO(status, code);
      await bt("Gerar link de convite").click();
      await secao.getByText(esperado).waitFor();
      await bt("Gerar link de convite").waitFor();
    });
  }
});

test("remover: o ref vai codificado na URL; cancelar não manda nada", async () => {
  const estranho = { ...MARIA, ref: "a/b?c#d e" };
  await com({ visao: titular({ membros: [estranho, JOAO] }) }, async ({ s, page, dlg, texto, escritas }) => {
    const remover = page.getByRole("button", { name: "Remover" }).first();
    await remover.click();
    await dlg().getByText("Remover Maria?").waitFor();
    await dlg().getByRole("button", { name: "Cancelar" }).click();
    assert.equal(escritas().length, 0);
    await remover.click();
    await dlg().getByRole("button", { name: "Remover", exact: true }).click();
    await page.locator(".fam-lista b", { hasText: "Maria" }).waitFor({ state: "detached" });
    const e = escritas();
    assert.equal(e.length, 1);
    assert.deepEqual([e[0].metodo, e[0].caminho, e[0].csrf, e[0].tipo], ["DELETE", "/membros/a%2Fb%3Fc%23d%20e", "tok-123", "application/json"]);
    assert.match(await texto(), /2 de 4 lugares João Suspenso/);
    void s;
  });
});

test("remover: 404 (já saiu) vira texto e a lista é relida", async () => {
  await com({ visao: titular({ membros: [MARIA] }) }, async ({ s, page, dlg, secao }) => {
    s.remover = (r) => { s.visao = titular({ membros: [] }); return ERRO(404, "not_found")(r); };
    await page.getByRole("button", { name: "Remover" }).click();
    await dlg().getByRole("button", { name: "Remover", exact: true }).click();
    await secao.getByText("Isso não existe mais.").waitFor();
    await page.locator(".fam-lista b", { hasText: "Maria" }).waitFor({ state: "detached" });
    assert.equal(s.reqs.filter((q) => q.metodo === "GET" && q.caminho === "").length, 2); // a releitura
  });
});

test("desfazer: lê a prévia, confirma com o nonce EXATO e volta a 'Criar família'", async () => {
  await com({ visao: titular({ membros: [MARIA, JOAO] }) }, async ({ s, bt, dlg, escritas, secao }) => {
    await bt("Desfazer família").click();
    await dlg().getByText("2 pessoas perdem o acesso.").waitFor();
    assert.deepEqual(s.reqs.map((q) => q.metodo + q.caminho).slice(-1), ["GET/dissolucao"]); // a leitura vem ANTES de qualquer escrita
    assert.equal(escritas().length, 0);
    await dlg().getByRole("button", { name: "Desfazer", exact: true }).click();
    await bt("Criar família").waitFor();
    const e = escritas();
    assert.deepEqual([e[0].metodo, e[0].caminho, e[0].csrf, e[0].tipo], ["POST", "/dissolucao", "tok-123", "application/json"]);
    assert.deepEqual(JSON.parse(e[0].corpo), { confirmacao: NONCE });
    assert.equal(await secao.getByText("perdem").count(), 0);
  });
});

test("desfazer com a família mudada: 409 relê, e o novo clique usa o nonce NOVO (nunca o velho)", async () => {
  await com({ visao: titular({ membros: [MARIA] }) }, async ({ s, bt, dlg, secao, escritas }) => {
    await bt("Desfazer família").click();
    await dlg().getByText("1 pessoa perde o acesso.").waitFor();
    s.nonce = "nonce-novo"; // alguém aceitou um convite entre ver e confirmar
    s.visao = titular({ membros: [MARIA, JOAO] });
    await dlg().getByRole("button", { name: "Desfazer", exact: true }).click();
    await secao.getByText("A família mudou. Confira de novo.").waitFor();
    await secao.getByText("João").waitFor(); // a releitura trouxe o membro novo
    await bt("Desfazer família").click();
    await dlg().getByText("2 pessoas perdem o acesso.").waitFor();
    await dlg().getByRole("button", { name: "Desfazer", exact: true }).click();
    await bt("Criar família").waitFor();
    assert.deepEqual(escritas().map((q) => JSON.parse(q.corpo).confirmacao), [NONCE, "nonce-novo"]);
  });
});

test("sair: 204 é sucesso e a página vai a /painel; o erro fica na tela e não navega", async () => {
  await com({ visao: F.membro }, async ({ s, bt, dlg, page, escritas, secao }) => {
    s.sair = ERRO(409, "owner_cannot_leave");
    await bt("Sair da família").click();
    await dlg().getByRole("button", { name: "Sair", exact: true }).click();
    await secao.getByText("Não foi possível concluir agora. Tente de novo.").waitFor();
    assert.match(page.url(), /painel\.html#\/conta\/familia$/);
    s.sair = null;
    const vai = page.waitForRequest((q) => new URL(q.url()).pathname === "/painel");
    await bt("Sair da família").click();
    await dlg().getByRole("button", { name: "Sair", exact: true }).click();
    await vai;
    const e = escritas().filter((q) => q.caminho === "/sair");
    assert.equal(e.length, 2);
    assert.deepEqual([e[1].csrf, e[1].tipo], ["tok-123", "application/json"]);
  });
});

test("resposta fora do contrato é erro de carga, nunca tela quebrada; 'Tentar de novo' lê outra vez", async () => {
  for (const corpo of [{ papel: "titular" }, { papel: "titular", capacidade: "4", membros: [], convites_ativos: 0 }, { papel: "titular", capacidade: 4, membros: [{ ref: "", nome: "x", status: "ativo" }], convites_ativos: 0 },
    { papel: "titular", capacidade: 4, membros: [{ ref: "r", nome: "x", status: "banido" }], convites_ativos: 0 }, { papel: "outro" }, [], "texto"]) {
    await com({}, async ({ s, secao, bt, page }) => {
      s.get = (r) => r.fulfill({ json: corpo });
      await page.reload();
      await secao.getByText("Não foi possível carregar a família.").waitFor();
      assert.equal(await bt("Criar família").count(), 0); // erro nunca vira "sem família"
      s.get = null;
      await bt("Tentar de novo").click();
      await bt("Criar família").waitFor();
    });
  }
});

test("só o 404 not_found é 'sem família'; outro 404 e o 500 são erro de carga", async () => {
  for (const [status, code] of [[404, "dashboard_v2_disabled"], [500, "internal"]]) {
    await com({}, async ({ s, secao, bt, page }) => {
      s.get = ERRO(status, code);
      await page.reload();
      await secao.getByText("Não foi possível carregar a família.").waitFor();
      assert.equal(await bt("Criar família").count(), 0);
    });
  }
});

test("toda escrita leva x-csrf-token e content-type json", async () => {
  await com({ visao: titular({ membros: [MARIA] }), antes: clipboard }, async ({ bt, page, dlg, escritas }) => {
    await bt("Gerar link de convite").click();
    await bt("Revogar").click();
    await bt("Gerar link de convite").waitFor();
    await page.getByRole("button", { name: "Remover" }).click();
    await dlg().getByRole("button", { name: "Remover", exact: true }).click();
    await bt("Desfazer família").waitFor();
    await bt("Desfazer família").click();
    await dlg().getByRole("button", { name: "Desfazer", exact: true }).click();
    await bt("Criar família").waitFor();
    await bt("Criar família").click();
    await bt("Desfazer família").waitFor();
    const e = escritas();
    assert.equal(e.length, 5);
    for (const q of e) assert.deepEqual([q.csrf, q.tipo], ["tok-123", "application/json"], `${q.metodo} ${q.caminho}`);
  });
});

// mutação: tirar `!membro` do CTA (escrita.tsx) -> o membro ganha o botão de compra; tirar `!noApp` -> o app iOS ganha (3.1.1); inverter o `membro` da Casa/Familia -> o titular vê "pausada".
test("card de upgrade: titular com CTA, membro sem CTA, app sem CTA", async () => {
  const MEMBRO = "A família está pausada", TITULAR = "A família é do plano Pro";
  await com({}, async ({ s, bt, secao }) => { // sem família: pode assinar
    s.criar = ERRO(402, "family_coverage_required");
    await bt("Criar família").click();
    await secao.getByText(TITULAR).waitFor();
    assert.equal(await secao.getByRole("link", { name: "Ver o Pro" }).count(), 1);
    assert.equal(await secao.getByText(MEMBRO).count(), 0);
  });
  await com({ visao: F.membro }, async ({ page, s, secao }) => { // membro: texto de pausa, sem compra
    s.liberar = ERRO(402, "family_coverage_required");
    await page.getByRole("switch").first().click();
    await secao.getByText(MEMBRO).waitFor();
    assert.match(await secao.innerText(), /Suas metas e permissões ficam guardadas\./);
    assert.equal(await secao.getByRole("link", { name: "Ver o Pro" }).count(), 0);
    assert.equal(await secao.getByText(TITULAR).count(), 0);
  });
  await com({ antes: (ctx) => ctx.addInitScript(() => { window.PB_IN_APP = true; }) }, async ({ s, bt, secao }) => { // app iOS: texto sem botão
    s.criar = ERRO(402, "family_coverage_required");
    await bt("Criar família").click();
    await secao.getByText(TITULAR).waitFor();
    assert.match(await secao.innerText(), /Junte até 3 pessoas/);
    assert.equal(await secao.getByRole("link", { name: "Ver o Pro" }).count(), 0);
  });
});
