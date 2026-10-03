// Protótipo dashboard-v2: o Safari 14 (alvo do build, webapp/vite.config.js) não tem
// @container. Cada `@container (max-width: N)` vem logo depois de um `@media (max-width:
// N + 68)` com as MESMAS declarações: no celular o bloco mede a tela − 68px (gutter 16 × 2 +
// padding do .w 18 × 2), e o @media reproduz só essa faixa. Este teste mantém as duas cópias
// iguais (CLAUDE.md §0.7). Os @container por altura não têm gêmeo: só enfeitam.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";

const DIR = new URL("../../webapp/src/dashboard/styles/", import.meta.url);
const PHONE = 68;
const SEM_GEMEO = ["(min-height: 600px)", "(max-height: 230px)"];

// Blocos @media/@container de um arquivo, na ordem, com o texto entre um e outro.
function atBlocks(css) {
  const out = [];
  for (const m of css.matchAll(/@(media|container) ([^{]+)\{/g)) {
    let depth = 1, i = m.index + m[0].length;
    for (; depth; i++) depth += css[i] === "{" ? 1 : css[i] === "}" ? -1 : 0;
    out.push({ kind: m[1], query: m[2].trim(), body: css.slice(m.index + m[0].length, i - 1).replace(/\s+/g, " ").trim(), start: m.index, end: i });
  }
  return out;
}

test("cada @container por largura tem o @media gêmeo logo antes", () => {
  let pares = 0;
  for (const file of readdirSync(DIR).filter((f) => f.endsWith(".css"))) {
    const css = readFileSync(new URL(file, DIR), "utf8");
    const blocks = atBlocks(css);
    blocks.forEach((b, i) => {
      if (b.kind !== "container") return;
      const w = b.query.match(/^\(max-width: (\d+)px\)$/);
      if (!w) return assert.ok(SEM_GEMEO.includes(b.query), `${file}: @container ${b.query} sem gêmeo e fora da lista`);
      const prev = blocks[i - 1];
      const between = prev ? css.slice(prev.end, b.start).replace(/\/\*[\s\S]*?\*\//g, "").trim() : "x";
      assert.ok(prev?.kind === "media" && between === "", `${file}: @container ${b.query} sem @media logo antes`);
      assert.equal(prev.query, `(max-width: ${Number(w[1]) + PHONE}px)`, `${file}: limiar do gêmeo de ${b.query}`);
      assert.equal(prev.body, b.body, `${file}: declarações do gêmeo de ${b.query}`);
      pares++;
    });
  }
  assert.equal(pares, 9); // remeça com grep -c '@container (max-width' se mudar
});
