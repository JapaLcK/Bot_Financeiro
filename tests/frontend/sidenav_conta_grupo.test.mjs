/**
 * O grupo "Conta" do menu lateral do desktop recolhe (parts/RailConta.tsx): o subtítulo é um botão com aria-expanded/aria-controls.
 * Fechado fora da Conta, aberto numa tela da Conta (inclusive por deep link), o clique e o teclado alternam, e a página atual nunca some.
 * O Sair fica FORA do que recolhe: está à vista e clicável de qualquer tela, com o grupo aberto ou fechado.
 * O Menu do celular (MenuTelas.tsx) NÃO recolhe: é uma folha rolável que lista tudo.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { abrir, navegador } from "./_guia.mjs";

const browser = navegador();
void browser;

const CONTA = ["/conta/perfil", "/conta/cobranca", "/conta/configuracoes", "/conta/bancos", "/conta/familia"];
const TOG = ".rail-conta-t";
// O estado observável: o botão (aria-expanded) e o que está de fato à vista no grupo (nome acessível dos itens não ocultos).
const estado = (page) => page.evaluate((tog) => {
  const sairNaTela = () => { const b = document.querySelector('.rail-conta button[title="Sair"]'), r = b.getBoundingClientRect(); return r.width > 0 && document.elementFromPoint(r.left + 4, r.top + r.height / 2)?.closest("button") === b; };
  const b = document.querySelector(tog), lista = document.getElementById(b.getAttribute("aria-controls"));
  const vis = (e) => e.getClientRects().length > 0;
  return {
    expandido: b.getAttribute("aria-expanded"), controla: lista?.closest(".rail-conta") != null,
    itens: [...lista.querySelectorAll("li")].filter((li) => vis(li)).map((li) => (li.textContent || "").trim()),
    atual: lista.querySelector('a[aria-current="page"]')?.textContent.trim() ?? null,
    sair: sairNaTela(),
  };
}, TOG);

test("fora da Conta o grupo começa fechado: aria-expanded=false, aria-controls aponta a lista e nada do grupo está à vista", async () => {
  const { ctx, page } = await abrir({ guia: "concluido", rota: "/assinaturas" });
  const e = await estado(page);
  await ctx.close();
  assert.deepEqual(e, { expandido: "false", controla: true, itens: [], atual: null, sair: true });
});

for (const rota of CONTA) {
  test(`deep link para ${rota}: o grupo abre sozinho com a página atual marcada, e o Sair segue à vista`, async () => {
    const { ctx, page } = await abrir({ guia: "concluido", rota });
    const e = await estado(page);
    await ctx.close();
    assert.equal(e.expandido, "true");
    assert.equal(e.itens.length, 5); // as 5 telas; o Sair está fora da lista que recolhe
    assert.equal(e.sair, true);
    assert.ok(e.atual && e.itens.includes(e.atual));
  });
}

test("o clique no subtítulo alterna: abre mostra as 5 telas, fecha esconde de novo (aria-expanded acompanha) e o Sair não se mexe", async () => {
  const { ctx, page } = await abrir({ guia: "concluido", rota: "/assinaturas" });
  const r = [];
  r.push(await estado(page));
  await page.locator(TOG).click();
  r.push(await estado(page));
  await page.locator(TOG).click();
  r.push(await estado(page));
  await ctx.close();
  assert.deepEqual(r.map((e) => [e.expandido, e.itens.length, e.sair]), [["false", 0, true], ["true", 5, true], ["false", 0, true]]);
});

test("teclado: Enter e Espaço alternam; fechado o Tab não cai nos itens da Conta mas cai no Sair; o foco tem anel visível", async () => {
  const { ctx, page } = await abrir({ guia: "concluido", rota: "/assinaturas" });
  await page.locator(TOG).focus();
  await page.keyboard.press("Enter");
  const aberto = await estado(page);
  await page.keyboard.press("Space");
  const fechado = await estado(page);
  await page.keyboard.press("Tab"); // fechado: o Tab seguinte pula as telas da Conta (escondidas) e cai no Sair, que está fora do grupo
  const depois = await page.evaluate(() => document.activeElement.textContent.trim());
  await page.locator(TOG).focus();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Tab"); // aberto: o Tab seguinte é a 1ª tela da Conta
  const depoisAberto = await page.evaluate(() => document.activeElement.textContent.trim());
  await page.locator(TOG).click(); // fecha de novo para o teste do anel abaixo
  await page.locator(TOG).focus();
  await page.keyboard.press("Shift+Tab");
  await page.keyboard.press("Tab");
  const anel = await page.evaluate((tog) => { const b = document.querySelector(tog), cs = getComputedStyle(b); return { foco: document.activeElement === b, anel: cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) >= 2 }; }, TOG);
  await ctx.close();
  assert.equal(aberto.expandido, "true");
  assert.equal(fechado.expandido, "false");
  assert.equal(depois, "Sair");
  assert.equal(depoisAberto, "Perfil");
  assert.deepEqual(anel, { foco: true, anel: true });
});

test("a página atual nunca fica escondida: fechar o grupo dentro de uma tela da Conta deixa só o item dela à vista", async () => {
  const { ctx, page } = await abrir({ guia: "concluido", rota: "/conta/bancos" });
  await page.locator(TOG).click();
  const e = await estado(page);
  await ctx.close();
  assert.deepEqual(e, { expandido: "false", controla: true, itens: ["Bancos conectados"], atual: "Bancos conectados", sair: true });
});

test("entrar numa tela da Conta abre o grupo e sair dela o fecha; o que o clique fez fora da Conta não vira regra", async () => {
  const { ctx, page } = await abrir({ guia: "concluido", rota: "/assinaturas" });
  await page.locator(TOG).click(); // aberto à mão numa tela comum
  await page.evaluate(() => { location.hash = "#/conta/perfil"; });
  await page.waitForFunction(() => document.title.includes("Perfil"));
  const dentro = await estado(page);
  await page.locator('.rail-list a[title="Resumo"]').click();
  await page.waitForFunction(() => document.title.includes("Resumo"));
  const fora = await estado(page);
  await ctx.close();
  assert.equal(dentro.expandido, "true");
  assert.equal(fora.expandido, "false");
});

for (const [width, height] of [[1440, 900], [1024, 768]]) {
  test(`alvo do subtítulo ${width}×${height}: pelo menos 44×44px (no trilho de ícones só a seta aparece, com o nome acessível "Conta")`, async () => {
    const { ctx, page } = await abrir({ guia: "concluido", width, height, rota: "/assinaturas" });
    const r = await page.evaluate((tog) => { const b = document.querySelector(tog), c = b.getBoundingClientRect(); return { h: c.height, w: c.width, nome: b.title }; }, TOG);
    await ctx.close();
    assert.ok(r.h >= 44 && r.w >= 44, `${r.w}×${r.h}`);
    assert.equal(r.nome, "Conta");
  });
}

test("o Menu do celular não recolhe: a folha lista as 5 telas da Conta e o Sair sem botão de grupo", async () => {
  const { ctx, page } = await abrir({ guia: "concluido", width: 375, height: 812, rota: "/assinaturas" });
  await page.locator(".tabbar").getByRole("button", { name: "Menu" }).click();
  const r = await page.evaluate(() => ({
    itens: [...document.querySelectorAll(".menu-telas-conta li")].filter((li) => li.getClientRects().length).length,
    botoesDeGrupo: document.querySelectorAll(".menu-telas [aria-expanded]").length,
  }));
  await ctx.close();
  assert.deepEqual(r, { itens: 6, botoesDeGrupo: 0 });
});

const vai = async (page, hash, titulo) => { await page.evaluate((h) => { location.hash = h; }, hash); await page.waitForFunction((t) => document.title.includes(t), titulo); };

// mutação: o efeito de RailConta.tsx voltar a depender de `emConta` e não de `path`: o que o clique fez numa tela comum atravessa a navegação para outra tela comum
test("aberto à mão numa tela comum, ir a outra tela comum encontra o grupo fechado", async () => {
  const { ctx, page } = await abrir({ guia: "concluido", rota: "/assinaturas" });
  await page.locator(TOG).click();
  const aberto = (await estado(page)).expandido;
  await vai(page, "#/", "Resumo");
  const depois = (await estado(page)).expandido;
  await ctx.close();
  assert.deepEqual([aberto, depois], ["true", "false"]);
});

// mutação: a mesma; fechado à mão em /conta/perfil, o grupo seguia fechado em /conta/bancos
test("fechado à mão numa tela da Conta, ir a outra tela da Conta encontra o grupo aberto; o clique segue alternando na mesma tela", async () => {
  const { ctx, page } = await abrir({ guia: "concluido", rota: "/conta/perfil" });
  await page.locator(TOG).click();
  const fechado = (await estado(page)).expandido;
  await vai(page, "#/conta/bancos", "Bancos conectados");
  const depois = await estado(page);
  await page.locator(TOG).click();
  const alterna = (await estado(page)).expandido;
  await ctx.close();
  assert.deepEqual([fechado, depois.expandido, depois.itens.length, alterna], ["false", "true", 5, "false"]);
});
