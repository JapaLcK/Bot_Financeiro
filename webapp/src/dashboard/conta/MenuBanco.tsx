import { useEffect, useId, useRef, type KeyboardEvent } from "react";

// O menu "…" de um banco (Reconectar, Desconectar). O pai guarda qual está aberto (um por vez) e fecha ao abrir o detalhe/diálogo.
// Padrão ARIA de menu: Enter/Espaço no botão abre e foca o 1º item; setas/Home/End andam; Esc fecha e devolve o foco ao botão; Tab fecha e segue; clique fora fecha.
// As ações recebem o botão "…" (não o item, que sai da tela ao fechar): é nele que o foco volta quando o diálogo/widget fecha.
export function MenuBanco({ nome, aberto, bloqueado, alternar, fechar, onReconectar, onDesconectar }: {
  nome: string; aberto: boolean; bloqueado: boolean; alternar: () => void; fechar: () => void;
  onReconectar?: (botao: HTMLButtonElement) => void; onDesconectar: (botao: HTMLButtonElement) => void;
}) {
  const id = useId();
  const botao = useRef<HTMLButtonElement>(null);
  const raiz = useRef<HTMLSpanElement>(null);
  const itens = () => Array.from(raiz.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]:not(:disabled)") ?? []);
  useEffect(() => {
    if (!aberto) return;
    itens()[0]?.focus();
    const fora = (e: Event) => { if (!raiz.current?.contains(e.target as Node)) fechar(); };
    document.addEventListener("pointerdown", fora);
    return () => document.removeEventListener("pointerdown", fora);
  }, [aberto]);
  const tecla = (e: KeyboardEvent) => {
    if (!aberto) return;
    if (e.key === "Escape") { e.preventDefault(); fechar(); botao.current?.focus(); return; }
    if (e.key === "Tab") { botao.current?.focus(); fechar(); return; } // o item sai da tela: o Tab segue do botão, não do nada
    const it = itens(), k = it.indexOf(document.activeElement as HTMLButtonElement);
    const alvo = e.key === "ArrowDown" ? it[(k + 1) % it.length] : e.key === "ArrowUp" ? it[(k - 1 + it.length) % it.length] : e.key === "Home" ? it[0] : e.key === "End" ? it[it.length - 1] : undefined;
    if (alvo) { e.preventDefault(); alvo.focus(); }
  };
  const agir = (f: (b: HTMLButtonElement) => void) => () => { fechar(); if (botao.current) f(botao.current); };
  return (
    <span className="banco-menu" ref={raiz} onKeyDown={tecla}>
      <button ref={botao} type="button" className="btn btn-ghost banco-acao banco-mais-acoes" aria-haspopup="menu" aria-expanded={aberto} aria-controls={aberto ? id : undefined} aria-label={`Mais ações de ${nome}`} onClick={alternar}>
        <span aria-hidden="true">…</span>
      </button>
      {aberto && (
        <ul id={id} role="menu" className="banco-menu-lista" aria-label={`Ações de ${nome}`}>
          {onReconectar && <li role="none"><button type="button" role="menuitem" disabled={bloqueado} onClick={agir(onReconectar)}><i className="ph ph-arrows-clockwise" aria-hidden="true" />Reconectar</button></li>}
          <li role="none"><button type="button" role="menuitem" className="perigo" disabled={bloqueado} onClick={agir(onDesconectar)}><i className="ph ph-trash" aria-hidden="true" />Desconectar</button></li>
        </ul>
      )}
    </span>
  );
}
