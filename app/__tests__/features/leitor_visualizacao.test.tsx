/**
 * Credencial de visualização (sessão de leitor) no app: entrar com usuário e senha, Push que NÃO registra, e o layout
 * (faixa fixa, sem a aba do Piggy, sem as ações que escrevem). Cada grupo tem o dono como controle POSITIVO e o seu NEGATIVO:
 * NEGATIVOS medidos desligando o conserto: (entrar) tirar o `leitura` de `enviar` → /auth/login em vez de /auth/leitura/login;
 * (push) tirar `&& !semPush` de `registravel` em Push.tsx → o leitor registra; (layout) fazer `leitorDe` devolver false →
 * a faixa some e as ações voltam.
 */
const estadoDoEsquema: { valor: "light" | "dark" } = { valor: "light" };
jest.mock("react-native/Libraries/Utilities/useColorScheme", () => ({
  __esModule: true,
  default: () => estadoDoEsquema.valor,
}));

import * as Notifications from "expo-notifications";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";

import { guardarCredenciais, leitorDe, lerCredenciais } from "@/storage/secure";

import { chamadas, fetchFalso, prepararCaso, resposta, rotear, S } from "./auth_apoio";
import { escritas } from "./leitor_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";

declare const global: typeof globalThis & {
  __definirPush: (v: Record<string, unknown>) => void;
};

const b64 = (o: object) => Buffer.from(JSON.stringify(o)).toString("base64url");
const jwt = (carga: object) => `${b64({ alg: "HS256" })}.${b64(carga)}.assinatura`;
const ACCESS_LEITOR = jwt({ ro: true, jti: "j-leitor", name: "leitor" });
const LEITOR = { access: ACCESS_LEITOR, refresh: "rt_leitor" };
const LOGIN_LEITURA = { user_id: 1, somente_leitura: true, dashboard_url: "/painel", access_token: ACCESS_LEITOR, refresh_token: "rt_leitor", dashboard_token: "d", expires_in: 900 };

const registros = () => chamadas().filter((c) => c.caminho === "/api/push/register");

beforeEach(() => {
  prepararCaso();
  desligarTrava();
  global.__definirPush({ permissao: "granted", promptResponde: "granted", token: "apns-teste", ambiente: "development", resposta: null });
  jest.mocked(Notifications.requestPermissionsAsync).mockClear();
});

describe("leitorDe", () => {
  it("só a claim `ro` verdadeira conta; token opaco ou sem a claim é dono", () => {
    expect(leitorDe(ACCESS_LEITOR)).toBe(true);
    expect(leitorDe(jwt({ jti: "x" }))).toBe(false);
    expect(leitorDe(jwt({ ro: "true" }))).toBe(false);
    expect(leitorDe("access-s")).toBe(false);
  });
});

describe("entrar com acesso de visualização", () => {
  it("o link troca o formulário; usuário e senha vão a /auth/leitura/login SEM e-mail; guarda a sessão; /auth/login não é chamado", async () => {
    rotear({ "/auth/leitura/login": () => resposta(200, LOGIN_LEITURA), "/api/push/register": () => resposta(200, { ok: true }) });
    renderRouter("./app", { initialUrl: "/entrar" });
    await waitFor(() => expect(screen.getByLabelText("E-mail")).toBeTruthy());
    expect(screen.getByRole("button", { name: "Entrar com acesso de visualização" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Esqueci a senha" })).toBeTruthy();

    fireEvent.press(screen.getByRole("button", { name: "Entrar com acesso de visualização" }));
    await waitFor(() => expect(screen.getByLabelText("Usuário")).toBeTruthy());
    expect(screen.queryByLabelText("E-mail")).toBeNull();
    expect(screen.queryByRole("button", { name: "Esqueci a senha" })).toBeNull(); // sem Google, Apple nem recuperação de senha
    expect(screen.queryByText("ou")).toBeNull();

    fireEvent.changeText(screen.getByLabelText("Usuário"), "condomino");
    fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha-leitura");
    await act(async () => fireEvent.press(screen.getByRole("button", { name: "Entrar" })));
    await waitFor(async () => expect(await lerCredenciais()).toEqual({ access: ACCESS_LEITOR, refresh: "rt_leitor" }));

    const login = chamadas().filter((c) => c.caminho === "/auth/leitura/login");
    expect(login).toEqual([{ caminho: "/auth/leitura/login", auth: undefined, corpo: { usuario: "condomino", senha: "s3nha-leitura" } }]);
    expect(chamadas().some((c) => c.caminho === "/auth/login")).toBe(false);
  });

  it("controle positivo: o link de volta restaura o e-mail e o login normal segue em /auth/login", async () => {
    rotear();
    renderRouter("./app", { initialUrl: "/entrar" });
    await waitFor(() => expect(screen.getByLabelText("E-mail")).toBeTruthy());
    fireEvent.press(screen.getByRole("button", { name: "Entrar com acesso de visualização" }));
    await waitFor(() => expect(screen.getByLabelText("Usuário")).toBeTruthy());
    fireEvent.press(screen.getByRole("button", { name: "Entrar com e-mail e senha" }));
    await waitFor(() => expect(screen.getByLabelText("E-mail")).toBeTruthy());
    fireEvent.changeText(screen.getByLabelText("E-mail"), "ana@x.com");
    fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha");
    await act(async () => fireEvent.press(screen.getByRole("button", { name: "Entrar" })));
    await waitFor(() => expect(chamadas().some((c) => c.caminho === "/auth/login")).toBe(true));
    expect(chamadas().some((c) => c.caminho === "/auth/leitura/login")).toBe(false);
  });

  it("senha errada: 401 genérico do servidor aparece e nada é guardado", async () => {
    rotear({ "/auth/leitura/login": () => resposta(401, { detail: "Usuário ou senha inválidos." }) });
    renderRouter("./app", { initialUrl: "/entrar" });
    await waitFor(() => expect(screen.getByLabelText("E-mail")).toBeTruthy());
    fireEvent.press(screen.getByRole("button", { name: "Entrar com acesso de visualização" }));
    await waitFor(() => expect(screen.getByLabelText("Usuário")).toBeTruthy());
    fireEvent.changeText(screen.getByLabelText("Usuário"), "condomino");
    fireEvent.changeText(screen.getByLabelText("Senha"), "errada");
    await act(async () => fireEvent.press(screen.getByRole("button", { name: "Entrar" })));
    await waitFor(() => expect(screen.getByText("Usuário ou senha inválidos.")).toBeTruthy());
    expect(await lerCredenciais()).toBeNull();
  });
});

describe("Push", () => {
  const abrir = async (cred: { access: string; refresh: string }) => {
    rotear({ "/api/push/register": () => resposta(200, { ok: true }) });
    await guardarCredenciais(cred);
    renderRouter("./app", { initialUrl: "/" });
    await act(drenar);
  };

  it("leitor: nenhum pedido de permissão e nenhum POST de registro, mesmo com tudo liberado", async () => {
    await abrir(LEITOR);
    await waitFor(() => expect(chamadas().some((c) => c.caminho === "/auth/me")).toBe(true));
    await act(drenar);
    expect(registros()).toHaveLength(0);
    expect(jest.mocked(Notifications.requestPermissionsAsync)).not.toHaveBeenCalled();
  });

  it("controle positivo: o dono, com o mesmo cenário, registra", async () => {
    await abrir(S);
    await waitFor(() => expect(registros()).toHaveLength(1));
  });
});

describe("layout do painel", () => {
  const netflix = fixture["/api/app/assinaturas"].servicos[0]!;
  const lista = { ...fixture["/api/app/assinaturas"], servicos: [{ ...netflix, marcada: true }], outras: [], ignoradas: [] };
  const servir = (leitor: boolean) =>
    fetchFalso.mockImplementation(async (url: string) => {
      const path = new URL(url).pathname;
      if (path === "/auth/me") return resposta(200, { user_id: 1, app_access: true, ...(leitor ? { somente_leitura: true } : { display_name: "Ana" }) });
      if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
      if (path === "/api/app/perfil" && leitor) return resposta(403, { error: "somente_leitura" }); // o servidor nega o GET de perfil ao leitor
      if (path === "/api/app/assinaturas") return resposta(200, lista);
      return resposta(200, (fixture as Record<string, unknown>)[path] ?? {});
    });
  const abrir = async (leitor: boolean) => {
    servir(leitor);
    await guardarCredenciais(leitor ? LEITOR : S);
    renderRouter("./app", { initialUrl: "/gastos" });
    await waitFor(() => expect(screen.getByLabelText("Aba Resumo")).toBeTruthy());
    await waitFor(() => expect(screen.getByText("Netflix")).toBeTruthy());
  };

  it("leitor: faixa fixa, sem a aba do Piggy, sem Ferramentas, sem Ignorar e sem o GET de perfil", async () => {
    await abrir(true);
    expect(screen.getByTestId("faixa-leitura")).toBeTruthy();
    expect(screen.getByText("Modo visualização · só leitura")).toBeTruthy();
    expect(screen.queryByLabelText("Aba Piggy")).toBeNull();
    expect(screen.queryByRole("button", { name: "Abrir ferramentas" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Ignorar Netflix" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Criar meta" })).toBeNull();
    expect(chamadas().some((c) => c.caminho === "/api/app/perfil")).toBe(false);
    expect(escritas()).toEqual([]); // nenhum pedido não-GET no boot, com ou sem corpo (POST vazio, DELETE)
  });

  it("controle positivo: o dono vê a aba do Piggy, Ferramentas e as ações, e não vê a faixa", async () => {
    await abrir(false);
    expect(screen.queryByTestId("faixa-leitura")).toBeNull();
    expect(screen.getByLabelText("Aba Piggy")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Abrir ferramentas" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Ignorar Netflix" })).toBeTruthy();
    expect(chamadas().some((c) => c.caminho === "/api/app/perfil")).toBe(true);
  });
});
