/**
 * O desregistro no APNs mora na limpeza do cofre (`apagarSessaoNoCofre`), não em
 * cada chamador: toda saída da sessão passa por lá. Grupo com os dois controles:
 * o NEGATIVO é tirar o gancho do `secure.ts` (todos os casos "desregistra" ficam
 * vermelhos); o POSITIVO são os casos em que NÃO se desregistra — sem eles, um
 * gancho que desregistrasse sempre passaria.
 */
import * as Notifications from "expo-notifications";

import { SessaoExpirada, _resetRenovacao, chamar } from "@/api/client";
import { guardarCredenciais, guardarCredenciaisSe, limparCredenciais, limparSe, limparSessaoDe } from "@/storage/secure";
import { z } from "zod";

import { cofre, falharApagar, fetchFalso, prepararCaso, respirar, resposta } from "./auth_apoio";

const ACCESS_A = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIiwianRpIjoic2Vzc2FvLUEifQ.assinatura";
const ACCESS_B = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIyIiwianRpIjoic2Vzc2FvLUIifQ.assinatura";
const A = { access: ACCESS_A, refresh: "rt_A" };
const B = { access: ACCESS_B, refresh: "rt_B" };

/** Permitido na 1ª pergunta e superado na 2ª (depois da gravação): dispara o desfazer. */
const superadoDepoisDeGravar = () => {
  let perguntas = 0;
  return () => ++perguntas === 1;
};

const desregistros = async () => {
  await respirar();
  return jest.mocked(Notifications.unregisterForNotificationsAsync).mock.calls.length;
};

beforeEach(() => {
  prepararCaso();
  jest.mocked(Notifications.unregisterForNotificationsAsync).mockClear();
});

describe("desregistra o aparelho quando a sessão sai do cofre", () => {
  it("limparCredenciais", async () => {
    await guardarCredenciais(A);
    await limparCredenciais();
    expect(await desregistros()).toBe(1);
  });

  it("limparSessaoDe da conta guardada (Sair e exclusão)", async () => {
    await guardarCredenciais(A);
    expect(await limparSessaoDe(ACCESS_A, "rt_A")).toBe(true);
    expect(await desregistros()).toBe(1);
  });

  it("limparSe com o refresh guardado (401 terminal do refresh)", async () => {
    await guardarCredenciais(A);
    expect(await limparSe("rt_A")).toBe(true);
    expect(await desregistros()).toBe(1);
  });

  it("401 terminal pelo chamar (refresh também 401) limpa o cofre e desregistra", async () => {
    await guardarCredenciais(A);
    _resetRenovacao();
    fetchFalso.mockImplementation(async () => resposta(401, { detail: "expirado" }));
    await expect(chamar("/auth/me", z.unknown())).rejects.toBeInstanceOf(SessaoExpirada);
    expect(cofre.size).toBe(0);
    expect(await desregistros()).toBe(1);
  });

  it("o desfazer de um login superado (cofre estava vazio) também", async () => {
    expect(await guardarCredenciaisSe(superadoDepoisDeGravar(), A)).toBe(false);
    expect(cofre.size).toBe(0);
    expect(await desregistros()).toBe(1);
  });
});

describe("e NÃO desregistra quando a sessão guardada fica", () => {
  it("limparSessaoDe de OUTRA conta: B segue no cofre e no APNs", async () => {
    await guardarCredenciais(B);
    expect(await limparSessaoDe(ACCESS_A, "rt_A")).toBe(false);
    expect(cofre.size).toBe(1);
    expect(await desregistros()).toBe(0);
  });

  it("limparSe com refresh de outra sessão", async () => {
    await guardarCredenciais(B);
    expect(await limparSe("rt_A")).toBe(false);
    expect(await desregistros()).toBe(0);
  });

  it("o cofre recusa apagar a credencial: sair() rejeita e o push continua", async () => {
    await guardarCredenciais(A);
    falharApagar(true);
    await expect(limparSessaoDe(ACCESS_A, "rt_A")).rejects.toThrow();
    expect(cofre.size).toBe(1);
    expect(await desregistros()).toBe(0);
  });

  it("o desfazer que RESTAURA outra sessão (cofre tinha B) não desregistra", async () => {
    await guardarCredenciais(B);
    expect(await guardarCredenciaisSe(superadoDepoisDeGravar(), A)).toBe(false);
    expect(await desregistros()).toBe(0);
  });

  it("falha do desregistro no APNs não derruba a limpeza", async () => {
    jest.mocked(Notifications.unregisterForNotificationsAsync).mockRejectedValueOnce(new Error("apns"));
    await guardarCredenciais(A);
    await expect(limparCredenciais()).resolves.toBeUndefined();
    expect(await desregistros()).toBe(1);
  });
});
