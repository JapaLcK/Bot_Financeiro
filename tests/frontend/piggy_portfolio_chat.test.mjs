import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { chromium } from 'playwright';

const frontend = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'frontend');
let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

async function openChat(viewport, investments) {
  const page = await browser.newPage({ viewport });
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.setContent(`
    <button id="piggy-fab">Abrir Piggy</button>
    <div id="piggy-panel"><div id="piggy-body" class="piggy-body"><div id="piggy-empty"></div></div>
      <textarea id="piggy-input"></textarea><button id="piggy-send">Enviar</button><div id="piggy-usage"></div></div>`);
  await page.addStyleTag({ path: join(frontend, 'dashboard.css') });
  await page.evaluate(items => {
    window.USER_ID = 42;
    window.isProUser = () => true;
    window.csrfHeaders = headers => headers;
    window.requests = [];
    window.fetch = async (url, options) => {
      requests.push({ url, options });
      if (url === '/ai/messages?limit=1') return new Response(JSON.stringify({ usage: { used: 1, limit: 100 } }), { status: 200 });
      if (url === '/ai/chat') return new Response(JSON.stringify({ reply: 'Veja seus investimentos abaixo.', usage: { used: 2, limit: 100 } }), { status: 200 });
      if (url === '/open-finance/42') return new Response(JSON.stringify({ investments: items }), { status: 200 });
      throw new Error(`URL inesperada: ${url}`);
    };
  }, investments);
  await page.addScriptTag({ path: join(frontend, 'dashboard-chat-portfolio.js') });
  await page.addScriptTag({ path: join(frontend, 'dashboard-chat.js') });
  await page.getByRole('button', { name: 'Abrir Piggy' }).click();
  return { page, errors };
}

test('pergunta sobre carteira exibe ativos reais agrupados e detalhes por clique', async () => {
  const { page, errors } = await openChat({ width: 1100, height: 800 }, [
    { name: 'CDB do Nubank', institution_name: 'Nubank', type: 'FIXED_INCOME', subtype: 'CDB', balance: '250.50', currency: 'BRL' },
    { name: 'Tesouro IPCA+', institution_name: 'Nubank', type: 'FIXED_INCOME', balance: '100', currency: 'BRL' },
    { name: 'XPML11', institution_name: 'Nubank', type: 'EQUITY', balance: '149.50', currency: 'BRL' },
    { name: '<img src=x onerror=alert(1)>', institution_name: 'Banco', type: 'EQUITY', balance: '0', currency: 'BRL' },
    { name: 'USD', institution_name: 'Banco', type: 'EQUITY', balance: '100', currency: 'USD' },
  ]);
  try {
    await page.evaluate(() => piggyAsk('Quanto tenho na minha carteira?'));
    await page.locator('.piggy-portfolio-heading strong').waitFor();
    assert.equal(await page.locator('.piggy-portfolio-heading strong').textContent(), 'R$ 500,00');
    assert.equal(await page.locator('.piggy-portfolio-group').count(), 2);
    assert.equal(await page.locator('.piggy-portfolio-row').first().isVisible(), false);
    await page.locator('.piggy-portfolio-group summary').first().click();
    assert.equal(await page.locator('.piggy-portfolio-group[open] .piggy-portfolio-row').count(), 2);
    assert.equal(await page.locator('.piggy-portfolio-row').first().isVisible(), true);
    assert.match(await page.locator('.piggy-portfolio-bar').getAttribute('aria-label'), /Renda fixa: R\$\s350,50/);
    assert.equal(await page.locator('.piggy-portfolio-note').count(), 1);
    assert.match(await page.locator('.piggy-portfolio-note').textContent(), /podem incluir caixinhas/);
    if (process.env.PIGGY_PREVIEW_DIR) await page.screenshot({ path: `${process.env.PIGGY_PREVIEW_DIR}/piggy-desktop.png` });
    await page.locator('.piggy-portfolio-followups button').first().click();
    await page.locator('.piggy-portfolio').nth(1).waitFor();
    assert.equal(await page.evaluate(() => requests.filter(r => r.url === '/ai/chat').length), 2);
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
});

test('nomes do conector viram texto, consultas sem carteira não carregam dados', async () => {
  const { page, errors } = await openChat({ width: 390, height: 844 }, [
    { name: '<img src=x onerror=alert(1)>', institution_name: 'Banco', type: 'FIXED_INCOME', balance: '10', currency: 'BRL' },
  ]);
  try {
    await page.evaluate(() => piggyAsk('Quanto gastei esse mês?'));
    await page.locator('.piggy-msg.assistant').waitFor();
    assert.equal(await page.locator('.piggy-portfolio').count(), 0);
    await page.evaluate(() => piggyAsk('Quais investimentos eu tenho?'));
    await page.locator('.piggy-portfolio-row').first().waitFor({ state: 'attached' });
    assert.equal(await page.locator('.piggy-portfolio img').count(), 0);
    await page.locator('.piggy-portfolio-group summary').click();
    assert.match(await page.locator('.piggy-portfolio-row').textContent(), /<img src=x onerror=alert\(1\)>/);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    assert.equal(overflow, false);
    if (process.env.PIGGY_PREVIEW_DIR) await page.screenshot({ path: `${process.env.PIGGY_PREVIEW_DIR}/piggy-mobile.png` });
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
});
