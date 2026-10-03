import { GOALS, fixedMonthly, incomeHistory, reserveMonths } from "../lib/api";
import { money0, monthName } from "../lib/format.js";
import { Frame } from "../parts/Frame";
import { MonthBars } from "../parts/MonthBars";

const ROWS = incomeHistory();
const one = (n: number) => n.toLocaleString("pt-BR", { maximumFractionDigits: 1 });

// Renda de quem ganha diferente todo mês: o histórico, a média, o pior mês e quanto
// tempo a reserva segura as contas fixas.
export function Income() {
  const avg = ROWS.reduce((a, r) => a + r.value, 0) / ROWS.length;
  const worst = ROWS.reduce((a, r) => (r.value < a.value ? r : a));
  const now = ROWS[ROWS.length - 1];
  const reserve = GOALS.find((g) => g.id === "reserva")!;

  return (
    <Frame id="renda" title="Renda mês a mês">
      <MonthBars rows={ROWS} color="var(--gain)" on={now.key} label="Entradas dos últimos 6 meses" />
      <dl className="detail-facts">
        <div><dt>Média de 6 meses</dt><dd className="num">{money0(avg)}</dd></div>
        <div><dt>Pior mês</dt><dd className="num">{money0(worst.value)} <span className="faint income-when">{monthName(worst.date)}</span></dd></div>
      </dl>
      <p className="income-reserve">
        Sua reserva cobre <b className="num">{one(reserveMonths())} meses</b> de gasto fixo
        <span className="faint">{money0(reserve.saved)} guardados, {money0(fixedMonthly())} de contas fixas por mês</span>
      </p>
    </Frame>
  );
}
