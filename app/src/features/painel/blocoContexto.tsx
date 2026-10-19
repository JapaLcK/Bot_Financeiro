import { createContext, useContext } from "react";
import type { RecursosBloco } from "@/api/schemas/painel";
/** Os dados que o servidor embutiu numa aba do card do chat. Dentro dele, os widgets desenham SÓ isto (nunca o provider) e não escrevem nada. A única ação é `pedir`: o card manda uma mensagem NO chat, como se o usuário a tivesse digitado (`desativado` = a entrada do chat está desligada). */
export const BlocoContexto = createContext<{ recursos: RecursosBloco; pedir?: { enviar: (texto: string) => void; desativado: boolean } } | null>(null);
export const useBloco = () => useContext(BlocoContexto);
