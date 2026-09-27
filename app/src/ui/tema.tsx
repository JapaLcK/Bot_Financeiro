import * as SystemUI from "expo-system-ui";
import { createContext, useContext, useEffect, useMemo, type ReactNode } from "react";
import { useColorScheme } from "react-native";

import { claro, escuro, type Paleta } from "@/ui/tokens";

type Esquema = "light" | "dark";

interface ContextoTema {
  esquema: Esquema;
  cores: Paleta;
}

const Contexto = createContext<ContextoTema | null>(null);

// Cor cosmética: se o nativo recusar, a janela fica branca como antes — a
// rejeição não pode estourar e derrubar nada.
function pintarJanela(cor: string) {
  SystemUI.setBackgroundColorAsync(cor).catch(() => {});
}

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

  // A janela nativa por baixo das telas é branca por padrão e aparece onde
  // nenhuma View cobre: nos cantos arredondados do teclado do iOS, por
  // exemplo. Pinta ela com o `bg` do tema em vigor. Um provider aninhado (o
  // tema forçado do `/_ds`) devolve a cor do de fora ao desmontar.
  // ponytail: sistema trocando de tema COM um provider aninhado montado deixa
  // a janela no tema do de fora (o efeito do pai roda depois do do filho) até
  // o aninhado trocar ou desmontar — só o catálogo de dev aninha hoje.
  const bgPai = useContext(Contexto)?.cores.bg;
  useEffect(() => {
    pintarJanela(valor.cores.bg);
    return () => {
      if (bgPai) pintarJanela(bgPai);
    };
  }, [valor.cores.bg, bgPai]);

  return <Contexto.Provider value={valor}>{props.children}</Contexto.Provider>;
}

export function useTema(): ContextoTema {
  const contexto = useContext(Contexto);
  if (!contexto) throw new Error("useTema() precisa de um <TemaProvider> por cima na árvore.");
  return contexto;
}
