import { test } from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { setup, ask, screenshots } from './agent_chat_fixture.mjs';

test('fixture mantém o botão de abrir agente clicável com menu expandido', async () => {
  const { page } = await setup({ openAgent: false });
  try {
    await page.mouse.move(32, 145);
    await page.waitForFunction(() => Math.round(document.getElementById('sidenav').getBoundingClientRect().width) === 260);
    assert.equal(await page.locator('#open').evaluate(button => {
      const rect = button.getBoundingClientRect();
      return document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2) === button;
    }), true);
  } finally { await page.close(); }
});

test('mantém contexto ao reabrir, separa agentes e limpa no reload', async () => {
  const { page, requests, errors } = await setup();
  try {
    await ask(page, 'Duplicidades?');
    assert.equal(requests[0].context, null);
    assert.equal(await page.locator('#agent-chat-log img[src="x"], #agent-chat-log [onerror]').count(), 0, 'resposta não pode executar HTML recebido');
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
    await page.click('#agent-chat-close');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'open',
      'encaminhar e remontar a conversa não pode perder o botão que abriu o painel');
  } finally { await page.close(); }
});

test('falha preserva pergunta e contexto para tentar novamente', async () => {
  const { page, requests } = await setup();
  try {
    await ask(page, 'Primeira');
    await ask(page, 'falhar');
    assert.equal(await page.inputValue('#agent-chat-input'), 'falhar');
    assert.match(await page.locator('.agent-chat-assistant[data-state="error"]').textContent(), /cota preservada/);
    assert.equal(await page.locator('.agent-chat-message').count(), 4);
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

test('chat dos agentes ocupa a página em desktop e celular, tema claro e escuro', async () => {
  for (const [name, viewport, light] of [
    ['desktop-escuro', { width: 1280, height: 900 }, false],
    ['desktop-claro', { width: 1280, height: 900 }, true],
    ['mobile-escuro', { width: 390, height: 844 }, false],
    ['mobile-claro', { width: 390, height: 844 }, true],
  ]) {
    const { page } = await setup({ viewport, failAt: [2], failureDetail: {
      error: 'model_timeout', message: 'O agente demorou para responder. Tente novamente.', retryable: true,
    } });
    try {
      if (light) await page.evaluate(() => document.body.classList.add('light'));
      await page.locator('#agent-chat-panel').screenshot({ path: join(screenshots, `agent-chat-empty-${name}.png`) });
      await ask(page, 'Analisar cobranças');
      const bounds = await page.locator('#agent-chat-panel').boundingBox();
      assert.equal(bounds.x, 0);
      assert.equal(bounds.y, 0);
      assert.equal(bounds.width, viewport.width);
      assert.equal(bounds.height, viewport.height);
      assert.equal(await page.locator('html').evaluate(el => el.classList.contains('pb-agent-chat-open')), true);
      assert.equal(await page.locator('#agentes-shelf').evaluate(el => el.inert), true);
      assert.equal(await page.locator('#agent-chat-send').isVisible(), true);
      await page.locator('#agent-chat-panel').screenshot({ path: join(screenshots, `agent-chat-${name}.png`) });
      await ask(page, 'Há mais algum indício de duplicidade?');
      await page.locator('#agent-chat-panel').screenshot({ path: join(screenshots, `agent-chat-${name}-erro.png`) });
      assert.equal(await page.getByRole('button', { name: 'Tentar novamente', exact: true }).isVisible(), true);
      assert.equal(await page.locator('#agent-chat-log').evaluate(el => el.scrollWidth <= el.clientWidth), true);
      const contrast = await page.locator('#agent-chat-panel').evaluate(panel => {
        function rgba(css) {
          const values = css.match(/[\d.]+/g).map(Number);
          const channels = css.startsWith('color(') ? values.slice(0, 3).map(n => n * 255) : values.slice(0, 3);
          return [...channels, values[3] ?? 1];
        }
        function over(color, base) {
          return color.slice(0, 3).map((n, i) => n * color[3] + base[i] * (1 - color[3]));
        }
        function luminance(rgb) {
          const linear = rgb.map(n => n / 255 <= 0.04045 ? n / 255 / 12.92 : ((n / 255 + 0.055) / 1.055) ** 2.4);
          return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
        }
        const pageColor = rgba(getComputedStyle(panel).backgroundColor);
        return [...panel.querySelectorAll('.agent-chat-message .pc-message-content, .agent-chat-message .pc-message-author')].map(el => {
          const bubbleStyle = getComputedStyle(el.parentElement);
          const gradientStops = bubbleStyle.backgroundImage.match(/rgba?\([^)]+\)/g);
          const backgrounds = gradientStops || [bubbleStyle.backgroundColor];
          return Math.min(...backgrounds.map(color => {
            const background = over(rgba(color), pageColor);
            const foreground = over(rgba(getComputedStyle(el).color), background);
            const [low, high] = [luminance(background), luminance(foreground)].sort((a, b) => a - b);
            return (high + 0.05) / (low + 0.05);
          }));
        });
      });
      assert.ok(contrast.length >= 4, 'a medição precisa alcançar conteúdo e autoria das bolhas reais');
      assert.ok(Math.min(...contrast) >= 4.5, `${name}: contraste mínimo ${Math.min(...contrast)}`);
      await page.press('#agent-chat-input', 'Escape');
      assert.equal(await page.locator('#agent-chat-panel').isHidden(), true);
      assert.equal(await page.locator('#agentes-shelf').evaluate(el => el.inert), false);
      assert.equal(await page.evaluate(() => document.activeElement.id), 'open');
    } finally { await page.close(); }
  }
});

test('chat reutiliza o menu lateral do dashboard com expansão e navegação', async () => {
  const { page, errors } = await setup({ viewport: { width: 1280, height: 900 } });
  try {
    await page.mouse.move(900, 500);
    const sidebar = page.locator('#sidenav');
    assert.equal(await sidebar.evaluate(el => el.inert), false);
    assert.equal(await page.locator('#agent-chat-panel .pc-agent-rail').count(), 0);
    await page.waitForFunction(() => Math.round(document.getElementById('sidenav').getBoundingClientRect().width) === 68);
    assert.equal(Math.round((await sidebar.boundingBox()).width), 68);
    assert.equal(Math.round((await page.locator('.pc-agent-page').boundingBox()).x), 68);
    assert.ok(Number(await sidebar.evaluate(el => getComputedStyle(el).zIndex)) > Number(await page.locator('#agent-chat-panel').evaluate(el => getComputedStyle(el).zIndex)));
    assert.match(await sidebar.locator('[data-nav="agentes"]').getAttribute('class'), /\bactive\b/);
    await page.screenshot({ path: join(screenshots, 'agent-sidebar-collapsed.png') });
    await sidebar.locator('[data-nav="overview"] .sn-icon').hover();
    await page.waitForFunction(() => Math.round(document.getElementById('sidenav').getBoundingClientRect().width) === 260);
    assert.equal(await sidebar.locator('[data-nav="overview"] .sn-label').evaluate(el => getComputedStyle(el).opacity), '1');
    await page.screenshot({ path: join(screenshots, 'agent-sidebar-expanded.png') });
    await sidebar.locator('[data-nav="overview"]').click();
    assert.equal(await page.evaluate(() => window.lastNavigation), 'overview');
    assert.equal(await page.locator('#agent-chat-panel').isHidden(), true);
    assert.equal(await sidebar.evaluate(el => el.inert), false);
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
});

test('menu lateral não cobre o chat de agentes no celular', async () => {
  const { page } = await setup({ viewport: { width: 390, height: 844 }, hasTouch: true });
  try {
    assert.equal(await page.locator('#sidenav').evaluate(el => el.inert), true);
    assert.ok((await page.locator('#sidenav').boundingBox()).x < 0);
    assert.equal((await page.locator('#agent-chat-panel').boundingBox()).width, 390);
    await page.screenshot({ path: join(screenshots, 'agent-sidebar-mobile.png') });
  } finally { await page.close(); }
});

test('chat não reserva espaço para menu lateral indisponível', async () => {
  for (const options of [
    { viewport: { width: 820, height: 900 } },
    { viewport: { width: 1280, height: 900 }, hasTouch: true },
  ]) {
    const { page } = await setup(options);
    try {
      assert.equal(await page.locator('#sidenav').evaluate(el => el.inert), true);
      assert.equal((await page.locator('.pc-agent-page').boundingBox()).x, 0);
      assert.equal((await page.locator('.pc-agent-page').boundingBox()).width, options.viewport.width);
    } finally { await page.close(); }
  }
  const { page } = await setup({ viewport: { width: 1280, height: 900 } });
  try {
    await page.evaluate(() => {
      PigBankChatUI.close('agent');
      document.documentElement.classList.add('pb-app');
      openAgentChat('detetive');
    });
    assert.equal(await page.locator('#sidenav').evaluate(el => el.inert), true);
    assert.equal((await page.locator('.pc-agent-page').boundingBox()).x, 0);
  } finally { await page.close(); }
});

test('ações auxiliares da barra lateral não fecham a conversa', async () => {
  const { page } = await setup({ viewport: { width: 1280, height: 900 } });
  try {
    await page.evaluate(() => {
      window.toggleTheme = () => { window.themeToggled = true; };
      window.connectWhatsAppFromDashboard = () => { window.whatsAppOpened = true; };
    });
    await page.locator('#theme-toggle-btn').click();
    assert.equal(await page.evaluate(() => window.themeToggled), true);
    assert.equal(await page.locator('#agent-chat-panel').isVisible(), true);
    await page.locator('#sidenav button:has(.ph-whatsapp-logo)').click();
    assert.equal(await page.evaluate(() => window.whatsAppOpened), true);
    assert.equal(await page.locator('#agent-chat-panel').isVisible(), true);
    await page.locator('#sidenav a[href="/suporte"]').evaluate(link => {
      link.addEventListener('click', event => event.preventDefault(), { once: true });
      link.click();
    });
    assert.equal(await page.locator('#agent-chat-panel').isVisible(), true);
  } finally { await page.close(); }
});

test('barra lateral usa o modo atual da janela ao fechar a conversa', async () => {
  for (const [initialWidth, finalWidth, expectedInert] of [
    [820, 1280, false],
    [1280, 820, true],
  ]) {
    const { page } = await setup({ viewport: { width: initialWidth, height: 900 } });
    try {
      await page.setViewportSize({ width: finalWidth, height: 900 });
      await page.waitForFunction(width => innerWidth === width, finalWidth);
      await page.locator('#agent-chat-close').click();
      assert.equal(await page.locator('#sidenav').evaluate(el => el.inert), expectedInert);
    } finally { await page.close(); }
  }
});

test('item bloqueado do menu mostra upgrade sem deixar modal atrás do chat', async () => {
  const { page } = await setup({ lockedUpgrade: true, viewport: { width: 1280, height: 900 } });
  try {
    await page.locator('#sidenav [data-nav="investments"]').click();
    assert.equal(await page.evaluate(() => window.upgradeOpened), true);
    assert.equal(await page.locator('#agent-chat-panel').isHidden(), true);
    assert.equal(await page.locator('#upgrade-overlay').evaluate(el => el.inert), false);
    assert.equal(await page.locator('#upgrade-overlay').evaluate(el => {
      const center = document.elementFromPoint(innerWidth / 2, innerHeight / 2);
      return center === el;
    }), true);
  } finally { await page.close(); }
});

test('sugestões são perguntas dos agentes e trocam de agente sem envio', async () => {
  const { page, requests, errors } = await setup({ active: ['detetive', 'barao', 'xerife'] });
  try {
    await page.getByRole('button', { name: 'Detetive · Lançamentos duplicados?' }).click();
    assert.equal(await page.inputValue('#agent-chat-input'), 'Há lançamentos que parecem duplicados?');
    await page.getByRole('button', { name: 'Xerife · Gasto incomum?' }).click();
    await page.waitForFunction(() => !document.getElementById('agent-chat-input').disabled);
    assert.equal(await page.inputValue('#agent-chat-input'), 'Algum gasto fugiu do meu padrão?');
    assert.equal(await page.textContent('#agent-chat-title'), 'Xerife');
    assert.equal(requests.length, 0);
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
});

test('compositor rosado cresce e avaliação da resposta fica na sessão', async () => {
  const { page, requests } = await setup();
  try {
    await page.fill('#agent-chat-input', 'Analisar cobranças');
    await page.waitForFunction(() => getComputedStyle(document.querySelector('.pc-agent-compose')).boxShadow.includes('255, 45, 142'));
    const shadow = await page.locator('.pc-agent-compose').evaluate(el => getComputedStyle(el).boxShadow);
    assert.match(shadow, /255, 45, 142/);
    await page.click('#agent-chat-send');
    await page.waitForFunction(() => !document.getElementById('agent-chat-input').disabled);
    await page.getByRole('button', { name: 'Não ajudou' }).click();
    assert.match(await page.locator('.pc-agent-response-feedback').textContent(), /Obrigado pelo retorno nesta conversa/);
    await page.click('#agent-chat-close');
    await page.click('#open');
    assert.match(await page.locator('.pc-agent-response-feedback').textContent(), /Obrigado pelo retorno nesta conversa/);
    assert.equal(requests.length, 1);
  } finally { await page.close(); }
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
    await page.waitForFunction(() => document.querySelectorAll('.agent-chat-assistant[data-state="complete"]').length === 1);
    assert.deepEqual(errors, []);
  } finally { release(); await page.close(); }
});

// A entrega manual elimina timers: cada abertura só termina quando o teste
// libera sua resposta, inclusive quando a abertura mais antiga termina por último.
async function controlAccess(page) {
  let receive;
  await page.route('https://agents.test/agents/42', route => receive(route));
  return async (kind, name) => {
    const pending = new Promise(resolve => { receive = resolve; });
    await page.evaluate(({ kind, name }) => {
      window[name] = openAgentChat(kind);
    }, { kind, name });
    return pending;
  };
}

function accessSnapshot({ available = true, budget = 14 } = {}) {
  return {
    energy_enabled: true, energy_budget: budget, energy_used: 6,
    can_activate: budget > 0,
    catalog: ['detetive', 'barao'].map(kind => ({
      kind, nome: kind, disponivel: available, status: 'active', energy_cost: 3,
    })),
  };
}

for (const previous of ['falha HTTP', 'falha de rede', 'sucesso bloqueado']) {
  test(`abertura antiga com ${previous} não invalida acesso e envio após reabrir`, async () => {
    const { page, requests } = await setup();
    try {
      await page.fill('#agent-chat-input', 'Rascunho preservado');
      const open = await controlAccess(page);
      const older = await open('detetive', 'olderOpening');
      await page.click('#agent-chat-close');
      const newer = await open('detetive', 'newerOpening');
      await newer.fulfill({ json: accessSnapshot() });
      await page.evaluate(() => window.newerOpening);
      if (previous === 'falha de rede') await older.abort('failed');
      else await older.fulfill(previous === 'falha HTTP'
        ? { status: 503, json: {} }
        : { json: accessSnapshot({ budget: 0 }) });
      await page.evaluate(() => window.olderOpening);

      assert.equal(await page.isDisabled('#agent-chat-input'), false);
      assert.equal(await page.inputValue('#agent-chat-input'), 'Rascunho preservado');
      assert.equal(await page.evaluate(() => _agentesCache.energy_budget), 14,
        'a resposta antiga não pode regredir o catálogo compartilhado do dashboard');
      const sent = page.waitForRequest(request => request.url().endsWith('/chat'), { timeout: 1000 });
      await page.click('#agent-chat-send');
      await sent;
      await page.waitForFunction(() => document.querySelectorAll('.agent-chat-assistant[data-state="complete"]').length === 1);
      assert.equal(requests[0].message, 'Rascunho preservado');
    } finally { await page.close(); }
  });
}

for (const latest of ['falha', 'sem acesso']) {
  test(`sucesso antigo não desfaz ${latest} da abertura mais recente`, async () => {
    const { page } = await setup();
    try {
      const open = await controlAccess(page);
      const older = await open('detetive', 'olderOpening');
      await page.click('#agent-chat-close');
      const newer = await open('detetive', 'newerOpening');
      await newer.fulfill(latest === 'falha'
        ? { status: 503, json: {} }
        : { json: accessSnapshot({ budget: 0 }) });
      await page.evaluate(() => window.newerOpening);
      const expectedStatus = await page.textContent('#agent-chat-status');
      await older.fulfill({ json: accessSnapshot() });
      await page.evaluate(() => window.olderOpening);
      assert.equal(await page.isDisabled('#agent-chat-input'), true);
      assert.equal(await page.textContent('#agent-chat-status'), expectedStatus);
      assert.equal(await page.evaluate(() => _agentesCache.energy_budget), latest === 'falha' ? 14 : 0);

      // Força um novo render pela tentativa de submit via Enter. Mesmo que o
      // controle pareça desabilitado, o estado de acesso também precisa negar.
      await page.evaluate(() => {
        document.getElementById('agent-chat-input').value = 'Não enviar';
        document.getElementById('agent-chat-form').dispatchEvent(new Event('submit', { cancelable: true }));
      });
      assert.equal(await page.locator('.agent-chat-message').count(), 0);
    } finally { await page.close(); }
  });
}

for (const active of [true, false]) {
  test(`Free legado ${active ? 'ativo' : 'inativo'} oferece plano sem ativar ou enviar`, async () => {
    const { page, requests, activations } = await setup({
      active: active ? ['detetive'] : [],
      accessOverride: { energy_enabled: false, can_activate: true, can_chat: false },
    });
    try {
      assert.equal(await page.isDisabled('#agent-chat-input'), true);
      assert.equal(await page.getByRole('button', { name: 'Ativar e conversar', exact: true }).count(), 0);
      await page.getByRole('button', { name: 'Ver opções de plano' }).click();
      assert.equal(await page.evaluate(() => window.upgradeOpened), true);
      assert.deepEqual(requests, []);
      assert.deepEqual(activations, []);
    } finally { await page.close(); }
  });
}

test('trocar de agente descarta catálogo antigo sem perder o rascunho atual', async () => {
  const { page, requests } = await setup();
  try {
    const open = await controlAccess(page);
    const older = await open('detetive', 'olderOpening');
    const newer = await open('barao', 'newerOpening');
    await newer.fulfill({ json: accessSnapshot() });
    await page.evaluate(() => window.newerOpening);
    await page.fill('#agent-chat-input', 'Refletir sobre renda fixa');
    await older.fulfill({ json: accessSnapshot({ budget: 0 }) });
    await page.evaluate(() => window.olderOpening);
    assert.equal(await page.evaluate(() => _agentesCache.energy_budget), 14);
    assert.equal(await page.inputValue('#agent-chat-input'), 'Refletir sobre renda fixa');
    await page.click('#agent-chat-send');
    await page.waitForFunction(() => document.querySelectorAll('.agent-chat-assistant[data-state="complete"]').length === 1);
    assert.match(requests[0].path, /barao\/chat$/);
    assert.equal(requests[0].context, null);
  } finally { await page.close(); }
});

test('reabrir durante envio preserva resposta e contexto da mesma conversa', async () => {
  const { page, requests, release } = await setup({ holdFirst: true });
  try {
    await page.fill('#agent-chat-input', 'Pergunta em andamento');
    await page.click('#agent-chat-send');
    await page.click('#agent-chat-close');
    await page.evaluate(() => openAgentChat('detetive'));
    assert.equal(await page.isDisabled('#agent-chat-input'), true);
    release();
    await page.waitForFunction(() => document.querySelectorAll('.agent-chat-assistant[data-state="complete"]').length === 1);
    await ask(page, 'Continuar a reflexão');
    assert.equal(requests[1].context, 'contexto-1');
  } finally { release(); await page.close(); }
});
