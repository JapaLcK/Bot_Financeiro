/**
 * Leitor (credencial de visualização): o que o painel ESCONDE dele, uma verificação por guarda, cada uma com o dono como controle
 * POSITIVO. NEGATIVOS medidos tirando o guard (cada um deixa um caso verde do leitor vermelho): provider.tsx (filtro do simulador
 * no layout), telas.tsx (filtro em TelaBlocos), contasResumo.tsx (`bloco || leitor` na linha), receitas.tsx (`leitor ||`),
 * cabecalho.tsx (seletor de perfil), corpoAvisoBanco.tsx (ramo do leitor). `SimularCompra` tem o guard repetido pelo filtro de
 * TelaBlocos: o botão só é isolado pelo teste de unidade da folha; aqui os dois juntos.
 */
import { screen, waitFor } from "expo-router/testing-library";

import { NO_MES_DO_TESTE, prepararCaso } from "./auth_apoio";
import { abrir } from "./leitor_apoio";
import { desligarTrava } from "./open_finance_volta_rota_apoio";

jest.setTimeout(30_000);
beforeEach(() => { NO_MES_DO_TESTE(); prepararCaso(); desligarTrava(); });
afterEach(() => jest.restoreAllMocks());

describe("Resumo", () => {
  it("leitor: sem o Simulador no layout, sem as receitas do extrato, sem o seletor de perfil e sem a linha de banco clicável", async () => {
    await abrir(true, "/resumo");
    await waitFor(() => expect(screen.getByText("Nubank")).toBeTruthy());
    expect(screen.queryByText("Simulador")).toBeNull();
    expect(screen.queryByText("Salário Acme")).toBeNull();
    expect(screen.queryByRole("button", { name: /Padrão/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Nubank/ })).toBeNull();
  });

  it("controle positivo: o dono vê o Simulador, as receitas, o seletor de perfil e a linha de banco clicável", async () => {
    await abrir(false, "/resumo");
    await waitFor(() => expect(screen.getByText("Nubank")).toBeTruthy());
    expect(screen.getByText("Simulador")).toBeTruthy();
    expect(screen.getByText("Salário Acme")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Padrão/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Nubank/ })).toBeTruthy();
  });
});

describe("Gastos", () => {
  it("leitor: sem o bloco Simulador e sem 'Simular uma compra'", async () => {
    await abrir(true, "/gastos");
    await waitFor(() => expect(screen.getByText("Para onde vai")).toBeTruthy());
    expect(screen.queryByText("Simulador")).toBeNull();
    expect(screen.queryByRole("button", { name: "Simular uma compra" })).toBeNull();
  });

  it("controle positivo: o dono (plano Pro) vê o bloco e o botão", async () => {
    await abrir(false, "/gastos");
    await waitFor(() => expect(screen.getByText("Para onde vai")).toBeTruthy());
    expect(screen.getByText("Simulador")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Simular uma compra" })).toBeTruthy();
  });
});
