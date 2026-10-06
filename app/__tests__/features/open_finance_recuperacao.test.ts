import { conferirRetornoOficial } from "./open_finance_volta_apoio";
import type { Dependencias } from "@/features/openFinance/volta";
import { capturarItemBancario, iniciarTentativaBancaria, lerTentativaBancaria, marcarTentativaBancariaVista } from "@/storage/secure";
import { cofre, falharApagar, falharLeitura, prepararCaso, resposta } from "./auth_apoio";
import { dependencias, guardarSessaoOf, ITEM, lista, posts, servidor, SESSAO_OF, VIVO } from "./open_finance_volta_apoio";
import { S } from "./auth_apoio";

let origem: string;
const conferirVolta = (link: unknown, d: Dependencias) => conferirRetornoOficial(link, d, origem);
beforeEach(async () => { prepararCaso(); await guardarSessaoOf(S); origem = (await lerTentativaBancaria(1))!.tentativa_id; });
it("callback antigo sem tentativa apenas consulta, nunca registra", async () => {
  cofre.delete("pb.of.tentativa");
  servidor({});
  const { d, ultimo } = dependencias();
  await conferirVolta(ITEM, d);
  expect(posts()).toEqual([]);
  expect(ultimo()).toEqual({ fase: "ainda-conferindo" });
});
it("sem link nem onSuccess observa bancos do webhook sem afirmar a origem da tentativa", async () => {
  const original = await lerTentativaBancaria(1);
  servidor({ get: () => lista(VIVO) });
  const { d, ultimo } = dependencias();
  await conferirVolta(undefined, d);
  expect(ultimo()).toEqual({ fase: "escolher-conexao" });
  expect(posts()).toEqual([]);
  expect(await lerTentativaBancaria(1)).toEqual(original);
});
it("retorno capturado no widget permite registrar depois do cold start sem URL", async () => {
  await capturarItemBancario(ITEM, origem);
  servidor({});
  await conferirVolta(undefined, dependencias().d);
  expect(posts()).toHaveLength(1);
});
it("item visto antes de fechar app não é readotado se sumir depois", async () => {
  await capturarItemBancario(ITEM, origem);
  const t = (await lerTentativaBancaria(1))!;
  await marcarTentativaBancariaVista(t.tentativa_id);
  servidor({});
  const { d, ultimo } = dependencias();
  await conferirVolta(undefined, d);
  expect(posts()).toEqual([]);
  expect(ultimo()).toEqual({ fase: "organizando" });
});
it("409 da lápide encerra tentativa e informa nova conexão", async () => {
  servidor({ post: () => resposta(409, { detail: { code: "OF_ITEM_REMOVED" } }) });
  const { d, ultimo } = dependencias();
  await conferirVolta(ITEM, d);
  expect(ultimo()).toEqual({ fase: "erro", texto: "Esse banco foi desconectado. Inicie uma nova conexão." });
  expect(await lerTentativaBancaria(1)).toBeNull();
});
it("falha ao limpar cofre após lápide não rejeita retorno nem repete adoção", async () => {
  servidor({ post: () => { falharApagar(true); return resposta(409, { detail: { code: "OF_ITEM_REMOVED" } }); } });
  const { d, ultimo } = dependencias();
  await expect(conferirVolta(ITEM, d)).resolves.toBeUndefined();
  expect(posts()).toHaveLength(1);
  expect(ultimo()).toEqual({ fase: "erro", texto: "Esse banco foi desconectado. Inicie uma nova conexão." });
});
it("falha inicial do cofre informa recuperação sem deixar spinner", async () => {
  falharLeitura(true);
  const { d, ultimo } = dependencias();
  await expect(conferirVolta(ITEM, d)).resolves.toBeUndefined();
  expect(ultimo()).toEqual({ fase: "erro", texto: "Não conseguimos ler o retorno do banco neste aparelho. Tente de novo." });
});
it("POST com resposta perdida é reconhecido pelo próximo snapshot sem duplicar", async () => {
  let registrado = false;
  servidor({ get: () => registrado ? lista(VIVO) : lista(), post: () => { registrado = true; throw new TypeError("resposta perdida"); } });
  const { d, ultimo } = dependencias();
  await conferirVolta(ITEM, d);
  expect(posts()).toHaveLength(1);
  expect(ultimo()).toEqual({ fase: "conectado", ui: VIVO.ui });
});
it("mais de uma candidata sem item não adota por ordem da lista", async () => {
  servidor({ get: () => lista(VIVO, { ...VIVO, id: 2, provider_item_id: "item_b" }) });
  const { d, ultimo } = dependencias();
  await conferirVolta(undefined, d);
  expect(posts()).toEqual([]);
  expect(ultimo()).toEqual({ fase: "escolher-conexao" });
});

it.each(["updated", "partial", "error"])("reconexão não encerra com snapshot %s anterior ao consentimento atual", async (state) => {
  await iniciarTentativaBancaria(1, SESSAO_OF, [ITEM], ITEM);
  servidor({ get: () => lista({ ...VIVO, ui: { ...VIVO.ui, state } }) });
  const { d, ultimo } = dependencias();
  await conferirVolta(undefined, d);
  expect(ultimo()).toEqual({ fase: "ainda-conferindo" });
  expect(await lerTentativaBancaria(1)).toMatchObject({ item_id: ITEM, modo: "reconectar" });
  expect(posts()).toEqual([]);
});
it("reconexão com callback espera carimbo atual e sync após esse carimbo", async () => {
  const tentativa = await iniciarTentativaBancaria(1, SESSAO_OF, [ITEM], ITEM);
  await capturarItemBancario(ITEM, tentativa!.tentativa_id);
  let registrado = false;
  let consultas = 0;
  const marco = "2026-10-05T13:00:00Z";
  servidor({ get: () => { consultas += 1; return lista(registrado ? { ...VIVO, reconnected_at: marco, last_sync_at: consultas > 2 ? marco : VIVO.last_sync_at } : VIVO); },
    post: () => { registrado = true; return lista({ ...VIVO, reconnected_at: marco, ui: { state: "updating", label: "Atualizando…" } }); } });
  const { d, estados, ultimo } = dependencias();
  await conferirVolta(undefined, d);
  expect(posts()).toHaveLength(1);
  expect(estados.filter((e) => e.fase === "conectado" && e.ui.state === "updated")).toHaveLength(1);
  expect(ultimo()).toEqual({ fase: "conectado", ui: VIVO.ui });
  expect(await lerTentativaBancaria(1)).toBeNull();
});
it("snapshot com carimbo mais antigo que o anterior não confirma a reconexão atual", async () => {
  await iniciarTentativaBancaria(1, SESSAO_OF, [ITEM], ITEM, "2026-10-05T14:00:00Z");
  servidor({ get: () => lista({ ...VIVO, reconnected_at: "2026-10-05T11:00:00Z" }) });
  const { d, ultimo } = dependencias();
  await conferirVolta(undefined, d);
  expect(ultimo()).toEqual({ fase: "ainda-conferindo" });
  expect(await lerTentativaBancaria(1)).not.toBeNull();
});
