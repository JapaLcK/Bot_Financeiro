/**
 * O verifier PKCE (#604) pela TELA: de "Continuar com Google" até o `complete-signup`,
 * passando por erro no servidor, validação local, "Voltar" e digitação com aviso na tela
 * (`aplicar({ ...estado, aviso: undefined })` em `CompletarCadastroSocial`). Em todos os
 * POST, S256(code_verifier) tem de ser o challenge do start DAQUELA tentativa.
 * Controle negativo (medido): reconstruir o estado sem `verificador` nesse `aplicar`
 * deixa o último caso vermelho.
 */
jest.mock("react-native/Libraries/Utilities/useColorScheme", () => ({ __esModule: true, default: () => "light" }));
import { createHash } from "crypto";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";

import { chamadas, credencialDe, prepararCaso, resposta } from "./auth_apoio";
import { abrirFolha, rotasGoogle, voltaDoGoogle } from "./google_apoio";

const s256 = (v: string) => createHash("sha256").update(v).digest("base64url");
const respirar = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
const botao = (n: string) => screen.getByRole("button", { name: n });
const campo = (r: string) => screen.getByLabelText(new RegExp(`^${r}`));
const cadastros = () => chamadas().filter((c) => c.caminho === "/auth/google/complete-signup");
const desafio = () => /code_challenge=([^&]*)$/.exec(abrirFolha.mock.calls.at(-1)![0])![1];

beforeEach(() => { prepararCaso(); voltaDoGoogle("pigbank://auth?onboarding=gso_bia"); });

async function irAoCadastro() {
  renderRouter("./app", { initialUrl: "/entrar" });
  await waitFor(() => expect(screen).toHavePathname("/entrar"));
  await waitFor(() => botao("Continuar com Google"));
  await act(async () => { fireEvent.press(botao("Continuar com Google")); await respirar(); });
  await waitFor(() => campo("WhatsApp"));
}
async function criar(tel: string) {
  fireEvent.changeText(campo("WhatsApp"), tel);
  await act(async () => { fireEvent.press(botao("Criar conta")); await respirar(); });
}

it("UI: 400 de telefone no servidor, depois sucesso: as DUAS requisições levam o verifier do start (S256 == challenge)", async () => {
  let n = 0;
  rotasGoogle({ "/auth/google/complete-signup": () => (++n === 1 ? resposta(400, { detail: "Informe um número de WhatsApp válido com DDD." }) : resposta(200, credencialDe("bia@gmail.com"))) });
  await irAoCadastro();
  const ch = desafio();
  await criar("(11) 99999-8888");
  expect(screen.getByText("Informe um número de WhatsApp válido com DDD.")).toBeTruthy();
  await criar("(11) 99999-8887");
  await waitFor(() => expect(cadastros()).toHaveLength(2));
  for (const c of cadastros()) expect(s256((c.corpo as { code_verifier: string }).code_verifier)).toBe(ch);
});

it("UI: telefone inválido no cliente (sem requisição), depois válido: leva o verifier do start", async () => {
  rotasGoogle();
  await irAoCadastro();
  const ch = desafio();
  await criar("99999-8888");
  expect(cadastros()).toEqual([]);
  await criar("(11) 99999-8888");
  await waitFor(() => expect(cadastros()).toHaveLength(1));
  expect(s256((cadastros()[0]!.corpo as { code_verifier: string }).code_verifier)).toBe(ch);
});

it("UI: Voltar e Google de novo: o complete-signup leva o verifier da SEGUNDA tentativa", async () => {
  rotasGoogle();
  await irAoCadastro();
  const c1 = desafio();
  fireEvent.press(botao("Voltar"));
  await waitFor(() => botao("Continuar com Google"));
  await act(async () => { fireEvent.press(botao("Continuar com Google")); await respirar(); });
  await waitFor(() => campo("WhatsApp"));
  const c2 = desafio();
  expect(c2).not.toBe(c1);
  await criar("(11) 99999-8888");
  await waitFor(() => expect(cadastros()).toHaveLength(1));
  expect(s256((cadastros()[0]!.corpo as { code_verifier: string }).code_verifier)).toBe(c2);
});

it("UI: digitar com aviso na tela (aplicar ...estado) não perde o verifier", async () => {
  let n = 0;
  rotasGoogle({ "/auth/google/complete-signup": () => (++n === 1 ? resposta(400, { detail: "Informe um número de WhatsApp válido com DDD." }) : resposta(200, credencialDe("bia@gmail.com"))) });
  await irAoCadastro();
  const ch = desafio();
  await criar("(11) 99999-8888");
  fireEvent.changeText(campo("Nome"), "Bia Silva");   // digita com aviso: aplicar({...estado, aviso: undefined})
  fireEvent.changeText(campo("WhatsApp"), "(11) 99999-8887");
  await act(async () => { fireEvent.press(botao("Criar conta")); await respirar(); });
  await waitFor(() => expect(cadastros()).toHaveLength(2));
  expect(s256((cadastros()[1]!.corpo as { code_verifier: string }).code_verifier)).toBe(ch);
});
