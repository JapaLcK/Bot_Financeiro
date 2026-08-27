/**
 * Wizard de primeira configuração (frontend/comecar.js + comecar.html).
 *
 * Três invariantes que só quebram em produção e que nenhum teste de "a tela
 * abre?" pegaria:
 *
 *  1. O passo do resumo manda os TRÊS booleanos de daily_report_prefs juntos, e
 *     "pular" não manda nada. As três flags nascem `true` no banco
 *     (db/schema.py:498,516,519), então um PATCH otimista DESLIGA o resumo
 *     semanal e mensal de quem só clicou em continuar.
 *  2. O vínculo do WhatsApp é detectado por `identities`, não por
 *     `whatsapp_linked`. Aquele campo é bool(whatsapp_verified_at), coluna que
 *     só o auto-link por telefone escreve (db_support.py:926) — quem vinculou
 *     pelo código nunca ganharia a confirmação.
 *  3. Nenhum handler inline no HTML: os 139 `onclick=` do dashboard.html são o
 *     que ainda segura o 'unsafe-inline' na CSP, e código novo não aumenta essa
 *     dívida.
 *
 * Sem browser: os helpers de comecar.js são JS puro e o arquivo se auto-protege
 * (boot() sai na hora se não achar `.onb-card`), então o vm abaixo carrega o
 * módulo sem DOM e sem disparar uma única chamada de rede.
 *
 * Rodar:  npm run test:frontend
 *         (ou, um arquivo só: node --test tests/frontend/onboarding_steps.test.mjs)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
const JS = join(FRONTEND, "comecar.js");
const HTML = join(FRONTEND, "comecar.html");

/** Carrega comecar.js sem DOM e devolve os helpers puros. */
function load() {
  const fetchCalls = [];
  const sandbox = {
    document: {
      // null aqui é o que faz o boot() sair antes de qualquer fetch.
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener: () => {},
      readyState: "complete",
    },
    location: { search: "", replace: () => {} },
    fetch: (...args) => { fetchCalls.push(args); return Promise.reject(new Error("sem rede")); },
    setInterval: () => 0,
    clearInterval: () => {},
    scrollTo: () => {},
    URLSearchParams,
    console,
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(readFileSync(JS, "utf8"), sandbox, { filename: JS });
  return { api: sandbox.window.PBOnboarding, fetchCalls };
}

const html = () => readFileSync(HTML, "utf8");

// ── 1. Resumo: as 4 opções × os 3 booleanos ─────────────────────────────────

test("cada opção de resumo manda os três booleanos", () => {
  const { api } = load();
  const esperado = {
    diario: [true, false, false],
    semanal: [false, true, false],
    mensal: [false, false, true],
    nenhum: [false, false, false],
  };
  for (const [id, [d, w, m]] of Object.entries(esperado)) {
    const prefs = api.reportPrefsFor(id);
    assert.deepEqual(
      prefs,
      { daily_report_enabled: d, weekly_report_enabled: w, monthly_report_enabled: m },
      `opção ${id}`,
    );
    // Os três SEMPRE presentes: mandar só o que liga deixaria os outros ativos
    // e a "escolha única" seria mentira.
    assert.equal(Object.keys(prefs).length, 3, `opção ${id} não mandou os três`);
  }
});

test("opção desconhecida não vira PATCH", () => {
  const { api } = load();
  assert.equal(api.reportPrefsFor("qualquer"), null);
  assert.equal(api.reportPrefsFor(null), null);
});

test("nenhuma opção de resumo reproduz o default do banco (true/true/true)", () => {
  // É exatamente por isso que nada nasce pré-marcado: não existe opção que
  // signifique "deixa como está", então pré-marcar mudaria a preferência do
  // usuário fingindo confirmá-la.
  const { api } = load();
  for (const choice of api.REPORT_CHOICES) {
    assert.ok(
      !(choice.daily && choice.weekly && choice.monthly),
      `${choice.id} reproduz o default e não deveria`,
    );
  }
});

test("o botão de salvar o resumo nasce desabilitado", () => {
  assert.match(html(), /data-role="save-report"[^>]*disabled/);
});

test("nenhum radio do resumo vem marcado do HTML", () => {
  // As opções são montadas em JS com aria-checked="false"; o HTML não pode
  // trazer nenhuma pré-seleção.
  assert.doesNotMatch(html(), /aria-checked="true"/);
  assert.doesNotMatch(html(), /<input[^>]*type="radio"[^>]*checked/);
});

// ── 2. WhatsApp: identities, não whatsapp_linked ────────────────────────────

test("vínculo por identities é reconhecido", () => {
  const { api } = load();
  assert.equal(api.isWhatsAppLinked({ identities: [{ provider: "whatsapp" }] }), true);
  assert.equal(api.isWhatsAppLinked({ identities: [{ provider: "discord" }] }), false);
  assert.equal(api.isWhatsAppLinked({ identities: [] }), false);
  assert.equal(api.isWhatsAppLinked({}), false);
  assert.equal(api.isWhatsAppLinked(null), false);
});

test("quem vinculou pelo código conta como vinculado mesmo sem whatsapp_verified_at", () => {
  // O caso que `whatsapp_linked` perderia: link_platform_identity cria a
  // identity mas NÃO carimba whatsapp_verified_at.
  const { api } = load();
  const security = { whatsapp_verified_at: null, identities: [{ provider: "whatsapp" }] };
  assert.equal(api.isWhatsAppLinked(security), true);
});

test("comecar.js não lê whatsapp_linked", () => {
  assert.doesNotMatch(readFileSync(JS, "utf8").replace(/\/\*[\s\S]*?\*\//g, ""),
    /profile\.whatsapp_linked|\.whatsapp_linked/);
});

// ── 3. Sem handler inline ───────────────────────────────────────────────────

test("o HTML do wizard não tem nenhum handler inline", () => {
  const matches = html().match(/\son[a-z]+\s*=/gi) || [];
  assert.deepEqual(matches, [], `handlers inline encontrados: ${matches.join(", ")}`);
});

test("toda ação do HTML tem handler registrado no JS", () => {
  const acoes = new Set(
    [...html().matchAll(/data-action="([^"]+)"/g)].map((m) => m[1]),
  );
  const js = readFileSync(JS, "utf8");
  assert.ok(acoes.size >= 5, "o HTML deveria declarar ações");
  for (const acao of acoes) {
    assert.ok(
      js.includes(`"${acao}"`) || js.includes(`${acao}:`),
      `ação "${acao}" do HTML não tem handler em comecar.js`,
    );
  }
});

// ── Retomada ────────────────────────────────────────────────────────────────

test("retomada abre no passo salvo", () => {
  const { api } = load();
  assert.equal(api.resumeStep({ step: 3 }), 3);
  assert.equal(api.resumeStep({ step: 0 }), 1, "passo 0 = nunca começou");
  assert.equal(api.resumeStep({}), 1);
  assert.equal(api.resumeStep(null), 1);
  assert.equal(api.resumeStep({ step: 99 }), api.TOTAL_STEPS, "não passa do último");
});

// ── Clique duplo / retentativa ──────────────────────────────────────────────

test("cartão de nome repetido é barrado antes do POST", () => {
  const { api } = load();
  const cards = [{ name: "Nubank" }];
  assert.equal(api.hasCardNamed(cards, "Nubank"), true);
  assert.equal(api.hasCardNamed(cards, "  nubank "), true, "compara sem caixa e sem espaço");
  assert.equal(api.hasCardNamed(cards, "Inter"), false);
  assert.equal(api.hasCardNamed([], "Nubank"), false);
  assert.equal(api.hasCardNamed(cards, ""), false);
});

test("o botão fica inerte durante a requisição", () => {
  // withBusy marca aria-busy e desabilita; o CSS torna o alvo não-clicável.
  const js = readFileSync(JS, "utf8");
  assert.match(js, /aria-busy/);
  assert.match(js, /state\.inFlight/);
  assert.match(readFileSync(join(FRONTEND, "comecar.css"), "utf8"),
    /\[aria-busy="true"\][^}]*pointer-events:\s*none/);
});

// ── Boot uma vez só ─────────────────────────────────────────────────────────

test("avaliar o arquivo duas vezes não registra dois listeners de clique", () => {
  // Medido no preview: com o arquivo avaliado três vezes, um clique em
  // "Continuar" pulava do passo 1 pro 4, porque cada avaliação registrava outro
  // listener no document. Acontece com script incluído em duplicidade ou com
  // re-execução de init (o pb-nav.js faz isso nas páginas que converte).
  const src = readFileSync(JS, "utf8");
  const listeners = [];
  const sandbox = {
    document: {
      querySelector: (s) => (s === ".onb-card" ? {} : null),
      querySelectorAll: () => [],
      addEventListener: (tipo) => listeners.push(tipo),
      readyState: "complete",
    },
    location: { search: "", replace: () => {} },
    fetch: () => Promise.reject(new Error("sem rede")),
    setInterval: () => 0, clearInterval: () => {}, scrollTo: () => {},
    URLSearchParams, console,
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: JS });
  vm.runInContext(src, sandbox, { filename: JS });
  vm.runInContext(src, sandbox, { filename: JS });

  const cliques = listeners.filter((t) => t === "click");
  assert.equal(cliques.length, 1, `registrou ${cliques.length} listeners de clique`);
});

// ── Alvo de toque ───────────────────────────────────────────────────────────

test("os botões de pular têm alvo de toque de 44px", () => {
  // Medido em 375×812 com padding 8px: davam 34px. Esta página abre dentro do
  // app iOS e NÃO carrega o app-mode.css, que é quem impõe o mínimo no resto
  // do app — então o mínimo tem de estar aqui.
  const css = readFileSync(join(FRONTEND, "comecar.css"), "utf8");
  const bloco = css.slice(css.indexOf(".onb-skip,"));
  assert.match(bloco.slice(0, 400), /min-height:\s*44px/);
});

// ── CTA de upgrade tem de ser <a href="/precos"> ────────────────────────────

test("upgrade é anchor pra /precos, não botão", () => {
  // auth-refresh.js esconde `a[href^="/precos"]` dentro do app iOS (diretriz
  // 3.1.1). Um <button> com location.href não seria pego pela regra.
  const js = readFileSync(JS, "utf8");
  assert.match(js, /link\.href\s*=\s*"\/precos"/);
  assert.doesNotMatch(js, /location\.href\s*=\s*"\/precos"/);
});

// ── Boot não dispara rede sem a página ──────────────────────────────────────

test("carregar o arquivo sem a marcação não faz nenhuma chamada", () => {
  const { fetchCalls } = load();
  assert.deepEqual(fetchCalls, []);
});
