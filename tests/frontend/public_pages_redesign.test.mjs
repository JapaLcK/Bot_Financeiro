import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { startServer } from './_server.mjs';
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';

let browser, server, origin;
before(async () => {
  ({ proc: server, origin } = await startServer());
  browser = await chromium.launch();
});
after(async () => { await browser?.close(); server?.kill(); });

const pages = ['agents', 'funcionalidades', 'whatsapp', 'como-funciona', 'comandos',
  'precos', 'privacy', 'termos', 'suporte', 'contato', 'login', 'cadastro',
  'recuperar-senha', 'reset-password', 'completar-cadastro'];

for (const width of [320, 390, 1440]) {
  test(`todas as páginas públicas cabem em ${width}px com conteúdo, imagens e navegação reais`, async () => {
    const page = await browser.newPage({ viewport: { width, height: 900 }, reducedMotion: 'reduce' });
    await page.route('**/auth/**', route => route.fulfill({ status: 401, body: '{}' }));
    await page.route('**/billing/**', route => route.fulfill({ status: 401, body: '{}' }));
    for (const name of pages) {
      const errors = [];
      const onError = error => errors.push(error.message);
      page.on('pageerror', onError);
      await page.goto(`${origin}/${name}.html`);
      await page.evaluate(() => document.fonts.ready);
      const layout = await page.evaluate(() => ({
        width: innerWidth, body: document.documentElement.scrollWidth,
        heading: document.querySelector('h1')?.textContent.trim(),
        demo: !!document.querySelector('[data-demo-form],#demo-trial,#contact-scenario'),
        broken: [...document.images].filter(i => i.complete && !i.naturalWidth).map(i => i.src),
      }));
      assert.ok(layout.heading, `${name}: falta h1`);
      assert.ok(layout.body <= width + 1, `${name}: overflow ${layout.body} > ${width}`);
      assert.equal(layout.demo, false, `${name}: controlador de demonstração`);
      assert.deepEqual(layout.broken, [], `${name}: imagens quebradas`);
      assert.deepEqual(errors, [], `${name}: erro JS`);
      assert.equal(await page.locator('body').evaluate(el => el.classList.contains('lp')), true);
      page.off('pageerror', onError);
    }
    await page.close();
  });
}

test('agentes mantêm sete recortes, textos com espaço e CTA para o destino autenticado', async () => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await page.route('**/auth/validate', route => route.fulfill({ status: 401, body: '{}' }));
  await page.goto(`${origin}/agents.html`);
  assert.equal(await page.locator('.lp-team-card').count(), 7);
  for (const card of await page.locator('.lp-team-card').all()) {
    await card.scrollIntoViewIfNeeded();
    const size = await card.evaluate(el => {
      const portrait = el.querySelector('.lp-team-portrait').getBoundingClientRect();
      const body = el.querySelector('.lp-team-card-body');
      const cs = getComputedStyle(body);
      return { width: portrait.width, height: portrait.height, padding: parseFloat(cs.paddingLeft) };
    });
    assert.ok(Math.abs(size.width - size.height) < 1);
    assert.ok(size.padding >= 20);
  }
  const cta = page.locator('[data-app-href="/app?view=agentes"]').first();
  assert.equal(await cta.getAttribute('href'), '/cadastro');
  await page.route('**/auth/validate', route => route.fulfill({ contentType: 'application/json', body: '{"user_id":42,"email":"qa@example.test"}' }));
  await page.reload();
  await page.waitForFunction(() => document.querySelector('[data-app-href]')?.getAttribute('href') === '/app?view=agentes');
  await page.close();
});

test('Open Finance e índice legal abrem a seção indicada na viewport', async () => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 }, reducedMotion: 'reduce' });
  for (const [name, id] of [['funcionalidades', 'open-finance'], ['privacy', 'privacy-8'], ['termos', 'termos-7']]) {
    await page.goto(`${origin}/${name}.html#${id}`);
    await page.evaluate(() => document.fonts.ready);
    await page.waitForFunction(id => {
      const r = document.getElementById(id).getBoundingClientRect();
      return r.top >= 60 && r.top < innerHeight - 50;
    }, id);
  }
  await page.close();
});

test('copiar comando usa texto real e não dispara envio ao WhatsApp', async () => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 }, userAgent: 'PigBankApp QA' });
  // Chromium não fornece insets de aparelho: substituir só o env do CSS servido.
  await page.route('**/site-help.css*', async route => {
    const response = await route.fetch();
    await route.fulfill({ response, body: (await response.text()).replaceAll('env(safe-area-inset-bottom)', '34px') });
  });
  await page.addInitScript(() => {
    window.copied = [];
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: async text => { window.copied.push(text); } } });
  });
  await page.goto(`${origin}/comandos.html`);
  await page.evaluate(() => document.fonts.ready);
  const button = page.locator('[data-copy]').first();
  const id = await button.getAttribute('data-copy');
  const text = await page.locator(`#${id}`).textContent();
  await button.click();
  assert.deepEqual(await page.evaluate(() => window.copied), [text.trim()]);
  assert.match(await page.locator('#copy-notice').textContent(), /copiado/);
  assert.ok(page.url().endsWith('/comandos.html'));
  const bounds = () => page.locator('#copy-notice').evaluate(el => {
    const range = document.createRange(); range.selectNodeContents(el);
    return { surface: el.getBoundingClientRect().bottom, text: range.getBoundingClientRect().bottom };
  });
  const safeBottom = 844 - 34, placed = await bounds();
  assert.ok(placed.surface <= safeBottom && placed.text <= safeBottom, JSON.stringify(placed));
  await page.addStyleTag({ content: '.lp-copy-notice {bottom:16px!important}' });
  const prior = await bounds();
  assert.ok(prior.surface > safeBottom && prior.text > safeBottom, `controle não detectou invasão: ${JSON.stringify(prior)}`);
  await page.close();
});

test('continuação real conserva contexto da compra, erro recuperável e a apresentação nova', async () => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await page.route('**/continuar-compra', route => route.fulfill({ contentType: 'text/html', body: readFileSync('frontend/precos.html', 'utf8') }));
  await page.route('**/auth/**', route => route.fulfill({ status: 401, body: '{}' }));
  await page.route('**/billing/**', route => route.fulfill({ status: 401, body: '{}' }));
  await page.goto(`${origin}/continuar-compra`);
  await page.locator('#purchase-continuation').waitFor({ state: 'visible' });
  assert.equal(await page.locator('body > .wrap').isVisible(), false);
  assert.equal(await page.locator('.lp-purchase-side').isVisible(), true);
  assert.equal(await page.locator('#purchase-continuation-back').getAttribute('href'), '/precos');
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.close();
});

// Estados criados pelos controladores oficiais, que não aparecem no HTML inicial.
const contrast = async (page, pairs) => page.evaluate(pairs => {
  function luminance(channels) {
    const rgb = channels.slice(0, 3).map(n => {
      const v = Number(n) / 255;
      return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4;
    });
    return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722;
  }
  return pairs.map(([text, surface, property = 'color']) => {
    const channels = color => color.match(/[\d.]+/g).map(Number);
    const bg = channels(getComputedStyle(document.querySelector(surface)).backgroundColor);
    const fg = channels(getComputedStyle(document.querySelector(text))[property]);
    const alpha = fg[3] ?? 1;
    const a = luminance(fg.slice(0, 3).map((v, i) => v * alpha + bg[i] * (1 - alpha)));
    const b = luminance(bg);
    return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
  });
}, pairs);

test('modais Stripe e estados Pix mantêm contraste no tema novo, com controle negativo', async () => {
  const page = await browser.newPage();
  await page.route('**/auth/**', route => route.fulfill({ status: 401, body: '{}' }));
  await page.route('**/billing/**', route => route.fulfill({ status: 401, body: '{}' }));
  await page.goto(`${origin}/precos.html`);
  await page.evaluate(() => {
    _ensureChangeModal();
    document.querySelector('#chg-overlay').style.display = 'flex';
    const modal = pixOverlay('Pagamento anual');
    for (const [className, message] of [['pix-erro', 'Não foi possível continuar.'], ['pix-status ok', 'Pagamento confirmado.']]) {
      const p = document.createElement('p'); p.className = className; p.textContent = message; modal.box.append(p);
    }
  });
  const pairs = [['#chg-overlay h3', '#chg-overlay>div'], ['.pix-erro', '.pix-box'], ['.pix-status.ok', '.pix-box']];
  assert.ok((await contrast(page, pairs)).every(r => r >= 4.5));
  await page.addStyleTag({ content: '#chg-overlay {color:#111!important}.pix-erro {color:#ffb4b4!important}.pix-status.ok {color:#c6f11a!important}' });
  assert.ok((await contrast(page, pairs)).every(r => r < 4.5), 'o controle deve detectar as três regressões de contraste');
  await page.close();
});

test('guias expandidos têm superfícies legíveis e um só marcador por item', async t => {
  const page = await browser.newPage();
  // Conteúdo original, incluindo SVG e blocos ricos: não reproduzir o markup numa fixture parcial.
  const guides = JSON.parse(execFileSync(process.env.PB_PYTHON || 'python3', ['-c',
    'import json; from core.blog_guides import GUIDES; print(json.dumps(GUIDES))'], { encoding: 'utf8' }));
  const guide = guides.map(g => `<div class="faq-item open"><button class="faq-q">${g.title}</button><div class="faq-a"><div class="guide-prose">${g.body}</div></div></div>`).join('');
  await page.route('**/suporte.html', route => route.fulfill({ contentType: 'text/html', body: readFileSync('frontend/suporte.html', 'utf8').replace('{{FAQ}}', guide) }));
  await page.goto(`${origin}/suporte.html`);
  const htmlPairs = [['.g-cardshot-name', '.g-cardshot'], ['.g-stat .n', '.g-stat']];
  const svgPairs = [['.g-chart-endlabel', '.g-chart', 'fill'], ['.g-chart svg>g[fill] text', '.g-chart', 'fill']];
  const gridPair = [['.g-chart svg>g[stroke]', '.g-chart', 'stroke']];
  assert.ok((await contrast(page, htmlPairs)).every(r => r >= 4.5));
  const labels = await contrast(page, svgPairs), grid = await contrast(page, gridPair);
  t.diagnostic(JSON.stringify({ svgLabels: labels, svgGrid: grid }));
  assert.ok(labels.every(r => r >= 4.5), `SVG real com labels ilegíveis: ${labels}`);
  assert.ok(grid[0] >= 1.5, `grade invisível: ${grid}`);
  assert.equal(await page.locator('.guide-prose ul').first().evaluate(el => getComputedStyle(el).listStyleType), 'none');
  assert.equal(await page.locator('.guide-prose li').first().evaluate(el => getComputedStyle(el, '::before').content), '"•"');
  await page.addStyleTag({ content: '.lp-help .guide-prose .g-chart-endlabel {fill:#c6f11a!important}.lp-help .guide-prose .g-chart svg>g[fill] text {fill:rgba(255,255,255,.42)!important}.lp-help .guide-prose .g-chart svg>g[stroke] {stroke:rgba(255,255,255,.07)!important}' });
  const oldLabels = await contrast(page, svgPairs), oldGrid = await contrast(page, gridPair);
  t.diagnostic(JSON.stringify({ oldSvgLabels: oldLabels, oldSvgGrid: oldGrid }));
  assert.ok(oldLabels.every(r => r < 4.5), 'o controle deve reprovar o SVG anterior');
  assert.ok(oldGrid[0] < 1.1, 'o controle deve detectar a grade clara anterior');
  await page.addStyleTag({ content: '.lp-help .guide-prose {--card-2:#1d1d21}' });
  assert.ok((await contrast(page, htmlPairs)).every(r => r < 4.5));
  await page.close();
});
