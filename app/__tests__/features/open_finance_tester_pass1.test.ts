import { conferirVolta } from "@/features/openFinance/volta";
import { capturarItemBancario, lerTentativaBancaria } from "@/storage/secure";
import { cofre, prepararCaso, S } from "./auth_apoio";
import { dependencias, guardarSessaoOf, ITEM, lista, posts, servidor, VIVO } from "./open_finance_volta_apoio";

beforeEach(async () => { prepararCaso(); await guardarSessaoOf(S); });

it("callback de outro banco vivo não cancela tentativa pendente do banco atual", async () => {
  await capturarItemBancario(ITEM);
  const original = await lerTentativaBancaria(1);
  const antigo = { ...VIVO, id: 2, provider_item_id: "banco_antigo" };
  servidor({ get: () => lista(antigo) });
  await conferirVolta("banco_antigo", dependencias().d);
  expect(posts()).toEqual([]);
  expect(await lerTentativaBancaria(1)).toEqual(original);
});

it("controle positivo: callback do item corrente conclui a tentativa corrente", async () => {
  await capturarItemBancario(ITEM);
  servidor({ get: () => lista(VIVO) });
  await conferirVolta(ITEM, dependencias().d);
  expect(await lerTentativaBancaria(1)).toBeNull();
});

it("marcador com mais de uma hora não autoriza POST", async () => {
  await capturarItemBancario(ITEM);
  const t = (await lerTentativaBancaria(1))!;
  cofre.set("pb.of.tentativa", JSON.stringify({ ...t, iniciada_em: Date.now() - 60 * 60_000 - 1_000 }));
  servidor({});
  await conferirVolta(undefined, dependencias().d);
  expect(posts()).toEqual([]);
});

it.each([[], [ITEM, "x"], "", "💰", "x".repeat(65), "item%20a", null])("entrada não confiável %p não registra arbitrariamente", async (entrada) => {
  cofre.delete("pb.of.tentativa");
  servidor({});
  await conferirVolta(entrada, dependencias().d);
  expect(posts()).toEqual([]);
});

it("cancelar durante GET impede registro e escrita de callback no estado", async () => {
  let cancelado = false;
  servidor({ get: () => { cancelado = true; return lista(); } });
  const { d, estados } = dependencias({ cancelado: () => cancelado });
  await conferirVolta(ITEM, d);
  expect(posts()).toEqual([]);
  expect(estados).toEqual([{ fase: "conferindo", instavel: false }]);
});

// A concorrência é provada neste seam; navegação nativa duplicando instâncias não foi reproduzida.
it("duas rodadas simultâneas do mesmo marcador não enviam dois POSTs", async () => {
  await capturarItemBancario(ITEM);
  servidor({ get: () => lista(), post: async () => { await Promise.resolve(); return lista(VIVO); } });
  await Promise.all([conferirVolta(ITEM, dependencias().d), conferirVolta(ITEM, dependencias().d)]);
  expect(posts()).toHaveLength(1);
});
