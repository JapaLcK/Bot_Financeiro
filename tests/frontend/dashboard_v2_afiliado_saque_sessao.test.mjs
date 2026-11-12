/**
 * #1412 no /painel (afiliado/Saque.tsx): a troca de conta recarrega a página (lib/sessao.ts) e a confirmação do saque sumia sem
 * aviso. Com ela aberta, uma marca em sessionStorage guarda a etapa (nunca a chave Pix); a página recarregada explica num alerta logo
 * abaixo do título (Afiliado.tsx, `Topo`), em todo ramo da tela: a conta nova pode não ser afiliada, nem ter o Saque, nem ter carregado.
 * A recarga é ATRASADA nos testes da troca (`lenta`): a página velha vive até a nova chegar, e o desmonte que roda nela (o `catch` de
 * `agir` fecha o diálogo) não pode apagar a marca. Com recarga instantânea esse caminho não existe e o teste não o veria.
 * Controles negativos (medidos com rebuild): tirar as duas chamadas de `marcar` deixa vermelhos P1, P2, P3, P9 e os P10 da troca;
 * desmontar sem olhar `deSaida()` deixa vermelhos os P3, P9 enviando e P10 não afiliada enviando; tirar o `pagehide` deixa vermelhos
 * P6, P7 e P9 confirmando (o vigia recarrega sem desmontar); não regravar a hora na saída (`solta`) deixa vermelhos os dois P9; o
 * `Topo` sem ler a marca deixa vermelhos P1–P3, P5, P8, P9 e os P10; o código de antes (marca lida no `Saque`) deixa P8 vermelho.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { RESPOSTAS } from "./_painel.mjs";
import { ZERO, abrirAfiliado, abrirSemPlano, confirmar, pedirSaque } from "./_afiliado.mjs";

const browser = navegador();
const CHAVE = "ana@pix.com";
const ANTES = "Sua sessão mudou e a página foi recarregada. Esta solicitação de saque não foi enviada. Confira a conta antes de tentar novamente.";
const DURANTE = "Sua sessão mudou durante a solicitação de saque. Não foi possível confirmar o resultado. Acesse a conta usada na solicitação e confira o histórico de saques antes de tentar novamente.";
const MARCA = "pb_saque_interrompido";
/** O aviso da recarga, onde quer que esteja. `alerta`: o do Saque (a falha comum). */
const aviso = (page) => page.getByRole("alert").filter({ hasText: /^Sua sessão mudou/ });
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
/** O aviso na tela como a pessoa o vê, SEM rolar: logo abaixo do título, dentro do viewport e não coberto (o ponto do meio é dele). */
async function naTela(page, loc, w, h) {
  await page.waitForTimeout(300); // o que mais carrega na página não pode rolar a tela
  const r = await loc.evaluate((e) => {
    const b = e.getBoundingClientRect();
    return { x: b.x, y: b.y, w: b.width, h: b.height, scrollY, dele: e.contains(document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2)), abaixoDoTitulo: e.previousElementSibling?.classList.contains("page-head"), foco: document.activeElement?.tagName };
  });
  assert.ok(r.x >= 0 && r.x + r.w <= w + 0.5 && r.y >= 0 && r.y + r.h <= h && r.dele && r.abaixoDoTitulo, JSON.stringify(r));
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
      await aviso(t.page).waitFor();
      assert.equal(await aviso(t.page).innerText(), ANTES);
      console.log(`P1 ${w}x${h}`, JSON.stringify(await naTela(t.page, aviso(t.page), w, h)));
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
    await aviso(t.page).waitFor();
    assert.equal(await aviso(t.page).innerText(), ANTES);
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
      await aviso(t.page).waitFor();
      assert.equal(await aviso(t.page).innerText(), DURANTE);
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
    assert.equal(await aviso(t.page).count(), 0);
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
    assert.equal(await aviso(t.page).count(), 0);
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
    assert.equal(await aviso(t.page).count(), 0);
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
    assert.equal(await aviso(t.page).count(), 0);
    assert.equal(await t.page.evaluate((m) => sessionStorage.getItem(m), MARCA), null);
  });
});

// P8: a falha comum depois do aviso da recarga fica onde a pessoa está (no campo que ela usa): nada puxa o foco nem rola a tela.
// A ordem é a que derrubou o CI (shard carregado, head 10559bbb): a marca é gravada com a página velha ainda sem o Saque (o GET dele
// chega depois do título), e o GET chega enquanto o documento da recarga demora. Quando a marca era lida na montagem do Saque, a página
// velha a consumia e a nova não avisava; lida no `Topo`, que monta com o título, a página velha já não a lê.
test("P8 390x844: depois do aviso da recarga, a falha comum não puxa o foco nem rola a tela", async () => {
  await comSaque({ width: 390, height: 844 }, async (t) => {
    let soltar, n = 0;
    const preso = new Promise((r) => { soltar = r; });
    t.s.get = async () => { if (n++ === 0) await preso; };
    await t.page.reload();
    await t.page.locator("#page-title").waitFor();
    assert.equal(await t.page.locator("#afil-pix").count(), 0, "o Saque ainda não montou");
    await t.page.evaluate((m) => sessionStorage.setItem(m, JSON.stringify({ etapa: "confirmando", em: Date.now() })), MARCA);
    await lenta(t, 1500);
    setTimeout(soltar, 200);
    await t.page.reload();
    await aviso(t.page).waitFor();
    assert.equal(await aviso(t.page).innerText(), ANTES);
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

// P9: a marca vale pela hora da INTERRUPÇÃO, não da abertura. A confirmação fica aberta (ou o POST fica preso) mais de 60 s antes da
// troca; o relógio fixo do painel é adiantado em vez de esperar. `confirmando`: quem acha a troca é o vigia de 1 s, que recarrega sem
// desmontar o React (só o `pagehide` regrava); `enviando`: o POST volta, o `catch` fecha o diálogo e o desmonte regrava.
const AGORA = Date.parse("2026-10-02T15:00:00Z");
for (const etapa of ["confirmando", "enviando"]) {
  test(`P9 ${etapa}: aberta há mais de 60 s quando a conta troca: ainda avisa`, async () => {
    await comSaque({}, async (t) => {
      let soltar;
      const preso = new Promise((r) => { soltar = r; });
      t.s.post = async () => { await preso; };
      await pedirSaque(t.page, CHAVE);
      if (etapa === "enviando") {
        await t.page.waitForTimeout(450);
        await confirmar(t.page).click();
        await t.page.getByRole("button", { name: "Solicitando…" }).waitFor();
      }
      await t.page.clock.setFixedTime(new Date(AGORA + 61000));
      await lenta(t);
      await t.page.evaluate(() => { document.cookie = "pb_conta=2; path=/"; });
      if (etapa === "enviando") soltar();
      await recarregou(t);
      await aviso(t.page).waitFor({ timeout: 3000 });
      assert.equal(await aviso(t.page).innerText(), etapa === "enviando" ? DURANTE : ANTES);
      assert.equal(t.s.posts.length, etapa === "enviando" ? 1 : 0);
    });
  });
}

// P10: a conta nova não monta o Saque: não é afiliada (o /me sem `afiliado`, ou ele com a API dizendo 404), é afiliada sem comissão
// e sem saldo, ou ainda está carregando. O aviso aparece mesmo assim, abaixo do título.
const CONTAS_NOVAS = {
  "não afiliada": (t) => t.ctx.route((u) => u.pathname === "/api/v2/me", (r) => r.fulfill({ json: { ...RESPOSTAS.me.pro, afiliado: false } })),
  "afiliada no /me, 404 na API": (t) => { t.s.corpo = null; },
  "sem comissão e sem saldo": (t) => { t.s.corpo = ZERO; },
  "carregando": (t) => { t.s.get = () => new Promise(() => {}); },
};
for (const [conta, troca] of Object.entries(CONTAS_NOVAS)) {
  for (const etapa of conta === "não afiliada" ? ["confirmando", "enviando"] : ["confirmando"]) {
    test(`P10 ${conta}, ${etapa}: a página recarregada avisa`, async () => {
      await comSaque({ width: 390, height: 844 }, async (t) => {
        let soltar;
        const preso = new Promise((r) => { soltar = r; });
        t.s.post = async () => { await preso; };
        await pedirSaque(t.page, CHAVE);
        if (etapa === "enviando") {
          await t.page.waitForTimeout(450);
          await confirmar(t.page).click();
          await t.page.getByRole("button", { name: "Solicitando…" }).waitFor();
        }
        await lenta(t);
        await troca(t);
        await t.page.evaluate(() => { document.cookie = "pb_conta=2; path=/"; });
        if (etapa === "enviando") soltar();
        const fim = Date.now() + 10000;
        while (t.cargas() < 1) { if (Date.now() > fim) throw new Error("não recarregou"); await dorme(50); }
        await aviso(t.page).waitFor({ timeout: 5000 });
        assert.equal(await aviso(t.page).innerText(), etapa === "enviando" ? DURANTE : ANTES);
        console.log(`P10 ${conta} ${etapa}`, JSON.stringify(await naTela(t.page, aviso(t.page), 390, 844)));
        assert.equal(await t.page.locator("#afil-pix").count(), 0, "a conta nova não tem o Saque");
      });
    });
  }
}

test("P10 sem plano (GET /afiliado): a página recarregada avisa abaixo do título", async () => {
  const t = await abrirSemPlano(browser());
  try {
    await t.page.evaluate((m) => sessionStorage.setItem(m, JSON.stringify({ etapa: "confirmando", em: Date.now() })), MARCA);
    await t.page.reload();
    await aviso(t.page).waitFor({ timeout: 5000 });
    assert.equal(await aviso(t.page).innerText(), ANTES);
    await naTela(t.page, aviso(t.page), 1280, 800);
  } finally { await t.ctx.close(); }
});
