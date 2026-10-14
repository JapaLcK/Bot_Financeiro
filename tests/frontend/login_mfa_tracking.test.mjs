/**
 * O desafio do MFA (#605) não pode chegar ao GA4 nem ao Meta Pixel.
 *
 * `/login` é servida por `html_file(..., pixel=True)`: o servidor injeta GA4 e Meta
 * Pixel no FIM do <head>, e o GA4 lê `location.href` no `gtag('config')`. Um
 * `replaceState` que rode depois (no fim do <body>) deixava `#mfa=<desafio>` no
 * `page_location` do `dataLayer` (medido). Este teste serve o HTML REAL que a rota
 * devolve (o mesmo `html_file`, com os dois IDs ligados) e lê o `dataLayer`.
 *
 * Controle negativo: tire o `history.replaceState` do <head> do login.html e ponha-o
 * de volta no fim do script do <body> (o conserto antigo) → o teste fica vermelho
 * com `mfa=` no `dataLayer`. Controle positivo: o caso "sem hash" prova que o GA4
 * foi injetado e o `page_view` saiu (senão o `dataLayer` vazio passaria à toa).
 *
 * Os terceiros (googletagmanager, facebook) são stubs vazios: o que se mede é o que
 * a PÁGINA entrega ao `dataLayer`/`fbq`, não o que o Google recebe. A rede real do
 * Google/Meta só se vê no aparelho/deploy.
 */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { chromium } from 'playwright';

let browser, html;
const ORIGEM = 'http://pb.test';
before(async () => {
  html = execFileSync(process.env.PB_PYTHON || 'python3', ['-c', [
    'import sys',
    'from pathlib import Path',
    'from frontend.routes import shared',
    'shared.GA4_MEASUREMENT_ID = "G-TESTE12345"',
    'shared.META_PIXEL_ID = "123456789012345"',
    'sys.stdout.buffer.write(shared.html_file(Path("frontend/login.html")).body)',
  ].join('\n')], { encoding: 'utf8', env: { ...process.env, PYTHONPATH: '.' } });
  assert.ok(html.includes('Google Analytics (GA4)') && html.includes('Meta Pixel Code'), 'tracking não injetado');
  browser = await chromium.launch();
});
after(async () => { await browser?.close(); });

async function abre(caminho) {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  const reqs = [];
  page.on('request', r => reqs.push(r.url()));
  await page.route('**/*', route => {
    const u = new URL(route.request().url());
    if (u.origin !== ORIGEM) return route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
    if (u.pathname === '/login') return route.fulfill({ contentType: 'text/html', body: html });
    if (u.pathname.startsWith('/auth/')) return route.fulfill({ status: 401, contentType: 'application/json', body: '{}' });
    return route.fulfill({ status: 204, body: '' });
  });
  await page.goto(`${ORIGEM}${caminho}`);
  await page.waitForTimeout(800);
  return { page, reqs };
}

test('com #mfa=: o dataLayer, o fbq e as requisições a terceiros não levam o desafio', async () => {
  const { page, reqs } = await abre('/login?next=/precos#mfa=SEGREDO_DESAFIO_abc123');
  const dl = await page.evaluate(() => JSON.stringify(window.dataLayer));
  assert.ok(dl.includes('page_location'), `o config do GA4 não rodou: ${dl}`);
  assert.ok(!dl.includes('mfa=') && !dl.includes('SEGREDO'), `desafio no dataLayer: ${dl}`);
  const fbq = await page.evaluate(() => JSON.stringify(window.fbq && window.fbq.queue));
  assert.ok(!fbq.includes('SEGREDO'), `desafio no fbq: ${fbq}`);
  assert.deepEqual(reqs.filter(u => u.includes('SEGREDO')), []);
  assert.equal(new URL(page.url()).hash, '');
  assert.equal(await page.locator('#form-mfa').isVisible(), true);  // e o formulário de código abriu
  await page.close();
});

test('sem hash: o GA4 foi injetado e o page_view sai com a URL da página', async () => {
  const { page } = await abre('/login?next=/precos');
  const dl = await page.evaluate(() => JSON.stringify(window.dataLayer));
  assert.ok(dl.includes(`"page_location":"${ORIGEM}/login?next=`), dl);
  assert.equal(await page.locator('#form-login').isVisible(), true);
  await page.close();
});
