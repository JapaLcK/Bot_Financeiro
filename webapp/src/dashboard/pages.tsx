import { useMemo, useRef, type ReactNode } from "react";
import { BANK_CDB, BANK_CDB_TOTAL, GOALS, MONTHS, caixinhasTotal, goalEta, goalsTotal, isCurrentMonth, keyDate, monthlySaving, trajectory } from "./lib/api";
import { money0, monthName, monthYear, signedBig, tone } from "./lib/format.js";
import { mesDe, simActive } from "./lib/store.js";
import type { DashState } from "./lib/types";
import { Frame } from "./parts/Frame";
import { Board } from "./parts/Board";
import { DicaDaTela } from "./parts/Dica";
import { Lancamentos } from "./parts/Lancamentos";
import { Ledger } from "./parts/Ledger";
import { abrirGuia } from "./parts/Guia";
import { PiggyChat } from "./parts/PiggyChat";
import { ContaBancos } from "./conta/Bancos";
import { ContaCobranca } from "./conta/Cobranca";
import { ContaConfiguracoes } from "./conta/Configuracoes";
import { ContaPerfil } from "./conta/Perfil";
import { DEMO } from "./lib/v2";
import { useMedia } from "./useMedia";
import { go, href, route, type Path } from "./router";
import { Bills } from "./widgets/Bills";
import { Calendar } from "./widgets/Calendar";
import { Categories } from "./widgets/Categories";
import { CategoryDetail } from "./widgets/CategoryDetail";
import { GastosCalendario } from "./widgets/GastosCalendario";
import { GastosCategorias } from "./widgets/GastosCategorias";
import { GastosCompras } from "./widgets/GastosCompras";
import { GastosDetalhe } from "./widgets/GastosDetalhe";
import { Goals } from "./widgets/Goals";
import { GoalsTimeline } from "./widgets/GoalsTimeline";
import { Hero } from "./widgets/Hero";
import { Invoice } from "./widgets/Invoice";
import { NetWorth } from "./widgets/NetWorth";
import { MetasPainel } from "./widgets/Metas";
import { MilhaoPainel } from "./widgets/ProgressoMilhao";
import { OrcamentoPainel } from "./widgets/OrcamentoDomestico";
import { OrcamentosCategoriaPainel } from "./widgets/OrcamentosCategoria";
import { PrevisaoPainel } from "./widgets/Previsao";
import { Simulator } from "./widgets/Simulator";
import { SimuladorCompra } from "./widgets/SimuladorCompra";
import { SubscriptionList } from "./widgets/Subscriptions";
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
      <DicaDaTela path={path} />
      <div className="page-grid">{children}</div>
    </>
  );
}
const Panel = ({ span, children }: { span: number; children: ReactNode }) => <div className={`panel span-${span}`}>{children}</div>;

function Home({ s }: { s: DashState }) {
  return (
    <>
      <header className="page-head">
        <h1 id="page-title" tabIndex={-1}>Resumo de {monthName(keyDate(mesDe(s)))}</h1>
        <p className="page-lede">O mês inteiro num lugar. A seta de cada bloco abre a página dele.</p>
      </header>
      <Board s={s} />
    </>
  );
}

function Forecast({ s }: { s: DashState }) {
  if (!DEMO) return <Page path="/previsao" lede="Quanto deve sobrar até o horizonte escolhido, com os compromissos que o Piggy conhece."><PrevisaoPainel /></Page>;
  return (
    <Page path="/previsao" lede="O saldo dia a dia até o horizonte escolhido, com cada conta no dia em que cai.">
      <Panel span={12}><Hero s={s} /></Panel>
      <Panel span={7}><Bills s={s} days={60} /></Panel>
      <Panel span={5}><Invoice s={s} /></Panel>
    </Page>
  );
}

function Spending({ s }: { s: DashState }) {
  const [Cats, Cal, Detail] = DEMO ? [Categories, Calendar, CategoryDetail] : [GastosCategorias, GastosCalendario, GastosDetalhe];
  // Acima de 1100px (o @media de pages.css, 1100px + 1) o card de compras ocupa o vão sob o Detalhe, na coluna da direita.
  // Em coluna única ele vem logo depois do que o abriu: o calendário (dia) ou o Detalhe (categoria). A ordem do DOM
  // é a ordem visual, para o Tab seguir a leitura.
  const largo = useMedia("(min-width: 1101px)");
  const g = DEMO ? null : s.gastosAberto;
  const chegada = useRef(!!s.gastosAberto); // o card já estava aberto quando a página montou: veio do Resumo (sair de /gastos fecha o card: só a ida pelo Resumo monta a página com ele aberto)
  const card = g && <GastosCompras s={s} chegada={chegada} />;
  const doDia = g && "dia" in g;
  return (
    <Page path="/gastos" lede="Escolha uma categoria para ver de onde vem o gasto; o calendário e o extrato seguem a escolha.">
      <div className="span-5 stack">
        <div className="panel"><Cats s={s} /></div>
        <div className="panel"><Cal s={s} /></div>
        {!largo && doDia && card}
      </div>
      <div className="span-7 stack">
        <div className="panel"><Detail s={s} /></div>
        {(largo || !doDia) && card}
      </div>
    </Page>
  );
}

// Os blocos da lista entram direto na grade: cada `.w` precisa ser filho de um `.panel`.
function SubscriptionsPage() {
  return (
    <Page path="/assinaturas" lede="Os serviços que cobram todo mês, achados no seu Open Finance.">
      <SubscriptionList />
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

function ComprarPage() {
  return (
    <Page path="/simulador/compra" lede="O que uma compra faz com o seu saldo nos próximos 90 dias, à vista ou parcelada. O PigBank mostra os números; a decisão é sua.">
      <SimuladorCompra />
    </Page>
  );
}

function GoalsPage({ s }: { s: DashState }) {
  if (!DEMO) return <Page path="/metas" lede="Quanto já tem, quanto falta e se o seu ritmo chega lá."><MetasPainel /></Page>;
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
              <div><dt>Guardado no total</dt><dd className="num">{money0(caixinhasTotal())}</dd></div>
              <div className="fact-sub"><dt>Nas metas</dt><dd className="num">{money0(goalsTotal())}</dd></div>
              <div className="fact-sub"><dt>No {BANK_CDB.bank} · via {BANK_CDB.via}</dt><dd className="num">{money0(BANK_CDB_TOTAL)}</dd></div>
              <div><dt>Entra por mês</dt><dd className="num">{money0(monthly)}{saving > 0 && <span className="gain"> +{money0(saving)}</span>}</dd></div>
              <div><dt>Próxima a chegar</dt><dd>{next.g.label} <span className="faint num">· {monthYear(next.eta.date)}</span></dd></div>
            </dl>
            <button type="button" className="btn btn-ghost" onClick={() => go("/simulador")}>
              <i className="ph ph-lightning" aria-hidden="true" />Chegar antes: simular
            </button>
          </Frame>
        </div>
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
    <Page path="/lancamentos" lede={!DEMO ? "O extrato do mês escolhido na barra de cima, com as informações de cada origem." : isCurrentMonth(s.month) ? "Tudo o que entrou e saiu no mês, com a mensagem que você mandou ao Piggy." : "O extrato do mês escolhido na barra de cima."}>
      <div className="span-12">{DEMO ? <Ledger s={s} /> : <Lancamentos s={s} />}</div>
    </Page>
  );
}

// Ferramentas: só o Simulador já existe no protótipo; as outras vêm do dashboard atual
// (ou são novas) e entram uma de cada vez. O Painel antigo sai do v2 para o /app. O guia mora
// aqui também (e no Cmd-K e na Ajuda do Menu do celular).
const TOOLS: { label: string; line: string; icon: string; to?: Path; url?: string; run?: () => void }[] = [
  { label: "Simulador", line: "E se você cortasse o delivery pela metade? Veja o saldo e as metas mudarem na hora.", icon: "ph-lightning", to: "/simulador" },
  { label: "Painel antigo", line: "O dashboard de sempre, com os seus dados de verdade.", icon: "ph-chart-bar", url: "/app" },
  { label: "Orçamento doméstico", line: "Divida a renda do mês em 6 potes e veja quanto ainda cabe em cada um.", icon: "ph-house", to: "/orcamento" },
  { label: "Orçamentos por categoria", line: "Um teto por categoria e um aviso antes de estourar.", icon: "ph-chart-pie", to: "/orcamentos-categoria" },
  { label: "Vale a pena comprar?", line: "Veja o que uma compra faz com o seu saldo nos próximos 90 dias.", icon: "ph-shopping-cart", ...(DEMO ? {} : { to: "/simulador/compra" as Path }) },
  { label: "Progresso ao milhão", line: "Quanto falta e em quanto tempo você chega ao primeiro milhão.", icon: "ph-trophy", to: "/milhao" },
  { label: "Agentes", line: "Piggys que cuidam de uma parte do seu dinheiro por você.", icon: "ph-robot" },
  ...(DEMO ? [] : [{ label: "Guia do painel", line: "O Piggy te mostra o painel em 3 passos.", icon: "ph-question", run: abrirGuia }]),
];

function Tools() {
  return (
    <Page path="/ferramentas" lede="Tudo o que ajuda a decidir, num lugar só.">
      <ul className="span-12 tools">
        {TOOLS.map((t) => {
          const body = <><i className={`ph ${t.icon}`} aria-hidden="true" /><b>{t.label}</b><span>{t.line}</span></>;
          const link = t.url ?? (t.to && href(t.to));
          return (
            <li key={t.label}>
              {link ? <a className="tool" href={link}>{body}</a>
                : t.run ? <button type="button" className="tool" onClick={t.run}>{body}</button>
                : <div className="tool" aria-disabled="true">{body}<em>Em breve</em></div>}
            </li>
          );
        })}
      </ul>
    </Page>
  );
}

function Budget({ s }: { s: DashState }) {
  return (
    <Page path="/orcamento" lede="A renda do mês dividida em potes: quanto cabe em cada um e quanto já saiu. Aportes em investimento contam no pote Liberdade financeira.">
      <OrcamentoPainel s={s} />
    </Page>
  );
}

function Milhao() {
  return (
    <Page path="/milhao" lede="Quanto falta para o seu primeiro milhão e quando você chega lá, no ritmo de hoje.">
      <MilhaoPainel />
    </Page>
  );
}

function CategoryBudgets() {
  return (
    <Page path="/orcamentos-categoria" lede="Um teto por mês para cada categoria. Quando um gasto que você manda para o Piggy passa de 80%, 100% e 120%, ele avisa no WhatsApp.">
      <OrcamentosCategoriaPainel />
    </Page>
  );
}

export const PAGES: Record<Path, (p: { s: DashState }) => ReactNode> = {
  "/": Home, "/previsao": Forecast, "/gastos": Spending, "/assinaturas": SubscriptionsPage, "/simulador": Simulate, "/simulador/compra": ComprarPage,
  "/metas": GoalsPage, "/milhao": Milhao, "/patrimonio": Wealthy, "/lancamentos": Launches,
  "/orcamento": Budget, "/orcamentos-categoria": CategoryBudgets, "/ferramentas": Tools, "/piggy": PiggyChat,
  "/conta/perfil": ContaPerfil, "/conta/cobranca": ContaCobranca, "/conta/configuracoes": ContaConfiguracoes, "/conta/bancos": ContaBancos,
};
