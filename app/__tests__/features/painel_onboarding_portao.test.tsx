import { act, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { fetchFalso, prepararCaso, resposta } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import { caminho, reiniciar } from "./conversa_apoio";
// 403 `open_finance_onboarding_required` no meio do uso (o onboarding deixou de estar completo entre o foco e a leitura): o PAINEL cai no portão, venha a falha de onde vier.
// A checagem mora em `p.falhou`, que todo leitor chama (recursos do provider, Extrato, chat, leitura sob demanda); antes só o `carregar` do provider a fazia.
jest.setTimeout(20_000);
const ONBOARDING = { error: { code: "open_finance_onboarding_required", message: "Conclua o Open Finance." } };
const OUTRO_403 = { error: { code: "feature_unavailable", message: "Indisponível no plano." } };
beforeEach(async () => { prepararCaso(); desligarTrava(); await reiniciar(); });
afterEach(() => { jest.restoreAllMocks(); jest.useRealTimers(); });
/** Troca a resposta de UM caminho; o resto segue o servidor falso. */
function falharEm(path: string, status: number, corpo: object) {
  const base = fetchFalso.getMockImplementation()!;
  fetchFalso.mockImplementation(async (url: string, req: RequestInit) => caminho(url) === path ? resposta(status, corpo) : base(url, req));
}
const chamou = (path: string) => fetchFalso.mock.calls.some(([u]) => caminho(u) === path);
const assentar = async (path: string) => { await waitFor(() => expect(chamou(path)).toBe(true)); for (let i = 0; i < 10; i++) await act(drenar); };

describe("403 do onboarding manda o painel ao portão: sem tela do painel e sem 'Tentar novamente'", () => {
  it("Extrato (lista de lançamentos)", async () => {
    falharEm("/api/app/lancamentos", 403, ONBOARDING); renderRouter("./app", { initialUrl: "/extrato" }); await assentar("/api/app/lancamentos");
    expect(screen.queryByLabelText("Buscar lançamento")).toBeNull(); expect(screen.queryByRole("button", { name: "Tentar de novo" })).toBeNull();
  });
  it("chat (abertura da conversa)", async () => {
    falharEm("/ai/pending", 403, ONBOARDING); renderRouter("./app", { initialUrl: "/piggy" }); await assentar("/ai/pending");
    expect(screen.queryByLabelText("Mensagem para o Piggy")).toBeNull(); expect(screen.queryByRole("button", { name: "Tentar novamente" })).toBeNull();
  });
  it("recurso do provider (o caminho que já existia, agora pelo mesmo `falhou`)", async () => {
    falharEm("/api/app/resumo-do-mes", 403, ONBOARDING); renderRouter("./app", { initialUrl: "/resumo" }); await assentar("/api/app/resumo-do-mes");
    expect(screen.queryByRole("button", { name: "Organizar" })).toBeNull();
  });
});
describe("controle positivo: 403 de OUTRO motivo não derruba o painel", () => {
  it("Extrato: segue na tela com o erro e o 'Tentar de novo'", async () => {
    falharEm("/api/app/lancamentos", 403, OUTRO_403); renderRouter("./app", { initialUrl: "/extrato" }); await assentar("/api/app/lancamentos");
    expect(screen.getByLabelText("Buscar lançamento")).toBeTruthy(); expect(screen.getByRole("button", { name: "Tentar de novo" })).toBeTruthy();
    expect(screen.getByText("Este recurso não está disponível no seu acesso atual.")).toBeTruthy();
  });
  it("chat: segue na tela com o 'Tentar novamente'", async () => {
    falharEm("/ai/pending", 403, OUTRO_403); renderRouter("./app", { initialUrl: "/piggy" }); await assentar("/ai/pending");
    expect(screen.getByRole("button", { name: "Tentar novamente" })).toBeTruthy();
  });
  it("recurso do provider: só o recurso fica 'negado'; o painel continua", async () => {
    falharEm("/api/app/resumo-do-mes", 403, OUTRO_403); renderRouter("./app", { initialUrl: "/resumo" }); await assentar("/api/app/resumo-do-mes");
    expect(screen.getByRole("button", { name: "Organizar" })).toBeTruthy();
  });
});
