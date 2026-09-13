import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chromium } from 'playwright';

const root = new URL('../../frontend/', import.meta.url);
const source = await readFile(new URL('dashboard.html', root), 'utf8');
const panel = source.match(/<section id="agent-chat-panel"[\s\S]*?<\/section>/)[0];
const script = await readFile(new URL('dashboard-agent-chat.js', root), 'utf8');
const css = await readFile(new URL('dashboard.css', root), 'utf8');
let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

async function setup({ budget = 14, active = ['detetive', 'barao'], viewport, holdFirst = false } = {}) {
  const page = await browser.newPage({ viewport: viewport || { width: 1280, height: 900 } });
  const requests = [];
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const names = { detetive: 'Detetive', barao: 'Barão', xerife: 'Xerife' };
  await page.route('https://agents.test/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/chat')) {
      const body = route.request().postDataJSON();
      requests.push({ path, ...body });
      if (holdFirst && requests.length === 1) await pending;
      if (body.message === 'falhar') return route.fulfill({ status: 503, json: { detail: { error: 'unavailable', message: 'Tente novamente; cota preservada.' } } });
      return route.fulfill({ json: {
        reply: body.message === 'Analisar cobranças' ? 'Encontrei duas cobranças de R$ 49,90 para o mesmo serviço, no mesmo dia. Isso é um indício de duplicidade. Você reconhece duas compras nesse valor?' : 'Podemos avaliar essas cobranças. <img src=x onerror=alert(1)>', context: `contexto-${requests.length}`,
        usage: { used: requests.length, limit: 100 },
        redirects: body.message.includes('CDI') ? [{ kind: 'barao', name: 'Barão', question: 'O que é CDI?', access: 'ready' }] : [],
      } });
    }
    if (path.endsWith('/activate')) {
      active.push(path.split('/')[3]);
      return route.fulfill({ json: { ok: true } });
    }
    if (path === '/agents/42') return route.fulfill({ json: {
      energy_enabled: true, energy_budget: budget, energy_used: active.length * 3,
      can_activate: budget > 0, catalog: Object.entries(names).map(([kind, nome]) => ({
        kind, nome, disponivel: true, status: active.includes(kind) ? 'active' : null,
        energy_cost: 3, desc: 'Investiga assinaturas, cobranças recorrentes e lançamentos possivelmente duplicados.',
      })),
    } });
    if (path === '/chat.js') return route.fulfill({ contentType: 'application/javascript', body: script });
    if (['/dashboard-mobile.css', '/phosphor.css', '/fonts/Phosphor.woff2'].includes(path)) return route.fulfill({ contentType: path.endsWith('.css') ? 'text/css' : 'font/woff2', body: await readFile(new URL(path.slice(1), root)) });
    if (path === '/dashboard.css') return route.fulfill({ contentType: 'text/css', body: css });
    if (path.startsWith('/brand/agents/')) {
      const file = new URL(`brand/agents/${path.split('/').pop()}`, root);
      return route.fulfill({ contentType: 'image/png', body: await readFile(file) });
    }
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/dashboard.css"><link rel="stylesheet" href="/dashboard-mobile.css" media="(max-width:900px)"><link rel="stylesheet" href="/phosphor.css"><body><div id="agentes-shelf"><button id="open" data-agent-chat="detetive">Conversar com Detetive</button></div>${panel}<script>
      const API=''; const USER_ID=42; let _agentesCache=null;
      function csrfHeaders(h={}){return h;}
      function _agentName(k){return k;}
      function navigateTo(){}
      async function loadAgentesView(){}
      function showUpgradeModal(){window.upgradeOpened=true;}
      </script><script src="/chat.js"></script></body>` });
  });
  await page.goto('https://agents.test/');
  await page.click('#open');
  await page.waitForFunction(() => !document.getElementById('agent-chat-status').textContent.includes('Verificando'));
  return { page, requests, errors, release };
}

async function ask(page, text) {
  await page.fill('#agent-chat-input', text);
  await page.click('#agent-chat-send');
  await page.waitForFunction(() => !document.getElementById('agent-chat-input').disabled);
}

test('mantém contexto ao reabrir, separa agentes e limpa no reload', async () => {
  const { page, requests, errors } = await setup();
  try {
    await ask(page, 'Duplicidades?');
    assert.equal(requests[0].context, null);
    assert.equal(await page.locator('#agent-chat-log img').count(), 0, 'resposta precisa ser texto, nunca HTML');
    await page.click('#agent-chat-close');
    await page.click('#open');
    assert.equal(await page.locator('.agent-chat-message').count(), 2);
    await ask(page, 'E as assinaturas?');
    assert.equal(requests[1].context, 'contexto-1');
    await page.evaluate(() => openAgentChat('barao'));
    await ask(page, 'Explique renda fixa');
    assert.equal(requests[2].context, null);
    await page.reload();
    await page.click('#open');
    assert.equal(await page.locator('.agent-chat-message').count(), 0);
    await ask(page, 'Nova conversa');
    assert.equal(requests[3].context, null);
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
});

test('encaminha pergunta preenchida sem envio automático nem histórico', async () => {
  const { page, requests } = await setup();
  try {
    await ask(page, 'Duplicidades e CDI');
    await page.getByRole('button', { name: 'Conversar com Barão' }).click();
    await page.waitForFunction(() => !document.getElementById('agent-chat-input').disabled);
    assert.equal(await page.inputValue('#agent-chat-input'), 'O que é CDI?');
    assert.equal(requests.length, 1);
    await page.click('#agent-chat-send');
    await page.waitForFunction(() => !document.getElementById('agent-chat-input').disabled);
    assert.equal(requests[1].context, null);
    assert.match(requests[1].path, /barao/);
  } finally { await page.close(); }
});

test('falha preserva pergunta e contexto para tentar novamente', async () => {
  const { page, requests } = await setup();
  try {
    await ask(page, 'Primeira');
    await ask(page, 'falhar');
    assert.equal(await page.inputValue('#agent-chat-input'), 'falhar');
    assert.match(await page.textContent('#agent-chat-status'), /cota preservada/);
    assert.equal(await page.locator('.agent-chat-message').count(), 2);
    await ask(page, 'Segunda');
    assert.equal(requests[2].context, 'contexto-1');
  } finally { await page.close(); }
});

test('ativa agente inativo; sem energia apresenta upsell', async () => {
  const first = await setup({ active: [] });
  try {
    await first.page.getByRole('button', { name: 'Ativar e conversar', exact: true }).click();
    await first.page.waitForFunction(() => !document.getElementById('agent-chat-input').disabled);
    await ask(first.page, 'Duplicidades?');
    assert.equal(first.requests.length, 1);
  } finally { await first.page.close(); }
  const second = await setup({ budget: 4, active: ['barao'] });
  try {
    assert.equal(await second.page.isDisabled('#agent-chat-input'), true);
    assert.match(await second.page.textContent('#agent-chat-actions'), /Ver opções de plano/, await second.page.locator('#agent-chat-panel').innerHTML());
    await second.page.getByRole('button', { name: 'Ver opções de plano' }).click();
    assert.equal(await second.page.evaluate(() => window.upgradeOpened), true);
    assert.equal(second.requests.length, 0);
  } finally { await second.page.close(); }
});

test('painel utilizável em desktop e celular, tema claro e escuro', async () => {
  for (const [name, viewport, light] of [
    ['desktop', { width: 1280, height: 900 }, false],
    ['mobile', { width: 390, height: 844 }, true],
  ]) {
    const { page } = await setup({ viewport });
    try {
      if (light) await page.evaluate(() => document.body.classList.add('light'));
      await ask(page, 'Analisar cobranças');
      const bounds = await page.locator('#agent-chat-panel').boundingBox();
      assert.ok(bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= viewport.width);
      assert.ok(bounds.y + bounds.height <= viewport.height);
      assert.equal(await page.locator('#agent-chat-send').isVisible(), true);
      await page.screenshot({ path: `/private/tmp/agent-chat-${name}.png` });
      await page.press('#agent-chat-input', 'Escape');
      assert.equal(await page.locator('#agent-chat-panel').isHidden(), true);
      assert.equal(await page.evaluate(() => document.activeElement.id), 'open');
    } finally { await page.close(); }
  }
});


test('resposta em andamento fica no agente de origem ao trocar de chat', async () => {
  const { page, release, errors } = await setup({ holdFirst: true });
  try {
    await page.fill('#agent-chat-input', 'Minha primeira pergunta');
    await page.click('#agent-chat-send');
    await page.evaluate(() => openAgentChat('barao'));
    await page.fill('#agent-chat-input', 'Rascunho do Barão');
    release();
    await page.waitForTimeout(100);
    assert.equal(await page.locator('.agent-chat-message').count(), 0);
    assert.equal(await page.inputValue('#agent-chat-input'), 'Rascunho do Barão');
    await page.evaluate(() => openAgentChat('detetive'));
    await page.waitForFunction(() => document.querySelectorAll('.agent-chat-message').length === 2);
    assert.deepEqual(errors, []);
  } finally { release(); await page.close(); }
});
