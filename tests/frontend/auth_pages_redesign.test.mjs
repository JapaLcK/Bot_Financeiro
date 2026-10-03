import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { startServer } from './_server.mjs';

let browser, server, origin;
before(async () => { ({ proc: server, origin } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });
const json = (route, body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
async function pageFor(file, options = {}) {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 }, ...options });
  await page.route('**/auth/**', route => json(route, {}, 401));
  await page.route('**/destino-auth', route => route.fulfill({ contentType: 'text/html', body: '<h1>Conta</h1>' }));
  return page;
}
async function loginFields(page) {
  await page.fill('#email', 'qa@example.test');
  await page.fill('#senha', 'SenhaSegura123');
}

test('login real alterna MFA, aceita backup completo e permite voltar sem expor os dois forms', async () => {
  const page = await pageFor('login');
  const bodies = [];
  await page.route('**/auth/login', route => json(route, { mfa_required: true, mfa_challenge: 'challenge-local', email: 'qa@example.test' }));
  await page.route('**/auth/mfa/verify-login', route => {
    bodies.push(route.request().postDataJSON());
    return json(route, { detail: 'Código inválido.' }, 400);
  });
  await page.goto(`${origin}/login.html`);
  await loginFields(page);
  await page.click('#btn-login');
  await page.locator('#form-mfa').waitFor({ state: 'visible' });
  assert.equal(await page.locator('#form-login').isVisible(), false);
  assert.equal(await page.locator('#mfa-code').evaluate(el => el === document.activeElement), true);
  await page.fill('#mfa-code', 'ABCD-EFGH-1234');
  await page.check('#mfa-backup');
  await page.click('#btn-mfa');
  await page.locator('#mfa-error.show').waitFor();
  assert.deepEqual(bodies, [{ challenge: 'challenge-local', code: 'ABCD-EFGH-1234', use_backup: true }]);
  assert.match(await page.locator('#mfa-error').textContent(), /Código inválido/);
  await page.locator('#form-mfa a').click();
  assert.equal(await page.locator('#form-mfa').isVisible(), false);
  assert.equal(await page.locator('#form-login').isVisible(), true);
  await page.close();
});

test('MFA TOTP concluído navega somente após resposta real, com CSRF', async () => {
  const page = await pageFor('login');
  let request;
  await page.context().addCookies([{ name: 'csrf_token', value: 'csrf-local', url: origin }]);
  await page.route('**/auth/login', route => json(route, { mfa_required: true, mfa_challenge: 'challenge-local' }));
  await page.route('**/auth/mfa/verify-login', route => {
    request = { data: route.request().postDataJSON(), csrf: route.request().headers()['x-csrf-token'] };
    return json(route, { dashboard_url: '/destino-auth' });
  });
  await page.goto(`${origin}/login.html`);
  await loginFields(page); await page.click('#btn-login');
  await page.fill('#mfa-code', '654321'); await page.click('#btn-mfa');
  await page.waitForURL('**/destino-auth');
  assert.deepEqual(request, { data: { challenge: 'challenge-local', code: '654321', use_backup: false }, csrf: 'csrf-local' });
  await page.close();
});

test('sessão access expirada renova com CSRF e pula formulário', async () => {
  const page = await pageFor('login');
  // Compare pathname: **/app também casaria a query ?next=/app do próprio login.
  let validations = 0, refresh = 0;
  await page.context().addCookies([{ name: 'csrf_token', value: 'csrf-local', url: origin }]);
  await page.route('**/auth/validate', route => (validations++, json(route, {}, refresh ? 200 : 401)));
  await page.route('**/auth/refresh', route => {
    assert.equal(route.request().headers()['x-csrf-token'], 'csrf-local'); refresh++;
    return json(route, {});
  });
  await page.route(url => url.pathname === '/app', route => route.fulfill({ contentType: 'text/html', body: '<h1>Painel</h1>' }));
  await page.goto(`${origin}/login.html?next=/app`);
  await page.waitForURL(url => url.pathname === '/app');
  assert.equal(refresh, 1); assert.equal(validations, 2);
  await page.close();
});

test('Google nativo continua anchor interceptável e não conclui login localmente', async () => {
  const page = await pageFor('login', { userAgent: 'PigBankApp QA' });
  await page.addInitScript(() => {
    window.bridgeMessages = [];
    window.webkit = { messageHandlers: { pbAuth: { postMessage: message => window.bridgeMessages.push(message) } } };
  });
  await page.goto(`${origin}/login.html`);
  const google = page.locator('a[href="/auth/google/start"]');
  await google.click();
  assert.deepEqual(await page.evaluate(() => window.bridgeMessages), ['google']);
  assert.ok(page.url().endsWith('/login.html'));
  await page.close();
});

test('cadastro mostra só a etapa real de verificação, reenvia e conserva o e-mail', async () => {
  const page = await pageFor('cadastro');
  const registered = [];
  await page.route('**/auth/register', route => { registered.push(route.request().postDataJSON()); return json(route, { status: 'verification_sent' }); });
  await page.route('**/auth/verify-email', route => json(route, { detail: 'Código inválido.' }, 400));
  await page.goto(`${origin}/cadastro.html`);
  await page.fill('#reg-name', 'Pessoa QA'); await page.fill('#reg-email', 'qa@example.test');
  await page.fill('#reg-phone', '(11) 99999-9999'); await page.fill('#reg-password', 'SenhaSegura123');
  await page.fill('#reg-confirm', 'SenhaSegura123'); await page.check('#terms'); await page.click('#btn-register');
  await page.locator('#form-verify').waitFor({ state: 'visible' });
  assert.equal(await page.locator('#form-register').isVisible(), false);
  assert.equal(await page.locator('#verify-email-display').textContent(), 'qa@example.test');
  await page.fill('#verify-code', '123456'); await page.click('#btn-verify');
  await page.locator('#verify-error.show').waitFor();
  assert.match(await page.locator('#verify-error').textContent(), /Código inválido/);
  await page.getByText('Reenviar código', { exact: true }).click();
  await page.waitForFunction(() => document.querySelector('#verify-error').classList.contains('ok'));
  assert.equal(registered.length, 2); assert.equal(registered[1].email, 'qa@example.test');
  await page.close();
});

for (const valid of [false, true]) {
  test(`perfil Google ${valid ? 'válido conclui com APIs reais' : 'expirado mostra erro e não cria sessão'}`, async () => {
    const page = await pageFor('completar-cadastro');
    let posted;
    await page.route('**/auth/google/pending/**', route => json(route, valid ? { email: 'qa@example.test', name_hint: 'Pessoa QA' } : { detail: 'Cadastro expirado.' }, valid ? 200 : 404));
    await page.route('**/auth/google/complete-signup', route => {
      posted = route.request().postDataJSON(); return json(route, { dashboard_url: '/destino-auth', user_id: 42 });
    });
    await page.goto(`${origin}/completar-cadastro.html?token=token-local`);
    if (!valid) {
      await page.locator('#msg-error.show').waitFor();
      assert.equal(await page.locator('#submit-btn').isDisabled(), true);
      assert.equal(posted, undefined);
    } else {
      await page.waitForFunction(() => document.querySelector('#name').value === 'Pessoa QA');
      await page.fill('#phone', '(11) 99999-9999'); await page.check('#accept-terms'); await page.click('#submit-btn');
      await page.waitForURL('**/destino-auth');
      assert.deepEqual(posted, { token: 'token-local', name: 'Pessoa QA', phone: '(11) 99999-9999', accepted_terms: true });
    }
    await page.close();
  });
}

test('reset preserva token em memória, retira fragmento e só anuncia sucesso após API', async () => {
  const page = await pageFor('reset-password');
  let body;
  await page.route('**/auth/reset-password', route => { body = route.request().postDataJSON(); return json(route, {}); });
  await page.goto(`${origin}/reset-password.html#token=segredo-local`);
  assert.equal(new URL(page.url()).hash, '');
  await page.fill('#password', 'SenhaNova123'); await page.fill('#password2', 'SenhaNova123');
  await page.click('#submit-btn'); await page.locator('#success-state').waitFor({ state: 'visible' });
  assert.deepEqual(body, { token: 'segredo-local', new_password: 'SenhaNova123' });
  assert.equal(await page.locator('#form-state').isVisible(), false);
  await page.goto(`${origin}/reset-password.html`);
  assert.equal(await page.locator('#submit-btn').isDisabled(), true);
  assert.match(await page.locator('#msg-error').textContent(), /Link inválido/);
  await page.close();
});

test('recuperação real distingue erro de envio de confirmação neutra, sem enumerar conta', async () => {
  const page = await pageFor('recuperar-senha');
  let status = 429, body;
  await page.route('**/auth/forgot-password', route => { body = route.request().postDataJSON(); return json(route, {}, status); });
  await page.goto(`${origin}/recuperar-senha.html`);
  await page.fill('#email', 'qa@example.test'); await page.click('#btn-forgot');
  await page.locator('#login-error.show').waitFor();
  assert.equal(await page.locator('#forgot-sent').isVisible(), false);
  assert.equal(await page.locator('#btn-forgot').isEnabled(), true);
  status = 200; await page.click('#btn-forgot');
  await page.locator('#forgot-sent').waitFor({ state: 'visible' });
  assert.deepEqual(body, { email: 'qa@example.test' });
  assert.match(await page.locator('#forgot-sent').textContent(), /Se houver uma conta/);
  await page.click('#forgot-again');
  assert.equal(await page.locator('#form-forgot').isVisible(), true);
  await page.close();
});

test('contato envia campos vazios preenchidos pelo usuário, CSRF e honeypot; erro não apaga mensagem', async () => {
  const page = await pageFor('contato');
  let status = 502, request;
  await page.context().addCookies([{ name: 'csrf_token', value: 'csrf-local', url: origin }]);
  await page.route('**/contact', route => {
    request = { body: route.request().postDataJSON(), csrf: route.request().headers()['x-csrf-token'] };
    return json(route, status === 200 ? { ok: true } : { detail: 'Não conseguimos enviar agora.' }, status);
  });
  await page.goto(`${origin}/contato.html`);
  assert.equal(await page.inputValue('#nome'), ''); assert.equal(await page.inputValue('#msg'), '');
  assert.equal(await page.locator('#website').getAttribute('tabindex'), '-1');
  await page.fill('#nome', 'Pessoa QA'); await page.fill('#email', 'qa@example.test');
  await page.fill('#assunto', 'Ajuda'); await page.fill('#msg', 'Dúvida sobre a conta.'); await page.click('#contact-btn');
  await page.locator('#contact-msg.err').waitFor();
  assert.equal(await page.inputValue('#msg'), 'Dúvida sobre a conta.');
  assert.deepEqual(request, { body: { name: 'Pessoa QA', email: 'qa@example.test', subject: 'Ajuda', message: 'Dúvida sobre a conta.', website: '' }, csrf: 'csrf-local' });
  status = 200; await page.click('#contact-btn'); await page.locator('#contact-msg.ok').waitFor();
  assert.equal(await page.inputValue('#msg'), '');
  await page.close();
});
