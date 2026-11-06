// Em disco que não distingue maiúsculas (padrão do macOS), dois caminhos que só
// diferem nelas viram um, e um import sem extensão pode cair no arquivo errado.
//
// `familia/Casa.tsx` e `familia/casa.ts` conviviam no disco; o que colidia era o
// import. Desde o #1401, `./familia/Casa` resolvia para `casa.ts` e o build morria com
// `"PaginaCasa" is not exported`, enquanto o CI em Linux seguia verde. Por isso a
// régua é a lista do `git`, que o Linux também enxerga, e não o comportamento do disco.
//
// São dois espaços de nome, separados de propósito; cada um acusa mais de uma grafia:
// - caminho, arquivos e pastas da árvore inteira: `Foo.css` + `foo.css`, o clone avisa
//   e o build segue com rc=0 e CSS faltando; `a/Foo/x.ts` + `a/foo/y.ts`, as pastas se
//   juntam e o `tsc` reprova; `a/Foo` + `a/foo/x.ts`, o arquivo some no clone;
// - módulo, o que um import sem extensão alcança, só em `webapp/` (Vite) e `app/`
//   (`tsc`): `Casa.tsx` disputa `./casa` com `casa.ts`, `casa.d.ts` e `casa/index.ts`.
//   Fora deles não há esse resolvedor: `frontend/Foo.json` + `foo.js` não quebra.
//
// Separados porque arquivo e pasta sem `index` não disputam import nenhum: é a forma
// de `parts/CardIa.tsx` + `parts/cardIa/`, que fica. Mesma grafia com extensões
// diferentes (`casa.ts` + `casa.tsx`) também não colide: quem decide é a ordem das
// extensões, igual em qualquer disco, e o CI já vê o resultado.
//
// RESOLVIDAS: a lista é o `DEFAULT_EXTENSIONS` do Vite, que ele não exporta (conferida
// no 8.3.0), e nenhum `webapp/vite*.config.*` troca o `resolve.extensions`; o `.d` vem
// do `tsc`. As extensões do Metro não foram conferidas.
//
// Não cobre, de propósito: pasta resolvida por `package.json` com `main`, e nomes que
// só diferem entre NFC e NFD.
//
// Rodar:  node --test tests/frontend/colisao_maiuscula.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const RAIZ = fileURLToPath(new URL("../..", import.meta.url));
const RESOLVIDAS = /(\.d)?\.(mjs|js|mts|ts|jsx|tsx|json)$/i;
const COM_RESOLVEDOR = /^(webapp|app)\//;

function colisoes(arquivos) {
  const pastas = arquivos.flatMap((a) => a.split("/").slice(0, -1).map((_, i, partes) => partes.slice(0, i + 1).join("/")));
  const modulos = arquivos.filter((a) => COM_RESOLVEDOR.test(a) && RESOLVIDAS.test(a))
    .map((a) => a.replace(RESOLVIDAS, "")).flatMap((m) => [m, m.replace(/\/index$/i, "")]);
  return [[...arquivos, ...pastas], modulos].flatMap((nomes) =>
    [...Map.groupBy(new Set(nomes), (nome) => nome.toLowerCase()).values()].filter((grafias) => grafias.length > 1));
}

const CONTROLES = [
  ["módulo: mesma pasta, grafias diferentes", ["webapp/Casa.tsx", "webapp/casa.ts"], [["webapp/Casa", "webapp/casa"]]],
  ["módulo: também em app/", ["app/Casa.tsx", "app/casa.ts"], [["app/Casa", "app/casa"]]],
  ["módulo: arquivo contra pasta com index", ["webapp/Casa.tsx", "webapp/casa/index.ts"], [["webapp/Casa", "webapp/casa"]]],
  ["módulo: pasta com index contra arquivo", ["webapp/casa.tsx", "webapp/Casa/index.ts"], [["webapp/casa", "webapp/Casa"]]],
  ["módulo: arquivo contra pasta com Index", ["webapp/Casa/Index.ts", "webapp/casa.ts"], [["webapp/Casa", "webapp/casa"]]],
  ["módulo: dois index de grafias diferentes", ["webapp/Index.ts", "webapp/index.tsx"], [["webapp/Index", "webapp/index"]]],
  ["módulo: extensão em maiúsculas", ["webapp/Casa.TSX", "webapp/casa.ts"], [["webapp/Casa", "webapp/casa"]]],
  ["módulo: arquivo contra declaração", ["webapp/Casa.tsx", "webapp/casa.d.ts"], [["webapp/Casa", "webapp/casa"]]],
  ["caminho: extensão que o resolvedor não tenta", ["a/Foo.css", "a/foo.css"], [["a/Foo.css", "a/foo.css"]]],
  ["caminho: pastas com arquivos diferentes", ["a/Foo/x.ts", "a/foo/y.ts"], [["a/Foo", "a/foo"]]],
  ["caminho: prefixo acima da pasta do arquivo", ["A/b/x.ts", "a/c/y.ts"], [["A", "a"]]],
  ["caminho: arquivo contra pasta", ["a/Foo", "a/foo/x.ts"], [["a/Foo", "a/foo"]]],
  ["os dois espaços juntos", ["webapp/Casa/x.ts", "webapp/casa/x.ts"],
    [["webapp/Casa/x.ts", "webapp/casa/x.ts"], ["webapp/Casa", "webapp/casa"], ["webapp/Casa/x", "webapp/casa/x"]]],
  ["sem colisão: extensão que o resolvedor não tenta", ["webapp/Casa.tsx", "webapp/casa.css"], []],
  ["sem colisão: pastas diferentes", ["webapp/a/Casa.tsx", "webapp/b/casa.ts"], []],
  ["sem colisão: mesma grafia, extensões diferentes", ["webapp/casa.ts", "webapp/casa.tsx"], []],
  ["sem colisão: mesma grafia, com declaração", ["webapp/casa.ts", "webapp/casa.d.ts"], []],
  ["sem colisão: arquivo e pasta sem index (forma do CardIa)", ["webapp/parts/CardIa.tsx", "webapp/parts/cardIa/piggy.tsx"], []],
  ["sem colisão: fora do resolvedor sem extensão", ["frontend/Foo.json", "frontend/foo.js"], []],
];

for (const [nome, arquivos, esperado] of CONTROLES) {
  test(`controle: ${nome}`, () => assert.deepEqual(colisoes(arquivos), esperado));
}

test("nenhum caminho ou módulo do repositório colide sem distinguir maiúsculas", () => {
  const { status, stdout } = spawnSync("git", ["-C", RAIZ, "ls-files", "-z"], { encoding: "utf8" });
  assert.equal(status, 0, "git ls-files falhou ou estourou o buffer: a lista veio incompleta");
  const arquivos = stdout.split("\0").filter(Boolean);
  assert.ok(arquivos.length > 0, "git ls-files não listou nada: o teste não mediu nada");
  assert.deepEqual(colisoes(arquivos), []);
});
