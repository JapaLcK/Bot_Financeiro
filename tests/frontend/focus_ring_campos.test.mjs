/**
 * O anel de foco tem de alcançar os CAMPOS DE FORMULÁRIO.
 *
 * `site.css` define o piso de foco do site público:
 *
 *   :is(a, button, input, select, textarea, summary, [tabindex]):focus-visible {
 *     outline: 2px solid var(--pink); outline-offset: 2px;
 *   }
 *
 * `:is()` e não `:where()`, e a diferença é o motivo deste arquivo existir.
 * `:where()` zera a especificidade dos argumentos: a regra ficava em (0,1,0),
 * vinda só do `:focus-visible`, e PERDIA para a linha 488
 *
 *   .field input, .field textarea, .field select { ... outline: none; }   (0,1,1)
 *
 * Resultado medido antes do conserto: `outline-style: none` nos 13 campos de
 * /login, /cadastro, /reset-password e /suporte — justamente onde o usuário de
 * teclado digita. `:is()` herda a especificidade do argumento mais forte
 * (`[tabindex]`, (0,1,0)), a regra sobe para (0,2,0) e vence o (0,1,1) pelo `b`.
 *
 * O `faq_focus_ring.test.mjs` não pega isto: ele mede se o anel CABE (geometria
 * de recorte), não se ele é PINTADO. São duas formas de o mesmo anel sumir.
 *
 * O caso 2 é o controle: reinjeta o `outline: none` com a especificidade que
 * vencia antes e exige que o anel suma. Sem ele o caso 1 passaria num CSS que
 * nem carregou.
 *
 * Rodar:  npm run test:frontend
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

/** Páginas do servidor estático que têm `.field`. /suporte é servida pela rota
 *  do FastAPI (o `{{FAQ}}` é montado lá), então fica fora deste harness — a
 *  regra CSS é a mesma e os campos são o mesmo markup. */
const PAGINAS = ["login.html", "cadastro.html", "reset-password.html"];

/** Foca por TECLADO. `el.focus()` nem sempre casa `:focus-visible` — o
 *  navegador decide pela modalidade da última interação. Tab a partir do body
 *  garante a heurística de teclado; sem isso o teste mediria outra coisa. */
async function focarPorTeclado(page, seletor) {
  await page.evaluate(() => document.body.focus());
  const alvo = await page.$(seletor);
  await alvo.evaluate((e) => e.setAttribute("data-alvo-foco", "1"));
  for (let i = 0; i < 60; i++) {
    await page.keyboard.press("Tab");
    if (await page.evaluate(() => document.activeElement?.dataset?.alvoFoco === "1")) return true;
  }
  return false;
}

const anel = (page) => page.evaluate(() => {
  const e = document.activeElement;
  const cs = getComputedStyle(e);
  return { tag: e.tagName.toLowerCase(), focusVisible: e.matches(":focus-visible"),
           style: cs.outlineStyle, width: cs.outlineWidth, offset: cs.outlineOffset };
});

test("campo de formulário focado por teclado recebe o anel de 2px", async () => {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const vistos = [];

  for (const arquivo of PAGINAS) {
    await page.goto(`${ORIGIN}/${arquivo}`);
    await page.waitForSelector(".field input");
    assert.ok(await focarPorTeclado(page, ".field input"),
      `${arquivo}: não consegui alcançar o .field input por Tab`);

    const a = await anel(page);
    assert.ok(a.focusVisible, `${arquivo}: o campo não casou :focus-visible — o caso não mede nada`);
    assert.equal(a.style,  "solid", `${arquivo}: outline-style deveria ser solid, veio "${a.style}"`);
    assert.equal(a.width,  "2px",   `${arquivo}: outline-width deveria ser 2px, veio "${a.width}"`);
    assert.equal(a.offset, "2px",   `${arquivo}: outline-offset deveria ser 2px, veio "${a.offset}"`);
    vistos.push(`${arquivo}:${a.tag}`);
  }

  assert.equal(vistos.length, PAGINAS.length, `esperava um campo por página, medi ${vistos.join(", ")}`);
  await page.close();
});

test("controle: voltando o :where(), o anel some", async () => {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });

  // Serve o site.css com `:is(` trocado de volta por `:where(` — o estado
  // exato de antes do conserto. Injetar uma regra concorrente NÃO serviria de
  // controle: `:is(...)` é (0,2,0) e continuaria vencendo qualquer
  // `.field input { outline: none }` (0,1,1), viesse ele de onde viesse.
  let trocou = false;
  await page.route("**/site.css*", async (route) => {
    const resp = await route.fetch();
    const texto = await resp.text();
    const antes = texto.replace(
      ":is(a, button, input, select, textarea, summary, [tabindex]):focus-visible",
      ":where(a, button, input, select, textarea, summary, [tabindex]):focus-visible");
    trocou = antes !== texto;
    await route.fulfill({ response: resp, body: antes });
  });

  await page.goto(`${ORIGIN}/login.html`);
  await page.waitForSelector(".field input");
  assert.ok(trocou, "não achei a regra `:is(...):focus-visible` no site.css — o controle não mede nada");

  assert.ok(await focarPorTeclado(page, ".field input"), "não alcancei o campo por Tab");
  const a = await anel(page);
  assert.ok(a.focusVisible, "o campo não casou :focus-visible — o controle não mede nada");
  assert.equal(a.style, "none",
    `com :where() o anel deveria sumir; veio "${a.style}" — então o caso 1 não discrimina`);

  await page.close();
});
