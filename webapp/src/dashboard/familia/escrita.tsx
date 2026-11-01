import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ErroApi } from "../lib/v2";
import { href } from "../router";
import { familiaQuery, falhaFamilia, type Falha } from "./api";

// Uma escrita por vez: a trava é uma ref (dois cliques no mesmo tick não passam pelo estado). Falha vira texto; 404/409 (o estado mudou) relê a família.
export function useEscrita() {
  const qc = useQueryClient();
  const voando = useRef(false);
  const [ocupado, setOcupado] = useState(false);
  const [falha, setFalha] = useState<Falha | null>(null);
  const rodar = async (f: () => Promise<unknown>): Promise<boolean> => {
    if (voando.current) return false;
    voando.current = true;
    setOcupado(true);
    setFalha(null);
    try { await f(); return true; }
    catch (e) {
      setFalha(falhaFamilia(e));
      if (e instanceof ErroApi && (e.status === 404 || e.status === 409)) qc.invalidateQueries({ queryKey: familiaQuery.queryKey });
      return false;
    } finally { voando.current = false; setOcupado(false); }
  };
  return { ocupado, falha, rodar, limpar: () => setFalha(null) };
}

export const FalhaFamilia = ({ falha }: { falha: Falha | null }) => falha && (
  <p role="alert" className="warn fam-falha">{falha.texto}{falha.plano && <> <a href={href("/conta/cobranca")}>Ver cobrança</a></>}</p>
);

// O elemento que tinha o foco saiu da tela (botão que virou outra coisa, linha removida, diálogo que acabou de fechar): o foco cairia no body. Vai ao título.
export const focarTitulo = () => {
  const a = document.activeElement;
  if (!a || a === document.body || !a.isConnected || a.closest("dialog:not([open])")) document.getElementById("page-title")?.focus();
};
