/**
 * Login de visualização (PR 2b, frontend/login.html): o link "Entrar com acesso de visualização" troca o formulário por
 * usuário + senha (sem Google, sem e-mail, sem MFA), posta `/auth/leitura/login {usuario, senha}` com o mesmo CSRF do login normal
 * e vai ao `dashboard_url`. 401 genérico, 429 e 422 têm texto próprio (o 422 do FastAPI manda uma LISTA em `detail`, que o `payload()` zera).
 * Sessão de leitor já viva (cookie): o /auth/validate do topo manda ao painel como a de qualquer conta.
 *
 * Controle POSITIVO: o login normal, no mesmo arquivo, segue postando /auth/login com e-mail e senha.
 * NEGATIVO (medido): tire o link do login.html (ou aponte o `doLeitura` para /auth/login) e os casos do leitor ficam vermelhos; o do e-mail segue verde.
 * Só no navegador local, com o `/auth/**` mockado; a rota de verdade é provada em tests/test_leitura_guard.py e tests/test_api_v2_leitura.py.
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
const semOverflow = (page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);

async function abre(tela, { validate = 401, leitura, login } = {}) {
  const page = await browser.newPage({ viewport: TELAS[tela] });
  const posts = [];
  await page.context().addCookies([{ name: 'csrf_token', value: 'csrf-local', url: origin }]);
  await page.route('**/auth/validate', (r) => json(r, { dashboard_url: '/painel' }, validate));
  await page.route('**/auth/refresh', (r) => json(r, {}, 401));
  await page.route('**/auth/leitura/login', (r) => { posts.push({ caminho: '/auth/leitura/login', corpo: r.request().postDataJSON(), csrf: r.request().headers()['x-csrf-token'] }); return leitura ? leitura(r) : json(r, { user_id: 1, somente_leitura: true, dashboard_url: '/painel' }); });
  await page.route('**/auth/login', (r) => { posts.push({ caminho: '/auth/login', corpo: r.request().postDataJSON() }); return login ? login(r) : json(r, { dashboard_url: '/painel' }); });
  await page.route((u) => /^\/(painel|home)$/.test(u.pathname), (r) => r.fulfill({ contentType: 'text/html', body: '<h1>Destino</h1>' }));
  await page.goto(`${origin}/login.html`);
  return { page, posts };
}
const abreLeitura = async (page) => { await page.getByRole('link', { name: 'Entrar com acesso de visualização' }).click(); await page.locator('#form-leitura').waitFor({ state: 'visible' }); };

for (const tela of Object.keys(TELAS)) {
  test(`${tela}: o link troca para usuário e senha, sem Google nem e-mail, e o Voltar devolve o formulário normal`, async () => {
    const { page } = await abre(tela);
    await abreLeitura(page);
    assert.equal(await page.locator('#form-login').isVisible(), false);
    assert.equal(await page.locator('#leitura-usuario').evaluate((el) => el === document.activeElement), true);
    assert.equal(await page.locator('#form-leitura .social-btn, #form-leitura input[type=email]').count(), 0);
    assert.equal(await semOverflow(page), true);
    await page.getByRole('link', { name: 'Voltar', exact: true }).click();
    await page.locator('#form-login').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#form-leitura').isVisible(), false);
    await page.close();
  });

  test(`${tela}: usuário e senha vão a /auth/leitura/login com o CSRF, sem e-mail, e o navegador segue o dashboard_url`, async () => {
    const { page, posts } = await abre(tela);
    await abreLeitura(page);
    await page.fill('#leitura-usuario', ' condomino ');
    await page.fill('#leitura-senha', 'senha-leitura');
    await Promise.all([page.waitForURL(`${origin}/painel`), page.click('#btn-leitura')]);
    assert.deepEqual(posts, [{ caminho: '/auth/leitura/login', corpo: { usuario: 'condomino', senha: 'senha-leitura' }, csrf: 'csrf-local' }]);
    await page.close();
  });

  test(`${tela}: erros — 401 genérico, 429 e 422 com texto próprio, campos vazios sem requisição; o botão volta a servir`, async () => {
    let status = 401;
    const { page, posts } = await abre(tela, { leitura: (r) => json(r, status === 422 ? { detail: [{ loc: ['body', 'usuario'], msg: 'x', type: 'y' }] } : { detail: status === 401 ? 'Usuário ou senha incorretos.' : 'limite' }, status) });
    await abreLeitura(page);
    await page.click('#btn-leitura');
    assert.equal(await page.locator('#leitura-error').textContent(), 'Preencha usuário e senha.');
    assert.equal(posts.length, 0);
    await page.fill('#leitura-usuario', 'condomino');
    await page.fill('#leitura-senha', 'errada');
    for (const [s, texto] of [[401, 'Usuário ou senha incorretos.'], [429, 'Muitas tentativas. Aguarde alguns minutos e tente de novo.'], [422, 'Confira o usuário e a senha.']]) {
      status = s;
      await page.click('#btn-leitura');
      await page.waitForFunction((t) => document.getElementById('leitura-error').textContent === t, texto);
      assert.equal(await page.locator('#btn-leitura').isEnabled(), true);
      assert.equal(await page.locator('#btn-leitura').textContent(), 'Entrar');
    }
    assert.equal(await semOverflow(page), true);
    await page.close();
  });
}

test('sessão viva (cookie de leitor): o /auth/validate do topo manda ao painel, sem passar pelo formulário', async () => {
  const page = await browser.newPage();
  await page.context().addCookies([{ name: 'csrf_token', value: 'csrf-local', url: origin }]);
  await page.route('**/auth/validate', (r) => json(r, { user_id: 1, dashboard_url: '/painel' }));
  await page.route((u) => /^\/(painel|home)$/.test(u.pathname), (r) => r.fulfill({ contentType: 'text/html', body: '<h1>Destino</h1>' }));
  await Promise.all([page.waitForURL(`${origin}/painel`), page.goto(`${origin}/login.html`)]);
  await page.close();
});

test('controle positivo: o login por e-mail segue postando /auth/login e não toca em /auth/leitura/login', async () => {
  const { page, posts } = await abre('desktop');
  await page.fill('#email', 'ana@example.com');
  await page.fill('#senha', 'senha-forte');
  await Promise.all([page.waitForURL(`${origin}/painel`), page.click('#btn-login')]);
  assert.deepEqual(posts, [{ caminho: '/auth/login', corpo: { email: 'ana@example.com', password: 'senha-forte' } }]);
  await page.close();
});
