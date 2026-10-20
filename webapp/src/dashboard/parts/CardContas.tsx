import type { ContasAPagar } from "../lib/api-v2.gen";
import { moneyText, relativeDays } from "../lib/format.js";

const VISIVEIS = 3;
const plural = (n: number, um: string, varios: string) => `${n} ${n === 1 ? um : varios}`;
// Corpo fora do contrato não desenha meio número.
export const contratoContas = (d: unknown) => {
  const x = d as ContasAPagar | null;
  return !!x && typeof x.total_estimado === "string" && moneyText(x.total_estimado) !== null && Number.isInteger(x.pendentes) && Number.isInteger(x.vencidas)
    && Array.isArray(x.contas) && x.contas.every((c) => c && typeof c.vencida === "boolean" && (c.dias === null || Number.isInteger(c.dias))
      && (c.nome === null || typeof c.nome === "string") && (c.valor === null || moneyText(c.valor) !== null));
};

/** As contas a pagar pendentes: o total estimado, quantas vencidas e as 3 que vencem primeiro (a ordem é a do servidor: a mais atrasada antes). */
export function CardContas({ d }: { d: ContasAPagar }) {
  const resto = d.contas.length - VISIVEIS;
  return (
    <section className="card-agente" aria-label="Contas a pagar">
      <p className="card-agente-tit"><b>Contas a pagar</b> <span className="faint">pendentes</span></p>
      {d.pendentes === 0
        ? <p className="card-agente-vazio">Nenhuma conta a pagar pendente.</p>
        : <><p className="card-agente-total num">{moneyText(d.total_estimado)}</p><p className="faint">{d.tem_variavel ? "estimado" : "no total"} · {plural(d.pendentes, "conta", "contas")}</p></>}
      {d.vencidas > 0 && <p className="warn card-agente-aviso">{plural(d.vencidas, "conta vencida", "contas vencidas")}</p>}
      {d.contas.length > 0 && (
        <ul className="card-agente-lista">
          {d.contas.slice(0, VISIVEIS).map((c) => (
            <li key={c.id}>
              <i className={c.vencida ? "ph ph-warning warn" : "ph ph-calendar-blank"} aria-hidden="true" />
              <span className="card-agente-nome">{c.nome ?? "Conta"}</span>
              <span className={c.vencida ? "warn num" : "faint num"}>{c.dias === null ? "" : relativeDays(c.dias)}</span>
              {c.variavel === true && c.valor !== null && Number(c.valor) === 0 ? <span className="faint">valor a definir</span> : <b className="num">{moneyText(c.valor) ?? "—"}</b>}{/* variável sem estimativa é gravada como 0: não é "grátis" */}
            </li>
          ))}
        </ul>
      )}
      {resto > 0 && <p className="faint">e mais {plural(resto, "conta", "contas")}</p>}
    </section>
  );
}
