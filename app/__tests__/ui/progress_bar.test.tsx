import { View } from "react-native";

import { ProgressBar } from "@/ui/componentes/ProgressBar";
import { claro, escuro } from "@/ui/tokens";

import { renderNosDoisTemas } from "./_render";

function escalaX(resultado: ReturnType<typeof renderNosDoisTemas>["claro"]) {
  const preenchimento = resultado.UNSAFE_getAllByType(View)[1]!;
  const transform = preenchimento.props.style.transform as { scaleX: number }[];
  return transform[0]!.scaleX;
}

describe("ProgressBar", () => {
  it("0.5 mantém 0.5", () => {
    const { claro: c } = renderNosDoisTemas(<ProgressBar valor={0.5} />);
    expect(escalaX(c)).toBe(0.5);
  });

  it("clampa 1.5 para 1", () => {
    const { claro: c } = renderNosDoisTemas(<ProgressBar valor={1.5} />);
    expect(escalaX(c)).toBe(1);
  });

  it("clampa -1 para 0", () => {
    const { claro: c } = renderNosDoisTemas(<ProgressBar valor={-1} />);
    expect(escalaX(c)).toBe(0);
  });

  it("NaN vira 0", () => {
    const { claro: c } = renderNosDoisTemas(<ProgressBar valor={NaN} />);
    expect(escalaX(c)).toBe(0);
  });

  it("Infinity vira 1 (barra cheia, nunca vazia — a guarda antiga zerava aqui)", () => {
    const { claro: c } = renderNosDoisTemas(<ProgressBar valor={Infinity} />);
    expect(escalaX(c)).toBe(1);
  });

  it("-Infinity vira 0", () => {
    const { claro: c } = renderNosDoisTemas(<ProgressBar valor={-Infinity} />);
    expect(escalaX(c)).toBe(0);
  });

  it("accessibilityValue expõe now em 0..100 arredondado (o tipo da RN quer inteiro), não 0..1", () => {
    const { claro: c } = renderNosDoisTemas(<ProgressBar valor={1.5} />);
    expect(c.UNSAFE_getAllByType(View)[0]!.props.accessibilityValue).toEqual({ min: 0, max: 100, now: 100 });
  });

  it("valor fracionário mínimo (0.005) não deixa now decimal", () => {
    const { claro: c } = renderNosDoisTemas(<ProgressBar valor={0.005} />);
    expect(c.UNSAFE_getAllByType(View)[0]!.props.accessibilityValue).toEqual({ min: 0, max: 100, now: 1 });
  });

  it("trilha é `surface` (nunca `border` — brand sobre border reprova 3:1)", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(<ProgressBar valor={0.5} />);
    expect(c.UNSAFE_getAllByType(View)[0]!.props.style.backgroundColor).toBe(claro.surface);
    expect(e.UNSAFE_getAllByType(View)[0]!.props.style.backgroundColor).toBe(escuro.surface);
  });

  it("tom customizado (positive/warning/danger) pinta o preenchimento", () => {
    const { claro: c } = renderNosDoisTemas(<ProgressBar valor={0.5} tom="warning" />);
    expect(c.UNSAFE_getAllByType(View)[1]!.props.style.backgroundColor).toBe(claro.warning);
  });

  it("snapshot (dois temas)", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(<ProgressBar valor={0.7} tom="positive" />);
    expect(c.toJSON()).toMatchSnapshot("claro");
    expect(e.toJSON()).toMatchSnapshot("escuro");
  });
});
