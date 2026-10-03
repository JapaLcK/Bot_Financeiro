import { installmentsAhead } from "../lib/api";
import { money0, monthName } from "../lib/format.js";
import { Frame } from "../parts/Frame";
import { MonthBars } from "../parts/MonthBars";

const P = installmentsAhead(6);

// Quanto das próximas faturas já está preso em parcelas, e quando a última acaba.
export function Installments() {
  const next = P.months[0];
  return (
    <Frame id="parcelas" title="Parcelas futuras">
      <div className="inst">
        <div>
          <p className="stat-value num">{money0(next.value)}</p>
          <p className="faint inst-note">da fatura de {monthName(next.date)} já são parcelas</p>
          <p className="inst-end">Última parcela em <b>{monthName(P.last)} de {P.last.getFullYear()}</b></p>
        </div>
        <MonthBars rows={P.months} color="var(--warn)" label="Parcelas nas próximas 6 faturas" />
      </div>
    </Frame>
  );
}
