/**
 * Exportar meus dados (C1), pelo roteador de verdade. Os controles negativos estão no relato do PR.
 */
import { act, fireEvent, screen, waitFor } from "expo-router/testing-library";

import { lerCredenciais } from "@/storage/secure";

import { resposta, S, segurar } from "./auth_apoio";
import { abrir, chamadasA, digitar, esperarBotao, MASCARADO, sessaoMorta, tocar } from "./conta_apoio";
import { drenar, preparar } from "./seguranca_sem_senha_apoio";

const EXPORT = "/auth/account/export";
const ENVIADO = { status: "email_sent", expires_in_minutes: 15, email_mascarado: MASCARADO };
const BOTAO = "Enviar para meu e-mail";

beforeEach(() => {
  preparar();
  jest.restoreAllMocks();
});

const abrirExportar = (me: object = {}, extra: Parameters<typeof abrir>[2] = {}) => abrir("/exportar-dados", me, extra).then(() => esperarBotao(BOTAO));

describe("Exportar meus dados", () => {
  it("conta com senha: o botão só habilita com a senha, e o corpo é {password}; o sucesso mostra o e-mail mascarado", async () => {
    await abrirExportar({}, { [EXPORT]: () => resposta(200, ENVIADO) });
    expect(screen.getByRole("button", { name: BOTAO }).props.accessibilityState).toMatchObject({ disabled: true });

    digitar("Senha", "s3nha");
    await tocar(BOTAO);

    await waitFor(() => expect(screen.getByText(`Enviado para ${MASCARADO}. Vale 15 min.`)).toBeTruthy());
    expect(chamadasA(EXPORT)).toEqual([expect.objectContaining({ corpo: { password: "s3nha" } })]);
    expect(screen.queryByRole("button", { name: BOTAO })).toBeNull();
  });

  it("conta sem senha (Google/Apple): sem campo Senha, e o corpo vai sem `password`", async () => {
    await abrirExportar({ has_password: false }, { [EXPORT]: () => resposta(200, ENVIADO) });
    expect(screen.queryByLabelText("Senha")).toBeNull();

    await tocar(BOTAO);

    await waitFor(() => expect(screen.getByText(`Enviado para ${MASCARADO}. Vale 15 min.`)).toBeTruthy());
    expect(chamadasA(EXPORT)[0]!.corpo).toEqual({});
  });

  it("401 'Senha incorreta.' SEM a marca não derruba a sessão: Banner, senha limpa, sem /auth/refresh", async () => {
    await abrirExportar({}, { [EXPORT]: () => resposta(401, { detail: "Senha incorreta." }) });

    digitar("Senha", "errada");
    await tocar(BOTAO);

    await waitFor(() => expect(screen.getByText("Senha incorreta.")).toBeTruthy());
    expect(screen.getByLabelText("Senha").props.value).toBe("");
    expect(chamadasA("/auth/refresh")).toEqual([]);
    await expect(lerCredenciais()).resolves.toEqual(S);
    expect(screen).toHavePathname("/exportar-dados");
  });

  it("positivo: 401 COM a marca e renovação recusada é sessão encerrada: vai para /entrar", async () => {
    await abrirExportar({}, { [EXPORT]: sessaoMorta, "/auth/refresh": () => resposta(401, { detail: "invalid_refresh_token" }) });

    digitar("Senha", "s3nha");
    await tocar(BOTAO);

    await waitFor(() => expect(screen).toHavePathname("/entrar"));
  });

  it("429 do cooldown vira Banner com o texto do servidor, e o botão volta", async () => {
    await abrirExportar({}, { [EXPORT]: () => resposta(429, { detail: "Já existe um link de exportação pendente enviado para o seu e-mail." }) });

    digitar("Senha", "s3nha");
    await tocar(BOTAO);

    await waitFor(() => expect(screen.getByText("Já existe um link de exportação pendente enviado para o seu e-mail.")).toBeTruthy());
    expect(screen.getByRole("button", { name: BOTAO }).props.accessibilityState).not.toMatchObject({ disabled: true });
  });

  it("duplo toque: 1 pedido (o teto é 3/h)", async () => {
    const seg = segurar();
    await abrirExportar({ has_password: false }, { [EXPORT]: async () => { await seg.promessa; return resposta(200, ENVIADO); } });
    const botao = screen.getByRole("button", { name: BOTAO });

    await act(async () => { fireEvent.press(botao); fireEvent.press(botao); await drenar(); });

    expect(chamadasA(EXPORT)).toHaveLength(1);
    await act(async () => { seg.soltar(); await drenar(); });
  });

  it("sem credencial: link para definir a senha e nenhum botão de enviar", async () => {
    await abrir("/exportar-dados", { precisa_criar_senha: true });
    await waitFor(() => expect(screen.getByText("Definir senha")).toBeTruthy());
    expect(screen.queryByRole("button", { name: BOTAO })).toBeNull();
  });

  it("Configurações: 'Exportar meus dados' abre a sheet", async () => {
    await abrir("/configuracoes");
    await esperarBotao("Exportar meus dados");

    await tocar("Exportar meus dados");

    await waitFor(() => expect(screen).toHavePathname("/exportar-dados"));
  });
});
