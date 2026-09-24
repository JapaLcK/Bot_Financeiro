/**
 * `AtivarMfa` pelo componente de verdade, com o cliente de API e o cofre de
 * verdade por baixo (só o `fetch` é dublê) — é o que prova que a senha errada
 * NÃO derruba a sessão, que é o caminho em que o `credencialSecundaria` mora.
 */
import { act, fireEvent } from "@testing-library/react-native";
import { Linking, TextInput } from "react-native";

import { SessaoProvider } from "@/features/auth/sessao";
import { AtivarMfa, SETUP_EXPIRADO } from "@/features/seguranca/AtivarMfa";
import { guardarCredenciais, lerCredenciais } from "@/storage/secure";

import { renderInterativo } from "../ui/_render";
import { chamadas, prepararCaso, respirar, resposta, rotear, S, segurar } from "./auth_apoio";

declare const __dirname: string;

/** base64 de `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><path d="M0 0h1v1H0z"/></svg>`. */
const SVG_BASE64 =
  "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA0IDQiPjxwYXRoIGQ9Ik0wIDBoMXYxSDB6Ii8+PC9zdmc+";
const SETUP = {
  secret: "JBSWY3DPEHPK3PXP",
  uri: "otpauth://totp/PigBank:ana%40x.com?secret=JBSWY3DPEHPK3PXP&issuer=PigBank",
  qr_code: `data:image/svg+xml;base64,${SVG_BASE64}`,
};
const CODIGOS = Array.from({ length: 10 }, (_, i) => `ABCDE-FGH${String(i).padStart(2, "0")}`);

const aoEnviar = jest.fn();
const aoFalhar = jest.fn();
const aoConcluir = jest.fn();
const aoGerarNovos = jest.fn();

function montar() {
  return renderInterativo(
    <SessaoProvider>
      <AtivarMfa aoEnviar={aoEnviar} aoFalhar={aoFalhar} aoConcluir={aoConcluir} aoGerarNovos={aoGerarNovos} />
    </SessaoProvider>,
  );
}

async function passarDaSenha(tela: ReturnType<typeof montar>) {
  fireEvent.changeText(tela.getByLabelText("Senha"), "s3nha");
  await act(async () => {
    fireEvent.press(tela.getByRole("button", { name: "Continuar" }));
    await respirar();
  });
}

const caminhos = () => chamadas().map((c) => c.caminho);

beforeEach(async () => {
  prepararCaso();
  jest.clearAllMocks();
  await guardarCredenciais(S);
});

describe("AtivarMfa — passo da senha", () => {
  it("senha errada (401 SEM a marca): fica no passo da senha, uma chamada só, e a sessão continua no cofre", async () => {
    rotear({ "/auth/mfa/setup": () => resposta(401, { detail: "Senha incorreta." }) });
    const tela = montar();

    await passarDaSenha(tela);

    expect(tela.getByText("Senha incorreta.")).toBeTruthy();
    expect(tela.getByRole("button", { name: "Continuar" })).toBeTruthy();
    expect(caminhos()).toEqual(["/auth/mfa/setup"]);
    await expect(lerCredenciais()).resolves.toEqual(S);
  });

  it("409 de conta sem senha: mostra o texto do servidor", async () => {
    const detalhe = "Sua conta não tem senha. Defina uma senha antes de continuar.";
    rotear({ "/auth/mfa/setup": () => resposta(409, { detail: detalhe }) });
    const tela = montar();

    await passarDaSenha(tela);

    expect(tela.getByText(detalhe)).toBeTruthy();
  });

  it("toque duplo em Continuar: o setup sai UMA vez", async () => {
    const portao = segurar();
    rotear({ "/auth/mfa/setup": async () => { await portao.promessa; return resposta(200, SETUP); } });
    const tela = montar();
    fireEvent.changeText(tela.getByLabelText("Senha"), "s3nha");

    await act(async () => {
      const botao = tela.getByRole("button", { name: "Continuar" });
      fireEvent.press(botao);
      fireEvent.press(botao);
      portao.soltar();
      await respirar();
    });

    expect(caminhos()).toEqual(["/auth/mfa/setup"]);
    expect(tela.getByText(SETUP.secret)).toBeTruthy();
  });
});

describe("AtivarMfa — passo do código", () => {
  it("\"123456\" digitado dispara o enable UMA vez, mesmo com Ativar tocado junto, e mostra os 10 códigos", async () => {
    rotear({
      "/auth/mfa/setup": () => resposta(200, SETUP),
      "/auth/mfa/enable": () => resposta(200, { ok: true, backup_codes: CODIGOS }),
    });
    const tela = montar();
    await passarDaSenha(tela);

    await act(async () => {
      fireEvent.changeText(tela.getByLabelText("Código de 6 dígitos"), "123456");
      fireEvent.press(tela.getByRole("button", { name: "Ativar" }));
      await respirar();
    });

    expect(chamadas().filter((c) => c.caminho === "/auth/mfa/enable")).toEqual([
      expect.objectContaining({ corpo: { code: "123456" } }),
    ]);
    expect(tela.getByText(CODIGOS.join("\n"))).toBeTruthy();
    expect(aoEnviar).toHaveBeenCalledTimes(1);
    expect(aoFalhar).not.toHaveBeenCalled();

    fireEvent.press(tela.getByRole("button", { name: "Já guardei meus códigos" }));
    expect(aoConcluir).toHaveBeenCalledTimes(1);
  });

  it("\"123-456\" colado não dispara sozinho; com menos de 6 dígitos o Ativar fica desativado", async () => {
    rotear({ "/auth/mfa/setup": () => resposta(200, SETUP) });
    const tela = montar();
    await passarDaSenha(tela);

    await act(async () => {
      fireEvent.changeText(tela.getByLabelText("Código de 6 dígitos"), "123-456");
      await respirar();
    });
    expect(caminhos()).toEqual(["/auth/mfa/setup"]);

    fireEvent.changeText(tela.getByLabelText("Código de 6 dígitos"), "12345");
    expect(tela.getByRole("button", { name: "Ativar" }).props.accessibilityState).toMatchObject({ disabled: true });
  });

  it("400 no enable: o campo esvazia e o foco volta a ele", async () => {
    rotear({
      "/auth/mfa/setup": () => resposta(200, SETUP),
      "/auth/mfa/enable": () => resposta(400, { detail: "Código inválido. Tente novamente." }),
    });
    const foco = jest.mocked(TextInput.prototype.focus);
    const tela = montar();
    await passarDaSenha(tela);
    foco.mockClear();

    await act(async () => {
      fireEvent.changeText(tela.getByLabelText("Código de 6 dígitos"), "000000");
      await respirar();
    });

    expect(tela.getByText("Código inválido. Tente novamente.")).toBeTruthy();
    expect(tela.getByLabelText(/^Código de 6 dígitos/).props.value).toBe("");
    expect(foco).toHaveBeenCalled();
  });

  it("409 no enable (MFA ligado, códigos perdidos): oferece Gerar novos códigos", async () => {
    rotear({
      "/auth/mfa/setup": () => resposta(200, SETUP),
      "/auth/mfa/enable": () => resposta(409, { detail: "MFA já está ativado." }),
    });
    const tela = montar();
    await passarDaSenha(tela);

    await act(async () => {
      fireEvent.changeText(tela.getByLabelText("Código de 6 dígitos"), "123456");
      await respirar();
    });
    fireEvent.press(tela.getByRole("button", { name: "Gerar novos códigos" }));

    expect(aoGerarNovos).toHaveBeenCalledTimes(1);
    expect(aoFalhar).toHaveBeenCalledTimes(1);
  });

  it("400 \"Inicie o setup primeiro.\": volta ao passo da senha", async () => {
    rotear({
      "/auth/mfa/setup": () => resposta(200, SETUP),
      "/auth/mfa/enable": () => resposta(400, { detail: "Inicie o setup primeiro." }),
    });
    const tela = montar();
    await passarDaSenha(tela);

    await act(async () => {
      fireEvent.changeText(tela.getByLabelText("Código de 6 dígitos"), "123456");
      await respirar();
    });

    expect(tela.getByRole("button", { name: "Continuar" })).toBeTruthy();
  });

  it("SETUP_EXPIRADO é o mesmo texto que o servidor manda no 400 de /auth/mfa/enable", () => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- sem @types/node neste projeto (mesmo motivo de stickers.test.ts).
    const fs = require("node:fs") as { readFileSync: (arquivo: string, cod: string) => string };
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- idem.
    const path = require("node:path") as { resolve: (...partes: string[]) => string };

    const py = fs.readFileSync(path.resolve(__dirname, "../../../frontend/finance_bot_websocket_custom.py"), "utf8");
    const inicio = py.indexOf('@app.post("/auth/mfa/enable")');
    expect(inicio).toBeGreaterThanOrEqual(0);
    const rota = py.slice(inicio, py.indexOf("\n@app.", inicio + 1));
    expect(rota).toContain(`raise HTTPException(status_code=400, detail="${SETUP_EXPIRADO}")`);
  });

  it("Abrir no app autenticador: chama o Linking com a uri; se falhar, pede para copiar a chave", async () => {
    rotear({ "/auth/mfa/setup": () => resposta(200, SETUP) });
    const abrir = jest.mocked(Linking.openURL).mockRejectedValueOnce(new Error("sem app"));
    const tela = montar();
    await passarDaSenha(tela);

    await act(async () => {
      fireEvent.press(tela.getByRole("button", { name: "Abrir no app autenticador" }));
      await respirar();
    });

    expect(abrir).toHaveBeenCalledWith(SETUP.uri);
    expect(tela.getByText(/Copie a chave acima/)).toBeTruthy();
  });
});

describe("AtivarMfa — resposta de outra conta (RequisicaoSuperada)", () => {
  // Outra conta assume o cofre com o pedido no ar: o cliente devolve
  // `RequisicaoSuperada`, a tela NÃO sai, e tem de voltar a aceitar toque.
  const outraConta = { access: "access-b", refresh: "rt_b" };

  it("no enable: volta ao QR com o Ativar habilitado, sem aviso, e aoFalhar uma vez", async () => {
    rotear({
      "/auth/mfa/setup": () => resposta(200, SETUP),
      "/auth/mfa/enable": async () => {
        await guardarCredenciais(outraConta);
        return resposta(400, { detail: "Código inválido. Tente novamente." });
      },
    });
    const tela = montar();
    await passarDaSenha(tela);

    await act(async () => {
      fireEvent.changeText(tela.getByLabelText("Código de 6 dígitos"), "123456");
      await respirar();
    });

    expect(tela.getByRole("button", { name: "Ativar" }).props.accessibilityState).toMatchObject({ disabled: false, busy: false });
    expect(tela.queryByText("Código inválido. Tente novamente.")).toBeNull();
    expect(aoFalhar).toHaveBeenCalledTimes(1);
  });

  it("no setup: volta ao passo da senha com o Continuar habilitado, sem aviso", async () => {
    rotear({
      "/auth/mfa/setup": async () => {
        await guardarCredenciais(outraConta);
        return resposta(401, { detail: "Senha incorreta." });
      },
    });
    const tela = montar();

    await passarDaSenha(tela);

    expect(tela.getByRole("button", { name: "Continuar" }).props.accessibilityState).toMatchObject({ disabled: false, busy: false });
    expect(tela.queryByText("Senha incorreta.")).toBeNull();
  });
});
