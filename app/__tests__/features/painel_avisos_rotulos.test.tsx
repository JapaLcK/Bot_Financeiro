import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { StyleSheet } from "react-native";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, resposta } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
import { claro, escuro } from "@/ui/tokens";
// Dados no formato REAL da API (conexão "partial", chave de categoria sem acento, slug do cartão):
// o fixture já vem bonito e não pegaria nenhum destes rótulos crus.
let extra: Record<string, unknown>, falhar: string[];
function servidor() {
 fetchFalso.mockImplementation(async (url: string) => {
  const path = new URL(url).pathname;
  if (falhar.includes(path)) return resposta(500, { detail: "erro" });
  if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
  if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
  if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
  if (path in extra) return resposta(200, extra[path]);
  return resposta(200, (fixture as Record<string, unknown>)[path] ?? {});
 });
}
beforeEach(async () => { prepararCaso(); desligarTrava(); extra = {}; falhar = []; await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" }); servidor(); });
afterEach(() => jest.restoreAllMocks());
const banco = ["banco_desatualizado"], dias = ["carteira_nao_confirmada", "banco_desatualizado", "conciliacao_pendente"];
function bancoDesatualizado() {
 const c = fixture["/api/app/contas"], pat = fixture["/api/app/patrimonio"];
 extra["/api/app/contas"] = { ...c, motivos: banco, contas: [{ ...c.contas[0], instituicao: "Nu Pagamentos S.A.", conexao: "partial", motivos: banco }, c.contas[1]] };
 extra["/api/app/patrimonio"] = { ...pat, motivos: banco, historico: pat.historico.map((h) => ({ ...h, motivos: dias })) };
 extra["/api/app/resumo-do-mes"] = { ...fixture["/api/app/resumo-do-mes"], motivos: banco };
 extra["/api/app/mes-detalhes"] = { ...fixture["/api/app/mes-detalhes"], motivos: banco };
}
const pronto = () => waitFor(() => expect(screen.getByText("Disponível agora")).toBeTruthy());
it("banco desatualizado vira UM aviso no topo; ressalvas dos cards ficam atrás do ícone", async () => {
 bancoDesatualizado(); renderRouter("./app", { initialUrl: "/resumo" }); await pronto();
 await waitFor(() => expect(screen.getAllByText(/precisam ser atualizados/)).toHaveLength(1));
 expect(screen.getByText(/Os dados de Nu Pagamentos S\.A\. precisam ser atualizados/)).toBeTruthy();
 expect(screen.getByRole("button", { name: "Ver bancos conectados" })).toBeTruthy();
 expect(screen.queryByText(/partial/)).toBeNull(); expect(screen.queryByText(/Confira se o saldo da carteira/)).toBeNull();
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Ver ressalvas" })); await drenar(); });
 expect(screen.getAllByText(/Confira se o saldo da carteira manual está atualizado\./)).toHaveLength(1);
 expect(screen.getByRole("button", { name: "Ocultar ressalvas" })).toBeTruthy();
});
it("o aviso do banco aparece em cada aba, não só no Resumo", async () => {
 bancoDesatualizado(); renderRouter("./app", { initialUrl: "/gastos" });
 await waitFor(() => expect(screen.getAllByText(/precisam ser atualizados/)).toHaveLength(1));
});
it("contas sem motivo de banco não ganham aviso no topo", async () => {
 renderRouter("./app", { initialUrl: "/resumo" }); await pronto();
 expect(screen.queryByText(/precisam ser atualizados/)).toBeNull(); expect(screen.queryByText("Dados do banco desatualizados")).toBeNull();
});
// O sinal de banco não pode depender só de /contas: ele falha, ou a conexão desatualizada é só de investimentos.
it("contas fora do ar não apagam o aviso: patrimônio com banco desatualizado gera o aviso genérico", async () => {
 falhar = ["/api/app/contas"]; const pat = fixture["/api/app/patrimonio"];
 extra["/api/app/patrimonio"] = { ...pat, motivos: banco };
 renderRouter("./app", { initialUrl: "/resumo" });
 await waitFor(() => expect(screen.getByText("Os dados de um banco conectado precisam ser atualizados. Alguns valores podem estar incompletos.")).toBeTruthy());
 expect(screen.getByRole("button", { name: "Ver bancos conectados" })).toBeTruthy();
});
it("conexão só de investimento: item do rendimento desatualizado gera o aviso com a instituição", async () => {
 const r = fixture["/api/app/rendimento"]; extra["/api/app/rendimento"] = { ...r, itens: [{ ...r.itens[0], instituicao: "XP Investimentos", motivos: banco }] };
 renderRouter("./app", { initialUrl: "/metas" });
 await waitFor(() => expect(screen.getByText(/Os dados de XP Investimentos precisam ser atualizados/)).toBeTruthy());
});
it("motivo de banco da previsão (objeto com código) também gera o aviso", async () => {
 extra["/api/app/previsao"] = { ...fixture["/api/app/previsao"], motivos: [{ codigo: "conexao_pausada", direcao_do_erro: "ambos" }] };
 renderRouter("./app", { initialUrl: "/resumo" });
 await waitFor(() => expect(screen.getByText("Dados do banco desatualizados")).toBeTruthy());
});
const fatura = (ciclo: string, data: string | null, futura = false) => ({ chave: "fat", ciclo, data, fonte: "fatura", tipo: "fatura_cartao", nome: "ultraviolet-black", valor: futura ? "1240.50" : null, direcao: "saida", qualidade_valor: futura ? "conhecido" : "desconhecido", qualidade_data: "conhecida", realizacao: futura ? "prevista" : "a_conferir", incluida_no_calculo: true, motivos: [] });
// Controles: tirar o `o.valor !== null` de qualquer dos dois filtros em futuro.tsx deixa este teste vermelho
// (as datas das vencidas sem valor, ou um segundo "sem data", voltam); esconder tudo também (a vencida com valor e as futuras somem).
it("Próximos N dias: vencida com valor primeiro, depois as futuras e o sem data com valor; o resto sem valor some", async () => {
 const conta = (chave: string, nome: string, valor: string | null) => ({ ...fatura(chave, null), chave, nome, tipo: "conta", valor, qualidade_valor: valor ? "conhecido" : "desconhecido" });
 extra["/api/app/previsao"] = { ...fixture["/api/app/previsao"], hoje: "2026-10-07", compromissos: [{ chave: "fat", fonte: "fatura", nome: "ultraviolet-black", primeira_data: "2025-11-08", ultima_data: "2026-10-15", ocorrencias: [fatura("2026-10", "2026-10-15", true), fatura("2025-11", "2025-11-08"), fatura("2026-07", "2026-07-08"), fatura("2026-08", "2026-08-08"), { ...fatura("2026-09", "2026-09-08"), valor: "980.00", qualidade_valor: "conhecido" }, fatura("2026-10b", "2026-10-07", true)] }, { chave: "c", fonte: "conta", nome: "c", primeira_data: null, ultima_data: null, ocorrencias: [conta("acad", "Academia", "99.90"), conta("seguro", "Seguro", null)] }] };
 renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByText(/^venceu 08\/09/)).toBeTruthy());
 expect(screen.queryByText(/a conferir \(/)).toBeNull();
 expect(screen.queryByText(/08\/11/)).toBeNull(); expect(screen.queryByText(/08\/07/)).toBeNull(); expect(screen.queryByText(/08\/08/)).toBeNull();
 expect(screen.getAllByText(/^sem data/)).toHaveLength(1); expect(screen.getByText("Academia")).toBeTruthy(); expect(screen.queryByText("Seguro")).toBeNull();
 expect(screen.getAllByText(/^(venceu 08\/09|07\/10\/2026|15\/10\/2026|sem data)/).map((t) => String(t.props.children).split(" · ")[0])).toEqual(["venceu 08/09", "07/10/2026", "15/10/2026", "sem data"]);
 expect(screen.getAllByText("Ultraviolet Black")).toHaveLength(3);
 expect(screen.queryByText(/ultraviolet-black/)).toBeNull(); expect(screen.queryByText(/desconhecido/)).toBeNull(); expect(screen.queryByText(/a_conferir/)).toBeNull();
});
const categorias = { categorias: [{ chave: "transferencias", nome: "transferências" }, { chave: "servicos", nome: "serviços" }] };
const comChaves = () => { extra["/api/app/mes-detalhes"] = { ...fixture["/api/app/mes-detalhes"], categorias: ["transferencias", "servicos", "compras"].map((categoria) => ({ categoria, valor: "10.00", quantidade: 1 })) }; };
it("categorias usam a grafia do catálogo do usuário; cartão e mês saem legíveis", async () => {
 comChaves(); extra["/api/app/categorias"] = categorias;
 const a = fixture["/api/app/assinaturas"]; extra["/api/app/assinaturas"] = { ...a, servicos: [{ ...a.servicos[0], meio: { tipo: "cartao", nome: "ultraviolet-black", final: "1234" } }] };
 renderRouter("./app", { initialUrl: "/gastos" }); await waitFor(() => expect(screen.getAllByText("Transferências").length).toBeGreaterThan(0));
 expect(screen.getAllByText("Serviços").length).toBeGreaterThan(0); expect(screen.getAllByText("Compras").length).toBeGreaterThan(0);
 expect(screen.queryByText("transferencias")).toBeNull(); expect(screen.queryByText("servicos")).toBeNull();
 expect(screen.getByText(/Ultraviolet Black ••1234/)).toBeTruthy(); expect(screen.queryByText(/ultraviolet-black/)).toBeNull();
});
it("catálogo de categorias indisponível não quebra o card: cai na chave capitalizada", async () => {
 comChaves(); falhar = ["/api/app/categorias"];
 renderRouter("./app", { initialUrl: "/gastos" }); await waitFor(() => expect(screen.getAllByText("Transferencias").length).toBeGreaterThan(0));
 expect(screen.queryByText("transferencias")).toBeNull(); expect(screen.queryByText("Não conseguimos carregar agora. Tente novamente.")).toBeNull();
});
it("resumo não mostra a legenda de comparação nem o mês ISO", async () => {
 renderRouter("./app", { initialUrl: "/resumo" }); await pronto();
 expect(screen.queryByText(/Comparação disponível/)).toBeNull(); expect(screen.queryByText(/2026-09/)).toBeNull();
});
it("taxa não informada tem tamanho de legenda, não de título de seção", async () => {
 extra["/api/app/rendimento"] = { ...fixture["/api/app/rendimento"], itens: [{ ...fixture["/api/app/rendimento"].itens[0], taxa: null, tipo_taxa: null, motivos: ["taxa_contratada_ausente"] }] };
 renderRouter("./app", { initialUrl: "/metas" }); await waitFor(() => expect(screen.getByText("Taxa não informada")).toBeTruthy());
 const tamanho = (t: string | RegExp) => StyleSheet.flatten(screen.getByText(t).props.style).fontSize;
 expect(tamanho("Taxa não informada")).toBe(tamanho(/^Informado em /));
 expect(screen.getByText("Taxa contratada")).toBeTruthy(); expect(screen.queryByText("Rendimento contratado")).toBeNull(); expect(screen.queryByText(/Não representam rendimento recebido/)).toBeNull();
});
it("lançamento de registro antigo tem rótulo legível, e o filtro de origem não o oferece", async () => {
 const l = fixture["/api/app/lancamentos"]; extra["/api/app/lancamentos"] = { ...l, itens: [{ ...l.itens[0], origem: "registro_antigo" }] };
 renderRouter("./app", { initialUrl: "/extrato" }); await waitFor(() => expect(screen.getByRole("button", { name: /^Banco/ })).toBeTruthy());
 expect(screen.queryByRole("button", { name: /^Registro antigo/ })).toBeNull();
 // O deep link frio descarta a 1ª página (a carga do provider cancela a operação); o filtro recarrega.
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: /^Banco/ })); await drenar(); });
 await waitFor(() => expect(screen.getByText(/· Registro antigo/)).toBeTruthy()); expect(screen.queryByText(/registro_antigo/)).toBeNull();
});
it("compromisso realizado diz recebido na entrada e pago na saída", async () => {
 const oc = (chave: string, nome: string, direcao: string) => ({ ...fatura("x", "2026-10-15", true), chave, nome, tipo: "conta", direcao, realizacao: "realizada" });
 extra["/api/app/previsao"] = { ...fixture["/api/app/previsao"], hoje: "2026-10-07", compromissos: [{ chave: "c", fonte: "conta", nome: "c", primeira_data: "2026-10-15", ultima_data: "2026-10-15", ocorrencias: [oc("e", "Salário", "entrada"), oc("s", "Aluguel", "saida")] }] };
 renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByText("15/10/2026 · recebido")).toBeTruthy());
 expect(screen.getAllByText("15/10/2026 · pago")).toHaveLength(1);
});
it("fatura sai como mês curto no Extrato, nunca o ISO cru", async () => {
 const l = fixture["/api/app/lancamentos"]; extra["/api/app/lancamentos"] = { ...l, itens: [{ ...l.itens[1], origem: "cartao", fatura: "2026-10" }] };
 renderRouter("./app", { initialUrl: "/extrato" }); await waitFor(() => expect(screen.getByRole("button", { name: "Cartão" })).toBeTruthy());
 // O deep link frio descarta a 1ª página (a carga do provider cancela a operação); o filtro recarrega.
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Cartão" })); await drenar(); });
 await waitFor(() => expect(screen.getByText(/· fatura out\/2026/)).toBeTruthy()); expect(screen.queryByText(/fatura 2026-10/)).toBeNull();
});
it("a atualização da conta sai curta, sem quebrar a linha com o ano", async () => {
 renderRouter("./app", { initialUrl: "/resumo" }); await pronto();
 // O ano depende do relógio (dataCurta tem teste de unidade com data fixa); aqui só o formato dd/mm.
 expect(screen.getAllByText(new RegExp(`Atualizado 06/10${new Date().getFullYear() === 2026 ? "" : "/2026"}$`))).toHaveLength(2);
});
it("Previsão: sem premissas, sem rótulo de estado e sem frase de motivo fora do 'indisponível'", async () => {
 extra["/api/app/previsao"] = { ...fixture["/api/app/previsao"], estado: "a_conferir", motivos: [{ codigo: "conciliacao_pendente", direcao_do_erro: "so_piora" }] };
 renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByText("A partir de hoje, 06/10")).toBeTruthy());
 expect(screen.queryByText("Há compromissos a conferir")).toBeNull(); expect(screen.queryByText(/gastos variáveis não estimados/)).toBeNull();
 expect(screen.queryByText(/O saldo pode/)).toBeNull(); expect(screen.queryByText("Previsão indisponível")).toBeNull();
});
it("Previsão indisponível continua dizendo isso, em amarelo", async () => {
 extra["/api/app/previsao"] = { ...fixture["/api/app/previsao"], estado: "indisponivel" };
 renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByText("Previsão indisponível")).toBeTruthy());
 expect([claro.warning, escuro.warning]).toContain(StyleSheet.flatten(screen.getByText("Previsão indisponível").props.style).color);
});
