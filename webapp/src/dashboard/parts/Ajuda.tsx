import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { guiaQuery } from "../lib/v2";
import type { Path } from "../router";
import { abrirDica, dicaDe } from "./Dica";
import { abrirGuia } from "./Guia";

// A Ajuda do trilho e da barra de baixo. Na tela que tem dica (parts/Dica.tsx) vira um menu de
// dois itens: o guia do painel e a dica da tela. Nas outras, o botão de sempre: abre o guia.
export function Ajuda({ path, title, children }: { path: Path; title?: string; children: ReactNode }) {
  const dica = !!dicaDe(useQuery(guiaQuery).data, path);
  const [quer, setAberto] = useState(false);
  const aberto = dica && quer;
  const id = useId();
  const botao = useRef<HTMLButtonElement>(null);

  useEffect(() => setAberto(false), [path]);
  // Toque fora fecha; Esc fecha e devolve o foco ao botão (o Esc de um dialog aberto por cima é dele).
  useEffect(() => {
    if (!aberto) return;
    const fora = (e: PointerEvent) => { if (!botao.current?.parentElement?.contains(e.target as Node)) setAberto(false); };
    const esc = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || document.querySelector("dialog[open]")) return;
      setAberto(false);
      botao.current?.focus();
    };
    document.addEventListener("pointerdown", fora);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("pointerdown", fora); document.removeEventListener("keydown", esc); };
  }, [aberto]);

  if (!dica) return <button type="button" aria-label="Ajuda" title={title} onClick={abrirGuia}>{children}</button>;
  const item = (abrir: () => void) => () => { setAberto(false); abrir(); };
  return <>
    <button ref={botao} type="button" aria-label="Ajuda" title={title} aria-expanded={aberto} aria-controls={aberto ? id : undefined} onClick={() => setAberto(!aberto)}>{children}</button>
    {aberto && (
      <ul id={id} className="ajuda-menu">
        <li><button type="button" onClick={item(abrirGuia)}>Guia do painel</button></li>
        <li><button type="button" onClick={item(abrirDica)}>Como funciona esta tela</button></li>
      </ul>
    )}
  </>;
}
