import { render } from "@testing-library/react-native";
import type { ReactElement } from "react";
import { SafeAreaProvider, type Metrics } from "react-native-safe-area-context";

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

/**
 * Métricas fixas de um iPhone com notch, para o único componente do C1 que
 * lê `useSafeAreaInsets()` (`Screen`) — fora de um `SafeAreaProvider` esse
 * hook lança. Não é o padrão dos outros testes (a maioria não usa área
 * segura), por isso não entra em `renderNosDoisTemas`/`renderInterativo`:
 * misturar mudaria o snapshot de TODO componente já existente (Money,
 * AmountInput) por causa de um hook que só o `Screen` chama.
 */
const METRICAS_DE_TESTE: Metrics = {
  frame: { x: 0, y: 0, width: 390, height: 844 },
  insets: { top: 47, bottom: 34, left: 0, right: 0 },
};

export function renderComAreaSegura(el: ReactElement) {
  const comProvedor = (esquema: "light" | "dark") => (
    <SafeAreaProvider initialMetrics={METRICAS_DE_TESTE}>
      <TemaProvider esquema={esquema}>{el}</TemaProvider>
    </SafeAreaProvider>
  );
  return { claro: render(comProvedor("light")), escuro: render(comProvedor("dark")) };
}

/**
 * SÓ para os testes que disparam `fireEvent`: a RNTL marca a última árvore
 * renderizada como "a tela ativa" (`screen.UNSAFE_root`), e `fireEvent`
 * recusa evento em qualquer elemento fora dela — `isElementMounted()` em
 * `component-tree.js`. Renderizar claro+escuro (`renderNosDoisTemas`) deixa
 * o claro "inativo" pra sempre depois que o escuro nasce por cima: o evento
 * vira no-op silencioso. Interação não muda com o tema, então um render só
 * (tema claro, arbitrário) resolve — extraído aqui para não duplicar entre
 * `amount_input.test.tsx` e os testes do C1.
 */
export function renderInterativo(el: ReactElement) {
  return render(<TemaProvider esquema="light">{el}</TemaProvider>);
}
