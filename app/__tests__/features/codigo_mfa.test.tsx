/**
 * `CodigoMfa` pelo componente de verdade (não só a máquina de `entrar.ts`):
 * B1 (busy visível ANTES da resposta chegar) e M4 (teto de dígitos do TOTP,
 * maxLength do backup) só se provam desenhando a tela — a lógica de
 * `verificar()`/`tocar()` já está coberta em `entrar_mfa.test.ts`.
 */
import { act, fireEvent } from "@testing-library/react-native";
import { useState } from "react";
import { TextInput } from "react-native";

import { CodigoMfa } from "@/features/auth/CodigoMfa";
import type { EstadoEntrar, EstadoMfa } from "@/features/auth/entrar";

import { renderInterativo } from "../ui/_render";
import { chamadas, prepararCaso, resposta, rotear, segurar } from "./auth_apoio";

const M: EstadoMfa = { fase: "mfa", desafio: "d-1", email: "m@x.com", modo: "totp" };

/** Mesmo papel do `estado`/`setEstado` de `app/(auth)/entrar.tsx`: `CodigoMfa` é controlado. */
function Harness({ inicial, autenticar }: { inicial: EstadoMfa; autenticar: () => void }) {
  const [estado, setEstado] = useState<EstadoEntrar>(inicial);
  if (estado.fase !== "mfa" && estado.fase !== "verificando") return null;
  return <CodigoMfa estado={estado} autenticar={autenticar} aplicar={setEstado} />;
}

const credencial = () => ({
  user_id: 1,
  email: M.email,
  access_token: "a",
  refresh_token: "r",
  dashboard_token: "d",
  expires_in: 900,
});

beforeEach(prepararCaso);

describe("CodigoMfa — B1 (busy antes da resposta)", () => {
  it("ao auto-enviar o 6º dígito, o campo fica desativado e os botões desativados/em carregando ANTES da resposta chegar", async () => {
    const portao = segurar();
    rotear({ "/auth/mfa/verify-login": async () => { await portao.promessa; return resposta(200, credencial()); } });
    const { getByLabelText, getByRole } = renderInterativo(<Harness inicial={M} autenticar={jest.fn()} />);

    fireEvent.changeText(getByLabelText("Código de 6 dígitos"), "123456");

    // Ainda SEM resposta (o portão não foi solto): isto é o estado "verificando".
    expect(getByLabelText("Código de 6 dígitos").props.accessibilityState).toMatchObject({ disabled: true });
    expect(getByRole("button", { name: "Verificar" }).props.accessibilityState).toMatchObject({ busy: true, disabled: true });
    expect(getByRole("button", { name: "Voltar" }).props.accessibilityState).toMatchObject({ disabled: true });
    expect(getByRole("button", { name: "Usar código de backup" }).props.accessibilityState).toMatchObject({ disabled: true });

    await act(async () => {
      portao.soltar();
      await Promise.resolve();
    });
  });
});

describe("CodigoMfa — duplo envio pela UI de verdade (controle positivo)", () => {
  it("colar \"123456\" (auto-envio) e tocar Verificar no mesmo instante: UMA requisição só", async () => {
    const chegou = segurar();
    const portao = segurar();
    rotear({
      "/auth/mfa/verify-login": async () => {
        chegou.soltar();
        await portao.promessa;
        return resposta(200, credencial());
      },
    });
    const autenticar = jest.fn();
    const { getByLabelText, getByRole } = renderInterativo(<Harness inicial={M} autenticar={autenticar} />);

    fireEvent.changeText(getByLabelText("Código de 6 dígitos"), "123456");
    await act(async () => {
      await chegou.promessa;
    });
    // O botão já está desativado (B1) — um toque aqui não soma pedido, nem
    // pela guarda de `tocar()`, nem pelo próprio `disabled` do componente.
    fireEvent.press(getByRole("button", { name: "Verificar" }));
    await act(async () => {
      portao.soltar();
      await Promise.resolve();
    });

    expect(chamadas().filter((c) => c.caminho === "/auth/mfa/verify-login")).toHaveLength(1);
    expect(autenticar).toHaveBeenCalledTimes(1);
  });
});

describe("CodigoMfa — Verificar só habilita com código completo (apontamento Codex #1)", () => {
  it("TOTP com 3 dígitos: Verificar fica desativado (não envia com código parcial)", () => {
    const { getByLabelText, getByRole } = renderInterativo(<Harness inicial={M} autenticar={jest.fn()} />);

    fireEvent.changeText(getByLabelText("Código de 6 dígitos"), "123");

    expect(getByRole("button", { name: "Verificar" }).props.accessibilityState).toMatchObject({ disabled: true });
  });

  // "6 dígitos habilita E envia" já está coberto por testes existentes: o
  // auto-envio dispara exatamente nesse limite (M4, "duplo envio pela UI de
  // verdade") e autentica com sucesso (entrar_mfa.test.ts, "200: autentica")
  // — testar "habilitado" isolado aqui exigiria digitar 6 dígitos SEM
  // auto-enviar, o que o próprio TOTP não permite (CLAUDE.md §3: não duplicar
  // o que já mede).

  it("backup com 1 caractere: Verificar habilita (regra é 'não vazio', não 6 dígitos)", () => {
    const { getByLabelText, getByRole } = renderInterativo(
      <Harness inicial={{ ...M, modo: "backup" }} autenticar={jest.fn()} />,
    );

    fireEvent.changeText(getByLabelText("Código de backup"), "A");

    expect(getByRole("button", { name: "Verificar" }).props.accessibilityState).toMatchObject({ disabled: false });
  });
});

describe("CodigoMfa — M4 (teto de dígitos)", () => {
  it("TOTP: colar 7 dígitos corta em 6 antes de enviar — não manda o 7º ao servidor", async () => {
    let corpoEnviado: unknown;
    rotear({
      "/auth/mfa/verify-login": (o) => {
        corpoEnviado = JSON.parse(String(o.body));
        return resposta(200, credencial());
      },
    });
    const { getByLabelText } = renderInterativo(<Harness inicial={M} autenticar={jest.fn()} />);

    await act(async () => {
      fireEvent.changeText(getByLabelText("Código de 6 dígitos"), "1234567");
      await Promise.resolve();
    });

    expect(getByLabelText("Código de 6 dígitos").props.value).toBe("123456");
    expect(corpoEnviado).toMatchObject({ code: "123456" });
  });

  it("backup: SEM maxLength nativo (M-c — um teto de 11 cortava um colado com espaço extra na ponta ANTES de normalizar)", () => {
    const { getByLabelText } = renderInterativo(<Harness inicial={{ ...M, modo: "backup" }} autenticar={jest.fn()} />);
    expect(getByLabelText("Código de backup").props.maxLength).toBeUndefined();
  });

  it("TOTP não tem maxLength nativo (o corte é feito à mão, para não cortar espaço de autofill no meio)", () => {
    const { getByLabelText } = renderInterativo(<Harness inicial={M} autenticar={jest.fn()} />);
    expect(getByLabelText("Código de 6 dígitos").props.maxLength).toBeUndefined();
  });

  // Não há um segundo teste "cola com espaço extra e chega inteiro" aqui:
  // `fireEvent.changeText` chama o `onChangeText` direto, sem passar pelo
  // truncamento NATIVO do `TextInput` (é o próprio `maxLength` do SO, não
  // JS) — nesse harness ele SEMPRE devolveria a string inteira, com ou sem
  // `maxLength` no componente. Só o teste acima (o valor do PROP) discrimina
  // o conserto; medido: restaurando `maxLength={11}` no componente, ele é
  // quem fica vermelho (Received: 11), e um segundo teste via
  // `fireEvent.changeText` continuaria verde nas duas versões — não mediria
  // nada (CLAUDE.md §3).
});

describe("CodigoMfa — I-D (auto-envio não engole lixo colado no TOTP)", () => {
  it.each(["123-456", "Código: 123456", "G-123456", "123 456", "١٢٣٤٥٦"])(
    "colar %p não auto-envia (mesmo quando sobra exatamente 6 dígitos depois do filtro)",
    async (colado) => {
      rotear({ "/auth/mfa/verify-login": () => resposta(400, { detail: "Código inválido." }) });
      const { getByLabelText } = renderInterativo(<Harness inicial={M} autenticar={jest.fn()} />);

      await act(async () => {
        fireEvent.changeText(getByLabelText("Código de 6 dígitos"), colado);
        await Promise.resolve();
      });

      expect(chamadas().filter((c) => c.caminho === "/auth/mfa/verify-login")).toHaveLength(0);
    },
  );

  it("dígito arábico-índico é DESCARTADO do campo (o servidor nunca bateria mesmo com o código certo)", () => {
    const { getByLabelText } = renderInterativo(<Harness inicial={M} autenticar={jest.fn()} />);
    const campo = getByLabelText("Código de 6 dígitos");
    fireEvent.changeText(campo, "١٢٣٤٥٦");
    expect(campo.props.value).toBe("");
  });

  it("colar '123-456' deixa só os dígitos no campo (filtra pra exibição, mesmo sem auto-enviar)", () => {
    const { getByLabelText } = renderInterativo(<Harness inicial={M} autenticar={jest.fn()} />);
    const campo = getByLabelText("Código de 6 dígitos");
    fireEvent.changeText(campo, "123-456");
    expect(campo.props.value).toBe("123456");
  });
});

describe("CodigoMfa — depois de um código errado (D1-A)", () => {
  const respirar = async () => {
    for (let i = 0; i < 20; i++) await Promise.resolve();
  };
  const verifies = () => chamadas().filter((c) => c.caminho === "/auth/mfa/verify-login");
  // O mock do RN põe `focus` no protótipo: um `jest.fn` só para todos os campos.
  const foco = jest.mocked(TextInput.prototype.focus);

  beforeEach(() => {
    rotear({ "/auth/mfa/verify-login": () => resposta(400, { detail: "Código inválido.", code: "mfa_code_invalid" }) });
    foco.mockClear();
  });

  it("TOTP: o campo esvazia a cada erro (também no 2º igual seguido), e o próximo dígito não reenvia o código velho", async () => {
    const { getByLabelText, getByText } = renderInterativo(<Harness inicial={M} autenticar={jest.fn()} />);
    const campo = () => getByLabelText(/^Código de 6 dígitos/);
    expect(foco).not.toHaveBeenCalled();

    for (const errado of ["000000", "111111"]) {
      await act(async () => {
        fireEvent.changeText(campo(), errado);
        await respirar();
      });
      expect(getByText("Código inválido.")).toBeTruthy();
      expect(campo().props.value).toBe("");
    }

    // O teclado entrega o texto do campo + a tecla nova.
    await act(async () => {
      fireEvent.changeText(campo(), `${campo().props.value}1`);
      await respirar();
    });
    expect(campo().props.value).toBe("1");
    expect(verifies()).toHaveLength(2);
    // O `editable={false}` da verificação tira o foco no iOS: cada erro o devolve.
    expect(foco).toHaveBeenCalledTimes(2);
  });

  it("backup: o texto digitado continua no campo depois do erro", async () => {
    const { getByLabelText, getByRole, getByText } = renderInterativo(
      <Harness inicial={{ ...M, modo: "backup" }} autenticar={jest.fn()} />,
    );

    fireEvent.changeText(getByLabelText("Código de backup"), "ABCDE-FGHIJ");
    await act(async () => {
      fireEvent.press(getByRole("button", { name: "Verificar" }));
      await respirar();
    });

    expect(getByText("Código inválido.")).toBeTruthy();
    expect(getByLabelText(/^Código de backup/).props.value).toBe("ABCDE-FGHIJ");
    expect(foco).toHaveBeenCalledTimes(1);
  });
});
