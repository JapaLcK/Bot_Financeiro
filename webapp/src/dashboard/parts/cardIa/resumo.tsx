import { useQuery } from "@tanstack/react-query";
import type { ResumoDoMes } from "../../lib/api-v2.gen";
import { textos } from "../../lib/bancos";
import { isoDay, medida, moneyText, monthTitle } from "../../lib/format.js";
import { obj, type MesDetalhes, type Periodo } from "../../lib/ia-cards";
import { categoriasQuery } from "../../lib/v2";
import { saida } from "../../widgets/Compromissos";
import { LancamentoLinha } from "../LancamentoLinha";
import { MonthBars } from "../MonthBars";
import { menos } from "../CardResumoMes";
import { Selos } from "../Selos";
import { Composicao, LinhasCat, type ItemCat } from "./Expansivel";
import { maiuscula } from "./gastos";
import { itemOk } from "./LancamentosDoCard";
import { useMais } from "./mais";

// Os cards `resumo` ("Resumo do mês") e `periodo` (o intervalo, de 1 a 12 meses) do chat: o `recursos` do bloco é `{ resumo: <GET /api/v2/resumo-do-mes>, detalhes: <MesDetalhes> }`
// e `{ periodo: <Periodo> }`. Só leitura da foto, sem consulta: o `periodo` já traz os maiores lançamentos de cada categoria (tocar abre a lista aqui dentro).
// Diferença do app: sem a curva acumulada do mês (o bloco não traz o mês anterior para comparar) e sem o esmaecido dos meses antes do histórico (o selo "início do histórico" avisa).
const MES = /^\d{4}-(0[1-9]|1[0-2])$/;
export const contratoResumo = (r: unknown) => {
  const x = r as { resumo?: ResumoDoMes; detalhes?: MesDetalhes } | null;
  return !!x && obj(x.resumo) && moneyText(x.resumo.entrou) !== null && moneyText(x.resumo.saiu) !== null && textos(x.resumo.motivos) && obj(x.detalhes) && textos(x.detalhes.motivos);
};
export const contratoPeriodo = (r: unknown) => {
  const d = (r as { periodo?: Periodo } | null)?.periodo;
  return obj(d) && MES.test(String(d.inicio)) && MES.test(String(d.fim)) && [d.entrou, d.saiu, d.saldo].every((v) => moneyText(v) !== null) && textos(d.motivos)
    && Array.isArray(d.meses) && d.meses.every((m) => obj(m) && MES.test(String(m.mes)) && moneyText(m.saiu) !== null)
    && Array.isArray(d.categorias) && d.categorias.every((c) => obj(c) && moneyText(c.valor) !== null && Number.isInteger(c.percentual) && Array.isArray(c.maiores) && c.maiores.every(itemOk));
};

/** "Agosto 2026", "Março a agosto 2026" (mesmo ano) ou "Nov 2025 a abr 2026" (anos diferentes): o título do app. */
export function tituloPeriodo(inicio: string, fim: string) {
  const [a, b] = [inicio.slice(0, 4), fim.slice(0, 4)], mes = (k: string) => monthTitle(k).split(" ")[0];
  if (inicio === fim) return monthTitle(inicio);
  return a === b ? `${mes(inicio)} a ${mes(fim).toLowerCase()} ${b}` : `${mes(inicio).slice(0, 3)} ${a} a ${mes(fim).slice(0, 3).toLowerCase()} ${b}`;
}

export const tituloDoPeriodo = (r: unknown) => { const p = (r as { periodo: Periodo }).periodo; return tituloPeriodo(p.inicio, p.fim); };

// O saldo em manchete e, embaixo, o que entrou e o que saiu (a barra de cada um é a razão com o maior dos dois).
function Fluxo({ entrou, saiu, saldo, rotulo }: { entrou: string; saiu: string; saldo: string; rotulo: string }) {
  const maximo = Math.max(medida(entrou), medida(saiu), 1), negativo = saldo.startsWith("-") && /[1-9]/.test(saldo);
  const linha = (nome: string, valor: string, cor: string, texto: string | null) => (
    <li className="ci-conta">
      <div className="ci-linha ci-fluxo"><span className="ci-nome">{nome}</span><span className="ci-trilho" aria-hidden="true"><i style={{ background: cor, transform: `scaleX(${medida(valor) / maximo})` }} /></span><span className="ci-saldo num">{texto}</span></div>
    </li>
  );
  return (
    <>
      <div className="ci-total">
        <span className={`stat-value num${negativo ? " warn" : ""}`}>{moneyText(saldo)}</span>
        <p className="ci-leg">{rotulo}</p>
      </div>
      <ul className="ci-lista">{linha("Entrou", entrou, "var(--gain)", moneyText(entrou))}{linha("Saiu", saiu, "var(--alert)", saida(saiu))}</ul>
    </>
  );
}

export function CardResumo({ resumo, detalhes }: { resumo: ResumoDoMes; detalhes: MesDetalhes }) {
  return (
    <>
      <Fluxo entrou={resumo.entrou} saiu={resumo.saiu} saldo={menos(resumo.entrou, resumo.saiu)} rotulo="Sobrou" />
      <Selos motivos={[...new Set([...resumo.motivos, ...detalhes.motivos])]} />
    </>
  );
}

export function CardPeriodo({ d }: { d: Periodo }) {
  const catalogo = useQuery(categoriasQuery).data?.categorias ?? [];
  const ano = (d.meses[d.meses.length - 1]?.mes ?? "").slice(0, 4); // o do último mês: data de outro ano (período que vira o ano, parcela antiga) leva o ano
  const itens: ItemCat[] = d.categorias.map((c) => {
    const cat = catalogo.find((x) => x.chave === c.categoria);
    return {
      nome: maiuscula(cat?.nome || c.categoria || "Sem categoria"), valor: c.valor, pct: c.percentual, cor: cat?.cor ?? "var(--ink-3)",
      corpo: c.maiores.length > 0 ? <div className="ci-lanc"><p className="ci-cap">Maiores lançamentos</p><ul className="ci-lanc-lista">{c.maiores.map((l) => <LancamentoLinha key={l.id} item={l} categorias={catalogo} historico={l.data.slice(0, 4) !== ano} />)}</ul></div> : undefined,
    };
  });
  const [vistos, mais] = useMais(itens, 5, ["categoria", "categorias"]);
  return (
    <>
      <Fluxo entrou={d.entrou} saiu={d.saiu} saldo={d.saldo} rotulo="Saldo" />
      <MonthBars rows={d.meses.map((m) => ({ key: m.mes, date: isoDay(`${m.mes}-01`), value: medida(m.saiu) }))} color="var(--alert)" label="Saídas por mês" className={d.meses.length > 6 ? "ci-meses muitos" : "ci-meses"} />
      {itens.length === 0 ? <p className="ci-vazio">Nenhum gasto em reais neste período.</p> : <><Composicao itens={itens} /><LinhasCat itens={vistos} />{mais}</>}
      <Selos motivos={d.motivos} />
    </>
  );
}
