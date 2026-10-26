import { isoDay, monthTitle } from "../../lib/format.js";
import { obj } from "../../lib/ia-cards";
import { mesAtual } from "../../lib/v2";
import { MonthBars } from "../MonthBars";
import { lista, num, reais } from "./legado";
import { MES } from "./resumo";
import { Sub } from "./previsao";

// O card `renda` ("Renda mês a mês", `{ renda: { ok, evolution[], months } }`, de `compute_evolution`): formato legado, dinheiro em float (parts/cardIa/legado.ts). As barras são
// o que entrou, só dos meses até o atual (o servidor monta os baldes em UTC: nas últimas horas do mês em Brasília vem um mês a mais), e abaixo, por mês, o que entrou e o que saiu.
type Mes = { month: string; income: number; expense: number };
const mesOk = (m: unknown) => obj(m) && typeof m.month === "string" && MES.test(m.month) && num(m.income) && num(m.expense);
export const contratoRenda = (r: unknown) => { const d = (r as { renda?: unknown } | null)?.renda; return obj(d) && d.ok === true && lista(d.evolution, mesOk); };

export function CardRenda({ d }: { d: { evolution: Mes[] } }) {
  const atual = mesAtual(), ate = d.evolution.filter((m) => m.month <= atual);
  if (!d.evolution.length) return <p className="ci-vazio">Ainda não há histórico de renda disponível.</p>;
  return (
    <>
      {ate.some((m) => m.income > 0) && <MonthBars rows={ate.map((m) => ({ key: m.month, date: isoDay(`${m.month}-01`), value: Math.max(0, m.income) }))} color="var(--gain)" on={atual} label="Entrou por mês" className={ate.length > 6 ? "ci-meses ci-atual muitos" : "ci-meses ci-atual"} />}
      <ul className="ci-lista">
        {d.evolution.map((m) => (
          <li key={m.month} className="ci-conta ci-grupo">
            <div className="ci-linha ci-leitura"><span className="ci-nome">{monthTitle(m.month)}</span><span className="ci-saldo num">{reais(m.income)}</span></div>
            <ul className="ci-subs"><Sub nome="Saídas" valor={reais(m.expense)} /></ul>
          </li>
        ))}
      </ul>
    </>
  );
}
