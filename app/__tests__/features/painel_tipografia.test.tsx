import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { Dimensions, StyleSheet } from "react-native";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, resposta, segurar } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
import { fluxoFim } from "./conversa_apoio";
import { ficha, filtrar } from "./extrato_filtros_apoio";
type No = ReturnType<typeof screen.getByText>;
const janelaOriginal = Dimensions.get("window");
async function fonte(fontScale: number) {
 await act(async () => { Dimensions.set({ window: { ...janelaOriginal, width: 402, height: 874, fontScale } }); await drenar(); });
}
function paiVisual(no: No): No | null {
 let p = no.parent;
 while (p && String(p.type) !== "View") p = p.parent;
 return p;
}
function temColuna(no: No) {
 let p = no.parent;
 while (p) { if (String(p.type) === "View" && StyleSheet.flatten(p.props.style)?.flexDirection === "column") return true; p = p.parent; }
 return false;
}
function servidor() {
 fetchFalso.mockImplementation(async (url: string) => {
  const path = new URL(url).pathname;
  if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
  if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
  if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
  if (path === "/api/app/perfil") return resposta(200, { perfil: "padrao" });
  return resposta(200, (fixture as Record<string, unknown>)[path] ?? {});
 });
}
beforeEach(async () => { prepararCaso(); desligarTrava(); await fonte(1); await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" }); servidor(); });
afterEach(async () => { await act(async () => { Dimensions.set({ window: janelaOriginal }); }); jest.restoreAllMocks(); });
it.each([1, 2])("cold start fontScale %s mantém teto e compõe label/valor conforme espaço", async (escala) => {
 await fonte(escala); renderRouter("./app", { initialUrl: "/metas" });
 await waitFor(() => expect(screen.getByText("Carteira")).toBeTruthy());
 const label = screen.getByText("Carteira"), valor = screen.getByLabelText("450 reais");
 expect(label.props.maxFontSizeMultiplier).toBe(1.3); expect(valor.props.maxFontSizeMultiplier).toBe(1.3);
 if (escala > 1) expect(paiVisual(label)).toBe(paiVisual(valor));
 else expect(paiVisual(label)).not.toBe(paiVisual(valor));
 expect(screen.getByText("PigBank")).toBeTruthy();
});
it.each([[1, 1], [2, undefined]])("pílula da meta com fontScale %s: nome com numberOfLines %s (sem corte quando ampliado)", async (escala, linhas) => {
 await fonte(escala); renderRouter("./app", { initialUrl: "/metas" });
 await waitFor(() => expect(screen.getByText("Viagem")).toBeTruthy());
 expect(screen.getByText("Viagem").props.numberOfLines).toBe(linhas);
});
it("mudança live remede texto e dinheiro sem remontar providers ou requisitar dados", async () => {
 renderRouter("./app", { initialUrl: "/metas" }); await waitFor(() => expect(screen.getByText("Carteira")).toBeTruthy());
 const antes = screen.getByText("PigBank"), dinheiroAntes = screen.getByLabelText("450 reais");
 const quantidade = fetchFalso.mock.calls.length;
 await fonte(2);
 expect(screen.getByText("PigBank")).not.toBe(antes); expect(screen.getByLabelText("450 reais")).not.toBe(dinheiroAntes);
 expect(paiVisual(screen.getByText("Carteira"))).toBe(paiVisual(screen.getByLabelText("450 reais")));
 expect(fetchFalso.mock.calls).toHaveLength(quantidade);
 await fonte(1); expect(paiVisual(screen.getByText("Carteira"))).not.toBe(paiVisual(screen.getByLabelText("450 reais")));
 expect(fetchFalso.mock.calls).toHaveLength(quantidade);
});
it("filtros refluem na troca live sem perder seleção, busca ou instância do campo", async () => {
 renderRouter("./app", { initialUrl: "/extrato" }); await waitFor(() => expect(screen.getByLabelText("Buscar lançamento")).toBeTruthy());
 const campo = screen.getByLabelText("Buscar lançamento");
 await act(async () => { fireEvent.changeText(campo, "mercado"); await drenar(); }); await filtrar({ origem: "Banco" });
 await waitFor(() => expect(fetchFalso.mock.calls.some(([u]) => new URL(String(u)).searchParams.get("q") === "mercado")).toBe(true));
 const quantidade = fetchFalso.mock.calls.length;
 await fonte(2);
 expect(screen.getByLabelText("Buscar lançamento")).toBe(campo); expect(campo.props.value).toBe("mercado");
 expect(temColuna(ficha("origem Banco")!)).toBe(true);
 expect(temColuna(screen.getByRole("button", { name: "Filtros" }))).toBe(true);
 expect(fetchFalso.mock.calls).toHaveLength(quantidade);
});
it("reflow durante POST mantém o rascunho novo e o voo, e aceita a resposta do mesmo usuário", async () => {
 const atraso = segurar(), impl = fetchFalso.getMockImplementation()!;
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => {
  if (new URL(url).pathname === "/ai/chat/stream") { await atraso.promessa; return fluxoFim("Resposta após reflow", 90, 100); } // mesmo limite da cota do fixture (2/100): limite diferente relê o servidor
  return impl(url, req);
 });
 renderRouter("./app", { initialUrl: "/piggy" }); await waitFor(() => expect(screen.getByLabelText("Mensagem para o Piggy")).toBeTruthy());
 await waitFor(() => expect(screen.getByRole("button", { name: /Onde eu consigo economizar/ })).toBeEnabled());
 const campo = screen.getByLabelText("Mensagem para o Piggy");
 await act(async () => { fireEvent.changeText(campo, "Primeira pergunta"); await drenar(); });
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Enviar mensagem" })); await drenar(); });
 await act(async () => { fireEvent.changeText(campo, "Rascunho preservado"); await drenar(); });
 const post = fetchFalso.mock.calls.find(([u]) => new URL(String(u)).pathname === "/ai/chat/stream")!;
 const quantidade = fetchFalso.mock.calls.length;
 await fonte(2);
 expect(screen.getByLabelText("Mensagem para o Piggy")).toBe(campo); expect(campo.props.value).toBe("Rascunho preservado"); expect(post[1].signal.aborted).toBe(false);
 expect(fetchFalso.mock.calls).toHaveLength(quantidade);
 atraso.soltar(); await act(async () => { await drenar(); }); await waitFor(() => expect(screen.getByText("Resposta após reflow")).toBeTruthy());
 expect(screen.getByLabelText("Mensagem para o Piggy").props.value).toBe("Rascunho preservado"); expect(screen.getByText("90 de 100 mensagens utilizadas")).toBeTruthy();
});
it.each([1, 2])("Extrato fontScale %s: o valor fica na linha da descrição, e embaixo dela com fonte ampliada", async (escala) => {
 await fonte(escala); renderRouter("./app", { initialUrl: "/extrato" }); await waitFor(() => expect(screen.getByRole("button", { name: "Filtros" })).toBeTruthy());
 // O deep link frio descarta a 1ª página (a carga do provider cancela a operação); o filtro recarrega.
 await filtrar({ tipo: "Saídas" });
 await waitFor(() => expect(screen.getByText("Mercado")).toBeTruthy());
 const titulo = screen.getByText("Mercado"), valores = screen.getAllByLabelText("menos 120 reais"), valor = valores[1]!; // exatamente dois: [0] é o total do dia (cabeçalho), [1] o valor da linha
 expect(valores).toHaveLength(2);
 expect(escala > 1 ? paiVisual(titulo) : paiVisual(paiVisual(titulo)!)).toBe(paiVisual(valor));
});
