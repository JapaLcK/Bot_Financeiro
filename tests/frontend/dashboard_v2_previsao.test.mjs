/**
 * /painel#/previsao com backend (Etapa 3, PR3): a tela mostra a GET /api/v2/previsao como
 * chega, sem recalcular (webapp/src/dashboard/widgets/Previsao.tsx e Compromissos.tsx).
 *
 *   · dinheiro é o texto da API (`moneyText`), nunca float: "10.005" → "R$ 10,005";
 *   · null não é zero; Plus sem detalhe Pro mostra convite, não lista vazia nem gráfico;
 *   · compromissos na ordem da API, homônimos separados, grupo sem total somado;
 *   · nenhuma "Faixa provável" nem "cabe" na tela real; o protótipo continua como era.
 *
 * Tempo, falha, corrida e downgrade: dashboard_v2_previsao_atualizacao.test.mjs.
 *
 * Rodar:  npm run test:frontend   (abre o artefato commitado frontend/dashboard-app.*: mudou webapp/src, rode `npm --prefix webapp run build`)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright";
import { RAIZ, RESPOSTAS, abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";
import { moneyText } from "../../webapp/src/dashboard/lib/format.js";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

// 12h de 6/10 em SP: o "hoje" das fixtures.
const BASE = { espera: "#page-title", agora: "2026-10-06T15:00:00Z" };
async function abrir(opts = {}, rota = "/previsao") {
  const r = await abrirPainel(browser, { ...BASE, ...opts });
  r.pedidos = [];
  // o protótipo guarda o perfil no aparelho: sem ele o modal da 1ª visita cobre o Resumo
  if (opts.demo) await r.ctx.addInitScript(() => localStorage.setItem("pigbank.dashboard.profile.v1", '"padrao"'));
  r.page.on("request", (q) => { const u = new URL(q.url()); if (u.pathname === "/api/v2/previsao") r.pedidos.push(u.search); });
  await r.ir(rota);
  return r;
}
// A resposta chegou e a tela desenhou ("Calculada às" só existe com dado).
const pronta = (page) => page.locator("#w-previsao .prev-hora").waitFor();
// O gráfico só desenha com a largura medida (useSize); o `svg` sem role é o puxador do bloco.
const GRAFICO = "svg[role=img]";
const textoDe = (page, sel) => page.locator(sel).innerText();
const servirPrevisao = (ctx, corpo) => ctx.route("**/api/v2/previsao**", (r) => r.fulfill({ json: corpo }));

test("moneyText: o texto da API, sem float nem arredondamento", () => {
  const casos = {
    "10.005": "R$ 10,005", "2.675": "R$ 2,675", "7.33": "R$ 7,33", "-5.32": "−R$ 5,32", "-0.00": "R$ 0,00",
    "0E-8": "R$ 0,00", "0": "R$ 0,00", "1E+2": "R$ 100,00", "1234567.5": "R$ 1.234.567,50", "1234": "R$ 1.234,00",
    "-1234.567": "−R$ 1.234,567", "0.5": "R$ 0,50",
  };
  for (const [e, s] of Object.entries(casos)) assert.equal(moneyText(e), s, e);
  for (const e of [null, undefined, "", "NaN", "Infinity", "-Infinity", "abc", "1,5", " 1", "1.", ".5", 7.33]) assert.equal(moneyText(e), null, String(e));
});

test("Pro 30/60/90: marcos com o texto exato, base e âncora lado a lado, gráfico; o Seg pede ?dias", async () => {
  const { ctx, page, erros, pedidos } = await abrir();
  const ler = () => page.evaluate(() => {
    const w = document.querySelector("#w-previsao");
    return {
      valor: w.querySelector(".hero-value").textContent, sub: w.querySelector(".hero-sub").textContent,
      marcos: [...w.querySelectorAll(".marcos li")].map((li) => li.textContent),
      fatos: [...w.querySelectorAll(".prev-facts dd")].map((x) => x.textContent), svg: w.querySelectorAll("svg[role=img]").length,
    };
  });
  await page.locator(`#w-previsao ${GRAFICO}`).waitFor();
  const r30 = await ler();
  await page.getByRole("radio", { name: "60 dias" }).click();
  await page.locator("#w-previsao .marcos li").first().waitFor();
  await page.locator(`#w-previsao ${GRAFICO}`).waitFor();
  const r60 = await ler();
  await page.getByRole("radio", { name: "90 dias" }).click();
  await page.waitForFunction(() => document.querySelectorAll("#w-previsao .marcos li").length === 2);
  await page.locator(`#w-previsao ${GRAFICO}`).waitFor();
  const r90 = await ler();
  const faixa = [await page.locator(".band").count(), (await textoDe(page, "main")).includes("Faixa provável")];
  await ctx.close();
  const fatos = ["R$ 10,005", "R$ 10,00", "R$ 7,33 em 07/10"];
  assert.deepEqual(r30, { valor: "R$ 7,33", sub: "em qui, 5 de novembro · Previsão condicional", marcos: [], fatos, svg: 1 });
  assert.deepEqual(r60, { valor: "R$ 7,33", sub: "em sáb, 5 de dezembro · Previsão condicional", marcos: ["Em 30 dias R$ 7,33"], fatos, svg: 1 });
  assert.deepEqual(r90, { valor: "R$ 7,33", sub: "em seg, 4 de janeiro · Previsão condicional", marcos: ["Em 30 dias R$ 7,33", "Em 60 dias R$ 7,33"], fatos, svg: 1 });
  assert.deepEqual(pedidos, ["", "?dias=60", "?dias=90"]);
  assert.deepEqual(faixa, [0, false]);
  assert.deepEqual(erros, []);
});

test("controle do seletor: o protótipo ainda tem a Faixa provável e o .band", async () => {
  const { ctx, page } = await abrir({ demo: true });
  await page.locator(`#w-hero ${GRAFICO}`).waitFor();
  const r = [await page.locator(".band").count() > 0, (await textoDe(page, "main")).includes("Faixa provável")];
  await ctx.close();
  assert.deepEqual(r, [true, true]);
});

test("Plus: 1 marco, motivos e premissas; convite ao Pro no lugar do gráfico e da lista, sem pedir 60/90", async () => {
  const { ctx, page, erros, pedidos } = await abrir({ plano: "plus", previsao: "plus30_a_conferir" });
  await pronta(page);
  const r = await page.evaluate(() => {
    const m = document.querySelector("main");
    return {
      valor: m.querySelector(".hero-value").textContent, marcos: m.querySelectorAll(".marcos li").length,
      svg: m.querySelectorAll("svg[role=img]").length, grupos: m.querySelectorAll(".grupo").length, radios: m.querySelectorAll("[role=radio]").length,
      horizonte: m.querySelector("#w-previsao .w-aside").textContent,
      convites: [...m.querySelectorAll(".convite")].map((c) => [c.querySelector("p").textContent, c.querySelector("a").getAttribute("href")]),
      motivos: [...m.querySelectorAll("#w-previsao-qualidade .prev-motivos li")].map((li) => li.textContent),
      premissas: m.querySelectorAll(".premissas li").length, nenhum: m.innerText.includes("Nenhum compromisso"),
    };
  });
  await ctx.close();
  assert.deepEqual(r, {
    valor: "R$ 7,33", marcos: 0, svg: 0, grupos: 0, radios: 0, horizonte: "30 dias",
    convites: [["O saldo dia a dia, o menor saldo e os compromissos de cada dia são do Pro.", "/precos"], ["A lista de contas e receitas de cada dia é do Pro.", "/precos"]],
    motivos: ["gastos do dia a dia fora da conta · o real pode ser pior", "pedido pendente na conversa · pode variar para os dois lados"],
    premissas: 4, nenhum: false,
  });
  assert.deepEqual(pedidos, [""]);
  assert.deepEqual(erros, []);
});

test("Essencial: convite ao Plus, nenhum R$ na página", async () => {
  const { ctx, page } = await abrir({ plano: "essencial" });
  await page.locator("#w-previsao .convite").waitFor();
  const r = [await textoDe(page, "#w-previsao .convite p"), (await textoDe(page, "main")).includes("R$")];
  await ctx.close();
  assert.deepEqual(r, ["A previsão é do Plus e do Pro.", false]);
});

test("zero × indisponível: R$ 0,00 só onde a API manda 0; null é 'indisponível', sem gráfico, e a lista segue", async () => {
  const zero = await abrir({ previsao: "zero" });
  await zero.page.locator(`#w-previsao ${GRAFICO}`).waitFor();
  const z = await zero.page.evaluate(() => [document.querySelector("#w-previsao .hero-value").textContent, [...document.querySelectorAll("#w-previsao .prev-facts dd")].map((d) => d.textContent), document.querySelectorAll("#w-previsao svg[role=img]").length]);
  await zero.ctx.close();
  const sem = await abrir({ previsao: "sem_saldo" });
  await pronta(sem.page);
  const s = await sem.page.evaluate(() => {
    const w = document.querySelector("#w-previsao");
    return [w.querySelector(".hero-value").textContent, [...w.querySelectorAll(".prev-facts dd")].map((d) => d.textContent), w.querySelectorAll("svg[role=img]").length,
      /R\$ 0/.test(w.innerText), w.innerText.includes("Sem saldo de partida, não dá para desenhar o saldo dia a dia."),
      document.querySelector("#w-previsao-compromissos .grupo").innerText.replace(/\s+/g, " ")];
  });
  await sem.ctx.close();
  assert.deepEqual(z, ["R$ 0,00", ["R$ 0,00", "R$ 0,00", "R$ 0,00 em 07/10"], 1]);
  assert.deepEqual(s, ["indisponível", ["indisponível", "indisponível", "indisponível"], 0, false, true, "7 qua Aluguel Gasto fixo · 1 ocorrência · 07/10 −R$ 2,675"]);
});

const grupos = (page) => page.evaluate(() => [...document.querySelectorAll("#w-previsao-compromissos .grupo")].map((g) => [g.querySelector(".bill-name").firstChild.textContent, g.querySelector(".bill-amt").textContent, g.querySelector(".bill-status").textContent]));

test("compromissos: desconhecido, homônimos separados, grupo repetido sem soma, expandir pelo teclado, vencido sem 'pago'", async () => {
  const desc = await abrir({ previsao: "desconhecido" });
  await pronta(desc.page);
  const d = await grupos(desc.page);
  await desc.ctx.close();
  const hom = await abrir({ previsao: "recorrencia_fatura_homonimos" });
  await pronta(hom.page);
  const h = await grupos(hom.page);
  await hom.ctx.close();

  const { ctx, page } = await abrir({ previsao: "real_diario" });
  await pronta(page);
  const r = await grupos(page);
  // Tab do 1º grupo (Conta de luz) leva ao 2º (Ônibus); Enter abre, Espaço fecha.
  await page.locator("#w-previsao-compromissos .grupo").first().focus();
  await page.keyboard.press("Tab");
  const focado = await page.evaluate(() => document.activeElement.querySelector(".bill-name")?.firstChild.textContent);
  await page.keyboard.press("Enter");
  const onibus = page.locator("#w-previsao-compromissos .grupo").nth(1);
  const aberto = [await onibus.getAttribute("aria-expanded"), await page.locator(`#${CSS_ESC(await onibus.getAttribute("aria-controls"))} .ocorrencia`).count()];
  await page.keyboard.press(" ");
  const fechado = await onibus.getAttribute("aria-expanded");
  await page.locator("#w-previsao-compromissos .grupo").first().click();
  const luz = (await textoDe(page, "#w-previsao-compromissos .ocorrencia")).replace(/\s+/g, " ");
  const pago = /pago|recebido/i.test(await textoDe(page, "main"));
  await ctx.close();

  assert.deepEqual(d, [["Aluguel", "−R$ 2,675", "Gasto fixo · 1 ocorrência · 07/10"], ["Valor a conferir", "valor desconhecido", "Gasto fixo · 1 ocorrência · sem data · 1 fora do cálculo"]]);
  assert.deepEqual(h.map(([n, v]) => [n, v]), [["Aluguel", "−R$ 1,00"], ["Aluguel", "−R$ 2,675"], ["Cartão", "−R$ 1,005"]]);
  assert.deepEqual(r.map(([n, v]) => [n, v]), [["Conta de luz", "−R$ 89,90"], ["Ônibus", "3 × −R$ 25,00"], ["Spotify", "−R$ 21,90"], ["Cartão Nubank", "valores diferentes"], ["Salário", "+R$ 2.100,00"]]);
  assert.match(r[0][2], /venceu/);
  assert.match(r[2][2], /1 fora do cálculo/);
  assert.equal(focado, "Ônibus");
  assert.deepEqual(aberto, ["true", 3]);
  assert.equal(fechado, "false");
  assert.match(luz, /^04\/10 −R\$ 89,90 venceu 04\/10 a conferir pagamento do boleto a conferir · o real pode ser melhor$/);
  assert.equal(pago, false);
});
// O id do useId tem ":" ("«r1»" no React 19 sai como ":r1:"): escapa para o seletor.
const CSS_ESC = (id) => id.replace(/[^a-zA-Z0-9_-]/g, (c) => `\\${c}`);

test("lista vazia × Plus: [] diz 'Nenhum compromisso'; null (Plus) é convite", async () => {
  const vazio = await abrir({ previsao: "vazio" });
  await pronta(vazio.page);
  const v = await textoDe(vazio.page, "#w-previsao-compromissos .w-body");
  await vazio.ctx.close();
  const plus = await abrir({ plano: "plus" });
  await pronta(plus.page);
  const p = await textoDe(plus.page, "#w-previsao-compromissos .w-body");
  await plus.ctx.close();
  assert.equal(v, "Nenhum compromisso conhecido nos próximos 30 dias.");
  assert.match(p, /^A lista de contas e receitas de cada dia é do Pro\.\s+Ver planos$/);
});

test("pior dia negativo com final positivo: os dois aparecem, sem 'cabe' nem 'seguro'", async () => {
  const { ctx, page } = await abrir({ previsao: "pior_negativo" });
  await pronta(page);
  const r = await page.evaluate(() => {
    const dd = [...document.querySelectorAll("#w-previsao .prev-facts dd")].at(-1);
    return [document.querySelector("#w-previsao .hero-value").textContent, dd.textContent, dd.className, document.querySelector("main").innerText];
  });
  await ctx.close();
  assert.deepEqual(r.slice(0, 3), ["R$ 120,00", "−R$ 5,32 em 10/10", "warn"]);
  assert.match(r[3], /Fica negativo em 10\/10, desde 06\/10: Aluguel −R\$ 15,32/);
  assert.doesNotMatch(r[3], /cabe|seguro|tranquilo|sobra com folga/i);
});

// Compromisso de valor zero: o sinal vem da direção, mas zero sai "R$ 0,00" como o moneyText,
// nunca "−R$ 0,00" (saída) nem "+R$ 0,00" (entrada), em qualquer escala ("-0", "0.000", "0.00").
test("compromisso de valor zero: 'R$ 0,00' sem sinal na lista, na ocorrência e nas causas do pior dia", async () => {
  const corpo = structuredClone(RESPOSTAS.previsao.pior_negativo);
  corpo.pior_dia.causas[0].valor = "0.00";
  corpo.compromissos[0].ocorrencias[0].valor = "-0";
  corpo.compromissos[1].ocorrencias[0].valor = "0.000";
  const { ctx, page } = await abrir({ previsao: "pior_negativo" });
  await servirPrevisao(ctx, corpo);
  await page.reload();
  await pronta(page);
  const g = await grupos(page);
  await page.locator("#w-previsao-compromissos .grupo").last().click();
  const oc = await textoDe(page, "#w-previsao-compromissos .ocorrencia .bill-amt");
  const lede = await textoDe(page, "#w-previsao p.w-lede");
  await ctx.close();
  assert.deepEqual({ grupos: g.map(([n, v]) => [n, v]), oc, causa: lede.split(": ").at(-1) },
    { grupos: [["Aluguel", "R$ 0,00"], ["Salário", "R$ 0,00"]], oc: "R$ 0,00", causa: "Aluguel R$ 0,00" });
});

test("qualidade: estado, motivos em português com a direção, código novo sai cru, 4 premissas", async () => {
  const { ctx, page } = await abrir({ previsao: "real_diario" });
  const novo = structuredClone(RESPOSTAS.previsao.pendencia);
  novo.motivos.push({ codigo: "codigo_novo_x", direcao_do_erro: "ambos" });
  await pronta(page);
  const r = await page.evaluate(() => {
    const q = document.querySelector("#w-previsao-qualidade");
    return [q.querySelector(".prev-base .selo").textContent, [...q.querySelectorAll(".prev-motivos li")].map((li) => li.textContent), q.querySelectorAll(".premissas li").length];
  });
  await servirPrevisao(ctx, novo);
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange"))); // o focusManager do TanStack ouve na window
  await page.waitForFunction(() => document.querySelector("#w-previsao-qualidade")?.innerText.includes("codigo_novo_x"));
  const cru = await page.evaluate(() => [...document.querySelectorAll("#w-previsao-qualidade .prev-motivos li")].map((li) => li.textContent));
  await ctx.close();
  assert.deepEqual(r, ["A conferir", [
    "gastos do dia a dia fora da conta · o real pode ser pior", "receita não garantida · o real pode ser pior",
    "pagamento do boleto a conferir · o real pode ser melhor", "pode já estar na fatura · o real pode ser pior",
    "pagamento da fatura a conferir · o real pode ser melhor"], 4]);
  assert.deepEqual(cru, ["pedido pendente na conversa · pode variar para os dois lados", "codigo_novo_x · pode variar para os dois lados"]);
});

// §0.7: os códigos moram no Python e os rótulos no Selos.tsx. Servidos todos de uma vez,
// nenhum pode sair cru na tela.
test("paridade: todo `_motivo(s, '…')` do motor (cashflow_snapshot.py e previsao_recorrencias.py) tem rótulo em português", async () => {
  const py = ["cashflow_snapshot.py", "previsao_recorrencias.py"].map((f) => readFileSync(join(RAIZ, "core", "services", f), "utf8")).join("\n");
  const codigos = [...new Set([...py.matchAll(/_motivo\(s, '([a-z_]+)'/g)].map((m) => m[1]))];
  assert.ok(codigos.length >= 20, `poucos códigos lidos: ${codigos}`);
  const corpo = { ...RESPOSTAS.previsao.pro30, estado: "a_conferir", motivos: codigos.map((codigo) => ({ codigo, direcao_do_erro: "ambos" })) };
  const { ctx, page } = await abrir();
  await servirPrevisao(ctx, corpo);
  await page.reload();
  await page.locator("#w-previsao-qualidade .prev-motivos").waitFor();
  const rotulos = await page.locator("#w-previsao-qualidade .prev-motivos b").allTextContents();
  await ctx.close();
  assert.equal(rotulos.length, codigos.length);
  assert.deepEqual(rotulos.filter((t) => codigos.includes(t)), []);
});

test("integração: Cmd-K e Piggy reais sem horizonte de exemplo, sem seletor de mês; o protótipo como era", async () => {
  const r = {};
  for (const demo of [false, true]) {
    const { ctx, page, pedidos } = await abrir({ demo }, "/");
    await page.keyboard.press("Control+k");
    await page.locator(".cmdk input").fill("Previsão");
    const cmdk = await page.locator(".cmdk-item").allTextContents();
    await page.keyboard.press("Escape");
    const acao = page.locator('[data-widget-id="piggy"] button', { hasText: /Ver (90 dias|a previsão)/ });
    const rotulo = await acao.textContent();
    await acao.click();
    await page.waitForFunction(() => location.hash === "#/previsao");
    await page.locator("#page-title").waitFor();
    if (!demo) await pronta(page);
    r[demo ? "prototipo" : "real"] = { cmdk, rotulo: rotulo.trim(), mes: await page.locator(".month-switch").count(), pedidos: [...new Set(pedidos)] };
    await ctx.close();
  }
  assert.deepEqual(r.real, { cmdk: ["Previsão"], rotulo: "Ver a previsão", mes: 0, pedidos: [""] });
  assert.deepEqual(r.prototipo, { cmdk: ["Previsão", "Previsão: fim do mês", "Previsão: 30 dias", "Previsão: 90 dias"], rotulo: "Ver 90 dias", mes: 1, pedidos: [] });
});
