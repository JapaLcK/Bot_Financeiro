import { before, after } from 'node:test';
import { mkdir, mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { chromium } from 'playwright';

const root = new URL('../../frontend/', import.meta.url);
const source = await readFile(new URL('dashboard.html', root), 'utf8');
const panel = source.match(/<div id="pigbank-chat-root"><\/div>/)?.[0];
if (!panel) throw new Error('Dashboard não inclui a ilha React do chat.');
const scripts = [...source.matchAll(/<script\b[^>]*src="\/(?:chat-app|dashboard-chat|dashboard-agent-chat)\.js[^"\s]*"[^>]*><\/script>/g)].map(match => match[0]).join('');
const styles = [...source.matchAll(/<link\b[^>]*href="\/(?:dashboard|dashboard-mobile|phosphor|app-mode|chat-app)\.css[^"\s]*"[^>]*>/g)].map(match => match[0]).join('');
const script = await readFile(new URL('dashboard-agent-chat.js', root), 'utf8');
const css = await readFile(new URL('dashboard.css', root), 'utf8');
let browser;
let screenshots;
before(async () => {
  screenshots = process.env.PIGBANK_CHAT_SCREENSHOTS || await mkdtemp(join(tmpdir(), 'pigbank-agent-chat-'));
  await mkdir(screenshots, { recursive: true });
  browser = await chromium.launch();
});
after(async () => {
  await browser?.close();
  if (screenshots && !process.env.PIGBANK_CHAT_SCREENSHOTS) await rm(screenshots, { recursive: true, force: true });
});

async function setup({ budget = 14, active = ['detetive', 'barao'], viewport, holdFirst = false, accessOverride = {}, failAt = [], failureDetail, failureStatus = 503, openAgent = true, piggyReply = "**Seu resumo** está pronto.", holdPiggy = false, pro = true, reducedMotion, hasTouch = false } = {}) {
  const page = await browser.newPage({ viewport: viewport || { width: 1280, height: 900 }, reducedMotion, hasTouch });
  const requests = [];
  const activations = [];
  const piggyRequests = [];
  const usageRequests = [];
  let releasePiggy;
  const piggyPending = new Promise(resolve => { releasePiggy = resolve; });
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const names = { detetive: 'Detetive', barao: 'Barão', xerife: 'Xerife' };
  await page.route('https://agents.test/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/ai/messages') {
      usageRequests.push(path);
      return route.fulfill({ json: { messages: [{ role: 'assistant', content: 'Histórico de outro canal' }], usage: { used: 81, limit: 100 } } });
    }
    if (path === '/ai/chat') {
      piggyRequests.push(route.request().postDataJSON());
      if (holdPiggy) await piggyPending;
      return route.fulfill({ json: { reply: piggyReply, usage: { used: 82, limit: 100 } } });
    }
    if (path.endsWith('/chat')) {
      const body = route.request().postDataJSON();
      requests.push({ path, ...body });
      if (holdFirst && requests.length === 1) await pending;
      if (body.message === 'falhar' || failAt.includes(requests.length)) return route.fulfill({ status: failureStatus, json: { detail: failureDetail || { error: 'unavailable', message: 'Tente novamente; cota preservada.' } } });
      return route.fulfill({ json: {
        reply: body.message === 'Analisar cobranças' ? 'Encontrei duas cobranças de R$ 49,90 para o mesmo serviço, no mesmo dia. Isso é um indício de duplicidade. Você reconhece duas compras nesse valor?' : 'Podemos avaliar essas cobranças. <img src=x onerror=alert(1)>', context: `contexto-${requests.length}`,
        usage: { used: requests.length, limit: 100 },
        redirects: body.message.includes('CDI') ? [{ kind: 'barao', name: 'Barão', question: 'O que é CDI?', access: 'ready' }] : [],
      } });
    }
    if (path.endsWith('/activate')) {
      activations.push(path);
      active.push(path.split('/')[3]);
      return route.fulfill({ json: { ok: true } });
    }
    if (path === '/agents/42') return route.fulfill({ json: {
      energy_enabled: true, energy_budget: budget, energy_used: active.length * 3,
      can_activate: budget > 0, catalog: Object.entries(names).map(([kind, nome]) => ({
        kind, nome, disponivel: true, status: active.includes(kind) ? 'active' : null,
        energy_cost: 3, desc: 'Investiga assinaturas, cobranças recorrentes e lançamentos possivelmente duplicados.',
      })), ...accessOverride,
    } });
    if (path === '/dashboard-agent-chat.js') return route.fulfill({ contentType: 'application/javascript', body: script });
    if (['/chat-app.js', '/dashboard-chat.js'].includes(path)) return route.fulfill({ contentType: 'application/javascript', body: await readFile(new URL(path.slice(1), root)) });
    if (path === '/chat-app.css') return route.fulfill({ contentType: 'text/css', body: await readFile(new URL('chat-app.css', root)) });
    if (['/dashboard-mobile.css', '/app-mode.css', '/phosphor.css', '/fonts/Phosphor.woff2'].includes(path)) return route.fulfill({ contentType: path.endsWith('.css') ? 'text/css' : 'font/woff2', body: await readFile(new URL(path.slice(1), root)) });
    if (path === '/dashboard.css') return route.fulfill({ contentType: 'text/css', body: css });
    if (path.startsWith('/brand/')) {
      const file = new URL(path.slice(1), root);
      return route.fulfill({ contentType: path.endsWith('.webp') ? 'image/webp' : 'image/png', body: await readFile(file) });
    }
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">${styles}<body><button id="piggy-fab" aria-label="Abrir Piggy IA">Piggy</button><div id="agentes-shelf"><button id="open" data-agent-chat="detetive">Conversar com Detetive</button></div>${panel}<script>
      const API=''; const USER_ID=42; let _agentesCache=null;
      function csrfHeaders(h={}){return h;}
      function _agentName(k){return k;}
      function navigateTo(){}
      function isProUser(){return ${pro};}
      async function loadAgentesView(){}
      function showUpgradeModal(){window.upgradeOpened=true;}
      </script>${scripts}</body>` });
  });
  await page.goto('https://agents.test/');
  await page.waitForFunction(() => Boolean(window.PigBankChatUI));
  if (openAgent) {
    await page.click('#open');
    await page.waitForFunction(() => document.getElementById('agent-chat-status') && !document.getElementById('agent-chat-status').textContent.includes('Verificando'));
  }
  return { page, requests, errors, release, activations, piggyRequests, usageRequests, releasePiggy };
}

async function ask(page, text) {
  await page.fill('#agent-chat-input', text);
  await page.click('#agent-chat-send');
  await page.waitForFunction(() => !document.getElementById('agent-chat-input').disabled);
}

export { setup, ask, screenshots };
