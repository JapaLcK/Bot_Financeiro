// O 👍👎 com o POST da mesma bolha em voo (webapp: lib/conversation.ts `avaliarIa`): o toque novo não se perde, fica guardado e sai quando o POST termina
// (só o último vale); a falha volta ao último estado que o servidor confirmou; o POST tem prazo de 15 s; Enviar do "Outro" devolve o foco à pílula.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { alerta, ate, clica, enviar, encerrar, evento, fim, gostei, iniciar, iniciarConversa, marcado, motivo, naoGostei, responder, trocarConta, esperaRotulo } from "./_chat_ia.mjs";

before(iniciar);
after(encerrar);
const campo = (page) => page.getByLabel("Comentário sobre a resposta");
const corpos = (f) => f.reqs.map((r) => r.corpo);

test("A. pílula tocada com o POST do 👎 em voo: aparece marcada na hora e sai quando o POST termina", async () => {
  const c = await iniciarConversa();
  await responder(c, 0);
  c.f.fila.push({ segurar: true });
  await naoGostei(c.page).click();
  await ate(() => c.f.reqs.length === 1);
  await motivo(c.page, "Número errado").click();
  assert.equal(await marcado(motivo(c.page, "Número errado")), "true", "otimista: marcada na hora");
  await c.page.waitForTimeout(200);
  assert.equal(c.f.reqs.length, 1, "um POST por vez: o toque espera");
  c.f.solta({ status: 200, json: { ok: true } });
  await ate(() => c.f.reqs.length === 2);
  assert.deepEqual(corpos(c.f), [{ nota: -1 }, { nota: -1, motivo: "numero_errado" }]);
  await c.page.waitForTimeout(300);
  assert.equal(await marcado(motivo(c.page, "Número errado")), "true");
  assert.equal(await alerta(c.page).count(), 0);
  await c.page.context().close();
});

test("A2. Enviar do 'Outro' com o POST do 'Outro' em voo: o texto sai depois, o campo fecha e agradece", async () => {
  const c = await iniciarConversa();
  await responder(c, 0);
  await clica(naoGostei(c.page), c.f);
  c.f.fila.push({ segurar: true });
  await motivo(c.page, "Outro").click();
  await ate(() => c.f.reqs.length === 2);
  await campo(c.page).fill("meu texto");
  await c.page.locator(".msg-comentario .btn").click();
  await c.page.waitForTimeout(200);
  assert.equal(c.f.reqs.length, 2, "o Enviar espera o POST em voo");
  assert.equal(await campo(c.page).inputValue(), "meu texto", "o campo segue até gravar");
  c.f.solta({ status: 200, json: { ok: true } });
  await ate(() => c.f.reqs.length === 3);
  assert.deepEqual(c.f.reqs[2].corpo, { nota: -1, motivo: "outro", texto: "meu texto" });
  await c.page.getByRole("status").filter({ hasText: "Obrigado pelo retorno" }).waitFor();
  assert.equal(await campo(c.page).count(), 0);
  await c.page.context().close();
});

test("A3. só o último toque guardado vale (3 toques em voo -> 2 POSTs)", async () => {
  const c = await iniciarConversa();
  await responder(c, 0);
  await clica(naoGostei(c.page), c.f);
  c.f.fila.push({ segurar: true });
  await motivo(c.page, "Faltou card").click();
  await ate(() => c.f.reqs.length === 2);
  await motivo(c.page, "Número errado").click();
  await motivo(c.page, "Não entendeu").click();
  c.f.solta();
  await ate(() => c.f.reqs.length === 3);
  await c.page.waitForTimeout(300);
  assert.deepEqual(corpos(c.f).slice(1), [{ nota: -1, motivo: "faltou_card" }, { nota: -1, motivo: "nao_entendeu" }]);
  await c.page.context().close();
});

test("A4. a falha do toque guardado volta ao último estado CONFIRMADO, não ao intermediário", async () => {
  const c = await iniciarConversa();
  await responder(c, 0);
  await clica(gostei(c.page), c.f); // confirmado: 👍
  c.f.fila.push({ segurar: true }, { status: 500, json: {} });
  await naoGostei(c.page).click();
  await ate(() => c.f.reqs.length === 2);
  await motivo(c.page, "Número errado").click(); // guardado, sobre o 👎 ainda não confirmado
  c.f.solta({ status: 500, json: {} });
  await alerta(c.page).waitFor({ timeout: 3000 });
  assert.equal(c.f.reqs.length, 3, "o guardado saiu e também falhou");
  assert.equal(await marcado(gostei(c.page)), "true", "volta ao 👍 confirmado");
  assert.equal(await marcado(naoGostei(c.page)), "false");
  assert.equal(await c.page.getByText("O que houve?").count(), 0);
  await c.page.context().close();
});

test("A5. o POST em voo falha mas o guardado grava: fica o estado novo, sem aviso", async () => {
  const c = await iniciarConversa();
  await responder(c, 0);
  c.f.fila.push({ segurar: true });
  await gostei(c.page).click();
  await ate(() => c.f.reqs.length === 1);
  await naoGostei(c.page).click();
  c.f.solta({ status: 500, json: {} });
  await ate(() => c.f.reqs.length === 2);
  await c.page.waitForTimeout(300);
  assert.deepEqual(corpos(c.f), [{ nota: 1 }, { nota: -1 }]);
  assert.equal(await marcado(naoGostei(c.page)), "true");
  assert.equal(await alerta(c.page).count(), 0);
  await c.page.context().close();
});

test("A6. troca de conta com um toque guardado: nada sai em nome da conta nova", async () => {
  const c = await iniciarConversa();
  const { page, conta, flux, f } = c;
  await responder(c, 0, 7, "ok A");
  f.fila.push({ segurar: true });
  await naoGostei(page).click();
  await ate(() => f.reqs.length === 1);
  await motivo(page, "Número errado").click(); // guardado
  await trocarConta(page, conta, 2);
  await page.waitForFunction(() => !document.body.textContent.includes("ok A"));
  await enviar(page, "de B");
  await flux.espera(2);
  await flux.push(1, evento(fim("ok B", { message_id: 9 })));
  await esperaRotulo(page);
  f.solta({ status: 200, json: { ok: true } });
  await page.waitForTimeout(400);
  assert.equal(f.reqs.length, 1, "o guardado de A não saiu");
  assert.equal(await alerta(page).count(), 0);
  assert.equal(await marcado(gostei(page)), "false");
  await page.context().close();
});

test("B. prazo: POST sem resposta em 15 s é abortado, volta ao confirmado e avisa; o toque seguinte funciona", async () => {
  const c = await iniciarConversa();
  await responder(c, 0);
  await c.page.clock.install();
  c.f.fila.push({ segurar: true });
  await gostei(c.page).click();
  await ate(() => c.f.reqs.length === 1);
  await c.page.clock.fastForward(14000);
  assert.equal(await marcado(gostei(c.page)), "true", "antes dos 15 s segue otimista");
  assert.equal(await alerta(c.page).count(), 0);
  await c.page.clock.fastForward(2000);
  await alerta(c.page).waitFor({ timeout: 3000 });
  assert.equal(await marcado(gostei(c.page)), "false", "estourou: volta ao que o servidor tinha");
  c.f.solta({ status: 200, json: { ok: true } }); // a resposta tardia não muda nada
  await clica(gostei(c.page), c.f); // a bolha não ficou travada
  assert.equal(c.f.reqs.length, 2);
  assert.equal(await marcado(gostei(c.page)), "true");
  await c.page.context().close();
});

test("C. Enviar do 'Outro' com sucesso devolve o foco à pílula 'Outro' (não ao <body>)", async () => {
  const c = await iniciarConversa();
  await responder(c, 0);
  await clica(naoGostei(c.page), c.f);
  await clica(motivo(c.page, "Outro"), c.f);
  await campo(c.page).fill("x");
  await clica(c.page.locator(".msg-comentario .btn"), c.f);
  await c.page.getByRole("status").filter({ hasText: "Obrigado pelo retorno" }).waitFor();
  assert.equal(await motivo(c.page, "Outro").evaluate((e) => e === document.activeElement), true);
  await c.page.context().close();
});

test("C+. se o foco já saiu do campo (foi para outra pílula), o envio não o rouba", async () => {
  const c = await iniciarConversa();
  await responder(c, 0);
  await clica(naoGostei(c.page), c.f);
  await clica(motivo(c.page, "Outro"), c.f);
  await campo(c.page).fill("x");
  c.f.fila.push({ segurar: true });
  await c.page.locator(".msg-comentario .btn").click();
  await ate(() => c.f.reqs.length === 3);
  await gostei(c.page).focus();
  c.f.solta();
  await c.page.getByRole("status").filter({ hasText: "Obrigado pelo retorno" }).waitFor();
  assert.equal(await gostei(c.page).evaluate((e) => e === document.activeElement), true);
  await c.page.context().close();
});
