import { ErroDeApi, RequisicaoSuperada, chamarFluxo } from "@/api/client";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, resposta } from "./auth_apoio";
import { respostaFluxo } from "./conversa_apoio";

const ROTA = "/ai/chat/stream", OPC = { metodo: "POST" as const, corpo: { message: "oi" } };
const url = (u: unknown) => new URL(String(u)).pathname;
const bearer = (r: RequestInit) => (r.headers as Record<string, string>).Authorization;
const fluxo = (...eventos: object[]) => { const f = respostaFluxo(); eventos.forEach((e) => f.empurrar(e)); f.fechar(); return f; };
beforeEach(async () => { prepararCaso(); await guardarCredenciais({ access: "velho", refresh: "rt_velho" }); });
afterEach(() => { jest.restoreAllMocks(); });

it("401 de sessão: renova pelo fetch global e reenvia o POST pelo expo/fetch; eventos entregues (2 POSTs, 1 refresh)", async () => {
  const doExpo = jest.spyOn(jest.requireMock("expo/fetch") as { fetch: typeof fetch }, "fetch"); let n = 0;
  fetchFalso.mockImplementation(async (u: string) => {
    if (url(u) === "/auth/refresh") return resposta(200, { access_token: "novo", refresh_token: "rt_novo", dashboard_token: "d", expires_in: 900 });
    return ++n === 1 ? resposta(401, {}) : fluxo({ tipo: "delta", texto: "oi" }, { tipo: "fim" });
  });
  const vistos: unknown[] = []; await chamarFluxo(ROTA, OPC, (e) => vistos.push(e));
  expect(vistos).toHaveLength(2);
  const posts = fetchFalso.mock.calls.filter(([u]) => url(u) === ROTA), refresh = fetchFalso.mock.calls.filter(([u]) => url(u) === "/auth/refresh");
  expect(posts).toHaveLength(2); expect(refresh).toHaveLength(1); expect(bearer(posts[1]![1])).toBe("Bearer novo");
  expect(doExpo.mock.calls.map(([u]) => url(u))).toEqual([ROTA, ROTA]); // o refresh NÃO passou pelo expo/fetch
});
it("cofre trocado entre a resposta e a leitura: RequisicaoSuperada e `aoEvento` nunca é chamado", async () => {
  fetchFalso.mockImplementation(async () => { await guardarCredenciais({ access: "b", refresh: "rt_b" }); return fluxo({ tipo: "delta", texto: "da conta A" }); });
  const aoEvento = jest.fn();
  await expect(chamarFluxo(ROTA, OPC, aoEvento)).rejects.toBeInstanceOf(RequisicaoSuperada);
  expect(aoEvento).not.toHaveBeenCalled();
});
it.each([400, 403, 429])("%i antes do stream vira ErroDeApi com o status", async (status) => {
  fetchFalso.mockImplementation(async () => resposta(status, { detail: "x" }));
  const aoEvento = jest.fn();
  await expect(chamarFluxo(ROTA, OPC, aoEvento)).rejects.toMatchObject({ status, name: "ErroDeApi" });
  await expect(chamarFluxo(ROTA, OPC, aoEvento)).rejects.toBeInstanceOf(ErroDeApi); expect(aoEvento).not.toHaveBeenCalled();
});
