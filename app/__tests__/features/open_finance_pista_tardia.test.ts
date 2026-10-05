import { conferirVolta } from "@/features/openFinance/volta";
import { capturarItemBancario, guardarCredenciais, iniciarTentativaBancaria, lerTentativaBancaria } from "@/storage/secure";
import { chamadas, prepararCaso, S } from "./auth_apoio";
import { comEstado, dependencias, guardarSessaoOf, ITEM, lista, posts, servidor, SESSAO_OF } from "./open_finance_volta_apoio";

beforeEach(async () => { prepararCaso(); await guardarSessaoOf(S); });
it("pista tardia de outro banco não substitui item corrente nem libera POST para ele", async () => {
  await capturarItemBancario(ITEM);
  const original = await lerTentativaBancaria(1);
  servidor({ get: () => lista(), post: () => lista() });
  let recebeu = false;
  const { d, relogio } = dependencias({ esperar: async () => {
    if (!recebeu) { recebeu = true; await capturarItemBancario("outro_banco", original!.tentativa_id); }
    relogio.t += 3_000;
  } });
  await conferirVolta(undefined, d);
  expect(await lerTentativaBancaria(1)).toEqual(original);
  expect(posts()).toHaveLength(3);
  expect(chamadas().filter((c) => c.caminho.endsWith("/pluggy-item")).map((c) => c.corpo)).toEqual(Array(3).fill({ item: { id: ITEM } }));
});
it("rodada sem item não aproveita marcador novo criado durante a espera", async () => {
  servidor({ get: () => lista() });
  let nova: Awaited<ReturnType<typeof lerTentativaBancaria>> = null;
  const { d, relogio } = dependencias({ esperar: async () => {
    if (!nova) { nova = await iniciarTentativaBancaria(1, SESSAO_OF, []); await capturarItemBancario(ITEM, nova!.tentativa_id); nova = await lerTentativaBancaria(1); }
    relogio.t += 3_000;
  } });
  await conferirVolta(undefined, d);
  expect(posts()).toHaveLength(0);
  expect(await lerTentativaBancaria(1)).toEqual(nova);
});
it("callback tardio após troca de sessão não é adotado pela rodada anterior", async () => {
  servidor({ get: () => lista() });
  let mudou = false;
  const { d, relogio } = dependencias({ esperar: async () => {
    if (!mudou) { mudou = true; await guardarCredenciais({ access: "sessao-nova", refresh: "r_novo" }); await capturarItemBancario(ITEM); }
    relogio.t += 3_000;
  } });
  await conferirVolta(undefined, d);
  expect(posts()).toHaveLength(0);
});
it("pausa cancela antes de usar pista tardia; retomada mesma tentativa registra", async () => {
  servidor({ get: () => lista() });
  let cancelado = false;
  const { d } = dependencias({ cancelado: () => cancelado, esperar: async () => { await capturarItemBancario(ITEM); cancelado = true; } });
  await conferirVolta(undefined, d);
  expect(posts()).toHaveLength(0);
  await conferirVolta(undefined, dependencias().d);
  expect(posts()).toHaveLength(1);
});
it("pista tardia removida no próximo snapshot não permite adoção", async () => {
  let consultas = 0;
  servidor({ get: () => ++consultas === 1 ? lista() : lista(comEstado("removed")) });
  const { d, relogio, ultimo } = dependencias({ esperar: async () => {
    await capturarItemBancario(ITEM);
    relogio.t += 3_000;
  } });
  await conferirVolta(undefined, d);
  expect(posts()).toHaveLength(0);
  expect(await lerTentativaBancaria(1)).toBeNull();
  expect(ultimo()).toEqual({ fase: "erro", texto: "Esse banco foi desconectado. Inicie uma nova conexão." });
});
