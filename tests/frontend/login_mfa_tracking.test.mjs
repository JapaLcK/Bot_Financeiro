/**
 * O desafio do MFA (#605) não pode chegar ao GA4 nem ao Meta Pixel.
 *
 * `/login` é servida por `html_file(..., pixel=True)`: o servidor injeta GA4 e Meta
 * Pixel no FIM do <head>, e o GA4 lê `location.href` no `gtag('config')`. Um
 * `replaceState` que rode depois (no fim do <body>) deixava `#mfa=<desafio>` no
 * `page_location` do `dataLayer` (medido). Este teste serve o login.html real com o
 * tracking injetado onde o servidor o injeta (GA4 e Meta ligados) e lê o `dataLayer`.
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
import { readFileSync } from 'node:fs';
import { chromium } from 'playwright';

let browser, html;
const ORIGEM = 'http://pb.test';
before(async () => {
  // O CI dos shards de frontend não instala as dependências Python, então nada aqui
  // chama o servidor. A página é o login.html REAL com o trecho de tracking que
  // `inject_tracking` põe antes de `</head>` (o mesmo ponto): o trecho mora em
  // `tracking_snippet_login.html`, e `tests/test_google_web_mfa.py::test_e7` o
  // compara com a saída de `inject_tracking` para ele não envelhecer.
  const snippet = readFileSync('tests/frontend/tracking_snippet_login.html', 'utf8');
  const login = readFileSync('frontend/login.html', 'utf8');
  const i = login.toLowerCase().indexOf('</head>');
  assert.ok(i > 0 && snippet.includes('Google Analytics (GA4)') && snippet.includes('Meta Pixel Code'));
  html = login.slice(0, i) + snippet + login.slice(i);
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
