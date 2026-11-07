/**
 * #990: o token de reset (`/reset-password#token=...`) não pode chegar ao GA4 nem ao Meta Pixel.
 * Mesmo molde do login_mfa_tracking.test.mjs: HTML real + trecho de tracking antes de `</head>`.
 * O `fbq.queue` nunca guarda URL, então uma sonda logo antes do trecho registra a
 * `location.href` que o tracking enxerga.
 * Os terceiros são stubs vazios: a rede real do Google/Meta só se vê no deploy.
 */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { chromium } from 'playwright';

let browser, html;
const ORIGEM = 'http://pb.test';
before(async () => {
  const snippet = readFileSync('tests/frontend/tracking_snippet_login.html', 'utf8');
  const pagina = readFileSync('frontend/reset-password.html', 'utf8');
  const i = pagina.toLowerCase().indexOf('</head>');
  assert.ok(i > 0 && snippet.includes('Google Analytics (GA4)') && snippet.includes('Meta Pixel Code'));
  const sonda = '<script>window.__urlNoRastreio=location.href</script>';
  html = pagina.slice(0, i) + sonda + snippet + pagina.slice(i);
  browser = await chromium.launch();
});
after(async () => { await browser?.close(); });

async function abre(caminho) {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  const reqs = [], posts = [];
  page.on('request', r => { reqs.push(r.url()); if (r.method() === 'POST') posts.push(r.postDataJSON()); });
  await page.route('**/*', route => {
    const u = new URL(route.request().url());
    if (u.origin !== ORIGEM) return route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
    if (u.pathname === '/reset-password') return route.fulfill({ contentType: 'text/html', body: html });
    if (u.pathname.startsWith('/auth/')) return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    return route.fulfill({ status: 204, body: '' });
  });
  await page.goto(`${ORIGEM}${caminho}`);
  await page.waitForTimeout(800);
  return { page, reqs, posts };
}

test('com #token=: sonda, dataLayer e requisições não levam o token, e o POST ainda o envia', async () => {
  const { page, reqs, posts } = await abre('/reset-password#token=SEGREDO_x');
  const dl = await page.evaluate(() => JSON.stringify(window.dataLayer));
  assert.ok(dl.includes('page_location'), `o config do GA4 não rodou: ${dl}`);
  assert.ok(!dl.includes('SEGREDO') && !dl.includes('token='), `token no dataLayer: ${dl}`);
  const sonda = await page.evaluate(() => window.__urlNoRastreio);
  assert.ok(sonda && !sonda.includes('SEGREDO'), `token na URL vista pelo tracking: ${sonda}`);
  const fbq = await page.evaluate(() => JSON.stringify(window.fbq && window.fbq.queue));
  assert.ok(!fbq.includes('SEGREDO'), `token no fbq: ${fbq}`);
  assert.deepEqual(reqs.filter(u => u.includes('SEGREDO')), []);
  assert.equal(new URL(page.url()).hash, '');
  await page.fill('#password', 'senhaforte123');
  await page.fill('#password2', 'senhaforte123');
  await page.click('#submit-btn');
  await page.waitForSelector('#success-state', { state: 'visible' });
  assert.equal(posts.length, 1);
  assert.equal(posts[0].token, 'SEGREDO_x');
  await page.close();
});

test('hash malformado #token%3D: nada vaza e a tela é "Link inválido"', async () => {
  const { page, reqs } = await abre('/reset-password#token%3DSEGREDO_x');
  const dl = await page.evaluate(() => JSON.stringify(window.dataLayer));
  const sonda = await page.evaluate(() => window.__urlNoRastreio);
  const fbq = await page.evaluate(() => JSON.stringify(window.fbq && window.fbq.queue));
  for (const v of [dl, sonda, fbq, reqs.join(' ')]) assert.ok(!v.includes('SEGREDO'), `vazou: ${v}`);
  assert.equal(new URL(page.url()).hash, '');
  assert.match(await page.locator('#msg-error').innerText(), /Link inválido/);
  assert.equal(await page.locator('#submit-btn').isDisabled(), true);
  await page.close();
});

test('sem hash: page_view com a URL da página, "Link inválido", botão desabilitado', async () => {
  const { page } = await abre('/reset-password');
  const dl = await page.evaluate(() => JSON.stringify(window.dataLayer));
  assert.ok(dl.includes(`"page_location":"${ORIGEM}/reset-password`), dl);
  assert.equal(await page.locator('#submit-btn').isDisabled(), true);
  await page.close();
});
