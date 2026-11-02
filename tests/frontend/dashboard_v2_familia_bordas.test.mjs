/**
 * /painel#/conta/familia, as bordas de estado local (webapp/src/dashboard/familia/*):
 *   · o foco nunca cai no BODY depois de uma escrita que tira da tela o botão clicado (criar, gerar, revogar, remover, desfazer);
 *   · uma escrita e um erro por tela: o 402 de duas escritas seguidas é UMA mensagem; gerar com o remover em voo = 1 pedido só;
 *   · trocar de conta com a página aberta apaga o estado local (convite com token, prévia com nonce).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { ERRO, F, abrirFamilia, titular } from "./_familia.mjs";

const browser = navegador();
void browser;

const com = async (opts, fn) => {
  const t = await abrirFamilia(opts);
  try { await fn(t); assert.deepEqual(t.erros, [], "pageerror"); } finally { await t.ctx.close(); }
};
const MARIA = { ref: "9f1c2a40-7b1e-4a53-8d3a-0c5d6e7f8a91", nome: "Maria Aparecida Costa", status: "ativo" };
const foco = (page) => page.evaluate(() => { const a = document.activeElement; return a === document.body ? "BODY" : a.id || a.dataset.testid || a.tagName; });
const noTitulo = async (page) => { await page.waitForFunction(() => document.activeElement?.id === "page-title"); assert.equal(await foco(page), "page-title"); };

test("foco: criar família e desfazer a família levam o foco ao título, nunca ao body", async () => {
  await com({ visao: titular({ membros: [MARIA] }) }, async ({ page, bt, dlg }) => {
    await bt("Desfazer família").click();
    await dlg().getByRole("button", { name: "Desfazer", exact: true }).click();
    await bt("Criar família").waitFor();
    await noTitulo(page);
    await bt("Criar família").click();
    await bt("Gerar link de convite").waitFor();
    await noTitulo(page);
  });
});

test("foco: gerar leva ao link novo; revogar e remover levam ao título", async () => {
  await com({ visao: titular({ membros: [MARIA] }) }, async ({ page, bt, dlg }) => {
    await bt("Gerar link de convite").click();
    await page.waitForFunction(() => document.activeElement?.dataset.testid === "fam-link");
    await bt("Revogar").click();
    await bt("Gerar link de convite").waitFor();
    await noTitulo(page);
    await page.getByRole("button", { name: "Remover" }).click();
    await dlg().getByRole("button", { name: "Remover", exact: true }).click();
    await page.locator(".fam-lista b", { hasText: "Maria" }).waitFor({ state: "detached" });
    await noTitulo(page);
  });
});

test("foco: cancelar o diálogo devolve o foco a quem o abriu (o título não rouba)", async () => {
  await com({ visao: titular({ membros: [MARIA] }) }, async ({ page, dlg }) => {
    const remover = page.getByRole("button", { name: "Remover" });
    await remover.click();
    await dlg().getByRole("button", { name: "Cancelar" }).click();
    await page.waitForFunction(() => document.activeElement?.textContent === "Remover");
    assert.equal(await page.evaluate(() => document.activeElement.textContent), "Remover");
  });
});

test("uma escrita e um erro por tela: 402 em gerar e depois em remover = uma mensagem só", async () => {
  await com({ visao: titular({ membros: [MARIA] }) }, async ({ s, page, bt, dlg, secao }) => {
    s.convidar = ERRO(402, "family_coverage_required");
    s.remover = ERRO(402, "family_coverage_required");
    await bt("Gerar link de convite").click();
    await secao.getByText("Seu plano não inclui família.").waitFor();
    await page.getByRole("button", { name: "Remover" }).click();
    await dlg().getByRole("button", { name: "Remover", exact: true }).click();
    await page.getByRole("dialog").waitFor({ state: "detached" });
    assert.equal(await secao.getByText("Seu plano não inclui família.").count(), 1);
    assert.equal(await secao.getByRole("alert").count(), 1);
  });
});

test("uma escrita por vez na tela: gerar enquanto o remover voa = 1 pedido só", async () => {
  await com({ visao: titular({ membros: [MARIA] }) }, async ({ s, page, dlg, bt, escritas }) => {
    s.remover = async () => { await new Promise((ok) => setTimeout(ok, 600)); };
    await page.getByRole("button", { name: "Remover" }).click();
    await dlg().getByRole("button", { name: "Remover", exact: true }).click();
    await dlg().getByRole("button", { name: "Removendo…" }).waitFor();
    await page.evaluate(() => [...document.querySelectorAll("button")].find((b) => b.textContent === "Gerar link de convite").click()); // o diálogo deixa a página inerte: clique programático
    await page.locator(".fam-lista b", { hasText: "Maria" }).waitFor({ state: "detached" });
    assert.deepEqual(escritas().map((q) => q.metodo + q.caminho.replace(/\/membros\/.*/, "/membros")), ["DELETE/membros"]);
    await bt("Gerar link de convite").waitFor(); // e a trava soltou: agora gera
    await bt("Gerar link de convite").click();
    await page.getByTestId("fam-link").waitFor();
    assert.equal(escritas().length, 2);
  });
});

test("trocar de conta com a página aberta apaga o convite (token) e a prévia (nonce) da conta anterior", async () => {
  let uid = 7;
  await com({ visao: titular({ membros: [MARIA] }), antes: (ctx) => ctx.route((u) => u.pathname === "/auth/dashboard-profile", (r) => r.fulfill({ json: { display_name: "Ana", email: "a@exemplo.com", user_id: uid, whatsapp_linked: false } })) }, async ({ page, bt, secao, s }) => {
    await bt("Gerar link de convite").click();
    await secao.getByText("tok-1").waitFor();
    uid = 8;
    await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange"))); // o foco da aba: o React Query relê o perfil
    await bt("Gerar link de convite").waitFor(); // o convite da conta 7 saiu do estado
    assert.equal(await page.getByText("tok-1").count(), 0);
    // o diálogo de desfazer, com o nonce da conta anterior, também some
    s.nonce = "nonce-da-7";
    await bt("Desfazer família").click();
    await page.getByRole("dialog").waitFor();
    uid = 9;
    await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
    await page.getByRole("dialog").waitFor({ state: "detached" });
  });
});

test("trocar de conta com a página aberta: a releitura lenta não mostra o membro nem o Remover da conta anterior", async () => {
  let uid = 7;
  await com({ visao: titular({ membros: [MARIA] }), antes: (ctx) => ctx.route((u) => u.pathname === "/auth/dashboard-profile", (r) => r.fulfill({ json: { display_name: "Ana", email: "a@exemplo.com", user_id: uid, whatsapp_linked: false } })) }, async ({ page, secao, s }) => {
    await page.getByText("Maria").first().waitFor();
    let soltar; s.hold = new Promise((r) => (soltar = r)); // o GET /familia da conta 8 demora
    s.visao = null;
    uid = 8;
    await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
    await page.waitForFunction(() => !document.body.innerText.includes("Maria")); // o dado sai assim que a troca é vista
    await page.waitForTimeout(300);
    assert.equal(await page.getByText("Maria").count(), 0);
    assert.equal(await page.getByRole("button", { name: "Remover" }).count(), 0);
    soltar();
    await secao.getByRole("button", { name: "Criar família" }).waitFor();
  });
});

test("um erro de ação some quando a ação seguinte dá certo", async () => {
  await com({ visao: titular({ membros: [MARIA] }) }, async ({ s, bt, secao }) => {
    s.convidar = ERRO(402, "family_coverage_required");
    await bt("Gerar link de convite").click();
    await secao.getByText("Seu plano não inclui família.").waitFor();
    s.convidar = null;
    await bt("Gerar link de convite").click();
    await secao.getByText("tok-1").waitFor();
    assert.equal(await secao.getByRole("alert").count(), 0);
  });
});

// Estados que só o `key` por uid em Familia.tsx limpa (o resetQueries do main.tsx já desmonta Titular/Convite, não estes dois: `falha` do useEscrita e `saindo`).
const trocaDeConta = { antes: (uid) => (ctx) => ctx.route((u) => u.pathname === "/auth/dashboard-profile", (r) => r.fulfill({ json: { display_name: "Ana", email: "a@exemplo.com", user_id: uid.v, whatsapp_linked: false } })) };
const trocar = async (page, uid, novo) => { uid.v = novo; await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange"))); };

test("trocar de conta: o erro de ação da conta anterior some", async () => {
  const uid = { v: 7 };
  await com({ visao: F.membro, antes: trocaDeConta.antes(uid) }, async ({ s, page, bt, dlg, secao }) => {
    s.sair = ERRO(409, "owner_cannot_leave");
    await bt("Sair da família").click();
    await dlg().getByRole("button", { name: "Sair", exact: true }).click();
    await secao.getByRole("alert").waitFor();
    await trocar(page, uid, 8);
    await secao.getByRole("alert").waitFor({ state: "detached" });
  });
});

test("trocar de conta: o diálogo Sair da família aberto não reaparece para a conta nova", async () => {
  const uid = { v: 7 };
  await com({ visao: F.membro, antes: trocaDeConta.antes(uid) }, async ({ page, bt, dlg }) => {
    await bt("Sair da família").click();
    await dlg().waitFor();
    await trocar(page, uid, 8);
    await dlg().waitFor({ state: "detached" });
  });
});
