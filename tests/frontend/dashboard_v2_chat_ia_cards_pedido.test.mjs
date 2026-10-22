// A ação de PEDIDO dos cards da IA no chat do /painel: "Montar reserva" e "Criar meta" do card `metas` mandam uma frase AO CHAT, como se o usuário a tivesse
// digitado (webapp: parts/cardIa/metas.tsx -> lib/conversation.ts `ask({ text, dono })`). Isolamento: o card tem dono (a conta da resposta que o trouxe) e `ask` lê o
// perfil ANTES; se a sessão já é de outra conta, não sai bolha nem POST. P1 a P6 do plano. Cada grupo foi provado vermelho tirando a guarda que ele cobre.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { RESPOSTAS, GENERICO, abrir, blocoDe, encerrar, enviar, evento, fim, iniciar, respostas, trocarConta, ultima } from "./_chat_ia.mjs";

before(iniciar);
after(encerrar);
const METAS_VAZIO = blocoDe("metas", { metas: RESPOSTAS.metas.vazio });
const pronto = (page) => page.waitForFunction(() => document.querySelector(".askbar-send")?.getAttribute("aria-label") === "Enviar", null, { timeout: 5000 });
const RESERVA = "Quero montar minha reserva de emergência", META = "Quero criar uma meta";

/** O /painel com UMA resposta que traz o card `metas` vazio (as duas linhas de pedido). `antes(c)`: rotas do teste, postas antes de recarregar a página. */
async function comCard(opts = {}, antes) {
  const c = await abrir(opts);
  if (antes) { await antes(c); await c.page.reload(); await c.page.locator("#askbar-input").waitFor(); }
  await enviar(c.page, "como estão minhas metas?");
  await c.flux.espera(1);
  await c.flux.push(0, evento(METAS_VAZIO, fim("Você ainda não tem metas.")));
  await ultima(c.page).locator(".card-ia").waitFor();
  await pronto(c.page);
  const reserva = c.page.getByRole("button", { name: /^Montar reserva de emergência/ }), meta = c.page.getByRole("button", { name: "Criar meta", exact: true });
  const bolhas = () => c.page.locator(".chat > .msg-user").allInnerTexts();
  return { ...c, reserva, meta, bolhas };
}

test("P1 positivo: cada linha manda UM POST com a frase do app, sem card no corpo, e a bolha do usuário entra", async () => {
  const a = await comCard();
  await a.reserva.click();
  await a.flux.espera(2);
  assert.deepEqual(a.posts[1].corpo, { message: RESERVA, cards: [] });
  assert.equal(a.posts.length, 2);
  assert.deepEqual((await a.bolhas()).slice(-1), [RESERVA]);
  await a.page.context().close();
  const b = await comCard();
  await b.meta.click();
  await b.flux.espera(2);
  assert.deepEqual(b.posts[1].corpo, { message: META, cards: [] });
  assert.deepEqual((await b.bolhas()).slice(-1), [META]);
  await b.page.context().close();
});

test("P2 a conta virou OUTRA em silêncio (outra aba, sem evento) antes do clique: nenhum POST, nenhuma bolha, e a conversa de A some", async () => {
  const a = await comCard();
  a.conta.uid = 2;
  await a.reserva.click();
  await a.page.waitForTimeout(600);
  assert.equal(a.posts.length, 1, "só o POST da pergunta original");
  assert.equal(await respostas(a.page).count(), 0, "a conversa de A foi apagada");
  assert.ok(!(await a.page.locator("body").innerText()).includes(RESERVA), "a frase do card não entrou na conversa de B");
  await a.page.context().close();
});

test("P3 a conta vira B ENQUANTO o perfil é lido para o clique: nenhum POST nem bolha (a conferência é do clique, não do card)", async () => {
  const a = await comCard();
  let solta;
  await a.ctx.route("**/auth/dashboard-profile", async (r) => { await new Promise((ok) => { solta = ok; }); return r.fulfill({ json: { user_id: a.conta.uid, display_name: null, email: null, whatsapp_linked: true } }); });
  await a.meta.click();
  await a.page.waitForFunction(() => true); // deixa o clique chegar ao `ask`
  for (let i = 0; i < 80 && !solta; i++) await a.page.waitForTimeout(25);
  assert.ok(solta, "o perfil ficou preso");
  a.conta.uid = 2;
  solta();
  await a.page.waitForTimeout(600);
  assert.equal(a.posts.length, 1);
  assert.ok(!(await a.page.locator("body").innerText()).includes(META));
  assert.equal(await respostas(a.page).count(), 0);
  await a.page.context().close();
});

test("P3b o perfil NÃO pôde ser lido para o clique (403): a conta não foi conferida, então nenhum POST nem bolha", async () => {
  const a = await comCard();
  await a.ctx.route("**/auth/dashboard-profile", (r) => r.fulfill({ status: 403, json: { detail: "forbidden" } }));
  await a.meta.click();
  await a.page.waitForTimeout(600);
  assert.equal(a.posts.length, 1);
  assert.ok(!(await a.bolhas()).includes(META));
  await a.page.context().close();
});

test("P4 o stream do pedido falha (500): vira o texto de erro na conversa e a linha volta a funcionar", async () => {
  const a = await comCard();
  await a.flux.proxima({ status: 500, json: { error: { code: "internal_error", message: "x" } } });
  await a.reserva.click();
  await a.page.getByText(GENERICO).waitFor();
  await pronto(a.page);
  assert.equal(await a.reserva.getAttribute("aria-disabled"), "false");
  await a.reserva.click();
  await a.flux.espera(3);
  assert.equal(a.posts.length, 3, "a nova tentativa sai");
  await a.page.context().close();
});

test("P5 duplo clique: UM POST só", async () => {
  const a = await comCard();
  await a.reserva.dblclick();
  await a.flux.espera(2);
  await a.page.waitForTimeout(400);
  assert.equal(a.posts.length, 2);
  assert.equal((await a.bolhas()).filter((t) => t === RESERVA).length, 1);
  await a.page.context().close();
});

test("P6 turno em voo e cota cheia: as linhas de TODOS os cards ficam aria-disabled (o foco fica no botão) e o clique não envia", async () => {
  const a = await abrir();
  const { page, flux } = a;
  for (const [i, q] of ["metas 1", "metas 2"].entries()) {
    await enviar(page, q); await flux.espera(i + 1);
    await flux.push(i, evento(METAS_VAZIO, fim("ok")));
    await pronto(page);
  }
  const todas = page.getByRole("button", { name: "Criar meta", exact: true });
  assert.equal(await todas.count(), 2);
  await enviar(page, "terceira"); await flux.espera(3); // turno em voo: sem `fim`
  for (const i of [0, 1]) assert.equal(await todas.nth(i).getAttribute("aria-disabled"), "true", `card ${i} com o turno em voo`);
  await todas.first().click({ force: true }); // force: o Playwright trata aria-disabled como "não habilitado" e esperaria para sempre
  assert.equal(await page.evaluate(() => document.activeElement?.getAttribute("aria-label")), "Criar meta", "o foco fica no botão");
  await page.waitForTimeout(300);
  assert.equal(a.posts.length, 3);
  await flux.push(2, evento(fim("ok", { usage: { used: 100, limit: 100 } })));
  await pronto(page);
  for (const i of [0, 1]) assert.equal(await todas.nth(i).getAttribute("aria-disabled"), "true", `card ${i} com a cota cheia`);
  await todas.last().click({ force: true });
  await page.waitForTimeout(300);
  assert.equal(a.posts.length, 3, "cota cheia: nada sai");
  await page.context().close();
});

test("P3c a linha fica aria-disabled enquanto o perfil do clique está sendo lido (a conferência em voo segura o 2º toque)", async () => {
  const a = await comCard();
  const soltas = [];
  await a.ctx.route("**/auth/dashboard-profile", async (r) => { await new Promise((ok) => soltas.push(ok)); return r.fulfill({ json: { user_id: a.conta.uid, display_name: null, email: null, whatsapp_linked: true } }); });
  assert.equal(await a.meta.getAttribute("aria-disabled"), "false", "antes do clique a linha está aberta");
  await a.meta.click();
  for (let i = 0; i < 80 && soltas.length === 0; i++) await a.page.waitForTimeout(25);
  assert.ok(soltas.length > 0, "o perfil ficou preso");
  assert.equal(await a.meta.getAttribute("aria-disabled"), "true");
  soltas.forEach((f) => f());
  await a.flux.espera(2);
  await a.page.context().close();
});

test("P7 a conta vira B durante a RELEITURA do /me (2º await do `ask`): o pedido do card de A não entra na conversa de B (nenhum POST, nenhuma bolha)", async () => {
  const t = { segura: false, presos: [], soltaSse: null };
  const a = await comCard({}, async (c) => {
    await c.ctx.route("**/api/v2/eventos", async (r) => { await new Promise((ok) => { t.soltaSse = ok; }); return r.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body: 'retry: 600000\ndata: {"recurso":"open_finance"}\n\n' }); });
    await c.ctx.route("**/api/v2/me", async (r) => { if (t.segura) await new Promise((ok) => t.presos.push(ok)); return r.fulfill({ json: { ...RESPOSTAS.me.pro, chat_ia: true } }); });
  });
  for (let i = 0; i < 80 && !t.soltaSse; i++) await a.page.waitForTimeout(25);
  t.segura = true;
  t.soltaSse(); // o aviso invalida o /me, que fica em voo (preso): `velho()` é verdadeiro
  for (let i = 0; i < 80 && t.presos.length === 0; i++) await a.page.waitForTimeout(25);
  assert.ok(t.presos.length > 0, "o /me ficou preso");
  await a.page.getByRole("button", { name: "Criar meta", exact: true }).click(); // A clica: o perfil confere (A), e o `ask` espera o /me
  await a.page.waitForTimeout(400);
  await trocarConta(a.page, a.conta, 2); // a sessão vira B noutra aba: o foco lê o perfil e a conversa de A some
  await a.page.waitForTimeout(400);
  t.segura = false; t.presos.forEach((f) => f()); // o /me chega
  await a.page.waitForTimeout(1500);
  assert.equal(a.posts.length, 1, "só o POST da pergunta original de A");
  assert.ok(!(await a.page.locator("body").innerText()).includes(META));
  assert.equal(await respostas(a.page).count(), 0);
  await a.page.context().close();
});
