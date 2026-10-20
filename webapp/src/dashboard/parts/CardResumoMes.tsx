import type { ResumoDoMes } from "../lib/api-v2.gen";
import { decimal, moneyText } from "../lib/format.js";
import { mesAno } from "../lib/metas-textos";
import { href } from "../router";

const BIG = BigInt;
// Corpo fora do contrato (ou de outro mês que o pedido) não desenha meio número.
export const contratoResumoMes = (d: unknown, mes: string) => {
  const x = d as ResumoDoMes | null;
  return !!x && x.mes === mes && decimal(x.entrou) !== null && decimal(x.saiu) !== null;
};

// entrou − saiu em inteiros exatos (BigInt, nunca float), de volta como texto decimal para o moneyText.
function sobrou(entrou: string, saiu: string) {
  const a = decimal(entrou)!, b = decimal(saiu)!, e = Math.max(a.escala, b.escala);
  const n = a.n * BIG(10) ** BIG(e - a.escala) - b.n * BIG(10) ** BIG(e - b.escala);
  const dig = (n < BIG(0) ? -n : n).toString().padStart(e + 1, "0");
  return `${n < BIG(0) ? "-" : ""}${dig.slice(0, dig.length - e)}${e ? `.${dig.slice(dig.length - e)}` : ""}`;
}

/** Entrou, Saiu e Sobrou do mês: a regra de `GET /api/v2/resumo-do-mes`, a mesma do `get_period_summary` de meses inteiros. */
export function CardResumoMes({ d }: { d: ResumoDoMes }) {
  const s = sobrou(d.entrou, d.saiu);
  return (
    <section className="card-agente" aria-label={`Resumo de ${mesAno(d.mes)}`}>
      <p className="card-agente-tit"><b>Resumo de {mesAno(d.mes)}</b></p>
      <dl className="card-agente-valores">
        <div><dt>Entrou</dt><dd className="num">{moneyText(d.entrou)}</dd></div>
        <div><dt>Saiu</dt><dd className="num">{moneyText(d.saiu)}</dd></div>
        <div><dt>Sobrou</dt><dd className={`num${s.startsWith("-") ? " warn" : ""}`}>{moneyText(s)}</dd></div>
      </dl>
      <a className="link msg-open" href={href("/")}>Ver o resumo<i className="ph ph-arrow-right" aria-hidden="true" /></a>
    </section>
  );
}
