import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { setImmediate as nextTurn } from 'node:timers/promises';
import vm from 'node:vm';

const source = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../../frontend/dashboard.js'), 'utf8');
const channel = source.slice(source.indexOf('function makeFetchChannel()'), source.indexOf('let filterDebounceTimer'));
const agents = source.slice(source.indexOf('let _agentesCache = null;'), source.indexOf('/* ═══════════════════════════════════════════════════════════════════════\n   PUXAR PRA ATUALIZAR'));

test('pausar agente mantém os cartões e o feed até a atualização terminar', async () => {
  const writes = [];
  const elements = Object.fromEntries(['agentes-shelf', 'agentes-feed', 'agentes-counters'].map(id => {
    let html = `conteúdo anterior: ${id}`;
    return [id, {
      get innerHTML() { return html; },
      set innerHTML(value) { writes.push([id, value]); html = value; },
    }];
  }));
  let finishShelf;
  let finishFeed;
  const shelfResponse = new Promise(resolve => { finishShelf = resolve; });
  const feedResponse = new Promise(resolve => { finishFeed = resolve; });
  const calls = [];
  const context = vm.createContext({
    API: '/api', USER_ID: 1, AbortController,
    document: { getElementById: id => elements[id] },
    fetch: (url, options) => {
      calls.push([url, options]);
      if (url.endsWith('/pause')) return Promise.resolve({ ok: true });
      if (url.endsWith('/feed/seen')) return Promise.resolve({ ok: true });
      if (url.endsWith('/feed?limit=20')) return feedResponse;
      if (url.endsWith('/agents/1')) return shelfResponse;
      throw new Error(`Unexpected request: ${url}`);
    },
    csrfHeaders: () => ({}),
    readResponsePayload: response => response.json(),
    alert: message => { throw new Error(message); },
    esc: value => String(value),
    fmtDate: value => value,
  });
  vm.runInContext(`${channel}\n${agents}`, context);

  const pause = vm.runInContext('pauseAgent("xerife")', context);
  await nextTurn();
  assert.ok(calls.some(([url]) => url.endsWith('/pause')));
  assert.deepEqual(writes, [], 'a atualização não deve apagar o conteúdo durante o GET');

  finishShelf({ ok: true, json: async () => ({ summary: {}, catalog: [] }) });
  finishFeed({ ok: true, json: async () => ({ events: [] }) });
  await pause;
  assert.ok(writes.some(([id]) => id === 'agentes-shelf'), 'a lista deve ser atualizada após o GET');
});
