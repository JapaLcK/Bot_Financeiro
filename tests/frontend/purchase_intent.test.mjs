import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync("frontend/purchase-intent.js", "utf8");

function loadIntent(now = 1_800_000_000_000, documentOverride = null) {
  const values = new Map();
  const sessionStorage = {
    getItem: (key) => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  };
  const document = documentOverride || {
    readyState: "loading",
    addEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
  const window = { sessionStorage };
  vm.runInNewContext(source, {
    window,
    document,
    Date: class extends Date { static now() { return now; } },
  });
  return { api: window.PBPurchaseIntent, values };
}

test("preserva plano, ciclo e meio durante a autenticação", () => {
  const { api } = loadIntent();
  api.begin("plus", "annual", "pix");
  api.markAwaitingAuth();

  assert.deepEqual(
    JSON.parse(JSON.stringify(api.pending())),
    {
      version: 1,
      plan: "plus",
      cycle: "annual",
      method: "pix",
      status: "awaiting_auth",
      createdAt: 1_800_000_000_000,
    },
  );
  assert.equal(api.afterAuth("/home"), "/precos?compra=continuar");
});

test("cadastro direto continua usando o destino normal", () => {
  const { api } = loadIntent();
  assert.equal(api.afterAuth("/precos?escolha=1"), "/precos?escolha=1");
});

test("login Google recebe o retorno para a compra pendente", () => {
  const googleLink = { href: "/auth/google/start" };
  const authCard = {};
  const document = {
    readyState: "loading",
    addEventListener() {},
    querySelector(selector) {
      return selector === ".auth-card" ? authCard : null;
    },
    querySelectorAll(selector) {
      if (selector === '.auth-card .auth-sub') return [];
      if (selector === 'a[href="/auth/google/start"]') return [googleLink];
      return [];
    },
    getElementById() { return null; },
  };
  const { api } = loadIntent(1_800_000_000_000, document);
  api.begin("plus", "monthly", "card");
  api.markAwaitingAuth();

  api.mountAuth();

  assert.equal(
    googleLink.href,
    "/auth/google/start?next=%2Fprecos%3Fcompra%3Dcontinuar",
  );
});

test("só uma compra iniciada vira confirmação do onboarding", () => {
  const { api } = loadIntent();
  api.begin("essencial", "monthly", "card");
  assert.equal(api.complete(), null, "selecionar não equivale a pagar");

  api.markCheckoutStarted();
  const done = api.complete();
  assert.equal(done.status, "completed");
  assert.equal(api.read(), null);
  assert.equal(api.readCompleted().plan, "essencial");
});

test("não aceita Pix mensal nem conteúdo fora do contrato", () => {
  const { api, values } = loadIntent();
  assert.equal(api.begin("plus", "monthly", "pix"), null);
  assert.equal(api.begin("premium", "annual", "card"), null);
  assert.equal(values.size, 0);
});

test("a intenção não contém campos de documento, cartão ou provedor", () => {
  const { api, values } = loadIntent();
  api.begin("pro", "annual", "pix");
  api.markCheckoutStarted();
  const serialized = values.get("pb_purchase_intent_v1");
  for (const forbidden of ["cpf", "cnpj", "documento", "cartao", "token", "asaas", "stripe"]) {
    assert.equal(serialized.toLowerCase().includes(forbidden), false, forbidden);
  }
});

test("todas as telas da jornada carregam o contrato compartilhado", () => {
  for (const file of [
    "frontend/precos.html",
    "frontend/cadastro.html",
    "frontend/login.html",
    "frontend/completar-cadastro.html",
    "frontend/home.html",
    "frontend/comecar.html",
  ]) {
    assert.match(fs.readFileSync(file, "utf8"), /\/purchase-intent\.js\?v=1/, file);
  }
  assert.match(
    fs.readFileSync("frontend/routes/static_pages.py", "utf8"),
    /@router\.get\("\/purchase-intent\.js"\)/,
  );
});
