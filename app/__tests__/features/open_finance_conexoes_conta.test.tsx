// Conexões: a conta troca no cofre ENTRE o /auth/me e a leitura por uid (403 de uid alheio) — some o dado da anterior e relê a conta nova.
import { act, renderRouter, screen, waitFor } from "expo-router/testing-library";
import * as servicosOF from "@/services/openFinance";
import { guardarCredenciais } from "@/storage/secure";
import { chamadas, credencialDe, prepararCaso, resposta, rotear, S, segurar } from "./auth_apoio";
import { VIVO } from "./open_finance_volta_apoio";
import { appVai, desligarTrava, drenar } from "./open_finance_volta_rota_apoio";

beforeEach(async () => { prepararCaso(); desligarTrava(); await guardarCredenciais(S); });
afterEach(() => jest.restoreAllMocks());

const cofre = (globalThis as unknown as { __cofreDeTeste: Map<string, string> }).__cofreDeTeste;
const ITAU = { ...VIVO, id: 2, provider_item_id: "item_b", institution_name: "Itaú" };
const LIMITE = { ok: true, of_banks_max: 2, em_uso: 1, pode_adicionar: true, code: null, message: null };
const daB = (o: RequestInit) => /access-b/.test((o.headers as Record<string, string>)["Authorization"] ?? "");
const ne = (caminho: string) => chamadas().filter((c) => c.caminho === caminho).length;

function servidor(conta: () => number, limite403 = (o: RequestInit) => daB(o), extra: Record<string, (o: RequestInit) => Response | Promise<Response>> = {}) {
  rotear({
    "/auth/me": () => resposta(200, { user_id: conta(), email: "sofia@x.com", display_name: "Sofia", app_access: true }),
    "/open-finance/1": (o) => (daB(o) ? resposta(403, { detail: "Acesso negado" }) : resposta(200, { connections: [VIVO] })),
    "/open-finance/1/limite": (o) => (limite403(o) ? resposta(403, { detail: "Acesso negado" }) : resposta(200, LIMITE)),
    "/open-finance/2": () => resposta(200, { connections: [ITAU] }),
    "/open-finance/2/limite": () => resposta(200, LIMITE),
    ...extra,
  });
}

it.each(["limiteBancario", "conexoes"] as const)("a conta troca ENTRE o /auth/me e %s: some o dado da anterior e relê a conta nova", async (alvo) => {
  let conta = 1, trocarAntes = false, prender = false;
  const presa = segurar();
  servidor(() => conta, undefined, { "/auth/me": () => {
    const r = () => resposta(200, { user_id: conta, email: "sofia@x.com", display_name: "Sofia", app_access: true });
    return prender ? presa.promessa.then(r) : r();
  } });
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: /^Nubank,/ })).toBeTruthy());
  const real = servicosOF[alvo] as (...a: never[]) => Promise<unknown>;
  jest.spyOn(servicosOF, alvo).mockImplementation(((...a: never[]) => {
    if (trocarAntes) { // outra conta assume o cofre depois do /auth/me da conta 1 e antes da leitura por uid
      trocarAntes = false;
      prender = true; // a releitura da conta nova fica presa no /auth/me
      cofre.set("pb.credenciais", JSON.stringify({ access: "access-b", refresh: "rt_b" }));
      conta = 2;
    }
    return real(...a);
  }) as never);
  trocarAntes = true;
  await appVai("background");
  await appVai("active");
  await act(async () => { for (let i = 0; i < 10; i++) await drenar(); });
  await waitFor(() => expect(ne("/auth/me")).toBe(3));
  expect(screen.queryByRole("button", { name: /^Nubank,/ })).toBeNull(); // enquanto a conta nova não chega, nada da anterior na tela
  await act(async () => { presa.soltar(); for (let i = 0; i < 10; i++) await drenar(); });
  await waitFor(() => expect(screen.getByRole("button", { name: /^Itaú,/ })).toBeTruthy());
  expect(screen.queryByRole("button", { name: /^Nubank,/ })).toBeNull();
  expect(ne("/open-finance/2")).toBeGreaterThan(0);
  expect(screen.queryByText("Conferir de novo")).toBeNull();
});

it("403 sem troca de conta: erro com 'Conferir de novo' e o /auth/me é lido uma vez só (sem laço de recarga)", async () => {
  servidor(() => 1, () => true);
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Conferir de novo" })).toBeTruthy());
  await act(async () => { for (let i = 0; i < 10; i++) await drenar(); });
  expect(ne("/auth/me")).toBe(1);
});

it("refresh legítimo da mesma conta + 500 na leitura com dado na tela: o banco continua na tela", async () => {
  let expirou = false, quebrar = false;
  servidor(() => 1, () => false, {
    "/auth/me": (o) => (expirou && (o.headers as Record<string, string>)["Authorization"] === "Bearer access-s" ? resposta(401, { detail: "expirado" })
      : resposta(200, { user_id: 1, email: "sofia@x.com", display_name: "Sofia", app_access: true })),
    "/auth/refresh": () => resposta(200, credencialDe("s2")),
    "/open-finance/1": () => (quebrar ? resposta(500, { detail: "boom" }) : resposta(200, { connections: [VIVO] })),
  });
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: /^Nubank,/ })).toBeTruthy());
  expirou = quebrar = true;
  await appVai("background");
  await appVai("active");
  await act(async () => { for (let i = 0; i < 10; i++) await drenar(); });
  await waitFor(() => expect(ne("/auth/refresh")).toBe(1));
  await waitFor(() => expect(ne("/open-finance/1")).toBe(2));
  await act(async () => { for (let i = 0; i < 10; i++) await drenar(); });
  expect(screen.getByRole("button", { name: /^Nubank,/ })).toBeTruthy();
});
