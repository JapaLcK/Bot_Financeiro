import { obj } from "../../lib/ia-cards";
import { Sub } from "./previsao";
import { dataPtBr, indicadorMeta, lista, num, numOuNull, reais, textoOuNull } from "./legado";

// O card `caixinhas` do chat (formato legado `{ ok, goals[] }`, de `status_metas`): a mesma lista do widget de metas do app (ListaMetas), só leitura do bloco. Sem
// meta, a linha é nome, saldo e de onde vem o dinheiro; com meta, o objetivo, o prazo, a barra e os dois ritmos. É a diferença do `metas` (o card v2, com pílulas).
type Goal = {
  id: number; name: string; balance: number; target_amount: number | null; target_date: string | null; source: string | null; of_plan_active: boolean;
  is_goal: boolean; pct_complete: number | null; monthly_pace_current: number | null; monthly_pace_needed: number | null; indicator: string;
};
const goalOk = (g: unknown) => obj(g) && Number.isInteger(g.id) && typeof g.name === "string" && num(g.balance) && numOuNull(g.target_amount) && textoOuNull(g.target_date) && textoOuNull(g.source)
  && typeof g.of_plan_active === "boolean" && typeof g.is_goal === "boolean" && numOuNull(g.pct_complete) && numOuNull(g.monthly_pace_current) && numOuNull(g.monthly_pace_needed) && typeof g.indicator === "string";
export const contratoCaixinhasLegado = (r: unknown) => {
  const d = (r as { caixinhas?: unknown } | null)?.caixinhas;
  return obj(d) && d.ok === true && lista(d.goals, goalOk);
};

const origem = (g: Goal) => (g.source === "open_finance" ? (g.of_plan_active ? "Caixinha do banco" : "Caixinha do banco com sincronização indisponível") : "Caixinha manual");

export function CardCaixinhasLegado({ d }: { d: { goals: Goal[] } }) {
  if (!d.goals.length) return <p className="ci-vazio">Nenhuma caixinha ainda.</p>;
  return (
    <ul className="ci-lista">
      {d.goals.map((g) => (
        <li key={g.id} className="ci-conta ci-grupo">
          <div className="ci-linha ci-leitura"><span className="ci-nome">{g.name}</span><span className="ci-saldo num">{reais(g.balance)}</span></div>
          <p className="ci-cap">{origem(g)}</p>
          {g.is_goal && (
            <>
              <ul className="ci-subs">
                <Sub nome={`Objetivo · ${g.target_date ? dataPtBr(g.target_date) : "Sem prazo definido"}`} valor={reais(g.target_amount)} />
                <Sub nome="Ritmo mensal atual" valor={reais(g.monthly_pace_current)} />
                <Sub nome="Ritmo mensal necessário" valor={reais(g.monthly_pace_needed)} />
              </ul>
              {g.pct_complete !== null && <span className="ci-trilho" role="img" aria-label={`${Math.round(g.pct_complete)}% do objetivo`}><i style={{ background: g.pct_complete >= 100 ? "var(--gain)" : "var(--ink-2)", transform: `scaleX(${Math.min(1, Math.max(0, g.pct_complete / 100))})` }} /></span>}
              <span className="selo">{indicadorMeta(g.indicator)}</span>
            </>
          )}
        </li>
      ))}
    </ul>
  );
}
