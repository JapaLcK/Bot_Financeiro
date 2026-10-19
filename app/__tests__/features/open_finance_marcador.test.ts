import { definirWidgetAberto } from "@/features/openFinance/volta";
import { conferirRetornoOficial, dependencias, origemDaTentativa, ITEM, lista, posts, servidor, VIVO } from "./open_finance_volta_apoio";
import { capturarItemBancario, concluirTentativaBancaria, guardarCredenciais, iniciarTentativaBancaria,
  lerTentativaBancaria, limparSessaoDe, PRAZO_TENTATIVA_MS, TentativaBancariaPendente, trocarSe } from "@/storage/secure";
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
  await capturarItemBancario("item_alheio", a!.tentativa_id);
  expect(JSON.parse(cofre.get("pb.of.tentativa")!)).toEqual(a);
});
it("sair limpa marcador junto da sessão; conclusão velha não limpa tentativa nova", async () => {
  const a = (await iniciarTentativaBancaria(1, SESSAO_OF, []))!;
  const b = (await iniciarTentativaBancaria(1, SESSAO_OF, [], undefined, undefined, (await lerTentativaBancaria(1)) ?? undefined))!;
  await concluirTentativaBancaria(a.tentativa_id);
  expect(await lerTentativaBancaria(1)).toEqual(b);
  await limparSessaoDe(JWT_OF, "r1");
  expect(cofre.has("pb.of.tentativa")).toBe(false);
});
it("callback não substitui alvo de reconexão ou item já capturado", async () => {
  const tentativa = await iniciarTentativaBancaria(1, SESSAO_OF, ["item_a"], "item_a");
  await capturarItemBancario("item_b", tentativa!.tentativa_id);
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
  const nova = await iniciarTentativaBancaria(1, SESSAO_OF, [], undefined, undefined, (await lerTentativaBancaria(1)) ?? undefined);
  await capturarItemBancario("item_a", antiga!.tentativa_id);
  expect(await lerTentativaBancaria(1)).toEqual(nova);
});

// Marcador esquecido (o dono tocou num banco sem querer): vence em PRAZO_TENTATIVA_MS e o Descartar do aviso usa `concluirTentativaBancaria`.
const idade = (ms: number) => cofre.set("pb.of.tentativa", JSON.stringify({ ...JSON.parse(cofre.get("pb.of.tentativa")!), iniciada_em: Date.now() - ms }));
describe("prazo do marcador", () => {
  it("vencido some da leitura; dentro do prazo aparece (sem apagar nada no cofre)", async () => {
    await iniciarTentativaBancaria(1, SESSAO_OF, []);
    idade(PRAZO_TENTATIVA_MS - 1_000);
    expect(await lerTentativaBancaria(1)).not.toBeNull();
    idade(PRAZO_TENTATIVA_MS + 1_000);
    expect(await lerTentativaBancaria(1)).toBeNull();
    expect(cofre.has("pb.of.tentativa")).toBe(true);
  });
  it("vencido não trava uma conexão nova; recente continua pedindo a escolha", async () => {
    const velha = (await iniciarTentativaBancaria(1, SESSAO_OF, []))!;
    idade(PRAZO_TENTATIVA_MS - 1_000);
    await expect(iniciarTentativaBancaria(1, SESSAO_OF, [])).rejects.toBeInstanceOf(TentativaBancariaPendente);
    idade(PRAZO_TENTATIVA_MS + 1_000);
    const nova = await iniciarTentativaBancaria(1, SESSAO_OF, []);
    expect(nova).not.toBeNull();
    expect(nova!.tentativa_id).not.toBe(velha.tentativa_id);
  });
  it("'Iniciar nova tentativa' com o marcador vencido na tela começa uma nova; com outro marcador no lugar continua recusando", async () => {
    const velha = (await iniciarTentativaBancaria(1, SESSAO_OF, []))!;
    idade(PRAZO_TENTATIVA_MS + 1_000);
    const nova = await iniciarTentativaBancaria(1, SESSAO_OF, [], undefined, undefined, { sessao: SESSAO_OF, tentativa_id: velha.tentativa_id });
    expect(nova).not.toBeNull();
    expect(nova!.tentativa_id).not.toBe(velha.tentativa_id);
    // B gravada depois: a confirmação velha (A) não a apaga.
    expect(await iniciarTentativaBancaria(1, SESSAO_OF, [], undefined, undefined, { sessao: SESSAO_OF, tentativa_id: velha.tentativa_id })).toBeNull();
    // Concluído (e não vencido) em outro lugar: segue recusando, como o teste de "limpeza" da substituição.
    await concluirTentativaBancaria(nova!.tentativa_id);
    expect(await iniciarTentativaBancaria(1, SESSAO_OF, [], undefined, undefined, { sessao: SESSAO_OF, tentativa_id: nova!.tentativa_id })).toBeNull();
  });
  it("captura com marcador vencido não faz nada; recente captura", async () => {
    const a = (await iniciarTentativaBancaria(1, SESSAO_OF, []))!;
    idade(PRAZO_TENTATIVA_MS + 1_000);
    const antes = cofre.get("pb.of.tentativa");
    await capturarItemBancario(ITEM, a.tentativa_id);
    expect(cofre.get("pb.of.tentativa")).toBe(antes);
    idade(PRAZO_TENTATIVA_MS - 1_000);
    await capturarItemBancario(ITEM, a.tentativa_id);
    expect(await lerTentativaBancaria(1)).toMatchObject({ item_id: ITEM });
  });
});

describe("descartar (concluirTentativaBancaria)", () => {
  const SESSAO_B = "sessao-b";
  const JWT_B = "eyJhbGciOiJIUzI1NiJ9.eyJqdGkiOiJzZXNzYW8tYiJ9.assinatura";
  it("descartar o marcador da sessão A não apaga o da sessão B; o id certo apaga", async () => {
    const a = (await iniciarTentativaBancaria(1, SESSAO_OF, []))!;
    await guardarCredenciais({ access: JWT_B, refresh: "r-b" });
    const b = (await iniciarTentativaBancaria(1, SESSAO_B, []))!;
    await concluirTentativaBancaria(a.tentativa_id);
    expect(await lerTentativaBancaria(1)).toEqual(b);
    await concluirTentativaBancaria(b.tentativa_id);
    expect(cofre.has("pb.of.tentativa")).toBe(false);
  });
  // Sem `await` entre as duas chamadas: a fila do cofre decide. Fora dela, a captura (que leu o marcador antes do descarte) o regrava depois.
  it.each(["descartar e captura", "captura e descartar"])("corrida %s termina sem marcador e sem POST", async (ordem) => {
    const a = (await iniciarTentativaBancaria(1, SESSAO_OF, []))!;
    const descartar = () => concluirTentativaBancaria(a.tentativa_id), capturar = () => capturarItemBancario(ITEM, a.tentativa_id);
    await Promise.all(ordem === "descartar e captura" ? [descartar(), capturar()] : [capturar(), descartar()]);
    expect(cofre.has("pb.of.tentativa")).toBe(false);
    servidor({ get: () => lista(VIVO) });
    await conferirRetornoOficial(ITEM, dependencias().d, a.tentativa_id);
    expect(posts()).toEqual([]);
  });
});
