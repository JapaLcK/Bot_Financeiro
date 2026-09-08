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
 * O FAQ de /suporte é o mesmo markup (`.faq-item > .faq-q`), montado em
 * `frontend/routes/static_pages.py` pelo `{{FAQ}}` — o servidor estático desta
 * pasta não serve aquela rota, mas a regra CSS é a mesma.
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

async function abrir() {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await page.goto(`${ORIGIN}/index.html`);
  await page.waitForSelector(".faq-q");
  return page;
}

/** Para cada .faq-q: a menor distância entre a border-box dele e a borda de
 *  recorte de qualquer ancestral que recorte. Infinity = ninguém recorta. */
const folgas = (page) => page.$$eval(".faq-q", (els) => els.map((el) => {
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
  assert.ok(fs.length >= 4, `poucos .faq-q na index: ${fs.length}`);
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
