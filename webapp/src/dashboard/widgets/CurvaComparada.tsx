// Curva "antes × depois" de um simulador real (compra, corte de hábito). Number() só desenha; o texto vem da API.
import type { PontoSimulador } from "../lib/api-v2.gen";
import { dayMonth, isoDay, moneyText } from "../lib/format.js";
import type { Point } from "../lib/types";
import { TrajectoryChart } from "./TrajectoryChart";

export function CurvaComparada({ antes, depois, rotulos, drawKey }: {
  antes: PontoSimulador[]; depois: PontoSimulador[]; rotulos: [string, string]; drawKey: string;
}) {
  const pts: Point[] = antes.map((p, i) => ({
    date: isoDay(p.data), value: Number(p.saldo), sim: Number(depois[i]?.saldo), real: false, events: [], texto: moneyText(p.saldo) ?? undefined,
  }));
  if (!pts.length || antes.some((p, i) => p.saldo === null || depois[i]?.saldo == null)) {
    return <p className="w-lede">Sem saldo de partida, não dá para desenhar a curva.</p>;
  }
  return (
    <>
      <ul className="legend" aria-label="Legenda do gráfico">
        <li><span className="key forecast" />{rotulos[0]}</li>
        <li><span className="key sim" />{rotulos[1]}</li>
        <li className="faint">de {dayMonth(pts[0].date)} a {dayMonth(pts[pts.length - 1].date)}</li>
      </ul>
      <TrajectoryChart traj={{ points: pts, end: pts[pts.length - 1], start: pts[0].value }} simOn highlight={null} drawKey={drawKey} />
    </>
  );
}
