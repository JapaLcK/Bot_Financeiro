import { Keyboard, Platform, ScrollView, Text, TextInput } from "react-native";

import { OPCOES_SHEET, SheetConteudo } from "@/ui/componentes/Sheet";
import { espaco, raio } from "@/ui/tokens";

import { renderComAreaSegura } from "./_render";

describe("OPCOES_SHEET", () => {
  // `toMatchObject`, não `toEqual`: `OPCOES_SHEET` agora é tipado como
  // `NativeStackNavigationOptions` (`tsc` é o gate contra chave inventada,
  // `TS2561` — ver Sheet.tsx), então este teste só precisa afirmar os
  // VALORES de comportamento, não repetir o literal inteiro como se fosse
  // ele quem garantisse a forma do objeto.
  it("presentation formSheet, detents [0.5, 1], raio do token, sem alça do sistema", () => {
    expect(OPCOES_SHEET).toMatchObject({
      presentation: "formSheet",
      sheetAllowedDetents: [0.5, 1],
      sheetCornerRadius: raio.lg,
      sheetGrabberVisible: false,
    });
  });
});

describe("SheetConteudo", () => {
  it("renderiza os filhos", () => {
    const { claro: c } = renderComAreaSegura(
      <SheetConteudo>
        <Text>conteúdo da sheet</Text>
      </SheetConteudo>,
    );
    expect(c.getByText("conteúdo da sheet")).toBeTruthy();
  });

  it("a alça é decorativa: escondida do leitor de tela", () => {
    const { claro: c } = renderComAreaSegura(
      <SheetConteudo>
        <Text>x</Text>
      </SheetConteudo>,
    );
    const alca = c.getByTestId("sheet-conteudo").props.children[0];
    expect(alca.props.accessibilityElementsHidden).toBe(true);
    expect(alca.props.importantForAccessibility).toBe("no-hide-descendants");
  });

  // O Jest não mede layout nativo; isto trava só o contrato de estrutura. O
  // `formSheet` do iOS impõe o frame da tela ao 1º ScrollView descendente
  // (ver Sheet.tsx): ele tem de ser a RAIZ, com o respiro no content container.
  it("rolar: o ScrollView é a raiz e o respiro vai no contentContainerStyle", () => {
    const { claro: c } = renderComAreaSegura(
      <SheetConteudo rolar>
        <Text>x</Text>
      </SheetConteudo>,
    );
    const raiz = c.UNSAFE_getByType(ScrollView);
    expect(raiz.props.testID).toBe("sheet-conteudo");
    expect(raiz.props.keyboardShouldPersistTaps).toBe("handled");
    // Sem isto o teclado cobre o campo do código na sheet do MFA (iPhone real).
    expect(raiz.props.automaticallyAdjustKeyboardInsets).toBe(true);
    expect(raiz.props.contentContainerStyle).toMatchObject({ paddingHorizontal: espaco.lg });
    expect(raiz.props.children[0].props.accessibilityElementsHidden).toBe(true);
  });

  // O Jest não tem teclado nem layout: isto trava só o contrato. No
  // `keyboardDidShow`, a casca rola até o rótulo do campo focado (y medido no
  // conteúdo menos `xxxl`); o corte no fim do conteúdo é do `scrollTo` nativo.
  it("rolar: no keyboardDidShow rola até o campo focado, e o nativo corta no fim", () => {
    const ouvintes: (() => void)[] = [];
    const escutar = jest.spyOn(Keyboard, "addListener").mockImplementation((evento, fn) => {
      if (evento === "keyboardDidShow") ouvintes.push(fn as () => void);
      return { remove: jest.fn() } as never;
    });
    const medir = jest.fn((_rel, ok: (x: number, y: number) => void) => ok(0, 300));
    const foco = jest.spyOn(TextInput.State, "currentlyFocusedInput").mockReturnValue({ measureLayout: medir } as never);
    try {
      const { claro: c } = renderComAreaSegura(
        <SheetConteudo rolar>
          <Text>x</Text>
        </SheetConteudo>,
      );
      const raiz = c.UNSAFE_getByType(ScrollView);
      // O mock do ScrollView não liga o `innerViewRef` a uma View; o nativo liga (ScrollView.js).
      const conteudo = {};
      raiz.props.innerViewRef.current = conteudo;
      ouvintes.forEach((fn) => fn());
      // `===`, não `toHaveBeenCalledWith`: com o alvo errado o 1º argumento é
      // uma instância do renderer, e o pretty-format dela estoura a memória.
      expect(medir).toHaveBeenCalledTimes(1);
      expect(medir.mock.calls[0]?.[0] === conteudo).toBe(true);
      expect(raiz.instance.scrollTo).toHaveBeenCalledWith({ y: 300 - espaco.xxxl });
    } finally {
      escutar.mockRestore();
      foco.mockRestore();
    }
  });

  it("sem rolar: não escuta o teclado (o iOS expande a sheet sozinho)", () => {
    const escutar = jest.spyOn(Keyboard, "addListener");
    try {
      renderComAreaSegura(
        <SheetConteudo>
          <Text>x</Text>
        </SheetConteudo>,
      );
      expect(escutar).not.toHaveBeenCalledWith("keyboardDidShow", expect.anything());
    } finally {
      escutar.mockRestore();
    }
  });

  it("rolar no Android: não escuta o teclado (o `resize` do sistema cuida)", () => {
    const so = jest.replaceProperty(Platform, "OS", "android");
    const escutar = jest.spyOn(Keyboard, "addListener");
    try {
      renderComAreaSegura(
        <SheetConteudo rolar>
          <Text>x</Text>
        </SheetConteudo>,
      );
      expect(escutar).not.toHaveBeenCalledWith("keyboardDidShow", expect.anything());
    } finally {
      escutar.mockRestore();
      so.restore();
    }
  });

  it("snapshot (dois temas)", () => {
    const { claro: c, escuro: e } = renderComAreaSegura(
      <SheetConteudo>
        <Text>conteúdo</Text>
      </SheetConteudo>,
    );
    expect(c.toJSON()).toMatchSnapshot("claro");
    expect(e.toJSON()).toMatchSnapshot("escuro");
  });
});
