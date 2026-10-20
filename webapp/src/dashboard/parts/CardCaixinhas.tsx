import type { Caixinhas } from "../lib/api-v2.gen";
import { moneyText } from "../lib/format.js";

const VISIVEIS = 4;
const money = (v: unknown) => typeof v === "string" && moneyText(v) !== null;
// Corpo fora do contrato não desenha meio número.
export const contratoCaixinhas = (d: unknown) => {
  const x = d as Caixinhas | null;
  return !!x && money(x.total) && Array.isArray(x.caixinhas)
    && x.caixinhas.every((c) => c && typeof c.nome === "string" && money(c.saldo) && (c.meta === null || money(c.meta))
      && (c.progresso_pct === null || (typeof c.progresso_pct === "string" && Number.isFinite(Number(c.progresso_pct)))));
};

/** O total guardado e as caixinhas do maior saldo, com a barra de quem tem meta (o saldo é o último registrado: o servidor não aplica juros ao ler). */
export function CardCaixinhas({ d }: { d: Caixinhas }) {
  const resto = d.caixinhas.length - VISIVEIS;
  return (
    <section className="card-agente" aria-label="Caixinhas">
      <p className="card-agente-tit"><b>Caixinhas</b> <span className="faint">guardado</span></p>
      {d.caixinhas.length === 0
        ? <p className="card-agente-vazio">Nenhuma caixinha ainda.</p>
        : <p className="card-agente-total num">{moneyText(d.total)}</p>}
      {d.caixinhas.length > 0 && (
        <ul className="card-agente-lista card-agente-lista-2">
          {d.caixinhas.slice(0, VISIVEIS).map((c) => {
            const p = c.meta !== null && c.progresso_pct !== null ? Math.max(0, Math.min(100, Number(c.progresso_pct))) : null;
            return (
              <li key={c.id}>
                <span className="card-agente-nome">{c.nome}</span><b className="num">{moneyText(c.saldo)}</b>
                {p !== null && (
                  <>
                    <div className={`meter${c.atingida ? " ok" : ""}`} role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(p)} aria-label={`${c.nome}: ${Math.round(p)}% da meta`}><span style={{ width: `${p}%` }} /></div>
                    <span className="faint card-agente-sub">{c.atingida ? "Meta batida" : `${Math.round(p)}%`} · meta {moneyText(c.meta)}</span>
                  </>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {resto > 0 && <p className="faint">e mais {resto} {resto === 1 ? "caixinha" : "caixinhas"}</p>}
    </section>
  );
}
