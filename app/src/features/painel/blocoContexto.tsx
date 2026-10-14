import { createContext, useContext } from "react";
import type { RecursosBloco } from "@/api/schemas/painel";
/** Os dados que o servidor embutiu numa aba do card do chat. Dentro dele, os widgets desenham SÓ isto (nunca o provider) e ficam só leitura. */
export const BlocoContexto = createContext<{ recursos: RecursosBloco } | null>(null);
export const useBloco = () => useContext(BlocoContexto);
