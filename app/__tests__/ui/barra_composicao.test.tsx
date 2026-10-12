import { StyleSheet } from "react-native";

import { BarraComposicao } from "@/ui/componentes/BarraComposicao";

import { renderNosDoisTemas } from "./_render";

// A barra é oculta ao VoiceOver de propósito: a query precisa enxergar elementos ocultos.
const H = { includeHiddenElements: true };
const estilo = (el: { props: { style?: unknown } }) => StyleSheet.flatten(el.props.style as never) as Record<string, unknown>;

describe("BarraComposicao", () => {
  it("10pt de altura, vão de 2, segmento de raio 5, mínimo de 4 e largura proporcional ao valor", () => {
    const { escuro } = renderNosDoisTemas(<BarraComposicao segmentos={[{ valor: 300, cor: "#111111" }, { valor: 100, cor: "#222222" }, { valor: 1, cor: "#333333" }]} />);
    expect(estilo(escuro.getByTestId("barra-composicao", H))).toMatchObject({ height: 10, gap: 2, flexDirection: "row" });
    const segs = escuro.getAllByTestId("segmento-composicao", H).map(estilo);
    expect(segs.map((s) => s.flexGrow)).toEqual([300, 100, 1]); // 3:1 entre os dois primeiros
    for (const s of segs) expect(s).toMatchObject({ minWidth: 4, borderRadius: 5, flexBasis: 0 });
    expect(segs.map((s) => s.backgroundColor)).toEqual(["#111111", "#222222", "#333333"]);
  });
  it("é decorativa para o VoiceOver", () => {
    const { escuro } = renderNosDoisTemas(<BarraComposicao segmentos={[{ valor: 1, cor: "#111111" }]} />);
    const barra = escuro.getByTestId("barra-composicao", H);
    expect(barra.props.accessibilityElementsHidden).toBe(true);
    expect(barra.props.importantForAccessibility).toBe("no-hide-descendants");
  });
  it("controle: sem segmentos não renderiza nada", () => {
    const { escuro } = renderNosDoisTemas(<BarraComposicao segmentos={[]} />);
    expect(escuro.queryByTestId("barra-composicao", H)).toBeNull();
  });
});
