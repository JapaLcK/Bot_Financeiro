import { before, after } from 'node:test';
import { mkdir, mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { chromium } from 'playwright';

const root = new URL('../../frontend/', import.meta.url);
const source = await readFile(new URL('dashboard.html', root), 'utf8');
const panel = source.match(/<section id="agent-chat-panel"[\s\S]*?<\/section>/)[0];
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

async function setup({ budget = 14, active = ['detetive', 'barao'], viewport, holdFirst = false, accessOverride = {}, failAt = [], failureDetail, failureStatus = 503 } = {}) {
  const page = await browser.newPage({ viewport: viewport || { width: 1280, height: 900 } });
  const requests = [];
  const activations = [];
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
  return { page, requests, errors, release, activations };
}

async function ask(page, text) {
  await page.fill('#agent-chat-input', text);
  await page.click('#agent-chat-send');
  await page.waitForFunction(() => !document.getElementById('agent-chat-input').disabled);
}

export { setup, ask, screenshots };
