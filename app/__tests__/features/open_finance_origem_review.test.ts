import { iniciarConexaoBancaria } from "@/features/openFinance/acesso";
import { conferirVolta, definirWidgetAberto } from "@/features/openFinance/volta";
import { capturarItemBancario, guardarCredenciais, iniciarTentativaBancaria, lerTentativaBancaria, trocarSe } from "@/storage/secure";
import { redirectSystemPath } from "../../app/+native-intent";
import { chamadas, cofre, prepararCaso, resposta, rotear, segurar, type Rota } from "./auth_apoio";
import { dependencias, JWT_OF, lista, SESSAO_OF, VIVO } from "./open_finance_volta_apoio";

function servidor(token: Rota = () => resposta(200, { ok: true, accessToken: "token" }), snapshot: Rota = () => lista()) {
  rotear({ "/auth/me": () => resposta(200, { user_id: 1, app_access: true }),
    "/open-finance/1": snapshot,
    "/open-finance/1/limite": () => resposta(200, { ok: true, of_banks_max: 2, em_uso: 0, pode_adicionar: true, message: null, code: null }),
    "/open-finance/1/connect-token": token,
    "/open-finance/1/pluggy-item": () => lista({ ...VIVO, provider_item_id: "banco_b" }),
  });
}
beforeEach(async () => { prepararCaso(); definirWidgetAberto(false); await guardarCredenciais({ access: JWT_OF, refresh: "r" }); servidor(); });
const posts = () => chamadas().filter((c) => c.caminho.endsWith("/pluggy-item"));
it("redirect legado de A não atribui A à tentativa nova B", async () => {
  await iniciarTentativaBancaria(1, SESSAO_OF, []);
  const b = await iniciarTentativaBancaria(1, SESSAO_OF, []);
  redirectSystemPath({ path: "pigbank://open-finance-volta?itemId=banco_a", initial: true });
  expect(await lerTentativaBancaria(1)).toEqual(b);
});
it("redirect com origem antiga A não atribui A à tentativa B mesmo com widget focado", async () => {
  const a = await iniciarTentativaBancaria(1, SESSAO_OF, []);
  const b = await iniciarTentativaBancaria(1, SESSAO_OF, []);
  definirWidgetAberto(true);
  expect(redirectSystemPath({ path: `pigbank://open-finance-volta/${a!.tentativa_id}?itemId=banco_a`, initial: false })).toBeNull();
  expect(await lerTentativaBancaria(1)).toEqual(b);
});
it("URL sem origem não prende item local A quando SDK entrega B depois", async () => {
  const b = await iniciarTentativaBancaria(1, SESSAO_OF, []);
  servidor(undefined, () => lista({ ...VIVO, provider_item_id: "banco_a" }));
  let entregou = false;
  const { d, relogio } = dependencias({ esperar: async () => { if (!entregou) { expect(await lerTentativaBancaria(1)).toEqual(b); expect(posts()).toHaveLength(0); entregou = true; await capturarItemBancario("banco_b", b!.tentativa_id); } relogio.t += 3_000; } });
  await conferirVolta("banco_a", d);
  expect(posts().map((c) => c.corpo)).toEqual([{ item: { id: "banco_b" } }]);
});
it("falha ao emitir token limpa somente marcador da preparação", async () => {
  servidor(() => resposta(503, { detail: "indisponível" }));
  await expect(iniciarConexaoBancaria()).rejects.toThrow();
  expect(await lerTentativaBancaria(1)).toBeNull();
});
it("cancelamento durante token não entrega widget nem conserva marcador fantasma", async () => {
  let cancelado = false;
  servidor(() => { cancelado = true; return resposta(200, { ok: true, accessToken: "token" }); });
  await expect(iniciarConexaoBancaria(undefined, () => cancelado)).rejects.toThrow();
  expect(await lerTentativaBancaria(1)).toBeNull();
});
it("cleanup de preparação A não apaga B criada enquanto o token A falha", async () => {
  const pausa = segurar();
  const recebeu = segurar();
  servidor(async () => { recebeu.soltar(); await pausa.promessa; return resposta(503, { detail: "indisponível" }); });
  const preparando = iniciarConexaoBancaria();
  await recebeu.promessa;
  const b = await iniciarTentativaBancaria(1, SESSAO_OF, []);
  pausa.soltar();
  await expect(preparando).rejects.toThrow();
  expect(await lerTentativaBancaria(1)).toEqual(b);
});
it("token entregue para widget conserva tentativa para callbacks/retorno frio", async () => {
  const token = await iniciarConexaoBancaria();
  expect(await lerTentativaBancaria(1)).toMatchObject({ tentativa_id: token.tentativa_id });
});
it("retorno frio com nonce próprio captura, sobrevive refresh e consulta antes de registrar", async () => {
  const b = await iniciarTentativaBancaria(1, SESSAO_OF, []);
  const destino = redirectSystemPath({ path: `pigbank://open-finance-volta/${b!.tentativa_id}?itemId=banco_b`, initial: true });
  expect(destino).toBe(`/open-finance-volta?itemId=banco_b&tentativaId=${b!.tentativa_id}`);
  await trocarSe("r", { access: JWT_OF, refresh: "r2" });
  expect(await lerTentativaBancaria(1)).toMatchObject({ item_id: "banco_b", tentativa_id: b!.tentativa_id });
  await conferirVolta("banco_b", dependencias().d, b!.tentativa_id);
  const fluxo = chamadas().filter((c) => c.caminho.startsWith("/open-finance/1"));
  expect(fluxo.map((c) => c.caminho)).toEqual(["/open-finance/1", "/open-finance/1/pluggy-item"]);
});
it.each(["?itemId=banco_b&tentativaId=duplicada", "?itemId=banco_b&attempt_id=duplicada", "?itemId=banco_b&itemId=banco_a"])("query duplicada %s não escreve na tentativa", async (query) => {
  const b = await iniciarTentativaBancaria(1, SESSAO_OF, []);
  redirectSystemPath({ path: `pigbank://open-finance-volta/${b!.tentativa_id}${query}`, initial: true });
  expect(await lerTentativaBancaria(1)).toEqual(b);
});
it("segmentos extras no path não fabricam origem da tentativa", async () => {
  const b = await iniciarTentativaBancaria(1, SESSAO_OF, []);
  redirectSystemPath({ path: `pigbank://open-finance-volta/${b!.tentativa_id}/${b!.tentativa_id}?itemId=banco_b`, initial: true });
  expect(await lerTentativaBancaria(1)).toEqual(b);
});
it("cancelar após escrever marcador e antes do token limpa preparação sem pedir token", async () => {
  await expect(iniciarConexaoBancaria(undefined, () => cofre.has("pb.of.tentativa"))).rejects.toThrow();
  expect(chamadas().some((c) => c.caminho.endsWith("connect-token"))).toBe(false);
  expect(await lerTentativaBancaria(1)).toBeNull();
});
it("token com contrato inválido também descarta preparação", async () => {
  servidor(() => resposta(200, { ok: true }));
  await expect(iniciarConexaoBancaria()).rejects.toThrow();
  expect(await lerTentativaBancaria(1)).toBeNull();
});
it("falha de preparação não apaga pista de possível autorização recebida no mesmo nonce", async () => {
  servidor(async () => { const t = (await lerTentativaBancaria(1))!; await capturarItemBancario("banco_b", t.tentativa_id); return resposta(503, { detail: "indisponível" }); });
  await expect(iniciarConexaoBancaria()).rejects.toThrow();
  expect(await lerTentativaBancaria(1)).toMatchObject({ item_id: "banco_b", autorizacao_recebida: true });
});
it("troca de sessão durante token não entrega A nem apaga preparação de B", async () => {
  const entrou = segurar(); const respostaPendente = segurar();
  servidor(async () => { entrou.soltar(); await respostaPendente.promessa; return resposta(200, { ok: true, accessToken: "token_antigo" }); });
  const preparando = iniciarConexaoBancaria();
  await entrou.promessa;
  await guardarCredenciais({ access: "eyJhbGciOiJIUzI1NiJ9.eyJqdGkiOiJzZXNzYW8tYiJ9.assinatura", refresh: "r_b" });
  const b = await iniciarTentativaBancaria(1, "sessao-b", []);
  respostaPendente.soltar();
  await expect(preparando).rejects.toThrow();
  expect(await lerTentativaBancaria(1)).toEqual(b);
});
