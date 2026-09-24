import { ScrollView, Text } from "react-native";

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
    expect(raiz.props.contentContainerStyle).toMatchObject({ paddingHorizontal: espaco.lg });
    expect(raiz.props.children[0].props.accessibilityElementsHidden).toBe(true);
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
