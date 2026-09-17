import { fireEvent, render } from "@testing-library/react-native";
import type { ReactElement } from "react";
import { useState } from "react";
import { StyleSheet } from "react-native";
import * as Haptics from "expo-haptics";

import { AmountInput } from "@/ui/componentes/AmountInput";
import { TETO_CENTAVOS } from "@/ui/dinheiro";
import { TemaProvider } from "@/ui/tema";
import { claro as coresClaras } from "@/ui/tokens";

import { renderNosDoisTemas } from "./_render";

/**
 * SÓ para os testes que disparam `fireEvent`: a RNTL marca a última árvore
 * renderizada como "a tela ativa" (`screen.UNSAFE_root`), e `fireEvent`
 * recusa evento em qualquer elemento fora dela — `isElementMounted()` em
 * `component-tree.js`. Renderizar claro+escuro (`renderNosDoisTemas`) deixa
 * o claro "inativo" pra sempre depois que o escuro nasce por cima: o evento
 * vira no-op silencioso (medido: nenhum erro, só `onChange` nunca chamado).
 * Interação não muda com o tema, então aqui um render só resolve.
 */
function renderInterativo(el: ReactElement) {
  return render(<TemaProvider esquema="light">{el}</TemaProvider>);
}

/**
 * Espelha o `AmountInput` real: `centavos` é estado do CHAMADOR, o
 * componente é controlado. É o harness que faz a "conversa" (§3 do
 * CLAUDE.md) em vez de um `onChangeText` isolado — sem ele, um rejeito que
 * "trava" o campo (nunca mais aceita dígito) não aparece em teste nenhum.
 */
function Formulario(props: { inicial?: number; erro?: string; desativado?: boolean }) {
  const [centavos, setCentavos] = useState(props.inicial ?? 0);
  return (
    <AmountInput
      centavos={centavos}
      onChange={setCentavos}
      rotulo="Valor"
      erro={props.erro}
      desativado={props.desativado}
    />
  );
}

describe("AmountInput — conversa (digitação real)", () => {
  it("1, 2, 3 vira R$ 1,23; backspace some com o último dígito; colar lixo não emperra", () => {
    const resultado = renderInterativo(<Formulario />);
    const campo = resultado.getByTestId("valor-input");

    let visor = "0,00";
    const tecla = (t: string) => {
      visor += t;
      fireEvent.changeText(campo, visor);
    };

    tecla("1");
    expect(resultado.getByTestId("valor-input").props.value).toBe("0,01");
    visor = resultado.getByTestId("valor-input").props.value;

    tecla("2");
    expect(resultado.getByTestId("valor-input").props.value).toBe("0,12");
    visor = resultado.getByTestId("valor-input").props.value;

    tecla("3");
    expect(resultado.getByTestId("valor-input").props.value).toBe("1,23");
    visor = resultado.getByTestId("valor-input").props.value;

    // backspace: o novo texto é o atual menos o último caractere.
    fireEvent.changeText(campo, visor.slice(0, -1));
    expect(resultado.getByTestId("valor-input").props.value).toBe("0,12");
    visor = resultado.getByTestId("valor-input").props.value;

    // colar um caractere fora da lista branca: nada muda (não emperra o campo).
    fireEvent.changeText(campo, visor + "١");
    expect(resultado.getByTestId("valor-input").props.value).toBe("0,12");

    // segue digitando depois da rejeição — não travou.
    fireEvent.changeText(campo, visor + "4");
    expect(resultado.getByTestId("valor-input").props.value).toBe("1,24");
  });

  it("colar 11 dígitos não muda o valor", () => {
    const resultado = renderInterativo(<Formulario />);
    const campo = resultado.getByTestId("valor-input");
    fireEvent.changeText(campo, "0,00" + "1".repeat(11));
    expect(resultado.getByTestId("valor-input").props.value).toBe("0,00");
  });

  it("colar o teto exato preenche até R$ 99.999.999,99", () => {
    const resultado = renderInterativo(<Formulario />);
    const campo = resultado.getByTestId("valor-input");
    fireEvent.changeText(campo, "R$ 99.999.999,99");
    expect(resultado.getByTestId("valor-input").props.value).toBe("99.999.999,99");
  });
});

describe("AmountInput — rejeição não chama onChange", () => {
  it("caractere fora da lista branca: onChange não é chamado", () => {
    const espiao = jest.fn();
    const resultado = renderInterativo(<AmountInput centavos={12} onChange={espiao} rotulo="Valor" />);
    fireEvent.changeText(resultado.getByTestId("valor-input"), "abc");
    expect(espiao).not.toHaveBeenCalled();
  });

  it("dígito que resulta no MESMO valor atual: onChange não é chamado", () => {
    const espiao = jest.fn();
    // partes(12) = "0","12" → value exibido "0,12"; reenviar o mesmo texto
    // dá o mesmo `centavos` (12) — não deve dobrar de aviso.
    const resultado = renderInterativo(<AmountInput centavos={12} onChange={espiao} rotulo="Valor" />);
    fireEvent.changeText(resultado.getByTestId("valor-input"), "0,12");
    expect(espiao).not.toHaveBeenCalled();
  });
});

describe("AmountInput — colar só substitui com símbolo", () => {
  it("digitação rápida em lote continua acumulando: evento 1,2345 sobre 1,23 dá R$ 123,45", () => {
    const resultado = renderInterativo(<Formulario inicial={123} />);
    const campo = resultado.getByTestId("valor-input");
    expect(campo.props.value).toBe("1,23");
    fireEvent.changeText(campo, "1,2345");
    expect(resultado.getByTestId("valor-input").props.value).toBe("123,45");
  });

  it("campo em 1,23, colar R$ 1.234,56 substitui: dá 123456, não 123123456", () => {
    const resultado = renderInterativo(<Formulario inicial={123} />);
    const campo = resultado.getByTestId("valor-input");
    expect(campo.props.value).toBe("1,23");
    // evento nativo real: cursor no fim, colar chega concatenado ao texto atual.
    fireEvent.changeText(campo, "1,23" + "R$ 1.234,56");
    expect(resultado.getByTestId("valor-input").props.value).toBe("1.234,56");
  });

  it("colar com pontuação mas sem R$ também substitui: 1.234,56 sobre 1,23 dá 123456", () => {
    const resultado = renderInterativo(<Formulario inicial={123} />);
    const campo = resultado.getByTestId("valor-input");
    fireEvent.changeText(campo, "1,23" + "1.234,56");
    expect(resultado.getByTestId("valor-input").props.value).toBe("1.234,56");
  });

  it("colar só dígitos ACUMULA em vez de substituir (consequência aceita pelo dono): 50 sobre 1,23 dá R$ 123,50", () => {
    const resultado = renderInterativo(<Formulario inicial={123} />);
    const campo = resultado.getByTestId("valor-input");
    fireEvent.changeText(campo, "1,23" + "50");
    expect(resultado.getByTestId("valor-input").props.value).toBe("123,50");
  });

  it("depois de colar com símbolo, digitar continua acumulando pela direita", () => {
    const resultado = renderInterativo(<Formulario inicial={123} />);
    const campo = resultado.getByTestId("valor-input");
    fireEvent.changeText(campo, "1,23" + "R$ 1.234,56");
    const atual = resultado.getByTestId("valor-input").props.value;
    fireEvent.changeText(campo, atual + "7");
    expect(resultado.getByTestId("valor-input").props.value).toBe("12.345,67");
  });

  it("backspace continua funcionando depois da colagem", () => {
    const resultado = renderInterativo(<Formulario inicial={123} />);
    const campo = resultado.getByTestId("valor-input");
    fireEvent.changeText(campo, "1,23" + "R$ 1.234,56");
    const atual = resultado.getByTestId("valor-input").props.value;
    fireEvent.changeText(campo, atual.slice(0, -1));
    expect(resultado.getByTestId("valor-input").props.value).toBe("123,45");
  });

  // Sufixo "abc" não tem símbolo da lista branca nem dígito: recusa (ver
  // describe dedicado "colar sem dígito recusa" para os casos só-símbolo).
  it("colar texto rejeitado (abc) com campo preenchido não muda nada", () => {
    const resultado = renderInterativo(<Formulario inicial={123} />);
    const campo = resultado.getByTestId("valor-input");
    fireEvent.changeText(campo, "1,23" + "abc");
    expect(resultado.getByTestId("valor-input").props.value).toBe("1,23");
  });

  // Colar 1 dígito é o mesmo caso de "sufixo só com dígitos": acumula, não
  // é um limite à parte, é a mesma regra de colagem descrita no componente.
  it("colar 1 dígito acumula como se fosse digitado (sufixo só com dígitos)", () => {
    const resultado = renderInterativo(<Formulario inicial={123} />);
    const campo = resultado.getByTestId("valor-input");
    fireEvent.changeText(campo, "1,23" + "5");
    expect(resultado.getByTestId("valor-input").props.value).toBe("12,35");
  });
});

describe("AmountInput — colar sem dígito recusa", () => {
  it.each(["R$", "  ", "--"])("colar %p sobre 1,23 não chama onChange nem muda o valor exibido", (colado) => {
    const espiao = jest.fn();
    const resultado = renderInterativo(<AmountInput centavos={123} onChange={espiao} rotulo="Valor" />);
    const campo = resultado.getByTestId("valor-input");
    fireEvent.changeText(campo, "1,23" + colado);
    expect(espiao).not.toHaveBeenCalled();
    expect(campo.props.value).toBe("1,23");
  });

  it.each([",", "-", "R$"])("centavos inválido (NaN) + colar %p não chama onChange", (colado) => {
    const espiao = jest.fn();
    const resultado = renderInterativo(<AmountInput centavos={NaN} onChange={espiao} rotulo="Valor" />);
    fireEvent.changeText(resultado.getByTestId("valor-input"), colado);
    expect(espiao).not.toHaveBeenCalled();
  });
});

describe("AmountInput — sufixo no teto", () => {
  it("no teto, sufixo só dígito é recusado (estouraria 10 dígitos de centavos)", () => {
    const resultado = renderInterativo(<Formulario inicial={TETO_CENTAVOS} />);
    const campo = resultado.getByTestId("valor-input");
    expect(campo.props.value).toBe("99.999.999,99");
    fireEvent.changeText(campo, "99.999.999,99" + "0");
    expect(resultado.getByTestId("valor-input").props.value).toBe("99.999.999,99");
  });

  it("no teto, colar com símbolo substitui normalmente: R$ 1,00 dá 100", () => {
    const resultado = renderInterativo(<Formulario inicial={TETO_CENTAVOS} />);
    const campo = resultado.getByTestId("valor-input");
    fireEvent.changeText(campo, "99.999.999,99" + "R$ 1,00");
    expect(resultado.getByTestId("valor-input").props.value).toBe("1,00");
  });
});

describe("AmountInput — apagar tudo zera", () => {
  it("string vazia sobre valor preenchido volta a R$ 0,00", () => {
    const resultado = renderInterativo(<Formulario inicial={123} />);
    const campo = resultado.getByTestId("valor-input");
    fireEvent.changeText(campo, "");
    expect(resultado.getByTestId("valor-input").props.value).toBe("0,00");
  });
});

describe("AmountInput — prop centavos inválida mostra placeholder", () => {
  it.each([NaN, -5, 1.5, TETO_CENTAVOS + 1, 2 ** 53])(
    "%p: campo vazio, placeholder '—', label 'valor indisponível', sem onChange",
    (v) => {
      const espiao = jest.fn();
      const { claro } = renderNosDoisTemas(<AmountInput centavos={v} onChange={espiao} rotulo="Valor" />);
      const campo = claro.getByTestId("valor-input");
      expect(campo.props.value).toBe("");
      expect(campo.props.placeholder).toBe("—");
      expect(campo.props.accessibilityLabel).toBe("Valor, valor indisponível");
      expect(espiao).not.toHaveBeenCalled();
    },
  );

  it("primeira digitação depois de inválido chama onChange com o valor visto", () => {
    const espiao = jest.fn();
    const resultado = renderInterativo(<AmountInput centavos={NaN} onChange={espiao} rotulo="Valor" />);
    fireEvent.changeText(resultado.getByTestId("valor-input"), "5");
    expect(espiao).toHaveBeenCalledWith(5);
  });

  it("primeira colagem depois de inválido chama onChange com o valor colado", () => {
    const espiao = jest.fn();
    const resultado = renderInterativo(<AmountInput centavos={-5} onChange={espiao} rotulo="Valor" />);
    fireEvent.changeText(resultado.getByTestId("valor-input"), "R$ 1.234,56");
    expect(espiao).toHaveBeenCalledWith(123456);
  });
});

describe("AmountInput — aviso tátil pelo componente (M8)", () => {
  afterEach(() => {
    jest.clearAllMocks();
  });

  it("nasce com erro: não vibra na montagem", () => {
    renderInterativo(<Formulario inicial={100} erro="Saldo insuficiente" />);
    expect(Haptics.notificationAsync).not.toHaveBeenCalled();
  });

  it("erro surge depois de undefined: vibra 1 vez", () => {
    const resultado = render(
      <TemaProvider esquema="light">
        <Formulario inicial={100} />
      </TemaProvider>,
    );
    expect(Haptics.notificationAsync).not.toHaveBeenCalled();
    resultado.rerender(
      <TemaProvider esquema="light">
        <Formulario inicial={100} erro="x" />
      </TemaProvider>,
    );
    expect(Haptics.notificationAsync).toHaveBeenCalledTimes(1);
  });
});

describe("AmountInput — desativado usa inkMuted no texto do campo (M10)", () => {
  it("cor do texto muda de ink para inkMuted quando desativado", () => {
    const { claro: ativo } = renderNosDoisTemas(<Formulario inicial={1230} />);
    const { claro: inativo } = renderNosDoisTemas(<Formulario inicial={1230} desativado />);
    const estiloAtivo = StyleSheet.flatten(ativo.getByTestId("valor-input").props.style);
    const estiloInativo = StyleSheet.flatten(inativo.getByTestId("valor-input").props.style);
    expect(estiloAtivo.color).toBe(coresClaras.ink);
    expect(estiloInativo.color).toBe(coresClaras.inkMuted);
  });
});

describe("AmountInput — fixos de teclado, cursor e teto de fonte", () => {
  it("number-pad, cursor preso no fim, sem maxLength, teto de fonte 1.3", () => {
    const { claro } = renderNosDoisTemas(<AmountInput centavos={1230} onChange={jest.fn()} rotulo="Valor" />);
    const campo = claro.getByTestId("valor-input");
    expect(campo.props.keyboardType).toBe("number-pad");
    expect(campo.props.selection).toEqual({ start: "12,30".length, end: "12,30".length });
    expect(campo.props.maxLength).toBeUndefined();
    expect(campo.props.maxFontSizeMultiplier).toBe(1.3);
  });

  it("estilo do campo tem fontVariant tabular-nums", () => {
    const { claro } = renderNosDoisTemas(<AmountInput centavos={1230} onChange={jest.fn()} rotulo="Valor" />);
    const estilo = StyleSheet.flatten(claro.getByTestId("valor-input").props.style);
    expect(estilo.fontVariant).toEqual(["tabular-nums"]);
  });
});

describe("AmountInput — desativado", () => {
  it("editable=false e accessibilityState.disabled", () => {
    const { claro } = renderNosDoisTemas(<Formulario inicial={1230} desativado />);
    const campo = claro.getByTestId("valor-input");
    expect(campo.props.editable).toBe(false);
    expect(campo.props.accessibilityState).toEqual({ disabled: true });
  });
});

describe("AmountInput — a11y e erro visível", () => {
  it("accessibilityLabel junta rótulo, fala e erro", () => {
    const { claro } = renderNosDoisTemas(<Formulario inicial={1230} erro="Valor muito alto" />);
    const campo = claro.getByTestId("valor-input");
    expect(campo.props.accessibilityLabel).toBe("Valor, 12 reais e 30 centavos, erro: Valor muito alto");
    expect(claro.getByText("Valor muito alto")).toBeTruthy();
  });
});

describe("AmountInput — snapshot (dois temas)", () => {
  it("com valor e com erro", () => {
    const { claro, escuro } = renderNosDoisTemas(
      <Formulario inicial={123456789} erro="Saldo insuficiente" />,
    );
    expect(claro.toJSON()).toMatchSnapshot("claro");
    expect(escuro.toJSON()).toMatchSnapshot("escuro");
  });
});
