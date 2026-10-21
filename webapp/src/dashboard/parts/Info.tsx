// O ⓘ do painel: o texto que EXPLICA (método, premissas, motivos) não fica no card, abre aqui.
// Um diálogo só de leitura, no modelo de PoteLancamentos; o conteúdo só existe no DOM com ele aberto.
import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { NATIVE, useDialogo } from "./dialog";

function Aberto({ id, rotulo, fechar, children }: { id: string; rotulo: string; fechar: () => void; children: ReactNode }) {
  const { fecharDialog, fundo, props } = useDialogo(fechar);
  // Evento de portal sobe pela árvore do React: sem isto o Esc também fecharia o card que contém o ⓘ (GastosCompras).
  // No fallback o stopPropagation corta o keydown antes do window, onde o useDialogo escuta o Esc: fecha-se aqui.
  const tecla = (e: KeyboardEvent<HTMLDialogElement>) => {
    props.onKeyDown(e);
    if (e.key === "Escape") { e.stopPropagation(); if (!NATIVE) fecharDialog(); }
  };
  return <>
    <dialog {...props} onKeyDown={tecla} id={id} className={`lanc-form${NATIVE ? "" : " lanc-form-fb"}`} aria-labelledby={`${id}-t`}>
      <div className="info-corpo">
        <header><h2 id={`${id}-t`}>{rotulo}</h2><button className="btn btn-quiet" type="button" onClick={fecharDialog}>Fechar</button></header>
        {children}
      </div>
    </dialog>
    {!NATIVE && <div className="lanc-scrim" aria-hidden="true" onClick={fundo} />}
  </>;
}

// Em portal no body: no fallback (Safari 14) o .lanc-form-fb é position: fixed e a célula do grid do Resumo
// (clip-path / overflow hidden) o cortaria.
export function Info({ rotulo, children }: { rotulo: string; children: ReactNode }) {
  const id = useId();
  const [aberto, setAberto] = useState(false);
  const botao = useRef<HTMLButtonElement>(null);
  const fechar = () => { setAberto(false); botao.current?.focus(); };
  return <>
    <button ref={botao} type="button" className="w-info" aria-label={rotulo} aria-haspopup="dialog" aria-expanded={aberto} aria-controls={aberto ? id : undefined} onClick={() => setAberto(true)}>
      <i className="ph ph-info" aria-hidden="true" />
    </button>
    {aberto && createPortal(<Aberto id={id} rotulo={rotulo} fechar={fechar}>{children}</Aberto>, document.body)}
  </>;
}

// O título de uma página com o ⓘ do subtítulo ao lado (o subtítulo saiu da tela).
export function TituloDaPagina({ titulo, info }: { titulo: ReactNode; info?: ReactNode }) {
  return (
    <div className="page-head-t">
      <h1 id="page-title" tabIndex={-1}>{titulo}</h1>
      {info && <Info rotulo="Sobre esta tela"><p>{info}</p></Info>}
    </div>
  );
}
