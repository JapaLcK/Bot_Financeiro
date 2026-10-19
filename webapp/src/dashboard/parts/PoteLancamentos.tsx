// O modal de um pote do Orçamento doméstico: os lançamentos do mês que caem nele (já vêm do servidor).
import { useId } from "react";
import type { Pote } from "../lib/api-v2.gen";
import { moneyText } from "../lib/format.js";
import { mesAno } from "../lib/metas-textos";
import { NATIVE, useDialogo } from "./dialog";

const dia = (iso: string) => `${iso.slice(8, 10)}/${iso.slice(5, 7)}`;

export function PoteLancamentos({ p, mes, guarda, fechar }: { p: Pote; mes: string; guarda: boolean; fechar: () => void }) {
  const id = useId();
  const { fecharDialog, fundo, props } = useDialogo(fechar);
  return <>
    <dialog {...props} className={`lanc-form orc-form orc-modal${NATIVE ? "" : " lanc-form-fb"}`} aria-labelledby={`${id}-t`}>
      <div className="orc-modal-corpo">
        <header><h2 id={`${id}-t`}>{p.nome} · {mesAno(mes)}</h2><button className="btn btn-quiet" type="button" onClick={fecharDialog} aria-label="Fechar">Fechar</button></header>
        {p.categorias.length > 0 && <p className="faint">Categorias neste pote: {p.categorias.join(", ")}</p>}
        <p>{guarda ? "Guardado" : "Gasto"} <b className="num">{moneyText(p.gasto)}</b> de <b className="num">{moneyText(p.teto)}</b></p>
        {p.lancamentos.length === 0 ? <p className="faint" role="status">Nenhum lançamento neste pote em {mesAno(mes)}.</p> : <>
          <ul className="orc-chips" aria-label="Total por categoria">
            {p.por_categoria.map((c) => <li key={c.categoria}>{c.categoria} <b className="num">{moneyText(c.total)}</b></li>)}
          </ul>
          <ul className="orc-lanc">
            {p.lancamentos.map((l, i) => (
              <li key={i}>
                <span className="orc-lanc-desc">{l.descricao}</span>
                <span className="num">{moneyText(l.valor)}</span>
                <span className="faint orc-lanc-sub">{l.categoria} · {dia(l.data)}{l.origem === "cartao" ? " · Cartão" : ""}</span>
              </li>
            ))}
          </ul>
        </>}
        <footer><button className="btn btn-ghost" type="button" onClick={fecharDialog}>Fechar</button></footer>
      </div>
    </dialog>
    {!NATIVE && <div className="lanc-scrim" aria-hidden="true" onClick={fundo} />}
  </>;
}
