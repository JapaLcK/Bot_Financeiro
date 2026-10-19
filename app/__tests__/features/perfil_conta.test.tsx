/**
 * Perfil: a conta troca no cofre ENTRE o /auth/me e a leitura por uid (403 de uid alheio) — e o refresh legítimo da MESMA conta não conta como troca.
 * Controles negativos (mutante, vermelho, restaura) no relato do PR.
 */
import { act, screen, waitFor } from "expo-router/testing-library";

import * as servicosConta from "@/services/conta";

import { chamadas, credencialDe, resposta, segurar } from "./auth_apoio";
import { CONTATO, abrirPerfil } from "./perfil_apoio";
import { appVai, drenar, preparar } from "./seguranca_sem_senha_apoio";

const cofre = (globalThis as unknown as { __cofreDeTeste: Map<string, string> }).__cofreDeTeste;
const NOME_A = () => screen.queryByLabelText("Nome, Sofia Lima");
const soltar = () => act(async () => { for (let i = 0; i < 10; i++) await drenar(); });

beforeEach(preparar);
afterEach(() => jest.restoreAllMocks());

it("a conta troca ENTRE o /auth/me e a leitura: some o dado da anterior enquanto relê, e depois mostra a conta nova", async () => {
  let conta = 1, trocarAntes = false;
  const daB = (o: RequestInit) => /access-b/.test((o.headers as Record<string, string>)["Authorization"] ?? "");
  const presa = segurar();
  let prender = false;
  await abrirPerfil({}, {
    "/auth/me": () => {
      const r = () => resposta(200, { user_id: conta, email: "sofia@x.com", display_name: "Sofia", app_access: true });
      return prender ? presa.promessa.then(r) : r();
    },
    "/settings/1/security": (o) => (daB(o) ? resposta(403, { detail: "Acesso negado" }) : resposta(200, CONTATO)),
    "/settings/2/security": () => resposta(200, { ...CONTATO, user_id: 2, display_name: "Bia Souza", email: "bia@x.com" }),
  });
  await waitFor(() => expect(NOME_A()).toBeTruthy());
  const real = servicosConta.lerContato;
  jest.spyOn(servicosConta, "lerContato").mockImplementation(async (uid) => {
    if (trocarAntes) { // outra conta assume o cofre depois do /auth/me da conta 1 e antes da 2ª leitura
      trocarAntes = false;
      prender = true; // e a releitura da conta nova fica presa no /auth/me
      cofre.set("pb.credenciais", JSON.stringify({ access: "access-b", refresh: "rt_b" }));
      conta = 2;
    }
    return real(uid);
  });
  trocarAntes = true;
  await appVai("background");
  await appVai("active");
  await soltar();
  await waitFor(() => expect(chamadas().filter((c) => c.caminho === "/auth/me")).toHaveLength(3));
  expect(NOME_A()).toBeNull(); // enquanto a conta nova não chega, nada da anterior na tela
  await act(async () => { presa.soltar(); for (let i = 0; i < 10; i++) await drenar(); });
  await waitFor(() => expect(screen.getByLabelText("Nome, Bia Souza")).toBeTruthy());
  expect(NOME_A()).toBeNull();
  expect(chamadas().some((c) => c.caminho === "/settings/2/security")).toBe(true);
});

it("refresh legítimo da mesma conta + 500 na leitura com dado na tela: a tela mantém o dado", async () => {
  let expirou = false, quebrar = false;
  const velho = (o: RequestInit) => (o.headers as Record<string, string>)["Authorization"] === "Bearer access-s";
  await abrirPerfil({}, {
    "/auth/me": (o) => (expirou && velho(o) ? resposta(401, { detail: "expirado" })
      : resposta(200, { user_id: 1, email: "sofia@x.com", display_name: "Sofia", app_access: true })),
    "/auth/refresh": () => resposta(200, credencialDe("s2")),
    "/settings/1/security": () => (quebrar ? resposta(500, { detail: "boom" }) : resposta(200, CONTATO)),
  });
  await waitFor(() => expect(NOME_A()).toBeTruthy());
  expirou = quebrar = true;
  await appVai("background");
  await appVai("active");
  await soltar();
  await waitFor(() => expect(chamadas().filter((c) => c.caminho === "/auth/refresh")).toHaveLength(1));
  await waitFor(() => expect(chamadas().filter((c) => c.caminho === "/settings/1/security")).toHaveLength(2));
  await soltar();
  expect(NOME_A()).toBeTruthy();
  expect(screen.queryByText("Não conseguimos carregar seu perfil.")).toBeNull();
});
