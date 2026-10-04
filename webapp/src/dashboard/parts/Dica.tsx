import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { Dica, Guia } from "../lib/api-v2.gen";
import { ICON } from "../lib/brand";
import { apiPost, guiaQuery } from "../lib/v2";
import type { Path } from "../router";

// A dica de primeiro uso de uma tela (#728): o texto e o "já vista" vêm do servidor (`DICAS`
// em api/v2/guia.py, só a da tela que o plano dá). Aparece sozinha uma vez; depois, só pela
// Ajuda ou pelo Cmd-K ("Como funciona esta tela").
export const TELA: Record<Dica["tela"], Path> = { assinaturas: "/assinaturas" };
export const dicaDe = (g: Guia | undefined, path: Path) => g?.dicas.find((d) => TELA[d.tela] === path);
export const abrirDica = () => window.dispatchEvent(new Event("dash:dica"));

// O guia aberto (parts/Guia.tsx avisa): a dica não briga com o balão e espera ele fechar.
let guiaAberto = false;
const subs = new Set<() => void>();
export const avisarGuia = (aberto: boolean) => {
  if (aberto === guiaAberto) return;
  guiaAberto = aberto;
  subs.forEach((f) => f());
};
// Um POST por carga de página, mesmo com refetch (SSE, foco na aba) antes da resposta e com
// sair e voltar à tela: o estado do componente morre com ele, o do módulo não.
const MARCADAS = new Set<string>();

export function DicaDaTela({ path }: { path: Path }) {
  const qc = useQueryClient();
  const d = dicaDe(useQuery(guiaQuery).data, path);
  const aberto = useSyncExternalStore((f) => { subs.add(f); return () => subs.delete(f); }, () => guiaAberto);
  const [mostrar, setMostrar] = useState(false);
  const card = useRef<HTMLElement>(null);
  const titulo = useRef<HTMLHeadingElement>(null);
  const focar = useRef(false);

  // Sozinha, sem mover o foco: quem lê a página segue de onde estava.
  useEffect(() => {
    if (!d || d.vista || aberto || MARCADAS.has(d.id)) return;
    MARCADAS.add(d.id);
    setMostrar(true);
    apiPost("/guia/dica", { dica: d.id }).then((g) => qc.setQueryData(guiaQuery.queryKey, g)).catch(() => {});
  }, [d?.id, d?.vista, aberto]);

  // Pela Ajuda ou pelo Cmd-K: a pessoa pediu, o foco vai para o título.
  useEffect(() => {
    const on = () => { focar.current = true; setMostrar(true); };
    window.addEventListener("dash:dica", on);
    return () => window.removeEventListener("dash:dica", on);
  }, []);
  useEffect(() => {
    if (!focar.current || !titulo.current) return;
    focar.current = false;
    titulo.current.focus({ preventScroll: true });
    titulo.current.scrollIntoView({ block: "nearest" });
  });

  const fechar = () => {
    if (card.current?.contains(document.activeElement)) document.getElementById("page-title")?.focus({ preventScroll: true });
    setMostrar(false);
  };

  if (!d || !mostrar || aberto) return null;
  return (
    <aside ref={card} className="dica-tela" aria-labelledby="dica-titulo">
      <img src={ICON} alt="" width={32} height={32} />
      <h2 ref={titulo} id="dica-titulo" tabIndex={-1}>{d.titulo}</h2>
      <p>{d.texto}</p>
      <button type="button" className="btn btn-ghost" onClick={fechar}>Entendi</button>
    </aside>
  );
}
