import { act, renderHook } from "@testing-library/react-native";
import { ErroDeApi, RequisicaoSuperada } from "@/api/client";
import { gastosSchema } from "@/api/schemas/painel";
import { useGastosMes } from "@/features/painel/gastosMes";
import fixture from "../../../tests/fixtures/app_gastos.json";
// `useGastosMes` (leitura sob demanda de /api/app/gastos) isolado: provider e rede falsos, para medir cada guarda do hook sozinha (molde de painel_lancamentos_hook).
const mockP = { usuario: { user_id: 1 }, versao: 0, falhou: jest.fn(), operacao: () => { const controlador = new AbortController(); return { controlador, atual: () => !controlador.signal.aborted }; } };
const mockLer = jest.fn();
jest.mock("@/features/painel/provider", () => ({ ...jest.requireActual("@/features/painel/provider"), usePainel: () => mockP }));
jest.mock("@/services/painel", () => ({ ...jest.requireActual("@/services/painel"), lerRecurso: (...a: unknown[]) => mockLer(...a) }));

const gastos = (hoje: string) => gastosSchema.parse({ ...fixture, hoje });
function presa() { let ok!: (v: unknown) => void, no!: (e: unknown) => void; const promessa = new Promise((a, b) => { ok = a; no = b; }); return { promessa, ok, no }; }
const solta = async (p: ReturnType<typeof presa>, v: unknown) => { await act(async () => { p.ok(v); await p.promessa; }); };
const urls = () => mockLer.mock.calls.map((c) => String(c[0]));
const montar = (mes = "2026-10") => renderHook((p: { mes: string }) => useGastosMes(p.mes), { initialProps: { mes } });
beforeEach(() => { mockLer.mockReset(); mockP.usuario = { user_id: 1 }; mockP.versao = 0; mockP.falhou.mockReset(); });

it("lê /api/app/gastos?mes= com o schema do app: carregando e depois pronto com o dado", async () => {
  const a = presa(); mockLer.mockReturnValueOnce(a.promessa);
  const { result } = montar(); await act(async () => {});
  expect(result.current.fase).toBe("carregando"); expect(urls()).toEqual(["/api/app/gastos?mes=2026-10"]); expect(mockLer.mock.calls[0]![1]).toBe(gastosSchema);
  await solta(a, gastos("2026-10-14")); expect(result.current).toMatchObject({ fase: "pronto", dado: { hoje: "2026-10-14" } });
});
describe("cache por [usuário, versao, mês] nesta instância", () => {
  async function lidoEmDoisMeses() {
    mockLer.mockImplementation(async (url: string) => gastos(url.endsWith("2026-10") ? "2026-10-14" : "2026-09-30"));
    const h = montar("2026-10"); await act(async () => {}); h.rerender({ mes: "2026-09" }); await act(async () => {});
    return h;
  }
  it("voltar a um mês já lido não repete a requisição", async () => {
    const { result, rerender } = await lidoEmDoisMeses(); expect(mockLer).toHaveBeenCalledTimes(2);
    rerender({ mes: "2026-10" }); await act(async () => {});
    expect(mockLer).toHaveBeenCalledTimes(2); expect(result.current).toMatchObject({ fase: "pronto", dado: { hoje: "2026-10-14" } });
  });
  it.each([
    ["`versao` do provider (cada foco do painel)", () => { mockP.versao = 1; }],
    ["troca de usuário", () => { mockP.usuario = { user_id: 2 }; }],
  ])("%s lê de novo", async (_nome, mudar) => {
    const { rerender } = await lidoEmDoisMeses(); mudar(); rerender({ mes: "2026-10" }); await act(async () => {});
    expect(mockLer).toHaveBeenCalledTimes(3);
  });
});
it("trocar de mês nunca mostra o dado do mês anterior, nem num quadro de render (o quadro com o dado velho é o defeito)", async () => {
  mockLer.mockImplementation(async (url: string) => gastos(url.endsWith("2026-10") ? "2026-10-14" : "2026-09-30"));
  const quadros: [string, string, string | null][] = [];
  const { result, rerender } = renderHook((p: { mes: string }) => { const r = useGastosMes(p.mes); quadros.push([p.mes, r.fase, r.fase === "pronto" ? r.dado.hoje : null]); return r; }, { initialProps: { mes: "2026-10" } });
  await act(async () => {}); expect(result.current).toMatchObject({ fase: "pronto", dado: { hoje: "2026-10-14" } });
  rerender({ mes: "2026-09" }); await act(async () => {});
  expect(quadros.filter(([mes, , hoje]) => mes === "2026-09" && hoje === "2026-10-14")).toEqual([]); expect(result.current).toMatchObject({ fase: "pronto", dado: { hoje: "2026-09-30" } });
});
it("resposta do mês anterior que chega tarde (mês trocado com a leitura em voo) não entra", async () => {
  const a = presa(), b = presa(); mockLer.mockReturnValueOnce(a.promessa).mockReturnValueOnce(b.promessa);
  const { result, rerender } = montar("2026-10"); await act(async () => {}); rerender({ mes: "2026-09" });
  await solta(a, gastos("2026-10-14")); expect(result.current.fase).toBe("carregando");
  await solta(b, gastos("2026-09-30")); expect(result.current).toMatchObject({ fase: "pronto", dado: { hoje: "2026-09-30" } });
});
it("resposta tardia de OUTRO mês não trava o mês atual (já em cache) em 'carregando'", async () => {
  const set = presa(), out = presa(); mockLer.mockReturnValueOnce(set.promessa).mockReturnValueOnce(out.promessa);
  const { result, rerender } = montar("2026-10"); await act(async () => {}); await solta(set, gastos("2026-10-14")); // 2026-10 lido e em cache
  rerender({ mes: "2026-09" }); await act(async () => {}); rerender({ mes: "2026-10" }); await act(async () => {}); // 2026-09 em voo; volta ao cache
  expect(result.current.fase).toBe("pronto"); await solta(out, gastos("2026-09-30"));
  expect(result.current).toMatchObject({ fase: "pronto", dado: { hoje: "2026-10-14" } }); expect(mockLer).toHaveBeenCalledTimes(2);
});
it("o cleanup aborta a leitura em voo (troca de mês e desmontagem)", async () => {
  mockLer.mockReturnValue(new Promise(() => {})); const { rerender, unmount } = montar("2026-10"); await act(async () => {});
  const sinal = (n: number) => (mockLer.mock.calls[n]![2] as AbortController).signal;
  expect(sinal(0).aborted).toBe(false); rerender({ mes: "2026-09" }); expect(sinal(0).aborted).toBe(true); expect(sinal(1).aborted).toBe(false); unmount(); expect(sinal(1).aborted).toBe(true);
});
describe("erros", () => {
  it("falha passa por `p.falhou` (portão: sessão, 402, 403 do onboarding) e vira 'erro'; `recarregar` repete SÓ esta leitura", async () => {
    const e = new ErroDeApi(500, "x"); mockLer.mockRejectedValueOnce(e).mockResolvedValueOnce(gastos("2026-10-14"));
    const { result } = montar(); await act(async () => {});
    expect(mockP.falhou).toHaveBeenCalledWith(e); expect(result.current).toMatchObject({ fase: "erro", mensagem: "Não conseguimos carregar agora. Tente novamente." });
    await act(async () => { result.current.recarregar(); }); expect(result.current.fase).toBe("pronto"); expect(urls()).toEqual(["/api/app/gastos?mes=2026-10", "/api/app/gastos?mes=2026-10"]);
  });
  it("403 de outro motivo é 'negado' do recurso (e também passa por `p.falhou`, que decide o portão)", async () => {
    const e = new ErroDeApi(403, "x", { error: { code: "plan_limit" } }); mockLer.mockRejectedValueOnce(e);
    const { result } = montar(); await act(async () => {});
    expect(result.current).toMatchObject({ fase: "negado", mensagem: "Este recurso não está disponível no seu acesso atual." }); expect(mockP.falhou).toHaveBeenCalledWith(e);
  });
  it("`RequisicaoSuperada` é silenciosa: fica 'carregando', sem `p.falhou`", async () => {
    mockLer.mockRejectedValueOnce(new RequisicaoSuperada()); const { result } = montar(); await act(async () => {});
    expect(result.current.fase).toBe("carregando"); expect(mockP.falhou).not.toHaveBeenCalled();
  });
  it("erro que chega depois de desmontar não derruba o portão", async () => {
    const a = presa(); mockLer.mockReturnValueOnce(a.promessa); const { unmount } = montar(); await act(async () => {}); unmount();
    await act(async () => { a.no(new ErroDeApi(402, "plano")); await a.promessa.catch(() => {}); }); expect(mockP.falhou).not.toHaveBeenCalled();
  });
});
