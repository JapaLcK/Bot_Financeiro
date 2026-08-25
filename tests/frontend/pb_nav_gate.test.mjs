/**
 * Porteiro do motor SPA experimental (pb-nav.js): QUEM liga o pbspa.
 *
 * UM invariante: o atalho ?pbspa=1 só vale DENTRO do modo app (html.pb-app) e
 * só pela sessão (sessionStorage). Fora do modo app a query não liga nada e —
 * o que importa — não GRAVA nada: era assim que o link de QA
 * /home?pbspa=1 aberto no navegador deixava a flag armada no aparelho pra
 * sempre, sem UI de saída, e o motor experimental acordava sozinho no próximo
 * abrir do app. A chave pbSpa legada do localStorage não liga mais nada e é
 * apagada em todo load.
 *
 * Arquivo separado do app_mode_gate porque o formato é outro: o app-mode
 * decide com um classList.add (sentinela), o pb-nav decide num const `enabled`
 * exposto em window.PBNav. Aqui o IIFE roda inteiro — no load ele só lê
 * storage/navigator e registra handlers; nada de DOM até um boot()/tap.
 *
 * Rodar:  npm run test:frontend
 *         (ou, um arquivo só: node --test tests/frontend/pb_nav_gate.test.mjs
 *          — `node --test tests/frontend/` NÃO funciona no Node 24 do Windows:
 *          ele trata o diretório como módulo e sai com MODULE_NOT_FOUND.)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const PB_NAV = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend", "pb-nav.js");

/** Storage do modo privado/cookies bloqueados: qualquer acesso estoura. */
const THROWS = new Proxy({}, {
  get() { throw new Error("SecurityError: storage bloqueado"); },
});

const mapStorage = (m) => ({
  getItem: (k) => (m.has(k) ? m.get(k) : null),
  setItem: (k, v) => m.set(k, String(v)),
  removeItem: (k) => m.delete(k),
});

/**
 * Roda pb-nav.js num contexto falso. `store`/`session` podem ser reaproveitados
 * entre chamadas pra simular dois loads seguidos no mesmo aparelho/aba (é assim
 * que o sessionStorage se comporta de verdade: sobrevive ao load, morre com a
 * aba).
 */
function run(ctx = {}) {
  const {
    inApp = false, search = "", store = new Map(), session = new Map(),
    localStorage: lsOverride, sessionStorage: ssOverride,
  } = ctx;
  // Toda leitura de localStorage fica registrada: o gate decide sem consultar
  // o aparelho, e isso é o que impede a chave legada de voltar a valer.
  const localReads = [];
  const spied = mapStorage(store);
  const localGet = spied.getItem;
  spied.getItem = (k) => { localReads.push(k); return localGet(k); };
  const sandbox = {
    location: {
      search, hostname: "pigbankai.com", pathname: "/home", hash: "",
      origin: "https://pigbankai.com", href: "https://pigbankai.com/home",
    },
    localStorage: lsOverride || spied,
    sessionStorage: ssOverride || mapStorage(session),
    document: {
      documentElement: { classList: { contains: (c) => c === "pb-app" && inApp } },
      startViewTransition() {},
      querySelectorAll: () => [],
      querySelector: () => null,
      scripts: [],
    },
    DOMParser: function DOMParser() {},
    addEventListener() {},
    URLSearchParams, URL, console, performance,
    fetch: () => Promise.reject(new Error("sem rede no teste")),
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(readFileSync(PB_NAV, "utf8"), sandbox, { filename: PB_NAV });
  return {
    enabled: sandbox.PBNav.enabled,
    store, session, localReads,
    local: store.has("pbSpa") ? store.get("pbSpa") : null,
    sess: session.has("pbSpa") ? session.get("pbSpa") : null,
  };
}

test("?pbspa=1 no navegador comum não liga nem grava — nem no load seguinte", () => {
  // O caminho cruzado que este gate fecha: clicar o link de QA no navegador
  // não mostra nada (o motor exige pb-app), mas a versão antiga já tinha
  // gravado a flag; depois, abrindo pelo ícone/app, o modo app aparecia, a
  // flag era lida e o motor experimental ligava sem ninguém ter pedido.
  const store = new Map(), session = new Map();
  const first = run({ inApp: false, search: "?pbspa=1", store, session });
  assert.equal(first.enabled, false, "load com ?pbspa=1 fora do app");
  assert.equal(store.size, 0, "não pode gravar no localStorage");
  assert.equal(session.size, 0, "não pode gravar no sessionStorage");

  assert.equal(run({ inApp: false, search: "", store, session }).enabled, false,
    "load seguinte, sem query");
  // E o pulo do gato: entrar no modo app depois NÃO pode achar flag nenhuma.
  assert.equal(run({ inApp: true, search: "", store, session }).enabled, false,
    "abrir o app depois do link no navegador");
});

test("?pbspa=1 dentro do app liga, grava só na sessão e vale no load seguinte", () => {
  const store = new Map(), session = new Map();
  const r = run({ inApp: true, search: "?pbspa=1", store, session });
  assert.equal(r.enabled, true);
  assert.equal(r.sess, "1", "a flag mora no sessionStorage");
  assert.equal(store.size, 0, "e nunca no localStorage — é isso que a tornaria permanente");
  // Sem isso o atalho seria inútil: trocar de aba dentro do app perde a query.
  assert.equal(run({ inApp: true, search: "", store, session }).enabled, true,
    "próximo load da mesma sessão continua ligado");
});

test("?pbspa=0 dentro do app desliga e limpa a sessão", () => {
  const session = new Map([["pbSpa", "1"]]);
  const r = run({ inApp: true, search: "?pbspa=0", session });
  assert.equal(r.enabled, false);
  assert.equal(r.sess, null, "a saída explícita tem que apagar a flag");
});

test("localStorage.pbSpa legado não liga em lugar nenhum e é apagado", () => {
  for (const inApp of [false, true]) {
    const store = new Map([["pbSpa", "1"]]);
    const r = run({ inApp, store });
    assert.equal(r.enabled, false, `inApp=${inApp}: chave legada não pode ligar`);
    assert.equal(r.local, null, `inApp=${inApp}: chave legada tem que sumir`);
    // O destravamento vem do gate, não do removeItem: ele não LÊ o
    // localStorage. Sem esta asserção, religar a leitura da chave legada
    // (ainda que hoje inócua, porque o removeItem roda antes) passaria batido.
    assert.deepEqual(r.localReads, [], `inApp=${inApp}: o gate não pode ler o localStorage`);
  }
});

test("storage que estoura em qualquer acesso não derruba o pb-nav", () => {
  // Sem try/catch a exceção mata o arquivo inteiro antes do window.PBNav — e o
  // dock do app-mode chama PBNav.go em todo tap de aba.
  // Com só o localStorage bloqueado o atalho segue funcionando (a flag mora na
  // sessão); com a sessão bloqueada não há onde guardar e o motor fica
  // desligado — fail-safe. Nos três casos o arquivo tem que chegar ao fim.
  for (const [label, over, want] of [
    ["localStorage bloqueado", { localStorage: THROWS }, true],
    ["sessionStorage bloqueado", { sessionStorage: THROWS }, false],
    ["os dois bloqueados", { localStorage: THROWS, sessionStorage: THROWS }, false],
  ]) {
    const r = run({ inApp: true, search: "?pbspa=1", ...over });
    assert.equal(typeof r.enabled, "boolean", `${label}: PBNav tem que existir`);
    assert.equal(r.enabled, want, `${label}: enabled`);
  }
  // E fora do app, com storage estourando, também não pode ligar nem morrer.
  assert.equal(run({ search: "?pbspa=1", localStorage: THROWS, sessionStorage: THROWS }).enabled,
    false, "navegador comum com storage bloqueado");
});

test("sem flag nenhuma o motor fica desligado, dentro e fora do app", () => {
  assert.equal(run({ inApp: true }).enabled, false);
  assert.equal(run({ inApp: false }).enabled, false);
});
