/**
 * Modal "Excluir cartão" (P8): cartão do banco (Open Finance) só avisa, sem botão destrutivo.
 *
 * O GET delete-impact devolve `bloqueio` (texto) para o cartão vinculado ao banco; o modal
 * mostra o texto, esconde "Sim, excluir tudo" e troca "Cancelar" por "Entendi". O reset no
 * começo de `openCardDeleteModal` também fecha o bug antigo: modal de um 2º cartão em
 * "Verificando impacto…" (ou com o delete-impact falhando) deixava o botão e o onclick do
 * cartão anterior vivos.
 *
 * Markup REAL do modal (lido da dashboard.html) + dashboard.css real; fetch mockado.
 * Resposta velha do delete-impact (modal reaberto) não atropela a do cartão atual (f); o 409 do DELETE
 * (cartão virou do banco com o modal aberto) põe o modal no estado de bloqueio (g), mas o 409 de um
 * DELETE antigo não pinta o modal de outro cartão (h), nem o erro do GET velho (i).
 * NEGATIVOS: sem o ramo `data.bloqueio` → (a) vermelho; sem o reset → (c) e (d) vermelhos;
 * sem o contador de geração no then do GET → (f); sem ele no catch → (i); sem o guarda no 409 → (h);
 * sem o ramo 409 → (g) vermelho; sem o `cancel.focus()` em _cardDeleteBloqueado → (a) e (g) vermelhos (foco no body).
 * POSITIVO: (b) sem bloqueio o botão aparece e diz "Cancelar".
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { abrirBrowser, fecharBrowser, loadDashboardJs, DASHBOARD_JS } from "./_dashboard_loader.mjs";

before(abrirBrowser);
after(fecharBrowser);

const HTML = readFileSync(DASHBOARD_JS.replace("dashboard.js", "dashboard.html"), "utf8");
const MODAL = HTML.match(/<div class="overlay" id="card-delete-overlay">[\s\S]*?\n<\/div>\n/)[0];
const CSS = DASHBOARD_JS.replace("dashboard.js", "dashboard.css");
const TEXTO = "Esse cartão vem do seu banco (Open Finance). Para tirar, desconecte o banco em Configurações.";

async function pagina(viewport) {
  const page = await loadDashboardJs({ pageOptions: { viewport } });
  await page.evaluate((html) => document.body.insertAdjacentHTML("beforeend", html), MODAL);
  await page.addStyleTag({ path: CSS });
  return page;
}

/** modo: {bloqueio} | "pendente" | "falha". Devolve o estado do modal depois de abrir. */
async function abrir(page, modo, nome = "Nubank") {
  return page.evaluate(async ({ modo, nome }) => {
    csrfHeaders = () => ({}); // about:blank não lê document.cookie
    window.fetch = () => {
      if (modo === "pendente") return new Promise(() => {});
      if (modo === "falha") return Promise.resolve({ ok: false, text: async () => "boom" });
      return Promise.resolve({
        ok: true,
        json: async () => ({ ok: true, card_name: nome, impact: { total_bills_count: 2, total_transactions_count: 5 }, ...modo }),
      });
    };
    const p = openCardDeleteModal(7, nome);
    if (modo !== "pendente") await p;
    else await new Promise((r) => setTimeout(r, 30));
    const btn = document.getElementById("card-delete-confirm-btn");
    const cancel = document.getElementById("card-delete-cancel-btn");
    const r = btn.getBoundingClientRect();
    return {
      corpo: document.getElementById("card-delete-body").textContent,
      html: document.getElementById("card-delete-body").innerHTML,
      confirmVisivel: r.width > 0 && r.height > 0 && getComputedStyle(btn).display !== "none",
      temOnclick: typeof btn.onclick === "function",
      cancelar: cancel.textContent,
      ativo: document.activeElement && document.activeElement.id,
    };
  }, { modo, nome });
}

const VIEWPORTS = [["desktop 1280x800", { width: 1280, height: 800 }],
                   ["mobile 375x812", { width: 375, height: 812 }]];

/** Cada caso roda numa página própria, nos dois viewports. */
function caso(nome, fn) {
  for (const [rotulo, viewport] of VIEWPORTS) {
    test(`${rotulo}: ${nome}`, async () => {
      const page = await pagina(viewport);
      await fn(page);
      assert.deepEqual(page.__errs, []);
      await page.close();
    });
  }
}

caso("(a) bloqueio: texto exato, confirm invisível e sem onclick, cancelar vira Entendi", async (page) => {
  const a = await abrir(page, { bloqueio: TEXTO });
  assert.equal(a.corpo, TEXTO);
  assert.equal(a.confirmVisivel, false);
  assert.equal(a.temOnclick, false);
  assert.equal(a.cancelar, "Entendi");
  assert.equal(a.ativo, "card-delete-cancel-btn"); // foco no "Entendi", não no body
  // sem overflow horizontal no modal (relevante no 375)
  const m = await page.evaluate(() => {
    const el = document.querySelector("#card-delete-overlay .modal");
    return { sw: el.scrollWidth, cw: el.clientWidth };
  });
  assert.ok(m.sw <= m.cw, `overflow no modal: ${m.sw} > ${m.cw}`);
});

caso("(b) sem bloqueio: botão visível com onclick e Cancelar (controle positivo)", async (page) => {
  const b = await abrir(page, { bloqueio: null });
  assert.match(b.corpo, /Tem certeza que quer excluir Nubank/);
  assert.equal(b.confirmVisivel, true);
  assert.equal(b.temOnclick, true);
  assert.equal(b.cancelar, "Cancelar");
});

caso("(c) bloqueio e depois cartão manual na mesma página: botão e Cancelar voltam", async (page) => {
  await abrir(page, { bloqueio: TEXTO });
  const c = await abrir(page, { bloqueio: null });
  assert.equal(c.confirmVisivel, true);
  assert.equal(c.temOnclick, true);
  assert.equal(c.cancelar, "Cancelar");
});

caso("(d) fetch pendente ou falhando depois de um cartão normal: botão e onclick do anterior somem", async (page) => {
  await abrir(page, { bloqueio: null });
  const pend = await abrir(page, "pendente");
  assert.match(pend.corpo, /Verificando/);
  assert.equal(pend.confirmVisivel, false);
  assert.equal(pend.temOnclick, false);
  await abrir(page, { bloqueio: null });
  const falha = await abrir(page, "falha");
  assert.match(falha.corpo, /Erro/);
  assert.equal(falha.confirmVisivel, false);
  assert.equal(falha.temOnclick, false);
});

caso("(e) bloqueio com HTML aparece literal", async (page) => {
  const e = await abrir(page, { bloqueio: "<b>x</b>" });
  assert.equal(e.corpo, "<b>x</b>");
  assert.equal(e.html, "&lt;b&gt;x&lt;/b&gt;");
});

/** Estado do modal, lido do DOM (dentro do page.evaluate). */
const LER = `(() => {
  const btn = document.getElementById("card-delete-confirm-btn");
  const r = btn.getBoundingClientRect();
  return { corpo: document.getElementById("card-delete-body").textContent,
           confirmVisivel: r.width > 0 && r.height > 0 && getComputedStyle(btn).display !== "none",
           cancelar: document.getElementById("card-delete-cancel-btn").textContent,
           ativo: document.activeElement && document.activeElement.id };
})()`;

caso("(f) resposta velha de outro cartão (A do banco, lenta) chega depois da do atual (B manual)", async (page) => {
  const r = await page.evaluate(async ({ texto, ler }) => {
    csrfHeaders = () => ({});
    const pend = [];
    window.fetch = () => new Promise((res) => pend.push(res));
    const ok = (d) => ({ ok: true, json: async () => ({ ok: true, impact: {}, ...d }) });
    const a = openCardDeleteModal(1, "Banco");
    closeCardDeleteModal();
    const b = openCardDeleteModal(2, "Manual");
    pend[1](ok({ bloqueio: null })); await b;
    pend[0](ok({ bloqueio: texto })); await a;
    return eval(ler);
  }, { texto: TEXTO, ler: LER });
  assert.match(r.corpo, /Tem certeza que quer excluir Manual/);
  assert.equal(r.confirmVisivel, true);
  assert.equal(r.cancelar, "Cancelar");
});

caso("(g) 409 no DELETE: modal vira bloqueio (texto do detail, sem botão, Entendi)", async (page) => {
  await abrir(page, { bloqueio: null });
  const r = await page.evaluate(async ({ texto, ler }) => {
    window.fetch = () => Promise.resolve({ ok: false, status: 409, json: async () => ({ detail: texto }) });
    // sem o ramo 409 o alertModal espera clique para sempre: o teto evita travar a suíte
    const sim = document.getElementById("card-delete-confirm-btn");
    sim.focus(); // quem clicou em "Sim" estava com o foco nele; ele some no 409
    await Promise.race([sim.onclick(), new Promise((r) => setTimeout(r, 300))]);
    return eval(ler);
  }, { texto: TEXTO, ler: LER });
  assert.equal(r.corpo, TEXTO);
  assert.equal(r.confirmVisivel, false);
  assert.equal(r.cancelar, "Entendi");
  assert.equal(r.ativo, "card-delete-cancel-btn");
});

caso("(h) 409 de um DELETE antigo (cartão A) chega com o modal já aberto para o cartão B", async (page) => {
  const r = await page.evaluate(async ({ texto, ler }) => {
    csrfHeaders = () => ({});
    let responde409;
    window.fetch = (url, opt) => opt && opt.method === "DELETE"
      ? new Promise((res) => { responde409 = () => res({ ok: false, status: 409, json: async () => ({ detail: texto }) }); })
      : Promise.resolve({ ok: true, json: async () => ({ ok: true, impact: {}, bloqueio: null }) });
    await openCardDeleteModal(1, "A");
    const sim = document.getElementById("card-delete-confirm-btn").onclick(); // "Sim" em A, DELETE lento
    closeCardDeleteModal();
    await openCardDeleteModal(2, "B");
    responde409(); await sim;
    return eval(ler);
  }, { texto: TEXTO, ler: LER });
  assert.match(r.corpo, /Tem certeza que quer excluir B/);
  assert.equal(r.confirmVisivel, true);
  assert.equal(r.cancelar, "Cancelar");
});

caso("(i) GET do cartão A rejeita depois que o modal do B abriu: sem 'Erro:' no modal do B", async (page) => {
  const r = await page.evaluate(async (ler) => {
    csrfHeaders = () => ({});
    let rejeitaA;
    window.fetch = (url) => String(url).includes("/10")
      ? new Promise((_, rej) => { rejeitaA = () => rej(new Error("erro velho")); })
      : Promise.resolve({ ok: true, json: async () => ({ ok: true, impact: {}, bloqueio: null }) });
    const a = openCardDeleteModal(10, "A");
    closeCardDeleteModal();
    await openCardDeleteModal(11, "B");
    rejeitaA(); await a;
    return eval(ler);
  }, LER);
  assert.match(r.corpo, /Tem certeza que quer excluir B/);
  assert.doesNotMatch(r.corpo, /Erro/);
  assert.equal(r.confirmVisivel, true);
});
