/**
 * A ligação JS ↔ tampa nativa (`features/bloqueio/tampa.ts`, dublado no
 * `jest.setup.js`): quando o JS manda descobrir, e o `pular` que segura a tampa
 * fora dos NOSSOS prompts de Face ID. O que a tampa faz no aparelho só se vê no
 * simulador; aqui se prova a ordem das chamadas.
 */
import * as LA from "expo-local-authentication";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";

import { descobrir, pular } from "@/features/bloqueio/tampa";
import { guardarCredenciais } from "@/storage/secure";

import { prepararCaso, resposta, rotear, S, segurar } from "./auth_apoio";

declare const global: typeof globalThis & { __dispararAppState: (v: string) => void; __definirAppState: (v: string) => void };

const OLA = "Bom dia, S";
const drenar = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
};

let pendentes: ((r: LA.LocalAuthenticationResult) => void)[] = [];
const prompts = () => jest.mocked(LA.authenticateAsync).mock.calls.length;
const travaNaTela = () => screen.queryByRole("header", { name: "PigBank" }) !== null;
/** Ordem global de chamada (`invocationCallOrder`) de cada `pular(v)`. */
const ordemDoPular = (v: boolean) =>
  jest.mocked(pular).mock.calls.flatMap(([x], i) => (x === v ? [jest.mocked(pular).mock.invocationCallOrder[i]!] : []));
const ordemDoPrompt = () => jest.mocked(LA.authenticateAsync).mock.invocationCallOrder;

async function appVai(...valores: string[]) {
  await act(async () => {
    for (const v of valores) global.__dispararAppState(v);
    await drenar();
  });
}

async function responder(r: LA.LocalAuthenticationResult) {
  await act(async () => {
    pendentes.shift()!(r);
    await drenar();
  });
}

beforeEach(() => {
  prepararCaso();
  rotear({ "/auth/mfa/status": () => resposta(200, { enabled: false, has_pending_secret: false, backup_codes_remaining: 0 }) });
  pendentes = [];
  global.__definirAppState("active");
  jest.mocked(descobrir).mockReset();
  jest.mocked(pular).mockReset().mockResolvedValue(undefined);
  jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(LA.SecurityLevel.BIOMETRIC);
  jest.mocked(LA.supportedAuthenticationTypesAsync).mockResolvedValue([LA.AuthenticationType.FACIAL_RECOGNITION]);
  jest.mocked(LA.authenticateAsync).mockReset().mockImplementation(() => new Promise((r) => pendentes.push(r)));
});

afterEach(() => {
  jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(LA.SecurityLevel.NONE);
  jest.mocked(LA.supportedAuthenticationTypesAsync).mockResolvedValue([]);
  jest.mocked(LA.authenticateAsync).mockReset().mockResolvedValue({ success: true });
});

async function liberado() {
  await guardarCredenciais(S);
  renderRouter("./app", { initialUrl: "/" });
  await waitFor(() => expect(prompts()).toBe(1));
  await responder({ success: true });
  await waitFor(() => expect(screen.getByText(OLA)).toBeTruthy());
}

describe("tampa — quando o JS descobre", () => {
  it("(a) a montagem do provider descobre uma vez, sem esperar evento", async () => {
    renderRouter("./app", { initialUrl: "/boas-vindas" });
    await waitFor(() => expect(screen).toHavePathname("/boas-vindas"));
    expect(descobrir).toHaveBeenCalledTimes(1);
  });

  it("(b) inactive e active no MESMO render: descobre de novo", async () => {
    await liberado();
    jest.mocked(descobrir).mockClear();
    await appVai("inactive", "active");
    expect(descobrir).toHaveBeenCalledTimes(1);
  });

  it("(c) volta de 61 s: quando descobre, a trava JS já está na tela", async () => {
    await liberado();
    const naHora: boolean[] = [];
    jest.mocked(descobrir).mockImplementation(() => void naHora.push(travaNaTela()));
    await appVai("inactive", "background");
    jest.setSystemTime(Date.now() + 61_000);
    await appVai("active");
    expect(naHora).toEqual([true]);
  });
});

describe("tampa — pular durante os nossos prompts", () => {
  it.each([
    ["sucesso", { success: true } as const, false],
    ["cancelar", { success: false, error: "user_cancel" } as const, true],
  ])("(d) abertura, %s: pular(true) ANTES do prompt, pular(false) depois", async (_nome, desfecho, travado) => {
    const portao = segurar();
    jest.mocked(pular).mockImplementation((v) => (v ? portao.promessa : Promise.resolve()));
    await guardarCredenciais(S);
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(pular).toHaveBeenCalledWith(true));
    await act(drenar);
    expect(prompts()).toBe(0);

    await act(async () => {
      portao.soltar();
      await drenar();
    });
    expect(prompts()).toBe(1);
    await responder(desfecho);

    expect(ordemDoPular(false)).toHaveLength(1);
    expect(ordemDoPular(false)[0]).toBeGreaterThan(ordemDoPrompt()[0]!);
    expect(travaNaTela()).toBe(travado);
  });

  it("(d) abertura, prompt que REJEITA: pular(false) do mesmo jeito, e fica travado", async () => {
    jest.mocked(LA.authenticateAsync).mockImplementation(() => Promise.reject(new Error("LAContext caiu")));
    await guardarCredenciais(S);
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(ordemDoPular(false)).toHaveLength(1));
    expect(ordemDoPular(true)[0]).toBeLessThan(ordemDoPrompt()[0]!);
    await waitFor(() => expect(screen.getByRole("button", { name: "Desbloquear" })).toBeTruthy());
  });

  it("(d) desligar na Segurança: pular(true) antes do prompt, pular(false) depois", async () => {
    await guardarCredenciais(S);
    renderRouter("./app", { initialUrl: "/seguranca" });
    await waitFor(() => expect(prompts()).toBe(1));
    await responder({ success: true });
    await waitFor(() => expect(screen.getByRole("switch", { name: "Desbloqueio com Face ID" })).toBeTruthy());
    jest.mocked(pular).mockClear();
    jest.mocked(LA.authenticateAsync).mockClear();

    await act(async () => {
      fireEvent(screen.getByRole("switch", { name: "Desbloqueio com Face ID" }), "valueChange", false);
      await drenar();
    });
    expect(ordemDoPular(true)[0]).toBeLessThan(ordemDoPrompt()[0]!);
    await responder({ success: false, error: "user_cancel" });
    expect(ordemDoPular(false)[0]).toBeGreaterThan(ordemDoPrompt()[0]!);
  });

  it("pular rejeitando (nativo falhou) não impede o prompt, e o sucesso libera", async () => {
    jest.mocked(pular).mockRejectedValue(new Error("nativo recusou"));
    await liberado();
    expect(travaNaTela()).toBe(false);
  });
});
