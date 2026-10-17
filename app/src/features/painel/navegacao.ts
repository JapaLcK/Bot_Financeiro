import { router, useFocusEffect, usePathname, type Href } from "expo-router";
import { createContext, useCallback, useContext, useRef, useState } from "react";
type Navegacao = { navegar: (destino: Href, metodo?: "push" | "navigate") => void; entrando: boolean };
export const ContextoNavegacaoPainel = createContext<Navegacao | null>(null);
export function useNavegacaoPainel() {
 const n = useContext(ContextoNavegacaoPainel);
 if (!n) throw new Error("Navegação do painel indisponível");
 return n;
}
/** Quem provê o contexto acima (TelaPainel e o chat): uma entrada por vez; destrava ao voltar o foco. `aoSair` roda antes de navegar (ex.: fechar folha). */
export function useNavegarPainel(aoSair?: () => void) {
 const caminho = usePathname();
 const emVoo = useRef(false); const [entrando, setEntrando] = useState(false); const [aviso, setAviso] = useState<string | null>(null);
 useFocusEffect(useCallback(() => { emVoo.current = false; setEntrando(false); }, []));
 const navegar = (destino: Href, metodo: "push" | "navigate" = "push") => {
  if (metodo === "navigate" && destino === caminho) return;
  if (emVoo.current) return;
  emVoo.current = true; setEntrando(true); setAviso(null); aoSair?.();
  try { router[metodo](destino); }
  catch { emVoo.current = false; setEntrando(false); setAviso("Não conseguimos abrir a tela. Tente de novo."); }
 };
 return { navegar, entrando, aviso, emVoo };
}
let entradas = 0;
/** Única porta para `/extrato` com `dia` ou `categoria`. A `entrada` (contador) muda a cada toque, mesmo no mesmo dia: o Extrato zera os filtros quando ela muda (as abas ficam montadas). */
export function irAoExtrato(navegar: ReturnType<typeof useNavegacaoPainel>["navegar"], filtro: { dia: string } | { categoria: string }) {
 navegar({ pathname: "/extrato", params: { ...filtro, entrada: ++entradas } });
}
