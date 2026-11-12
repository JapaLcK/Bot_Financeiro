/**
 * Notificações do app, parte 6: o banner de erro da escrita ("Não conseguimos salvar") é da fase em que ela falhou (PL-06 B6, PR 3;
 * R5–R6 da rodada 4 e Y3–Y11 do Tester; plano §26.1): some com nova escrita, troca de conta e leitura que muda a fase; fica numa
 * leitura que mantém a fase, inclusive a releitura que falha e deixa `pronto` calado (§24).
 * Os controles negativos (mutante, vermelho, restaura) estão no relato do PR.
 */
import { act, screen, waitFor } from "expo-router/testing-library";

import { FALHA_CARREGAR, FALHA_SALVAR } from "@/features/conta/notificacoes";

import { cofre, fetchFalso, resposta, segurar } from "./auth_apoio";
import { ALCANCE, MARGEM, abrirNotificacoes, servidor, trocar } from "./notificacoes_apoio";
import { appVai, drenar, preparar, tocar } from "./seguranca_sem_senha_apoio";

jest.mock("@react-native-community/datetimepicker", () => ({
  __esModule: true,
  default: (p: object) => jest.requireActual("react").createElement(jest.requireActual("react-native").View, { testID: "seletor-hora", ...p }),
  DateTimePickerAndroid: { open: jest.fn() },
}));

beforeEach(() => preparar());
afterEach(() => jest.restoreAllMocks());

const PLANO = "Escolha um plano para ver suas notificações.";
const ADERIU = { insight_alert_email_notice: ALCANCE, insight_alert_email_enabled: true };
const assentar = () => act(async () => { for (let i = 0; i < 5; i++) await drenar(); });
const soltar = (seg: ReturnType<typeof segurar>) => act(async () => { seg.soltar(); for (let i = 0; i < 5; i++) await drenar(); });
const voltarAoApp = async () => {
  await appVai("background");
  await appVai("active");
  await assentar();
};

describe("Avisos do Piggy — o banner de erro da escrita é da fase em que ela falhou", () => {
  it("R5: a escrita cai na rede em `pronto` (banner); a conta é cortada e a volta ao app lê `plano`: o banner sai", async () => {
    const srv = servidor({});
    let cair = false;
    await abrirNotificacoes(srv, { "/settings/1/notifications": (o) => (cair && o.method === "PATCH" ? Promise.reject(new TypeError("Network request failed")) : srv.rota(o)) });
    await waitFor(() => expect(screen.getByText("Resumo mensal")).toBeTruthy());
    cair = true;
    await trocar("Resumo mensal", false);
    await waitFor(() => expect(screen.getByText(FALHA_SALVAR)).toBeTruthy());
    await assentar();
    expect(screen.getByText(FALHA_SALVAR)).toBeTruthy(); // a releitura da própria falha (pronto → pronto) mantém o banner
    srv.conta.cortada = true;
    await voltarAoApp();
    await waitFor(() => expect(screen.getByText(PLANO)).toBeTruthy());
    expect(screen.queryByText(FALHA_SALVAR)).toBeNull();
  });

  it("R6: 'Desligar agora' cai na rede e a releitura dá 500: 'não carregou', sem o banner da escrita", async () => {
    const srv = servidor(ADERIU);
    srv.conta.cortada = true;
    let cair = false;
    await abrirNotificacoes(srv, { "/settings/1/notifications": (o) => (!cair ? srv.rota(o) : o.method === "PATCH" ? Promise.reject(new TypeError("Network request failed")) : resposta(500, { detail: "x" })) });
    await waitFor(() => expect(screen.getByText(MARGEM)).toBeTruthy());
    cair = true;
    await tocar("Desligar agora");
    await assentar();
    await waitFor(() => expect(screen.getByText(FALHA_CARREGAR)).toBeTruthy());
    expect(screen.queryByText(FALHA_SALVAR)).toBeNull();
  });

  it("Y3: a escrita cai na rede em `pronto` (banner) e a releitura vira troca de conta (RequisicaoSuperada): a conta 2 não herda o banner", async () => {
    const seg = segurar();
    let conta = 1, prender = false, cair = false;
    const srv1 = servidor(ADERIU);
    const srv2 = servidor({ monthly_report_enabled: false });
    await abrirNotificacoes(srv1, {
      "/auth/me": () => resposta(200, { user_id: conta, email: "sofia@x.com", display_name: "Sofia", app_access: true }),
      "/settings/1/notifications": (o) => {
        if (cair && o.method === "PATCH") { prender = true; return Promise.reject(new TypeError("Network request failed")); }
        return prender && o.method !== "PATCH" ? seg.promessa.then(() => srv1.rota(o)) : srv1.rota(o);
      },
      "/settings/2/notifications": srv2.rota,
    });
    await waitFor(() => expect(screen.getByText("Resumo mensal")).toBeTruthy());
    cair = true;
    await trocar("Resumo diário", false);
    await assentar();
    expect(screen.getByText(FALHA_SALVAR)).toBeTruthy();
    cofre.set("pb.credenciais", JSON.stringify({ access: "access-b", refresh: "rt_b" }));
    conta = 2;
    await soltar(seg);
    await waitFor(() => expect(fetchFalso.mock.calls.some(([u]: [string]) => String(u).endsWith("/settings/2/notifications"))).toBe(true));
    await assentar();
    expect(screen.getByText("Resumo mensal")).toBeTruthy(); // positivo: a conta 2 carregou
    expect(screen.queryByText(FALHA_SALVAR)).toBeNull();
  });

  it("Y6: a escrita cai na rede em `pronto` e a releitura dá 500 (a tela fica, calada, §24): o banner fica", async () => {
    const srv = servidor({});
    let cair = false;
    await abrirNotificacoes(srv, { "/settings/1/notifications": (o) => (!cair ? srv.rota(o) : o.method === "PATCH" ? Promise.reject(new TypeError("Network request failed")) : resposta(500, { detail: "x" })) });
    await waitFor(() => expect(screen.getByText("Resumo mensal")).toBeTruthy());
    cair = true;
    await trocar("Resumo mensal", false);
    await assentar();
    expect(screen.getByText(FALHA_SALVAR)).toBeTruthy();
    expect(screen.getByText("Resumo mensal")).toBeTruthy();
    expect(screen.queryByText(FALHA_CARREGAR)).toBeNull();
  });

  it("Y10: 'Desligar agora' cai na rede em `plano` (banner, nada gravado); o plano volta e a volta ao app lê `pronto`: o banner sai", async () => {
    const srv = servidor(ADERIU);
    srv.conta.cortada = true;
    let cair = false;
    await abrirNotificacoes(srv, { "/settings/1/notifications": (o) => (cair && o.method === "PATCH" ? Promise.reject(new TypeError("Network request failed")) : srv.rota(o)) });
    await waitFor(() => expect(screen.getByText(MARGEM)).toBeTruthy());
    cair = true;
    await tocar("Desligar agora");
    await assentar();
    expect(screen.getByText(FALHA_SALVAR)).toBeTruthy();
    srv.conta.cortada = false;
    await voltarAoApp();
    await waitFor(() => expect(screen.getByText("Resumo mensal")).toBeTruthy());
    expect(screen.queryByText(FALHA_SALVAR)).toBeNull();
  });

  it.each([
    ["exclusao", "Esta conta está agendada para exclusão."],
    ["senha", "Definir senha"],
  ] as const)("Y11: a escrita cai na rede em `pronto` (banner); a volta ao app lê %s: o banner sai", async (destino, texto) => {
    const srv = servidor({});
    let cair = false, modo: "ok" | "exclusao" | "senha" = "ok";
    await abrirNotificacoes(srv, { "/settings/1/notifications": (o) => (
      cair && o.method === "PATCH" ? Promise.reject(new TypeError("Network request failed"))
        : modo === "exclusao" ? resposta(403, { detail: "Conta agendada para exclusão." })
          : modo === "senha" ? resposta(403, { detail: { error: "password_required" } }) : srv.rota(o)) });
    await waitFor(() => expect(screen.getByText("Resumo mensal")).toBeTruthy());
    cair = true;
    await trocar("Resumo mensal", false);
    await assentar();
    expect(screen.getByText(FALHA_SALVAR)).toBeTruthy();
    modo = destino;
    await voltarAoApp();
    await waitFor(() => expect(screen.getByText(texto)).toBeTruthy());
    expect(screen.queryByText(FALHA_SALVAR)).toBeNull();
  });
});
