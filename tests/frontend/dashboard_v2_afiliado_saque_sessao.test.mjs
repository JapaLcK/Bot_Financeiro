/**
 * #1412 no /painel (afiliado/Saque.tsx): a troca de conta recarrega a página (lib/sessao.ts) e a confirmação do saque sumia sem
 * aviso. Com ela aberta, uma marca em sessionStorage guarda a etapa (nunca a chave Pix); a página recarregada explica no alerta do Saque.
 * A recarga é ATRASADA nos testes da troca (`lenta`): a página velha vive até a nova chegar, e o desmonte que roda nela (o `catch` de
 * `agir` fecha o diálogo) não pode apagar a marca. Com recarga instantânea esse caminho não existe e o teste não o veria.
 * Controles negativos: tirar as duas chamadas de `marcar` deixa vermelhos P1, P2 e P3; desmontar sem olhar `deSaida()` deixa vermelhos
 * os dois P3; tirar o `pagehide` deixa vermelhos P6 e P7; tirar a rolagem até o aviso deixa vermelhos P1 390x844 e 320x568; tirar o
 * `daRecarga.current = false` deixa vermelho P8.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { abrirAfiliado, confirmar, pedirSaque } from "./_afiliado.mjs";

const browser = navegador();
const CHAVE = "ana@pix.com";
const ANTES = "Sua sessão mudou e a página foi recarregada. Esta solicitação de saque não foi enviada. Confira a conta antes de tentar novamente.";
const DURANTE = "Sua sessão mudou durante a solicitação de saque. Não foi possível confirmar o resultado. Acesse a conta usada na solicitação e confira o histórico de saques antes de tentar novamente.";
const MARCA = "pb_saque_interrompido";
const alerta = (page) => page.locator("#afil-saque").locator("..").locator("[role=alert]");
const dorme = (ms) => new Promise((r) => setTimeout(r, ms));

async function comSaque(opts, fn) {
  const t = await abrirAfiliado(browser(), opts);
  let cargas = 0;
  t.page.on("load", () => { cargas += 1; });
  try { await fn({ ...t, cargas: () => cargas }); } finally { await t.ctx.close(); }
}
/** Daqui em diante, o documento da recarga leva `ms` para chegar (a página velha segue viva nesse tempo). */
const lenta = (t, ms = 500) => t.ctx.route((u) => u.pathname === "/painel.html", async (r) => { await dorme(ms); return r.fallback(); });
/** Espera a página recarregada mostrar o Saque de novo. */
async function recarregou(t) {
  const fim = Date.now() + 10000;
  while (t.cargas() < 1) { if (Date.now() > fim) throw new Error("não recarregou"); await dorme(50); }
  await t.page.locator("#afil-pix").waitFor();
}
/** O aviso na tela como a pessoa o vê, SEM rolar: dentro do viewport e não coberto (o ponto do meio é dele). */
async function naTela(page, loc, w, h) {
  await page.waitForTimeout(300); // o que mais carrega na página não pode rolar de volta
  const r = await loc.evaluate((e) => {
    const b = e.getBoundingClientRect();
    return { x: b.x, y: b.y, w: b.width, h: b.height, scrollY, dele: e.contains(document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2)), foco: document.activeElement?.id };
  });
  assert.ok(r.x >= 0 && r.x + r.w <= w + 0.5 && r.y >= 0 && r.y + r.h <= h && r.dele, JSON.stringify(r));
  assert.equal(r.foco, "afil-saque", "o foco vai ao título da seção");
  return r;
}

// P1: a outra aba entrou noutra conta com a confirmação aberta.
for (const [w, h] of [[1280, 800], [390, 844], [320, 568]]) {
  test(`P1 ${w}x${h}: o marcador muda com a confirmação aberta: recarrega e avisa que o saque não foi enviado`, async () => {
    await comSaque({ width: w, height: h }, async (t) => {
      await pedirSaque(t.page, CHAVE);
      await lenta(t);
      await t.page.evaluate(() => { document.cookie = "pb_conta=2; path=/"; });
      await recarregou(t);
      await alerta(t.page).waitFor();
      assert.equal(await alerta(t.page).innerText(), ANTES);
      console.log(`P1 ${w}x${h}`, JSON.stringify(await naTela(t.page, alerta(t.page), w, h)));
      assert.equal(t.s.posts.length, 0);
      assert.equal(await t.page.evaluate((m) => sessionStorage.getItem(m), MARCA), null, "lida, a marca sai");
      const tudo = await t.page.evaluate(() => JSON.stringify([{ ...sessionStorage }, { ...localStorage }, document.cookie, location.href]));
      assert.ok(!tudo.includes(CHAVE), tudo);
    });
  });
}

test("P2: o marcador some com a confirmação aberta: o mesmo aviso", async () => {
  await comSaque({}, async (t) => {
    await pedirSaque(t.page, CHAVE);
    await lenta(t);
    await t.page.evaluate(() => { document.cookie = "pb_conta=; path=/; max-age=0"; });
    await recarregou(t);
    await alerta(t.page).waitFor();
    assert.equal(await alerta(t.page).innerText(), ANTES);
    assert.equal(t.s.posts.length, 0);
  });
});

// P3: a troca acontece com o POST em voo. `resposta`: o POST volta e o `buscar` acha a troca; `vigia`: o vigia de 1 s acha antes, e o
// POST volta com a página velha ainda viva (a recarga leva 2 s).
for (const [modo, solta, ms] of [["resposta", 0, 500], ["vigia", 1300, 2000]]) {
  test(`P3 ${modo}: o marcador muda com o POST em voo: avisa que o resultado não pôde ser confirmado`, async () => {
    await comSaque({}, async (t) => {
      let soltar;
      const preso = new Promise((r) => { soltar = r; });
      t.s.post = async () => { await preso; };
      await pedirSaque(t.page, CHAVE);
      await t.page.waitForTimeout(450);
      await confirmar(t.page).click();
      await t.page.getByRole("button", { name: "Solicitando…" }).waitFor();
      await lenta(t, ms);
      await t.page.evaluate(() => { document.cookie = "pb_conta=2; path=/"; });
      setTimeout(soltar, solta);
      await recarregou(t);
      await alerta(t.page).waitFor();
      assert.equal(await alerta(t.page).innerText(), DURANTE);
      assert.equal(t.s.posts.length, 1);
    });
  });
}

// ── positivos: sem troca de conta, nada de aviso ──
test("P4: cancelar e recarregar não avisa; o saque que deu certo não deixa marca", async () => {
  await comSaque({}, async (t) => {
    await pedirSaque(t.page, CHAVE);
    await t.page.waitForTimeout(450);
    await t.page.getByRole("dialog").getByRole("button", { name: "Cancelar" }).click();
    assert.equal(await t.page.evaluate((m) => sessionStorage.getItem(m), MARCA), null);
    await t.page.reload();
    await t.page.locator("#afil-pix").waitFor();
    await t.page.waitForTimeout(300);
    assert.equal(await alerta(t.page).count(), 0);
    await pedirSaque(t.page, CHAVE);
    await t.page.waitForTimeout(450);
    await confirmar(t.page).click();
    await t.page.getByText("Saque solicitado.").waitFor();
    assert.equal(await t.page.evaluate((m) => sessionStorage.getItem(m), MARCA), null);
  });
});

test("P5: a marca de mais de 60 s não avisa (e sai)", async () => {
  await comSaque({}, async (t) => {
    await t.page.evaluate((m) => sessionStorage.setItem(m, JSON.stringify({ etapa: "confirmando", em: Date.now() - 120000 })), MARCA);
    await t.page.reload();
    await t.page.locator("#afil-pix").waitFor();
    await t.page.waitForTimeout(300);
    assert.equal(await alerta(t.page).count(), 0);
    assert.equal(await t.page.evaluate((m) => sessionStorage.getItem(m), MARCA), null);
  });
});

// N1: recarga manual (F5) não é troca de sessão.
test("P6: F5 com a confirmação aberta não avisa", async () => {
  await comSaque({}, async (t) => {
    await pedirSaque(t.page, CHAVE);
    await t.page.reload();
    await t.page.locator("#afil-pix").waitFor();
    await t.page.waitForTimeout(300);
    assert.equal(await alerta(t.page).count(), 0);
    assert.equal(await t.page.evaluate((m) => sessionStorage.getItem(m), MARCA), null);
    assert.equal(t.s.posts.length, 0);
  });
});

test("P7: F5 com o POST em voo não avisa que a sessão mudou", async () => {
  await comSaque({}, async (t) => {
    t.s.post = () => new Promise(() => {}); // nunca responde: a recarga corta o pedido
    await pedirSaque(t.page, CHAVE);
    await t.page.waitForTimeout(450);
    await confirmar(t.page).click();
    await t.page.getByRole("button", { name: "Solicitando…" }).waitFor();
    await t.page.reload();
    await t.page.locator("#afil-pix").waitFor();
    await t.page.waitForTimeout(300);
    assert.equal(await alerta(t.page).count(), 0);
    assert.equal(await t.page.evaluate((m) => sessionStorage.getItem(m), MARCA), null);
  });
});

// P8: só o aviso da recarga leva o foco ao título e rola; a falha comum seguinte fica onde a pessoa está (no campo que ela usa).
test("P8 390x844: depois do aviso da recarga, a falha comum não puxa o foco nem rola a tela", async () => {
  await comSaque({ width: 390, height: 844 }, async (t) => {
    await t.page.evaluate((m) => sessionStorage.setItem(m, JSON.stringify({ etapa: "confirmando", em: Date.now() })), MARCA);
    await t.page.reload();
    await alerta(t.page).waitFor();
    assert.equal(await alerta(t.page).innerText(), ANTES);
    await t.page.waitForTimeout(300);
    await t.page.locator("#afil-pix").fill("abc");
    const antes = await t.page.evaluate(() => { document.getElementById("afil-pix").scrollIntoView({ block: "start" }); return scrollY; });
    await t.page.locator("#afil-pix").press("Enter");
    await alerta(t.page).filter({ hasText: "Informe sua chave Pix." }).waitFor();
    await t.page.waitForTimeout(300);
    const r = await t.page.evaluate(() => ({ foco: document.activeElement?.id, scrollY }));
    console.log("P8", antes, JSON.stringify(r));
    assert.deepEqual(r, { foco: "afil-pix", scrollY: antes });
  });
});
