import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup, ask } from './agent_chat_fixture.mjs';

async function openPiggy(page) {
  await page.evaluate(() => togglePiggy());
  await page.locator('#piggy-input').waitFor({ state: 'visible' });
}

async function askPiggy(page, text) {
  await page.fill('#piggy-input', text);
  await page.click('#piggy-send');
  await page.waitForFunction(() => !document.getElementById('piggy-input').disabled);
}

test('Piggy e agentes compartilham interface e alternam sem perder mensagens ou rascunhos', async () => {
  const { page, requests, piggyRequests, errors } = await setup();
  try {
    await ask(page, 'Analisar cobranças');
    await page.fill('#agent-chat-input', 'Rascunho do Detetive');
    await openPiggy(page);
    assert.equal(await page.locator('#agent-chat-panel').isHidden(), true);
    await askPiggy(page, 'Resumo do mês');
    await page.fill('#piggy-input', 'Rascunho do Piggy');
    await page.evaluate(() => openAgentChat('detetive'));
    assert.equal(await page.locator('#piggy-panel').isHidden(), true);
    assert.equal(await page.inputValue('#agent-chat-input'), 'Rascunho do Detetive');
    assert.equal(await page.locator('.agent-chat-message').count(), 2);
    await openPiggy(page);
    assert.equal(await page.inputValue('#piggy-input'), 'Rascunho do Piggy');
    assert.equal(await page.locator('.piggy-msg').count(), 2);
    await page.click('#piggy-close');
    await openPiggy(page);
    assert.equal(await page.inputValue('#piggy-input'), 'Rascunho do Piggy');
    assert.equal(requests.length, 1);
    assert.deepEqual(piggyRequests, [{ message: 'Resumo do mês' }]);
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
});

test('Piggy e agentes usam o mesmo padrão de mensagem', async () => {
  const { page } = await setup();
  try {
    await ask(page, 'Analisar cobranças');
    const agentReply = page.locator('.agent-chat-assistant[data-state="complete"]');
    assert.equal(await agentReply.locator('[data-slot="bubble"][data-variant="ghost"]').count(), 1);
    assert.equal(await page.locator('.agent-chat-user [data-slot="bubble"][data-variant="muted"]').count(), 1);
    assert.equal(await page.locator('.agent-chat-user [data-slot="message-footer"] .pc-message-author').textContent(), 'Você');
    assert.equal(await agentReply.locator('[data-slot="message-footer"] time').count(), 1);

    await openPiggy(page);
    await askPiggy(page, 'Resumo do mês');
    const piggyReply = page.locator('.piggy-msg.assistant[data-state="complete"]');
    assert.equal(await piggyReply.locator('[data-slot="bubble"][data-variant="ghost"]').count(), 1);
    assert.equal(await page.locator('.piggy-msg.user [data-slot="bubble"][data-variant="muted"]').count(), 1);
    assert.equal(await page.locator('.piggy-msg.user [data-slot="message-footer"] .pc-message-author').textContent(), 'Você');
    assert.equal(await piggyReply.locator('[data-slot="message-footer"] time').count(), 1);
  } finally { await page.close(); }
});

test('Piggy abre com cota sem misturar histórico externo e reload limpa apenas estado visual', async () => {
  const { page, usageRequests, piggyRequests } = await setup({ openAgent: false });
  try {
    await openPiggy(page);
    await page.waitForFunction(() => document.getElementById('piggy-usage').textContent.includes('81'));
    assert.equal(await page.locator('.piggy-msg').count(), 0);
    assert.doesNotMatch(await page.locator('#piggy-body').textContent(), /Histórico de outro canal/);
    await askPiggy(page, 'Minha primeira pergunta');
    await page.fill('#piggy-input', 'Rascunho temporário');
    await page.reload();
    await openPiggy(page);
    assert.equal(await page.locator('.piggy-msg').count(), 0);
    assert.equal(await page.inputValue('#piggy-input'), '');
    await askPiggy(page, 'Pergunta depois do reload');
    assert.deepEqual(piggyRequests, [{ message: 'Minha primeira pergunta' }, { message: 'Pergunta depois do reload' }]);
    assert.equal(usageRequests.length, 2);
  } finally { await page.close(); }
});

test('resposta tardia do Piggy não abre painel nem sobrescreve conversa do agente', async () => {
  const { page, releasePiggy, piggyRequests } = await setup({ holdPiggy: true, openAgent: false });
  try {
    await openPiggy(page);
    await page.fill('#piggy-input', 'Resumo do mês');
    await page.click('#piggy-send');
    await page.evaluate(() => openAgentChat('detetive'));
    await page.fill('#agent-chat-input', 'Rascunho protegido');
    releasePiggy();
    await page.waitForFunction(() => document.querySelector('.piggy-msg.assistant[data-state="complete"]'));
    assert.equal(await page.locator('#piggy-panel').isHidden(), true);
    assert.equal(await page.inputValue('#agent-chat-input'), 'Rascunho protegido');
    assert.equal(await page.locator('.agent-chat-message').count(), 0);
    await openPiggy(page);
    assert.equal(await page.locator('.piggy-msg').count(), 2);
    assert.equal(piggyRequests.length, 1);
  } finally { releasePiggy(); await page.close(); }
});

for (const failure of ['rede', 'HTML', 'limite']) {
  test(`Piggy mantém falha ${failure} no turno sem repetir possível operação financeira`, async () => {
    const { page } = await setup({ openAgent: false });
    let writes = 0;
    await page.route('**/ai/chat', route => {
      writes += 1;
      if (failure === 'rede') return route.abort('failed');
      if (failure === 'HTML') return route.fulfill({ status: 503, contentType: 'text/html', body: '<h1>upstream error</h1>' });
      return route.fulfill({ status: 429, json: { detail: { error: 'quota_exhausted', message: 'Limite mensal atingido.' } } });
    });
    try {
      await openPiggy(page);
      await askPiggy(page, 'Registre um gasto de R$ 50 no mercado');
      const error = page.locator('.piggy-msg.assistant[data-state="error"]');
      assert.equal(await error.count(), 1);
      assert.doesNotMatch(await error.textContent(), /SyntaxError|upstream|Unexpected|fetch|cota não foi descontada/i);
      assert.equal(await error.getByRole('button', { name: /tentar novamente/i }).count(), 0);
      await page.click('#piggy-close');
      await openPiggy(page);
      await page.waitForTimeout(100);
      assert.equal(writes, 1);
      assert.equal(await page.locator('.piggy-msg.user').count(), 1);
      assert.equal(await page.locator('.piggy-msg.assistant[data-state="error"]').count(), 1);
    } finally { await page.close(); }
  });
}

test('markdown de Piggy renderiza formatação e links seguros sem executar HTML ou esquemas ativos', async () => {
  const reply = '**Resumo** e `saldo`\n[Planos](/precos)\n[Ajuda](https://pigbankai.com/suporte)\n[Executar](javascript:alert%281%29)\n<img src=x onerror="window.pwned=1">\n[Injeção](https://example.com/" onmouseover="window.pwned=2)';
  const { page, errors } = await setup({ openAgent: false, piggyReply: reply });
  try {
    await openPiggy(page);
    await askPiggy(page, 'Resumo seguro');
    const bubble = page.locator('.piggy-msg.assistant[data-state="complete"]');
    assert.match(await bubble.locator('.pc-message-content b, .pc-message-content strong').textContent(), /Resumo/);
    assert.equal(await bubble.locator('code').textContent(), 'saldo');
    assert.equal(await bubble.getByRole('link', { name: 'Planos' }).getAttribute('href'), '/precos');
    assert.equal(await bubble.getByRole('link', { name: 'Ajuda' }).getAttribute('href'), 'https://pigbankai.com/suporte');
    assert.equal(await bubble.locator('a[href^="javascript:"], [onmouseover], [onerror], img[src="x"]').count(), 0);
    assert.equal(await page.evaluate(() => window.pwned), undefined);
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
});

for (const mode of ['Piggy', 'agente']) {
  test(`${mode}: composição IME não envia; Enter envia e Shift+Enter permite outra linha`, async () => {
    const { page, requests, piggyRequests } = await setup({ openAgent: mode === 'agente' });
    const input = mode === 'agente' ? '#agent-chat-input' : '#piggy-input';
    try {
      if (mode === 'Piggy') await openPiggy(page);
      await page.fill(input, 'Texto em composição');
      await page.locator(input).evaluate(el => el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true, cancelable: true })));
      assert.equal(requests.length + piggyRequests.length, 0);
      await page.press(input, 'Shift+Enter');
      assert.equal(requests.length + piggyRequests.length, 0);
      assert.match(await page.inputValue(input), /\n/);
      await page.press(input, 'Enter');
      await page.waitForFunction(() => Boolean(document.querySelector('[data-state="complete"]')));
      assert.equal(requests.length + piggyRequests.length, 1);
    } finally { await page.close(); }
  });
}

test('Piggy respeita plano antes de abrir e não consulta nem envia para usuário sem acesso', async () => {
  const { page, usageRequests, piggyRequests } = await setup({ pro: false, openAgent: false });
  try {
    await page.click('#piggy-fab');
    assert.equal(await page.evaluate(() => window.upgradeOpened), true);
    assert.equal(await page.locator('#piggy-panel').isHidden(), true);
    assert.deepEqual(usageRequests, []);
    assert.deepEqual(piggyRequests, []);
  } finally { await page.close(); }
});
