import { createContext, useContext } from "react";
import type { Href } from "expo-router";
type Navegacao = { navegar: (destino: Href, metodo?: "push" | "navigate") => void; entrando: boolean };
export const ContextoNavegacaoPainel = createContext<Navegacao | null>(null);
export function useNavegacaoPainel() {
 const n = useContext(ContextoNavegacaoPainel);
 if (!n) throw new Error("Navegação do painel indisponível");
 return n;
}
