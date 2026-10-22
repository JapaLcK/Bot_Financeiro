// O chat do /painel quando o freio `chat_ia` está ligado (webapp: lib/conversaIa.ts, lib/sse.ts, parts/PiggyIa.tsx): tudo vai à IA por
// `POST /ai/chat/stream`, o texto chega em pedaços, parar aborta, o DELETE /ai/pending sai uma vez por carga da página (e depois de
// erro/parada), a cota cheia vira texto com "Ver planos", e a troca de conta no meio do stream não deixa texto de A para B.
// O stream é de mentira mas é stream de verdade (ReadableStream controlado pelo teste): ver _chat_ia.mjs.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { PAROU, PREPARANDO, SEM_CARD, SEM_COTA, GENERICO, abrir, botao, enviar, encerrar, evento, fim, iniciar, respostas, texto, trocarConta, ultima } from "./_chat_ia.mjs";

before(iniciar);
after(encerrar);
const SHOTS = process.env.PAINEL_SHOTS;
const shot = async (page, nome, w) => { if (SHOTS) { mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: join(SHOTS, `${nome}-${w}.png`) }); } };
const delta = (t) => evento({ tipo: "delta", texto: t });
const esperaTexto = (page, parte) => page.waitForFunction((p) => document.querySelector(".chat > .msg-piggy:last-child .msg-ia")?.textContent.includes(p), parte);
const rede = (log) => log.filter((l) => !l.startsWith("POST /auth/refresh"));

test("freio desligado: nada de IA (sem POST, sem DELETE, sem GET)", async () => {
  const { page, log, erros } = await abrir({ chatIa: false });
  await enviar(page, "quanto gastei no mercado?");
  await page.waitForFunction(() => document.querySelectorAll(".chat > .msg-piggy").length >= 1);
  await page.waitForTimeout(400);
  assert.deepEqual(rede(log), []);
  assert.equal(await page.locator(".msg-ia").count(), 0);
  assert.deepEqual(erros, []);
  await page.context().close();
});

test("freio ligado: DELETE, GET da cota e POST, nessa ordem; corpo sem uid; CSRF; texto em pedaços; fim troca o texto inteiro", async () => {
  const { page, log, posts, deletes, flux, erros } = await abrir();
  await enviar(page, "quanto gastei no mercado?");
  await flux.espera(1);
  assert.deepEqual(rede(log), ["DELETE /ai/pending", "GET /ai/messages", "POST /ai/chat/stream"]);
  assert.deepEqual(posts[0].corpo, { message: "quanto gastei no mercado?", cards: [] }); // D1, e nenhum uid no corpo
  assert.equal(posts[0].csrf, "tok-123");
  assert.equal(deletes[0].csrf, "tok-123");
  assert.equal(await page.getByText(PREPARANDO).first().isVisible(), true);
  assert.equal(await botao(page).getAttribute("aria-label"), "Parar resposta");
  await flux.push(0, `: ping\n\n${evento({ tipo: "passo", texto: "Olhando seus gastos…" })}`);
  await ultima(page).getByText("Olhando seus gastos…").first().waitFor();
  // um caractere de 2 bytes ("ã" = C3 A3) e uma linha SSE cortados no meio entre dois pushes
  const bytes = [...new TextEncoder().encode(delta("Você gastou R$ 10 e n"))];
  const corte = bytes.indexOf(0xc3) + 1;
  await flux.push(0, bytes.slice(0, corte));
  await flux.push(0, bytes.slice(corte));
  await esperaTexto(page, "Você gastou R$ 10 e n");
  assert.equal(await page.getByText("Olhando seus gastos…").count(), 0, "o passo sai com o 1º texto");
  await flux.push(0, delta("ão passou do teto.") + evento(fim("Você gastou R$ 10 e não passou do teto.")));
  await page.waitForFunction(() => document.querySelector(".askbar-send")?.getAttribute("aria-label") === "Enviar");
  assert.equal(await texto(page), "Você gastou R$ 10 e não passou do teto.");
  assert.equal(await page.locator(".chat > .msg-piggy button", { hasText: /copiar/i }).count(), 0, "Q12: sem Copiar (o 👍👎 é de tests/frontend/dashboard_v2_chat_ia_feedback.test.mjs)");
  // 2º envio na mesma carga: sem novo DELETE nem GET
  await enviar(page, "e no mês passado?");
  await flux.espera(2);
  assert.deepEqual(rede(log).slice(3), ["POST /ai/chat/stream"]);
  await flux.push(1, evento(fim("Foi R$ 20.")));
  await page.waitForFunction(() => document.querySelector(".askbar-send")?.getAttribute("aria-label") === "Enviar");
  assert.equal(await respostas(page).count(), 2);
  assert.deepEqual(erros, []);
  await page.context().close();
});

test("D3: com o freio ligado até a pergunta de metas e de investido vai à IA", async () => {
  const { page, posts, flux } = await abrir();
  await enviar(page, "como estão minhas metas?");
  await flux.espera(1);
  await flux.push(0, evento(fim("Sem metas.")));
  await page.waitForFunction(() => document.querySelector(".askbar-send")?.getAttribute("aria-label") === "Enviar");
  await enviar(page, "quanto tenho investido?");
  await flux.espera(2);
  assert.deepEqual(posts.map((p) => p.corpo.message), ["como estão minhas metas?", "quanto tenho investido?"]);
  await page.context().close();
});

test("texto do modelo é só texto: HTML não vira elemento, *negrito* e lista viram <b> e <li>", async () => {
  const { page, flux, erros } = await abrir();
  await enviar(page, "oi");
  await flux.espera(1);
  const corpo = `<img src=x onerror="window.__xss=1"> <script>window.__xss=1</script> *forte* e **duplo**\n\n- um\n- dois`;
  await flux.push(0, evento(fim(corpo)));
  await page.waitForFunction(() => document.querySelector(".askbar-send")?.getAttribute("aria-label") === "Enviar");
  const ia = ultima(page).locator(".msg-ia");
  assert.equal(await ia.locator("img, script").count(), 0);
  assert.equal(await page.evaluate(() => window.__xss), undefined);
  assert.match(await ia.innerText(), /<img src=x/);
  assert.equal(await ia.locator("b").innerText(), "forte");
  assert.match(await ia.innerText(), /\*\*duplo\*\*/); // markdown duplo fica como veio
  assert.deepEqual(await ia.locator("li").allInnerTexts(), ["um", "dois"]);
  assert.deepEqual(erros, []);
  await page.context().close();
});

test("bloco vira marcador neutro, sem número", async () => {
  const { page, flux } = await abrir();
  await enviar(page, "oi");
  await flux.espera(1);
  await flux.push(0, evento({ tipo: "bloco", bloco: { card: "gastos", abas: [{ rotulo: "Mês", total: 999 }] } }, fim("Veja.")));
  await page.waitForFunction(() => document.querySelector(".askbar-send")?.getAttribute("aria-label") === "Enviar");
  assert.equal(await ultima(page).getByText(SEM_CARD).count(), 1);
  assert.equal(await ultima(page).getByText("999").count(), 0);
  await page.context().close();
});

test("Parar: aborta o fetch, mostra o texto parcial e a nota; o envio seguinte refaz o DELETE antes do POST", async () => {
  const { page, log, flux, deletes } = await abrir();
  await enviar(page, "me conta tudo");
  await flux.espera(1);
  await flux.push(0, delta("Começando a contar"));
  await esperaTexto(page, "Começando a contar");
  await botao(page).click();
  await ultima(page).getByText(PAROU).first().waitFor();
  assert.equal(await flux.abortado(0), true);
  assert.match(await ultima(page).innerText(), /Começando a contar/);
  assert.equal(await botao(page).getAttribute("aria-label"), "Enviar");
  const antes = deletes.length;
  assert.equal(antes, 1, "parar não manda DELETE");
  await enviar(page, "de novo");
  await flux.espera(2);
  const cauda = rede(log).slice(3);
  assert.deepEqual(cauda, ["DELETE /ai/pending", "GET /ai/messages", "POST /ai/chat/stream"]);
  await page.context().close();
});

test("erro do servidor: texto genérico, sem detalhe; o envio seguinte refaz o DELETE", async () => {
  const { page, log, flux } = await abrir();
  await enviar(page, "oi");
  await flux.espera(1);
  await flux.push(0, evento({ tipo: "erro", detail: "Traceback (most recent call last) secreto" }));
  await ultima(page).getByText(GENERICO).first().waitFor();
  assert.equal(await page.getByText("Traceback").count(), 0);
  await enviar(page, "outra");
  await flux.espera(2);
  assert.deepEqual(rede(log).slice(3), ["DELETE /ai/pending", "GET /ai/messages", "POST /ai/chat/stream"]);
  await page.context().close();
});

test("stream que fecha sem `fim` é falha e suja a carga (próximo envio refaz o DELETE)", async () => {
  const { page, log, flux } = await abrir();
  await enviar(page, "oi");
  await flux.espera(1);
  await flux.push(0, delta("pela metade"));
  await flux.fechar(0);
  await ultima(page).getByText(GENERICO).first().waitFor();
  await enviar(page, "outra");
  await flux.espera(2);
  assert.equal(rede(log).filter((l) => l === "DELETE /ai/pending").length, 2);
  await page.context().close();
});

test("403 pro_required: texto da cota com link Ver planos, sem 'falhou'", async () => {
  const { page, flux } = await abrir();
  await flux.proxima({ status: 403, json: { detail: { error: "pro_required", feature: "ai_chat" } } });
  await enviar(page, "oi");
  await ultima(page).getByText(SEM_COTA).first().waitFor();
  const link = ultima(page).getByRole("link", { name: "Ver planos" });
  assert.equal(await link.getAttribute("href"), "/precos");
  assert.equal(await page.getByText(GENERICO).count(), 0);
  await page.context().close();
});

test("cota cheia no GET (403 pro_required, o contrato real): texto da cota sem POST; chips ficam desabilitados", async () => {
  const { page, posts } = await abrir({ usage: { used: 100, limit: 100 } });
  await enviar(page, "oi");
  await ultima(page).getByText(SEM_COTA).first().waitFor();
  assert.equal(posts.length, 0);
  assert.equal(await page.locator(".chat-follow .chip:not([disabled])").count(), 0);
  await page.context().close();
});

test("troca de conta no meio do stream: nada de A aparece para B e nenhum DELETE sai depois", async () => {
  const { page, log, conta, flux } = await abrir();
  await enviar(page, "segredo de A?");
  await flux.espera(1);
  await flux.push(0, delta("SEGREDO-DE-A parte 1"));
  await esperaTexto(page, "SEGREDO-DE-A parte 1");
  await flux.ignoraAbort(); // o servidor continua mandando mesmo depois do abort: só a guarda do cliente segura
  const antes = rede(log).length;
  await trocarConta(page, conta, 2);
  await page.waitForFunction(() => !document.body.textContent.includes("SEGREDO-DE-A"));
  await flux.push(0, delta(" SEGREDO-DE-A parte 2") + evento(fim("SEGREDO-DE-A final")));
  await page.waitForTimeout(500);
  assert.equal(await page.evaluate(() => document.body.textContent.includes("SEGREDO-DE-A")), false);
  assert.equal(await flux.abortado(0), true);
  assert.equal(rede(log).length, antes, "nenhum DELETE/GET/POST depois da troca");
  assert.equal(await botao(page).getAttribute("aria-label"), "Enviar");
  await page.context().close();
});

test("troca de conta: o `fim` atrasado de A não mexe no turno nem na cota de B", async () => {
  const { page, conta, flux } = await abrir();
  await enviar(page, "pergunta de A");
  await flux.espera(1);
  await flux.ignoraAbort();
  await trocarConta(page, conta, 2);
  await page.waitForFunction(() => !document.body.textContent.includes("pergunta de A"));
  await enviar(page, "pergunta de B");
  await flux.espera(2);
  await flux.push(0, evento(fim("resposta velha de A", { usage: { used: 95, limit: 100 } }))); // chega depois, ainda no stream de A
  await page.waitForTimeout(400);
  assert.equal(await page.locator(".chat-uso").count(), 0, "a cota de A (95 de 100) não vaza para B");
  assert.equal(await botao(page).getAttribute("aria-label"), "Parar resposta", "o turno de B segue em voo");
  assert.equal(await page.getByText("resposta velha de A").count(), 0);
  await flux.push(1, evento(fim("resposta de B")));
  await page.waitForFunction(() => document.querySelector(".askbar-send")?.getAttribute("aria-label") === "Enviar");
  assert.equal(await texto(page), "resposta de B");
  await page.context().close();
});

for (const w of [1440, 390]) {
  test(`geometria em ${w}px: sem overflow horizontal, botão Parar e linha de uso dentro da barra`, async () => {
    const { page, flux } = await abrir({ width: w, usage: { used: 90, limit: 100 } });
    await enviar(page, "uma pergunta bem longa para medir como a barra se comporta quando o texto passa de uma linha");
    await flux.espera(1);
    await shot(page, "ia-preparando", w);
    const parar = await botao(page).boundingBox(), barra = await page.locator(".askbar").boundingBox();
    assert.ok(parar.width >= 32 && parar.height >= 32, JSON.stringify(parar));
    assert.ok(parar.x >= barra.x && parar.x + parar.width <= barra.x + barra.width + 1, `${JSON.stringify({ parar, barra })}`);
    await flux.push(0, delta(Array.from({ length: 12 }, (_, i) => `Parágrafo ${i + 1} com um texto razoável sobre os seus gastos do mês.`).join("\n\n") + " https://exemplo.com/" + "x".repeat(120)));
    await esperaTexto(page, "Parágrafo 12");
    await shot(page, "ia-pronto", w);
    const dim = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth, chat: document.querySelector(".chat").scrollWidth, chatCw: document.querySelector(".chat").clientWidth }));
    assert.ok(dim.sw <= dim.cw && dim.chat <= dim.chatCw, JSON.stringify(dim));
    await flux.push(0, evento(fim("ok")));
    await page.context().close();
  });
}
