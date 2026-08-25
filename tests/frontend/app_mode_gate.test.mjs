/**
 * Porteiro do modo app: QUEM ganha html.pb-app (app-mode.js) e html.pb-safe
 * (safe-area.js).
 *
 * UM invariante: modo app é do APP (UA PigBankApp) e do ÍCONE INSTALADO
 * (standalone), e de mais nada. Nenhum storage e nenhuma query participam da
 * decisão — o preview ?pbapp=1 foi REMOVIDO. Ele gravava localStorage.pbApp
 * pra sempre, sem UI de saída, e travou o Safari de um usuário de verdade;
 * qualquer link velho (histórico, autocomplete, bookmark, WhatsApp) reabria a
 * armadilha. Quem ficou preso se destrava no primeiro load porque o GATE parou
 * de ler a chave — o removeItem que sobrou é higiene, não a saída. E os dois
 * scripts têm que concordar: pb-safe sem pb-app deixa o site com respiro de
 * notch sem motivo.
 *
 * Sem browser: o gate dos dois arquivos é JS puro (storage, navigator,
 * matchMedia, location.search) e só toca no DOM na PRIMEIRA linha depois de
 * decidir — `classList.add`. O stub abaixo registra a classe e estoura um
 * sentinela ali, o que também poda as ~980 linhas de montagem da tab bar.
 * Por isso este teste não precisa do playwright do outro .test.mjs.
 *
 * Rodar:  npm run test:frontend
 *         (ou, um arquivo só: node --test tests/frontend/app_mode_gate.test.mjs
 *          — `node --test tests/frontend/` NÃO funciona no Node 24 do Windows:
 *          ele trata o diretório como módulo e sai com MODULE_NOT_FOUND.)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
const APP_MODE = join(FRONTEND, "app-mode.js");
const SAFE_AREA = join(FRONTEND, "safe-area.js");
const FILES = [[APP_MODE, "pb-app"], [SAFE_AREA, "pb-safe"]];

const SAFARI = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1";
const APP_UA = SAFARI + " PigBankApp/1.0";
/** Versões que o app ainda vai ter: o sufixo mora em capacitor.config.json. */
const APP_UA_NEXT = [SAFARI + " PigBankApp/2.7", SAFARI + " PigBankApp/10.0.3"];

/** Storage do modo privado/cookies bloqueados: qualquer acesso estoura. */
const THROWS = new Proxy({}, {
  get() { throw new Error("SecurityError: storage bloqueado"); },
});

const mapStorage = (m) => ({
  getItem: (k) => (m.has(k) ? m.get(k) : null),
  setItem: (k, v) => m.set(k, String(v)),
  removeItem: (k) => m.delete(k),
});

class Stop extends Error {}

/**
 * Roda `file` num contexto falso e devolve se `cls` foi aplicada.
 * `store`/`session` podem ser reaproveitados entre chamadas pra simular dois
 * loads seguidos no mesmo aparelho/aba.
 */
function run(file, cls, ctx = {}) {
  const {
    ua = SAFARI, stored = null, search = "", displayMode = false, iosStandalone = false,
    store = new Map(), session = new Map(),
    localStorage: lsOverride, sessionStorage: ssOverride,
  } = ctx;
  if (stored !== null) store.set("pbApp", stored);
  const added = [];

  const navigator = { userAgent: ua };
  if (iosStandalone) navigator.standalone = true;
  const sandbox = {
    navigator,
    localStorage: lsOverride || mapStorage(store),
    sessionStorage: ssOverride || mapStorage(session),
    location: { search, pathname: "/app", replace: () => {} },
    matchMedia: (q) => ({ matches: displayMode && q.includes("standalone") }),
    document: {
      documentElement: { classList: { add: (c) => { added.push(c); throw new Stop(); } } },
    },
    URLSearchParams,
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  try {
    vm.runInContext(readFileSync(file, "utf8"), sandbox, { filename: file });
  } catch (e) {
    if (!(e instanceof Stop)) throw e;
  }
  return {
    applied: added.includes(cls),
    pbApp: store.has("pbApp") ? store.get("pbApp") : null,
    store,
    session,
  };
}

const appMode = (ctx) => run(APP_MODE, "pb-app", ctx);
const safeArea = (ctx) => run(SAFE_AREA, "pb-safe", ctx);

test("navegador travado pelo ?pbapp=1 antigo volta pro site e se auto-limpa", () => {
  const r = appMode({ stored: "1" });
  assert.equal(r.applied, false);
  assert.equal(r.pbApp, null, "a chave legada tem que sumir do localStorage");
});

test("navegador limpo continua no site", () => {
  assert.equal(appMode({}).applied, false);
});

test("app iOS entra, mesmo sem storage nenhum e sem query — e sem gravar nada", () => {
  // A segunda metade é o contrário do bug original: re-armar localStorage.pbApp
  // no caminho do app recria a forma exata dele (chave gravada pra sempre, sem
  // UI de saída). Hoje seria inócuo, porque ninguém mais LÊ a chave — e é por
  // ser inócuo que passaria despercebido até alguém religar a leitura.
  for (const [file, cls] of FILES) {
    const r = run(file, cls, { ua: APP_UA });
    assert.equal(r.applied, true, cls);
    assert.equal(r.store.size, 0, `${cls}: o caminho do app não pode gravar no localStorage`);
    assert.equal(r.session.size, 0, `${cls}: nem no sessionStorage`);
  }
});

test("o gatilho é a SUBSTRING PigBankApp: bump de versão não desliga o app", () => {
  // O sufixo do UA (mobile/capacitor.config.json:18) vai mudar, e depois da
  // remoção do preview ele é o ÚNICO gatilho do app nativo: uma regex cravada
  // em "PigBankApp/1.0" derrubaria o layout do app inteiro num bump de versão,
  // em todas as páginas. O servidor já tem esse contrato coberto
  // (shared.py:548 usa `"PigBankApp" in ua`; test_gate_plan_selection.py:82
  // testa com PigBankApp/1.2) — o cliente precisa do mesmo.
  for (const [file, cls] of FILES) {
    for (const ua of APP_UA_NEXT) {
      assert.equal(run(file, cls, { ua }).applied, true, `${cls}: ${ua}`);
    }
  }
});

test("ícone instalado entra pelos dois sinais de standalone", () => {
  assert.equal(appMode({ displayMode: true }).applied, true);
  assert.equal(appMode({ iosStandalone: true }).applied, true);
});

test("storage que estoura em qualquer acesso não derruba o app nativo", () => {
  // Sem o try/catch em volta do localStorage.removeItem, a exceção mata o
  // script antes do classList.add e o app abre com layout de site (nav,
  // footer, sem tab bar). Este é o caso que prova o try/catch — e que trava
  // qualquer volta de leitura de storage sem proteção.
  for (const [file, cls] of FILES) {
    assert.equal(run(file, cls, { ua: APP_UA, localStorage: THROWS }).applied, true,
      `${cls}: localStorage bloqueado`);
    assert.equal(run(file, cls, { ua: APP_UA, sessionStorage: THROWS }).applied, true,
      `${cls}: sessionStorage bloqueado`);
    assert.equal(run(file, cls, { ua: APP_UA, localStorage: THROWS, sessionStorage: THROWS }).applied,
      true, `${cls}: os dois bloqueados`);
  }
});

test("?pbapp=1 no navegador comum NÃO liga o modo app — nem no load seguinte", () => {
  // O preview foi removido de propósito: um link velho (histórico,
  // autocomplete, bookmark, WhatsApp) não pode mais prender uma aba de Safari
  // em modo app. Nem naquele load, nem no próximo — porque nada é gravado.
  for (const [file, cls] of FILES) {
    const store = new Map(), session = new Map();
    assert.equal(run(file, cls, { search: "?pbapp=1", store, session }).applied, false,
      `${cls}: load com ?pbapp=1`);
    assert.equal(store.size, 0, `${cls}: não pode gravar no localStorage`);
    assert.equal(session.size, 0, `${cls}: não pode gravar no sessionStorage`);
    assert.equal(run(file, cls, { search: "", store, session }).applied, false,
      `${cls}: load seguinte, sem query`);

    // Aba que ficou armada pela versão ANTERIOR (sessionStorage.pbApp) também
    // não vale mais: o iOS restaura sessionStorage quando a aba volta.
    assert.equal(run(file, cls, { search: "", session: new Map([["pbApp", "1"]]) }).applied, false,
      `${cls}: sessão armada pela versão antiga`);
  }
});

test("safe-area decide igual ao app-mode em todo contexto", () => {
  // As queries pbapp e as chaves pbApp aqui são SOBRAS DE MUNDO, não features:
  // link velho no histórico/WhatsApp, aparelho travado pela versão antiga. O
  // ?pbapp=1 não liga nada e o ?pbapp=0 não desliga nada (não há mais o que
  // desligar) — entram na lista só pra provar que os DOIS arquivos ignoram
  // cada uma delas do mesmo jeito, hoje e depois de qualquer refactor.
  const CTXS = [
    {}, { stored: "1" }, { ua: APP_UA }, { displayMode: true }, { iosStandalone: true },
    { search: "?pbapp=1" }, { search: "?pbapp=0" },
    { session: new Map([["pbApp", "1"]]) },
    { stored: "1", search: "?pbapp=1", session: new Map([["pbApp", "1"]]) },
    { ua: APP_UA, localStorage: THROWS, sessionStorage: THROWS },
  ];
  for (const [i, ctx] of CTXS.entries()) {
    // Storage fresco por chamada é higiene, não necessidade: o app-mode escreve
    // storage (removeItem da chave legada, app-mode.js:35) mas o safe-area não
    // LÊ storage nenhum, então não há o que um vazar pro outro. A cópia só
    // mantém as duas chamadas independentes se algum dia voltarem a ler.
    const a = appMode({ ...ctx, store: new Map(ctx.store), session: new Map(ctx.session) });
    const b = safeArea({ ...ctx, store: new Map(ctx.store), session: new Map(ctx.session) });
    // Só campos escalares na mensagem: JSON.stringify(ctx) estoura no Proxy de
    // storage da lista.
    const label = `${Object.keys(ctx).join()} search=${JSON.stringify(ctx.search ?? "")}`
      + ` ua=${/PigBankApp/.test(ctx.ua ?? "") ? "PigBankApp" : "Safari"}`;
    assert.equal(b.applied, a.applied, `contexto #${i}: ${label}`);
  }
});
