import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor, within } from "expo-router/testing-library";
import type { Conexao } from "@/api/schemas/openFinance";
import { capturarItemBancario, iniciarTentativaBancaria, lerTentativaBancaria, PRAZO_TENTATIVA_MS } from "@/storage/secure";
import { chamadas, cofre, prepararCaso, resposta, rotear, S, segurar } from "./auth_apoio";
import { guardarSessaoOf, SESSAO_OF, VIVO } from "./open_finance_volta_apoio";
import { abrirBanco, appVai, desligarTrava, drenar, umIntervalo } from "./open_finance_volta_rota_apoio";

// Só a fronteira WebView nativa é dublada; Router, autorização e HTTP são reais.
jest.mock("react-native-pluggy-connect", () => ({ PluggyConnect: () => null }));
// O 1º caso monta o app inteiro a frio; no runner do CI isso passa dos 5 s padrão.
jest.setTimeout(20_000);

const B = "banco_b";
function servidor(completo = true, pausa?: Promise<void>) {
  const e = { bancos: [{ ...VIVO, ui: { ...VIVO.ui, state: "updating", label: "A atualizando" } }, { ...VIVO, id: 2, provider_item_id: B, institution_name: "Itaú", ui: { ...VIVO.ui, state: "updating", label: "B atualizando" } }] as Conexao[], segurarGet: false };
  rotear({
    "/auth/me": () => resposta(200, { user_id: 1, display_name: "Ana", app_access: true, of_banks_max: 3 }),
    "/onboarding/open-finance": () => resposta(200, { ok: true, completed: completo, completed_at: completo ? VIVO.last_sync_at : null }),
    "/open-finance/1/limite": () => resposta(200, { ok: true, of_banks_max: 3, em_uso: 2, pode_adicionar: true, code: null, message: null }),
    "/open-finance/1": async () => { if (e.segurarGet && pausa) await pausa; return resposta(200, { connections: e.bancos }); },
    "/open-finance/1/connect-token": () => resposta(503, {}),
    "/open-finance/1/pluggy-item": () => resposta(200, { connections: [{ ...VIVO, ui: { ...VIVO.ui, state: "updating" } }] }),
  });
  return e;
}
const gets = () => chamadas().filter((c) => c.caminho === "/open-finance/1").length;
const posts = () => chamadas().filter((c) => c.caminho.endsWith("pluggy-item"));
const tokens = () => chamadas().filter((c) => c.caminho.endsWith("connect-token"));
const pilha = (r: ReturnType<typeof renderRouter>) => r.getRouterState()?.routes[0]?.state?.routes.find((t) => t.name === "(app)")?.state?.routes.map((t) => ({ nome: t.name, item: t.params && "itemId" in t.params ? t.params.itemId : undefined }));
async function apertar(nome: string) {
  await waitFor(() => expect(screen.getByRole("button", { name: nome })).toBeEnabled());
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: nome })); await drenar(); });
}
beforeEach(async () => {
  prepararCaso(); desligarTrava(); await guardarSessaoOf(S);
  const a = (await lerTentativaBancaria(1))!; await capturarItemBancario(VIVO.provider_item_id, a.tentativa_id);
});
afterEach(() => jest.restoreAllMocks());

it("marcador vence com a Home aberta: 'Retomar conexão' sai sozinho, sem esperar o próximo foco", async () => {
  const m = JSON.parse(cofre.get("pb.of.tentativa")!) as object;
  cofre.set("pb.of.tentativa", JSON.stringify({ ...m, iniciada_em: Date.now() - PRAZO_TENTATIVA_MS + 60_000 }));
  servidor(false); renderRouter("./app", { initialUrl: "/" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Retomar conexão" })).toBeEnabled());
  await act(async () => { jest.advanceTimersByTime(30_000); await drenar(); });
  expect(screen.queryByRole("button", { name: "Retomar conexão" })).toBeTruthy(); // ainda dentro do prazo
  await act(async () => { jest.advanceTimersByTime(31_000); await drenar(); });
  expect(screen.queryByRole("button", { name: "Retomar conexão" })).toBeNull();
});

it("Retomar duplo real tem um leitor/uma Volta e back chega à Home", async () => {
  servidor(); const r = renderRouter("./app", { initialUrl: "/" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Retomar conexão" })).toBeEnabled());
  const b = screen.getByRole("button", { name: "Retomar conexão" });
  await act(async () => { fireEvent.press(b); fireEvent.press(b); await drenar(); });
  await waitFor(() => expect(screen.getByRole("button", { name: "Sair" })).toBeTruthy());
  const entrada = { pilha: pilha(r)?.filter((p) => p.nome === "open-finance-volta").length, gets: gets() };
  await umIntervalo(); const depois = gets();
  await act(async () => { router.back(); await drenar(); });
  expect({ ...entrada, depois, destinoBack: r.getPathname() }).toEqual({ pilha: 1, gets: 1, depois: 2, destinoBack: "/resumo" });
  expect(tokens()).toHaveLength(0); expect(posts()).toHaveLength(0);
});

it("Volta coberta por outra rota pausa GET e retoma pelo mesmo marcador ao voltar", async () => {
  servidor(); renderRouter("./app", { initialUrl: "/" }); await apertar("Retomar conexão");
  await waitFor(() => expect(screen.getByRole("button", { name: "Sair" })).toBeTruthy());
  const marcador = await lerTentativaBancaria(1); const antes = gets();
  await act(async () => { router.push("/configuracoes"); await drenar(); });
  await waitFor(() => expect(screen).toHavePathname("/configuracoes"));
  await appVai("background"); await appVai("active");
  await umIntervalo(); await umIntervalo();
  expect(gets()).toBe(antes); expect(await lerTentativaBancaria(1)).toEqual(marcador); expect(posts()).toHaveLength(0);
  await act(async () => { router.back(); await drenar(); });
  await waitFor(() => expect(gets()).toBe(antes + 1));
  expect(screen).toHavePathname("/open-finance-volta");
  expect(await lerTentativaBancaria(1)).toEqual(marcador); expect(tokens()).toHaveLength(0);
});

// Uma folha por vez: a corrida é no MESMO banco (Acompanhar duas vezes, ou Acompanhar e Reconectar), e só o primeiro botão guarda o banco.
it.each([["Nubank", VIVO.provider_item_id, "Acompanhar sincronização"], ["Itaú", B, "Acompanhar sincronização"], ["Itaú", B, "Reconectar Itaú"]])("Acompanhar %s (%s) → %s no mesmo frame guarda só o primeiro botão", async (banco, item, segundo) => {
  servidor(); const marcador = await lerTentativaBancaria(1); const r = renderRouter("./app", { initialUrl: "/conexoes" });
  await abrirBanco(banco);
  const primeiro = screen.getByRole("button", { name: "Acompanhar sincronização" }); const outro = screen.getByRole("button", { name: segundo }); const antes = gets();
  await act(async () => { fireEvent.press(primeiro); fireEvent.press(outro); await drenar(); });
  await waitFor(() => expect(screen.getByRole("button", { name: "Sair" })).toBeTruthy());
  expect({ pilha: pilha(r)?.filter((p) => p.nome === "open-finance-volta"), gets: gets() - antes }).toEqual({ pilha: [{ nome: "open-finance-volta", item }], gets: 1 });
  expect(r.getSearchParams().modo).toBe("acompanhar"); expect(await lerTentativaBancaria(1)).toEqual(marcador);
  expect(tokens()).toHaveLength(0); expect(posts()).toHaveLength(0);
});


it.each([
  ["Configurações", true, true], ["Configurações", false, true],
  ["Bancos conectados", true, true], ["Bancos conectados", false, true],
  ["Conectar meu banco", true, false], ["Conectar meu banco", false, false],
  ["Segurança", true, true], ["Segurança", false, true],
])("Home Retomar × %s, recovery primeiro=%s, reserva também navegações irmãs", async (irma, recoveryPrimeiro, completo) => {
  servidor(completo); const r = renderRouter("./app", { initialUrl: "/" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Retomar conexão" })).toBeEnabled());
  const a = screen.getByRole("button", { name: "Retomar conexão" });
  if (completo && irma !== "Bancos conectados") await apertar("Abrir minha conta");
  const b = screen.getByRole("button", { name: irma });
  await act(async () => { fireEvent.press(recoveryPrimeiro ? a : b); fireEvent.press(recoveryPrimeiro ? b : a); await drenar(); });
  const destino = recoveryPrimeiro ? "open-finance-volta" : irma === "Configurações" ? "configuracoes" : irma === "Bancos conectados" ? "conexoes" : irma === "Segurança" ? "seguranca" : "conectar-banco";
  expect(pilha(r)?.map((p) => p.nome)).toEqual([completo ? "(painel)" : "index", destino]);
  expect(tokens()).toHaveLength(0); expect(posts()).toHaveLength(0);
});

it("Acompanhar antes de Reconectar irmão não emite token por trás da recuperação", async () => {
  servidor(); const r = renderRouter("./app", { initialUrl: "/conexoes" });
  await abrirBanco();
  const a = screen.getByRole("button", { name: "Acompanhar sincronização" });
  const b = screen.getByRole("button", { name: "Reconectar Nubank" });
  await act(async () => { fireEvent.press(a); fireEvent.press(b); await drenar(); });
  await act(drenar);
  expect({ pilha: pilha(r)?.map((p) => p.nome), tokens: tokens().length }).toEqual({ pilha: ["index", "conexoes", "open-finance-volta"], tokens: 0 });
  expect(posts()).toHaveLength(0);
});

it("Ver bancos antes de Conectar na origem não inicia autorização oculta", async () => {
  servidor(); const r = renderRouter("./app", { initialUrl: "/conectar-banco" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Ver meus bancos" })).toBeEnabled());
  const a = screen.getByRole("button", { name: "Ver meus bancos" }); const b = screen.getByRole("button", { name: "Conectar meu banco" });
  await act(async () => { fireEvent.press(a); fireEvent.press(b); await drenar(); });
  await act(drenar);
  expect({ pilha: pilha(r)?.map((p) => p.nome), tokens: tokens().length }).toEqual({ pilha: ["index", "conectar-banco", "conexoes"], tokens: 0 });
  expect(posts()).toHaveLength(0);
});

it("retomada sem item pausa/refoca sem adotar A visto no snapshot nem criar outro nonce", async () => {
  const anterior = (await lerTentativaBancaria(1))!;
  const marcador = await iniciarTentativaBancaria(1, SESSAO_OF, [], undefined, undefined, anterior);
  expect(marcador!.item_id).toBeUndefined();
  servidor(); renderRouter("./app", { initialUrl: "/" }); await apertar("Retomar conexão");
  await waitFor(() => expect(gets()).toBe(1));
  await act(async () => { router.push("/configuracoes"); await drenar(); });
  await umIntervalo(); expect(gets()).toBe(1);
  expect(await lerTentativaBancaria(1)).toEqual(marcador);
  await act(async () => { router.back(); await drenar(); });
  await waitFor(() => expect(gets()).toBe(2));
  expect(await lerTentativaBancaria(1)).toEqual(marcador);
  expect(posts()).toHaveLength(0); expect(tokens()).toHaveLength(0);
});

it("GET atrasado após blur não registra; refoco recupera A conhecido com GET antes do POST", async () => {
  const pausa = segurar(); const e = servidor(true, pausa.promessa);
  renderRouter("./app", { initialUrl: "/" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Retomar conexão" })).toBeEnabled());
  const marcador = await lerTentativaBancaria(1);
  e.bancos = []; e.segurarGet = true;
  await apertar("Retomar conexão"); await waitFor(() => expect(gets()).toBe(1));
  await act(async () => { router.push("/configuracoes"); await drenar(); });
  await act(async () => { pausa.soltar(); await drenar(); }); await umIntervalo();
  expect(posts()).toHaveLength(0); expect(gets()).toBe(1);
  expect(await lerTentativaBancaria(1)).toEqual(marcador);
  e.segurarGet = false;
  await act(async () => { router.back(); await drenar(); });
  await waitFor(() => expect(posts()).toHaveLength(1));
  expect(chamadas().filter((c) => c.caminho === "/open-finance/1" || c.caminho.endsWith("pluggy-item")).map((c) => c.caminho)).toEqual(["/open-finance/1", "/open-finance/1", "/open-finance/1/pluggy-item"]);
  expect(posts()[0]?.corpo).toEqual({ item: { id: VIVO.provider_item_id } });
  expect(await lerTentativaBancaria(1)).toMatchObject({ tentativa_id: marcador!.tentativa_id, sessao: marcador!.sessao, item_id: marcador!.item_id });
  expect(tokens()).toHaveLength(0);
});

it("observador B pausa/refoca sem consumir tentativa A e mostra resultado próprio B", async () => {
  const e = servidor(); const marcador = await lerTentativaBancaria(1);
  renderRouter("./app", { initialUrl: "/conexoes" });
  await abrirBanco("Itaú");
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Acompanhar sincronização" })); await drenar(); });
  await waitFor(() => expect(screen.getByRole("button", { name: "Sair" })).toBeTruthy());
  const antes = gets();
  await act(async () => { router.push("/configuracoes"); await drenar(); });
  e.bancos[1] = { ...e.bancos[1]!, ui: { ...e.bancos[1]!.ui, state: "updated", label: "B terminou" } };
  await umIntervalo(); expect(gets()).toBe(antes);
  await act(async () => { router.back(); await drenar(); });
  await waitFor(() => expect(screen.getByText("B terminou")).toBeTruthy());
  expect(gets()).toBe(antes + 1); expect(await lerTentativaBancaria(1)).toEqual(marcador);
  expect(tokens()).toHaveLength(0); expect(posts()).toHaveLength(0);
});

it.each([["/", "Retomar conexão"]])("foco em %s libera reserva para nova recuperação após back", async (origem, rotulo) => {
  servidor(); const r = renderRouter("./app", { initialUrl: origem });
  await apertar(rotulo); await waitFor(() => expect(screen.getByRole("button", { name: "Sair" })).toBeTruthy());
  const marcador = await lerTentativaBancaria(1);
  await act(async () => { router.back(); await drenar(); });
  await waitFor(() => expect(screen.getByRole("button", { name: rotulo })).toBeEnabled());
  const b = screen.getByRole("button", { name: rotulo });
  await act(async () => { fireEvent.press(b); fireEvent.press(b); await drenar(); });
  expect(pilha(r)?.filter((p) => p.nome === "open-finance-volta")).toHaveLength(1);
  expect(await lerTentativaBancaria(1)).toEqual(marcador);
  expect(posts()).toHaveLength(0); expect(tokens()).toHaveLength(0);
});

it.each([["/", "Retomar conexão"], ["/conexoes", "Acompanhar sincronização"], ["/conectar-banco", "Ver meus bancos"]])("falha injetada no push de %s libera reserva para próximo push real", async (origem, rotulo) => {
  servidor(); renderRouter("./app", { initialUrl: origem });
  if (origem === "/conexoes") await abrirBanco();
  await waitFor(() => expect(screen.getAllByRole("button", { name: rotulo })[0]).toBeEnabled());
  jest.spyOn(router, "push").mockImplementationOnce(() => { throw new Error("falha de navegação"); });
  await act(async () => { fireEvent.press(screen.getAllByRole("button", { name: rotulo })[0]!); await drenar(); });
  // Em /conexoes o erro é da folha aberta (que cobre a tela): aparece DENTRO dela, uma vez.
  expect(screen.getAllByText("Não conseguimos abrir a tela. Tente de novo.")).toHaveLength(1);
  if (origem === "/conexoes") expect(within(screen.getByTestId("folha-sheet")).getByText("Não conseguimos abrir a tela. Tente de novo.")).toBeTruthy();
  expect(screen.getAllByRole("button", { name: rotulo })[0]).toBeEnabled();
  await act(async () => { fireEvent.press(screen.getAllByRole("button", { name: rotulo })[0]!); await drenar(); });
  expect(screen).toHavePathname(origem === "/conectar-banco" ? "/conexoes" : "/open-finance-volta");
  expect(posts()).toHaveLength(0); expect(tokens()).toHaveLength(0);
});

it("logo no Resumo não reserva navegação sem blur e permite Minha conta e Retomar", async () => {
 servidor(); const r = renderRouter("./app", { initialUrl: "/resumo" }); await apertar("PigBank, abrir Resumo");
 expect(screen).toHavePathname("/resumo"); await apertar("Abrir minha conta"); expect(screen.getByText("Minha conta")).toBeTruthy(); await apertar("Fechar");
 await apertar("Retomar conexão"); expect(pilha(r)?.filter((p) => p.nome === "open-finance-volta")).toHaveLength(1); expect(gets()).toBe(1); expect(tokens()).toHaveLength(0); expect(posts()).toHaveLength(0);
});
it("navigate Piggy repetido não trava o destino nem duplica a pilha", async () => {
 servidor(); const r = renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByRole("button", { name: "Conversar com o Piggy" })).toBeTruthy());
 const antes = pilha(r); const b = screen.getByRole("button", { name: "Conversar com o Piggy" }); await act(async () => { fireEvent.press(b); await drenar(); }); expect(screen).toHavePathname("/piggy");
 await act(async () => { fireEvent.press(b); await drenar(); }); expect(screen).toHavePathname("/piggy"); expect(pilha(r)).toEqual(antes);
 // A aba Piggy é só o chat (sem Minha conta nem Retomar): a trava de entrada do Resumo não pode ter ficado presa.
 await act(async () => { router.navigate("/resumo"); await drenar(); });
 await apertar("Abrir minha conta"); expect(screen.getByText("Minha conta")).toBeTruthy(); await apertar("Fechar"); await apertar("Retomar conexão"); expect(pilha(r)?.filter((p) => p.nome === "open-finance-volta")).toHaveLength(1);
});

it.each([["Resumo do mês, abrir Gastos", true], ["Resumo do mês, abrir Gastos", false], ["01/10/2026", true], ["01/10/2026", false]])("Retomar × link do widget %s, recovery primeiro=%s, usa a mesma reserva", async (irma, recoveryPrimeiro) => {
 servidor(); const r = renderRouter("./app", { initialUrl: "/" }); await waitFor(() => expect(screen.getByRole("button", { name: "Retomar conexão" })).toBeEnabled());
 const a = screen.getByRole("button", { name: "Retomar conexão" });
 // O toque no dia abre a folha do dia (PR 2b); quem navega ao Extrato é o botão dela, que disputa a mesma reserva de navegação.
 if (irma === "01/10/2026") await act(async () => { fireEvent.press(screen.getByTestId("calendario-dia-2026-10-01")); await drenar(); });
 const b = irma !== "01/10/2026" ? screen.getByRole("button", { name: irma }) : screen.getByRole("button", { name: "Ver extrato do dia" });
 await act(async () => { fireEvent.press(recoveryPrimeiro ? a : b); fireEvent.press(recoveryPrimeiro ? b : a); await drenar(); });
 expect(pilha(r)?.map((p) => p.nome)).toEqual(recoveryPrimeiro ? ["(painel)", "open-finance-volta"] : ["(painel)"]);
 expect(screen).toHavePathname(recoveryPrimeiro ? "/open-finance-volta" : irma === "01/10/2026" ? "/extrato" : "/gastos");
 if (!recoveryPrimeiro && irma === "01/10/2026") expect(r.getSearchParams().dia).toBe("2026-10-01");
 expect(tokens()).toHaveLength(0); expect(posts()).toHaveLength(0);
});
