import { test } from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { setup, ask, screenshots } from './agent_chat_fixture.mjs';

test('envio mostra bolha do agente em andamento e substitui pelo conteúdo da resposta', async () => {
  const { page, release } = await setup({ holdFirst: true });
  try {
    await page.fill('#agent-chat-input', 'Há alguma cobrança duplicada?');
    await page.click('#agent-chat-send');
    await page.locator('#agent-chat-panel').screenshot({ path: join(screenshots, 'chat-pendente.png') });
    assert.equal(await page.locator('.agent-chat-assistant[data-state="pending"]').count(), 1);
    assert.equal(await page.locator('.agent-chat-user').count(), 1);
    assert.equal(await page.isDisabled('#agent-chat-send'), true);
    release();
    await page.waitForFunction(() => !document.getElementById('agent-chat-input').disabled);
    const bubble = page.locator('.agent-chat-assistant[data-state="complete"]');
    assert.equal(await bubble.count(), 1);
    const style = await bubble.evaluate(el => ({ background: getComputedStyle(el).backgroundColor, radius: getComputedStyle(el).borderRadius }));
    assert.notEqual(style.background, 'rgba(0, 0, 0, 0)');
    assert.notEqual(style.radius, '0px');
    assert.equal(await page.locator('.agent-chat-message').count(), 2);
  } finally { release(); await page.close(); }
});

test('falha fica no turno, preserva a pergunta e retry não duplica histórico nem contexto', async () => {
  const { page, requests } = await setup({ failAt: [2], failureDetail: {
    error: 'model_timeout', message: 'O agente demorou para responder. Tente novamente.', retryable: true,
  } });
  try {
    await ask(page, 'Primeira pergunta');
    await ask(page, 'Há alguma cobrança duplicada?');
    await page.locator('#agent-chat-panel').screenshot({ path: join(screenshots, 'chat-falha.png') });
    const failed = page.locator('.agent-chat-assistant[data-state="error"]');
    assert.equal(await failed.count(), 1);
    assert.match(await failed.textContent(), /demorou para responder/);
    assert.equal(await page.locator('.agent-chat-user').count(), 2);
    assert.match(await page.locator('.agent-chat-user').last().textContent(), /cobrança duplicada/);
    assert.doesNotMatch(await page.textContent('#agent-chat-status'), /demorou/);
    await page.fill('#agent-chat-input', 'Um próximo rascunho');
    await failed.getByRole('button', { name: 'Tentar novamente', exact: true }).evaluate(button => { button.click(); button.click(); });
    await page.waitForFunction(() => !document.getElementById('agent-chat-input').disabled);
    assert.equal(requests.length, 3);
    assert.equal(requests[1].context, 'contexto-1');
    assert.equal(requests[2].context, 'contexto-1');
    assert.equal(requests[2].message, requests[1].message);
    assert.equal(await page.locator('.agent-chat-message').count(), 4);
    assert.equal(await page.inputValue('#agent-chat-input'), 'Um próximo rascunho');
    assert.equal(await page.locator('[data-state="error"]').count(), 0);
  } finally { await page.close(); }
});


test('turno falho sobrevive à reabertura e reenviar a mesma pergunta reaproveita a bolha', async () => {
  const { page, requests } = await setup({ failAt: [1] });
  try {
    await ask(page, 'Minha pergunta');
    await page.click('#agent-chat-close');
    await page.evaluate(() => openAgentChat('detetive'));
    assert.equal(await page.locator('.agent-chat-assistant[data-state="error"]').count(), 1);
    assert.equal(await page.inputValue('#agent-chat-input'), 'Minha pergunta');
    await page.click('#agent-chat-send');
    await page.waitForFunction(() => !document.getElementById('agent-chat-input').disabled);
    assert.equal(await page.locator('.agent-chat-message').count(), 2);
    assert.equal(requests[1].context, null);
    await page.reload();
    await page.click('#open');
    assert.equal(await page.locator('.agent-chat-message').count(), 0);
  } finally { await page.close(); }
});

for (const [error, retryable] of [
  ['model_busy', true], ['answer_rejected', true], ['model_configuration_error', false],
  ['quota_exhausted', false], ['quota_unavailable', false], ['internal_error', false],
]) {
  test(`erro ${error} respeita retryable sem repetir automaticamente`, async () => {
    const { page, requests } = await setup({ failAt: [1], failureDetail: {
      error, retryable, request_id: 'test-only', message: 'Não foi possível concluir esta resposta.',
    } });
    try {
      await ask(page, 'Minha pergunta');
      assert.equal(requests.length, 1);
      assert.equal(await page.locator('.agent-chat-assistant[data-state="error"]').count(), 1);
      assert.equal(await page.getByRole('button', { name: 'Tentar novamente', exact: true }).count(), retryable ? 1 : 0);
      assert.equal(await page.locator('.agent-chat-user').count(), 1);
    } finally { await page.close(); }
  });
}

for (const failure of ['rede', 'HTML em vez de JSON']) {
  test(`falha de ${failure} tem recuperação legível e conserva o contexto`, async () => {
    const { page, requests } = await setup();
    try {
      await ask(page, 'Primeira pergunta');
      await page.route('**/chat', route => failure === 'rede'
        ? route.abort('failed')
        : route.fulfill({ status: 503, contentType: 'text/html', body: '<h1>proxy error</h1>' }));
      await ask(page, 'Segunda pergunta');
      const failed = page.locator('.agent-chat-assistant[data-state="error"]');
      const message = await failed.textContent();
      assert.doesNotMatch(message, /SyntaxError|Unexpected|fetch|proxy error|cota não|cota preservada|fora do tema/i);
      assert.match(message, failure === 'rede' ? /conexão foi interrompida/ : /obter a resposta/);
      assert.equal(await page.locator('.agent-chat-user').count(), 2);
      await page.unroute('**/chat');
      await failed.getByRole('button', { name: 'Tentar novamente', exact: true }).click();
      await page.waitForFunction(() => !document.getElementById('agent-chat-input').disabled);
      assert.equal(requests[1].context, 'contexto-1');
      assert.equal(await page.locator('.agent-chat-message').count(), 4);
    } finally { await page.close(); }
  });
}

test('contexto expirado oferece reinício no turno e preserva pergunta sem enviar sozinho', async () => {
  const { page, requests } = await setup({ failAt: [2], failureStatus: 400, failureDetail: {
    error: 'invalid_context', message: 'Essa conversa expirou. Inicie uma nova conversa.', retryable: false,
  } });
  try {
    await ask(page, 'Primeira pergunta');
    await ask(page, 'Uma continuação');
    await page.fill('#agent-chat-input', 'Uma pergunta revisada');
    await page.locator('.agent-chat-assistant[data-state="error"]').getByRole('button', { name: 'Iniciar nova conversa' }).click();
    await page.waitForFunction(() => !document.getElementById('agent-chat-input').disabled);
    assert.equal(await page.inputValue('#agent-chat-input'), 'Uma pergunta revisada');
    assert.equal(requests.length, 2);
    assert.equal(await page.locator('.agent-chat-message').count(), 0);
    await ask(page, 'Uma continuação');
    assert.equal(requests[2].context, null);
  } finally { await page.close(); }
});

test('ativação em andamento informa progresso e desabilita a ação até concluir', async () => {
  const { page } = await setup({ active: [] });
  let release;
  let attempts = 0;
  const pending = new Promise(resolve => { release = resolve; });
  await page.route('**/activate', async route => {
    attempts += 1;
    await pending;
    await route.fulfill({ json: { ok: true } });
  });
  try {
    await page.getByRole('button', { name: 'Ativar e conversar', exact: true }).click();
    assert.match(await page.textContent('#agent-chat-status'), /Ativando agente/);
    const activating = page.getByRole('button', { name: 'Ativando…', exact: true });
    assert.equal(await activating.isDisabled(), true);
    await activating.evaluate(button => button.click());
    assert.equal(attempts, 1);
    release();
    await page.waitForFunction(() => !document.getElementById('agent-chat-input').disabled);
    assert.doesNotMatch(await page.textContent('#agent-chat-status'), /Ativando/);
    assert.equal(await page.locator('.agent-chat-message').count(), 0);
  } finally { release(); await page.close(); }
});
