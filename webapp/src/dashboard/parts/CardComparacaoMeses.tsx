import type { ResumoDoMes } from "../lib/api-v2.gen";
import { moneyText } from "../lib/format.js";
import { mesAno } from "../lib/metas-textos";
import { href } from "../router";
import { contratoResumoMes, menos } from "./CardResumoMes";
import { Selos } from "./Selos";

// Os dois corpos do resumo-do-mes, [mais antigo, mais novo], cada um do mês pedido: fora disso não desenha meio card.
export const contratoComparacaoMeses = (d: unknown, meses: [string, string]) =>
  Array.isArray(d) && d.length === 2 && contratoResumoMes(d[0], meses[0]) && contratoResumoMes(d[1], meses[1]);

const minus = (mes: string) => mesAno(mes).split(" de ")[0];
const nome = (mes: string) => minus(mes).replace(/^./, (c) => c.toUpperCase());
// Melhorou = verde, piorou = aviso, zero = neutro; o sinal fica no texto (a cor não é o único sinal).
const sentido = (dif: string, subirEMelhor: boolean) => (!/[1-9]/.test(dif) ? "" : dif.startsWith("-") === subirEMelhor ? " warn" : " gain");

/** O título do card, também o dos estados Carregando/erro do `CardAgente`: "Setembro × outubro de 2026". */
export const tituloComparacao = (meses: [string, string]) => {
  const [a, b] = meses.map((m) => mesAno(m));
  return meses[0].slice(0, 4) === meses[1].slice(0, 4) ? `${nome(meses[0])} × ${minus(meses[1])} de ${meses[1].slice(0, 4)}` : `${a.replace(/^./, (c) => c.toUpperCase())} × ${b}`;
};

/** Entrou, Saiu e Sobrou dos dois meses lado a lado e a diferença (mais novo − mais antigo): o resumo-do-mes de cada um, a mesma regra do `compare_periods` por fatura. */
export function CardComparacaoMeses({ d, meses, motivos }: { d: [ResumoDoMes, ResumoDoMes]; meses: [string, string]; motivos: string[] }) {
  const [ant, nov] = d;
  const sob = (x: ResumoDoMes) => menos(x.entrou, x.saiu);
  const linhas: [string, string, string, boolean][] = [["Entrou", ant.entrou, nov.entrou, true], ["Saiu", ant.saiu, nov.saiu, false], ["Sobrou", sob(ant), sob(nov), true]];
  const [a, b] = meses.map((m) => mesAno(m));
  return (
    <section className="card-agente" aria-label={`Comparação de ${a} com ${b}`}>
      <p className="card-agente-tit"><b>{tituloComparacao(meses)}</b></p>
      <dl className="card-agente-comp">
        {linhas.map(([rot, x, y, sobe]) => {
          const dif = menos(y, x);
          return (
            <div key={rot}>
              <dt>{rot}</dt>
              <dd className={`num card-agente-dif${sentido(dif, sobe)}`}><span className="sr-only">Diferença </span>{/[1-9]/.test(dif) && !dif.startsWith("-") ? "+" : ""}{moneyText(dif)}</dd>
              <dd className="num"><span className="faint">{nome(meses[0])}</span>{moneyText(x)}</dd>
              <dd className="num"><span className="faint">{nome(meses[1])}</span>{moneyText(y)}</dd>
            </div>
          );
        })}
      </dl>
      <Selos motivos={motivos} />
      <a className="link msg-open" href={href("/")}>Ver o resumo<i className="ph ph-arrow-right" aria-hidden="true" /></a>
    </section>
  );
}
