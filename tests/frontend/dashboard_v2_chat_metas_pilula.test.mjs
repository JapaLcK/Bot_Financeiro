// A pílula de meta do chat (parts/MetaPilula.tsx), como a do app: nenhuma frase na tela (elas moram
// no aria-label), reserva em blocos por mês, ✓ na batida, ⚠ no aviso, "+N" que expande, sem botão de editar.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { iniciar, encerrar, CHIP_META, abrir, resposta, ultima, chip, pilulas, varios, servirMetas, igual, batida } from "./_chat_metas.mjs";

before(iniciar);
after(encerrar);

test('"+N" expande as outras metas aqui mesmo (teclado) e "Mostrar menos" recolhe, sem sair da conversa', async () => {
  const { ctx, page, gets } = await abrir();
  await chip(page, CHIP_META);
  await resposta(page);
  const mais = ultima(page).locator(".pilula-mais");
  await mais.waitFor();
  const antes = [await pilulas(page).count(), await mais.getAttribute("aria-expanded"), await mais.getAttribute("aria-label")];
  await mais.focus();
  await page.keyboard.press("Enter");
  const aberto = [await pilulas(page).count(), await mais.textContent(), await mais.getAttribute("aria-expanded"), await mais.getAttribute("aria-label")];
  const emFoco = await page.evaluate(() => document.activeElement.className);
  await page.keyboard.press("Enter");
  const depois = [await pilulas(page).count(), await mais.textContent()];
  const hash = await page.evaluate(() => location.hash);
  await ctx.close();
  assert.deepEqual(antes, [5, "false", "+6, mostrar mais 6 metas"]);
  assert.deepEqual(aberto, [11, "Mostrar menos", "true", "Mostrar menos metas"]);
  assert.match(emFoco, /pilula-mais/); // o foco fica no botão
  assert.deepEqual(depois, [5, "+6"]);
  assert.equal(hash, "#/piggy");
  assert.deepEqual(gets, ["GET"]); // expandir não pede a rota de novo
});

test("sem custo mensal: a reserva sem alvo e a meta sem investimentos têm ⚠ e a frase só no aria-label, sem botão e sem R$", async () => {
  const { ctx, page } = await abrir({ metas: "sem_custo" });
  await chip(page, CHIP_META);
  await resposta(page);
  await pilulas(page).nth(1).waitFor();
  const dados = await pilulas(page).evaluateAll((ps) => ps.map((p) => ({
    rotulo: p.getAttribute("aria-label"), avisos: p.querySelectorAll(".ph-warning-circle").length, pct: p.querySelectorAll(".pilula-pct").length,
    barras: p.querySelectorAll(".meter").length, cheios: p.querySelectorAll(".cheio").length, blocos: p.querySelectorAll(".blocos > span").length,
  })));
  const tudo = await ultima(page).innerText();
  const interativos = await ultima(page).locator(".pilula button, .pilula a").count();
  await ctx.close();
  assert.equal(dados.length, 2);
  assert.match(dados[0].rotulo, /^Reserva de emergência, R\$ 800,00 guardado,.*Seu plano não calcula as contas fixas\. Defina o valor você mesmo\./);
  assert.deepEqual([dados[0].avisos, dados[0].pct, dados[0].barras, dados[0].cheios, dados[0].blocos], [1, 0, 0, 0, 6]);
  assert.equal(dados[1].rotulo, "Viagem, Seu plano atual não inclui investimentos.");
  assert.deepEqual([dados[1].avisos, dados[1].pct, dados[1].barras], [1, 0, 0]); // sem saldo: nem % nem barra
  assert.equal(interativos, 0);
  assert.doesNotMatch(tudo, /R\$|—|Seu plano|Defina|Nesse ritmo/); // a frase só vai no rótulo
});

test("conexão pausada (0,00) tem ⚠ com o motivo no rótulo; o estresse (nome e valores enormes) não estoura em 390", async () => {
  const { ctx, page } = await abrir({ width: 390 });
  const pausada = varios.metas.find((m) => m.nome === "Conta pausada");
  await servirMetas(ctx, [varios.metas[0], pausada, varios.metas[1]]);
  await chip(page, CHIP_META);
  await resposta(page);
  await pilulas(page).nth(2).waitFor();
  const p = await pilulas(page).nth(1).evaluate((el) => [el.getAttribute("aria-label"), el.querySelectorAll(".ph-warning-circle").length, el.querySelector(".pilula-pct").textContent]);
  await ctx.close();
  assert.match(p[0], /^Conta pausada, 0%, R\$ 0,00 de R\$ 4\.000,00.*A conexão com o banco está pausada\./);
  assert.deepEqual(p.slice(1), [1, "0%"]);
  const e = await abrir({ width: 390, metas: "estresse" });
  await chip(e.page, CHIP_META);
  await resposta(e.page);
  await pilulas(e.page).nth(2).waitFor();
  const docLargo = await e.page.evaluate(() => document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth);
  const estoura = await ultima(e.page).evaluate((li) => [li.scrollWidth > li.clientWidth, [...li.querySelectorAll(".pilula")].some((x) => x.scrollWidth > x.clientWidth)]);
  await e.ctx.close();
  assert.equal(docLargo, 0);
  assert.deepEqual(estoura, [false, false]);
});

// Blocos cheios = floor((alvo - falta) × meses / alvo) em centavos. O % do servidor é truncado
// (12000 de 18000 = 66%) e daria 3 blocos; 17999,99 não pode fechar os 6.
test("reserva em blocos: 4 de 6 meses (66% não vale 3), 17999,99 de 18000 deixa 1 vazio, e a batida fecha todos em verde com ✓", async () => {
  const reserva = varios.metas[0];
  const leia = async (r) => {
    const { ctx, page } = await abrir();
    await servirMetas(ctx, [r]);
    await chip(page, CHIP_META);
    await resposta(page);
    await pilulas(page).first().waitFor();
    const p = pilulas(page).first();
    const out = await p.evaluate((el) => [el.querySelectorAll(".cheio").length, el.querySelectorAll(".cheio.ok").length, el.querySelectorAll(".ph-check-circle").length, el.querySelectorAll(".pilula-pct").length, el.getAttribute("aria-label")]);
    await ctx.close();
    return out;
  };
  const quatro = await leia(igual(reserva, { atual: "12000.00", falta: "6000.00", percentual: 66 }));
  const quase = await leia(igual(reserva, { atual: "17999.99", falta: "0.01", percentual: 99 }));
  const cheia = await leia(igual(reserva, batida));
  assert.deepEqual(quatro.slice(0, 4), [4, 0, 0, 1]);
  assert.match(quatro[4], /4 de 6 meses/);
  assert.deepEqual(quase.slice(0, 4), [5, 0, 0, 1]);
  assert.match(quase[4], /5 de 6 meses/);
  assert.deepEqual(cheia.slice(0, 4), [6, 6, 1, 0]); // verde, ✓ no lugar do %
  assert.match(cheia[4], /6 de 6 meses.*meta batida/);
});

test("meta batida: ✓ no lugar do %, barra verde e 'meta batida' no rótulo; a comum ainda tem barra", async () => {
  const { ctx, page } = await abrir();
  await servirMetas(ctx, [igual(varios.metas[0], batida), igual(varios.metas[1], { atual: "20000.00", falta: "0.00", percentual: 100, chega_em: null }), varios.metas[2]]);
  await chip(page, CHIP_META);
  await resposta(page);
  await pilulas(page).nth(2).waitFor();
  const d = await pilulas(page).evaluateAll((ps) => ps.map((p) => [p.querySelectorAll(".ph-check-circle").length, p.querySelectorAll(".meter.ok").length, p.querySelectorAll(".meter").length, p.getAttribute("aria-label")]));
  await ctx.close();
  assert.deepEqual(d.map((x) => x.slice(0, 3)), [[1, 0, 0], [1, 1, 1], [0, 0, 1]]);
  assert.match(d[1][3], /^Viagem pro Japão, 100%, R\$ 20\.000,00 de R\$ 20\.000,00, meta batida, prazo junho de 2027$/);
});

test("nome hostil é texto: nada executa, nenhuma tag dentro da pílula", async () => {
  const { ctx, page, erros } = await abrir();
  const nome = '<img src=x onerror="window.__x=1">';
  await servirMetas(ctx, [varios.metas[0], igual(varios.metas[1], { nome }), ...varios.metas.slice(2)]);
  await chip(page, CHIP_META);
  await resposta(page);
  await pilulas(page).nth(1).waitFor();
  const h = await pilulas(page).nth(1).locator(".pilula-nome").textContent();
  const rotulo = await pilulas(page).nth(1).getAttribute("aria-label");
  const imgs = await ultima(page).locator(".pilula img").count();
  const x = await page.evaluate(() => window.__x);
  await ctx.close();
  assert.equal(h, nome);
  assert.ok(rotulo.startsWith(`${nome}, 60%`));
  assert.equal(imgs, 0);
  assert.equal(x, undefined);
  assert.deepEqual(erros, []);
});

// Cor de série (styles/metas.css): a reserva é sempre o rosa da marca; as outras ganham uma de 4 pelo `id`
// da meta, não pela posição; a batida é verde; nenhuma série se parece com os estados (verde, âmbar, laranja).
const lerCores = (page) => pilulas(page).evaluateAll((ps) => ps.map((p) => {
  const el = p.querySelector(".meter > span, .blocos > .cheio"), ic = getComputedStyle(p.querySelector(".pilula-icone"));
  return { nome: p.querySelector(".pilula-nome").textContent, serie: p.dataset.serie ?? null, cor: el ? getComputedStyle(el).backgroundColor : null, icone: ic.color };
}));
const porNome = (xs) => Object.fromEntries(xs.map((x) => [x.nome, [x.serie, x.cor, x.icone]]));

test("cor de série: a reserva é rosa; a mesma meta tem a mesma cor em 'varios', expandida e com a lista reordenada", async () => {
  const { ctx, page } = await abrir();
  await chip(page, CHIP_META);
  await resposta(page);
  await pilulas(page).nth(4).waitFor();
  const curta = await lerCores(page);
  await ultima(page).locator(".pilula-mais").click();
  const longa = await lerCores(page);
  await ctx.close();
  const r = await abrir();
  await servirMetas(r.ctx, [...varios.metas].reverse()); // outra ordem: a reserva segue primeiro (emOrdem), as outras trocam de posição
  await chip(r.page, CHIP_META);
  await resposta(r.page);
  await r.page.waitForFunction(() => document.querySelectorAll(".chat > .msg-piggy .pilula").length === 5);
  await r.page.locator(".pilula-mais").click();
  const invertida = await lerCores(r.page);
  await r.ctx.close();
  const [c, l, i] = [porNome(curta), porNome(longa), porNome(invertida)];
  for (const x of longa.filter((y) => y.cor && y.cor !== "rgb(61, 220, 151)")) assert.equal(x.icone, x.cor, x.nome); // o ícone leva a cor da barra (batida: barra verde; sem saldo: sem barra)
  assert.deepEqual(c["Reserva de emergência"], [null, "rgb(255, 45, 142)", "rgb(255, 45, 142)"]); // o --pink da marca, sem série
  assert.deepEqual(curta.slice(1).map((x) => x.serie), ["3", "4", "1", "2"]); // ids 2..5: uma série diferente por meta
  assert.equal(new Set(curta.slice(1).map((x) => x.cor)).size, 4);
  assert.equal(new Set(curta.map((x) => x.cor)).size, 5); // com a reserva, 5 cores distintas
  for (const [nome, v] of Object.entries(c)) assert.deepEqual(l[nome], v, nome); // expandir não troca a cor de quem já estava
  assert.equal(Object.keys(l).length, 11);
  assert.deepEqual(i, l); // nem reordenar a lista
});

test("cor de série: meta batida é verde (✓) por cima da série, e nenhuma série é verde, âmbar ou laranja de estado nem sai do contraste", async () => {
  const { ctx, page } = await abrir();
  await servirMetas(ctx, [igual(varios.metas[0], batida), igual(varios.metas[1], { atual: "20000.00", falta: "0.00", percentual: 100, chega_em: null }), varios.metas[2]]);
  await chip(page, CHIP_META);
  await resposta(page);
  await pilulas(page).nth(2).waitFor();
  const cores = await lerCores(page);
  const t = await page.evaluate(() => {
    const raiz = getComputedStyle(document.documentElement), chat = getComputedStyle(document.querySelector(".metas-chat"));
    const v = (s, n) => s.getPropertyValue(n).trim();
    return { series: [1, 2, 3, 4].map((n) => v(chat, `--serie-${n}`)), estados: ["--gain", "--warn", "--alert"].map((n) => v(raiz, n)), planos: ["--plane-2", "--plane-3"].map((n) => v(raiz, n)), pink: v(raiz, "--pink") };
  });
  await ctx.close();
  assert.equal(cores[0].cor, "rgb(61, 220, 151)"); // reserva batida: blocos verdes (o --gain), não o rosa
  assert.equal(cores[1].cor, "rgb(61, 220, 151)"); // meta batida: barra verde, mesmo com série
  assert.equal(cores[1].serie, "3");
  assert.notEqual(cores[2].cor, "rgb(61, 220, 151)");
  const rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
  const lum = (h) => { const [r, g, b] = rgb(h).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const razao = (a, b) => (Math.max(lum(a), lum(b)) + 0.05) / (Math.min(lum(a), lum(b)) + 0.05);
  const matiz = (h) => { const [r, g, b] = rgb(h), mx = Math.max(r, g, b), d = mx - Math.min(r, g, b); return ((mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4) * 60 + 360) % 360; };
  const dist = (a, b) => Math.min(Math.abs(a - b), 360 - Math.abs(a - b));
  for (const s of [...t.series, t.pink]) for (const p of t.planos) assert.ok(razao(s, p) >= 3, `${s} contra ${p}: ${razao(s, p).toFixed(2)}`); // não-texto: 3:1
  for (const s of t.series) for (const e of t.estados) assert.ok(dist(matiz(s), matiz(e)) >= 25, `${s} perto do estado ${e}`); // matiz a 25° ou mais
  assert.equal(new Set(t.series).size, 4);
  assert.ok(!t.series.includes(t.pink));
});

// Lacunas da 1ª passada do Tester: cada teste abaixo mata um mutante (nomeado no comentário).
const reservaBase = varios.metas[0];
const rotulos = (page) => pilulas(page).evaluateAll((ps) => ps.map((p) => p.getAttribute("aria-label")));
const perguntarMetas = async (page) => { await chip(page, CHIP_META); await resposta(page); await pilulas(page).first().waitFor(); };

// mutante: tirar `emOrdem` da lista do chat
test("a reserva que vem DEPOIS das outras sobe para a primeira pílula", async () => {
  const { ctx, page } = await abrir();
  await servirMetas(ctx, [...varios.metas.slice(1, 6), reservaBase]);
  await perguntarMetas(page);
  const nomes = await ultima(page).locator(".pilula-nome").allInnerTexts();
  await ctx.close();
  assert.deepEqual(nomes, ["Reserva de emergência", "Viagem pro Japão", "Notebook novo", "Entrada do apê", "Curso de inglês"]);
});

// mutante: `visiveis = VISIVEIS` (a vaga "Monte no painel" deixaria de contar)
test("sem reserva: a vaga 'Monte no painel' conta entre as 5 (4 metas + vaga), e o '+N' é do resto", async () => {
  for (const [n, mais] of [[5, "+1"], [6, "+2"]]) {
    const { ctx, page } = await abrir();
    await servirMetas(ctx, varios.metas.slice(1, 1 + n));
    await perguntarMetas(page);
    const d = [await pilulas(page).count(), await ultima(page).locator(".pilula").first().textContent(), await ultima(page).locator(".pilula-mais").textContent()];
    await ultima(page).locator(".pilula-mais").click();
    const aberto = await pilulas(page).count();
    await ctx.close();
    assert.deepEqual(d, [5, "Monte no painel", mais], `${n} metas`);
    assert.equal(aberto, n + 1);
  }
});

// mutante: tirar `|| falta > alvo` de `blocosCheios` (daria -1 bloco)
test("saldo negativo (falta maior que o alvo): 0 blocos e '0 de 6 meses', nunca -1", async () => {
  const { ctx, page } = await abrir();
  await servirMetas(ctx, [igual(reservaBase, { atual: "-5000.00", falta: "23000.00", percentual: 0, chega_em: null })]);
  await perguntarMetas(page);
  const [r] = await rotulos(page).then((xs) => xs.slice(0, 1));
  const cheios = await pilulas(page).first().locator(".cheio").count();
  await ctx.close();
  assert.match(r, /, 0 de 6 meses,/);
  assert.doesNotMatch(r, /-1/);
  assert.equal(cheios, 0);
});

// mutante: `repeat(6, …)` em `.blocos`
test("reserva de 24 meses: 12 blocos por linha (2 linhas) e nada estoura em 320 px", async () => {
  const { ctx, page } = await abrir({ width: 320 });
  await servirMetas(ctx, [igual(reservaBase, { meses: 24, alvo: "72000.00", atual: "30000.00", falta: "42000.00", percentual: 41 })]);
  await perguntarMetas(page);
  const linhas = await pilulas(page).first().locator(".blocos > span").evaluateAll((bs) => {
    const por = {};
    for (const b of bs) { const t = Math.round(b.getBoundingClientRect().top); por[t] = (por[t] ?? 0) + 1; }
    return Object.values(por);
  });
  const cheios = await pilulas(page).first().locator(".cheio").count();
  const estoura = await pilulas(page).first().evaluate((p) => p.scrollWidth > p.clientWidth);
  const docLargo = await page.evaluate(() => document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth);
  await ctx.close();
  assert.deepEqual(linhas, [12, 12]);
  assert.equal(cheios, 10); // floor(30000 × 24 / 72000)
  assert.equal(estoura, false);
  assert.equal(docLargo, 0);
});

// mutantes: tirar `reserva && !!fraseCusto(custo)` do ⚠ e `reserva && fraseCusto(custo)` do rótulo
test("reserva com saldo e alvo, mas custo lido do banco com falha: ⚠ e 'pode estar velho' no rótulo", async () => {
  const { ctx, page } = await abrir();
  await ctx.route("**/api/v2/metas", (r) => r.fulfill({ json: { ...varios, custo_mensal: { ...varios.custo_mensal, motivos: ["recorrencias_banco_nao_lidas"] }, metas: [reservaBase] } }));
  await perguntarMetas(page);
  const [r] = await rotulos(page);
  const avisos = await pilulas(page).first().locator(".ph-warning-circle").count();
  await ctx.close();
  assert.match(r, /Não consegui ler as cobranças do banco agora\. O valor pode estar velho\./);
  assert.equal(avisos, 1);
});

// mutante: `ritmoDito = null`
test("ritmo sem número por histórico curto: a frase vai no rótulo (e não é ⚠, é a explicação do ritmo)", async () => {
  const { ctx, page } = await abrir();
  const curto = igual(varios.metas[1], { ritmo: { valor: null, dias_de_historico: 3, parcial: true, motivos: ["historico_curto"] }, chega_em: null });
  await servirMetas(ctx, [reservaBase, curto]);
  await perguntarMetas(page);
  const rs = await rotulos(page);
  const avisos = await pilulas(page).nth(1).locator(".ph-warning-circle").count();
  await ctx.close();
  assert.match(rs[1], /Ainda estou juntando histórico \(3 de 7 dias\)/);
  assert.equal(avisos, 0);
});

// mutante: tirar "reserva de emergência" do rótulo quando o nome é outro
test("reserva com outro nome ('Minha caixinha'): o rótulo diz que é a reserva de emergência", async () => {
  const { ctx, page } = await abrir();
  await servirMetas(ctx, [igual(reservaBase, { nome: "Minha caixinha" })]);
  await perguntarMetas(page);
  const [r] = await rotulos(page);
  await ctx.close();
  assert.match(r, /^Minha caixinha, 25%, reserva de emergência, 1 de 6 meses,/);
});

// §0.7: `blocosCheios` é copiada do app (metasChat.tsx). A tabela fixa (a do Manager, conferida com a do app) prende a conta.
test("blocos cheios = floor((alvo - falta) × meses / alvo) em centavos: a mesma tabela do app", async () => {
  const casos = [["18000.00", "6000.00", 6, 4], ["18000.00", "0.01", 6, 5], ["18000.00", "0.00", 6, 6], ["18000.00", "3000.01", 6, 4], ["18000.00", "2999.99", 6, 5],
    ["0.06", "0.01", 6, 5], ["18000.00", "18000.00", 6, 0], ["18000.00", "20000.00", 6, 0], ["100.00", "33.34", 3, 1], ["999999999.99", "0.01", 24, 23],
    ["18000.5", "9000.25", 12, 6], ["18000", "0", 6, 6]];
  const { ctx, page } = await abrir();
  const lidos = [];
  for (const [alvo, falta, meses] of casos) {
    await servirMetas(ctx, [igual(reservaBase, { alvo, falta, meses, atual: "1.00", percentual: 50, chega_em: null })]);
    await page.locator("#askbar-input").fill("minhas metas");
    // o cache da pergunta anterior aparece antes da resposta nova: espera a resposta da rota
    await Promise.all([page.waitForResponse((r) => r.url().endsWith("/api/v2/metas")), page.locator("#askbar-input").press("Enter")]);
    await page.waitForFunction((n) => document.querySelectorAll(".chat > .msg-piggy").length === n, lidos.length + 1);
    await pilulas(page).first().waitFor();
    await page.waitForTimeout(150);
    lidos.push(await pilulas(page).first().locator(".cheio").count());
  }
  await ctx.close();
  assert.deepEqual(lidos, casos.map((c) => c[3]));
});

// Divergência antiga com o app: código de motivo DESCONHECIDO nunca sai cru no rótulo; vira uma frase só.
test("motivo desconhecido do servidor: uma frase genérica no rótulo (uma vez, sem o código cru) e ⚠", async () => {
  const { ctx, page } = await abrir();
  const nova = igual(varios.metas[1], { motivos: ["motivo_novo"], ritmo: { ...varios.metas[1].ritmo, motivos: ["outro_motivo_novo"] } });
  await servirMetas(ctx, [reservaBase, nova]);
  await perguntarMetas(page);
  const [, r] = await rotulos(page);
  const avisos = await pilulas(page).nth(1).locator(".ph-warning-circle").count();
  await ctx.close();
  assert.equal(r.split("Há informações a conferir nesta meta.").length, 2); // uma vez
  assert.doesNotMatch(r, /motivo_novo/);
  assert.equal(avisos, 1);
});
