import { origemDaTentativa } from "./open_finance_volta_apoio";
import { definirWidgetAberto } from "@/features/openFinance/volta";
import { capturarItemBancario, concluirTentativaBancaria, guardarCredenciais, iniciarTentativaBancaria,
  lerTentativaBancaria, limparSessaoDe, trocarSe } from "@/storage/secure";
import { redirectSystemPath } from "../../app/+native-intent";
import { cofre, prepararCaso } from "./auth_apoio";
import { JWT_OF, SESSAO_OF } from "./open_finance_volta_apoio";

beforeEach(async () => { prepararCaso(); definirWidgetAberto(false); await guardarCredenciais({ access: JWT_OF, refresh: "r1" }); });
it("marcador sobrevive ao retorno frio e à rotação da mesma sessão", async () => {
  await iniciarTentativaBancaria(1, SESSAO_OF, []);
  redirectSystemPath({ path: `pigbank://open-finance-volta/${origemDaTentativa()}?itemId=item_a`, initial: true });
  await trocarSe("r1", { access: JWT_OF, refresh: "r2" });
  expect(await lerTentativaBancaria(1)).toMatchObject({ item_id: "item_a", sessao: SESSAO_OF });
  expect(cofre.get("pb.of.tentativa")).not.toContain(JWT_OF);
  expect(cofre.get("pb.of.tentativa")).not.toContain("r2");
});
it("deep link em widget focado captura item sem trocar rota", async () => {
  await iniciarTentativaBancaria(1, SESSAO_OF, []);
  definirWidgetAberto(true);
  expect(redirectSystemPath({ path: `pigbank://open-finance-volta/${origemDaTentativa()}?itemId=item_a`, initial: false })).toBeNull();
  expect(await lerTentativaBancaria(1)).toMatchObject({ item_id: "item_a" });
});
it("outra conta/sessão não lê nem sobrescreve marcador antigo", async () => {
  const a = await iniciarTentativaBancaria(1, SESSAO_OF, []);
  await guardarCredenciais({ access: "eyJhbGciOiJIUzI1NiJ9.eyJqdGkiOiJzZXNzYW8tYiJ9.assinatura", refresh: "r-b" });
  expect(await lerTentativaBancaria(1)).toBeNull();
  expect(await iniciarTentativaBancaria(1, SESSAO_OF, [])).toBeNull();
  await capturarItemBancario("item_alheio");
  expect(JSON.parse(cofre.get("pb.of.tentativa")!)).toEqual(a);
});
it("sair limpa marcador junto da sessão; conclusão velha não limpa tentativa nova", async () => {
  const a = (await iniciarTentativaBancaria(1, SESSAO_OF, []))!;
  const b = (await iniciarTentativaBancaria(1, SESSAO_OF, []))!;
  await concluirTentativaBancaria(a.tentativa_id);
  expect(await lerTentativaBancaria(1)).toEqual(b);
  await limparSessaoDe(JWT_OF, "r1");
  expect(cofre.has("pb.of.tentativa")).toBe(false);
});
it("callback não substitui alvo de reconexão ou item já capturado", async () => {
  await iniciarTentativaBancaria(1, SESSAO_OF, ["item_a"], "item_a");
  await capturarItemBancario("item_b");
  expect(await lerTentativaBancaria(1)).toMatchObject({ item_id: "item_a" });
});

it("itemId repetido/malformado no link não captura candidato arbitrário", async () => {
  await iniciarTentativaBancaria(1, SESSAO_OF, []);
  definirWidgetAberto(true);
  redirectSystemPath({ path: "pigbank://open-finance-volta?itemId=item_a&itemId=item_b", initial: false });
  expect((await lerTentativaBancaria(1))?.item_id).toBeUndefined();
});

it("callback tardio do widget não captura item na tentativa seguinte da mesma sessão", async () => {
  const antiga = await iniciarTentativaBancaria(1, SESSAO_OF, []);
  const nova = await iniciarTentativaBancaria(1, SESSAO_OF, []);
  await capturarItemBancario("item_a", antiga!.tentativa_id);
  expect(await lerTentativaBancaria(1)).toEqual(nova);
});
