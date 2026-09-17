import { View } from "react-native";

import { TransactionRow } from "@/ui/componentes/TransactionRow";

import { renderNosDoisTemas } from "./_render";

describe("TransactionRow", () => {
  it("mostra descrição, categoria • data e o valor", () => {
    const { claro: c } = renderNosDoisTemas(
      <TransactionRow descricao="Mercado" categoria="Alimentação" data="Hoje, 14:32" centavos={4590} tipo="saida" />,
    );
    expect(c.getByText("Mercado")).toBeTruthy();
    expect(c.getByText("Alimentação • Hoje, 14:32")).toBeTruthy();
    expect(c.getByText("−R$ 45,90")).toBeTruthy();
  });

  it("sem `data`, o subtítulo é só a categoria", () => {
    const { claro: c } = renderNosDoisTemas(<TransactionRow descricao="Uber" categoria="Transporte" centavos={2350} tipo="saida" />);
    expect(c.getByText("Transporte")).toBeTruthy();
  });

  it("a11y NUM rótulo: o container externo é `accessible` com um único rótulo combinado", () => {
    const { claro: c } = renderNosDoisTemas(
      <TransactionRow descricao="Salário" categoria="Renda" data="Ontem" centavos={450000} tipo="entrada" />,
    );
    // `[0]`: o wrapper externo é o primeiro `View` na árvore (o `ListRow`
    // interno também renderiza `View`s) — a ordem de `UNSAFE_getAllByType` é
    // pai antes de filho.
    const raiz = c.UNSAFE_getAllByType(View)[0]!;
    expect(raiz.props.accessible).toBe(true);
    expect(raiz.props.accessibilityLabel).toBe("Salário, Renda, Ontem, mais 4500 reais");
  });

  it("saida usa 'menos' na fala, entrada usa 'mais'", () => {
    const { claro: saida } = renderNosDoisTemas(<TransactionRow descricao="x" categoria="y" centavos={100} tipo="saida" />);
    expect(saida.UNSAFE_getAllByType(View)[0]!.props.accessibilityLabel).toContain("menos 1 real");

    const { claro: entrada } = renderNosDoisTemas(<TransactionRow descricao="x" categoria="y" centavos={100} tipo="entrada" />);
    expect(entrada.UNSAFE_getAllByType(View)[0]!.props.accessibilityLabel).toContain("mais 1 real");
  });

  it("centavos zero: fala 'zero reais' SEM sinal, nunca 'menos zero'/'mais zero'", () => {
    const { claro: saida } = renderNosDoisTemas(<TransactionRow descricao="x" categoria="y" centavos={0} tipo="saida" />);
    const rotuloSaida = saida.UNSAFE_getAllByType(View)[0]!.props.accessibilityLabel as string;
    expect(rotuloSaida).toContain("zero reais");
    expect(rotuloSaida).not.toMatch(/menos|mais/);

    const { claro: entrada } = renderNosDoisTemas(<TransactionRow descricao="x" categoria="y" centavos={0} tipo="entrada" />);
    const rotuloEntrada = entrada.UNSAFE_getAllByType(View)[0]!.props.accessibilityLabel as string;
    expect(rotuloEntrada).toContain("zero reais");
    expect(rotuloEntrada).not.toMatch(/menos|mais/);
  });

  it("centavos não-inteiro: fala 'valor indisponível' SEM sinal", () => {
    const { claro: c } = renderNosDoisTemas(<TransactionRow descricao="x" categoria="y" centavos={1.5} tipo="saida" />);
    const rotulo = c.UNSAFE_getAllByType(View)[0]!.props.accessibilityLabel as string;
    expect(rotulo).toContain("valor indisponível");
    expect(rotulo).not.toMatch(/menos|mais/);
  });

  it("oculto repassa ao Money (máscara 'R$ ••••') e não fala o valor real", () => {
    const { claro: c } = renderNosDoisTemas(
      <TransactionRow descricao="Salário" categoria="Renda" centavos={450000} tipo="entrada" oculto />,
    );
    expect(c.getByText("R$ ••••")).toBeTruthy();
    const rotulo = c.UNSAFE_getAllByType(View)[0]!.props.accessibilityLabel as string;
    expect(rotulo).not.toContain("4500");
  });

  it("snapshot (dois temas)", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(
      <TransactionRow descricao="Mercado" categoria="Alimentação" data="Hoje, 14:32" centavos={4590} tipo="saida" />,
    );
    expect(c.toJSON()).toMatchSnapshot("claro");
    expect(e.toJSON()).toMatchSnapshot("escuro");
  });
});
