import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { chromium } from 'playwright';

const frontend = join(dirname(fileURLToPath(import.meta.url)), '../../frontend');
const source = readFileSync(join(frontend, 'dashboard.js'), 'utf8');
const renderAgents = source.slice(source.indexOf('function _renderAgentes(data)'), source.indexOf('async function activateAgent(kind)'));
const chat = readFileSync(join(frontend, 'dashboard-agent-chat.js'), 'utf8');
const styles = readFileSync(join(frontend, 'dashboard.css'), 'utf8');

test('card disponível abre conversa por mouse e teclado sem acionar pausa', async () => {
  const browser = await chromium.launch();
  try {
    for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
      const page = await browser.newPage({ viewport });
      try {
        await page.setContent('<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><div id="agentes-counters"></div><div id="agentes-shelf" class="ag-shelf"></div><div id="agentes-feed"></div>');
        await page.addStyleTag({ content: styles });
        await page.addScriptTag({ content: `const esc = value => String(value); const fmtDate = value => value; let _agentesCache = null; ${renderAgents}` });
        await page.evaluate(() => {
          window.pauseCalls = [];
          window.activateCalls = [];
          window.pauseAgent = kind => window.pauseCalls.push(kind);
          window.activateAgent = kind => window.activateCalls.push(kind);
          window.PigBankChatUI = {};
          _renderAgentes({
            energy_enabled: true, energy_budget: 6, energy_used: 3, summary: { ativos: 1, pausados: 1 },
            catalog: [
              { kind: 'detetive', nome: 'Detetive', desc: 'Encontra cobranças duplicadas.', freq: 'a cada sync', disponivel: true, status: 'active', energy_cost: 3 },
              { kind: 'barao', nome: 'Barão', desc: 'Ajuda com investimentos.', freq: 'mensal', disponivel: true, status: 'paused', energy_cost: 3 },
              { kind: 'aviador', nome: 'Aviador', desc: 'Em breve.', freq: 'contínuo', disponivel: false, status: 'paused', energy_cost: 0 },
            ], events: [],
          });
        });
        await page.addScriptTag({ content: chat });
        await page.evaluate(() => {
          window.chatCalls = [];
          window.openAgentChat = kind => window.chatCalls.push(kind);
        });

        assert.equal(await page.locator('.ag-card-open').count(), 2);
        assert.equal(await page.locator('.ag-card-soon .ag-card-open').count(), 0);
        assert.equal(await page.locator('.ag-chat-btn, [title="Receber os avisos deste agente por e-mail"]').count(), 0);
        assert.doesNotMatch(await page.locator('#agentes-shelf').innerText(), /a cada sync|mensal|contínuo/i);

        const avatar = await page.locator('.ag-card:first-child .ag-avatar').boundingBox();
        await page.mouse.click(avatar.x + avatar.width / 2, avatar.y + avatar.height / 2);
        const open = page.getByRole('button', { name: 'Conversar com Detetive' });
        await open.focus();
        await open.press('Enter');
        await open.press('Space');
        assert.deepEqual(await page.evaluate(() => window.chatCalls), ['detetive', 'detetive', 'detetive']);

        await page.locator('.ag-card:nth-child(2) .ag-desc').scrollIntoViewIfNeeded();
        const description = await page.locator('.ag-card:nth-child(2) .ag-desc').boundingBox();
        const point = { x: description.x + description.width / 2, y: description.y + description.height / 2 };
        const hit = await page.evaluate(({ x, y }) => document.elementFromPoint(x, y)?.className, point);
        assert.equal(hit, 'ag-card-open', `${viewport.width}px: a descrição precisa abrir a conversa`);
        await page.mouse.click(point.x, point.y);
        assert.deepEqual(await page.evaluate(() => window.chatCalls), ['detetive', 'detetive', 'detetive', 'barao']);

        await page.getByRole('button', { name: /Ativo.*Pausar/ }).click();
        await page.getByRole('button', { name: /^Ativar/ }).click();
        assert.deepEqual(await page.evaluate(() => window.pauseCalls), ['detetive']);
        assert.deepEqual(await page.evaluate(() => window.activateCalls), ['barao']);
        assert.equal(await page.evaluate(() => window.chatCalls.length), 4);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      } finally { await page.close(); }
    }
  } finally { await browser.close(); }
});
