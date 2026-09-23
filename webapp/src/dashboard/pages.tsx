import { useMemo, type ReactNode } from "react";
import { GOALS, MONTHS, goalEta, isCurrentMonth, keyDate, monthlySaving, trajectory } from "./lib/api";
import { money0, monthName, monthYear, signedBig, tone } from "./lib/format.js";
import { simActive } from "./lib/store.js";
import type { DashState } from "./lib/types";
import { Frame } from "./parts/Frame";
import { Board } from "./parts/Board";
import { Ledger } from "./parts/Ledger";
import { go, route, type Path } from "./router";
import { BankCdb } from "./widgets/BankCdb";
import { Bills } from "./widgets/Bills";
import { Calendar } from "./widgets/Calendar";
import { Categories } from "./widgets/Categories";
import { CategoryDetail } from "./widgets/CategoryDetail";
import { Goals } from "./widgets/Goals";
import { GoalsTimeline } from "./widgets/GoalsTimeline";
import { Hero } from "./widgets/Hero";
import { Invoice } from "./widgets/Invoice";
import { NetWorth } from "./widgets/NetWorth";
import { Simulator } from "./widgets/Simulator";
import { TrajectoryChart } from "./widgets/TrajectoryChart";
import { Wealth } from "./widgets/Wealth";

// Cada página: título, uma linha do que ela responde e os blocos em grade de 12.
function Page({ path, lede, children }: { path: Path; lede: ReactNode; children: ReactNode }) {
  return (
    <>
      <header className="page-head">
        <h1 id="page-title" tabIndex={-1}>{route(path).title}</h1>
        <p className="page-lede">{lede}</p>
      </header>
      <div className="page-grid">{children}</div>
    </>
  );
}
const Panel = ({ span, children }: { span: number; children: ReactNode }) => <div className={`panel span-${span}`}>{children}</div>;

function Home({ s }: { s: DashState }) {
  return (
    <>
      <header className="page-head">
        <h1 id="page-title" tabIndex={-1}>Resumo de {monthName(keyDate(s.month))}</h1>
        <p className="page-lede">O mês inteiro num lugar. A seta de cada bloco abre a página dele.</p>
      </header>
      <Board s={s} />
    </>
  );
}

function Forecast({ s }: { s: DashState }) {
  return (
    <Page path="/previsao" lede="O saldo dia a dia até o horizonte escolhido, com cada conta no dia em que cai.">
      <Panel span={12}><Hero s={s} /></Panel>
      <Panel span={7}><Bills s={s} days={60} /></Panel>
      <Panel span={5}><Invoice s={s} /></Panel>
    </Page>
  );
}

function Spending({ s }: { s: DashState }) {
  return (
    <Page path="/gastos" lede="Escolha uma categoria para ver de onde vem o gasto; o calendário e o extrato seguem a escolha.">
      <div className="span-5 stack">
        <div className="panel"><Categories s={s} /></div>
        <div className="panel"><Calendar s={s} /></div>
      </div>
      <Panel span={7}><CategoryDetail s={s} /></Panel>
    </Page>
  );
}

function SimChart({ s }: { s: DashState }) {
  const current = MONTHS[MONTHS.length - 1];
  const on = simActive(s);
  const traj = useMemo(() => trajectory(current, "90", on ? s.sim : null), [current, s.sim, on]);
  const gain = on && traj.end.sim != null ? traj.end.sim - traj.end.value : 0;
  return (
    <Frame id="sim-grafico" title="Saldo nos próximos 90 dias" className="w-hero"
      aside={on ? <span className={`w-aside-num num ${tone(gain)}`}>{signedBig(gain)} em 90 dias</span> : null}>
      <ul className="legend" aria-label="Legenda do gráfico">
        <li><span className="key real" />Realizado</li>
        <li><span className="key forecast" />Sem mudar nada</li>
        <li><span className="key band" />Faixa provável</li>
        {on && <li><span className={`key sim ${tone(gain)}`} />Com a simulação</li>}
      </ul>
      <TrajectoryChart traj={traj} simOn={on} highlight={null} drawKey="sim" />
    </Frame>
  );
}

function Simulate({ s }: { s: DashState }) {
  return (
    <Page path="/simulador" lede="Corte um hábito ou some um gasto novo e veja o saldo e as metas mudarem na hora.">
      <Panel span={5}><Simulator s={s} full /></Panel>
      <div className="span-7 stack">
        <div className="panel"><SimChart s={s} /></div>
        <div className="panel"><Goals s={s} /></div>
      </div>
    </Page>
  );
}

function GoalsPage({ s }: { s: DashState }) {
  const total = GOALS.reduce((a, g) => a + g.saved, 0);
  const monthly = GOALS.reduce((a, g) => a + g.monthly, 0);
  const saving = simActive(s) ? monthlySaving(s.sim) : 0;
  const next = [...GOALS].map((g) => ({ g, eta: goalEta(g) })).sort((a, b) => a.eta.months - b.eta.months)[0];
  return (
    <Page path="/metas" lede="Quanto já foi guardado, quanto entra por mês e quando cada meta chega.">
      <div className="span-8 stack">
        <div className="panel"><Goals s={s} detailed title="Suas metas" /></div>
        <div className="panel"><GoalsTimeline s={s} /></div>
      </div>
      <div className="span-4 stack">
        <div className="panel">
          <Frame id="caixinhas" title="Caixinhas">
            <dl className="detail-facts col">
              <div><dt>Guardado no total</dt><dd className="num">{money0(total)}</dd></div>
              <div><dt>Entra por mês</dt><dd className="num">{money0(monthly)}{saving > 0 && <span className="gain"> +{money0(saving)}</span>}</dd></div>
              <div><dt>Próxima a chegar</dt><dd>{next.g.label} <span className="faint num">· {monthYear(next.eta.date)}</span></dd></div>
            </dl>
            <button type="button" className="btn btn-ghost" onClick={() => go("/simulador")}>
              <i className="ph ph-lightning" aria-hidden="true" />Chegar antes: simular
            </button>
          </Frame>
        </div>
        <div className="panel"><BankCdb /></div>
      </div>
    </Page>
  );
}

function Wealthy() {
  return (
    <Page path="/patrimonio" lede="Tudo o que é seu somado: conta, caixinhas e investimentos, nos últimos 12 meses.">
      <Panel span={7}><NetWorth title="Últimos 12 meses" /></Panel>
      <Panel span={5}><Wealth /></Panel>
    </Page>
  );
}

function Launches({ s }: { s: DashState }) {
  return (
    <Page path="/lancamentos" lede={isCurrentMonth(s.month) ? "Tudo o que entrou e saiu no mês, com a mensagem que você mandou ao Piggy." : "O extrato do mês escolhido na barra de cima."}>
      <div className="span-12"><Ledger s={s} /></div>
    </Page>
  );
}

export const PAGES: Record<Path, (p: { s: DashState }) => ReactNode> = {
  "/": Home, "/previsao": Forecast, "/gastos": Spending, "/simulador": Simulate,
  "/metas": GoalsPage, "/patrimonio": Wealthy, "/lancamentos": Launches,
};
