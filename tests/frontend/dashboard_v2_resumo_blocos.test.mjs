/**
 * /painel, PR B das telas reais: fatura, parcelas, renda, "Piggy notou" e Rendimentos no Resumo leem a /api/v2
 * (cartoes, parcelas, renda, insights, rendimento) e desenham o card do chat (widgets/BlocosReais.tsx).
 *
 *   · com backend: data-dado real, sem selo, os números da fixture (texto decimal → "R$ 1.200,50");
 *   · fora do contrato ("NaN", "Infinity"): "Não deu para ler este bloco agora.", nunca "R$ NaN" nem "∞";
 *   · 500: "Tentar de novo" (e a API boa depois do clique desenha o bloco); 403: convite/bloqueio;
 *   · releitura falha com dado em tela: o dado segue, com o aviso;
 *   · Essencial: renda e Piggy notou fora do layout e zero GET /renda e /insights; Plus: os dois aparecem;
 *   · o protótipo (DEMO) segue com os blocos de exemplo, com selo;
 *   · 1440 e 390: o corpo de cada bloco cabe na célula, sem rolagem lateral.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { RESPOSTAS, abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const abrir = (opts) => abrirPainel(browser, opts);
// Perfil em que o bloco está no preset, a fixture e o que a tela tem de mostrar (texto de um nó do bloco).
const BLOCOS = {
  fatura: { perfil: "dividas", rota: "cartoes", titulo: "Fatura do cartão", texto: ["Nubank · 1234", "R$ 1.200,50", "R$ 5.000,00", "Inter"] },
  parcelas: { perfil: "dividas", rota: "parcelas", titulo: "Parcelas futuras", texto: ["Notebook", "1 de 3 pagas"] },
  renda: { perfil: "autonomo", rota: "renda", titulo: "Renda mês a mês", texto: ["R$ 5.000,00", "R$ 3.200,40"] },
  piggy: { perfil: "padrao", rota: "insights", titulo: "Piggy notou", texto: ["Mercado acima da média", "Você gastou 30% a mais em mercado este mês."] },
  rendimento: { perfil: "investir", rota: "rendimento", titulo: "Rendimentos", texto: ["+R$ 112,35", "Renda fixa"] },
};
const corpo = (page, id) => page.locator(`[data-widget-id="${id}"] .w-body`);
// Espera o CARD desenhado (.bloco-real), não "qualquer filho que não seja o carregando": o erro (div.empty[role=alert]) casava com aquele seletor e o texto era lido no meio do recarregamento.
const esperaBloco = (page, id) => page.locator(`[data-widget-id="${id}"] .w-body .bloco-real`).first().waitFor({ timeout: 20000 });

for (const [id, b] of Object.entries(BLOCOS)) {
  test(`${id}: dado real da /api/v2/${b.rota}, sem selo, com os números da fixture`, async () => {
    const { ctx, page, ir, erros } = await abrir({ perfil: b.perfil });
    await ir();
    await esperaBloco(page, id);
    const w = page.locator(`[data-widget-id="${id}"] article`);
    const r = [await w.getAttribute("data-dado"), await w.locator(".w-title").innerText(), await w.locator(".w-title > .selo").count()];
    const t = await corpo(page, id).innerText();
    await ctx.close();
    assert.deepEqual(r, ["real", b.titulo, 0]);
    for (const x of b.texto) assert.ok(t.includes(x), `${id}: falta "${x}" em ${JSON.stringify(t)}`);
    assert.ok(!/NaN|∞|undefined/.test(t), t);
    assert.deepEqual(erros, []);
  });

  test(`${id}: valor fora do contrato (NaN, Infinity) vira "Não deu para ler este bloco agora.", nunca R$ NaN`, async () => {
    for (const ruim of ["NaN", "Infinity"]) {
      const { ctx, page, ir, erros } = await abrir({ perfil: b.perfil });
      const base = structuredClone(RESPOSTAS[b.rota].padrao);
      // O primeiro campo de dinheiro do corpo vira o valor ruim (o piggy não tem dinheiro: título que não é texto).
      const j = JSON.stringify(base).replace(...(id === "piggy" ? [/"title":"[^"]*"/, '"title":5'] : [/("(?:credit_used|valor|income|saldo)":)"[^"]*"/, `$1"${ruim}"`]));
      await ctx.route(`**/api/v2/${b.rota}`, (rt) => rt.fulfill({ contentType: "application/json", body: j }));
      await ir();
      const w = page.locator(`[data-widget-id="${id}"]`);
      await w.getByText("Não deu para ler este bloco agora.").waitFor({ timeout: 20000 });
      const t = await w.innerText();
      await ctx.close();
      assert.ok(!/NaN|∞|Infinity/.test(t), `${ruim}: ${t}`);
      assert.deepEqual(erros, []);
      if (id === "piggy") break; // o piggy não tem dinheiro: um caso basta
    }
  });

  test(`${id}: 500 mostra "Tentar de novo"; com a API boa o clique desenha o bloco`, async () => {
    const { ctx, page, ir } = await abrir({ perfil: b.perfil });
    let falhar = true;
    await ctx.route(`**/api/v2/${b.rota}`, (rt) => (falhar ? rt.fulfill({ status: 500, json: {} }) : rt.fallback()));
    await ir();
    const botao = page.locator(`[data-widget-id="${id}"]`).getByRole("button", { name: "Tentar de novo" });
    await botao.waitFor({ timeout: 20000 });
    falhar = false;
    await botao.click();
    await esperaBloco(page, id);
    const t = await corpo(page, id).innerText();
    await ctx.close();
    assert.ok(b.texto.every((x) => t.includes(x)), t);
  });

  test(`${id}: a releitura falha (503) e o dado antigo segue com o aviso`, async () => {
    const { ctx, page, ir } = await abrir({ perfil: b.perfil });
    await ir();
    await esperaBloco(page, id);
    await ctx.route(`**/api/v2/${b.rota}`, (rt) => rt.fulfill({ status: 503, json: {} }));
    await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
    const w = page.locator(`[data-widget-id="${id}"]`);
    await w.getByText("Não deu para atualizar.").waitFor({ timeout: 20000 });
    const t = await corpo(page, id).innerText();
    await ctx.close();
    assert.ok(b.texto.every((x) => t.includes(x)), t);
  });

  for (const width of [1440, 390]) {
    test(`${id}: ${width}px, o corpo cabe na célula e na tela, sem rolagem lateral`, async () => {
      const { ctx, page, ir } = await abrir({ perfil: b.perfil, width });
      await ir();
      await esperaBloco(page, id);
      await page.waitForTimeout(400);
      const r = await page.evaluate((i) => {
        const w = document.querySelector(`[data-widget-id="${i}"]`).getBoundingClientRect();
        const c = document.querySelector(`[data-widget-id="${i}"] .bloco-real`);
        return [document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth, c.scrollWidth - c.clientWidth, c.getBoundingClientRect().right <= w.right + 1 && w.right <= innerWidth + 1];
      }, id);
      await ctx.close();
      assert.deepEqual(r, [0, 0, true], `${id} ${width}`);
    });
  }
}

test("403 pro_required: convite no lugar do número (renda e Piggy notou); 403 password_required: Criar senha", async () => {
  const { ctx, page, ir } = await abrir({ perfil: "autonomo" });
  const e = RESPOSTAS.erros["403_pro_required"];
  await ctx.route("**/api/v2/renda", (rt) => rt.fulfill({ status: e.status, json: e.body }));
  await ctx.route("**/api/v2/insights", (rt) => rt.fulfill({ status: 403, json: { error: { code: "password_required", message: "x" } } }));
  await ir();
  const renda = page.locator('[data-widget-id="renda"]');
  await renda.getByText("A renda mês a mês é do Plus e do Pro.").waitFor({ timeout: 20000 });
  const piggy = page.locator('[data-widget-id="piggy"]');
  await piggy.getByRole("link", { name: /senha/i }).waitFor({ timeout: 20000 });
  const t = (await renda.innerText()) + (await piggy.innerText());
  await ctx.close();
  assert.ok(!/R\$ \d/.test(t), t);
});

test("a releitura volta 403 pro_required com o dado antigo em tela: o convite entra e nenhum número guardado fica (4xx nunca mostra dado)", async () => {
  const { ctx, page, ir } = await abrir({ perfil: "autonomo" });
  await ir();
  await esperaBloco(page, "renda");
  const e = RESPOSTAS.erros["403_pro_required"];
  await ctx.route("**/api/v2/renda", (rt) => rt.fulfill({ status: e.status, json: e.body }));
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
  const renda = page.locator('[data-widget-id="renda"]');
  await renda.getByText("A renda mês a mês é do Plus e do Pro.").waitFor({ timeout: 20000 });
  const t = await renda.innerText();
  await ctx.close();
  assert.ok(!/R\$ \d|5\.000|3\.200/.test(t), t);
});

// A conta muda noutra aba com o bloco fora da tela (outra rota): o cache do React Query não leva o uid na chave, e o main.tsx remove o que ninguém
// mostra quando o perfil diz outro uid. Ao voltar ao Resumo, o texto da conta A não pode aparecer em NENHUM instante (o /insights da B responde na hora).
// (Com o bloco já na tela a troca só é vista pelo bloco quando a releitura dele chega: é a política de todo dado vivo, o dado fica até o novo chegar.)
test("troca de conta em outra aba com o bloco fora da tela: o Piggy notou da conta A nunca aparece para a B ao voltar ao Resumo", async () => {
  let uid = 7;
  const { ctx, page, ir } = await abrir({ perfil: "padrao" });
  await ctx.route("**/auth/dashboard-profile", (rt) => rt.fulfill({ json: { user_id: uid, display_name: "Ana", email: "ana@exemplo.com", whatsapp_linked: false } }));
  await ctx.route("**/api/v2/insights", async (rt) => {
    if (uid === 8) await new Promise((ok) => setTimeout(ok, 600)); // a conta B demora: é a janela em que um cache velho apareceria
    await rt.fulfill({ json: { insights: [{ title: uid === 7 ? "Segredo da conta A" : "Aviso da conta B", message: "x" }] } }).catch(() => {});
  });
  await ir();
  const piggy = page.locator('[data-widget-id="piggy"]');
  await piggy.getByText("Segredo da conta A").waitFor({ timeout: 20000 });
  await page.evaluate(() => { location.hash = "#/conta/bancos"; });
  await piggy.waitFor({ state: "detached" });
  uid = 8;
  const perfilB = page.waitForResponse((r) => new URL(r.url()).pathname === "/auth/dashboard-profile");
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
  await perfilB;
  await page.waitForTimeout(300);
  await page.evaluate(() => { location.hash = "#/"; });
  const vistos = await page.evaluate(() => new Promise((ok) => { // amostra o bloco a cada 20 ms até o B chegar
    const v = []; const t0 = Date.now();
    const i = setInterval(() => {
      const t = document.querySelector('[data-widget-id="piggy"]')?.textContent ?? "";
      v.push(t);
      if (t.includes("Aviso da conta B") || Date.now() - t0 > 15000) { clearInterval(i); ok(v); }
    }, 20);
  }));
  await ctx.close();
  assert.ok(vistos.at(-1).includes("Aviso da conta B"), "a conta B chegou");
  assert.ok(vistos.every((t) => !t.includes("Segredo da conta A")), "texto da conta A visto depois da troca");
});

// Mesma troca, mas SEM visibilitychange: o perfil nem foi relido, então o main.tsx não limpou nada. Só o gcTime: 0 tira o dado da A do cache ao desmontar o bloco.
// Também: voltar ao Resumo na MESMA conta mostra o carregando e depois o dado, e entrar uma vez pede /insights uma vez só.
test("troca de conta sem o perfil ser relido: o Piggy notou da conta A não aparece ao voltar; B chega; e a mesma conta relê sem laço", async () => {
  let uid = 7; let lento = false;
  const { ctx, page, ir } = await abrir({ perfil: "padrao" });
  const pedidos = [];
  page.on("request", (q) => { if (new URL(q.url()).pathname === "/api/v2/insights") pedidos.push(uid); });
  await ctx.route("**/api/v2/insights", async (rt) => {
    if (uid === 8 || lento) await new Promise((ok) => setTimeout(ok, 1500));
    await rt.fulfill({ json: { insights: [{ title: uid === 7 ? "Segredo da conta A" : "Aviso da conta B", message: "x" }] } }).catch(() => {});
  });
  await ir();
  const piggy = page.locator('[data-widget-id="piggy"]');
  await piggy.getByText("Segredo da conta A").waitFor({ timeout: 20000 });
  await page.waitForTimeout(500);
  lento = true; // a volta à mesma conta demora, para o carregando poder ser visto
  const entradaUnica = pedidos.length;
  // A amostragem começa ANTES de voltar ao Resumo, no mesmo evaluate: sem a ida e volta do Playwright no meio (máquina lenta do CI).
  const amostra = () => page.evaluate(() => new Promise((ok) => {
    const v = []; const t0 = Date.now();
    location.hash = "#/";
    const i = setInterval(() => {
      const t = document.querySelector('[data-widget-id="piggy"]')?.textContent ?? "";
      v.push(t);
      if (/Aviso da conta B|Segredo da conta A/.test(t) || Date.now() - t0 > 15000) { clearInterval(i); setTimeout(() => ok(v), 300); }
    }, 20);
  }));
  // mesma conta: sai, volta, vê o carregando e depois o dado
  await page.evaluate(() => { location.hash = "#/conta/bancos"; });
  await piggy.waitFor({ state: "detached" });
  const mesma = await amostra();
  // outra conta, perfil não relido
  await page.evaluate(() => { location.hash = "#/conta/bancos"; });
  await piggy.waitFor({ state: "detached" });
  uid = 8;
  const vistos = await amostra();
  await page.waitForTimeout(500);
  const total = pedidos.length;
  await ctx.close();
  assert.equal(entradaUnica, 1, "uma entrada no Resumo = um GET /insights");
  assert.ok(vistos.every((t) => !t.includes("Segredo da conta A")), "texto da conta A visto depois da troca");
  assert.ok(vistos.at(-1).includes("Aviso da conta B"), "a conta B chegou");
  assert.ok(mesma.some((t) => t.includes("olhando os seus números")), "mesma conta: carregando");
  assert.ok(mesma.at(-1).includes("Segredo da conta A"), "mesma conta: depois o dado");
  assert.equal(total, 3, "um GET por entrada, sem laço");
});

test("Essencial: renda e Piggy notou fora do Resumo (e do Organizar, travados), zero GET /renda e /insights; Plus: os dois aparecem, 1 GET cada", async () => {
  const r = {};
  for (const plano of ["essencial", "plus"]) {
    const { ctx, page, ir } = await abrir({ perfil: "autonomo", plano });
    const pedidos = [];
    page.on("request", (q) => { const p = new URL(q.url()).pathname; if (p === "/api/v2/renda" || p === "/api/v2/insights") pedidos.push(p); });
    await ir();
    await page.waitForTimeout(800);
    const blocos = await page.evaluate(() => [...document.querySelectorAll("[data-widget-id]")].map((w) => w.dataset.widgetId));
    r[plano] = [blocos.includes("renda"), blocos.includes("piggy"), pedidos.sort()];
    if (plano === "essencial") {
      await page.getByRole("button", { name: "Organizar" }).click();
      await page.getByRole("button", { name: "Adicionar bloco" }).click();
      r.catalogo = await page.locator("#board-catalog .catalog-item", { hasText: "Renda mês a mês" }).innerText();
    }
    await ctx.close();
  }
  assert.deepEqual([r.essencial, r.plus], [[false, false, []], [true, true, ["/api/v2/insights", "/api/v2/renda"]]]);
  assert.match(r.catalogo, /No Plus/);
});

test("protótipo (DEMO): os cinco blocos seguem de exemplo, com selo, sem pedido à API", async () => {
  const pedidos = [];
  const selos = {};
  const carregando = {};
  // Cada bloco só monta no perfil em que está no preset (o `padrao` só tem o piggy): abre-se o perfil de cada um.
  for (const [id, b] of Object.entries(BLOCOS)) {
    const { ctx, page, ir } = await abrir({ demo: true, espera: "#page-title" });
    // No protótipo o perfil mora no localStorage (não na API).
    await ctx.addInitScript((p) => localStorage.setItem("pigbank.dashboard.profile.v1", JSON.stringify(p)), b.perfil);
    page.on("request", (q) => { if (/\/api\/v2\/(cartoes|parcelas|renda|insights|rendimento)$/.test(new URL(q.url()).pathname)) pedidos.push(q.url()); });
    await ir();
    await page.locator(`[data-widget-id="${id}"]`).waitFor();
    await page.waitForTimeout(300);
    selos[id] = await page.locator(`[data-widget-id="${id}"] .w-title > .selo`).count();
    // O bloco real no protótipo (consulta desligada) fica para sempre no "Carregando…" [role=status]; o de exemplo desenha o corpo.
    carregando[id] = await corpo(page, id).locator('[role="status"]').count();
    await ctx.close();
  }
  assert.deepEqual(selos, { fatura: 1, parcelas: 1, renda: 1, piggy: 1, rendimento: 1 });
  assert.deepEqual(carregando, { fatura: 0, parcelas: 0, renda: 0, piggy: 0, rendimento: 0 });
  assert.deepEqual(pedidos, []);
});
