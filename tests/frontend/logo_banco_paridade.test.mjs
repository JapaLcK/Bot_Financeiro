// A regra do host do `logo_url` mora em DOIS lugares (o app é outro projeto TS, sem import entre eles): o painel
// (webapp/src/dashboard/conta/api.ts) copia a do app (app/src/features/openFinance/conexoes/juntar.ts). CLAUDE.md §0.7:
// um teste compara as duas. Aqui: o literal da regex dos dois arquivos tem de ser o mesmo, e a tabela de casos vale pro literal.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";

const literal = (arquivo, nome = "LOGO_PLUGGY") => {
  const m = readFileSync(new URL(`../../${arquivo}`, import.meta.url), "utf8").match(new RegExp(`^(?:export )?const ${nome} = (\\/.+\\/);$`, "m"));
  assert.ok(m, `${arquivo}: não achei a regex ${nome}`);
  const [, corpo, flags] = /^\/(.+)\/([a-z]*)$/.exec(m[1]);
  return new RegExp(corpo, flags);
};
const APP = "app/src/features/openFinance/conexoes/juntar.ts";
const PAINEL = "webapp/src/dashboard/conta/api.ts";

const BOAS = ["https://cdn.pluggy.ai/assets/connector-icons/612.svg", "https://cdn.pluggy.ai/a_b-c.d~e/f.png"];
const RUINS = ["http://cdn.pluggy.ai/a.svg", "https://evil.example/a.svg", "https://cdn.pluggy.ai/a.svg?x=1", "https://cdn.pluggy.ai/a.svg#x", "https://u@cdn.pluggy.ai/a.svg",
  "https://cdn.pluggy.ai:444/a.svg", "https://cdn.pluggy.ai.evil.example/a.svg", "https://cdn.pluggy.ai/", "https://cdn.pluggy.ai/a b.svg", "https://cdn.pluggy.ai/a.svg\n",
  "https://cdn.pluggy.ai/café.svg", "https://CDN.pluggy.ai/a.svg", "javascript:alert(1)", ""];

test("logo do banco: a regra do painel é a do app (mesmo literal) e a tabela de casos vale", () => {
  const app = literal(APP), painel = literal(PAINEL);
  assert.equal(painel.source, app.source);
  assert.equal(painel.flags, app.flags);
  for (const u of BOAS) assert.ok(painel.test(u) && app.test(u), u); // positivo: a regra não recusa tudo
  for (const u of RUINS) assert.ok(!painel.test(u) && !app.test(u), JSON.stringify(u));
});

// A função inteira de cada lado, executada: as duas linhas (`LOGO_PLUGGY` e `logoSeguro`) saem do .ts, perdem os tipos e rodam.
// O app devolve `undefined` e o painel `null` para "sem logo" (a diferença é de propósito): o que tem de bater é "sem logo" e, quando há, a URL.
const funcao = (arquivo) => {
  const fonte = readFileSync(new URL(`../../${arquivo}`, import.meta.url), "utf8");
  const linhas = [/^const LOGO_PLUGGY = .+;$/m, /^export const logoSeguro = .+;$/m].map((r) => { const m = fonte.match(r); assert.ok(m, `${arquivo}: ${r}`); return m[0].replace("export ", ""); });
  return new Function(`${stripTypeScriptTypes(linhas.join("\n"))}\nreturn logoSeguro;`)();
};

test("logo do banco: a função logoSeguro do painel e a do app dão o mesmo resultado em toda a tabela (o painel ainda recusa o que não é texto)", () => {
  const app = funcao(APP), painel = funcao(PAINEL);
  for (const u of [...BOAS, ...RUINS, null, undefined]) assert.equal(app(u) ?? null, painel(u) ?? null, JSON.stringify(u));
  for (const u of BOAS) assert.equal(painel(u), u);
  // Só o painel confere o tipo (o app tipa `string | null | undefined` e o zod já filtrou): número, objeto e lista não são logo.
  for (const u of [123, {}, ["https://cdn.pluggy.ai/a.svg"], true]) assert.equal(painel(u), null, JSON.stringify(u));
});

// Logo PRÓPRIO (PR do servidor + painel + app): mesmo literal nos dois lados e a mesma precedência (próprio > Pluggy > nada).
const PROPRIAS = ["/brand/bancos/60746948.png", "/brand/bancos/00000000.png"];
const PROPRIAS_RUINS = ["//evil.example/x.png", "/brand/bancos/../x.png", "https://x.example/brand/bancos/60746948.png", "/brand/bancos/60746948.png?x=1", "/brand/bancos/6074694.png",
  "/brand/bancos/607469481.png", "/brand/bancos/60746948.svg", "/brand/bancos/60746948.png\n", "brand/bancos/60746948.png", "/brand/bancos/6074694a.png", "/x/brand/bancos/60746948.png", ""];

test("logo próprio: a regra do painel é a do app (mesmo literal) e a tabela de casos vale", () => {
  const app = literal(APP, "LOGO_PROPRIO"), painel = literal(PAINEL, "LOGO_PROPRIO");
  assert.equal(painel.source, app.source);
  assert.equal(painel.flags, app.flags);
  for (const u of PROPRIAS) assert.ok(painel.test(u) && app.test(u), u);
  for (const u of PROPRIAS_RUINS) assert.ok(!painel.test(u) && !app.test(u), JSON.stringify(u));
});

// logoDoBanco inteira, executada. O app a devolve ABSOLUTA (`baseUrl() + caminho`) e o painel RELATIVA (mesma origem): com `baseUrl` = "" os dois têm de dar o mesmo resultado.
const precedencia = (arquivo) => {
  const fonte = readFileSync(new URL(`../../${arquivo}`, import.meta.url), "utf8");
  const trechos = [/^const LOGO_PLUGGY = .+;$/m, /^export const logoSeguro = .+;$/m, /^const LOGO_PROPRIO = .+;$/m, /^export const logoDoBanco = [\s\S]*?;$/m]
    .map((r) => { const m = fonte.match(r); assert.ok(m, `${arquivo}: ${r}`); return m[0].replace("export ", ""); });
  return new Function(`const baseUrl = () => "";\n${stripTypeScriptTypes(trechos.join("\n"))}\nreturn logoDoBanco;`)();
};

test("logo próprio: logoDoBanco do painel e a do app dão o mesmo resultado, com a precedência próprio > Pluggy > nada", () => {
  const app = precedencia(APP), painel = precedencia(PAINEL);
  const PLUGGY = BOAS[0];
  for (const proprio of [...PROPRIAS, ...PROPRIAS_RUINS, null, undefined]) {
    for (const url of [PLUGGY, RUINS[1], null, undefined]) {
      assert.equal(app({ logo_proprio: proprio, logo_url: url }) ?? null, painel({ logo_proprio: proprio, logo_url: url }) ?? null, JSON.stringify([proprio, url]));
    }
  }
  assert.equal(app({ logo_proprio: PROPRIAS[0], logo_url: PLUGGY }), PROPRIAS[0]); // o próprio ganha
  assert.equal(app({ logo_proprio: null, logo_url: PLUGGY }), PLUGGY);             // positivo: sem próprio, segue o da Pluggy
  assert.equal(app({ logo_proprio: PROPRIAS_RUINS[0], logo_url: PLUGGY }), PLUGGY); // próprio fora da regra cai no da Pluggy
  assert.equal(app({ logo_proprio: PROPRIAS_RUINS[0], logo_url: null }) ?? null, null);
});
