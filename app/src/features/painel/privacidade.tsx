import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

type Privacidade = { oculto: boolean; alternar: () => void; mostrar: () => void };

/**
 * "Ocultar valores" mora acima do Painel, em `app/(app)/_layout.tsx`: a tela de Bancos conectados fica FORA do `PainelProvider`
 * e tem de respeitar a escolha feita lá. Sem provedor (testes que montam só o `PainelProvider`) o valor fica sempre visível e o
 * alternar não faz nada: o padrão seguro para dinheiro é mostrar o que o usuário não escondeu, mas nenhum caminho de produção chega aqui sem o provedor.
 */
const Contexto = createContext<Privacidade>({ oculto: false, alternar: () => undefined, mostrar: () => undefined });

export function PrivacidadeProvider({ children }: { children: ReactNode }) {
  const [oculto, setOculto] = useState(false);
  const alternar = useCallback(() => setOculto((v) => !v), []);
  const mostrar = useCallback(() => setOculto(false), []);
  const valor = useMemo(() => ({ oculto, alternar, mostrar }), [oculto, alternar, mostrar]);
  return <Contexto.Provider value={valor}>{children}</Contexto.Provider>;
}

export const usePrivacidade = () => useContext(Contexto);
