import { render } from "@testing-library/react-native";
import type { ReactElement } from "react";

import { TemaProvider } from "@/ui/tema";

/**
 * Renderiza o MESMO elemento nos dois temas. É o mínimo que prova que um
 * componente lê `useTema()` em vez de hard-codificar a cor de um esquema só
 * — o defeito mais barato de reproduzir aqui e mais caro de achar no aparelho.
 *
 * Não é `.test.tsx`: é helper, não suíte — `jest.config.js` só roda
 * `*.test.ts?(x)`.
 */
export function renderNosDoisTemas(el: ReactElement) {
  return {
    claro: render(<TemaProvider esquema="light">{el}</TemaProvider>),
    escuro: render(<TemaProvider esquema="dark">{el}</TemaProvider>),
  };
}
