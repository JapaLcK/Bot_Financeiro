// Bancos conectados: o desenho em UMA coluna de cards (resumo, "Meus bancos conectados", "Disponíveis para conectar" e segurança), igual à referência do dono.
// Antes eram duas colunas (bancos | disponíveis) a partir de 1101 px; agora o que muda com a largura é o INTERIOR dos cards:
//   · >= 1181 px (trilho do painel aberto): o resumo em 3 zonas lado a lado; cada banco numa linha só (ladrilho, nome, estado, última atualização, ações), com as colunas alinhadas entre os bancos;
//   · abaixo disso e no celular: o resumo empilha e cada banco vira blocos (nome/estado em cima; última atualização e ações embaixo); a <= 480 px os botões viram só ícone (44 px, com aria-label).
// Controles negativos (desligue, rebuild `npm --prefix webapp run build`, o grupo tem de ficar vermelho; restaure e rebuild), em styles/bancos.css: (a) trocar os dois `min-width: 1181px` por `min-width: 5000px`
// (a tela larga fica empilhada: 1280 e 1440 reprovam), (b) tirar a regra `.banco-bt { display: none; }` de `max-width: 480px` (os rótulos voltam no celular: 390 reprova), (c) tirar
// `grid-template-areas` da linha de `.banco-card` (as colunas deixam de alinhar entre os bancos no desktop e o bloco do celular desmonta), (d) tirar `.banco-resumo` de `grid-template-columns: ... auto` do bloco >= 1181 (o resumo não vira 3 zonas).
// Positivos: 1024 e 390 seguem em UMA coluna de cards sem rolagem horizontal, nomes de 70 letras sem espaço também (320, 375, 1280).
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { navegador } from "./_guia.mjs";
import { con } from "./_bancos.mjs";
import { telaRecon } from "./_bancos_reconectar.mjs";

const browser = navegador();
void browser;
const TELAS = process.env.PB_TELAS; // opcional: pasta para os screenshots

const medidas = (page) => page.evaluate(() => {
  const r = (e) => { const b = e?.getBoundingClientRect(); return b && { left: b.left, right: b.right, top: b.top, bottom: b.bottom, width: b.width, height: b.height }; };
  const cards = [...document.querySelectorAll(".bancos-pag > section")];
  const linhas = [...document.querySelectorAll(".banco-card")].map((c) => ({ card: r(c), ini: r(c.querySelector(".banco-ini")), nome: r(c.querySelector(".banco-nome")), estado: r(c.querySelector(".banco-estado")), quando: r(c.querySelector(".banco-quando")), acoes: r(c.querySelector(".banco-acoes")),
    rotulos: [...c.querySelectorAll(".banco-bt")].map((e) => getComputedStyle(e).display), botoes: [...c.querySelectorAll("button")].map((b) => [b.getAttribute("aria-label"), Math.round(b.getBoundingClientRect().width), Math.round(b.getBoundingClientRect().height)]) }));
  const zonas = [...document.querySelectorAll(".banco-resumo > *")].map(r);
  return { cards: cards.map(r), linhas, zonas, saiba: r(document.querySelector(".banco-saiba")), seguro: r(document.querySelector(".banco-seguro")), lateral: [document.documentElement.scrollWidth, innerWidth] };
});
const perto = (a, b, rotulo) => assert.ok(Math.abs(a - b) <= 1, `${rotulo}: ${a} × ${b}`);
async function foto(page, nome) { if (!TELAS) return; mkdirSync(TELAS, { recursive: true }); await page.screenshot({ path: join(TELAS, `${nome}.png`), fullPage: true }); }
const tres = () => [con(11, "Nubank"), con(12, "Inter"), con(13, "Itaú", "needs_user_action", "Atenção")];

for (const [width, height] of [[1440, 900], [1280, 800]]) {
  test(`${width}x${height}: uma coluna de 4 cards; resumo em 3 zonas lado a lado; cada banco numa linha só, com as colunas alinhadas entre os bancos; 'Saiba mais' na borda direita`, async () => {
    const { ctx, page } = await telaRecon({ conexoes: tres() }, { width, height });
    const m = await medidas(page);
    await foto(page, `layout-${width}`);
    await ctx.close();
    assert.equal(m.cards.length, 4);
    for (const c of m.cards) { perto(c.left, m.cards[0].left, "mesma borda esquerda"); perto(c.width, m.cards[0].width, "mesma largura"); }
    assert.ok(m.cards.every((c, i) => i === 0 || c.top >= m.cards[i - 1].bottom), "empilhados, sem sobreposição");
    // resumo: as 3 zonas na mesma linha, em ordem, com a de "Conectar banco" à direita
    assert.equal(m.zonas.length, 3);
    assert.ok(m.zonas[0].right <= m.zonas[1].left + 1 && m.zonas[1].right <= m.zonas[2].left + 1, `zonas lado a lado: ${JSON.stringify(m.zonas.map((z) => [z.left, z.right]))}`);
    assert.ok(m.zonas.every((z) => Math.abs(z.top - m.zonas[0].top) <= 1), "zonas na mesma linha");
    assert.ok(m.cards[0].right - m.zonas[2].right <= 32, "o 'Conectar banco' fica na borda direita do resumo");
    // cada banco: uma linha só (ordem da esquerda para a direita) e as colunas na mesma posição nos 3 bancos
    for (const l of m.linhas) {
      assert.ok(l.card.height < 100, `uma linha só: ${l.card.height}px`);
      assert.ok(l.ini.right <= l.nome.left + 1 && l.nome.right <= l.estado.left + 1 && l.estado.right <= l.quando.left + 1 && l.quando.right <= l.acoes.left + 1, "ladrilho, nome, estado, atualização e ações, nesta ordem");
    }
    for (const k of ["nome", "estado", "quando", "acoes"]) for (const l of m.linhas) perto(l[k].left, m.linhas[0][k].left, `coluna ${k}`);
    assert.ok(m.linhas.every((l) => l.rotulos.every((d) => d !== "none")), "rótulos Detalhes/Sincronizar visíveis");
    assert.ok(m.seguro.right - m.saiba.right <= 28, `'Saiba mais' na borda direita: ${m.seguro.right} × ${m.saiba.right}`);
    assert.ok(m.lateral[0] <= m.lateral[1], `rolagem horizontal ${m.lateral}`);
  });
}

for (const [width, height] of [[1024, 768], [390, 844]]) {
  test(`${width}x${height}: uma coluna de 4 cards; resumo e cada banco empilhados; ${width < 481 ? "botões só com ícone (44 px, aria-label)" : "botões com rótulo"}; sem rolagem horizontal`, async () => {
    const { ctx, page } = await telaRecon({ conexoes: tres() }, { width, height });
    const m = await medidas(page);
    await foto(page, `layout-${width}`);
    await ctx.close();
    for (const c of m.cards) { perto(c.left, m.cards[0].left, "mesma borda esquerda"); perto(c.width, m.cards[0].width, "mesma largura"); }
    assert.ok(m.zonas.every((z, i) => i === 0 || z.top >= m.zonas[i - 1].bottom - 1), `zonas do resumo empilhadas: ${JSON.stringify(m.zonas.map((z) => [z.top, z.bottom]))}`);
    for (const l of m.linhas) {
      assert.ok(l.quando.top >= l.estado.bottom - 1, "a última atualização fica abaixo do nome e do estado");
      assert.ok(l.acoes.right <= l.card.right + 1 && l.acoes.left >= l.card.left, "as ações cabem no bloco do banco");
      assert.ok(l.botoes.every(([, w, h]) => w >= 44 && h >= 44), `alvos de 44 px: ${JSON.stringify(l.botoes)}`);
      assert.deepEqual(l.rotulos.every((d) => d === "none"), width < 481, `rótulos: ${l.rotulos}`);
    }
    assert.ok(m.lateral[0] <= m.lateral[1], `rolagem horizontal ${m.lateral}`);
  });
}

test("nome de 70 letras sem espaço e 'Disponíveis' longos não geram rolagem horizontal em 320, 375 e 1280", async () => {
  for (const width of [320, 375, 1280]) {
    const { ctx, page } = await telaRecon({ conexoes: [con(11, "W".repeat(70)), con(12, "Inter"), con(13, "Itaú", "needs_user_action", "Atenção redobrada para este banco")] }, { width, height: 800 });
    const m = await medidas(page);
    await ctx.close();
    assert.ok(m.lateral[0] <= m.lateral[1], `${width}: rolagem horizontal ${m.lateral}`);
    assert.ok(m.cards.every((c) => c.right <= m.lateral[1] + 1), `${width}: card fora da tela`);
  }
});
