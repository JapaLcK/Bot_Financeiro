import { CATEGORIES, MONTHS, TODAY, catById, isCurrentMonth, keyDate, summary } from "../lib/api";
import { LAUNCHES } from "../lib/data.js";
import { money, money0, monthShort } from "../lib/format.js";
import { setFilter } from "../lib/store.js";
import type { DashState, Launch } from "../lib/types";
import { Frame } from "../parts/Frame";
import { go } from "../router";

// Detalhe da categoria escolhida em "Para onde vai" (ou da maior variável do mês).
export function CategoryDetail({ s }: { s: DashState }) {
  const m = summary(s.month);
  const top = CATEGORIES.filter((c) => c.variable).sort((a, b) => m.byCategory[b.id] - m.byCategory[a.id])[0];
  const picked = catById(s.filter.category);
  const c = picked ?? top;
  const mine = m.launches.filter((l) => l.kind === "expense" && l.category === c.id);
  const total = mine.reduce((a, l) => a + (l.amount ?? 0), 0);
  const months = MONTHS.map((k) => ({
    k,
    v: (LAUNCHES[k] as Launch[]).filter((l) => l.kind === "expense" && l.category === c.id).reduce((a, l) => a + (l.amount ?? 0), 0),
  }));
  const peak = Math.max(...months.map((x) => x.v), 1);
  const byPlace = new Map<string, { n: number; v: number }>();
  for (const l of mine) {
    const p = byPlace.get(l.label) ?? { n: 0, v: 0 };
    byPlace.set(l.label, { n: p.n + 1, v: p.v + (l.amount ?? 0) });
  }
  const places = [...byPlace.entries()].sort((a, b) => b[1].v - a[1].v).slice(0, 5);
  const placePeak = Math.max(...places.map(([, p]) => p.v), 1);

  return (
    <Frame id="cat-detalhe" title={<span className="detail-title"><i className={`ph ${c.icon}`} style={{ color: c.color }} aria-hidden="true" />{c.label}{!picked && <span className="faint detail-auto">maior gasto do dia a dia</span>}</span>}
      aside={<button type="button" className="link" onClick={() => { setFilter({ category: c.id, day: null, query: "" }); go("/lancamentos"); }}>
        Ver {mine.length} lançamentos<i className="ph ph-arrow-right" aria-hidden="true" /></button>}>
      <dl className="detail-facts">
        <div><dt>No mês</dt><dd className="num">{money0(total)}</dd></div>
        <div><dt>Compras</dt><dd className="num">{mine.length}</dd></div>
        <div><dt>Valor médio</dt><dd className="num">{money(mine.length ? total / mine.length : 0)}</dd></div>
      </dl>

      <div className="detail-cols">
        <section aria-label="Últimos três meses">
          <h3 className="detail-h">Últimos meses</h3>
          <ul className="months">
            {months.map(({ k, v }) => (
              <li key={k} data-on={k === s.month || undefined}>
                <span className="months-bar"><i style={{ background: c.color, transform: `scaleY(${v / peak})` }} /></span>
                <b className="num">{money0(v)}</b>
                <span className="faint">{monthShort(keyDate(k))}{isCurrentMonth(k) ? ` até ${TODAY.getDate()}` : ""}</span>
              </li>
            ))}
          </ul>
        </section>
        <section aria-label="Onde mais gasta">
          <h3 className="detail-h">Onde mais gasta</h3>
          {places.length === 0 ? <p className="faint">Nenhum gasto nesta categoria no mês.</p> : (
            <ul className="places">
              {places.map(([name, p]) => (
                <li key={name}>
                  <span className="place-name">{name}</span>
                  <span className="faint num">{p.n}×</span>
                  <b className="num">{money0(p.v)}</b>
                  <span className="place-bar" aria-hidden="true"><i style={{ background: c.color, transform: `scaleX(${p.v / placePeak})` }} /></span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {mine.length > 0 && (
        <section className="detail-recent" aria-label="Últimas compras">
          <h3 className="detail-h">Últimas compras</h3>
          <ul className="inv-list">
            {mine.slice(0, 6).map((l, i) => (
              <li key={l.id ?? i}>
                <span className="faint num">{l.date.getDate()}</span>
                <span className="inv-name">{l.label}{l.msg && <q className="row-msg"> {l.msg}</q>}</span>
                <span className="faint">{l.source === "whatsapp" ? "WhatsApp" : l.source === "cartao" ? "Cartão" : "Open Finance"}</span>
                <b className="num">{money(l.amount ?? 0)}</b>
              </li>
            ))}
          </ul>
        </section>
      )}
    </Frame>
  );
}
