import { createContext, useContext, useMemo, type ReactNode } from "react";
import { useColorScheme } from "react-native";

import { claro, escuro, type Paleta } from "@/ui/tokens";

type Esquema = "light" | "dark";

interface ContextoTema {
  esquema: Esquema;
  cores: Paleta;
}

const Contexto = createContext<ContextoTema | null>(null);

/**
 * `esquema` forçado é para telas que precisam de um tema fixo (catálogo do
 * `/_ds`, por exemplo); sem ele, segue `useColorScheme()` — o app não repete o
 * site, que força escuro por localStorage e ignora o aparelho.
 */
export function TemaProvider(props: { esquema?: Esquema; children: ReactNode }) {
  const doSistema = useColorScheme();
  const esquema: Esquema = props.esquema ?? (doSistema === "dark" ? "dark" : "light");
  const valor = useMemo<ContextoTema>(
    () => ({ esquema, cores: esquema === "dark" ? escuro : claro }),
    [esquema],
  );
  return <Contexto.Provider value={valor}>{props.children}</Contexto.Provider>;
}

export function useTema(): ContextoTema {
  const contexto = useContext(Contexto);
  if (!contexto) throw new Error("useTema() precisa de um <TemaProvider> por cima na árvore.");
  return contexto;
}
