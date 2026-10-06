import { conferirVolta, JANELA_MS } from "@/features/openFinance/volta";
import { capturarItemBancario, iniciarTentativaBancaria, lerTentativaBancaria } from "@/storage/secure";
import { chamadas, prepararCaso, S } from "./auth_apoio";
import { dependencias, guardarSessaoOf, lista, posts, servidor, SESSAO_OF, VIVO } from "./open_finance_volta_apoio";

beforeEach(async () => { prepararCaso(); await guardarSessaoOf(S); });

it.each([1, 2])("retomada sem URL com %i bancos observados não toma B nem encerra antes do SDK B", async (quantidade) => {
  // B foi criada quando A ainda não existia no snapshot.
  const b = (await iniciarTentativaBancaria(1, SESSAO_OF, []))!;
  const a = { ...VIVO, provider_item_id: "banco_a" };
  const outros = quantidade === 1 ? [a] : [a, { ...a, id: 2, provider_item_id: "banco_c" }];
  servidor({ get: () => lista(...outros), post: () => lista({ ...VIVO, provider_item_id: "banco_b" }) });
  let entregou = false;
  const { d, relogio, ultimo } = dependencias({ esperar: async () => {
    if (!entregou) {
      expect(await lerTentativaBancaria(1)).toEqual(b);
      expect(posts()).toEqual([]);
      entregou = true;
      await capturarItemBancario("banco_b", b.tentativa_id);
    }
    relogio.t += 3_000;
  } });
  await conferirVolta(undefined, d);
  expect(entregou).toBe(true);
  expect(chamadas().filter((c) => c.caminho.endsWith("/pluggy-item")).map((c) => c.corpo)).toEqual([{ item: { id: "banco_b" } }]);
  expect(ultimo()).toEqual({ fase: "conectado", ui: VIVO.ui });
  expect(await lerTentativaBancaria(1)).toBeNull();
});

it.each([1, 2])("sem pista própria e %i bancos no servidor, espera janela e oferece Conexões sem associar", async (quantidade) => {
  const b = (await iniciarTentativaBancaria(1, SESSAO_OF, []))!;
  const a = { ...VIVO, provider_item_id: "banco_a" };
  servidor({ get: () => lista(a, ...(quantidade === 2 ? [{ ...a, id: 2, provider_item_id: "banco_c" }] : [])) });
  const { d, relogio, estados, ultimo } = dependencias();
  await conferirVolta(undefined, d);
  expect(relogio.t).toBeGreaterThanOrEqual(JANELA_MS);
  expect(await lerTentativaBancaria(1)).toEqual(b);
  expect(posts()).toEqual([]);
  expect(estados.some((e) => e.fase === "conectado")).toBe(false);
  expect(ultimo()).toEqual({ fase: "escolher-conexao" });
});

it("snapshot sem bancos e sem pista conserva B para recuperação posterior", async () => {
  const b = (await iniciarTentativaBancaria(1, SESSAO_OF, []))!;
  servidor({});
  const { d, ultimo } = dependencias();
  await conferirVolta(undefined, d);
  expect(posts()).toEqual([]);
  expect(await lerTentativaBancaria(1)).toEqual(b);
  expect(ultimo()).toEqual({ fase: "ainda-conferindo" });
});

it("fronteira recusa pista sem nonce, mas aceita pista do nonce originário", async () => {
  const b = (await iniciarTentativaBancaria(1, SESSAO_OF, []))!;
  await capturarItemBancario("banco_a", undefined);
  expect(await lerTentativaBancaria(1)).toEqual(b);
  await capturarItemBancario("banco_b", b.tentativa_id);
  expect(await lerTentativaBancaria(1)).toMatchObject({ tentativa_id: b.tentativa_id, item_id: "banco_b", autorizacao_recebida: true });
});
