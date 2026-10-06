import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { iniciarConexaoBancaria } from "@/features/openFinance/acesso";
import { definirWidgetAberto } from "@/features/openFinance/volta";
import { Button } from "@/ui/componentes/Button";
import { concluirTentativaBancaria, guardarCredenciais, iniciarTentativaBancaria, lerTentativaBancaria, TentativaBancariaPendente } from "@/storage/secure";
import { redirectSystemPath } from "../../app/+native-intent";
import { chamadas, cofre, prepararCaso, resposta, rotear, S, segurar } from "./auth_apoio";
import { guardarSessaoOf, ITEM, JWT_OF, SESSAO_OF, VIVO } from "./open_finance_volta_apoio";
import { desligarTrava, drenar, umIntervalo } from "./open_finance_volta_rota_apoio";

jest.mock("react-native-pluggy-connect", () => ({ PluggyConnect: () => jest.requireActual("react").createElement(jest.requireActual("react-native").View, { testID: "widget-substituicao" }) }));
function servidor() {
  const estado = { falhar: false, bancos: [] as typeof VIVO[] };
  rotear({
    "/auth/me": () => resposta(200, { user_id: 1, display_name: "Ana", app_access: true }),
    "/open-finance/1": () => resposta(200, { connections: estado.bancos }),
    "/open-finance/1/limite": () => resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null }),
    "/open-finance/1/connect-token": () => resposta(estado.falhar ? 503 : 200, estado.falhar ? {} : { ok: true, accessToken: "token" }),
    "/open-finance/1/pluggy-item": () => { estado.bancos = [VIVO]; return resposta(200, { connections: estado.bancos }); },
  });
  return estado;
}
const tokens = () => chamadas().filter((c) => c.caminho.endsWith("connect-token"));
const posts = () => chamadas().filter((c) => c.caminho.endsWith("pluggy-item"));
async function apertar(nome: string) {
  await waitFor(() => expect(screen.getByRole("button", { name: nome })).toBeEnabled());
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: nome })); await drenar(); });
}
async function aviso(url = "/autorizando") {
  renderRouter("./app", { initialUrl: url });
  if (url === "/conexoes") await apertar("Reconectar Nubank");
  await waitFor(() => expect(screen.getByRole("button", { name: "Iniciar nova tentativa" })).toBeEnabled());
  expect(tokens()).toHaveLength(0);
  expect(screen.queryByTestId("widget-substituicao")).toBeNull();
}
beforeEach(async () => { prepararCaso(); desligarTrava(); definirWidgetAberto(false); await guardarSessaoOf(S); servidor(); });

afterEach(() => jest.restoreAllMocks());
it("sem escolha, preparação central conserva A e não pede connect-token", async () => {
  const a = await lerTentativaBancaria(1);
  await expect(iniciarConexaoBancaria()).rejects.toBeInstanceOf(TentativaBancariaPendente);
  expect(await lerTentativaBancaria(1)).toEqual(a);
  expect(tokens()).toHaveLength(0);
});

it.each(["Cancelar", "Retomar conexão"])("callback UUID A durante aviso, depois %s mantém origem e registra com GET primeiro", async (acao) => {
  const a = (await lerTentativaBancaria(1))!;
  await aviso();
  await act(async () => {
    redirectSystemPath({ path: `pigbank-dev://open-finance-volta/${a.tentativa_id}?itemId=${ITEM}`, initial: true });
    await drenar();
  });
  expect(await lerTentativaBancaria(1)).toMatchObject({ tentativa_id: a.tentativa_id, item_id: ITEM });
  expect(posts()).toHaveLength(0);
  await apertar(acao);
  expect(tokens()).toHaveLength(0);
  if (acao === "Cancelar") {
    await waitFor(() => expect(screen).toHavePathname("/"));
    expect(await lerTentativaBancaria(1)).toMatchObject({ tentativa_id: a.tentativa_id, item_id: ITEM });
    await apertar("Retomar conexão");
  }
  await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
  expect(posts()).toHaveLength(1);
  expect(await lerTentativaBancaria(1)).toBeNull();
  const fluxo = chamadas().filter((c) => c.caminho === "/open-finance/1" || c.caminho.endsWith("pluggy-item"));
  expect(fluxo[0]?.caminho).toBe("/open-finance/1");
  expect(posts()[0]?.corpo).toEqual({ item: { id: ITEM } });
});

it.each(["Cancelar", "Retomar conexão"])("%s sem pista conserva A; callback oficial recebido depois é recuperado", async (acao) => {
  const a = (await lerTentativaBancaria(1))!;
  await aviso(); await apertar(acao);
  await waitFor(() => expect(screen).toHavePathname(acao === "Cancelar" ? "/" : "/open-finance-volta"));
  expect(await lerTentativaBancaria(1)).toEqual(a);
  await act(async () => {
    const destino = redirectSystemPath({ path: `pigbank-dev://open-finance-volta/${a.tentativa_id}?itemId=${ITEM}`, initial: false });
    if (acao === "Cancelar") router.navigate(destino!);
    await drenar();
  });
  if (acao === "Retomar conexão") {
    expect(await lerTentativaBancaria(1)).toMatchObject({ tentativa_id: a.tentativa_id, item_id: ITEM });
    await umIntervalo();
  }
  await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
  expect(tokens()).toHaveLength(0); expect(posts()).toHaveLength(1);
});

it("nova escolha dupla cria só B e callback A continua recusado", async () => {
  const a = (await lerTentativaBancaria(1))!;
  await aviso();
  const botao = screen.getByRole("button", { name: "Iniciar nova tentativa" });
  await act(async () => { fireEvent.press(botao); fireEvent.press(botao); await drenar(); });
  await waitFor(() => expect(screen.getByTestId("widget-substituicao")).toBeTruthy());
  const b = (await lerTentativaBancaria(1))!;
  expect(b.tentativa_id).not.toBe(a.tentativa_id);
  expect(tokens()).toHaveLength(1);
  expect(tokens()[0]?.corpo).toEqual(expect.objectContaining({ attempt_id: b.tentativa_id }));
  await act(async () => { redirectSystemPath({ path: `pigbank-dev://open-finance-volta/${a.tentativa_id}?itemId=${ITEM}`, initial: false }); await drenar(); });
  expect(await lerTentativaBancaria(1)).toEqual(b);
  expect(posts()).toHaveLength(0);
});

it.each(["nonce", "sessao", "limpeza"])("confirmar aviso A obsoleto por %s não grava nem pede token", async (mudanca) => {
  const a = (await lerTentativaBancaria(1))!;
  await aviso();
  const botao = screen.getByRole("button", { name: "Iniciar nova tentativa" });
  if (mudanca === "sessao") await guardarCredenciais({ access: "eyJhbGciOiJIUzI1NiJ9.eyJqdGkiOiJzZXNzYW8tYiJ9.assinatura", refresh: "r-b" });
  if (mudanca === "limpeza") await concluirTentativaBancaria(a.tentativa_id);
  else await iniciarTentativaBancaria(1, mudanca === "sessao" ? "sessao-b" : SESSAO_OF, [], undefined, undefined, mudanca === "sessao" ? undefined : a);
  const antes = cofre.get("pb.of.tentativa");
  await act(async () => { fireEvent.press(botao); await drenar(); });
  await act(drenar);
  expect(cofre.get("pb.of.tentativa")).toBe(antes);
  expect(tokens()).toHaveLength(0);
});

it("confirmação A concorrente compara dentro da fila: apenas uma substitui", async () => {
  const a = (await lerTentativaBancaria(1))!;
  const resultados = await Promise.all([
    iniciarTentativaBancaria(1, SESSAO_OF, [], undefined, undefined, a),
    iniciarTentativaBancaria(1, SESSAO_OF, [], undefined, undefined, a),
  ]);
  expect(resultados.filter(Boolean)).toHaveLength(1);
  expect(await lerTentativaBancaria(1)).toEqual(resultados[0]);
});

it("B assume durante snapshot da confirmação A: preparação não troca B nem emite token", async () => {
  const a = (await lerTentativaBancaria(1))!;
  const entrou = segurar(); const pausa = segurar();
  rotear({
    "/auth/me": () => resposta(200, { user_id: 1, app_access: true }),
    "/open-finance/1": async () => { entrou.soltar(); await pausa.promessa; return resposta(200, { connections: [] }); },
    "/open-finance/1/limite": () => resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null }),
  });
  const preparando = iniciarConexaoBancaria(undefined, () => false, a);
  await entrou.promessa;
  const b = await iniciarTentativaBancaria(1, SESSAO_OF, [], undefined, undefined, a);
  pausa.soltar();
  await expect(preparando).rejects.toThrow();
  expect(await lerTentativaBancaria(1)).toEqual(b);
  expect(tokens()).toHaveLength(0);
});

it("primeira conexão e retry após falha legítima dispensam aviso", async () => {
  await guardarCredenciais({ access: JWT_OF, refresh: S.refresh });
  cofre.delete("pb.of.tentativa");
  const estado = servidor(); estado.falhar = true;
  renderRouter("./app", { initialUrl: "/conectar-banco" });
  await apertar("Conectar meu banco");
  await waitFor(() => expect(screen.getByRole("button", { name: "Voltar e tentar de novo" })).toBeEnabled());
  expect(await lerTentativaBancaria(1)).toBeNull();
  await apertar("Voltar e tentar de novo"); estado.falhar = false;
  await apertar("Conectar meu banco");
  await waitFor(() => expect(screen.getByTestId("widget-substituicao")).toBeTruthy());
  expect(tokens()).toHaveLength(2);
  expect(screen.queryByRole("button", { name: "Iniciar nova tentativa" })).toBeNull();
});

it("reconectar o mesmo banco também exige escolher antes de substituir A", async () => {
  const anterior = (await lerTentativaBancaria(1))!;
  const a = await iniciarTentativaBancaria(1, SESSAO_OF, [ITEM], ITEM, null, anterior);
  const estado = servidor(); estado.bancos = [VIVO];
  await aviso("/conexoes");
  expect(await lerTentativaBancaria(1)).toEqual(a);
  await apertar("Iniciar nova tentativa");
  await waitFor(() => expect(screen.getByTestId("widget-substituicao")).toBeTruthy());
  expect(tokens()).toHaveLength(1);
  expect(tokens()[0]?.corpo).toEqual(expect.objectContaining({ item_id: ITEM }));
  expect((await lerTentativaBancaria(1))?.tentativa_id).not.toBe(a?.tentativa_id);
});

it("confirmação capturada antes de sair do foco não inicia tentativa oculta", async () => {
  const a = await lerTentativaBancaria(1);
  await aviso();
  const confirmar = screen.UNSAFE_getAllByType(Button).find((b) => b.props.rotulo === "Iniciar nova tentativa")!.props.onPress;
  await act(async () => { router.push("/configuracoes"); await drenar(); });
  await act(async () => { confirmar(); await drenar(); });
  expect(await lerTentativaBancaria(1)).toEqual(a);
  expect(tokens()).toHaveLength(0);
});
