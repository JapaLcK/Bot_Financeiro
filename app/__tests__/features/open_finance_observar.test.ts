import { conferirVolta } from "@/features/openFinance/volta";
import { capturarItemBancario, guardarCredenciais, iniciarTentativaBancaria, lerTentativaBancaria } from "@/storage/secure";
import { chamadas, cofre, prepararCaso, resposta, rotear, S, segurar } from "./auth_apoio";
import { dependencias, guardarSessaoOf, lista, VIVO } from "./open_finance_volta_apoio";
import { drenar } from "./open_finance_volta_rota_apoio";

const B = "banco_b";
const bancoB = { ...VIVO, id: 2, provider_item_id: B, ui: { ...VIVO.ui, label: "Banco B atualizado" } };
const marker = () => cofre.get("pb.of.tentativa");
const writes = () => chamadas().filter((c) => c.caminho.endsWith("pluggy-item") || c.caminho.endsWith("connect-token"));
beforeEach(async () => { prepararCaso(); await guardarSessaoOf(S); });
function servidor(bancos: unknown[] = [bancoB]) {
  rotear({ "/open-finance/1": () => lista(...bancos), "/open-finance/1/pluggy-item": () => lista(...bancos) });
}

it.each([false, true])("somente GET acompanha banco próprio sem marcador=%s", async (semMarcador) => {
  servidor(); if (semMarcador) cofre.delete("pb.of.tentativa");
  const antes = marker(); const { d, ultimo } = dependencias();
  await conferirVolta(B, d, undefined, "acompanhar");
  expect(ultimo()).toEqual({ fase: "conectado", ui: bancoB.ui });
  expect(marker()).toBe(antes); expect(writes()).toHaveLength(0);
});

it("observar o mesmo item de A não marca visto nem conclui A", async () => {
  const a = (await lerTentativaBancaria(1))!;
  await capturarItemBancario(VIVO.provider_item_id, a.tentativa_id);
  servidor([VIVO]); const antes = marker();
  const { d, ultimo } = dependencias(); await conferirVolta(VIVO.provider_item_id, d, a.tentativa_id, "acompanhar");
  expect(ultimo()).toEqual({ fase: "conectado", ui: VIVO.ui });
  expect(marker()).toBe(antes); expect(writes()).toHaveLength(0);
});

it.each(["foreign", "desaparecido"])("item %s ausente da lista própria não é adotado nem usa A", async () => {
  servidor([VIVO]); const a = (await lerTentativaBancaria(1))!;
  await capturarItemBancario(VIVO.provider_item_id, a.tentativa_id);
  const antes = marker(); const { d, ultimo } = dependencias();
  await conferirVolta(B, d, a.tentativa_id, "acompanhar");
  expect(ultimo()).toEqual({ fase: "sem-item" });
  expect(marker()).toBe(antes); expect(writes()).toHaveLength(0);
});

it.each([undefined, null, "", "outro/banco", [B, VIVO.provider_item_id]])("item inválido %j fecha antes do GET", async (item) => {
  servidor(); const antes = marker(); const { d, ultimo } = dependencias();
  await conferirVolta(item, d, undefined, "acompanhar");
  expect(ultimo()).toEqual({ fase: "sem-item" });
  expect(chamadas()).toHaveLength(0); expect(marker()).toBe(antes);
});

it.each(["trusted", "", null, ["acompanhar", "acompanhar"]])("modo inválido %j não cai em callback autorizado", async (modo) => {
  servidor(); const a = (await lerTentativaBancaria(1))!; const antes = marker();
  const { d, ultimo } = dependencias(); await conferirVolta(B, d, a.tentativa_id, modo);
  expect(ultimo()).toEqual({ fase: "sem-item" });
  expect(chamadas()).toHaveLength(0); expect(marker()).toBe(antes);
});

it.each(["removed", "item_missing"])("lápide %s não conclui tentativa mesmo com mesmo item", async (state) => {
  const a = (await lerTentativaBancaria(1))!;
  await capturarItemBancario(B, a.tentativa_id);
  servidor([{ ...bancoB, ui: { ...bancoB.ui, state } }]); const antes = marker();
  const { d, ultimo } = dependencias(); await conferirVolta(B, d, a.tentativa_id, "acompanhar");
  expect(ultimo()).toEqual({ fase: "erro", texto: "Esse banco foi desconectado. Inicie uma nova conexão." });
  expect(marker()).toBe(antes); expect(writes()).toHaveLength(0);
});

it("Free não consulta financeiro nem promove checkpoint", async () => {
  rotear({ "/auth/me": () => resposta(200, { user_id: 1, app_access: false }), "/open-finance/1": () => lista(bancoB) });
  const antes = marker(); const { d, ultimo } = dependencias();
  await conferirVolta(B, d, undefined, "acompanhar");
  expect(ultimo()).toEqual({ fase: "erro", texto: "Acesso indisponível. Confira sua conta no Início." });
  expect(chamadas().map((c) => c.caminho)).toEqual(["/auth/me"]);
  expect(marker()).toBe(antes);
});

it("snapshot que desaparece durante coleta fecha sem adotar A", async () => {
  let n = 0;
  rotear({ "/open-finance/1": () => ++n === 1 ? lista({ ...bancoB, ui: { ...bancoB.ui, state: "updating" } }) : lista(VIVO) });
  const antes = marker(); const { d, estados, ultimo } = dependencias();
  await conferirVolta(B, d, undefined, "acompanhar");
  expect(estados).toContainEqual({ fase: "conectado", ui: { ...bancoB.ui, state: "updating" } });
  expect(ultimo()).toEqual({ fase: "sem-item" }); expect(writes()).toHaveLength(0); expect(marker()).toBe(antes);
});

it("cancelamento com GET pendente não mostra resultado nem toca A", async () => {
  const pausa = segurar(); const entrou = segurar(); let cancelado = false;
  rotear({ "/open-finance/1": async () => { entrou.soltar(); await pausa.promessa; return lista(bancoB); } });
  const antes = marker(); const { d, estados } = dependencias({ cancelado: () => cancelado });
  const trabalho = conferirVolta(B, d, undefined, "acompanhar"); await entrou.promessa;
  cancelado = true; pausa.soltar(); await trabalho;
  expect(estados).toEqual([{ fase: "conferindo", instavel: false }]); expect(marker()).toBe(antes); expect(writes()).toHaveLength(0);
});

it("resposta da sessão anterior não exibe banco nem altera nova tentativa", async () => {
  const pausa = segurar(); const entrou = segurar();
  rotear({ "/open-finance/1": async () => { entrou.soltar(); await pausa.promessa; return lista(bancoB); } });
  const { d, estados, expirou } = dependencias(); const trabalho = conferirVolta(B, d, undefined, "acompanhar");
  await entrou.promessa;
  await guardarCredenciais({ access: "eyJhbGciOiJIUzI1NiJ9.eyJqdGkiOiJzZXNzYW8tMiJ9.assinatura", refresh: "refresh-2" });
  await iniciarTentativaBancaria(2, "sessao-2", []); const nova = marker();
  pausa.soltar(); await trabalho; await drenar();
  expect(estados).toEqual([{ fase: "conferindo", instavel: false }]); expect(expirou).not.toHaveBeenCalled();
  expect(marker()).toBe(nova); expect(writes()).toHaveLength(0);
});

it("coleta ainda em curso termina janela em Organizando sem tocar o marcador", async () => {
  servidor([{ ...bancoB, ui: { ...bancoB.ui, state: "updating" } }]);
  const antes = marker(); const { d, ultimo, relogio } = dependencias();
  await conferirVolta(B, d, undefined, "acompanhar");
  expect(ultimo()).toEqual({ fase: "organizando" }); expect(relogio.t).toBe(300_000);
  expect(marker()).toBe(antes); expect(writes()).toHaveLength(0);
});

it("409 de item removido em observação não apaga A mesmo quando A tem o mesmo item", async () => {
  const a = (await lerTentativaBancaria(1))!; await capturarItemBancario(B, a.tentativa_id);
  rotear({ "/open-finance/1": () => resposta(409, { detail: { code: "OF_ITEM_REMOVED" } }) });
  const antes = marker(); const { d, ultimo } = dependencias(); await conferirVolta(B, d, undefined, "acompanhar");
  expect(ultimo()).toEqual({ fase: "erro", texto: "Esse banco foi desconectado. Inicie uma nova conexão." });
  expect(marker()).toBe(antes); expect(writes()).toHaveLength(0);
});

it("cancelamento durante perfil não mostra gate tardio nem consulta bancos", async () => {
  const pausa = segurar(); const entrou = segurar(); let cancelado = false;
  rotear({ "/auth/me": async () => { entrou.soltar(); await pausa.promessa; return resposta(200, { user_id: 1, app_access: false }); } });
  const antes = marker(); const { d, estados } = dependencias({ cancelado: () => cancelado });
  const trabalho = conferirVolta(B, d, undefined, "acompanhar"); await entrou.promessa;
  cancelado = true; pausa.soltar(); await trabalho;
  expect(estados).toEqual([{ fase: "conferindo", instavel: false }]); expect(marker()).toBe(antes);
  expect(chamadas().map((c) => c.caminho)).toEqual(["/auth/me"]);
});
