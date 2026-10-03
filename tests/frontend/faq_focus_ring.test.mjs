/**
 * O anel de foco do FAQ precisa de espaço para existir.
 *
 * `site.css` desenha o foco do site público com `outline: 2px solid var(--pink);
 * outline-offset: 2px` — 4px de tinta FORA da border-box do elemento. O `.faq-q`
 * encosta na borda do `.faq-item` (folga 0), então qualquer `overflow` que
 * recorte no `.faq-item` come o anel inteiro: 0 pixel visível, WCAG 2.4.7. Foi
 * o que a ONDA 1 causou ao trocar o `outline-style: auto` do navegador (que o
 * Chromium pinta por fora do recorte) por um outline próprio, que não.
 *
 * Duas asserções, e a segunda é o controle: ela reinjeta `overflow: hidden` e
 * exige que a folga volte a ZERO. Sem ela a primeira passaria num CSS que nem
 * carregou.
 *
 * O FAQ de /suporte usa `.faq-item > .faq-q`, montado no servidor pelo
 * `{{FAQ}}`. O harness estático recebe uma fixture desse componente dentro
 * do template real de suporte; a guarda do renderer mantém o contrato visível.
 * A landing agora usa details/summary nativos e tem seu próprio par de testes.
 *
 * Rodar:  npm run test:frontend
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";
import { readFileSync } from "node:fs";

const SUPPORT = readFileSync(new URL('../../frontend/suporte.html', import.meta.url), 'utf8');
const ROUTES = readFileSync(new URL('../../frontend/routes/static_pages.py', import.meta.url), 'utf8');
// Fixture de geometria, sem prometer cobertura do conteúdo/renderer do FastAPI.
const QUESTION = '<div class="faq-item">'
  + '<button class="faq-q" type="button" aria-expanded="false">Pergunta de exemplo <span class="chev">+</span></button>'
  + '<div class="faq-a"><div class="guide-prose">Resposta de exemplo</div></div></div>';


let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

async function abrir(pagina = 'suporte') {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  if (pagina === 'suporte') {
    assert.ok(ROUTES.includes('<div class="faq-item">'));
    assert.ok(ROUTES.includes('<button class="faq-q" type="button" aria-expanded="false">'));
    await page.route('**/suporte.html', route => route.fulfill({
      status: 200, contentType: 'text/html', body: SUPPORT.replaceAll('{{FAQ}}', QUESTION.repeat(4)),
    }));
  }
  await page.goto(`${ORIGIN}/${pagina}.html`);
  await page.waitForSelector(pagina === 'suporte' ? '.faq-q' : '.lp-faq summary');
  return page;
}

/** Para cada .faq-q: a menor distância entre a border-box dele e a borda de
 *  recorte de qualquer ancestral que recorte. Infinity = ninguém recorta. */
const folgas = (page, seletor = ".faq-q") => page.$$eval(seletor, (els) => els.map((el) => {
  const r = el.getBoundingClientRect();
  let menor = Infinity;
  for (let p = el.parentElement; p; p = p.parentElement) {
    const s = getComputedStyle(p);
    if (s.overflowX === "visible" && s.overflowY === "visible") continue;
    const pr = p.getBoundingClientRect();
    const cm = parseFloat(s.overflowClipMargin) || 0;   // overflow: clip
    const lados = [];
    if (s.overflowX !== "visible")
      lados.push(r.left - (pr.left + parseFloat(s.borderLeftWidth) - cm),
                 (pr.right - parseFloat(s.borderRightWidth) + cm) - r.right);
    if (s.overflowY !== "visible")
      lados.push(r.top - (pr.top + parseFloat(s.borderTopWidth) - cm),
                 (pr.bottom - parseFloat(s.borderBottomWidth) + cm) - r.bottom);
    menor = Math.min(menor, ...lados);
  }
  return menor;
}));

test("o anel de foco do .faq-q cabe: nenhum ancestral recorta os 4px", async () => {
  const page = await abrir();

  // A regra do site: 2px de largura + 2px de offset = 4px fora da border-box.
  // Se o CSS não carregar, isto não dá 4 — a medição abaixo não passa por acaso.
  const anel = await page.$eval(".faq-q", (b) => {
    b.focus();
    const s = getComputedStyle(b);
    return parseFloat(s.outlineWidth) + parseFloat(s.outlineOffset);
  });
  assert.equal(anel, 4, "site.css mudou o anel; ajuste a folga exigida junto");

  const fs = await folgas(page);
  assert.ok(fs.length >= 4, `poucos .faq-q na fixture de suporte: ${fs.length}`);
  for (const f of fs) assert.ok(f >= 4, `folga ${f}px < 4px — o anel some`);
  await page.close();
});

test("controle: com overflow:hidden de volta no .faq-item, a folga zera", async () => {
  const page = await abrir();
  await page.addStyleTag({ content: ".faq-item { overflow: hidden !important; }" });
  const fs = await folgas(page);
  assert.ok(fs.every((f) => f < 4), `medição cega: folgas ${fs} mesmo com o recorte de volta`);
  await page.close();
});


test("o foco do FAQ nativo da landing é visível e cabe fora do summary", async () => {
  const page = await abrir('index');
  await page.keyboard.press('Tab');
  await page.locator('.lp-faq summary').first().focus();
  const ring = await page.locator('.lp-faq summary').first().evaluate(el => {
    const css = getComputedStyle(el);
    return { visible: el.matches(':focus-visible'), style: css.outlineStyle,
      space: parseFloat(css.outlineWidth) + parseFloat(css.outlineOffset) };
  });
  assert.equal(ring.visible, true);
  assert.equal(ring.style, 'solid');
  assert.ok(ring.space >= 4, `anel insuficiente: ${JSON.stringify(ring)}`);
  const spaces = await folgas(page, '.lp-faq summary');
  assert.equal(spaces.length, 3);
  for (const space of spaces) assert.ok(space >= ring.space, `folga ${space}px < ${ring.space}px`);
  await page.close();
});

test("controle: recortar details da landing também elimina o espaço do anel", async () => {
  const page = await abrir('index');
  await page.addStyleTag({ content: '.lp-faq details { overflow: hidden !important; }' });
  const spaces = await folgas(page, '.lp-faq summary');
  assert.equal(spaces.length, 3);
  assert.ok(spaces.every(space => space < 4), `medição cega ao recorte: ${spaces}`);
  await page.close();
});
