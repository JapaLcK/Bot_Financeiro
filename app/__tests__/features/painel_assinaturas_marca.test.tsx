import { AccessibilityInfo } from "react-native";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, resposta } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
const netflix = fixture["/api/app/assinaturas"].servicos[0]!;
const vazio = { ...fixture["/api/app/assinaturas"], servicos: [], outras: [], ignoradas: [] };
let falhaMarca = false; let falhaGet = false; let segura: Promise<void> | null = null; // `segura`: segura o POST de marca (um POST em voo)
const spotify = { ...netflix, chave: "spotify", nome: "Spotify" };
/** Serve a lista dada no GET e no POST de marca (o servidor devolve a lista inteira). */
function servir(dados: unknown) {
 fetchFalso.mockImplementation(async (url: string) => {
  const path = new URL(url).pathname;
  if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
  if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
  if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
  if (path === "/api/app/assinaturas" && falhaGet) return resposta(500, {});
  if (path === "/api/app/assinaturas/marca" && segura) await segura;
  if (path === "/api/app/assinaturas/marca" && falhaMarca) return resposta(500, {});
  if (path === "/api/app/assinaturas" || path === "/api/app/assinaturas/marca") return resposta(200, dados);
  return resposta(200, (fixture as Record<string, unknown>)[path] ?? {});
 });
}
const corpos = () => fetchFalso.mock.calls.filter(([url]) => new URL(String(url)).pathname === "/api/app/assinaturas/marca").map(([, req]) => JSON.parse(req.body));
async function abrir(dados: unknown, botao: string) { servir(dados); renderRouter("./app", { initialUrl: "/gastos" }); await waitFor(() => expect(screen.getByRole("button", { name: botao })).toBeTruthy()); }
async function apertar(nome: string) { await act(async () => { fireEvent.press(screen.getByRole("button", { name: nome })); await drenar(); }); }
beforeEach(async () => { prepararCaso(); desligarTrava(); falhaMarca = false; falhaGet = false; segura = null; jest.mocked(AccessibilityInfo.announceForAccessibility).mockClear(); await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" }); });
afterEach(() => jest.restoreAllMocks());
it("Voltar a mostrar restaura a marca manual guardada (marcada) em vez de apagá-la", async () => {
 await abrir({ ...vazio, ignoradas: [{ ...netflix, marcada: true }] }, "Voltar a mostrar Netflix"); await apertar("Voltar a mostrar Netflix");
 expect(corpos()).toEqual([{ chave: "netflix", status: "assinatura" }]); expect(screen.getByText("Netflix voltou para a lista.")).toBeTruthy();
});
it("Voltar a mostrar sem marca manual manda nenhuma", async () => {
 await abrir({ ...vazio, ignoradas: [{ ...netflix, marcada: false }] }, "Voltar a mostrar Netflix"); await apertar("Voltar a mostrar Netflix");
 expect(corpos()).toEqual([{ chave: "netflix", status: "nenhuma" }]);
});
it("Não é assinatura só aparece com marca manual e manda nenhuma", async () => {
 await abrir({ ...vazio, servicos: [{ ...netflix, marcada: false }] }, "Ignorar Netflix"); expect(screen.queryByRole("button", { name: "Remover marca de Netflix" })).toBeNull();
 screen.unmount(); jest.clearAllMocks();
 await abrir({ ...vazio, servicos: [{ ...netflix, marcada: true }] }, "Remover marca de Netflix"); await apertar("Remover marca de Netflix");
 expect(corpos()).toEqual([{ chave: "netflix", status: "nenhuma" }]); expect(screen.getByText("Marca removida de Netflix.")).toBeTruthy();
});
it("Desfazer volta ao estado anterior; desfeito não oferece novo Desfazer; falha mantém o Desfazer", async () => {
 await abrir({ ...vazio, servicos: [{ ...netflix, marcada: true }] }, "Ignorar Netflix"); await apertar("Ignorar Netflix");
 expect(screen.getByText("Netflix ignorada.")).toBeTruthy(); falhaMarca = true; await apertar("Desfazer"); expect(screen.getByRole("button", { name: "Desfazer" })).toBeTruthy();
 falhaMarca = false; await apertar("Desfazer"); expect(corpos()).toEqual([{ chave: "netflix", status: "ignorar" }, { chave: "netflix", status: "assinatura" }, { chave: "netflix", status: "assinatura" }]);
 expect(screen.getByText("Desfeito.")).toBeTruthy(); expect(screen.queryByRole("button", { name: "Desfazer" })).toBeNull();
});
it("Desfazer de Voltar a mostrar manda ignorar", async () => {
 await abrir({ ...vazio, ignoradas: [{ ...netflix, marcada: false }] }, "Voltar a mostrar Netflix"); await apertar("Voltar a mostrar Netflix"); await apertar("Desfazer");
 expect(corpos()).toEqual([{ chave: "netflix", status: "nenhuma" }, { chave: "netflix", status: "ignorar" }]);
});
it("reajuste mostra o valor anterior e some com os valores ocultos; o resto do detalhe é o novo", async () => {
 await abrir({ ...vazio, servicos: [{ ...netflix, valor: "39.90", valor_anterior: "34.90", reajuste_em: "2026-10-01" }] }, "Ignorar Netflix");
 expect(screen.getByText(/^subiu de R\$ 34,90 em 01\/10/)).toBeTruthy(); expect(screen.getByText(/^todo dia 15 · próxima 15\/10/)).toBeTruthy();
 await apertar("Ocultar valores"); expect(screen.getByText(/^subiu em 01\/10/)).toBeTruthy(); expect(screen.queryByText(/34,90/)).toBeNull();
});
it("valor que baixou diz baixou; possivelmente cancelada diz parece cancelada", async () => {
 await abrir({ ...vazio, servicos: [{ ...netflix, valor: "29.90", valor_anterior: "34.90", reajuste_em: "2026-10-01", status: "possivelmente_cancelada" }] }, "Ignorar Netflix");
 expect(screen.getByText(/^baixou de R\$ 34,90 em 01\/10/)).toBeTruthy(); expect(screen.getByText("todo dia 15 · parece cancelada · última em 15/09")).toBeTruthy(); expect(screen.queryByText(/possivelmente/)).toBeNull();
 expect(screen.getByText("Nubank ••1234")).toBeTruthy(); // o meio é linha própria: com o detalhe quebrando em 2 linhas ele não pode sumir
});
it("Desfazer de Ignorar em serviço sem marca manda nenhuma, e ação nova que falha mantém o Desfazer anterior", async () => {
 await abrir({ ...vazio, servicos: [{ ...netflix, marcada: false }, spotify] }, "Ignorar Netflix"); await apertar("Ignorar Netflix");
 falhaMarca = true; await apertar("Ignorar Spotify");
 expect(screen.getByText("Netflix ignorada.")).toBeTruthy(); expect(screen.queryByText("Spotify ignorada.")).toBeNull();
 falhaMarca = false; await apertar("Desfazer");
 expect(corpos()).toEqual([{ chave: "netflix", status: "ignorar" }, { chave: "spotify", status: "ignorar" }, { chave: "netflix", status: "nenhuma" }]);
});
it("ação nova que dá certo troca o aviso e o Desfazer", async () => {
 await abrir({ ...vazio, servicos: [{ ...netflix, marcada: true }, spotify] }, "Ignorar Netflix"); await apertar("Ignorar Netflix"); await apertar("Ignorar Spotify");
 expect(screen.queryByText("Netflix ignorada.")).toBeNull(); expect(screen.getByText("Spotify ignorada.")).toBeTruthy(); await apertar("Desfazer");
 expect(corpos().at(-1)).toEqual({ chave: "spotify", status: "nenhuma" });
});
it("Desfazer de É assinatura manda nenhuma", async () => {
 await abrir({ ...vazio, outras: [{ ...netflix, nome: "Outra", chave: "o" }] }, "Marcar Outra como assinatura"); await apertar("Marcar Outra como assinatura"); await apertar("Desfazer");
 expect(corpos()).toEqual([{ chave: "o", status: "assinatura" }, { chave: "o", status: "nenhuma" }]);
});
it("Desfazer de Não é assinatura manda assinatura", async () => {
 await abrir({ ...vazio, servicos: [{ ...netflix, marcada: true }] }, "Remover marca de Netflix"); await apertar("Remover marca de Netflix"); await apertar("Desfazer");
 expect(corpos()).toEqual([{ chave: "netflix", status: "nenhuma" }, { chave: "netflix", status: "assinatura" }]);
});
it("duplo toque em Ignorar gera um POST só", async () => {
 await abrir({ ...vazio, servicos: [netflix] }, "Ignorar Netflix");
 await act(async () => { const b = screen.getByRole("button", { name: "Ignorar Netflix" }); fireEvent.press(b); fireEvent.press(b); await drenar(); });
 expect(corpos()).toEqual([{ chave: "netflix", status: "ignorar" }]);
});
it("Desfazer fica desabilitado enquanto outro POST está em voo", async () => {
 await abrir({ ...vazio, servicos: [netflix, spotify] }, "Ignorar Netflix"); await apertar("Ignorar Netflix");
 let soltar!: () => void; segura = new Promise<void>((ok) => { soltar = ok; });
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Ignorar Spotify" })); await drenar(); });
 expect(screen.getByRole("button", { name: "Desfazer" })).toBeDisabled();
 await act(async () => { soltar(); await drenar(); }); expect(screen.getByRole("button", { name: "Desfazer" })).not.toBeDisabled();
});
it("sem data de reajuste, ou com o mesmo valor, não mostra linha de reajuste", async () => {
 await abrir({ ...vazio, servicos: [{ ...netflix, valor_anterior: "34.90", reajuste_em: null }] }, "Ignorar Netflix"); expect(screen.queryByText(/subiu|baixou/)).toBeNull();
 screen.unmount();
 await abrir({ ...vazio, servicos: [{ ...netflix, valor: "39.90", valor_anterior: "39.90", reajuste_em: "2026-10-01" }] }, "Ignorar Netflix"); expect(screen.queryByText(/subiu|baixou/)).toBeNull();
});
it("o final do cartão aparece e some com os valores ocultos", async () => {
 await abrir({ ...vazio, servicos: [netflix] }, "Ignorar Netflix"); expect(screen.getByText(/••1234/)).toBeTruthy();
 await apertar("Ocultar valores"); expect(screen.queryByText(/••1234/)).toBeNull();
});
it("o aviso novo é anunciado no iOS (liveRegion só vale no Android)", async () => {
 await abrir({ ...vazio, servicos: [netflix] }, "Ignorar Netflix"); await apertar("Ignorar Netflix");
 expect(AccessibilityInfo.announceForAccessibility).toHaveBeenCalledWith("Netflix ignorada."); await apertar("Desfazer"); expect(AccessibilityInfo.announceForAccessibility).toHaveBeenLastCalledWith("Desfeito.");
});
it("Salvando… aparece no botão tocado, não no Ignorar da mesma linha", async () => {
 await abrir({ ...vazio, servicos: [{ ...netflix, marcada: true }] }, "Remover marca de Netflix");
 let soltar!: () => void; segura = new Promise<void>((ok) => { soltar = ok; });
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Remover marca de Netflix" })); await drenar(); });
 expect(screen.getByRole("button", { name: "Salvando…" })).toBeDisabled(); expect(screen.getByRole("button", { name: "Ignorar Netflix" })).toBeDisabled(); expect(screen.queryByRole("button", { name: "Remover marca de Netflix" })).toBeNull();
 await act(async () => { soltar(); await drenar(); }); expect(screen.queryByRole("button", { name: "Salvando…" })).toBeNull();
});
it("recarga que falha depois da marca não esconde o aviso nem o Desfazer", async () => {
 await abrir({ ...vazio, servicos: [{ ...netflix, marcada: true }] }, "Ignorar Netflix");
 falhaGet = true; await apertar("Ignorar Netflix");
 expect(screen.getByText("Netflix ignorada.")).toBeTruthy(); expect(screen.queryByRole("button", { name: "Remover marca de Netflix" })).toBeNull(); // a lista saiu: o recurso está em erro
 falhaGet = false; await apertar("Desfazer"); expect(corpos()).toEqual([{ chave: "netflix", status: "ignorar" }, { chave: "netflix", status: "assinatura" }]);
});
