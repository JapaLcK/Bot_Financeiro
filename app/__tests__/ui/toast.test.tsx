import { act, fireEvent, render } from "@testing-library/react-native";
import { useEffect, type ReactElement } from "react";
import { AccessibilityInfo, Animated, Pressable, StyleSheet, Text } from "react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { ToastProvider, useToast } from "@/ui/componentes/Toast";
import { duracoes } from "@/ui/motion";
import { TemaProvider } from "@/ui/tema";
import { escuro } from "@/ui/tokens";

import IconeStub from "./__mocks__/iconeStub";
import { renderComAreaSegura } from "./_render";

declare const global: typeof globalThis & { __definirReduzirMovimento: (v: boolean) => void };

/**
 * Dois gatilhos independentes (precisa dos dois para testar "o segundo
 * substitui o primeiro"). Rótulo do botão ≠ mensagem do toast de propósito
 * ("abrir A"/"abrir B" vs. "A"/"B") — os dois textos apareceriam juntos na
 * árvore e um `getByText`/`queryByText` na mensagem colidiria com o rótulo.
 */
/** Dispara dois `mostrar()` no MESMO evento: o React agrupa os dois updaters. */
function GatilhoDuplo() {
  const { mostrar } = useToast();
  return (
    <Pressable
      accessibilityRole="button"
      onPress={() => {
        mostrar({ mensagem: "A", tom: "sucesso" });
        mostrar({ mensagem: "B", tom: "erro" });
      }}
    >
      <Text>abrir dois</Text>
    </Pressable>
  );
}

function DoisGatilhos() {
  const { mostrar } = useToast();
  return (
    <>
      <Pressable accessibilityRole="button" onPress={() => mostrar({ mensagem: "A", tom: "sucesso" })}>
        <Text>abrir A</Text>
      </Pressable>
      <Pressable accessibilityRole="button" onPress={() => mostrar({ mensagem: "B", tom: "erro" })}>
        <Text>abrir B</Text>
      </Pressable>
    </>
  );
}

/**
 * `ToastVisual` lê `useSafeAreaInsets()` (mesmo motivo do `Screen`) — sem
 * `SafeAreaProvider` aqui, lança. Não reaproveita `renderComAreaSegura` (que
 * já tem `SafeAreaProvider`+dois temas) nem `renderInterativo` (que já é
 * light-only): o primeiro monta DUAS árvores, e `fireEvent` só alcança a
 * última (mesma armadilha de `renderNosDoisTemas`, ver `avatar.test.tsx`);
 * o segundo não tem `SafeAreaProvider`. Nenhum dos dois cobre "uma árvore,
 * interativa, com área segura" — extrair um terceiro helper para o único
 * arquivo que precisa dessa combinação seria abstração de uso único.
 */
function montar(gatilhos: ReactElement = <DoisGatilhos />) {
  return render(
    <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 844 }, insets: { top: 47, bottom: 34, left: 0, right: 0 } }}>
      <TemaProvider esquema="light">
        <ToastProvider>{gatilhos}</ToastProvider>
      </TemaProvider>
    </SafeAreaProvider>,
  );
}

/**
 * Chamadas pendentes de `Animated.timing(...).start(callback)`. O callback só
 * dispara quando `resolverAnimacoes()` é chamado explicitamente — é o que
 * permite ao teste visitar o estado "saída disparada mas ainda não concluída"
 * (o `mostrar()` durante a saída).
 *
 * `stop()` é FIEL ao RN real, não um recorte de conveniência: no RN,
 * `Animation.stop()` chama `__notifyAnimationEnd({finished: false})` de forma
 * SÍNCRONA, na hora — não enfileira nada, não espera `resolverAnimacoes()`
 * (`node_modules/react-native/Libraries/Animated/animations/TimingAnimation.js:167-174`
 * chama `super.stop()`; `.../Animation.js:184-190` invoca o callback ali
 * mesmo). Um dublê que só removesse a chamada da fila sem invocar o callback
 * tornaria invisível para o teste qualquer bug de reentrância disparado pelo
 * PRÓPRIO `.stop()` — que é exatamente a classe de bug do Bloqueio 1.
 */
let pendentes: { callback?: Animated.EndCallback }[] = [];

function resolverAnimacoes() {
  const lista = pendentes;
  pendentes = [];
  lista.forEach((chamada) => chamada.callback?.({ finished: true }));
}

/**
 * Mostra no `useEffect`, sem depender de `fireEvent`: os testes de tema e de
 * snapshot só precisam do toast já visível, e `renderComAreaSegura` monta
 * DUAS árvores (claro e escuro) — `fireEvent` só alcança a última.
 */
function MostraAoMontar() {
  const { mostrar } = useToast();
  useEffect(() => {
    mostrar({ mensagem: "Categoria salva", tom: "sucesso" });
  }, [mostrar]);
  return null;
}

describe("Toast", () => {
  beforeEach(() => {
    jest.useFakeTimers();
    pendentes = [];
    (AccessibilityInfo.announceForAccessibility as jest.Mock).mockClear();
    jest.spyOn(Animated, "timing").mockImplementation(() => {
      const chamada: { callback?: Animated.EndCallback } = {};
      pendentes.push(chamada);
      return {
        start: (callback?: Animated.EndCallback) => {
          chamada.callback = callback;
        },
        stop: () => {
          // Ordem igual à do RN: sai da fila e SÓ DEPOIS invoca o callback
          // (senão `resolverAnimacoes()` a disparava de novo — o RN nunca
          // chama o mesmo `onEnd` duas vezes, ver `Animation.js:184-190`).
          pendentes = pendentes.filter((p) => p !== chamada);
          chamada.callback?.({ finished: false });
        },
      } as unknown as Animated.CompositeAnimation;
    });
  });

  afterEach(async () => {
    await act(async () => jest.runOnlyPendingTimers());
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  // `await act(async () => {})` logo após `montar()` em todo teste: o
  // `ToastProvider` (via `useReduzirMovimento`) lê `isReduceMotionEnabled()`
  // de forma assíncrona (efeito + Promise) — sem o tick, o `setState` daquela
  // promise resolve DEPOIS do corpo do teste, fora de qualquer `act()`.

  it("mostra a mensagem ao chamar mostrar() e some sozinho depois de 3s", async () => {
    const { getByText, queryByText } = montar();
    await act(async () => {});
    expect(queryByText("A")).toBeNull();

    fireEvent.press(getByText("abrir A"));
    expect(getByText("A")).toBeTruthy();

    await act(async () => {
      jest.advanceTimersByTime(3000);
      resolverAnimacoes();
    });
    expect(queryByText("A")).toBeNull();
  });

  it("o segundo mostrar() substitui o primeiro e reinicia os 3s", async () => {
    const { getByText, queryByText } = montar();
    await act(async () => {});

    fireEvent.press(getByText("abrir A"));
    expect(getByText("A")).toBeTruthy();

    await act(async () => jest.advanceTimersByTime(2000));
    fireEvent.press(getByText("abrir B"));
    expect(queryByText("A")).toBeNull();
    expect(getByText("B")).toBeTruthy();

    // 2000ms depois do B: ainda visível, porque o cronômetro reiniciou no B
    // (sem a substituição, o timer do A já teria disparado em 3000ms).
    await act(async () => jest.advanceTimersByTime(2000));
    expect(getByText("B")).toBeTruthy();

    await act(async () => {
      jest.advanceTimersByTime(1000);
      resolverAnimacoes();
    });
    expect(queryByText("B")).toBeNull();
  });

  it("mostrar() durante a saída mantém o toast NOVO na tela (não deixa a saída velha apagá-lo)", async () => {
    const { getByText, queryByText } = montar();
    await act(async () => {});

    fireEvent.press(getByText("abrir A"));
    // Dispara a saída de A (agenda a animação, callback ainda PENDENTE).
    await act(async () => jest.advanceTimersByTime(3000));

    // mostrar() chega nos 150ms da saída — antes do fix, isto não impedia o
    // callback velho de zerar o conteúdo depois.
    fireEvent.press(getByText("abrir B"));
    expect(getByText("B")).toBeTruthy();
    expect(queryByText("A")).toBeNull();

    // Resolve a saída VELHA (o callback que já estava pendente antes do B):
    // sem o guard `saida.current === animacao`, ela zerava o conteúdo novo
    // incondicionalmente.
    await act(async () => resolverAnimacoes());
    expect(getByText("B")).toBeTruthy();
  });

  it("mostrar() durante a saída reanima a entrada (novo timing para toValue 1)", async () => {
    const { getByText } = montar();
    await act(async () => {});

    fireEvent.press(getByText("abrir A"));
    await act(async () => jest.advanceTimersByTime(3000));

    (Animated.timing as jest.Mock).mockClear();
    fireEvent.press(getByText("abrir B"));

    expect(Animated.timing).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ toValue: 1, duration: duracoes.transicao }),
    );
  });

  /**
   * Controle da regressão do `.start()` dentro do updater de `setConteudo`:
   * naquela versão, `Animated.timing(...).start()` rodava DURANTE o render
   * que cria `conteudo`, antes do commit — `queryByTestId("toast")` ainda
   * devolvia `null` nesse instante, porque a árvore commitada do
   * `react-test-renderer` só reflete o `conteudo` novo depois do commit. Este
   * teste falha (`false`) com esse bug de volta e passa (`true`) com o
   * `useEffect` — sem tocar em `Animated.timing`/`useNativeDriver`, que o
   * dublê já mocka e por isso não vê a corrida real do driver nativo.
   */
  it("dois mostrar() no mesmo evento ainda animam a entrada (updaters agrupados)", async () => {
    const tela = montar(<GatilhoDuplo />);
    await act(async () => {});

    let animouEntrada = false;
    (Animated.timing as jest.Mock).mockImplementation((_valor: unknown, config: { toValue: number }) => {
      if (config.toValue === 1) animouEntrada = true;
      const chamada: { callback?: Animated.EndCallback } = {};
      pendentes.push(chamada);
      return {
        start: (callback?: Animated.EndCallback) => {
          chamada.callback = callback;
        },
        stop: () => {
          pendentes = pendentes.filter((p) => p !== chamada);
          chamada.callback?.({ finished: false });
        },
      } as unknown as Animated.CompositeAnimation;
    });

    fireEvent.press(tela.getByText("abrir dois"));

    // Sem isto, o segundo updater apagava a marca do primeiro e o toast ficava
    // com opacidade 0 até o tempo acabar — visível para ninguém.
    expect(animouEntrada).toBe(true);
    expect(tela.getByText("B")).toBeTruthy();
  });

  it("a animação de entrada só começa depois que o toast já está montado (nunca durante o próprio render)", async () => {
    const { getByText, queryByTestId } = montar();
    await act(async () => {});

    let montadoAoIniciarEntrada: boolean | null = null;
    (Animated.timing as jest.Mock).mockImplementation((_valor: unknown, config: { toValue: number }) => {
      if (config.toValue === 1 && montadoAoIniciarEntrada === null) {
        montadoAoIniciarEntrada = queryByTestId("toast") !== null;
      }
      const chamada: { callback?: Animated.EndCallback } = {};
      pendentes.push(chamada);
      return {
        start: (callback?: Animated.EndCallback) => {
          chamada.callback = callback;
        },
        stop: () => {
          pendentes = pendentes.filter((p) => p !== chamada);
          chamada.callback?.({ finished: false });
        },
      } as unknown as Animated.CompositeAnimation;
    });

    fireEvent.press(getByText("abrir A"));

    expect(montadoAoIniciarEntrada).toBe(true);
  });

  it("anuncia cada mensagem por acessibilidade (a substituição não fica muda)", async () => {
    const anunciar = AccessibilityInfo.announceForAccessibility as jest.Mock;
    const { getByText } = montar();
    await act(async () => {});

    fireEvent.press(getByText("abrir A"));
    expect(anunciar).toHaveBeenCalledWith("A");

    fireEvent.press(getByText("abrir B"));
    expect(anunciar).toHaveBeenCalledWith("B");
    expect(anunciar).toHaveBeenCalledTimes(2);
  });

  it("mensagem em branco não mostra toast nem anuncia", async () => {
    const anunciar = AccessibilityInfo.announceForAccessibility as jest.Mock;
    function GatilhoVazio() {
      const { mostrar } = useToast();
      return (
        <Pressable accessibilityRole="button" onPress={() => mostrar({ mensagem: "   " })}>
          <Text>abrir vazio</Text>
        </Pressable>
      );
    }
    const { getByText, queryByTestId } = render(
      <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 844 }, insets: { top: 47, bottom: 34, left: 0, right: 0 } }}>
        <TemaProvider esquema="light">
          <ToastProvider>
            <GatilhoVazio />
          </ToastProvider>
        </TemaProvider>
      </SafeAreaProvider>,
    );
    await act(async () => {});

    fireEvent.press(getByText("abrir vazio"));
    expect(queryByTestId("toast")).toBeNull();
    expect(anunciar).not.toHaveBeenCalled();
  });

  it("entra e sai pelo mesmo eixo (translateY), saída (150) mais rápida que entrada (250)", async () => {
    const { getByText } = montar();
    await act(async () => {});
    fireEvent.press(getByText("abrir A"));
    expect(Animated.timing).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ toValue: 1, duration: duracoes.transicao }));

    await act(async () => jest.advanceTimersByTime(3000));
    expect(Animated.timing).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ toValue: 0, duration: duracoes.feedback }));
  });

  it("desmontar limpa o cronômetro pendente (não chama setState depois de desmontado)", async () => {
    const limparCronometro = jest.spyOn(global, "clearTimeout");
    const { getByText, unmount } = montar();
    await act(async () => {});
    fireEvent.press(getByText("abrir A"));

    const chamadasAntes = limparCronometro.mock.calls.length;
    unmount();
    expect(limparCronometro.mock.calls.length).toBeGreaterThan(chamadasAntes);
  });

  it("reduzir movimento: sem transform, só opacity", async () => {
    global.__definirReduzirMovimento(true);
    const { getByText, getByTestId } = montar();
    await act(async () => {});
    fireEvent.press(getByText("abrir A"));
    const estilo = StyleSheet.flatten(getByTestId("toast").props.style);
    expect(estilo).not.toHaveProperty("transform");
    expect(estilo).toHaveProperty("opacity");
    global.__definirReduzirMovimento(false);
  });

  it("lê o tema: cor do texto e do ícone mudam no escuro", async () => {
    const { escuro: e } = renderComAreaSegura(
      <ToastProvider>
        <MostraAoMontar />
      </ToastProvider>,
    );
    await act(async () => {});

    const estiloTexto = StyleSheet.flatten(e.getByText("Categoria salva").props.style);
    expect(estiloTexto.color).toBe(escuro.ink);
    expect(e.UNSAFE_getByType(IconeStub).props.color).toBe(escuro.positive);
  });

  it("snapshot (dois temas)", async () => {
    const { claro: c, escuro: e } = renderComAreaSegura(
      <ToastProvider>
        <MostraAoMontar />
      </ToastProvider>,
    );
    await act(async () => {});

    expect(c.toJSON()).toMatchSnapshot("claro");
    expect(e.toJSON()).toMatchSnapshot("escuro");
  });
});
