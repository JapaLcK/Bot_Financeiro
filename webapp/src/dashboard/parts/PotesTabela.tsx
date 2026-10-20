// "Potes" do /orcamento: tabela no desktop, cards no celular (o CSS troca; os dois ficam no DOM, e o que está
// escondido some da leitura de tela). Linha e card abrem os lançamentos do pote pelo botão do nome: o clique em
// qualquer ponto da linha vale o mesmo, o teclado e a leitura de tela passam só pelo botão.
import type { MouseEvent } from "react";
import type { Pote } from "../lib/api-v2.gen";
import { moneyText } from "../lib/format.js";
import { ehNegativo } from "../lib/metas-textos";
import { largura, pctTxt, visualDoPote } from "../lib/potes";
import { Frame } from "./Frame";
import { mostra } from "./OrcamentoForm";

const Icone = ({ p }: { p: Pote }) => {
  const v = visualDoPote(p);
  return <span className="orc-ico" style={{ color: v.cor, background: v.hex ? `${v.cor}1f` : "var(--plane-3)" }} aria-hidden="true"><i className={`ph ${v.icone}`} /></span>;
};
const Barra = ({ p, passou }: { p: Pote; passou: boolean }) => <div className={`meter${passou ? " warn" : ""}`} aria-hidden="true"><span style={{ width: `${largura(p.usado_pct)}%` }} /></div>;

function Restante({ p, passou }: { p: Pote; passou: boolean }) {
  if (passou) return <span className="warn"><i className="ph ph-warning" aria-hidden="true" /> passou {moneyText(p.restante.slice(1))}</span>;
  if (ehNegativo(p.restante)) return <>guardou {moneyText(p.restante.slice(1))} a mais</>;
  return <span className={Number(p.restante) > 0 ? "gain" : undefined}>{moneyText(p.restante)}</span>;
}

export function PotesTabela({ potes, abrir: abrirPote }: { potes: Pote[]; abrir: (chave: string) => void }) {
  // O clique fora do botão do nome também abre: o foco passa por ele antes, para o modal devolvê-lo ali ao fechar.
  const abrir = (e: MouseEvent<HTMLElement>, chave: string) => { e.currentTarget.querySelector("button")?.focus({ preventScroll: true }); abrirPote(chave); };
  return (
    <div className="panel span-12">
      <Frame id="orcamento-potes" title="Potes" className="orc-w" real>
        <div className="orc-rola" role="region" aria-label="Tabela dos potes" tabIndex={0}>
        <table className="orc-tabela">
          <thead>
            <tr><th scope="col">Pote</th><th scope="col">Percentual</th><th scope="col">Uso do teto</th><th scope="col">Orçado</th><th scope="col">Gasto</th><th scope="col">Restante</th><th scope="col"><span className="sr-only">Abrir</span></th></tr>
          </thead>
          <tbody>
            {potes.map((p) => {
              const passou = ehNegativo(p.restante) && !p.guardado;
              return (
                <tr key={p.chave} onClick={(e) => abrir(e, p.chave)}>
                  <th scope="row">
                    <span className="orc-cat"><Icone p={p} /><button type="button" className="orc-pote-btn" aria-haspopup="dialog">{p.nome}</button></span>
                  </th>
                  <td className="num">{mostra(p.pct)}%</td>
                  <td><span className="orc-uso"><Barra p={p} passou={passou} /><span className="num faint">{pctTxt(p.usado_pct)}</span></span></td>
                  <td className="num">{moneyText(p.teto)}</td>
                  <td className="num">{moneyText(p.gasto)}{p.guardado && <small className="faint">guardado</small>}</td>
                  <td className="num"><Restante p={p} passou={passou} /></td>
                  <td aria-hidden="true"><i className="ph ph-caret-right orc-car" /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </div>
        <ul className="orc-cards">
          {potes.map((p) => {
            const passou = ehNegativo(p.restante) && !p.guardado;
            return (
              <li key={p.chave} onClick={(e) => abrir(e, p.chave)}>
                <Icone p={p} />
                <span className="orc-card-meio">
                  <span className="orc-card-topo"><button type="button" className="orc-pote-btn" aria-haspopup="dialog">{p.nome}</button> <span className="num faint">{mostra(p.pct)}%</span></span>
                  <Barra p={p} passou={passou} />
                </span>
                <span className="orc-card-val">
                  <small className="faint">{p.guardado ? "Guardado" : "Gasto"}</small>
                  <b className="num">{moneyText(p.gasto)}</b>
                  <small className="faint num">de {moneyText(p.teto)}</small>
                  {passou && <small className="warn"><i className="ph ph-warning" aria-hidden="true" /> passou {moneyText(p.restante.slice(1))}</small>}
                  {p.guardado && ehNegativo(p.restante) && <small>guardou {moneyText(p.restante.slice(1))} a mais</small>}
                </span>
                <i className="ph ph-caret-right orc-car" aria-hidden="true" />
              </li>
            );
          })}
        </ul>
      </Frame>
    </div>
  );
}
