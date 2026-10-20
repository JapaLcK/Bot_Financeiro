// "Comparado ao mês anterior" (/api/v2/comparacao, Plus+): a mesma comparação do chat, sem
// recalcular. O período e a base do cartão são o texto `escolha` do servidor. Dinheiro é texto
// (moneyText); Number() só escolhe seta e tom. Erro vem antes do dado: o TanStack guarda o
// `data` antigo quando o refetch falha, e o downgrade no meio da sessão mostraria número velho.
import { useContext } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Comparacao as Dados, LinhaComparada } from "../lib/api-v2.gen";
import { isoDay, monthName, moneyText } from "../lib/format.js";
import { mesDe } from "../lib/store.js";
import type { DashState } from "../lib/types";
import { ErroApi, comparacaoQuery } from "../lib/v2";
import { Frame, FrameLink } from "../parts/Frame";
import { Selos } from "../parts/Selos";
import { ConvitePro } from "./Compromissos";

const pctTexto = (p: string) => `${p.replace("-", "").replace(".", ",")}%`;

// `bom`: para que lado a variação é boa (gasto caindo, entrada subindo); 0 = sempre neutra. No mês aberto
// (P1-B: o mês inteiro até agora contra o anterior inteiro) a queda ainda não se sabe e fica neutra; a alta
// já é fato e mantém a cor (decisão do dono PB-1). O Resultado do mês aberto é neutro (decisão de 2026-10-20).
function variacao(l: LinhaComparada, bom: 1 | -1 | 0, aberto: boolean) {
  if (l.estado === "nova") return { texto: "nova", tom: "faint", seta: null, fala: "não tinha no mês anterior" };
  if (l.estado === "vazio") return { texto: "—", tom: "faint", seta: null, fala: "nada nos dois períodos" };
  if (l.estado === "igual") return { texto: "igual", tom: "faint", seta: null, fala: "igual ao anterior" };
  const sobe = Number(l.total_delta) > 0;
  const texto = l.pct === null ? moneyText(l.total_delta.replace("-", "")) : pctTexto(l.pct); // a seta diz o sinal
  const tom = bom === 0 || (aberto && !sobe) ? "faint" : (sobe ? 1 : -1) === bom ? "gain" : "warn";
  return { texto, tom, seta: sobe ? "ph-arrow-up" : "ph-arrow-down", fala: `${sobe ? "subiu" : "caiu"} ${texto}` };
}

function Linha({ nome, l, bom, antes, aberto }: { nome: string; l: LinhaComparada; bom: 1 | -1 | 0; antes: string; aberto: boolean }) {
  const v = variacao(l, bom, aberto);
  return (
    <li className="cmp-linha" aria-label={`${nome}: ${moneyText(l.total_atual)}, em ${antes} ${moneyText(l.total_anterior)}, ${v.fala}`}>
      <span className="cat-name"><span className="cat-nome">{nome}</span></span>
      <span className="cat-val num">{moneyText(l.total_atual)}</span>
      <span className={`cat-delta num ${v.tom}`}>{v.seta && <i className={`ph ${v.seta}`} aria-hidden="true" />}{v.texto}</span>
      <span className="cmp-antes faint">em {antes}: <span className="num">{moneyText(l.total_anterior)}</span></span>
    </li>
  );
}

// No Resumo a célula tem altura fixa e, na grade de 4 colunas, é estreita: o corpo inteiro rola
// (os totais ficam no topo, nada some). O teclado tem de alcançar a rolagem, como no "Para onde vai".
function Corpo({ d }: { d: Dados }) {
  const antes = monthName(isoDay(d.anterior.inicio));
  const rola = useContext(FrameLink) !== null;
  const aberto = d.modo === "mes_aberto";
  if (d.motivo === "inicio_do_historico" || !d.despesa || !d.receita || !d.resultado) {
    return <div className="empty"><i className="ph ph-clock-counter-clockwise" aria-hidden="true" /><p>O mês anterior está fora do histórico do seu plano.</p></div>;
  }
  return (
    <div className="cmp-corpo" tabIndex={rola ? 0 : undefined} aria-label={rola ? "Comparação do mês" : undefined}>
      <p className="w-lede cmp-lede">{d.escolha}</p>
      <ul className="cats cmp-totais" aria-label="Totais">
        <Linha nome="Saiu" l={d.despesa} bom={-1} antes={antes} aberto={aberto} />
        <Linha nome="Entrou" l={d.receita} bom={1} antes={antes} aberto={aberto} />
        <Linha nome="Resultado" l={d.resultado} bom={aberto ? 0 : 1} antes={antes} aberto={aberto} />
      </ul>
      {d.motivo === "sem_base" ? <p className="faint">Sem lançamentos em {antes} para comparar.</p>
        : d.motivo === "categorias_indisponiveis" ? <p className="faint">Não deu para carregar as categorias agora.</p>
        : d.categorias.length > 0 && (
          <>
            <h3 className="prev-h">Gastos por categoria</h3>
            <ul className="cats" aria-label="Gastos por categoria">
              {d.categorias.map((c, i) => <Linha key={`${c.categoria}#${i}`} nome={c.categoria} l={c} bom={-1} antes={antes} aberto={aberto} />)}
            </ul>
          </>
        )}
      {d.categorias_omitidas > 0 && <p className="faint">+{d.categorias_omitidas} outras</p>}
      <Selos motivos={d.motivos} />
    </div>
  );
}

export function Comparacao({ s }: { s: DashState }) {
  const q = useQuery(comparacaoQuery(mesDe(s)));
  const titulo = "Comparado ao mês anterior";
  if (q.error instanceof ErroApi && q.error.code === "pro_required") {
    return <Frame id="comparacao" title={titulo} real><ConvitePro texto="Comparar períodos é do Plus e do Pro." /></Frame>;
  }
  return (
    <Frame id="comparacao" title={titulo} real>
      {q.isPending ? <p role="status" className="faint">Carregando…</p>
        : q.isError ? (
          <div className="empty" role="alert">
            <p>Não deu para carregar a comparação.</p>
            <button type="button" className="btn btn-ghost retry" onClick={() => q.refetch()}>Tentar de novo</button>
          </div>
        ) : <Corpo d={q.data} />}
    </Frame>
  );
}
