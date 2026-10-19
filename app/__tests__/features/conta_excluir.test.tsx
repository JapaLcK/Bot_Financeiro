/**
 * Excluir conta (C1), pelo roteador de verdade: o cliente e o cofre são os reais, só `fetch`, `Alert` e SecureStore são dublês.
 * Os controles negativos (mutante, vermelho, restaura) estão no relato do PR.
 */
import { router } from "expo-router";
import { act, fireEvent, screen, waitFor } from "expo-router/testing-library";
import { Alert } from "react-native";

import { NAO_CONFIRMOU } from "@/features/conta/exclusao";
import { chaveLayout, PERFIS } from "@/features/painel/catalogo";
import { guardarCredenciais, lerCredenciais } from "@/storage/secure";

import { chamadas, cofre, falharApagar, GENERICO, resposta, S, segurar } from "./auth_apoio";
import { abrir, chamadasA, digitar, esperarBotao, MASCARADO, metodoDe, responderAlerta, sessaoMorta, tocar } from "./conta_apoio";
import { drenar, preparar } from "./seguranca_sem_senha_apoio";

const CAMPO_TOTP = "Código do app ou de backup";
const AVISO_GENERICO = "Tivemos um problema aqui. Tente de novo em instantes.";

beforeEach(() => {
  preparar();
  jest.restoreAllMocks();
});

const abrirExcluir = (me: object = {}, extra: Parameters<typeof abrir>[2] = {}) => abrir("/excluir-conta", me, extra).then(() => esperarBotao("Excluir conta"));

/** Preenche e toca "Excluir conta" (o alerta já foi armado por `responderAlerta`). */
async function excluir(campos: Record<string, string> = { Senha: "s3nha", "Código do e-mail": "123456" }) {
  for (const [rotulo, valor] of Object.entries(campos)) digitar(rotulo, valor);
  await tocar("Excluir conta");
}

const layouts = (uid: number) => PERFIS.map((p) => cofre.has(chaveLayout(uid, p.id)));
const semearLayouts = () => {
  for (const uid of [7, 8]) for (const p of PERFIS) cofre.set(chaveLayout(uid, p.id), '["contas"]');
};
const DELETE = "/auth/account";

describe("Excluir conta — confirmação", () => {
  it("cancelar o Alert nativo não chama o DELETE (nem sai da conta)", async () => {
    await abrirExcluir({}, { [DELETE]: () => resposta(200, { status: "deleted" }) });
    const alerta = responderAlerta("Cancelar");

    await excluir();

    expect(alerta).toHaveBeenCalledWith("Excluir a conta?", "Não dá para desfazer.", expect.any(Array), expect.anything());
    expect(chamadasA(DELETE)).toEqual([]);
    await expect(lerCredenciais()).resolves.toEqual(S);
  });
});

describe("Excluir conta — erro de credencial não encerra a sessão", () => {
  it("401 'Senha incorreta.' SEM a marca: cofre fica, sem /auth/refresh, Banner, senha limpa, ainda na sheet", async () => {
    await abrirExcluir({}, { [DELETE]: () => resposta(401, { detail: "Senha incorreta." }) });
    responderAlerta("Excluir");

    await excluir();

    await waitFor(() => expect(screen.getByText("Senha incorreta.")).toBeTruthy());
    expect(screen.getByLabelText("Senha").props.value).toBe("");
    expect(screen.getByLabelText("Código do e-mail").props.value).toBe("123456");
    expect(chamadasA("/auth/refresh")).toEqual([]);
    expect(chamadasA("/auth/logout")).toEqual([]);
    await expect(lerCredenciais()).resolves.toEqual(S);
    expect(screen).toHavePathname("/excluir-conta");
  });

  it("positivo: 401 COM a marca e renovação recusada é sessão encerrada: vai para /entrar", async () => {
    await abrirExcluir({}, { [DELETE]: sessaoMorta, "/auth/refresh": () => resposta(401, { detail: "invalid_refresh_token" }) });
    responderAlerta("Excluir");

    await excluir();

    await waitFor(() => expect(screen).toHavePathname("/entrar"));
    expect(screen.getByText("Sua sessão expirou. Entre de novo.")).toBeTruthy();
  });
});

describe("Excluir conta — sucesso", () => {
  it("DELETE com a constante, senha e código; sai da conta, apaga só os layouts DESTA conta e avisa", async () => {
    await abrirExcluir({}, { [DELETE]: () => resposta(200, { ok: true, status: "deleted", deleted: true }) });
    semearLayouts();
    const alerta = responderAlerta("Excluir");

    await excluir();

    await waitFor(() => expect(alerta).toHaveBeenCalledWith("Conta excluída"));
    expect(metodoDe(DELETE)).toBe("DELETE");
    expect(chamadasA(DELETE)).toEqual([expect.objectContaining({ corpo: { confirmacao: "EXCLUIR", password: "s3nha", code: "123456" } })]);
    expect(chamadasA("/auth/logout")).toHaveLength(1);
    await expect(lerCredenciais()).resolves.toBeNull();
    expect(layouts(7)).toEqual(PERFIS.map(() => false));
    expect(layouts(8)).toEqual(PERFIS.map(() => true));
    await waitFor(() => expect(screen).toHavePathname("/boas-vindas"));
  });

  it("conta com MFA: o autenticador já está na tela e vai como `totp`", async () => {
    await abrirExcluir({ mfa_enabled: true }, { [DELETE]: () => resposta(200, { status: "deleted" }) });
    responderAlerta("Excluir");

    await excluir({ Senha: "s3nha", "Código do e-mail": "123456", [CAMPO_TOTP]: " 654321 " });

    await waitFor(() => expect(chamadasA("/auth/logout")).toHaveLength(1));
    expect(chamadasA(DELETE)[0]!.corpo).toEqual({ confirmacao: "EXCLUIR", password: "s3nha", code: "123456", totp: "654321" });
  });

  it("200 com corpo inesperado (sem `status`) conta como excluída: sai e avisa", async () => {
    await abrirExcluir({}, { [DELETE]: () => resposta(200, { message: "ok" }) });
    const alerta = responderAlerta("Excluir");

    await excluir();

    await waitFor(() => expect(alerta).toHaveBeenCalledWith("Conta excluída"));
    expect(chamadasA("/auth/logout")).toHaveLength(1);
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("`status: deleting`: sai da conta e avisa que o e-mail chega ao terminar", async () => {
    await abrirExcluir({}, { [DELETE]: () => resposta(200, { status: "deleting", deleted: false }) });
    const alerta = responderAlerta("Excluir");

    await excluir();

    await waitFor(() => expect(alerta).toHaveBeenCalledWith("Excluindo", "Você recebe um e-mail ao terminar."));
    await expect(lerCredenciais()).resolves.toBeNull();
  });
});

describe("Excluir conta — falhas que deixam a conta de pé", () => {
  it.each([
    ["409 export_pendente", 409, { detail: { code: "export_pendente", message: "Baixe sua cópia dos dados pelo link do e-mail antes de excluir." } }, "Baixe sua cópia dos dados pelo link do e-mail antes de excluir."],
    ["503 stripe_indisponivel", 503, { detail: { code: "stripe_indisponivel", message: "x" } }, AVISO_GENERICO],
    ["400 confirmacao_invalida", 400, { detail: { code: "confirmacao_invalida", message: "Digite EXCLUIR para confirmar." } }, "Algo deu errado. Tente de novo."],
  ])("%s: Banner, sem sair, cofre fica e o botão volta", async (_nome, status, corpo, texto) => {
    await abrirExcluir({}, { [DELETE]: () => resposta(status, corpo) });
    responderAlerta("Excluir");

    await excluir();

    await waitFor(() => expect(screen.getByText(texto)).toBeTruthy());
    expect(screen.queryByText("Digite EXCLUIR para confirmar.")).toBeNull();
    expect(chamadasA("/auth/logout")).toEqual([]);
    await expect(lerCredenciais()).resolves.toEqual(S);
    expect(screen.getByRole("button", { name: "Excluir conta" }).props.accessibilityState).not.toMatchObject({ disabled: true });
  });

  it("rede caiu no DELETE: não afirma nada sobre a conta, só manda conferir o e-mail", async () => {
    await abrirExcluir({}, { [DELETE]: () => Promise.reject(new TypeError("Network request failed")) });
    responderAlerta("Excluir");

    await excluir();

    await waitFor(() => expect(screen.getByText("Não deu para confirmar. Se a conta foi excluída, chega um e-mail.")).toBeTruthy());
    await expect(lerCredenciais()).resolves.toEqual(S);
  });

  it("tempo limite no DELETE: a conta pode existir, então os layouts ficam", async () => {
    await abrirExcluir({}, { [DELETE]: () => Promise.reject(Object.assign(new Error("aborted"), { name: "AbortError" })) });
    semearLayouts();
    responderAlerta("Excluir");

    await excluir();

    await waitFor(() => expect(screen.getByText(NAO_CONFIRMOU)).toBeTruthy());
    expect(layouts(7)).toEqual(PERFIS.map(() => true));
  });

  it("429 no DELETE: Banner com o texto do servidor, sem sair", async () => {
    await abrirExcluir({}, { [DELETE]: () => resposta(429, { detail: "Muitas tentativas. Tente de novo mais tarde." }) });
    responderAlerta("Excluir");

    await excluir();

    await waitFor(() => expect(screen.getByText("Muitas tentativas. Tente de novo mais tarde.")).toBeTruthy());
    expect(chamadasA("/auth/logout")).toEqual([]);
  });
});

describe("Excluir conta — guardas e estado da tela", () => {
  it("duplo toque: 1 alerta e 1 DELETE", async () => {
    const seg = segurar();
    await abrirExcluir({}, { [DELETE]: async () => { await seg.promessa; return resposta(200, { status: "deleted" }); } });
    const alerta = responderAlerta("Excluir");
    digitar("Senha", "s3nha");
    digitar("Código do e-mail", "123456");
    const botao = screen.getByRole("button", { name: "Excluir conta" });

    await act(async () => { fireEvent.press(botao); fireEvent.press(botao); fireEvent.press(botao); await drenar(); });

    expect(alerta).toHaveBeenCalledTimes(1);
    expect(chamadasA(DELETE)).toHaveLength(1);
    await act(async () => { seg.soltar(); await drenar(); });
  });

  it("o botão só habilita com senha e código de 6 dígitos", async () => {
    await abrirExcluir();
    const habilitado = () => screen.getByRole("button", { name: "Excluir conta" }).props.accessibilityState.disabled === false;
    digitar("Senha", "s3nha");
    digitar("Código do e-mail", "12345");
    expect(habilitado()).toBe(false);
    digitar("Código do e-mail", "123456");
    expect(habilitado()).toBe(true);
    digitar("Senha", "");
    expect(habilitado()).toBe(false);
  });

  it("DELETE em voo prende a sheet: voltar não sai; o 5xx solta", async () => {
    const seg = segurar();
    await abrirExcluir({}, { [DELETE]: async () => { await seg.promessa; return resposta(503, { detail: { code: "stripe_indisponivel", message: "x" } }); } });
    responderAlerta("Excluir");

    await excluir();
    await act(async () => { router.back(); await drenar(); });
    expect(screen).toHavePathname("/excluir-conta");

    await act(async () => { seg.soltar(); await drenar(); });
    await waitFor(() => expect(screen.getByText(AVISO_GENERICO)).toBeTruthy());
    await act(async () => { router.back(); await drenar(); });
    await waitFor(() => expect(screen).not.toHavePathname("/excluir-conta"));
  });

  it("o DELETE tem o limite de 60 s (Stripe e Pluggy na mesma requisição)", async () => {
    await abrirExcluir({}, { [DELETE]: () => resposta(503, { detail: { code: "stripe_indisponivel", message: "x" } }) });
    responderAlerta("Excluir");
    const prazos = jest.spyOn(globalThis, "setTimeout");

    await excluir();

    expect(prazos.mock.calls.map((c) => c[1])).toContain(60_000);
  });

  it("sair() recusado: o spinner para, o botão fica desabilitado, a sheet solta e o Sair repete só a saída", async () => {
    await abrirExcluir({}, { [DELETE]: () => resposta(200, { status: "deleted" }) });
    responderAlerta("Excluir");
    falharApagar(true);

    await excluir();

    await waitFor(() => expect(screen.getByText("Não conseguimos sair. Tente de novo.")).toBeTruthy());
    expect(screen.getByRole("button", { name: "Excluir conta" }).props.accessibilityState).toMatchObject({ disabled: true, busy: false });
    await act(async () => { router.back(); await drenar(); });
    await waitFor(() => expect(screen).not.toHavePathname("/excluir-conta"));
    falharApagar(false);
  });

  it("duplo toque no Sair do Banner: um aviso só", async () => {
    await abrirExcluir({}, { [DELETE]: () => resposta(200, { status: "deleted" }) });
    const alerta = responderAlerta("Excluir");
    falharApagar(true);
    await excluir();
    await waitFor(() => expect(screen.getByText("Não conseguimos sair. Tente de novo.")).toBeTruthy());
    falharApagar(false);
    const sair = screen.getByRole("button", { name: "Sair" });

    await act(async () => { fireEvent.press(sair); fireEvent.press(sair); await drenar(); });

    await waitFor(() => expect(alerta).toHaveBeenCalledWith("Conta excluída"));
    expect(alerta.mock.calls.filter((c) => c[0] === "Conta excluída")).toHaveLength(1);
  });
});

describe("Excluir conta — modos", () => {
  it("conta sem senha (Google/Apple): sem campo Senha, e o corpo vai sem `password`", async () => {
    await abrirExcluir({ has_password: false }, { [DELETE]: () => resposta(200, { status: "deleted" }) });
    expect(screen.queryByLabelText("Senha")).toBeNull();
    responderAlerta("Excluir");

    await excluir({ "Código do e-mail": "123456" });

    await waitFor(() => expect(chamadasA(DELETE)).toHaveLength(1));
    expect(chamadasA(DELETE)[0]!.corpo).toEqual({ confirmacao: "EXCLUIR", code: "123456" });
  });

  it("400 mfa_obrigatorio revela o autenticador, mantém o código do e-mail, e o reenvio leva `totp`", async () => {
    let vez = 0;
    await abrirExcluir({}, {
      [DELETE]: () => (++vez === 1 ? resposta(400, { detail: { code: "mfa_obrigatorio", message: "Informe o código do autenticador ou de backup." } }) : resposta(200, { status: "deleted" })),
    });
    expect(screen.queryByLabelText(CAMPO_TOTP)).toBeNull();
    responderAlerta("Excluir");

    await excluir();

    await waitFor(() => expect(screen.getByLabelText(CAMPO_TOTP)).toBeTruthy());
    expect(screen.getByText("Informe o código do autenticador ou de backup.")).toBeTruthy();
    expect(screen.getByLabelText("Código do e-mail").props.value).toBe("123456");
    expect(screen.getByRole("button", { name: "Excluir conta" }).props.accessibilityState).toMatchObject({ disabled: true });

    digitar(CAMPO_TOTP, "654321");
    await tocar("Excluir conta");
    await waitFor(() => expect(chamadasA(DELETE)).toHaveLength(2));
    expect(chamadasA(DELETE)[1]!.corpo).toEqual({ confirmacao: "EXCLUIR", password: "s3nha", code: "123456", totp: "654321" });
  });

  it("código do e-mail recusado: esvazia só o código", async () => {
    await abrirExcluir({}, { [DELETE]: () => resposta(401, { detail: { code: "codigo_invalido", message: "Código incorreto." } }) });
    responderAlerta("Excluir");

    await excluir();

    await waitFor(() => expect(screen.getByText("Código incorreto.")).toBeTruthy());
    expect(screen.getByLabelText("Código do e-mail").props.value).toBe("");
    expect(screen.getByLabelText("Senha").props.value).toBe("s3nha");
  });

  it("sem credencial (precisa_criar_senha): link para definir a senha e nenhum botão de excluir", async () => {
    await abrir("/excluir-conta", { precisa_criar_senha: true });
    await waitFor(() => expect(screen.getByText("Definir senha")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Excluir conta" })).toBeNull();
    expect(screen.queryByLabelText("Código do e-mail")).toBeNull();
  });
});

describe("Excluir conta — código por e-mail", () => {
  it("abrir a sheet não chama o reauth-code; o toque manda corpo vazio, mostra o e-mail mascarado e vira Reenviar", async () => {
    await abrirExcluir();
    expect(chamadasA("/auth/account/reauth-code")).toEqual([]);

    await tocar("Enviar código");

    await waitFor(() => expect(screen.getByText(`Enviado para ${MASCARADO}`)).toBeTruthy());
    expect(chamadasA("/auth/account/reauth-code")).toEqual([expect.objectContaining({ corpo: {} })]);
    expect(screen.getByRole("button", { name: "Reenviar" })).toBeTruthy();
  });

  it("duplo toque em Enviar código: 1 pedido", async () => {
    const seg = segurar();
    await abrirExcluir({}, { "/auth/account/reauth-code": async () => { await seg.promessa; return resposta(200, { status: "sent", email_mascarado: MASCARADO }); } });
    const botao = screen.getByRole("button", { name: "Enviar código" });

    await act(async () => { fireEvent.press(botao); fireEvent.press(botao); await drenar(); });

    expect(chamadasA("/auth/account/reauth-code")).toHaveLength(1);
    await act(async () => { seg.soltar(); await drenar(); });
  });

  it("Enviar código durante o alerta de confirmação do Excluir não pede código", async () => {
    await abrirExcluir();
    jest.spyOn(Alert, "alert").mockImplementation(() => undefined); // o alerta fica aberto
    digitar("Senha", "s3nha");
    digitar("Código do e-mail", "123456");
    await tocar("Excluir conta");

    await tocar("Enviar código");

    expect(chamadasA("/auth/account/reauth-code")).toEqual([]);
  });

  it("resposta tardia do pedido não apaga o código que a pessoa já digitou", async () => {
    const seg = segurar();
    await abrirExcluir({}, { "/auth/account/reauth-code": async () => { await seg.promessa; return resposta(200, { status: "sent", email_mascarado: MASCARADO }); } });
    await tocar("Enviar código");
    digitar("Código do e-mail", "123456");

    await act(async () => { seg.soltar(); await drenar(); });

    await waitFor(() => expect(screen.getByText(`Enviado para ${MASCARADO}`)).toBeTruthy());
    expect(screen.getByLabelText("Código do e-mail").props.value).toBe("123456");
  });

  it("com o pedido do código em voo, Excluir conta fica desabilitado", async () => {
    const seg = segurar();
    await abrirExcluir({}, { "/auth/account/reauth-code": async () => { await seg.promessa; return resposta(200, { status: "sent", email_mascarado: MASCARADO }); } });
    digitar("Senha", "s3nha");
    digitar("Código do e-mail", "123456");
    await tocar("Enviar código");

    expect(screen.getByRole("button", { name: "Excluir conta" }).props.accessibilityState).toMatchObject({ disabled: true });
    await act(async () => { seg.soltar(); await drenar(); });
  });

  it("429 do reauth-code vira Banner com o texto do servidor, sem sair", async () => {
    await abrirExcluir({}, { "/auth/account/reauth-code": () => resposta(429, { detail: "Muitas tentativas. Tente de novo mais tarde." }) });

    await tocar("Enviar código");

    await waitFor(() => expect(screen.getByText("Muitas tentativas. Tente de novo mais tarde.")).toBeTruthy());
    await expect(lerCredenciais()).resolves.toEqual(S);
    expect(screen.getByRole("button", { name: "Enviar código" })).toBeTruthy();
  });
});

describe("Excluir conta — leitura do perfil", () => {
  it("resposta de outra conta (cofre trocado no meio): erro com Tentar de novo, não o spinner eterno", async () => {
    const seg = segurar();
    await abrir("/excluir-conta", {}, { "/auth/me": async () => { await seg.promessa; return resposta(200, { user_id: 7, email: "ana@x.com", app_access: true }); } });
    await waitFor(() => expect(chamadasA("/auth/me").length).toBeGreaterThan(0));
    await guardarCredenciais({ access: "access-b", refresh: "rt_b" });

    await act(async () => { seg.soltar(); await drenar(); });

    await waitFor(() => expect(screen.getByRole("button", { name: "Tentar de novo" })).toBeTruthy());
    expect(screen.getByText(GENERICO)).toBeTruthy();
  });
});

describe("Configurações — grupo Conta", () => {
  it("'Excluir conta' abre a sheet, e abrir não gasta código", async () => {
    await abrir("/configuracoes");
    await esperarBotao("Excluir conta");
    expect(screen.getByRole("button", { name: "Exportar meus dados" })).toBeTruthy();

    await tocar("Excluir conta");

    await waitFor(() => expect(screen).toHavePathname("/excluir-conta"));
    await waitFor(() => expect(screen.getByLabelText("Código do e-mail")).toBeTruthy());
    expect(chamadasA("/auth/account/reauth-code")).toEqual([]);
    expect(chamadas().some((c) => c.caminho === DELETE)).toBe(false);
  });
});
