// O chat do /painel com a IA, o que dá errado ou chega torto (webapp: lib/sse.ts, lib/conversaIa.ts, lib/conversation.ts): eventos do stream
// (reinicio, tipo desconhecido, JSON quebrado, bloco fora de 1..3), o parser (última linha sem `\n`, `\r\n`), 401/403/429/500 do POST e a cota cheia
// pelo contrato real (o GET /ai/messages responde 403 `pro_required`, não 200 com 100/100). O stream é de mentira mas é stream de verdade: ver _chat_ia.mjs.
// Cada grupo foi provado vermelho com uma mutação do código que ele cobre (a tabela está no relato do PR, não aqui).
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { GENERICO, RESPOSTAS, SEM_CARD, SEM_COTA, SESSAO, abrir, botao, campo, encerrar, enviar, evento, fim, iniciar, respostas, texto, trocarConta, ultima } from "./_chat_ia.mjs";

before(iniciar);
after(encerrar);
const delta = (t) => evento({ tipo: "delta", texto: t });
const rotulo = (page) => page.evaluate(() => document.querySelector(".askbar-send")?.getAttribute("aria-label"));
const esperaRotulo = (page, r) => page.waitForFunction((r) => document.querySelector(".askbar-send")?.getAttribute("aria-label") === r, r, { timeout: 3000 });
const tem = (page, parte) => page.waitForFunction((p) => document.body.textContent.includes(p), parte, { timeout: 3000 });
const rede = (log) => log.filter((l) => !l.startsWith("POST /auth/refresh"));

test("reinicio limpa o texto parcial: o que veio antes sai e o que vem depois fica", async () => {
  const { page, flux } = await abrir();
  await enviar(page, "oi");
  await flux.espera(1);
  await flux.push(0, delta("lixo parcial"));
  await tem(page, "lixo parcial");
  await flux.push(0, evento({ tipo: "reinicio" }) + delta("novo"));
  await page.waitForFunction(() => !document.body.textContent.includes("lixo parcial") && document.body.textContent.includes("novo"), null, { timeout: 3000 });
  await page.context().close();
});

test("tipo desconhecido, JSON quebrado e bloco com abas fora de 1..3 são descartados e o stream segue", async () => {
  const { page, flux } = await abrir();
  await enviar(page, "oi");
  await flux.espera(1);
  const bloco = (abas) => evento({ tipo: "bloco", bloco: { card: "gastos", abas } });
  await flux.push(0, delta("antes") + evento({ tipo: "desconhecido", x: 1 }) + "data: {quebrado\n\n" + bloco([]) + bloco([1, 2, 3, 4]));
  await flux.push(0, delta(" e depois"));
  await tem(page, "antes e depois");
  assert.equal(await ultima(page).getByText(SEM_CARD).count(), 0, "bloco inválido não vira marcador");
  assert.equal(await rotulo(page), "Parar resposta", "nada disso derruba o turno");
  await flux.push(0, bloco([1, 2, 3]));
  await ultima(page).getByText(SEM_CARD).first().waitFor({ timeout: 3000 }); // controle positivo: 3 abas é válido
  await page.context().close();
});

test("última linha sem `\\n` final (proxy que corta o `\\n\\n`) ainda é lida", async () => {
  const { page, flux } = await abrir();
  await enviar(page, "oi");
  await flux.espera(1);
  await flux.push(0, `data: ${JSON.stringify(fim("final sem quebra"))}`);
  await flux.fechar(0);
  await esperaRotulo(page, "Enviar");
  assert.equal(await texto(page), "final sem quebra");
  await page.context().close();
});

test("`\\r\\n` como separador, inclusive no meio de um pedaço", async () => {
  const { page, flux } = await abrir();
  await enviar(page, "oi");
  await flux.espera(1);
  await flux.push(0, `data: ${JSON.stringify({ tipo: "delta", texto: "crlf" })}\r\n\r\n`);
  await tem(page, "crlf");
  await flux.push(0, `data: ${JSON.stringify(fim("pronto"))}\r`);
  await flux.push(0, "\n\r\n");
  await esperaRotulo(page, "Enviar");
  assert.equal(await texto(page), "pronto");
  await page.context().close();
});

test("401 renovado: o POST é repetido UMA vez depois do /auth/refresh, numa bolha só, e Parar aborta o repetido", async () => {
  const { page, flux, log, posts } = await abrir();
  await flux.proxima({ status: 401, json: { detail: "Token expirado" }, headers: { "www-authenticate": "Bearer" } });
  await enviar(page, "oi");
  await flux.espera(2);
  assert.deepEqual(log, ["DELETE /ai/pending", "GET /ai/messages", "POST /ai/chat/stream", "POST /auth/refresh", "POST /ai/chat/stream"]);
  assert.equal(posts.length, 2);
  assert.equal(await respostas(page).count(), 1);
  await flux.push(1, delta("resp"));
  await tem(page, "resp");
  await botao(page).click();
  await esperaRotulo(page, "Enviar");
  assert.equal(await flux.abortado(1), true, "o POST repetido ouviu o Parar");
  await page.context().close();
});

// Cada falha do POST: o texto certo, a tela solta e o envio seguinte refaz o DELETE antes do POST (a carga ficou suja).
for (const [nome, resp, esperado, antes] of [
  ["401 sem renovação", { status: 401, json: { detail: "Token expirado" }, headers: { "www-authenticate": "Bearer" } }, SESSAO, (ctx) => ctx.route("**/auth/refresh", (r) => r.fulfill({ status: 401, json: { detail: "x" } }))],
  ["403 CSRF", { status: 403, json: { detail: "Token CSRF inválido" } }, SESSAO],
  ["403 password_required", { status: 403, json: { detail: { error: "password_required" } } }, "Crie sua senha"],
  ["429", { status: 429, json: { error: "Rate limit exceeded: 12 per 1 minute" } }, "Muitas mensagens"],
  ["500", { status: 500, json: { detail: "boom" } }, GENERICO],
]) {
  test(`${nome} no POST: texto certo, tela solta, e o envio seguinte faz DELETE antes do POST`, async () => {
    const { ctx, page, flux, log } = await abrir();
    await antes?.(ctx);
    await flux.proxima(resp);
    await enviar(page, "oi");
    await ultima(page).getByText(esperado).first().waitFor({ timeout: 3000 });
    await esperaRotulo(page, "Enviar");
    await enviar(page, "outra");
    await flux.espera(2);
    assert.deepEqual(rede(log).slice(3), ["DELETE /ai/pending", "GET /ai/messages", "POST /ai/chat/stream"]);
    await page.context().close();
  });
}

test("cota cheia pelo contrato real: o GET do preparo responde 403 pro_required, e isso é a cota (texto, sem POST, Enviar desligado)", async () => {
  const { page, posts } = await abrir({ usage: { used: 100, limit: 100 } });
  await enviar(page, "oi");
  await ultima(page).getByText(SEM_COTA).first().waitFor({ timeout: 3000 });
  assert.equal(posts.length, 0);
  assert.equal(await page.getByText(GENERICO).count(), 0, "não é 'falhou'");
  await campo(page).fill("outra");
  assert.equal(await botao(page).isDisabled(), true);
  await page.context().close();
});

test("GET com 200 e used == limit (PLANS_V2_ENABLED=0, ou outra aba gastando a última entre o gate e o uso) também vira a cota, sem POST", async () => {
  const { ctx, page, posts } = await abrir();
  await ctx.route("**/ai/messages?limit=1", (r) => r.fulfill({ json: { messages: [], usage: { used: 100, limit: 100 } } }));
  await enviar(page, "oi");
  await ultima(page).getByText(SEM_COTA).first().waitFor({ timeout: 3000 });
  await page.waitForTimeout(300);
  assert.equal(posts.length, 0);
  await page.context().close();
});

test("freio DESLIGADO + troca de conta: a pergunta entra sem esperar a releitura do /me (o cache diz desligado, e esse é o lado seguro)", async () => {
  const { ctx, page, conta, log, posts } = await abrir({ chatIa: false });
  let pedidos = 0, soltar;
  await ctx.route("**/api/v2/me", async (r) => {
    pedidos++;
    await new Promise((ok) => { soltar = ok; }); // o /me da conta nova fica em voo
    return r.fulfill({ json: { ...RESPOSTAS.me.pro, chat_ia: false } });
  });
  await trocarConta(page, conta, 2);
  while (!pedidos) await page.waitForTimeout(50); // controle: a troca de conta de fato releu o /me
  await enviar(page, "quanto gastei?");
  await page.waitForFunction(() => document.querySelectorAll(".chat > .msg-piggy").length === 1, null, { timeout: 3000 }); // sem soltar o /me
  assert.equal(await page.locator(".msg-ia").count(), 0, "o caminho de hoje, não a IA");
  assert.deepEqual(rede(log), []);
  assert.equal(posts.length, 0);
  soltar?.();
  await page.context().close();
});

// A trava de A (cota cheia) não prende B: a pergunta barrada fora do /piggy (faixa do Resumo) abre o /piggy, que monta relendo o perfil, e a troca solta a trava.
test("cota cheia de A, a sessão vira B sem o foco chegar: o clique barrado na faixa abre o /piggy, que percebe a troca, e o envio seguinte de B sai", async () => {
  const { page, log, conta, cota, posts, flux } = await abrir({ usage: { used: 100, limit: 100 } });
  await enviar(page, "de A");
  await ultima(page).getByText(SEM_COTA).first().waitFor();
  await page.evaluate(() => { location.hash = "#/"; });
  const faixa = page.locator(".piggy-band");
  await faixa.waitFor();
  conta.uid = 2; cota.used = 0; // B tem cota livre; sem visibilitychange: só a montagem do /piggy percebe
  const perfis = page.waitForResponse((r) => r.url().includes("/auth/dashboard-profile"));
  await faixa.click();
  await perfis;
  await page.waitForFunction(() => !document.body.textContent.includes("de A"), null, { timeout: 3000 }); // a conta nova limpa a conversa de A
  assert.equal(posts.length, 0, "a pergunta barrada não saiu (e a de A parou no GET 403)");
  await enviar(page, "de B");
  await flux.espera(1);
  assert.deepEqual(rede(log).slice(-3), ["DELETE /ai/pending", "GET /ai/messages", "POST /ai/chat/stream"]);
  assert.equal(posts.length, 1);
  await page.context().close();
});

test("com a cota livre o @agente segue indo ao agente (nada de IA do Piggy)", async () => {
  const { ctx, page, posts } = await abrir();
  const chats = [];
  await ctx.route(/\/agents\/1(\/|$)/, (r) => {
    const m = new URL(r.request().url()).pathname.match(/^\/agents\/1\/(\w+)\/chat$/);
    if (!m) return r.fulfill({ json: { catalog: [{ kind: "xerife", nome: "Xerife", disponivel: true, status: "active", energy_cost: 2 }], can_chat: true, can_activate: true, energy_enabled: false, energy_budget: 99, energy_used: 0 } });
    chats.push(m[1]);
    return r.fulfill({ json: { reply: "Oi do agente.", context: "c", redirects: [] } });
  });
  await enviar(page, "@Xerife oi");
  await ultima(page).getByText("Oi do agente.").waitFor({ timeout: 3000 });
  assert.deepEqual(chats, ["xerife"]);
  assert.equal(posts.length, 0, "o Piggy não foi chamado");
  await page.context().close();
});
