import { test } from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { setup, ask, screenshots } from './agent_chat_fixture.mjs';

test('chat dos agentes acompanha o visualViewport quando teclado muda só a área visível', async () => {
  const { page } = await setup({ viewport: { width: 390, height: 844 } });
  try {
    const panel = page.locator('#agent-chat-panel');
    await panel.evaluate(el => {
      el.style.setProperty('--pc-viewport-top', '12px');
      el.style.setProperty('--pc-viewport-height', '420px');
    });
    const bounds = await panel.boundingBox();
    assert.equal(bounds.y, 12);
    assert.equal(bounds.height, 420);
    const send = await page.locator('#agent-chat-send').boundingBox();
    assert.ok(send.y + send.height <= bounds.y + bounds.height, 'envio permanece acima do teclado');
  } finally { await page.close(); }
});

for (const light of [false, true]) {
  test(`agentes ocupam a página e Piggy mantém painel no tema ${light ? 'claro' : 'escuro'}`, async () => {
    const { page } = await setup();
    try {
      if (light) await page.evaluate(() => document.body.classList.add('light'));
      await ask(page, 'Analisar cobranças');
      for (const width of [1280, 390, 320]) {
        await page.setViewportSize({ width, height: 844 });
        for (const mode of ['agent-chat', 'piggy']) {
          await page.evaluate(mode => mode === 'piggy' ? togglePiggy() : openAgentChat('detetive'), mode);
          const panel = page.locator(`#${mode}-panel`);
          await panel.waitFor({ state: 'visible' });
          if (mode === 'piggy' && width === 1280) {
            await page.fill('#piggy-input', 'Resumo do mês de exemplo');
            await page.click('#piggy-send');
            await page.waitForFunction(() => !document.getElementById('piggy-input').disabled);
          }
          const bounds = await panel.boundingBox();
          assert.ok(bounds.x >= -1 && bounds.y >= -1, JSON.stringify(bounds));
          assert.ok(bounds.x + bounds.width <= width + 1 && bounds.y + bounds.height <= 845, JSON.stringify(bounds));
          if (mode === 'agent-chat') {
            assert.ok(Math.abs(bounds.width - width) <= 1, 'agentes ocupam toda a largura');
            assert.ok(Math.abs(bounds.height - 844) <= 1, 'agentes ocupam toda a altura');
            assert.equal(await panel.getAttribute('role'), 'main');
            assert.equal(await panel.getAttribute('aria-modal'), null);
          } else if (width <= 600) {
            assert.ok(Math.abs(bounds.width - width) <= 1, 'no celular a conversa ocupa toda a largura');
            assert.ok(Math.abs(bounds.height - 844) <= 1, 'no celular a conversa ocupa toda a altura');
            assert.equal(await panel.getAttribute('aria-modal'), 'true');
          } else {
            assert.ok(bounds.width < width / 2, 'no desktop a conversa permanece em painel');
            assert.equal(await panel.getAttribute('aria-modal'), 'false');
          }
          const input = page.locator(`#${mode === 'agent-chat' ? 'agent-chat' : 'piggy'}-input`);
          const send = page.locator(`#${mode}-send`);
          assert.equal(await input.isVisible(), true);
          assert.equal(await send.isVisible(), true);
          assert.equal(await panel.evaluate(el => el.scrollWidth <= el.clientWidth), true);
          await panel.screenshot({ path: join(screenshots, `shared-${mode}-${width}-${light ? 'claro' : 'escuro'}.png`) });
        }
      }
    } finally { await page.close(); }
  });
}

test('modal móvel mantém foco e scroll lock na troca, devolvendo-os ao fechar', async () => {
  const { page } = await setup({ viewport: { width: 390, height: 844 } });
  try {
    await page.waitForFunction(() => document.activeElement.id === 'agent-chat-input');
    assert.equal(await page.evaluate(() => getComputedStyle(document.body).overflow), 'hidden');
    await page.press('#agent-chat-input', 'Tab');
    assert.equal(await page.locator('#agent-chat-panel').evaluate(el => el.contains(document.activeElement)), true);
    await page.evaluate(() => togglePiggy());
    assert.equal(await page.evaluate(() => getComputedStyle(document.body).overflow), 'hidden');
    await page.press('#piggy-input', 'Escape');
    assert.equal(await page.locator('#piggy-panel').isHidden(), true);
    assert.notEqual(await page.evaluate(() => getComputedStyle(document.body).overflow), 'hidden');
  } finally { await page.close(); }
});

test('painel permanece utilizável no modo app e após reduzir viewport com teclado', async () => {
  const { page } = await setup({ viewport: { width: 390, height: 844 } });
  try {
    await page.evaluate(() => {
      document.documentElement.classList.add('pb-app', 'pb-root-app');
      document.body.classList.add('pb-page-app');
    });
    await page.fill('#agent-chat-input', 'Rascunho com teclado aberto');
    await page.setViewportSize({ width: 390, height: 420 });
    await page.waitForFunction(() => document.getElementById('agent-chat-panel').getBoundingClientRect().bottom <= window.innerHeight + 1, null, { timeout: 1000 });
    const panel = await page.locator('#agent-chat-panel').boundingBox();
    const composer = await page.locator('#agent-chat-input').boundingBox();
    const send = await page.locator('#agent-chat-send').boundingBox();
    await page.screenshot({ path: join(screenshots, 'chat-app-mode-keyboard.png') });
    assert.ok(panel.y + panel.height <= 421, JSON.stringify({ panel, composer, send }));
    assert.ok(composer.y >= 0 && composer.y + composer.height <= 420);
    assert.ok(send.y >= 0 && send.y + send.height <= 420);
    assert.equal(await page.inputValue('#agent-chat-input'), 'Rascunho com teclado aberto');
    await ask(page, 'Analisar cobranças');
    assert.equal(await page.locator('.agent-chat-assistant[data-state="complete"]').count(), 1);
  } finally { await page.close(); }
});

for (const dynamic of [false, true]) {
  test(`movimento reduzido ${dynamic ? 'ativado após abrir' : 'inicial'} elimina animação sem ocultar resposta`, async () => {
    const { page } = await setup({ reducedMotion: dynamic ? 'no-preference' : 'reduce' });
    try {
      if (dynamic) await page.emulateMedia({ reducedMotion: 'reduce' });
      await ask(page, 'Analisar cobranças');
      const bubble = page.locator('.agent-chat-assistant[data-state="complete"]');
      const animation = await bubble.evaluate(el => ({ duration: getComputedStyle(el).animationDuration, opacity: getComputedStyle(el).opacity, transform: getComputedStyle(el).transform }));
      assert.equal(animation.opacity, '1');
      assert.equal(animation.transform, 'none');
      assert.ok(animation.duration.split(',').every(value => parseFloat(value) <= 0.001), animation.duration);
    } finally { await page.close(); }
  });
}

for (const mode of ['agent-chat', 'piggy']) {
  test(`${mode}: paisagem com toque mantém modal e compositor ao abrir teclado`, async () => {
    const { page } = await setup({ viewport: { width: 844, height: 390 }, hasTouch: true, openAgent: mode === 'agent-chat' });
    try {
      await page.evaluate(() => {
        document.documentElement.classList.add('pb-app', 'pb-root-app');
        document.body.classList.add('pb-page-app');
      });
      if (mode === 'piggy') await page.evaluate(() => togglePiggy());
      await page.fill(`#${mode}-input`, 'Rascunho em paisagem');
      assert.equal(await page.locator(`#${mode}-panel`).getAttribute('aria-modal'), mode === 'piggy' ? 'true' : null);
      for (const height of [390, 220]) {
        await page.setViewportSize({ width: 844, height });
        await page.waitForFunction(({ mode, height }) => {
          const rect = document.getElementById(`${mode}-panel`).getBoundingClientRect();
          return rect.bottom <= height + 1 && Math.abs(rect.height - height) <= 1;
        }, { mode, height }, { timeout: 1000 });
        const panel = await page.locator(`#${mode}-panel`).boundingBox();
        const input = await page.locator(`#${mode}-input`).boundingBox();
        const send = await page.locator(`#${mode}-send`).boundingBox();
        assert.equal(panel.width, 844);
        assert.ok(input.y >= 0 && input.y + input.height <= height);
        assert.ok(send.y >= 0 && send.y + send.height <= height);
        assert.equal(await page.locator(`#${mode}-close`).isVisible(), true);
        assert.equal(await page.inputValue(`#${mode}-input`), 'Rascunho em paisagem');
        await page.screenshot({ path: join(screenshots, `chat-${mode}-paisagem-${height}.png`) });
      }
      await page.click(`#${mode}-send`);
      await page.waitForFunction(mode => !document.getElementById(`${mode}-input`).disabled, mode);
      assert.equal(await page.locator(`#${mode}-panel [data-state="complete"]`).count(), 2);
    } finally { await page.close(); }
  });
}

test('troca de agente reinicia rolagem; reabrir a mesma conversa preserva posição de leitura', async () => {
  const { page } = await setup();
  await page.route('**/agents/42/*/chat', route => route.fulfill({ json: {
    reply: 'Uma explicação longa para leitura.\n'.repeat(65), context: 'contexto-longo', usage: { used: 1, limit: 100 }, redirects: [],
  } }));
  try {
    await page.evaluate(() => openAgentChat('barao'));
    await ask(page, 'Explicar renda fixa');
    await page.evaluate(() => openAgentChat('detetive'));
    await ask(page, 'Explicar recorrências');
    await page.locator('#agent-chat-log').evaluate(log => { log.scrollTop = 0; });
    await page.getByRole('button', { name: 'Ir para as mensagens recentes' }).waitFor({ state: 'visible' });
    await page.click('#agent-chat-close');
    await page.evaluate(() => openAgentChat('detetive'));
    assert.equal(await page.locator('#agent-chat-log').evaluate(log => log.scrollTop), 0);
    assert.equal(await page.getByRole('button', { name: 'Ir para as mensagens recentes' }).isVisible(), true);
    await page.evaluate(() => openAgentChat('barao'));
    await page.waitForFunction(() => {
      const log = document.getElementById('agent-chat-log');
      return log.scrollHeight - log.clientHeight - log.scrollTop < 2;
    });
    assert.equal(await page.getByRole('button', { name: 'Ir para as mensagens recentes' }).count(), 0);
    assert.match(await page.locator('#agent-chat-title').textContent(), /Barão/);
    await page.evaluate(() => openAgentChat('xerife'));
    assert.equal(await page.locator('#agent-chat-log').evaluate(log => log.scrollTop), 0);
    assert.equal(await page.getByRole('button', { name: 'Ir para as mensagens recentes' }).count(), 0);
  } finally { await page.close(); }
});
