/**
 * Login pelo Google / magic link do bot com MFA (#605): o servidor manda o
 * desafio no FRAGMENTO — `/login?next=…#mfa=<desafio>` — e o login.html abre
 * direto o formulário de código e tira o desafio da URL.
 *
 * Controle negativo: tire o `if(PB_MFA)showMfa(PB_MFA,'')` do login.html e os casos de hash
 * válido ficam vermelhos (o formulário de e-mail e senha aparece no lugar);
 * os de "sem hash"/"hash malformado" seguem verdes e provam o caminho normal.
 * Sessão viva (#991): tire o `if(!PB_MFA)` que embrulha o `/auth/validate` e os casos
 * "sessão viva + #mfa=" ficam vermelhos; os de "sessão viva sem hash / hash
 * malformado" seguem verdes (o pulo do app iOS continua).
 *
 * Só no navegador local, com o `/auth/**` mockado; o 302 do servidor é provado
 * em `tests/test_google_web_mfa.py`.
 */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { startServer } from './_server.mjs';

let browser, server, origin;
before(async () => { ({ proc: server, origin } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const json = (route, body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
const TELAS = { desktop: { width: 1440, height: 900 }, mobile: { width: 390, height: 844 } };

async function abre(tela, caminho, { verify, validate = 401 } = {}) {
  const page = await browser.newPage({ viewport: TELAS[tela] });
  const chamadas = [];
  page.validates = 0;                       // quantas vezes o login.html consultou a sessão
  await page.context().addCookies([{ name: 'csrf_token', value: 'csrf-local', url: origin }]);
  await page.route('**/auth/validate', route => { page.validates++; return json(route, {}, validate); });
  await page.route('**/auth/refresh', route => json(route, {}, 401));
  await page.route('**/auth/mfa/verify-login', route => {
    chamadas.push(route.request().postDataJSON());
    return json(route, verify ?? { dashboard_url: '/destino-padrao' });
  });
  // Os destinos não existem no servidor de arquivos: só o caminho importa.
  await page.route(url => /^\/(destino-padrao|continuar-compra|precos|conta|app|settings)$/.test(url.pathname),
    route => route.fulfill({ contentType: 'text/html', body: '<h1>Destino</h1>' }));
  await page.goto(`${origin}${caminho}`);
  return { page, chamadas };
}

const semOverflow = page => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);

for (const tela of Object.keys(TELAS)) {
  test(`${tela}: #mfa=<desafio> abre o formulário de código e some da URL`, async () => {
    const { page, chamadas } = await abre(tela, '/login.html?next=/continuar-compra#mfa=abc_DEF-123');
    await page.locator('#form-mfa').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#form-login').isVisible(), false);
    assert.equal(await page.locator('#mfa-code').evaluate(el => el === document.activeElement), true);
    assert.equal(await page.locator('#mfa-para').isHidden(), true);   // sem e-mail, sem "para ."
    assert.equal(new URL(page.url()).hash, '');
    assert.ok(!page.url().includes('abc_DEF'), page.url());
    assert.ok(page.url().includes('next=/continuar-compra'), page.url());
    assert.equal(await semOverflow(page), true);

    await page.fill('#mfa-code', '123456');
    await page.click('#btn-mfa');
    await page.waitForURL(url => url.pathname === '/continuar-compra');
    assert.deepEqual(chamadas, [{ challenge: 'abc_DEF-123', code: '123456', use_backup: false }]);
    await page.close();
  });

  test(`${tela}: sem hash o formulário normal segue`, async () => {
    const { page } = await abre(tela, '/login.html');
    await page.locator('#form-login').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#form-mfa').isVisible(), false);
    assert.equal(await semOverflow(page), true);
    await page.close();
  });
}

for (const hash of ['#mfa=', '#mfa=a b', '#mfa=<script>', '#mfa=abc&x=1', '#outra=abc']) {
  test(`hash malformado ${JSON.stringify(hash)} é ignorado`, async () => {
    const { page } = await abre('desktop', `/login.html${hash}`);
    await page.locator('#form-login').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#form-mfa').isVisible(), false);
    await page.close();
  });
}

// #991: sessão viva (de OUTRA conta) não pode vencer o formulário de código.
// `validate: 200` = o cookie ainda vale. O contador prova que o IIFE nem rodou
// (só "não navegou" passaria por sorte de timing).
for (const tela of Object.keys(TELAS)) {
  test(`${tela}: sessão viva + #mfa= mostra o código, não consulta /auth/validate e não navega`, async () => {
    const { page, chamadas } = await abre(tela, '/login.html?next=/continuar-compra#mfa=abc_DEF-123', { validate: 200 });
    await page.locator('#form-mfa').waitFor({ state: 'visible' });
    await page.waitForLoadState('networkidle');
    assert.equal(page.validates, 0);
    assert.equal(new URL(page.url()).pathname, '/login.html');
    assert.equal(await page.locator('#form-login').isVisible(), false);
    assert.equal(await semOverflow(page), true);

    await page.fill('#mfa-code', '123456');
    await page.click('#btn-mfa');
    await page.waitForURL(url => url.pathname === '/continuar-compra');
    assert.deepEqual(chamadas, [{ challenge: 'abc_DEF-123', code: '123456', use_backup: false }]);
    await page.close();
  });

  // Positivo: sem desafio válido, a sessão viva continua pulando o login (app iOS).
  for (const hash of ['', '#mfa=a b']) {
    test(`${tela}: sessão viva e hash ${JSON.stringify(hash)} ainda pula o login`, async () => {
      const { page } = await abre(tela, `/login.html?next=/precos${hash}`, { validate: 200 });
      await page.waitForURL(url => url.pathname === '/precos');
      assert.equal(page.validates, 1);
      await page.close();
    });
  }
}

// Todo destino que o servidor devolve em `next` (callback: /continuar-compra;
// /d/: next=/precos|/conta, /app?view=…, /settings?view=open-finance) passa na
// allowlist do `nextParam()` e vence o `dashboard_url` do verify-login.
for (const destino of ['/continuar-compra', '/precos', '/conta', '/app', '/app?view=investments', '/settings?view=open-finance']) {
  test(`next=${destino} sobrevive ao verify-login`, async () => {
    const { page } = await abre('desktop', `/login.html?next=${encodeURIComponent(destino)}#mfa=xyz`);
    await page.fill('#mfa-code', '123456');
    await page.click('#btn-mfa');
    await page.waitForURL(url => url.pathname + url.search === destino);
    await page.close();
  });
}
