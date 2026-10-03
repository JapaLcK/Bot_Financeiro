/**
 * A seção "Desbloqueio com Face ID" da Segurança, pelo roteador real: desligar
 * pede o Face ID antes de gravar (decisão Q1 do dono), ligar só apaga a chave,
 * e o switch mostra sempre o que está no cofre.
 */
import * as LA from "expo-local-authentication";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";

import { guardarCredenciais } from "@/storage/secure";

import { cofre, falharApagar, falharEscrita, prepararCaso, resposta, rotear, S } from "./auth_apoio";

const TRAVA = "pb.trava.desligada";
const ROTULO = "Desbloqueio com Face ID";
const ERRO = "Não conseguimos salvar sua escolha neste aparelho. Tente de novo.";
const MFA_DESLIGADO = { enabled: false, has_pending_secret: false, backup_codes_remaining: 0 };

const drenar = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
};
const prompts = () => jest.mocked(LA.authenticateAsync).mock.calls.length;
const chave = () => screen.getByRole("switch", { name: ROTULO });

async function mudarPara(valor: boolean) {
  await act(async () => {
    fireEvent(chave(), "valueChange", valor);
    await drenar();
  });
}

/** Abre a Segurança com sessão (o prompt da abertura responde sucesso). */
async function abrir(mfa: () => Response = () => resposta(200, MFA_DESLIGADO)) {
  await guardarCredenciais(S);
  rotear({ "/auth/mfa/status": mfa });
  renderRouter("./app", { initialUrl: "/seguranca" });
  await waitFor(() => expect(chave()).toBeTruthy());
}

beforeEach(() => {
  prepararCaso();
  jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(LA.SecurityLevel.BIOMETRIC);
  jest.mocked(LA.supportedAuthenticationTypesAsync).mockResolvedValue([LA.AuthenticationType.FACIAL_RECOGNITION]);
  jest.mocked(LA.authenticateAsync).mockClear().mockResolvedValue({ success: true });
});

afterEach(() => {
  jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(LA.SecurityLevel.NONE);
  jest.mocked(LA.supportedAuthenticationTypesAsync).mockResolvedValue([]);
});

describe("Segurança — Desbloqueio", () => {
  it("desligar pede o Face ID; cancelar deixa o switch ligado e a chave ausente", async () => {
    await abrir();
    expect(chave().props.value).toBe(true);
    const antes = prompts();
    jest.mocked(LA.authenticateAsync).mockResolvedValueOnce({ success: false, error: "user_cancel" });

    await mudarPara(false);

    expect(prompts()).toBe(antes + 1);
    expect(chave().props.value).toBe(true);
    expect(cofre.has(TRAVA)).toBe(false);
  });

  it("desligar com o Face ID confirmado grava a chave e desliga o switch", async () => {
    await abrir();
    await mudarPara(false);
    expect(cofre.get(TRAVA)).toBe("1");
    expect(chave().props.value).toBe(false);
  });

  it("ligar apaga a chave, sem pedir nada", async () => {
    cofre.set(TRAVA, "1");
    await abrir();
    expect(chave().props.value).toBe(false);
    const antes = prompts();

    await mudarPara(true);

    expect(cofre.has(TRAVA)).toBe(false);
    expect(chave().props.value).toBe(true);
    expect(prompts()).toBe(antes);
  });

  it("cofre recusando ao desligar: erro na seção e o switch continua ligado", async () => {
    await abrir();
    falharEscrita(true);
    await mudarPara(false);
    expect(screen.getByText(ERRO)).toBeTruthy();
    expect(chave().props.value).toBe(true);
  });

  it("cofre recusando ao ligar: erro na seção e o switch continua desligado (o disco manda)", async () => {
    cofre.set(TRAVA, "1");
    await abrir();
    falharApagar(true);
    await mudarPara(true);
    expect(screen.getByText(ERRO)).toBeTruthy();
    expect(chave().props.value).toBe(false);
    expect(cofre.get(TRAVA)).toBe("1");
  });

  it("aparelho sem código: switch desativado e o aviso", async () => {
    jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(LA.SecurityLevel.NONE);
    await guardarCredenciais(S);
    rotear({ "/auth/mfa/status": () => resposta(200, MFA_DESLIGADO) });
    renderRouter("./app", { initialUrl: "/seguranca" });
    await waitFor(() => expect(screen.getByText("Configure um código no aparelho para usar.")).toBeTruthy());
    const s = screen.getByRole("switch", { name: "Desbloqueio com o código do aparelho" });
    expect(s.props.disabled ?? s.props.accessibilityState?.disabled).toBe(true);
    expect(s.props.value).toBe(false);
  });

  it("erro no status do MFA não esconde a seção", async () => {
    await abrir(() => resposta(500, {}));
    await waitFor(() => expect(screen.getByRole("button", { name: "Tentar de novo" })).toBeTruthy());
    expect(chave()).toBeTruthy();
  });
});
