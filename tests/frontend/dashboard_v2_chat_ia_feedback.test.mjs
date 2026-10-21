// O 👍👎 da última resposta da IA no chat do /painel (webapp: lib/conversation.ts `avaliarIa`, parts/PiggyIa.tsx `Avaliar`): `POST /ai/messages/{id}/feedback`,
// atualização otimista com reversão, um POST por bolha, motivos do 👎 e o campo do "Outro". O servidor do feedback é de mentira (`rotaFeedback`, _chat_ia.mjs).
// Cada grupo foi provado vermelho com uma mutação do código que ele cobre (a tabela está no relato do PR, não aqui).
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { alerta, ate, clica, enviar, encerrar, esperaRotulo, evento, fim, gostei, iniciar, iniciarConversa, marcado, motivo, naoGostei, respostas, responder, trocarConta, ultima } from "./_chat_ia.mjs";

before(iniciar);
after(encerrar);
const SHOTS = process.env.PAINEL_SHOTS;
const shot = async (page, nome, w) => { if (SHOTS) { mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: join(SHOTS, `${nome}-${w}.png`) }); } };
test("1. 👍: POST em /ai/messages/{message_id}/feedback com CSRF e corpo {nota:1}; fica pressionado", async () => {
  const c = await iniciarConversa();
  await responder(c, 0, 7);
  await clica(gostei(c.page), c.f);
  assert.equal(c.f.reqs[0].url, "/ai/messages/7/feedback");
  assert.equal(c.f.reqs[0].metodo, "POST");
  assert.equal(c.f.reqs[0].csrf, "tok-123");
  assert.deepEqual(c.f.reqs[0].corpo, { nota: 1 });
  assert.equal(await marcado(gostei(c.page)), "true");
  assert.equal(await marcado(naoGostei(c.page)), "false");
  assert.equal(await alerta(c.page).count(), 0, "4+: 200 não avisa nada");
  await c.page.context().close();
});

test("2. 👍 e 👍 de novo desfaz: corpo {nota:null}", async () => {
  const c = await iniciarConversa();
  await responder(c, 0);
  await clica(gostei(c.page), c.f);
  await clica(gostei(c.page), c.f);
  assert.deepEqual(c.f.reqs.map((r) => r.corpo), [{ nota: 1 }, { nota: null }]);
  assert.equal(await marcado(gostei(c.page)), "false");
  await c.page.context().close();
});

test("3. 👍 depois 👎: corpo {nota:-1}, só o 👎 pressionado, e as pílulas só aparecem com o 👎", async () => {
  const c = await iniciarConversa();
  await responder(c, 0);
  await clica(gostei(c.page), c.f);
  assert.equal(await c.page.getByText("O que houve?").count(), 0, "com 👍 não há motivos");
  await clica(naoGostei(c.page), c.f);
  assert.deepEqual(c.f.reqs[1].corpo, { nota: -1 });
  assert.equal(await marcado(gostei(c.page)), "false");
  assert.equal(await marcado(naoGostei(c.page)), "true");
  await c.page.getByText("O que houve?").waitFor();
  assert.deepEqual(await c.page.locator(".msg-motivos .chip").allInnerTexts(), ["Número errado", "Não entendeu", "Faltou card", "Outro"]);
  await c.page.context().close();
});

for (const [nome, resp] of [["404", { status: 404, json: { detail: "x" } }], ["429", { status: 429, json: { detail: "x" } }], ["500", { status: 500, json: {} }], ["rede", { abortar: true }]]) {
  test(`4. falha (${nome}): volta ao estado anterior e avisa com role=alert`, async () => {
    const c = await iniciarConversa();
    await responder(c, 0);
    c.f.fila.push(resp);
    await clica(gostei(c.page), c.f);
    await alerta(c.page).waitFor({ timeout: 3000 });
    assert.equal(await marcado(gostei(c.page)), "false", "sem nota, como antes");
    // de 👍 gravado para 👎 que falha: volta para 👍
    await clica(gostei(c.page), c.f); // 200
    assert.equal(await alerta(c.page).count(), 0, "o aviso some na próxima tentativa");
    assert.equal(await marcado(gostei(c.page)), "true");
    c.f.fila.push(resp);
    await clica(naoGostei(c.page), c.f);
    await alerta(c.page).waitFor({ timeout: 3000 });
    assert.equal(await marcado(gostei(c.page)), "true", "voltou para o 👍");
    assert.equal(await marcado(naoGostei(c.page)), "false");
    await c.page.context().close();
  });
}

test("5. duplo clique com a resposta segurada: nunca dois POSTs ao mesmo tempo; o 2º toque sai depois do 1º", async () => {
  for (const modo of ["dblclick", "dois clicks no mesmo tick"]) {
    const c = await iniciarConversa();
    await responder(c, 0);
    c.f.fila.push({ segurar: true });
    if (modo === "dblclick") await gostei(c.page).dblclick();
    else await c.page.evaluate(() => { const b = document.querySelector('[aria-label="Gostei da resposta"]'); b.click(); b.click(); });
    await ate(() => c.f.reqs.length > 0);
    await c.page.waitForTimeout(300);
    assert.equal(c.f.reqs.length, 1, `${modo}: um só em voo`);
    c.f.solta({ status: 200, json: { ok: true } });
    await ate(() => c.f.reqs.length === 2);
    // dblclick: o 2º clique já vê o 👍 otimista e desmarca. Dois clicks sem render no meio leem o mesmo estado: o mesmo corpo duas vezes (idempotente).
    const esperado = modo === "dblclick" ? [{ nota: 1 }, { nota: null }] : [{ nota: 1 }, { nota: 1 }];
    assert.deepEqual(c.f.reqs.map((r) => r.corpo), esperado, modo);
    await c.page.waitForTimeout(300);
    assert.equal(await marcado(gostei(c.page)), modo === "dblclick" ? "false" : "true");
    assert.equal(c.f.reqs.length, 2);
    await c.page.context().close();
  }
});

test("6. troca de conta durante o POST: a falha de A não chega à bolha de B", async () => {
  const c = await iniciarConversa();
  const { page, conta, flux, f } = c;
  await responder(c, 0, 7, "ok A");
  f.fila.push({ segurar: true });
  await gostei(page).click();
  await ate(() => f.reqs.length === 1);
  await trocarConta(page, conta, 2);
  await page.waitForFunction(() => !document.body.textContent.includes("ok A"));
  await enviar(page, "de B");
  await flux.espera(2);
  await flux.push(1, evento(fim("ok B", { message_id: 9 })));
  await esperaRotulo(page);
  f.solta({ status: 500, json: {} });
  await page.waitForTimeout(400);
  assert.equal(await alerta(page).count(), 0, "o aviso de A não aparece em B");
  assert.equal(await marcado(gostei(page)), "false");
  assert.equal(f.reqs.length, 1, "nenhum POST novo");
  await clica(gostei(page), f); // positivo: B avalia a própria resposta
  assert.equal(f.reqs[1].url, "/ai/messages/9/feedback");
  assert.equal(await marcado(gostei(page)), "true");
  await page.context().close();
});

test("7. sem message_id (null) não há botões; com message_id há os dois", async () => {
  const c = await iniciarConversa();
  await responder(c, 0, null);
  assert.equal(await gostei(c.page).count(), 0);
  assert.equal(await naoGostei(c.page).count(), 0);
  await responder(c, 1, 8);
  assert.equal(await gostei(c.page).count(), 1, "7+: com message_id aparece");
  assert.equal(await naoGostei(c.page).count(), 1);
  await c.page.context().close();
});

test("7b. erro do turno (sem fim) não tem botões", async () => {
  const c = await iniciarConversa();
  await enviar(c.page, "oi");
  await c.flux.espera(1);
  await c.flux.push(0, evento({ tipo: "erro" }));
  await esperaRotulo(c.page);
  assert.equal(await respostas(c.page).count(), 1);
  assert.equal(await gostei(c.page).count(), 0);
  await c.page.context().close();
});

test("8. só na última resposta: a anterior perde os botões quando chega a pergunta seguinte", async () => {
  const c = await iniciarConversa();
  await responder(c, 0, 7);
  assert.equal(await respostas(c.page).first().getByRole("button", { name: "Gostei da resposta", exact: true }).count(), 1);
  await responder(c, 1, 8);
  assert.equal(await respostas(c.page).count(), 2);
  assert.equal(await respostas(c.page).first().getByRole("button", { name: "Gostei da resposta", exact: true }).count(), 0, "a 1ª sem botões");
  assert.equal(await ultima(c.page).getByRole("button", { name: "Gostei da resposta", exact: true }).count(), 1, "a 2ª com");
  await clica(gostei(c.page), c.f);
  assert.equal(c.f.reqs[0].url, "/ai/messages/8/feedback");
  await c.page.context().close();
});

test("9. motivo: {nota:-1, motivo}; tocar de novo desmarca ({nota:-1}); trocar para 👍 zera o motivo", async () => {
  const c = await iniciarConversa();
  await responder(c, 0);
  await clica(naoGostei(c.page), c.f);
  await clica(motivo(c.page, "Número errado"), c.f);
  assert.deepEqual(c.f.reqs[1].corpo, { nota: -1, motivo: "numero_errado" });
  assert.equal(await marcado(motivo(c.page, "Número errado")), "true");
  await clica(motivo(c.page, "Número errado"), c.f);
  assert.deepEqual(c.f.reqs[2].corpo, { nota: -1 });
  assert.equal(await marcado(motivo(c.page, "Número errado")), "false");
  await clica(motivo(c.page, "Faltou card"), c.f);
  assert.deepEqual(c.f.reqs[3].corpo, { nota: -1, motivo: "faltou_card" });
  await clica(gostei(c.page), c.f);
  assert.deepEqual(c.f.reqs[4].corpo, { nota: 1 });
  await clica(naoGostei(c.page), c.f);
  assert.equal(await c.page.locator(".msg-motivos .chip[aria-pressed=true]").count(), 0, "o motivo antigo não volta");
  await c.page.context().close();
});

test("10. motivo que falha (500): voltam a nota E o motivo anteriores", async () => {
  const c = await iniciarConversa();
  await responder(c, 0);
  await clica(naoGostei(c.page), c.f);
  await clica(motivo(c.page, "Faltou card"), c.f);
  c.f.fila.push({ status: 500, json: {} });
  await clica(motivo(c.page, "Não entendeu"), c.f);
  await alerta(c.page).waitFor({ timeout: 3000 });
  assert.equal(await marcado(naoGostei(c.page)), "true");
  assert.equal(await marcado(motivo(c.page, "Faltou card")), "true", "o motivo anterior volta");
  assert.equal(await marcado(motivo(c.page, "Não entendeu")), "false");
  await c.page.context().close();
});

test("11. Outro: abre o campo com foco; corta por code point (499 a + 🐷🐷 -> 500); Enviar só com texto; sucesso fecha e agradece; falha mantém o texto", async () => {
  const c = await iniciarConversa();
  const { page, f } = c;
  await responder(c, 0);
  await clica(naoGostei(page), f);
  await clica(motivo(page, "Outro"), f);
  assert.deepEqual(f.reqs[1].corpo, { nota: -1, motivo: "outro" });
  const campo = page.getByLabel("Comentário sobre a resposta"), enviarBtn = page.locator(".msg-comentario .btn");
  await campo.waitFor();
  assert.equal(await campo.evaluate((e) => e === document.activeElement), true, "o campo recebe o foco");
  assert.equal(await enviarBtn.isDisabled(), true, "vazio");
  await campo.fill("   ");
  assert.equal(await enviarBtn.isDisabled(), true, "só espaços");
  await campo.fill("a".repeat(499) + "🐷🐷");
  const v = await campo.inputValue();
  assert.equal(Array.from(v).length, 500);
  assert.equal(v, "a".repeat(499) + "🐷", "cortou por code point: o 🐷 inteiro entra, o 2º não");
  await campo.fill("  conte  ");
  f.fila.push({ status: 500, json: {} });
  await clica(enviarBtn, f);
  assert.deepEqual(f.reqs[2].corpo, { nota: -1, motivo: "outro", texto: "conte" }, "texto aparado");
  await alerta(page).waitFor({ timeout: 3000 });
  assert.equal(await campo.inputValue(), "  conte  ", "falhou: o texto fica no campo");
  await campo.fill("a".repeat(499) + "🐷🐷");
  await clica(enviarBtn, f);
  const texto = f.reqs[3].corpo.texto;
  assert.equal(texto, "a".repeat(499) + "🐷");
  assert.equal(Array.from(texto).length, 500);
  assert.equal(await campo.count(), 0, "enviou: o campo fecha");
  await page.getByRole("status").filter({ hasText: "Obrigado pelo retorno" }).waitFor();
  assert.equal(await alerta(page).count(), 0);
  assert.equal(await marcado(motivo(page, "Outro")), "true");
  await page.context().close();
});

test("12. avaliar não relê a resposta: o anunciador do leitor de tela não muda", async () => {
  const c = await iniciarConversa();
  await responder(c, 0);
  await c.page.waitForTimeout(300);
  await c.page.evaluate(() => {
    const anuncio = document.querySelector("ol.chat").previousElementSibling; // o <p class="sr-only" role="status"> do PiggyChat (há outros no /painel)
    if (anuncio?.getAttribute("role") !== "status") throw new Error("o anunciador não é o irmão anterior do .chat");
    window.__mut = 0;
    new MutationObserver(() => { window.__mut++; }).observe(anuncio, { childList: true, subtree: true, characterData: true });
  });
  await clica(gostei(c.page), c.f);
  await clica(naoGostei(c.page), c.f);
  await clica(motivo(c.page, "Outro"), c.f);
  await c.page.waitForTimeout(300);
  assert.equal(await c.page.evaluate(() => window.__mut), 0);
  await c.page.context().close();
});

for (const w of [1440, 390]) {
  test(`13. ${w}px: 👍👎 e pílulas com 44x44 no mínimo, sem overflow horizontal`, async () => {
    const c = await iniciarConversa({ width: w });
    const { page, f } = c;
    await responder(c, 0, 7, "Você gastou R$ 10 no mercado e ficou dentro do ritmo do mês.");
    await clica(gostei(page), f);
    await shot(page, "feedback-gostei", w);
    await clica(naoGostei(page), f);
    await clica(motivo(page, "Outro"), f);
    await page.getByLabel("Comentário sobre a resposta").waitFor();
    await shot(page, "feedback-outro", w);
    const caixa = async (loc) => (await loc.boundingBox());
    const bolha = await caixa(ultima(page));
    const alvos = [gostei(page), naoGostei(page), ...(await page.locator(".msg-motivos .chip").all()), page.locator(".msg-comentario .btn")];
    const medidas = [];
    for (const a of alvos) {
      const b = await caixa(a);
      medidas.push(`${Math.round(b.width)}x${Math.round(b.height)}`);
      assert.ok(b.width >= 44 && b.height >= 44, `alvo menor que 44x44: ${Math.round(b.width)}x${Math.round(b.height)}`);
      assert.ok(b.x >= bolha.x - 11 && b.x + b.width <= bolha.x + bolha.width + 1, "fora da largura da bolha");
    }
    const campo = await caixa(page.getByLabel("Comentário sobre a resposta"));
    assert.ok(campo.x + campo.width <= bolha.x + bolha.width + 1, "o campo estoura a bolha");
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true, "overflow horizontal da página");
    console.log(`# ${w}px alvos: ${medidas.join(" ")}; bolha ${Math.round(bolha.width)}px; campo ${Math.round(campo.width)}px`);
    await page.context().close();
  });
}
