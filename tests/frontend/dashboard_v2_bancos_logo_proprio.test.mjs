// Logo PRÓPRIO do banco no /painel (conta/api.ts::logoDoBanco): arquivo de /brand/bancos/<ispb>.png, mesma origem. Precedência: próprio > `logo_url` da Pluggy > letra.
// Base: dashboard_v2_bancos.test.mjs (os mesmos ladrilhos, o `logo_url`). O servidor serve o PNG de verdade (frontend/brand/bancos/).
//
// CONTROLE NEGATIVO (medido; remeça se mexer): `logoDoBanco` devolvendo `logoSeguro(logo_url)` antes do próprio (precedência invertida) → vermelho em "com os dois campos".
import { test } from "node:test";
import assert from "node:assert/strict";
import { abrir, navegador } from "./_guia.mjs";
import { LOGO, con, regiao, servidorBancos } from "./_bancos.mjs";

const browser = navegador();
void browser;

const PROPRIO = "/brand/bancos/18236120.png"; // Nubank
async function bancos(opts = {}, tela = {}) {
  const s = servidorBancos(opts);
  s.brand = []; // os caminhos de /brand/bancos/ que o navegador pediu
  const antes = async (ctx) => { await s.antes(ctx); ctx.on("request", (q) => { const u = new URL(q.url()); if (u.pathname.startsWith("/brand/bancos/")) s.brand.push(u.pathname + u.search); }); };
  const p = await abrir({ guia: "concluido", rota: "/conta/bancos", antes, ...tela });
  return { ...p, srv: s };
}
const esperaCards = (page, n) => page.waitForFunction((n) => document.querySelectorAll(".banco-card").length === n, n, { timeout: 8000 });
const srcs = (page, sel = ".banco-card .banco-ini") => page.locator(sel).evaluateAll((l) => l.map((e) => e.querySelector("img")?.getAttribute("src") ?? null));
const carregadas = (page, sel) => page.waitForFunction((s) => { const l = [...document.querySelectorAll(`${s} img`)]; return l.length > 0 && l.every((i) => i.complete && i.naturalWidth > 0); }, sel, { timeout: 8000 });
const com = (id, nome, logo_proprio, logo_url) => ({ ...con(id, nome, "updated", "Atualizado", undefined, null, logo_url), ...(logo_proprio === undefined ? {} : { logo_proprio }) });

test("logo próprio: com os dois campos o src é o próprio (carregado, 256 px de origem), a Pluggy nem é pedida; só logo_url = Pluggy; sem os dois = letra", async () => {
  const { ctx, page, srv, erros } = await bancos({ conexoes: [com(11, "Nubank", PROPRIO, LOGO), com(12, "Inter", undefined, LOGO), com(13, "Itaú", null, null)], bancos: "padrao", conectores: [] });
  await esperaCards(page, 3);
  await page.waitForFunction(() => [...document.querySelectorAll(".banco-ini img")].length === 2 && [...document.querySelectorAll(".banco-ini img")].every((i) => i.complete && i.naturalWidth > 0), null, { timeout: 8000 });
  const r = { srcs: await srcs(page), letra: await page.locator(".banco-card .banco-ini").nth(2).innerText(), natural: await page.locator(".banco-card .banco-ini img").first().evaluate((i) => i.naturalWidth) };
  await ctx.close();
  assert.deepEqual(r, { srcs: [PROPRIO, LOGO, null], letra: "I", natural: 256 });
  assert.deepEqual(srv.brand, [PROPRIO]);
  assert.deepEqual(srv.cdn, [LOGO]); // só o do 2º banco; o do 1º (que também tem logo_url) não foi buscado
  assert.deepEqual(erros, []);
});

test("logo próprio: valor fora da regra (outro host, //, .., https, query, 7 dígitos, número, objeto) é ignorado e o logo_url da Pluggy vale; o navegador nem pede o caminho ruim", async () => {
  const ruins = ["//evil.example/x.png", "/brand/bancos/../x.png", "https://evil.example/brand/bancos/18236120.png", "/brand/bancos/18236120.png?x=1", "/brand/bancos/1823612.png", "/brand/bancos/18236120.svg", "/brand/bancos/18236120.png\n", 18236120, {}];
  const { ctx, page, srv, erros } = await bancos({ conexoes: ruins.map((p, i) => com(20 + i, "Alfa", p, LOGO)), bancos: "padrao", conectores: [] });
  await esperaCards(page, ruins.length);
  await carregadas(page, ".banco-card .banco-ini");
  const r = await srcs(page);
  await ctx.close();
  assert.deepEqual(r, ruins.map(() => LOGO));
  assert.deepEqual(srv.brand, []);
  assert.deepEqual(erros, []);
});

test("logo próprio que dá 404 cai na letra (não tenta a Pluggy no meio)", async () => {
  const { ctx, page, srv } = await bancos({ conexoes: [com(11, "Itaú Unibanco", "/brand/bancos/99999999.png", LOGO)], bancos: "padrao", conectores: [] });
  await esperaCards(page, 1);
  await page.waitForFunction(() => document.querySelector(".banco-ini")?.textContent === "I", null, { timeout: 8000 });
  const imgs = await page.locator(".banco-card .banco-ini img").count();
  await ctx.close();
  assert.equal(imgs, 0);
  assert.deepEqual(srv.brand, ["/brand/bancos/99999999.png"]);
  assert.deepEqual(srv.cdn, []);
});

// Os 10 bancos: os 5 primeiros conectados (lista + detalhe), os 5 outros em "Disponíveis para conectar".
const DEZ = [["Itaú", 601, "60701190"], ["Bradesco", 603, "60746948"], ["Banco do Brasil", 611, "00000000"], ["Santander", 608, "90400888"], ["Caixa Econômica Federal", 619, "00360305"],
  ["Inter", 823, "00416968"], ["C6 Bank", 626, "31872495"], ["Nubank", 612, "18236120"], ["BTG Pactual", 675, "30306294"], ["Mercado Pago", 606, "10573521"]];
const caminho = (ispb) => `/brand/bancos/${ispb}.png`;
const conectado = ([nome, id, ispb], i) => ({ ...com(30 + i, nome, caminho(ispb), LOGO), institution_id: String(id) });
const catalogo = DEZ.map(([name, id, ispb]) => ({ id, name, type: "PERSONAL_BANK", color: "123456", logo: LOGO, logo_proprio: caminho(ispb), inv: false }));
const medida = (loc) => loc.evaluateAll((ls) => ls.map((c) => {
  const i = c.querySelector("img"), a = c.getBoundingClientRect(), b = i.getBoundingClientRect();
  return { src: i.getAttribute("src"), natural: i.naturalWidth, c: [a.width, a.height], i: [b.width, b.height], dentro: b.left >= a.left && b.top >= a.top && b.right <= a.right && b.bottom <= a.bottom };
}));

for (const [width, height] of [[1280, 800], [390, 844]]) {
  test(`os 10 bancos com logo próprio em ${width}x${height}: lista (44), detalhe (64) e Disponíveis, imagem carregada dentro do ladrilho, sem rolagem lateral`, async () => {
    const { ctx, page, srv, erros } = await bancos({ conexoes: DEZ.slice(0, 5).map(conectado), conectores: catalogo, bancos: "padrao" }, { width, height });
    await esperaCards(page, 5);
    await regiao(page).waitFor({ timeout: 8000 });
    await carregadas(page, ".banco-card .banco-ini");
    await carregadas(page, ".banco-sec:not(:has(.banco-card)) .banco-ini"); // a seção Disponíveis (a de Meus bancos tem os cards)
    const lista = await medida(page.locator(".banco-card .banco-ini")), disp = await medida(regiao(page).locator(".banco-ini"));
    const detalhes = [];
    for (let n = 0; n < 5; n++) {
      await page.locator(".banco-linha").nth(n).click();
      const d = page.locator("dialog.banco-det[open]");
      await d.waitFor();
      await carregadas(page, "dialog.banco-det[open] .banco-ini");
      detalhes.push((await medida(page.locator("dialog.banco-det[open] .banco-ini")))[0]);
      await d.getByRole("button", { name: "Fechar" }).click();
      await d.waitFor({ state: "detached" });
    }
    const lateral = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    await ctx.close();
    const bons = (l) => l.every((x) => x.natural === 256 && x.dentro);
    assert.deepEqual(lista.map((x) => x.src), DEZ.slice(0, 5).map((d) => caminho(d[2])));
    assert.deepEqual(disp.map((x) => x.src), DEZ.slice(5).map((d) => caminho(d[2])));
    assert.deepEqual(detalhes.map((x) => x.src), DEZ.slice(0, 5).map((d) => caminho(d[2])));
    assert.ok(bons(lista) && bons(disp) && bons(detalhes), JSON.stringify({ lista, disp, detalhes }));
    assert.ok(lista.every((x) => x.c[0] === 44 && x.c[1] === 44) && detalhes.every((x) => x.c[0] === 64 && x.c[1] === 64), JSON.stringify({ lista: lista.map((x) => x.c), detalhes: detalhes.map((x) => x.c) }));
    assert.equal(lateral, false);
    assert.deepEqual([...new Set(srv.brand)].sort(), DEZ.map((d) => caminho(d[2])).sort()); // os 10 arquivos foram pedidos
    assert.deepEqual(srv.cdn, []);
    assert.deepEqual(erros, []);
  });
}

test("folha de contato: os 10 logos no ladrilho da lista (desktop), screenshot para o dono aprovar", { skip: !process.env.FOLHA_LOGOS && "só com FOLHA_LOGOS=/caminho.png" }, async () => {
  const saida = process.env.FOLHA_LOGOS;
  const { ctx, page } = await bancos({ conexoes: DEZ.map(conectado), conectores: [], bancos: "padrao", limite: { ok: true, of_banks_max: 12, em_uso: 10, pode_adicionar: false, code: null, message: null } }, { width: 1280, height: 1400 });
  await esperaCards(page, 10);
  await carregadas(page, ".banco-card .banco-ini");
  await page.locator("#main").screenshot({ path: saida });
  await ctx.close();
});
