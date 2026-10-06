import { conferirRetornoOficial } from "./open_finance_volta_apoio";
import type { Dependencias } from "@/features/openFinance/volta";
import { capturarItemBancario, iniciarTentativaBancaria, lerTentativaBancaria } from "@/storage/secure";
import { prepararCaso, S } from "./auth_apoio";
import { dependencias, guardarSessaoOf, ITEM, lista, posts, servidor, SESSAO_OF, VIVO } from "./open_finance_volta_apoio";

let origem: string;
const conferirVolta = (link: unknown, d: Dependencias) => conferirRetornoOficial(link, d, origem);
beforeEach(async () => { prepararCaso(); await guardarSessaoOf(S); origem = (await lerTentativaBancaria(1))!.tentativa_id; });

it("pista recebida depois do primeiro GET sem item é usada pela rodada seguinte", async () => {
  let consultas = 0;
  servidor({ get: () => { consultas += 1; return lista(); } });
  const { d, ultimo } = dependencias({ esperar: async () => {
    if (consultas === 1) await capturarItemBancario(ITEM, origem);
    relogio.t += 3_000;
  } });
  const relogio = { t: 0 };
  d.agora = () => relogio.t;
  await conferirVolta(undefined, d);
  expect(posts()).toHaveLength(1);
  expect(ultimo()).toEqual({ fase: "conectado", ui: VIVO.ui });
});

it("controle positivo: pista presente antes da conferência sem URL registra e conclui", async () => {
  await capturarItemBancario(ITEM, origem);
  servidor({});
  const { d, ultimo } = dependencias();
  await conferirVolta(undefined, d);
  expect(posts()).toHaveLength(1);
  expect(ultimo()).toEqual({ fase: "conectado", ui: VIVO.ui });
  expect(await lerTentativaBancaria(1)).toBeNull();
});

it.each(["updated", "partial"])("reconexão %s não conclui com carimbo igual ao baseline", async (state) => {
  const marco = "2026-10-05T13:00:00Z";
  await iniciarTentativaBancaria(1, SESSAO_OF, [ITEM], ITEM, marco);
  servidor({ get: () => lista({ ...VIVO, reconnected_at: marco, last_sync_at: marco, ui: { state, label: state } }) });
  const { d, ultimo } = dependencias();
  await conferirVolta(undefined, d);
  expect(posts()).toHaveLength(0);
  expect(ultimo()).toEqual({ fase: "ainda-conferindo" });
  expect(await lerTentativaBancaria(1)).not.toBeNull();
});

it.each(["updated", "partial"])("controle positivo: reconexão %s com carimbo avançado e sync igual conclui", async (state) => {
  const antigo = "2026-10-05T12:00:00Z";
  const marco = "2026-10-05T13:00:00Z";
  await iniciarTentativaBancaria(1, SESSAO_OF, [ITEM], ITEM, antigo);
  const ui = { state, label: state, detail: null };
  servidor({ get: () => lista({ ...VIVO, reconnected_at: marco, last_sync_at: marco, ui }) });
  const { d, ultimo } = dependencias();
  await conferirVolta(undefined, d);
  expect(posts()).toHaveLength(0);
  expect(ultimo()).toEqual({ fase: "conectado", ui });
  expect(await lerTentativaBancaria(1)).toBeNull();
});

it("sync um milissegundo anterior ao carimbo novo mantém recuperação sem repetir POST", async () => {
  const marco = "2026-10-05T13:00:00.000Z";
  await iniciarTentativaBancaria(1, SESSAO_OF, [ITEM], ITEM, null);
  servidor({ get: () => lista({ ...VIVO, reconnected_at: marco, last_sync_at: "2026-10-05T12:59:59.999Z" }) });
  const { d, estados, ultimo } = dependencias();
  await conferirVolta(undefined, d);
  expect(posts()).toHaveLength(0);
  expect(estados.some((e) => e.fase === "conectado" && e.ui.state === "updated")).toBe(false);
  expect(ultimo()).toEqual({ fase: "organizando" });
  expect(await lerTentativaBancaria(1)).toMatchObject({ visto_no_servidor: true });
});

it.each(["removed", "item_missing"])("callback de outro banco %s não apaga tentativa corrente", async (state) => {
  await capturarItemBancario(ITEM, origem);
  const original = await lerTentativaBancaria(1);
  servidor({ get: () => lista({ ...VIVO, id: 2, provider_item_id: "outro_banco", ui: { state, label: state } }) });
  await conferirVolta("outro_banco", dependencias().d);
  expect(posts()).toHaveLength(0);
  expect(await lerTentativaBancaria(1)).toEqual(original);
});

it("callback com nonce corrente captura; nonce anterior não marca autorização da reconexão nova", async () => {
  const antiga = (await iniciarTentativaBancaria(1, SESSAO_OF, [ITEM], ITEM))!;
  const nova = (await iniciarTentativaBancaria(1, SESSAO_OF, [ITEM], ITEM))!;
  await capturarItemBancario(ITEM, antiga.tentativa_id);
  expect(await lerTentativaBancaria(1)).toEqual(nova);
  await capturarItemBancario(ITEM, nova.tentativa_id);
  expect(await lerTentativaBancaria(1)).toMatchObject({ tentativa_id: nova.tentativa_id, autorizacao_recebida: true });
});
