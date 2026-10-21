// Consertos do ataque do Tester no PR 1 do chat da IA no /painel (webapp: lib/conversaIa.ts, lib/conversation.ts, parts/AskBar.tsx, parts/PiggyChat.tsx):
// troca de conta × freio, Parar com texto no campo, duplo clique, DELETE /ai/pending pendurado (fila e teto de 15 s), prazo de 60 s e cota cheia.
// Cada grupo foi provado vermelho com uma mutação do conserto que ele cobre, EXCETO a guarda `naConversa` de lib/conversation.ts: ela é redundante
// com o `patch` por id (que já ignora um id fora da conversa), e tirá-la sozinha não deixa teste nenhum vermelho. O stream é de mentira mas é stream
// de verdade: ver _chat_ia.mjs.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { PAROU, PRAZO, SEM_COTA, GENERICO, RESPOSTAS, abrir, botao, campo, encerrar, enviar, evento, fim, iniciar, respostas, trocarConta, ultima } from "./_chat_ia.mjs";

before(iniciar);
after(encerrar);
const delta = (t) => evento({ tipo: "delta", texto: t });
const rotulo = (page) => page.evaluate(() => document.querySelector(".askbar-send")?.getAttribute("aria-label"));
const esperaRotulo = (page, r) => page.waitForFunction((r) => document.querySelector(".askbar-send")?.getAttribute("aria-label") === r, r, { timeout: 3000 });
const rede = (log) => log.filter((l) => !l.startsWith("POST /auth/refresh"));

test("troca de conta A (na lista) -> B (fora): a 1ª pergunta de B vai pelo caminho de hoje, sem DELETE, GET nem POST de IA", async () => {
  const { ctx, page, log, conta, posts, flux } = await abrir();
  await ctx.route("**/api/v2/me", (r) => r.fulfill({ json: { ...RESPOSTAS.me.pro, chat_ia: conta.uid === 1 } })); // o /me de B diz false
  await enviar(page, "primeira de A");
  await flux.espera(1);
  await flux.push(0, evento(fim("ok A")));
  await esperaRotulo(page, "Enviar");
  conta.uid = 2; // troca noutra aba, sem o foco chegar aqui: só a conferência do `ask` percebe
  const antes = rede(log).length;
  await campo(page).fill("pergunta logo depois da troca");
  await campo(page).press("Enter");
  await page.waitForFunction(() => document.querySelectorAll(".chat > .msg-piggy").length === 1 && !document.querySelector(".chat .msg-ia"));
  assert.equal(rede(log).length, antes, "nada de IA para quem não está na lista");
  assert.equal(posts.length, 1, "só o POST de A");
  assert.equal(await page.getByText("primeira de A").count(), 0);
  await page.context().close();
});

test("troca de conta A -> B (também na lista): B faz o próprio DELETE e GET no 1º envio (o 'preparado' de A não vale)", async () => {
  const { page, log, conta, flux } = await abrir();
  await enviar(page, "de A");
  await flux.espera(1);
  await flux.push(0, evento(fim("ok A")));
  await esperaRotulo(page, "Enviar");
  await trocarConta(page, conta, 2);
  await page.waitForFunction(() => !document.body.textContent.includes("ok A"));
  await enviar(page, "de B");
  await flux.espera(2);
  assert.deepEqual(rede(log).slice(3), ["DELETE /ai/pending", "GET /ai/messages", "POST /ai/chat/stream"]);
  await page.context().close();
});

for (const txt of ["", "@Pig oi", "texto comum"]) {
  test(`Parar com '${txt}' no campo só para: não envia o texto`, async () => {
    const { page, flux, posts } = await abrir();
    await enviar(page, "oi");
    await flux.espera(1);
    if (txt) await campo(page).fill(txt);
    await botao(page).click();
    await esperaRotulo(page, "Enviar");
    await page.waitForTimeout(500);
    assert.equal(await flux.abortado(0), true);
    assert.equal(posts.length, 1, "nenhum POST além do primeiro");
    assert.equal(await respostas(page).count(), 1);
    assert.equal(await campo(page).inputValue(), txt, "o texto do campo fica");
    await page.context().close();
  });
}

test("Parar pelo teclado (Espaço no botão) segue parando", async () => {
  const { page, flux } = await abrir();
  await enviar(page, "oi");
  await flux.espera(1);
  await botao(page).focus();
  await page.keyboard.press("Space");
  await esperaRotulo(page, "Enviar");
  assert.equal(await flux.abortado(0), true);
  await ultima(page).getByText(PAROU).first().waitFor();
  await page.context().close();
});

test("duplo clique no Enviar: o 2º clique (detail 2) cai no Parar e é ignorado; o turno segue", async () => {
  const { page, flux, posts } = await abrir();
  await campo(page).fill("quanto gastei?");
  await botao(page).click(); // espera o botão assentar (a barra anima ao abrir)
  await esperaRotulo(page, "Parar resposta");
  const b = await botao(page).boundingBox();
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
  await page.mouse.down({ clickCount: 2 }); // `mouse.click({ clickCount })` não passa o detail; down/up sim (o mouse já está no botão)
  await page.mouse.up({ clickCount: 2 });
  await page.waitForTimeout(500);
  assert.equal(posts.length, 1);
  assert.equal(await flux.abortado(0), false, "o duplo clique não pode parar o turno");
  assert.equal(await rotulo(page), "Parar resposta");
  await page.context().close();
});

test("DELETE /ai/pending pendurado: Parar solta a tela na hora, com a nota e o envio seguinte refaz o preparo", async () => {
  const { ctx, page } = await abrir();
  let deletes = 0;
  await ctx.route("**/ai/pending", () => { deletes++; }); // nunca responde
  await enviar(page, "oi");
  await esperaRotulo(page, "Parar resposta");
  await botao(page).click();
  await esperaRotulo(page, "Enviar");
  await ultima(page).getByText(PAROU).first().waitFor();
  assert.equal(deletes, 1);
  await page.context().close();
});

test("DELETE pendurado: o prazo de 60 s solta a tela com o texto de prazo", async () => {
  const { ctx, page } = await abrir();
  await page.clock.install();
  await ctx.route("**/ai/pending", () => {});
  await enviar(page, "oi");
  await esperaRotulo(page, "Parar resposta");
  await page.clock.fastForward(61000);
  await esperaRotulo(page, "Enviar");
  await ultima(page).getByText(PRAZO).first().waitFor();
  await page.context().close();
});

test("DELETE pendurado: fila única (o envio seguinte espera o mesmo DELETE), teto de 15 s com erro, e depois um DELETE novo", async () => {
  const { ctx, page, posts } = await abrir();
  await page.clock.install();
  let deletes = 0;
  await ctx.route("**/ai/pending", () => { deletes++; });
  await enviar(page, "primeira");
  await esperaRotulo(page, "Parar resposta");
  await botao(page).click();
  await esperaRotulo(page, "Enviar");
  await enviar(page, "segunda");
  await esperaRotulo(page, "Parar resposta");
  await page.waitForTimeout(300);
  assert.equal(deletes, 1, "o 2º turno espera o DELETE em voo, não abre outro");
  await page.clock.fastForward(16000); // o teto de 15 s
  await esperaRotulo(page, "Enviar");
  await ultima(page).getByText(GENERICO).first().waitFor();
  assert.equal(posts.length, 0, "sem DELETE confirmado não há POST");
  await enviar(page, "terceira");
  await page.waitForFunction(() => document.querySelector(".askbar-send")?.getAttribute("aria-label") === "Parar resposta");
  await page.waitForTimeout(300);
  assert.equal(deletes, 2, "passado o teto, o envio seguinte refaz o DELETE");
  await page.context().close();
});

test("troca de conta com o DELETE de A pendurado: B faz o próprio DELETE, não herda o de A", async () => {
  const { ctx, page, conta, flux } = await abrir();
  let deletes = 0;
  await ctx.route("**/ai/pending", (r) => { if (++deletes === 1) return; return r.fulfill({ json: { cancelada: null } }); });
  await enviar(page, "de A");
  await esperaRotulo(page, "Parar resposta");
  await trocarConta(page, conta, 2);
  await esperaRotulo(page, "Enviar");
  await enviar(page, "de B");
  await flux.espera(1);
  assert.equal(deletes, 2);
  await page.context().close();
});

test("prazo de 60 s com o stream mudo: aborta, mostra o texto de prazo e suja a carga", async () => {
  const { page, flux, log } = await abrir();
  await page.clock.install();
  await enviar(page, "oi");
  await flux.espera(1);
  await flux.push(0, delta("começo"));
  await page.clock.fastForward(61000);
  await esperaRotulo(page, "Enviar");
  assert.equal(await flux.abortado(0), true);
  await ultima(page).getByText(PRAZO).first().waitFor();
  await enviar(page, "de novo");
  await flux.espera(2);
  assert.equal(log.filter((l) => l === "DELETE /ai/pending").length, 2, "o prazo suja");
  await page.context().close();
});

test("cota cheia pelo `fim.usage`: Enviar desliga, Enter não cria bolha nem POST, o aviso com Ver planos aparece; @agente segue liberado", async () => {
  const { page, flux, posts } = await abrir();
  await enviar(page, "oi");
  await flux.espera(1);
  await flux.push(0, evento(fim("ok", { usage: { used: 100, limit: 100 } })));
  await esperaRotulo(page, "Enviar");
  const aviso = page.locator(".chat-uso");
  await aviso.getByText(SEM_COTA).waitFor();
  assert.equal(await aviso.getByRole("link", { name: "Ver planos" }).getAttribute("href"), "/precos");
  await campo(page).fill("mais uma");
  await campo(page).press("Enter");
  await page.waitForTimeout(400);
  assert.equal(await botao(page).isDisabled(), true);
  assert.equal(await respostas(page).count(), 1);
  assert.equal(posts.length, 1);
  await campo(page).fill("@Pig oi");
  assert.equal(await botao(page).isDisabled(), false, "o @agente tem cota própria");
  await page.context().close();
});

test("cota cheia pelo 403 do POST: Enviar desliga, sem aviso repetido (a bolha já traz o texto e o link)", async () => {
  const { page, flux, posts } = await abrir();
  await flux.proxima({ status: 403, json: { detail: { error: "pro_required", feature: "ai_chat" } } });
  await enviar(page, "oi");
  await ultima(page).getByText(SEM_COTA).first().waitFor();
  await campo(page).fill("outra");
  await campo(page).press("Enter");
  await page.waitForTimeout(400);
  assert.equal(await botao(page).isDisabled(), true);
  assert.equal(posts.length, 1);
  assert.equal(await respostas(page).count(), 1);
  assert.equal(await page.locator(".chat-uso").count(), 0);
  await page.context().close();
});


test("troca de conta A -> B percebida pelo FOCO com o /me de B ainda em voo: o envio espera o /me de B (fora da lista) e a IA não sai em nome dele", async () => {
  const { ctx, page, conta, posts, flux } = await abrir();
  let segurar = false, soltar;
  await ctx.route("**/api/v2/me", async (r) => {
    const uid = conta.uid;
    if (segurar) await new Promise((ok) => { soltar = ok; });
    return r.fulfill({ json: { ...RESPOSTAS.me.pro, chat_ia: uid === 1 } });
  });
  await enviar(page, "de A");
  await flux.espera(1);
  await flux.push(0, evento(fim("ok A")));
  await esperaRotulo(page, "Enviar");
  segurar = true;
  await trocarConta(page, conta, 2); // o perfil novo chega e o /me de B fica em voo
  await page.waitForFunction(() => !document.body.textContent.includes("ok A"));
  await enviar(page, "pergunta de B");
  await page.waitForTimeout(500);
  assert.equal(posts.length, 1, "com o /me de B em voo, o freio de A não vale");
  soltar();
  await page.waitForFunction(() => document.querySelectorAll(".chat > .msg-piggy").length === 1 && !document.querySelector(".chat .msg-ia"));
  assert.equal(posts.length, 1, "B fora da lista: o caminho de hoje, sem POST de IA");
  await page.context().close();
});

test("sem troca de conta o envio não relê o /me (0 GET extra)", async () => {
  const { ctx, page, flux } = await abrir();
  let gets = 0;
  await ctx.route("**/api/v2/me", (r) => { gets++; return r.fulfill({ json: { ...RESPOSTAS.me.pro, chat_ia: true } }); });
  await enviar(page, "oi");
  await flux.espera(1);
  await flux.push(0, evento(fim("ok")));
  await esperaRotulo(page, "Enviar");
  assert.equal(gets, 0);
  await page.context().close();
});

test("cota cheia renovada noutra aba: ao a aba voltar a ficar visível a cota é relida (403 = continua cheia; 200 com cota livre = o Enviar volta)", async () => {
  const { page, cota, log } = await abrir({ usage: { used: 100, limit: 100 } });
  await enviar(page, "oi");
  await ultima(page).getByText(SEM_COTA).first().waitFor();
  await campo(page).fill("de novo");
  assert.equal(await botao(page).isDisabled(), true);
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange"))); // ainda cheia: o GET responde 403 e a tela continua cheia
  await page.waitForTimeout(400);
  assert.equal(log.filter((l) => l === "GET /ai/messages").length, 2, "o preparo e a releitura do foco");
  assert.equal(await botao(page).isDisabled(), true, "403 na releitura = continua cheia");
  cota.used = 0; // o servidor renovou
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await page.waitForFunction(() => !document.querySelector(".askbar-send")?.disabled);
  assert.equal(log.filter((l) => l === "GET /ai/messages").length, 3, "o preparo e as duas releituras do foco");
  await page.context().close();
});

test("cota NÃO cheia: o foco da aba não lê a cota", async () => {
  const { page, flux, log } = await abrir();
  await enviar(page, "oi");
  await flux.espera(1);
  await flux.push(0, evento(fim("ok")));
  await esperaRotulo(page, "Enviar");
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await page.waitForTimeout(400);
  assert.equal(log.filter((l) => l === "GET /ai/messages").length, 1, "só o do preparo");
  await page.context().close();
});

test("cota cheia de A, a releitura do foco em voo quando a conta vira B: a cota de A não vaza para B (200 cheio: PLANS_V2_ENABLED=0 ou corrida entre o gate e o uso)", async () => {
  const { ctx, page, conta } = await abrir({ usage: { used: 100, limit: 100 } });
  await enviar(page, "de A");
  await ultima(page).getByText(SEM_COTA).first().waitFor();
  let soltar;
  await ctx.route("**/ai/messages?limit=1", async (r) => { await new Promise((ok) => { soltar = ok; }); return r.fulfill({ json: { messages: [], usage: { used: 100, limit: 100 } } }); });
  conta.uid = 2; // o evento do navegador borbulha: o listener do document lê a cota e o do window (react-query) relê o perfil, que vira B e zera o estado
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange", { bubbles: true })));
  await page.waitForFunction(() => !document.body.textContent.includes("de A"));
  soltar(); // a leitura de A chega depois, ainda "cheia". Com o v2 ligado sai 403; 200 com 100/100 sai com PLANS_V2_ENABLED=0 ou na corrida gate × uso. A época segura os dois
  await page.waitForTimeout(400);
  await campo(page).fill("de B");
  assert.equal(await botao(page).isDisabled(), false, "B não herda a cota cheia de A");
  await page.context().close();
});

test("o DELETE de A que termina tarde não solta o DELETE de B: o envio seguinte de B reaproveita o dele", async () => {
  const { ctx, page, conta } = await abrir();
  const presos = [];
  await ctx.route("**/ai/pending", (r) => { presos.push(r); });
  await enviar(page, "de A");
  await esperaRotulo(page, "Parar resposta");
  await trocarConta(page, conta, 2);
  await esperaRotulo(page, "Enviar");
  await enviar(page, "de B");
  await esperaRotulo(page, "Parar resposta");
  while (presos.length < 2) await page.waitForTimeout(50);
  await presos[0].fulfill({ json: { cancelada: null } }); // o de A termina tarde
  await page.waitForTimeout(300);
  await botao(page).click(); // Parar B
  await esperaRotulo(page, "Enviar");
  await enviar(page, "de B de novo");
  await esperaRotulo(page, "Parar resposta");
  await page.waitForTimeout(300);
  assert.equal(presos.length, 2, "o DELETE de B segue em voo: não abre um 3º");
  await page.context().close();
});
