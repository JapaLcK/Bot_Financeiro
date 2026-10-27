// O que o card da IA do /painel COPIA do app tem de bater com a fonte (CLAUDE.md §0.7). Os dois projetos npm não se importam, então o teste lê o TEXTO dos
// dois lados, sem navegador, e EXECUTA as funções copiadas (as linhas saem do .ts, perdem os tipos e rodam), no molde de logo_banco_paridade.test.mjs:
//   · `segmentosDeContas` e `MOTIVOS_SEM_EFEITO_NO_SALDO` (barra de composição do card `contas`): webapp/src/dashboard/lib/bancos.ts × app/src/features/painel/composicaoContas.ts;
//   · os dois pedidos ao chat do card `metas` (parts/cardIa/metas.tsx × metasChat.tsx);
//   · o `limite` da expansão de lançamentos (parts/cardIa/LancamentosDoCard.tsx × contasResumo.tsx);
//   · PR 4: a % de cada categoria e a soma exata (parts/cardIa/decimais.ts × app decimal.ts, pela tabela tests/fixtures/percentual_categorias.json que o servidor também
//     usa) e o título do card `periodo` (parts/cardIa/resumo.tsx × cardPeriodo.tsx, pela tabela do teste do app).
//   · PR 6 (cards legados): o título de TODA entrada do `CARDS` com título fixo, as 3 funções de `legado.ts` e o `pctTexto` do rendimento (executados contra os do app) e os textos copiados.
// DIVERGÊNCIA DECLARADA (decisão do dono, 2026-10-21): no card o Nubank é sempre roxo (#820AD1), no app e nas outras telas cai na tinta pela regra de 3:1 do
// `corDoBanco`. A tabela abaixo afirma as duas coisas: o roxo no painel E a tinta no app (se um lado mudar, o teste avisa). Nenhuma outra cor diverge.
// Cada comparação falha ALTO se o formato mudar, e os negativos provam que uma palavra, uma lista ou uma regra trocada só de um lado é vista.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import { join } from "node:path";
import { RAIZ } from "./_painel.mjs";
import { decimal, monthTitle } from "../../webapp/src/dashboard/lib/format.js";

const ler = (...p) => readFileSync(join(RAIZ, ...p), "utf8");
const WEB = () => ler("webapp", "src", "dashboard", "lib", "bancos.ts");
const APP = () => ler("app", "src", "features", "painel", "composicaoContas.ts");
const CONTRASTE = () => ler("app", "src", "ui", "contraste.ts");
const TOKENS = () => ler("app", "src", "ui", "tokens.ts");
const METAS_WEB = () => ler("webapp", "src", "dashboard", "parts", "cardIa", "metas.tsx");
const METAS_APP = () => ler("app", "src", "features", "painel", "metasChat.tsx");
const LANC_WEB = () => ler("webapp", "src", "dashboard", "parts", "cardIa", "LancamentosDoCard.tsx");
const LANC_APP = () => ler("app", "src", "features", "painel", "contasResumo.tsx");

const NUBANK = "#820AD1";
const TINTA = ["var(--ink)", "var(--ink-2)", "var(--ink-3)"]; // a escala de tinta do painel; o app a recebe como `cores`
const CORES = { ink: TINTA[0], inkMuted: TINTA[1], inkFaint: TINTA[2], surface: "#101013" };

const corta = (texto, de, ate, rotulo) => {
  const a = texto.indexOf(de), b = texto.indexOf(ate, a);
  assert.ok(a >= 0 && b > a, `${rotulo}: o formato mudou (${de} … ${ate})`);
  return texto.slice(a, b);
};
const semImport = (t) => t.replace(/^import .*$/gm, "").replace(/^export /gm, "");
// O lado do painel: do CORES_BANCO até a cor do conector (inclui corDoBanco, MOTIVOS…, corDoBancoNoCard e segmentosDeContas).
const painel = (fonte) => new Function("decimal", `${stripTypeScriptTypes(semImport(corta(fonte, "export const CORES_BANCO", "// A cor do conector", "bancos.ts")))}
return { segmentosDeContas, MOTIVOS_SEM_EFEITO_NO_SALDO };`)(decimal);
// O lado do app: contraste.ts até o `type Chave`, o `coresBanco` do tokens.ts e o composicaoContas.ts inteiro.
const app = (fonte) => {
  const contraste = corta(CONTRASTE(), "function canal", "type Chave", "contraste.ts");
  const cores = /export const coresBanco[^=]*=\s*\{[^}]*\};/.exec(TOKENS());
  assert.ok(cores, "tokens.ts: o coresBanco mudou de formato");
  return new Function(`${stripTypeScriptTypes(semImport(contraste))}\n${stripTypeScriptTypes(semImport(cores[0]))}\n${stripTypeScriptTypes(semImport(fonte))}
return { segmentosDeContas, MOTIVOS_SEM_EFEITO_NO_SALDO };`)();
};

const c = (id, instituicao, saldo, extra = {}) => ({ id, instituicao, nome: "Conta", saldo, moeda: "BRL", no_total: true, conexao: "updated", sincronizado_em: null, motivos: [], ...extra });
const caso = (total, carteira, contas, motivos = ["carteira_nao_confirmada"]) => ({ total, motivos, fora_do_total: 0, carteira: { saldo: carteira, motivos: [] }, contas });
const TABELA = {
  "três segmentos que fecham": caso("310.00", "100.00", [c(1, "Itaú", "200.00"), c(2, "Inter", "10.00")]),
  "com Nubank (a exceção do dono)": caso("1300.00", "100.00", [c(1, "Nubank", "1000.00"), c(2, "Itaú", "200.00")]),
  "Nubank com acento e caixa": caso("500.00", "0.00", [c(1, "NÚBANK S.A.", "300.00"), c(2, "Bradesco", "200.00")]),
  "banco desconhecido cai na tinta": caso("30.00", "10.00", [c(1, "Banco Zé", "10.00"), c(2, null, "10.00")]),
  "a soma não fecha com o total": caso("311.00", "100.00", [c(1, "Itaú", "200.00"), c(2, "Inter", "10.00")]),
  "banco desatualizado esconde a barra": caso("310.00", "100.00", [c(1, "Itaú", "200.00", { motivos: ["banco_desatualizado"] }), c(2, "Inter", "10.00")], ["carteira_nao_confirmada", "banco_desatualizado"]),
  "código novo do servidor esconde a barra": caso("310.00", "100.00", [c(1, "Itaú", "200.00"), c(2, "Inter", "10.00")], ["motivo_do_futuro"]),
  "só um segmento não é composição": caso("100.00", "100.00", []),
  "conta em dólar fica fora e a barra fecha": caso("310.00", "100.00", [c(1, "Itaú", "200.00"), c(2, "Inter", "10.00"), c(3, "Wise", "30.00", { moeda: "USD", no_total: false, motivos: ["outra_moeda"] })], ["carteira_nao_confirmada", "outra_moeda"]),
  "saldo negativo quebra a soma": caso("90.00", "100.00", [c(1, "Itaú", "200.00"), c(2, "Inter", "-210.00")]),
  "carteira zerada": caso("210.00", "0.00", [c(1, "Itaú", "200.00"), c(2, "Inter", "10.00")]),
  "conta fora do total não entra": caso("110.00", "100.00", [c(1, "Itaú", "10.00"), c(2, "Inter", "50.00", { no_total: false })]),
  "centavos exatos": caso("0.30", "0.10", [c(1, "Itaú", "0.20")]),
};

const reais = (v) => Math.round(Number(v) * 100);
const quemFicaNaBarra = (d) => [...(reais(d.carteira.saldo) > 0 ? [{ nome: "" }] : []),
  ...d.contas.filter((x) => x.moeda === "BRL" && x.no_total && reais(x.saldo) > 0).map((x) => ({ nome: x.instituicao ?? "" }))];

// Roda a tabela nos dois lados e devolve as diferenças (vazio = paridade).
const diferencas = (webTs, appTs) => {
  const w = painel(webTs), a = app(appTs), out = [];
  if (w.MOTIVOS_SEM_EFEITO_NO_SALDO.join() !== a.MOTIVOS_SEM_EFEITO_NO_SALDO.join()) out.push(`lista branca: ${w.MOTIVOS_SEM_EFEITO_NO_SALDO} × ${a.MOTIVOS_SEM_EFEITO_NO_SALDO}`);
  for (const [nome, d] of Object.entries(TABELA)) {
    const doWeb = w.segmentosDeContas(d).map((s) => ({ valor: reais(s.valor), cor: s.cor }));
    const doApp = a.segmentosDeContas({ ...d, total: reais(d.total), carteira: { ...d.carteira, saldo: reais(d.carteira.saldo) }, contas: d.contas.map((x) => ({ ...x, saldo: reais(x.saldo) })) }, CORES)
      .map((s) => ({ valor: s.valor, cor: s.cor }));
    if (doWeb.length !== doApp.length || doWeb.some((s, i) => s.valor !== doApp[i].valor)) { out.push(`${nome}: ${JSON.stringify(doWeb)} × ${JSON.stringify(doApp)}`); continue; }
    const quem = quemFicaNaBarra(d);
    doWeb.forEach((s, i) => {
      if (/\bnubank\b/.test(quem[i].nome.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase())) {
        if (s.cor !== NUBANK || doApp[i].cor === NUBANK) out.push(`${nome}[${i}]: Nubank tem de ser roxo no painel (${s.cor}) e cair na tinta no app (${doApp[i].cor})`);
      } else if (s.cor !== doApp[i].cor) out.push(`${nome}[${i}]: cor ${s.cor} × ${doApp[i].cor}`);
    });
  }
  return out;
};

test("barra de composição: segmentosDeContas e a lista branca do painel == as do app, em toda a tabela (menos o Nubank roxo, declarado)", () => {
  assert.deepEqual(diferencas(WEB(), APP()), []);
  // positivo: a tabela desenha barra onde deve (a regra não recusa tudo) e esconde onde deve
  const w = painel(WEB());
  assert.equal(w.segmentosDeContas(TABELA["três segmentos que fecham"]).length, 3);
  assert.equal(w.segmentosDeContas(TABELA["a soma não fecha com o total"]).length, 0);
  assert.equal(w.segmentosDeContas(TABELA["banco desatualizado esconde a barra"]).length, 0);
});

test("negativo: lista branca, regra da soma e cor trocadas só de um lado aparecem como diferença", () => {
  assert.ok(diferencas(WEB(), APP().replace('"outra_moeda", ', "")).length > 0, "motivo a menos no app");
  assert.ok(diferencas(WEB().replace('"especie_incompleta"]', '"especie_incompleta", "saldo_ausente"]'), APP()).length > 0, "motivo a mais no painel");
  assert.ok(diferencas(WEB(), APP().replace("!== d.total", "=== d.total")).length > 0, "regra da soma invertida no app");
  assert.ok(diferencas(WEB().replace("!== ate(total)) return []", "!== ate(total) + BigInt(1)) return []"), APP()).length > 0, "regra da soma folgada no painel");
  assert.ok(diferencas(WEB().replace("itens.length < 2", "itens.length < 1"), APP()).length > 0, "um segmento já seria composição no painel");
  assert.ok(diferencas(WEB().replace('itau: "#EC7000"', 'itau: "#EC7001"'), APP()).length > 0, "cor do Itaú só no painel");
  assert.ok(diferencas(WEB().replace("? CORES_BANCO.nubank :", "? TINTA[0] :"), APP()).some((d) => d.includes("Nubank")), "sem o roxo do Nubank no painel o teste da exceção vê");
});

const pedidos = (texto) => [...texto.matchAll(/pedido="([^"]+)"/g)].map((m) => m[1]).sort();
const doPainel = (texto) => ["PEDIDO_RESERVA", "PEDIDO_META"].map((n) => { const m = new RegExp(`export const ${n} = "([^"]+)";`).exec(texto); assert.ok(m, `${n}: o formato mudou`); return m[1]; }).sort();

test("os dois pedidos ao chat do card de metas são os do app, palavra por palavra", () => {
  assert.deepEqual(doPainel(METAS_WEB()), pedidos(METAS_APP()));
  assert.equal(pedidos(METAS_APP()).length, 2, "o app mudou o número de pedidos: ajuste a paridade");
  assert.deepEqual(doPainel(METAS_WEB()), ["Quero criar uma meta", "Quero montar minha reserva de emergência"]);
});
test("negativo: uma palavra trocada num pedido, de qualquer lado, aparece como diferença", () => {
  assert.notDeepEqual(doPainel(METAS_WEB().replace("minha reserva", "a reserva")), pedidos(METAS_APP()));
  assert.notDeepEqual(doPainel(METAS_WEB()), pedidos(METAS_APP().replace("criar uma meta", "criar uma metas")));
});

const limite = (texto, re) => { const m = re.exec(texto); assert.ok(m, `limite: o formato mudou (${re})`); return String(m[1]); };
test("a expansão de lançamentos pede 10 por página, como a do app", () => {
  assert.equal(limite(LANC_WEB(), /limite: (\d+)/), limite(LANC_APP(), /limite: "(\d+)"/));
  assert.equal(limite(LANC_WEB(), /limite: (\d+)/), "10");
});
test("negativo: outro limite de um lado só é visto", () => {
  assert.notEqual(limite(LANC_WEB().replace("limite: 10", "limite: 50"), /limite: (\d+)/), limite(LANC_APP(), /limite: "(\d+)"/));
});

// PR 4 ---------------------------------------------------------------------------------------------------------------------------------------------
const DECIMAIS_WEB = () => ler("webapp", "src", "dashboard", "parts", "cardIa", "decimais.ts");
const RESUMO_MES_WEB = () => ler("webapp", "src", "dashboard", "parts", "CardResumoMes.tsx");
const PERIODO_WEB = () => ler("webapp", "src", "dashboard", "parts", "cardIa", "resumo.tsx");
const PERIODO_APP_TESTE = () => ler("app", "__tests__", "features", "painel_card_periodo.test.tsx");
const PERCENTUAIS = JSON.parse(ler("tests", "fixtures", "percentual_categorias.json")); // [{ valores, percentuais }]: o % de cada valor no total (a soma), HALF_UP afastando do zero

// `soma` e `pctDe` (decimais.ts) rodam com o `decimal` e o `menos` de verdade (o `menos` sai do CardResumoMes.tsx).
const decimais = (fonte, resumo = RESUMO_MES_WEB()) => {
  const menos = new Function("decimal", `const BIG = BigInt;\n${stripTypeScriptTypes(semImport(corta(resumo, "export function menos", "/** Entrou, Saiu e Sobrou do mês", "CardResumoMes.tsx")))}\nreturn menos;`)(decimal);
  return new Function("decimal", "menos", `${stripTypeScriptTypes(semImport(fonte))}\nreturn { soma, pctDe };`)(decimal, menos);
};
const divergenciasDePercentual = (fonte) => {
  const { soma, pctDe } = decimais(fonte), out = [];
  for (const { valores, percentuais } of PERCENTUAIS) {
    const total = soma(valores), doWeb = valores.map((v) => pctDe(v, total));
    if (doWeb.join() !== percentuais.join()) out.push(`${valores}: ${doWeb} × ${percentuais}`);
    if (Number(total) !== valores.reduce((a, v) => a + Number(v), 0)) out.push(`soma de ${valores}: ${total}`);
  }
  return out;
};
test("a % de cada categoria e a soma exata do painel batem com a tabela do servidor e do app (HALF_UP afastando do zero; total 0 dá 0)", () => {
  assert.deepEqual(divergenciasDePercentual(DECIMAIS_WEB()), []);
  assert.ok(PERCENTUAIS.length >= 5, "a tabela mudou de forma");
  assert.deepEqual(PERCENTUAIS.find((c) => c.valores.join() === "995.00,5.00").percentuais, [100, 1], "o caso que separa HALF_UP de truncar");
});
test("negativo: arredondar para baixo, esquecer o sinal na soma ou deixar o total 0 estourar são vistos pela tabela", () => {
  assert.ok(divergenciasDePercentual(DECIMAIS_WEB().replace("(BigInt(2) * an + ad)", "(BigInt(2) * an + ad - BigInt(1))")).length > 0, "HALF_DOWN");
  assert.ok(divergenciasDePercentual(DECIMAIS_WEB().replace("menos(a, neg(v))", "menos(a, v)")).length > 0, "a soma subtrai");
  assert.throws(() => divergenciasDePercentual(DECIMAIS_WEB().replace("if (ad === BigInt(0)) return 0;", "")), "total 0 sem a guarda divide por zero");
});

const TITULOS = () => { const m = /it\.each\((\[\[.*?\]\])\)\("tituloPeriodo/s.exec(PERIODO_APP_TESTE()); assert.ok(m, "o teste do app mudou de formato"); return JSON.parse(m[1]); };
const tituloPeriodoWeb = (fonte) => new Function("monthTitle", `${stripTypeScriptTypes(corta(fonte, "export function tituloPeriodo", "export const tituloDoPeriodo", "resumo.tsx").replace(/^export /, ""))}\nreturn tituloPeriodo;`)(monthTitle);
const titulosDiferentes = (fonte) => { const f = tituloPeriodoWeb(fonte); return TITULOS().filter(([a, b, esperado]) => f(a, b) !== esperado).map(([a, b, esperado]) => `${a}..${b}: ${f(a, b)} × ${esperado}`); };
test("o título do card `periodo` é o do app nos 4 casos da tabela dele (mês só, mesmo ano, anos diferentes, ano inteiro)", () => {
  assert.equal(TITULOS().length, 4);
  assert.deepEqual(titulosDiferentes(PERIODO_WEB()), []);
});
test("negativo: título de outro formato num lado só aparece como diferença", () => {
  assert.ok(titulosDiferentes(PERIODO_WEB().replace(".slice(0, 3)", ".slice(0, 4)")).length > 0, "abreviação maior");
  assert.ok(titulosDiferentes(PERIODO_WEB().replace("mes(fim).toLowerCase()} ${b}`", "mes(fim)} ${b}`")).length > 0, "mês final com maiúscula");
});

// Os tetos de "quantos aparecem antes do +N": 5 categorias (4 + "Outras") no categorias, 5 no periodo, 7 dias no calendario (o `VISIVEIS`/`MAIS`/`SeteDias` do app).
const num = (texto, re, rotulo) => { const m = re.exec(texto); assert.ok(m, `${rotulo}: o formato mudou (${re})`); return Number(m[1]); };
const tetos = (gastos, resumo, app) => ({
  categorias: [num(gastos, /const VISIVEIS = (\d+);/, "gastos.tsx"), num(app.gastos, /const VISIVEIS = (\d+),/, "cardGastos.tsx")],
  periodo: [num(resumo, /useMais\(itens, (\d+), \["categoria"/, "resumo.tsx"), num(app.periodo, /const MAIS = (\d+);/, "cardPeriodo.tsx")],
  dias: [num(gastos, /useMais\(dias, (\d+), \["dia"/, "gastos.tsx"), num(app.dias, /itens\.slice\(0, (\d+)\)/, "base.tsx")],
});
const GASTOS_WEB = () => ler("webapp", "src", "dashboard", "parts", "cardIa", "gastos.tsx");
const APP_TETOS = () => ({ gastos: ler("app", "src", "features", "painel", "cardGastos.tsx"), periodo: ler("app", "src", "features", "painel", "cardPeriodo.tsx"), dias: ler("app", "src", "features", "painel", "base.tsx") });
test("quantas categorias e dias aparecem antes do \"+N\" são os do app (5, 5 e 7)", () => {
  for (const [nome, [web, app]] of Object.entries(tetos(GASTOS_WEB(), PERIODO_WEB(), APP_TETOS()))) assert.equal(web, app, nome);
  assert.deepEqual(Object.values(tetos(GASTOS_WEB(), PERIODO_WEB(), APP_TETOS())).map(([w]) => w), [5, 5, 7]);
});
test("negativo: outro teto num lado só é visto", () => {
  const web = tetos(GASTOS_WEB().replace("const VISIVEIS = 5;", "const VISIVEIS = 6;"), PERIODO_WEB().replace("useMais(itens, 5,", "useMais(itens, 4,"), APP_TETOS());
  assert.notEqual(web.categorias[0], web.categorias[1]);
  assert.notEqual(web.periodo[0], web.periodo[1]);
  const app = APP_TETOS(); app.dias = app.dias.replace("itens.slice(0, 7)", "itens.slice(0, 8)");
  assert.notEqual(tetos(GASTOS_WEB(), PERIODO_WEB(), app).dias[0], tetos(GASTOS_WEB(), PERIODO_WEB(), app).dias[1]);
});

// PR 5: os dois textos do card `simulador` que o app também diz, palavra por palavra. O `GENERICO` NÃO entra: é o do widget do painel ("Não deu para calcular agora."), diferente do do app, de propósito.
const texto = (fonte, nome) => { const m = new RegExp(`const ${nome} = "([^"]+)";`).exec(fonte); assert.ok(m, `${nome}: o formato mudou`); return m[1]; };
const SIM_APP = () => ler("app", "src", "features", "painel", "simuladorHabitos.tsx");
const COMPRA_APP = () => ler("app", "src", "features", "painel", "simuladorCompra.ts");
const SIM_WIDGET = () => ler("webapp", "src", "dashboard", "widgets", "SimuladorHabitos.tsx");
const SIM_CARD = () => ler("webapp", "src", "dashboard", "parts", "cardIa", "simulador.tsx");
test("o aviso de \"gastos mudaram\" (422) e o de plano do card `simulador` são os do app, palavra por palavra", () => {
  assert.equal(texto(SIM_WIDGET(), "MUDOU"), texto(SIM_APP(), "MUDOU"));
  assert.equal(texto(SIM_CARD(), "SEM_PLANO"), texto(COMPRA_APP(), "SEM_PLANO"));
  assert.equal(texto(SIM_CARD(), "SEM_PLANO"), "Simulador indisponível no seu plano.");
});
test("negativo: uma palavra trocada num texto do simulador, de qualquer lado, aparece como diferença", () => {
  assert.notEqual(texto(SIM_WIDGET().replace("mudaram. Ajuste", "mudaram! Ajuste"), "MUDOU"), texto(SIM_APP(), "MUDOU"));
  assert.notEqual(texto(SIM_WIDGET(), "MUDOU"), texto(SIM_APP().replace("Ajuste de novo", "Ajuste outra vez"), "MUDOU"));
  assert.notEqual(texto(SIM_CARD().replace("no seu plano", "no seu pacote"), "SEM_PLANO"), texto(COMPRA_APP(), "SEM_PLANO"));
  assert.notEqual(texto(SIM_CARD(), "SEM_PLANO"), texto(COMPRA_APP().replace("Simulador indisponível", "Simulador fora"), "SEM_PLANO"));
});

// O texto de "sem histórico" do card `simulador`: o do app (`<Vazio texto="…" />`), o do painel (`<p className="ci-vazio">…</p>`).
const vazioApp = (f) => { const m = /<Vazio texto="([^"]+)" \/>/.exec(f); assert.ok(m, "o formato do Vazio do app mudou"); return m[1]; };
const vazioWeb = (f) => { const m = /<p className="ci-vazio">([^<]+)<\/p>/.exec(f); assert.ok(m, "o formato do ci-vazio do card mudou"); return m[1]; };
test("o texto de \"sem histórico\" do card `simulador` é o do app, palavra por palavra", () => {
  assert.equal(vazioWeb(SIM_CARD()), vazioApp(SIM_APP()));
  assert.equal(vazioWeb(SIM_CARD()), "Ainda sem histórico para estimar.");
});
test("negativo: uma palavra trocada no texto de \"sem histórico\", de qualquer lado, aparece como diferença", () => {
  assert.notEqual(vazioWeb(SIM_CARD().replace("para estimar", "para calcular")), vazioApp(SIM_APP()));
  assert.notEqual(vazioWeb(SIM_CARD()), vazioApp(SIM_APP().replace("Ainda sem histórico", "Ainda não há histórico")));
});

// PR 6: os cards legados (caixinhas, fatura, parcelas, renda, piggy, rendimento) ------------------------------------------------------------------------
const CARD_IA = () => ler("webapp", "src", "dashboard", "parts", "CardIa.tsx");
const CATALOGO_APP = () => ler("app", "src", "features", "painel", "catalogo.ts");
const FALAS_APP = () => ler("app", "src", "features", "painel", "conversaFalas.tsx");
const LEGADO_WEB = () => ler("webapp", "src", "dashboard", "parts", "cardIa", "legado.ts");
const REND_WEB = () => ler("webapp", "src", "dashboard", "parts", "cardIa", "rendimento.tsx");
const REND_APP = () => ler("app", "src", "features", "painel", "cardRendimentos.tsx");
const WEB_CARDS = ["caixinhas", "cartao", "renda", "piggy", "rendimento"].map((f) => ler("webapp", "src", "dashboard", "parts", "cardIa", `${f}.tsx`)).join("\n");
const APP_CARDS = () => ["complementos", "patrimonio", "cardRendimentos"].map((f) => ler("app", "src", "features", "painel", `${f}.tsx`)).join("\n");

// Os títulos: cada entrada do `CARDS` com título fixo (`titulo: () => "X"`) é igual ao `TITULOS` do app; `caixinhas` é só do chat, no `tituloCard`. `compromissos` e `periodo` são dinâmicos e ficam de fora.
const titulosWeb = (f) => Object.fromEntries([...f.matchAll(/^ {2}(\w+): \{ titulo: \(\) => "([^"]+)"/gm)].map((m) => [m[1], m[2]]));
const titulosApp = (f) => { const m = /export const TITULOS = \{(.*?)\};/.exec(f); assert.ok(m, "catalogo.ts: o TITULOS mudou de formato"); return Object.fromEntries([...m[1].matchAll(/(\w+): "([^"]+)"/g)].map((x) => [x[1], x[2]])); };
const tituloDeCaixinhas = (f) => { const m = /card === "caixinhas"\) return "([^"]+)"/.exec(f); assert.ok(m, "conversaFalas.tsx: o título das caixinhas mudou de formato"); return m[1]; };
const titulosForaDoApp = (web, catalogo, falas) => { const a = { ...titulosApp(catalogo), caixinhas: tituloDeCaixinhas(falas) }; return Object.entries(web).filter(([id, t]) => a[id] !== t).map(([id, t]) => `${id}: ${t} × ${a[id]}`); };
test("o título de cada card do painel com título fixo é o do app (os 18 menos `compromissos` e `periodo`, que são dinâmicos)", () => {
  assert.deepEqual(titulosForaDoApp(titulosWeb(CARD_IA()), CATALOGO_APP(), FALAS_APP()), []);
  assert.equal(Object.keys(titulosWeb(CARD_IA())).length, 16, "o teste tem de ver as 16 entradas de título fixo");
});
test("negativo: um título trocado de um lado só aparece como diferença", () => {
  assert.deepEqual(titulosForaDoApp(titulosWeb(CARD_IA().replace('"Parcelas futuras"', '"Parcelas"')), CATALOGO_APP(), FALAS_APP()).length, 1);
  assert.deepEqual(titulosForaDoApp(titulosWeb(CARD_IA()), CATALOGO_APP().replace('renda: "Renda mês a mês"', 'renda: "Renda"'), FALAS_APP()).length, 1);
  assert.deepEqual(titulosForaDoApp(titulosWeb(CARD_IA()), CATALOGO_APP(), FALAS_APP().replace('return "Caixinhas"', 'return "Metas do banco"')).length, 1);
});

// As 3 funções copiadas (`legado.ts` × `catalogo.ts`) e o `pctTexto`, EXECUTADAS nas duas versões contra uma tabela.
const funcao = (fonte, nome) => { const m = new RegExp(`export function ${nome}[\\s\\S]*?\\n\\}`).exec(fonte); assert.ok(m, `${nome}: o formato mudou`); return new Function(`${stripTypeScriptTypes(semImport(m[0]))}\nreturn ${nome};`)(); };
const pctFn = (fonte) => { const m = /const pctTexto = [^\n]*/.exec(fonte); assert.ok(m, "pctTexto: o formato mudou"); return new Function(`${stripTypeScriptTypes(semImport(m[0]))}\nreturn pctTexto;`)(); };
const TABELA_LEGADO = {
  nomeLegivel: ["ultraviolet-black", "nubank", "Nubank Gold", "a_b-c", "Cartão-X", "", "x-"],
  indicadorMeta: ["no_target", "achieved", "on_track", "ahead", "behind", "tight", "active", "no_deadline", "inventado", ""],
  dataPtBr: ["2026-12-22", null, "lixo", "2026-12-22T00:00", "", "2026-1-2"],
  pctTexto: ["1.2345", "-0.50", "0", "0.00", null, " ", "abc", "+3.1", "10"],
};
const diferencasLegado = (legado, catalogo, rendWeb, rendApp) => {
  const out = [];
  for (const nome of ["nomeLegivel", "indicadorMeta", "dataPtBr"]) { const [w, a] = [funcao(legado, nome), funcao(catalogo, nome)]; for (const x of TABELA_LEGADO[nome]) if (w(x) !== a(x)) out.push(`${nome}(${JSON.stringify(x)}): ${w(x)} × ${a(x)}`); }
  const [w, a] = [pctFn(rendWeb), pctFn(rendApp)];
  for (const x of TABELA_LEGADO.pctTexto) if (w(x) !== a(x)) out.push(`pctTexto(${JSON.stringify(x)}): ${w(x)} × ${a(x)}`);
  return out;
};
test("nomeLegivel, indicadorMeta, dataPtBr e pctTexto do painel dão o mesmo que os do app, em toda a tabela", () => {
  assert.deepEqual(diferencasLegado(LEGADO_WEB(), CATALOGO_APP(), REND_WEB(), REND_APP()), []);
  assert.equal(funcao(LEGADO_WEB(), "nomeLegivel")("ultraviolet-black"), "Ultraviolet Black", "positivo: a função faz o que deve, não recusa tudo");
  assert.equal(pctFn(REND_WEB())("-0.50"), "−0,50%");
});
test("negativo: uma regra ou palavra trocada num lado só (nome, rótulo, data, pct) aparece como diferença", () => {
  assert.ok(diferencasLegado(LEGADO_WEB().replace("[-_]", "[-]"), CATALOGO_APP(), REND_WEB(), REND_APP()).length > 0, "slug só com hífen");
  assert.ok(diferencasLegado(LEGADO_WEB().replace('tight: "Prazo apertado"', 'tight: "Prazo curto"'), CATALOGO_APP(), REND_WEB(), REND_APP()).length > 0, "palavra do rótulo");
  assert.ok(diferencasLegado(LEGADO_WEB(), CATALOGO_APP().replace("\\d{4}-\\d{2}-\\d{2}", "\\d{4}-\\d{1,2}-\\d{1,2}"), REND_WEB(), REND_APP()).length > 0, "data mais frouxa no app");
  assert.ok(diferencasLegado(LEGADO_WEB(), CATALOGO_APP(), REND_WEB().replace('.replace(".", ",")', ""), REND_APP()).length > 0, "pct sem a vírgula no painel");
  assert.ok(diferencasLegado(LEGADO_WEB(), CATALOGO_APP(), REND_WEB(), REND_APP().replace('v === 0 ? "" :', "")).length > 0, "zero com sinal no app");
});

// Os textos copiados: cada literal está nos DOIS lados (os vazios, a fatura, os ritmos, as origens da caixinha, o rendimento).
const LITERAIS = [
  "Nenhum cartão cadastrado.", "Não há parcelamentos registrados.", "Ainda não há histórico de renda disponível.", "O Piggy ainda não encontrou um destaque para você.", "Não foi possível ler seus rendimentos agora.",
  "Vencimento dia ", " · fechamento dia ", "Crédito utilizado", "Já pago nesta fatura", "Ritmo mensal atual", "Ritmo mensal necessário", "Caixinha do banco com sincronização indisponível", "Caixinha manual", "Sem prazo definido",
  "O rendimento do mês aparece quando houver a foto do fechamento do mês anterior.", " ativos sem rendimento informado", "Open Finance sincronizado em", "Estimado pela variação do saldo desde",
  ", sem contar aportes. Ações, FIIs e fundos: só a variação de preço; dividendos não entram.", "Saldo menos o valor aplicado, como o banco informa. Ações, FIIs e fundos não informam o custo e ficam sem rendimento.",
  "neste mês", "desde a aplicação", "Não informado", "Parcela ", '"paga"', '"próxima"', '"pendente"',
  '"Limite"', '"Saídas"', // os rótulos das linhas da fatura e da renda (`Sub nome=` no painel, `Linha titulo=` no app)
  ["Objetivo · ", 'titulo="Objetivo"'], // o painel junta o rótulo e o prazo numa linha ("Objetivo · 22/12/2026"); o app põe o prazo no detalhe da mesma linha: a palavra é a mesma
  "Nenhuma caixinha ainda.", // o vazio do card `caixinhas` (dono, 2026-10-23)
  ["{pagas} de {n} pagas", "${pagas} de ${n} pagas"], // parcelas: uma linha por compra nos dois lados (dono, 2026-10-23)
];
// Um item é o texto que os dois lados têm, ou [o do painel, o do app] quando o desenho muda mas a palavra não.
const semLiteral = (web, app) => LITERAIS.filter((l) => { const [w, a] = Array.isArray(l) ? l : [l, l]; return !web.includes(w) || !app.includes(a); }).map((l) => (Array.isArray(l) ? l[0] : l));
test("os textos que o painel copia do app estão nos dois lados, palavra por palavra", () => {
  assert.deepEqual(semLiteral(WEB_CARDS + LEGADO_WEB(), APP_CARDS()), []);
});
test("negativo: uma palavra trocada num texto copiado, de qualquer lado, é vista", () => {
  assert.deepEqual(semLiteral(WEB_CARDS.replace("Crédito utilizado", "Crédito usado") + LEGADO_WEB(), APP_CARDS()), ["Crédito utilizado"]);
  assert.deepEqual(semLiteral(WEB_CARDS + LEGADO_WEB(), APP_CARDS().replace("dividendos não entram", "dividendos ficam de fora")), [", sem contar aportes. Ações, FIIs e fundos: só a variação de preço; dividendos não entram."]);
  assert.deepEqual(semLiteral(WEB_CARDS.replace('nome="Limite"', 'nome="Teto"') + LEGADO_WEB(), APP_CARDS()), ['"Limite"'], "rótulo da fatura");
  assert.deepEqual(semLiteral(WEB_CARDS.replace('nome="Saídas"', 'nome="Gastos"') + LEGADO_WEB(), APP_CARDS()), ['"Saídas"'], "rótulo da renda");
  assert.deepEqual(semLiteral(WEB_CARDS.replace("`Objetivo · ", "`Meta · ") + LEGADO_WEB(), APP_CARDS()), ["Objetivo · "], "rótulo da meta (o \"Objetivo alcançado\" do legado.ts não o cobre)");
  assert.deepEqual(semLiteral(WEB_CARDS + LEGADO_WEB(), APP_CARDS().replace('titulo="Saídas"', 'titulo="Gastos"')), ['"Saídas"'], "o rótulo trocado só no app");
});
// Parcelas: quantas compras aparecem antes do "+N" (cartao.tsx `useMais(abertas, 5, …)` × complementos.tsx `COMPRAS_VISIVEIS`).
const tetoCompras = (web, app) => [num(web, /useMais\(abertas, (\d+), \["compra"/, "cartao.tsx"), num(app, /const COMPRAS_VISIVEIS = (\d+);/, "complementos.tsx")];
const CARTAO_WEB = () => ler("webapp", "src", "dashboard", "parts", "cardIa", "cartao.tsx"), COMPLEMENTOS_APP = () => ler("app", "src", "features", "painel", "complementos.tsx");
test("parcelas: quantas compras aparecem antes do \"+N\" é o mesmo nos dois lados (5); negativo: outro teto num lado só é visto", () => {
  assert.deepEqual(tetoCompras(CARTAO_WEB(), COMPLEMENTOS_APP()), [5, 5]);
  const [w, a] = tetoCompras(CARTAO_WEB(), COMPLEMENTOS_APP().replace("COMPRAS_VISIVEIS = 5;", "COMPRAS_VISIVEIS = 6;")); assert.notEqual(w, a);
});
// Parcelas: o "n de N pagas" (`progresso`, executado dos dois lados). N é o total do banco e as parcelas antes da 1ª vista contam como pagas (dono, 2026-10-24).
const progressoDe = (fonte, rotulo, de = "export function progresso") => new Function(`${stripTypeScriptTypes(semImport(corta(fonte, de, "\n}\n", rotulo) + "\n}"))}\nreturn progresso;`)();
// A mesma tabela do servidor (tests/test_parcelas_progresso.py): [{ grupo, esperado, nota }]. O dashboard.js legado é a 3ª cópia.
const PROGRESSO = JSON.parse(ler("tests", "fixtures", "parcelas_progresso.json"));
const DASHBOARD_LEGADO = () => ler("frontend", "dashboard.js");
test("parcelas: `progresso` dá o mesmo \"n de N pagas\" nas 3 cópias (web, app, dashboard.js legado); negativo: a regra trocada numa cópia só é vista", () => {
  assert.ok(PROGRESSO.length >= 9);
  const copias = [
    progressoDe(CARTAO_WEB(), "cartao.tsx"), progressoDe(COMPLEMENTOS_APP(), "complementos.tsx"),
    progressoDe(DASHBOARD_LEGADO(), "dashboard.js", "function progresso"),
  ];
  for (const { grupo, esperado, nota } of PROGRESSO) for (const f of copias) assert.deepEqual(f(grupo), esperado, nota);
  const torta = (fonte, de, para, rotulo, ...resto) => progressoDe(fonte.replace(de, para), rotulo, ...resto);
  for (const t of [
    torta(COMPLEMENTOS_APP(), "installment_no)) - 1)", "installment_no)) - 0)", "complementos.tsx"),
    torta(DASHBOARD_LEGADO(), "Math.min(...nos) - 1)", "Math.min(...nos) - 0)", "dashboard.js", "function progresso"),
  ]) assert.ok(PROGRESSO.some(({ grupo, esperado }) => JSON.stringify(t(grupo)) !== JSON.stringify(esperado)));
});
test("a caixinha vazia não volta a ser \"Nenhuma meta ainda.\" no card do app (o texto da aba Metas mora em metasChat.tsx, fora daqui)", () => {
  assert.ok(!APP_CARDS().includes("Nenhuma meta ainda.") && !WEB_CARDS.includes("Nenhuma meta ainda."));
});
