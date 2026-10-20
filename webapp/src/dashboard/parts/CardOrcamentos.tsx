import type { OrcamentosCategoria } from "../lib/api-v2.gen";
import { moneyText } from "../lib/format.js";
import { mesAno } from "../lib/metas-textos";
import { href } from "../router";
import { Linha } from "../widgets/OrcamentosCategoria";

const VISIVEIS = 5;
const money = (v: unknown) => typeof v === "string" && moneyText(v) !== null;
// Corpo fora do contrato não desenha meio número (o que a `Linha` lê, inclusive).
export const contratoOrcamentos = (d: unknown) => {
  const x = d as OrcamentosCategoria | null;
  return !!x && typeof x.mes === "string" && !!x.total && money(x.total.gasto) && money(x.total.teto) && Array.isArray(x.orcamentos)
    && x.orcamentos.every((o) => o && typeof o.categoria === "string" && typeof o.icone === "string" && typeof o.cor === "string" && typeof o.status === "string"
      && money(o.gasto) && money(o.teto) && money(o.restante) && typeof o.usado_pct === "string" && Number.isFinite(Number(o.usado_pct)));
};

/** O total usado do mês e as categorias mais perto do teto (a ordem é do `usado_pct`, que o servidor calcula). */
export function CardOrcamentos({ d }: { d: OrcamentosCategoria }) {
  const linhas = [...d.orcamentos].sort((a, b) => Number(b.usado_pct) - Number(a.usado_pct));
  return (
    <section className="card-agente" aria-label={`Orçamentos de ${mesAno(d.mes)}`}>
      <p className="card-agente-tit"><b>Orçamentos de {mesAno(d.mes)}</b></p>
      {linhas.length === 0
        ? <p className="card-agente-vazio">Nenhum orçamento por categoria ainda.</p>
        : <p className="card-agente-total"><span className="num">{moneyText(d.total.gasto)}</span> <span className="faint num card-agente-de">de {moneyText(d.total.teto)}</span></p>}
      {linhas.length > 0 && (
        <ul className="orc-lista">
          {linhas.slice(0, VISIVEIS).map((o) => <li key={o.categoria}><div className="orc-linha"><Linha o={o} /></div></li>)}
        </ul>
      )}
      <a className="link msg-open" href={href("/orcamentos-categoria")}>{linhas.length > VISIVEIS ? `Ver os ${linhas.length} orçamentos` : "Ver orçamentos"}<i className="ph ph-arrow-right" aria-hidden="true" /></a>
    </section>
  );
}
