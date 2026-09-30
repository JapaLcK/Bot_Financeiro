/**
 * Receita/despesa de movimentação interna no dashboard: "entrada"/"saída", neutra.
 *
 * O saque em espécie espelhado na Carteira (`_credita`, db/open_finance_cash.py)
 * é `receita` + `is_internal_movement`. A lista (`renderLaunches`) e o detalhe
 * (`_renderLaunchDetail`) diziam "receita", e a etiqueta `tag receita` sai verde
 * (dashboard.css). Decisão do dono: rótulo pela direção, etiqueta neutra (`tag x`).
 *
 * NEGATIVO: tirar o `INTERNAL_LABELS[...]` de renderLaunches e de
 * _renderLaunchDetail (dashboard.js) → vermelho aqui.
 * POSITIVO: a receita/despesa comum da MESMA lista continua "receita"/"despesa"
 * com a classe de cor dela.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { abrirBrowser, fecharBrowser, loadDashboardJs } from "./_dashboard_loader.mjs";

before(abrirBrowser);
after(fecharBrowser);

const linha = (o) => ({
  valor: 200, categoria: "transferencia_interna", nota: null, alvo: "Saque em dinheiro",
  criado_em: "2026-09-30T10:00:00-03:00", is_internal_movement: true, id: 1, ...o,
});

test("lista e detalhe: interna diz entrada/saída e fica neutra; comum não muda", async () => {
  const page = await loadDashboardJs();
  const r = await page.evaluate((launches) => {
    document.body.insertAdjacentHTML("beforeend", '<div id="launches-card"></div>');
    lastData = { recent_launches: launches };
    renderLaunches();
    const tags = [...document.querySelectorAll("#launches-card .row")].map((row) => {
      const t = row.querySelector(".tag");
      return { texto: t.textContent.trim(), classe: t.className };
    });
    const detalhes = launches.map((_, i) => {
      openLaunchDetail(i);
      return document.getElementById("ld-meta").textContent;
    });
    return { tags, detalhes };
  }, [
    linha({ tipo: "receita" }),
    linha({ tipo: "despesa", alvo: "Depósito em dinheiro", id: 2 }),
    linha({ tipo: "receita", alvo: "freela", categoria: "rendimentos", is_internal_movement: false, id: 3 }),
    linha({ tipo: "despesa", alvo: "mercado", categoria: "mercado", is_internal_movement: false, id: 4 }),
  ]);

  assert.deepEqual(r.tags, [
    { texto: "entrada", classe: "tag x" },
    { texto: "saída", classe: "tag x" },
    { texto: "receita", classe: "tag receita" },
    { texto: "despesa", classe: "tag despesa" },
  ]);
  assert.match(r.detalhes[0], /Tipoentrada/, r.detalhes[0]);
  assert.match(r.detalhes[1], /Tiposaída/, r.detalhes[1]);
  assert.match(r.detalhes[2], /Tiporeceita/, r.detalhes[2]);
  assert.match(r.detalhes[3], /Tipodespesa/, r.detalhes[3]);
  assert.deepEqual(page.__errs, []);
  await page.close();
});
