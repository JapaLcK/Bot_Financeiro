// §0.7: o SDK da Pluggy (versão + SRI) mora em dois HTML escritos à mão, settings.html e painel.html. Mudar a versão num só deixaria o integrity do outro
// recusar o script (o seletor de bancos sem widget). Este teste compara a URL e o integrity dos dois.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const sdk = (arq) => {
  const html = readFileSync(fileURLToPath(new URL(`../../frontend/${arq}`, import.meta.url)), "utf8");
  const tags = html.match(/<script\b[^>]*cdn\.pluggy\.ai\/pluggy-connect\/[^>]*>/g) ?? [];
  assert.equal(tags.length, 1, `${arq}: esperava 1 <script> do SDK da Pluggy`);
  return { src: tags[0].match(/\ssrc="([^"]+)"/)?.[1], integrity: tags[0].match(/\sintegrity="([^"]+)"/)?.[1] };
};

test("settings.html e painel.html carregam o mesmo SDK da Pluggy (versão e integrity)", () => {
  const [s, p] = [sdk("settings.html"), sdk("painel.html")];
  assert.match(s.src, /\/pluggy-connect\/v\d+\.\d+\.\d+\/pluggy-connect\.js$/);
  assert.match(s.integrity, /^sha384-/);
  assert.deepEqual(p, s);
});
