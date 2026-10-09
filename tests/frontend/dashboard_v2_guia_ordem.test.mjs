// Guia do /painel (#728, auditoria do #828): a resposta do POST /guia que chega por último não
// pode apagar o progresso de uma mais nova. Os dois atalhos para o retrato velho:
//   · POST × POST: o `visto` do convite, lento, volta depois do `feito` do último passo;
//   · GET × POST: o GET do foco da janela saiu antes do `feito` e volta depois dele.
// Nos dois, sem o conserto, a tela final "Fechou! O painel é seu." vira "Por agora é isso".
// E a fila dos POSTs não prende o guia: um POST pendurado segura os seguintes só por ESPERA
// (5 s, parts/Guia.tsx), e a resposta dele, quando enfim volta, é velha demais para gravar.
// Setup: `oferecer` com os passos 2 e 3 já feitos — o 1º (Resumo) é o último a fazer.
import { test } from "node:test";
import assert from "node:assert/strict";
import { FAZER, PASSOS, abrir, acoes, bora, navegador } from "./_guia.mjs";

navegador();

const ate = async (f, ms = 10000) => {
  for (const t0 = Date.now(); !f();) {
    if (Date.now() - t0 > ms) throw new Error(`tempo esgotado: ${f}`);
    await new Promise((ok) => setTimeout(ok, 50));
  }
};
// Os POST /guia que já voltaram ao navegador, na ordem (a `acao` de cada um).
const voltas = (ctx) => {
  const v = [];
  ctx.on("requestfinished", (q) => { if (q.method() === "POST" && q.url().endsWith("/api/v2/guia")) v.push(q.postDataJSON().acao); });
  return v;
};
const doisFeitos = (s) => { s.g.passos[1].feito = s.g.passos[2].feito = true; };
const titulo = (page) => page.locator("#guia-titulo").textContent();

test("POST × POST: o `visto` lento que volta depois do `feito` não desfaz a conclusão", async () => {
  let libera;
  const segura = new Promise((ok) => { libera = ok; });
  let v;
  const { ctx, page, s, erros } = await abrir({
    antes: (ctx, s) => { doisFeitos(s); v = voltas(ctx); },
    postLento: (c) => (c.acao === "visto" ? segura : undefined),
  });
  await bora(page);
  await FAZER[PASSOS[0].acao](page);
  // Sem fila, o `feito` já foi e voltou; com fila, ele espera o `visto`.
  await page.waitForTimeout(500);
  libera();
  await ate(() => v.includes("visto") && v.includes("feito"));
  await page.waitForTimeout(300); // a volta aplicada e pintada
  const t = await titulo(page);
  await ctx.close();
  assert.equal(t, "Fechou! O painel é seu.");
  assert.deepEqual(acoes(s), ["visto", `feito:${PASSOS[0].id}`]);
  assert.deepEqual(erros, []);
});

test("GET × POST: o GET do foco que saiu antes do `feito` e volta depois não desfaz a conclusão", async () => {
  let segurar = false, chegou, respondeu, libera;
  const getChegou = new Promise((ok) => { chegou = ok; });
  const getRespondido = new Promise((ok) => { respondeu = ok; });
  const feitoVoltou = new Promise((ok) => { libera = ok; });
  let v;
  const { ctx, page, s, erros } = await abrir({
    antes: async (ctx, s) => {
      doisFeitos(s);
      v = voltas(ctx);
      // Registrada depois, vence a do helper: o GET segurado leva o retrato da chegada.
      await ctx.route("**/api/v2/guia", async (r) => {
        if (r.request().method() !== "GET" || !segurar) return r.fallback();
        segurar = false;
        const json = structuredClone(s.g);
        chegou();
        await feitoVoltou;
        await r.fulfill({ json }).catch(() => {}); // com o conserto o GET foi abortado
        respondeu();
      });
    },
  });
  await ate(() => v.includes("visto"));
  await bora(page);
  segurar = true;
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange"))); // o focusManager do TanStack ouve na window
  await getChegou;
  await FAZER[PASSOS[0].acao](page);
  await ate(() => v.includes("feito"));
  libera();
  await getRespondido;
  await page.waitForTimeout(300);
  const t = await titulo(page);
  await ctx.close();
  assert.equal(t, "Fechou! O painel é seu.");
  assert.deepEqual(acoes(s), ["visto", `feito:${PASSOS[0].id}`]);
  assert.deepEqual(erros, []);
});

test("fila: um `visto` pendurado segura o `feito` por ~5 s; quando volta, o retrato velho não desfaz a conclusão", async () => {
  let solta, v;
  const { ctx, page, s, erros } = await abrir({
    antes: (ctx, s) => { doisFeitos(s); v = voltas(ctx); },
    // O retrato do `visto` é o da chegada (antes do `feito`): o velho que não pode vencer.
    postLento: (c) => (c.acao === "visto" ? new Promise((ok) => { solta = ok; }) : undefined),
  });
  await bora(page);
  await FAZER[PASSOS[0].acao](page);
  const t0 = Date.now();
  await page.locator("#guia-titulo", { hasText: "Fechou!" }).waitFor({ timeout: 9000 });
  const espera = Date.now() - t0;
  const salvando = await page.getByText("Salvando…").count();
  solta();
  await ate(() => v.includes("visto")); // a resposta velha voltou ao navegador
  await page.waitForTimeout(300); // e o onSuccess dela rodou
  const depois = await titulo(page);
  await ctx.close();
  assert.equal(depois, "Fechou! O painel é seu.");
  assert.ok(espera >= 3500, `o feito não esperou a fila (${espera} ms)`);
  assert.equal(salvando, 0);
  assert.deepEqual(acoes(s), ["visto", `feito:${PASSOS[0].id}`]);
  assert.deepEqual(erros, []);
});
