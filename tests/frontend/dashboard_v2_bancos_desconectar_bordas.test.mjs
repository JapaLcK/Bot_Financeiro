// Bordas do desconectar banco (#/conta/bancos, conta/Bancos.tsx): foco, conta trocada com o diálogo aberto, releitura em erro, 404 e falha velha.
// Cada teste tem uma mutação que o reprova (a lista está no relato do PR); o caminho feliz e o resto da tabela: dashboard_v2_bancos_desconectar.test.mjs.
import { test } from "node:test";
import assert from "node:assert/strict";
import { abrir, navegador } from "./_guia.mjs";
import { ate } from "./_conta_perfil.mjs";
import { JSON_ERRO } from "./_conta_seguranca.mjs";
import { PADRAO, con, servidorBancos } from "./_bancos.mjs";
import { DEL, semReticencias, esperaCards, refoco, dlg, confirmar, manter, card, aviso, alerta, foco, noFoco, pedir, tiraDoSnapshot, lidosDepois, tela } from "./_bancos_desconectar.mjs";

const browser = navegador();
void browser;

// A conta passa a uid 8 quando o teste chama `troca()` (o foco da aba relê o perfil); /open-finance/8 tem um banco só (C6 Bank).
async function telaTrocavel(del) {
  let uid = 7;
  const srv = servidorBancos({ bancos: "padrao", perfil: () => ({ display_name: "Ana", email: "ana@exemplo.com", user_id: uid, whatsapp_linked: false }), del });
  const { ctx, page } = await abrir({ guia: "concluido", rota: "/conta/bancos", antes: async (c) => {
    await srv.antes(c);
    await c.route((u) => u.pathname === "/open-finance/8", (r) => r.fulfill({ json: { ok: true, connections: [con(21, "C6 Bank")] } }));
    await c.route((u) => u.pathname === "/open-finance/8/limite", (r) => r.fulfill({ json: { ok: true, of_banks_max: 3, em_uso: 1, pode_adicionar: true, code: null, message: null } }));
  } });
  page.setDefaultTimeout(15000);
  await esperaCards(page, 3);
  await semReticencias(page);
  await page.getByText("2 de 3 bancos").waitFor();
  return { ctx, page, srv, troca: async () => { uid = 8; await refoco(page); await card(page, "C6 Bank").waitFor(); } };
}

test("B1 o card some com o diálogo aberto (outra aba) e 'Manter banco' leva o foco ao título, não ao body", async () => {
  const { ctx, page, srv } = await tela();
  await pedir(page, "Nubank");
  tiraDoSnapshot(srv, 11);
  await refoco(page);
  await esperaCards(page, 2);
  await manter(page).click();
  await dlg(page).waitFor({ state: "detached" });
  await noFoco(page, "page-title");
  await ctx.close();
  assert.deepEqual(srv.dels, []);
});

test("B2 a conta troca com o diálogo aberto e a rede cai no DELETE: o aviso do 403 aparece, o foco vai ao título e o snapshot do uid velho não é relido", async () => {
  const { ctx, page, srv, troca } = await telaTrocavel((r) => r.abort());
  await pedir(page, "Nubank");
  await troca();
  await confirmar(page).click();
  await alerta(page).filter({ hasText: "Recarregue a página" }).waitFor();
  await noFoco(page, "page-title");
  const depois = lidosDepois(srv).filter((x) => x === "/open-finance/7");
  await ctx.close();
  assert.deepEqual(depois, []);
  assert.deepEqual(srv.dels, [DEL(11)]);
});

test("C1 depois do 200 o /api/v2/contas é relido (o 'N bancos' do menu)", async () => {
  const { ctx, page } = await tela();
  let apagou = false, contas = 0;
  page.on("request", (q) => { if (apagou && new URL(q.url()).pathname === "/api/v2/contas") contas++; });
  await pedir(page, "Nubank");
  apagou = true;
  await confirmar(page).click();
  await esperaCards(page, 2);
  await ate(page, () => contas > 0, "GET /api/v2/contas depois do DELETE");
  await ctx.close();
});

test("C2 o uid muda com o diálogo aberto: o DELETE sai com o uid do clique (7) e o 403 segue o ramo do 403", async () => {
  const { ctx, page, srv, troca } = await telaTrocavel((r) => r.fulfill({ status: 403, json: { detail: "Acesso negado para este usuário." } }));
  await pedir(page, "Nubank");
  await troca();
  await confirmar(page).click();
  await alerta(page).filter({ hasText: "Recarregue a página" }).waitFor();
  const r = { aviso: await aviso(page).innerText(), depois: lidosDepois(srv).filter((x) => x === "/open-finance/7") };
  await ctx.close();
  assert.deepEqual(r, { aviso: "", depois: [] });
  assert.deepEqual(srv.dels, [DEL(11)]);
});

test("C3 resposta perdida e a releitura do snapshot em erro: a página mostra o próprio erro de carga, o foco vai ao título, 1 DELETE; o 'Tentar de novo' não traz alerta de desconectar", async () => {
  let apagou = false, ok = false;
  const { ctx, page, srv } = await tela({ snap: (r) => (apagou && !ok ? r.fulfill({ status: 500, json: { detail: "x" } }) : r.fulfill({ json: { ok: true, connections: PADRAO() } })), del: (r) => { apagou = true; return r.abort(); } });
  await pedir(page, "Nubank");
  await confirmar(page).click();
  await page.getByText("Não deu para carregar seus bancos.").waitFor();
  await dlg(page).waitFor({ state: "detached" });
  await noFoco(page, "page-title");
  const erro = await alerta(page).allInnerTexts();
  ok = true;
  await page.getByRole("button", { name: "Tentar de novo" }).click();
  await esperaCards(page, 3);
  const depois = await alerta(page).count();
  await ctx.close();
  assert.deepEqual(erro.map((t) => /Não deu para carregar seus bancos\./.test(t) && !/desconectar/.test(t)), [true]);
  assert.equal(depois, 0);
  assert.deepEqual(srv.dels, [DEL(11)]);
});

test("C4 404 com o banco ainda no snapshot: 'Não deu para desconectar Nubank. Tente de novo.', nada de 'desconectado'", async () => {
  const { ctx, page, srv } = await tela({ del: JSON_ERRO(404, { code: "OF_CONNECTION_NOT_FOUND", message: "Não achamos esse banco nas suas conexões." }) });
  await pedir(page, "Nubank");
  await confirmar(page).click();
  await alerta(page).waitFor();
  const r = { alerta: await alerta(page).innerText(), aviso: await aviso(page).innerText(), cards: await page.locator(".banco-card").count() };
  await ctx.close();
  assert.deepEqual(r, { alerta: "Não deu para desconectar Nubank. Tente de novo.", aviso: "", cards: 3 });
  assert.deepEqual(srv.dels, [DEL(11)]);
});

test("C5 403: o foco vai ao título (não ao botão)", async () => {
  const { ctx, page } = await tela({ del: JSON_ERRO(403, "CSRF token ausente.") });
  await pedir(page, "Nubank");
  await confirmar(page).click();
  await alerta(page).filter({ hasText: "Recarregue a página" }).waitFor();
  await noFoco(page, "page-title");
  await ctx.close();
});

test("C6 uma falha velha (Nubank) some quando se abre o diálogo de OUTRO banco (Inter)", async () => {
  const { ctx, page } = await tela({ del: (r) => r.abort() });
  await pedir(page, "Nubank");
  await confirmar(page).click();
  await alerta(page).waitFor();
  await dlg(page).waitFor({ state: "detached" });
  await pedir(page, "Inter");
  const r = await alerta(page).count();
  await ctx.close();
  assert.equal(r, 0);
});
