import { carregarAcessoBancario, iniciarConexaoBancaria } from "@/features/openFinance/acesso";
import { guardarCredenciais, lerTentativaBancaria } from "@/storage/secure";
import { chamadas, falharEscrita, prepararCaso, resposta, rotear } from "./auth_apoio";
import { JWT_OF } from "./open_finance_volta_apoio";

const p = { user_id: 1, app_access: true, email: "teste@pigbank.test", of_banks_max: 2 };
const limite = { ok: true, of_banks_max: 2, em_uso: 0, pode_adicionar: true, code: null, message: null };
const c = { id: 1, provider_item_id: "item_a", institution_name: "Banco", status: "ACTIVE", status_reason: null,
  last_sync_at: "2026-10-05T12:00:00Z", reconnected_at: null, ui: { state: "updated", label: "Atualizado" } };
function servidor(opcoes: { perfil?: object; completed?: boolean; limite?: object; lista?: object[] } = {}) {
  rotear({
    "/auth/me": () => resposta(200, opcoes.perfil ?? p),
    "/onboarding/open-finance": () => resposta(200, { ok: true, completed: opcoes.completed ?? false, completed_at: opcoes.completed ? "2026-10-05T12:00:00Z" : null }),
    "/open-finance/1/limite": () => resposta(200, opcoes.limite ?? limite),
    "/open-finance/1": () => resposta(200, { connections: opcoes.lista ?? [] }),
    "/open-finance/1/connect-token": () => resposta(200, { ok: true, accessToken: "token-que-nao-persiste" }),
  });
}
beforeEach(async () => { prepararCaso(); await guardarCredenciais({ access: JWT_OF, refresh: "refresh" }); });

it("Free sem acesso não consulta estado bancário nem libera produto", async () => {
  servidor({ perfil: { ...p, app_access: false } });
  expect((await carregarAcessoBancario()).fase).toBe("sem-acesso");
  expect(chamadas().map((v) => v.caminho)).toEqual(["/auth/me"]);
});
it("campo app_access ausente não equivale a conta com direito", async () => {
  servidor({ perfil: { user_id: 1 } });
  await expect(carregarAcessoBancario()).rejects.toThrow("confirmar seu acesso");
});
it("banco sincronizado no site é reconhecido pela prova servidor", async () => {
  servidor({ completed: true });
  expect((await carregarAcessoBancario()).fase).toBe("inicio");
  expect(chamadas().map((v) => v.caminho)).toEqual(["/auth/me", "/onboarding/open-finance"]);
});
it("conclusão preservada permite Início mesmo sem bancos e com OF0", async () => {
  servidor({ completed: true, perfil: { ...p, of_banks_max: 0, cobranca_em_atraso: true } });
  expect((await carregarAcessoBancario()).fase).toBe("inicio");
});
it("carência sem primeira sincronização/OF0 oferece aviso, não conexão impossível", async () => {
  servidor({ perfil: { ...p, cobranca_em_atraso: true }, limite: { ...limite, of_banks_max: 0, pode_adicionar: false } });
  expect((await carregarAcessoBancario()).fase).toBe("cobranca-pendente");
});
it("checkpoint falso mantém primeiro onboarding mesmo que exista item", async () => {
  servidor({ lista: [c] });
  expect((await carregarAcessoBancario()).fase).toBe("conectar");
});
it("novo banco não abre widget se teto cheio; reconexão própria no teto funciona", async () => {
  servidor({ lista: [c], limite: { ...limite, em_uso: 2, pode_adicionar: false, code: "OF_BANK_LIMIT", message: "Limite atingido" } });
  await expect(iniciarConexaoBancaria()).rejects.toThrow("Limite atingido");
  expect(chamadas().some((v) => v.caminho.endsWith("connect-token"))).toBe(false);
  await expect(iniciarConexaoBancaria("item_a")).resolves.toHaveProperty("accessToken");
  expect(chamadas().at(-1)?.corpo).toEqual({ app_scheme: "pigbank-dev", item_id: "item_a", attempt_id: (await lerTentativaBancaria(1))!.tentativa_id });
  expect(await lerTentativaBancaria(1)).toMatchObject({ modo: "reconectar", item_id: "item_a" });
});
it("item alheio/inexistente não pede token de reconexão", async () => {
  servidor({ lista: [c] });
  await expect(iniciarConexaoBancaria("item_alheio")).rejects.toThrow("reconexão");
  expect(chamadas().some((v) => v.caminho.endsWith("connect-token"))).toBe(false);
});
it("cofre recusando marcador impede emissão do connect token", async () => {
  servidor(); falharEscrita(true);
  await expect(iniciarConexaoBancaria()).rejects.toThrow();
  expect(chamadas().some((v) => v.caminho.endsWith("connect-token"))).toBe(false);
});
it("cancelar a preparação não emite token nem grava tentativa", async () => {
  servidor();
  await expect(iniciarConexaoBancaria(undefined, () => true)).rejects.toThrow("outra conta");
  expect(await lerTentativaBancaria(1)).toBeNull();
});
