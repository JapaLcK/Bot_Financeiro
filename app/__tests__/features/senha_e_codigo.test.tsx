/**
 * "Desativar" e "Gerar novos códigos" — os dois usam `SenhaECodigo`, com o
 * cliente e o cofre de verdade por baixo (só `fetch` e `Alert` são dublês).
 */
import { act, fireEvent } from "@testing-library/react-native";
import { Alert, TextInput, type AlertButton } from "react-native";

import { SessaoProvider } from "@/features/auth/sessao";
import { DesativarMfa } from "@/features/seguranca/DesativarMfa";
import { NovosCodigos } from "@/features/seguranca/NovosCodigos";
import { guardarCredenciais, lerCredenciais } from "@/storage/secure";

import { renderInterativo } from "../ui/_render";
import { chamadas, prepararCaso, respirar, resposta, rotear, S, segurar } from "./auth_apoio";

const CODIGOS = Array.from({ length: 10 }, (_, i) => `QWERT-YUP${String(i).padStart(2, "0")}`);
const CAMPO_DESATIVAR = "Código do app ou de backup";

/** O alerta responde sozinho com o botão `escolha`. */
function responderAlerta(escolha: "Cancelar" | "Desativar") {
  return jest.spyOn(Alert, "alert").mockImplementation((_t, _m, botoes?: AlertButton[]) => {
    botoes?.find((b) => b.text === escolha)?.onPress?.();
  });
}

function montarDesativar(aoConcluir = jest.fn()) {
  const tela = renderInterativo(
    <SessaoProvider>
      <DesativarMfa aoConcluir={aoConcluir} />
    </SessaoProvider>,
  );
  return { ...tela, aoConcluir };
}

function preencher(tela: ReturnType<typeof renderInterativo>, rotuloCodigo: string, codigo: string) {
  fireEvent.changeText(tela.getByLabelText("Senha"), "s3nha");
  fireEvent.changeText(tela.getByLabelText(rotuloCodigo), codigo);
}

async function tocar(tela: ReturnType<typeof renderInterativo>, rotulo: string) {
  await act(async () => {
    fireEvent.press(tela.getByRole("button", { name: rotulo }));
    await respirar();
  });
}

const disable = () => chamadas().filter((c) => c.caminho === "/auth/mfa/disable");

beforeEach(async () => {
  prepararCaso();
  jest.restoreAllMocks();
  await guardarCredenciais(S);
});

describe("DesativarMfa", () => {
  it("sem senha e código o botão fica desativado", async () => {
    const tela = montarDesativar();
    await act(respirar);
    expect(tela.getByRole("button", { name: "Desativar" }).props.accessibilityState).toMatchObject({ disabled: true });
  });

  it("confirmação CANCELADA não chama a rota", async () => {
    rotear({ "/auth/mfa/disable": () => resposta(200, { ok: true }) });
    const alerta = responderAlerta("Cancelar");
    const tela = montarDesativar();
    preencher(tela, CAMPO_DESATIVAR, "123456");

    await tocar(tela, "Desativar");

    expect(alerta).toHaveBeenCalledTimes(1);
    expect(disable()).toEqual([]);
    expect(tela.aoConcluir).not.toHaveBeenCalled();
  });

  it("confirmação aceita, toque duplo: UM alerta e UM /auth/mfa/disable com {password, code}; depois fecha", async () => {
    const portao = segurar();
    rotear({ "/auth/mfa/disable": async () => { await portao.promessa; return resposta(200, { ok: true }); } });
    const alerta = responderAlerta("Desativar");
    const tela = montarDesativar();
    preencher(tela, CAMPO_DESATIVAR, "ABCDE-FGHIJ");

    await act(async () => {
      const botao = tela.getByRole("button", { name: "Desativar" });
      fireEvent.press(botao);
      fireEvent.press(botao);
      await respirar();
      portao.soltar();
      await respirar();
    });

    expect(alerta).toHaveBeenCalledTimes(1);
    expect(disable()).toEqual([expect.objectContaining({ corpo: { password: "s3nha", code: "ABCDE-FGHIJ" } })]);
    expect(tela.aoConcluir).toHaveBeenCalledTimes(1);
  });

  it("senha errada (401 SEM a marca): mostra o erro, fica na sheet, e a sessão continua no cofre", async () => {
    rotear({ "/auth/mfa/disable": () => resposta(401, { detail: "Senha incorreta." }) });
    responderAlerta("Desativar");
    const tela = montarDesativar();
    preencher(tela, CAMPO_DESATIVAR, "123456");

    await tocar(tela, "Desativar");

    expect(tela.getByText("Senha incorreta.")).toBeTruthy();
    expect(tela.aoConcluir).not.toHaveBeenCalled();
    expect(chamadas().map((c) => c.caminho)).toEqual(["/auth/mfa/disable"]);
    await expect(lerCredenciais()).resolves.toEqual(S);
  });

  it("400 (código inválido): o campo do código esvazia e o foco volta a ele; a senha fica", async () => {
    rotear({ "/auth/mfa/disable": () => resposta(400, { detail: "Código inválido." }) });
    responderAlerta("Desativar");
    const foco = jest.mocked(TextInput.prototype.focus);
    const tela = montarDesativar();
    preencher(tela, CAMPO_DESATIVAR, "123456");
    foco.mockClear();

    await tocar(tela, "Desativar");

    expect(tela.getByText("Código inválido.")).toBeTruthy();
    expect(tela.getByLabelText(CAMPO_DESATIVAR).props.value).toBe("");
    expect(tela.getByLabelText("Senha").props.value).toBe("s3nha");
    expect(foco).toHaveBeenCalled();
  });
});

describe("NovosCodigos", () => {
  function montar(aoEnviar = jest.fn(), aoFalhar = jest.fn()) {
    const tela = renderInterativo(
      <SessaoProvider>
        <NovosCodigos aoEnviar={aoEnviar} aoFalhar={aoFalhar} aoConcluir={jest.fn()} />
      </SessaoProvider>,
    );
    return { ...tela, aoEnviar, aoFalhar };
  }

  it("só TOTP: letras somem, e o botão só habilita com 6 dígitos — sem auto-envio", async () => {
    rotear({ "/auth/mfa/regenerate-backup-codes": () => resposta(200, { backup_codes: CODIGOS }) });
    const tela = montar();

    preencher(tela, "Código de 6 dígitos", "12a345");
    expect(tela.getByLabelText("Código de 6 dígitos").props.value).toBe("12345");
    expect(tela.getByRole("button", { name: "Gerar novos códigos" }).props.accessibilityState).toMatchObject({ disabled: true });

    await act(async () => {
      fireEvent.changeText(tela.getByLabelText("Código de 6 dígitos"), "123456");
      await respirar();
    });
    expect(chamadas()).toEqual([]);
  });

  it("200: mostra os códigos novos e avisa quem chama para prender a sheet", async () => {
    rotear({ "/auth/mfa/regenerate-backup-codes": () => resposta(200, { backup_codes: CODIGOS }) });
    const tela = montar();
    preencher(tela, "Código de 6 dígitos", "123456");

    await tocar(tela, "Gerar novos códigos");

    expect(chamadas()).toEqual([
      expect.objectContaining({ caminho: "/auth/mfa/regenerate-backup-codes", corpo: { password: "s3nha", code: "123456" } }),
    ]);
    expect(tela.getByText(CODIGOS.join("\n"))).toBeTruthy();
    expect(tela.aoEnviar).toHaveBeenCalledTimes(1);
    expect(tela.aoFalhar).not.toHaveBeenCalled();
  });
});
