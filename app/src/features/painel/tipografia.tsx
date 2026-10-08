import { createContext, useContext, useMemo, type ComponentProps, type ReactNode } from "react";
import { useWindowDimensions } from "react-native";
import { Texto } from "@/ui/componentes/Texto";
import { Button } from "@/ui/componentes/Button";

type Tipografia = { fontScale: number; ampliado: boolean };
const Contexto = createContext<Tipografia>({ fontScale: 1, ampliado: false });

/** O evento Dimensions acompanha Dynamic Type também com abas já montadas. */
export function TipografiaPainelProvider({ children }: { children: ReactNode }) {
  const { fontScale } = useWindowDimensions();
  const valor = useMemo(() => ({ fontScale, ampliado: fontScale > 1.1 }), [fontScale]);
  return <Contexto.Provider value={valor}>{children}</Contexto.Provider>;
}
export const useTipografiaPainel = () => useContext(Contexto);

/** Invalida somente a folha nativa medida. Nunca remonta tela, formulário ou dados. */
export function TextoPainel(props: ComponentProps<typeof Texto>) {
  const { fontScale } = useTipografiaPainel();
  return <Texto key={fontScale} {...props} />;
}
/** Button só contém apresentação/feedback; o estado do formulário permanece no pai. */
export function ButtonPainel(props: ComponentProps<typeof Button>) {
  const { fontScale } = useTipografiaPainel();
  return <Button key={fontScale} {...props} />;
}
