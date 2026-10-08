import { render } from "@testing-library/react-native";
import { RefreshControl, ScrollView, StyleSheet, Text } from "react-native";
import { SafeAreaProvider, type Metrics } from "react-native-safe-area-context";

import { Screen } from "@/ui/componentes/Screen";
import { TemaProvider } from "@/ui/tema";
import { claro, escuro, espaco } from "@/ui/tokens";

import { METRICAS_DE_TESTE, renderComAreaSegura } from "./_render";

describe("Screen", () => {
  it("rola por padrão: usa ScrollView, sem RefreshControl sem onAtualizar", () => {
    const { claro: c } = renderComAreaSegura(
      <Screen>
        <Text>conteúdo</Text>
      </Screen>,
    );
    const scroll = c.getByTestId("tela");
    expect(scroll.props.refreshControl).toBeUndefined();
  });

  it("rolar=false não usa ScrollView", () => {
    const { claro: c } = renderComAreaSegura(
      <Screen rolar={false}>
        <Text>conteúdo</Text>
      </Screen>,
    );
    expect(c.UNSAFE_queryAllByType(ScrollView)).toHaveLength(0);
    expect(c.getByTestId("tela")).toBeTruthy();
  });

  it("com onAtualizar, o ScrollView ganha RefreshControl com o estado de atualizando", () => {
    const onAtualizar = jest.fn();
    const { claro: c } = renderComAreaSegura(
      <Screen onAtualizar={onAtualizar} atualizando>
        <Text>conteúdo</Text>
      </Screen>,
    );
    const refresh = c.UNSAFE_getByType(RefreshControl);
    expect(refresh.props.refreshing).toBe(true);
    expect(refresh.props.onRefresh).toBe(onAtualizar);
    // Sem o deslocamento, o indicador nasce sob a barra de status: o padding
    // de área segura está no conteúdo, não no ScrollView.
    expect(refresh.props.progressViewOffset).toBe(METRICAS_DE_TESTE.insets.top);
    // As duas cores: `tintColor` só vale no iOS, `colors` só no Android.
    expect(refresh.props.tintColor).toBe(claro.brand);
    expect(refresh.props.colors).toEqual([claro.brand]);
  });

  // Entrar e Criar conta vivem sob o cabeçalho nativo (só a seta), que já
  // ocupa a área segura de cima: somar `insets.top` de novo dobrava a margem.
  it("sobCabecalho zera o topo (padding e progressViewOffset) nos dois ramos; sem ele, soma", () => {
    const topoDe = (el: React.ReactElement) => {
      const { claro: c } = renderComAreaSegura(el);
      const tela = c.getByTestId("tela");
      return StyleSheet.flatten([tela.props.style, tela.props.contentContainerStyle]).paddingTop;
    };
    const t = METRICAS_DE_TESTE.insets.top;
    expect(topoDe(<Screen><Text>x</Text></Screen>)).toBe(t);
    expect(topoDe(<Screen rolar={false}><Text>x</Text></Screen>)).toBe(t);
    expect(topoDe(<Screen sobCabecalho><Text>x</Text></Screen>)).toBe(0);
    expect(topoDe(<Screen sobCabecalho rolar={false}><Text>x</Text></Screen>)).toBe(0);

    const { claro: c } = renderComAreaSegura(
      <Screen sobCabecalho onAtualizar={jest.fn()}>
        <Text>x</Text>
      </Screen>,
    );
    expect(c.UNSAFE_getByType(RefreshControl).props.progressViewOffset).toBe(0);
  });

  // Conteúdo rolado não pode aparecer sob o relógio: faixa fixa do fundo do
  // tema na altura da área segura, só onde o Screen é dono do topo.
  it("faixa de fundo atrás da barra de status só no ramo que rola e é dono do topo", () => {
    const { claro: c, escuro: e } = renderComAreaSegura(
      <Screen>
        <Text>x</Text>
      </Screen>,
    );
    expect(StyleSheet.flatten(c.getByTestId("tela-topo").props.style)).toMatchObject({
      position: "absolute",
      top: 0,
      height: METRICAS_DE_TESTE.insets.top,
      backgroundColor: claro.bg,
    });
    expect(StyleSheet.flatten(e.getByTestId("tela-topo").props.style).backgroundColor).toBe(escuro.bg);
    expect(c.getByTestId("tela-topo").props.pointerEvents).toBe("none");
    const semFaixa = (el: React.ReactElement) => renderComAreaSegura(el).claro.queryByTestId("tela-topo");
    expect(semFaixa(<Screen sobCabecalho><Text>x</Text></Screen>)).toBeNull();
    expect(semFaixa(<Screen rolar={false}><Text>x</Text></Screen>)).toBeNull();
  });

  it("fundo muda de cor entre os dois temas", () => {
    const { claro: c, escuro: e } = renderComAreaSegura(
      <Screen>
        <Text>x</Text>
      </Screen>,
    );
    expect(c.getByTestId("tela").props.style.backgroundColor).toBe(claro.bg);
    expect(e.getByTestId("tela").props.style.backgroundColor).toBe(escuro.bg);
  });

  // `METRICAS_DE_TESTE` de `_render.tsx` tem left/right = 0 (iPhone em
  // retrato) — não serve para provar que os insets HORIZONTAIS entram no
  // padding. Paisagem com notch lateral (ou uma Dynamic Island em landscape)
  // tem os dois diferentes de zero, por isso as métricas locais aqui.
  it("soma insets.left/insets.right ao padding horizontal (não só top/bottom)", () => {
    const metricas: Metrics = {
      frame: { x: 0, y: 0, width: 812, height: 375 },
      insets: { top: 0, bottom: 21, left: 44, right: 44 },
    };
    const { getByTestId } = render(
      <SafeAreaProvider initialMetrics={metricas}>
        <TemaProvider esquema="light">
          <Screen rolar={false}>
            <Text>conteúdo</Text>
          </Screen>
        </TemaProvider>
      </SafeAreaProvider>,
    );
    const estilo = StyleSheet.flatten(getByTestId("tela").props.style);
    expect(estilo.paddingLeft).toBe(espaco.lg + 44);
    expect(estilo.paddingRight).toBe(espaco.lg + 44);
  });

  it("ScrollView tem keyboardShouldPersistTaps='handled' (1º toque com teclado aberto, só provável no aparelho)", () => {
    const { claro: c } = renderComAreaSegura(
      <Screen>
        <Text>conteúdo</Text>
      </Screen>,
    );
    expect(c.getByTestId("tela").props.keyboardShouldPersistTaps).toBe("handled");
  });

  it("contentContainerStyle tem flexGrow: 1 (sem isto o EmptyState do C2 não centraliza em tela curta)", () => {
    const { claro: c } = renderComAreaSegura(
      <Screen>
        <Text>conteúdo</Text>
      </Screen>,
    );
    expect(c.getByTestId("tela").props.contentContainerStyle.flexGrow).toBe(1);
  });

  it("rolar=false também aplica o fundo do tema", () => {
    const { claro: c } = renderComAreaSegura(
      <Screen rolar={false}>
        <Text>x</Text>
      </Screen>,
    );
    const estilo = ([] as unknown[]).concat(c.getByTestId("tela").props.style);
    expect(estilo.some((s) => (s as { backgroundColor?: string })?.backgroundColor === claro.bg)).toBe(true);
  });

  // Sem `onAtualizar`: com `RefreshControl` montado, o `toJSON()` do
  // react-test-renderer NÃO é referência circular — é o `_owner` (o
  // FiberNode que criou o elemento) sendo serializado junto, e o diff do
  // Jest explode em cima disso (medido: 5,2 milhões de linhas antes de
  // desistir). CLAUDE.md §6, limite de ambiente, não bug deste componente.
  // O prop wiring do `RefreshControl` já está coberto sem snapshot acima.
  it("snapshot (dois temas)", () => {
    const { claro: c } = renderComAreaSegura(
      <Screen>
        <Text>conteúdo</Text>
      </Screen>,
    );
    const { escuro: e } = renderComAreaSegura(
      <Screen rolar={false}>
        <Text>conteúdo</Text>
      </Screen>,
    );
    expect(c.toJSON()).toMatchSnapshot("claro, rolando");
    expect(e.toJSON()).toMatchSnapshot("escuro, sem rolar");
  });
});
