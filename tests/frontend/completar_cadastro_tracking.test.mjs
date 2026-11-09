/**
 * #1459: o token do cadastro (`/completar-cadastro?token=...`) não pode chegar ao GA4 nem ao Meta Pixel.
 * Mesmo molde do reset_password_tracking.test.mjs: HTML real + sonda + trecho de tracking antes de `</head>`.
 */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { chromium } from 'playwright';

let browser, html;
const ORIGEM = 'http://pb.test';
before(async () => {
  const snippet = readFileSync('tests/frontend/tracking_snippet_login.html', 'utf8');
  const pagina = readFileSync('frontend/completar-cadastro.html', 'utf8');
  const i = pagina.toLowerCase().indexOf('</head>');
  assert.ok(i > 0 && snippet.includes('Google Analytics (GA4)') && snippet.includes('Meta Pixel Code'));
  const sonda = '<script>window.__urlNoRastreio=location.href</script>';
  html = pagina.slice(0, i) + sonda + snippet + pagina.slice(i);
  browser = await chromium.launch();
});
after(async () => { await browser?.close(); });

let pendingStatus = 200;
let signupStatus = 200;
async function abre(caminho, { page: page0, pending = 200, signup = 200 } = {}) {
  const page = page0 || await browser.newPage({ viewport: { width: 390, height: 844 } });
  const reqs = [];
  page.on('request', r => reqs.push(r.url()));
  await page.route('**/*', route => {
    const u = new URL(route.request().url());
    if (u.origin !== ORIGEM) return route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
    if (u.pathname === '/completar-cadastro') return route.fulfill({ contentType: 'text/html', body: html });
    if (u.pathname.startsWith('/auth/google/pending/')) return route.fulfill({ status: pendingStatus, contentType: 'application/json', body: pendingStatus === 200 ? '{"email":"a@b.co"}' : '{"detail":"Cadastro expirado ou inválido."}' });
    if (u.pathname === '/auth/google/complete-signup') return route.fulfill({ status: signupStatus, contentType: 'application/json', body: signupStatus !== 200 ? '{"detail":"erro"}' : '{"user_id":7,"dashboard_url":"/home"}' });
    return route.fulfill({ status: 204, body: '' });
  });
  pendingStatus = pending;
  signupStatus = signup;
  await page.goto(`${ORIGEM}${caminho}`);
  await page.waitForTimeout(800);
  return { page, reqs };
}
const pendentes = reqs => reqs.filter(u => u.includes('/auth/google/pending/'));

test('com ?token=: rastreio não vê o token, o fetch o usa, e o F5 continua funcionando', async () => {
  const { page, reqs } = await abre('/completar-cadastro?token=SEGREDO_x');
  const dl = await page.evaluate(() => JSON.stringify(window.dataLayer));
  assert.ok(dl.includes('page_location'), `o config do GA4 não rodou: ${dl}`);
  const sonda = await page.evaluate(() => window.__urlNoRastreio);
  const fbq = await page.evaluate(() => JSON.stringify(window.fbq && window.fbq.queue));
  for (const v of [dl, sonda, fbq]) assert.ok(v && !v.includes('SEGREDO'), `vazou: ${v}`);
  assert.equal(new URL(page.url()).search, '');
  assert.deepEqual(pendentes(reqs), [`${ORIGEM}/auth/google/pending/SEGREDO_x`]);
  assert.equal(await page.locator('#email-display').innerText(), 'a@b.co');
  reqs.length = 0;
  await page.reload();
  await page.waitForTimeout(800);
  assert.deepEqual(pendentes(reqs), [`${ORIGEM}/auth/google/pending/SEGREDO_x`]);
  assert.equal(await page.locator('#email-display').innerText(), 'a@b.co');
  await page.close();
});

test('sem token: "Link inválido" e nenhum fetch', async () => {
  const { page, reqs } = await abre('/completar-cadastro');
  assert.match(await page.locator('#msg-error').innerText(), /Link inválido/);
  assert.deepEqual(pendentes(reqs), []);
  await page.close();
});

const guardado = page => page.evaluate(() => sessionStorage.getItem('pb_signup_token'));

test('pending 410: limpa a chave e novo acesso sem token mostra "Link inválido" sem fetch', async () => {
  const { page, reqs: a } = await abre('/completar-cadastro?token=VELHO', { pending: 410 });
  assert.equal(pendentes(a).length, 1);
  assert.equal(await guardado(page), null);
  const { reqs: b } = await abre('/completar-cadastro', { page });
  assert.match(await page.locator('#msg-error').innerText(), /Link inválido/);
  assert.deepEqual(pendentes(b), []);
  await page.close();
});

test('pending 503: NÃO limpa a chave e o F5 refaz o fetch com o mesmo token', async () => {
  const { page, reqs: a } = await abre('/completar-cadastro?token=TRANSIT', { pending: 503 });
  assert.equal(pendentes(a).length, 1);
  assert.equal(await guardado(page), 'TRANSIT');
  a.length = 0;
  await page.reload();
  await page.waitForTimeout(800);
  assert.deepEqual(pendentes(a), [`${ORIGEM}/auth/google/pending/TRANSIT`]);
  await page.close();
});

test('sucesso do complete-signup limpa a chave',async () => {
  const { page } = await abre('/completar-cadastro?token=OK_1');
  assert.equal(await guardado(page), 'OK_1');
  await page.locator('#name').fill('Maria Silva');
  await page.locator('#phone').fill('11999999999');
  await page.locator('#accept-terms').check();
  await page.route('**/home', r => r.fulfill({ contentType: 'text/html', body: 'ok' }));
  await Promise.all([page.waitForURL('**/home'), page.locator('#submit-btn').click()]);
  assert.equal(await guardado(page), null);
  await page.close();
});

test('parâmetro extra e hash sobrevivem, o token não', async () => {
  const { page } = await abre('/completar-cadastro?utm=x&token=SEGREDO_y#ancora');
  const u = new URL(page.url());
  assert.equal(u.search, '?utm=x');
  assert.equal(u.hash, '#ancora');
  await page.close();
});

test('Cancelar limpa a chave', async () => {
  const { page } = await abre('/completar-cadastro?token=CANC_1');
  assert.equal(await guardado(page), 'CANC_1');
  await page.locator('a.back').click();
  assert.equal(await guardado(page), null);
  await page.close();
});

test('?token=A e depois ?token= na mesma aba: "Link inválido", sem fetch para A, chave vazia', async () => {
  const { page } = await abre('/completar-cadastro?token=A');
  assert.equal(await guardado(page), 'A');
  const { reqs } = await abre('/completar-cadastro?token=', { page });
  assert.match(await page.locator('#msg-error').innerText(), /Link inválido/);
  assert.deepEqual(pendentes(reqs), []);
  assert.equal(await guardado(page), null);
  await page.close();
});

test('pending 404 limpa a chave', async () => {
  const { page } = await abre('/completar-cadastro?token=P404', { pending: 404 });
  assert.equal(await guardado(page), null);
  await page.close();
});

async function enviaCadastro(token, signup) {
  const { page } = await abre('/completar-cadastro?token=' + token, { signup });
  await page.locator('#name').fill('Maria Silva');
  await page.locator('#phone').fill('11999999999');
  await page.locator('#accept-terms').check();
  await page.locator('#submit-btn').click();
  await page.waitForTimeout(500);
  return page;
}
for (const st of [401, 404, 410]) {
  test(`complete-signup ${st} limpa a chave`, async () => {
    const page = await enviaCadastro('CS' + st, st);
    assert.equal(await guardado(page), null);
    await page.close();
  });
}
test('complete-signup 400 NÃO limpa a chave', async () => {
  const page = await enviaCadastro('CS400', 400);
  assert.equal(await guardado(page), 'CS400');
  await page.close();
});
