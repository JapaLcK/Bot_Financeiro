import { act, renderHook } from "@testing-library/react-native";
import { ErroDeApi, RequisicaoSuperada } from "@/api/client";
import { useLancamentos } from "@/features/painel/extrato";
import fixture from "./painel.fixture.json";
// `useLancamentos` (paginação de /api/app/lancamentos, partilhada por Extrato e cards do chat) isolado: o provider e a rede são falsos, então cada guarda do hook é medida
// SOZINHA. O `atual()` do falso é sempre true de propósito: no app real o `operacao().atual` repete parte do que `lote` já garante, e aqui o que se mede é o `lote`.
const mockP = { usuario: { user_id: 1 }, versao: 0, oculto: false, dados: {}, falhou: jest.fn(), operacao: () => { const controlador = new AbortController(); return { controlador, atual: () => true }; } };
const mockLer = jest.fn();
jest.mock("@/features/painel/provider", () => ({ ...jest.requireActual("@/features/painel/provider"), usePainel: () => mockP }));
jest.mock("@/services/painel", () => ({ ...jest.requireActual("@/services/painel"), lerRecurso: (...a: unknown[]) => mockLer(...a) }));

const BASE = (fixture as Record<string, any>)["/api/app/lancamentos"];
const item = (id: string) => ({ ...BASE.itens[0], id });
const pag = (ids: string[], proximo: string | null = null, motivos: string[] = []) => ({ ...BASE, itens: ids.map(item), proximo, motivos });
/** Uma resposta que o teste solta (ou rejeita) quando quiser. */
function presa() { let ok!: (v: unknown) => void, no!: (e: unknown) => void; const promessa = new Promise((a, b) => { ok = a; no = b; }); return { promessa, ok, no }; }
const ids = (r: { current: { itens: { id: string }[] } }) => r.current.itens.map((i) => i.id);
const urls = () => mockLer.mock.calls.map((c) => String(c[0]));
const montar = (consulta = "mes=2026-10&limite=50") => renderHook((p: { consulta: string }) => useLancamentos(p.consulta), { initialProps: { consulta } });
const solta = async (p: ReturnType<typeof presa>, v: unknown) => { await act(async () => { p.ok(v); await p.promessa; }); };
beforeEach(() => { mockLer.mockReset(); mockP.usuario = { user_id: 1 }; mockP.versao = 0; mockP.falhou.mockReset(); });

it("1ª página: lista, `proximo` e `motivos` do servidor (motivos atualizam a cada página)", async () => {
  mockLer.mockResolvedValueOnce(pag(["a"], "c2", ["outra_moeda"])).mockResolvedValueOnce(pag(["b"], null, ["conexao_pausada"]));
  const { result } = montar(); await act(async () => {});
  expect(ids(result)).toEqual(["a"]); expect(result.current.proximo).toBe("c2"); expect(result.current.motivos).toEqual(["outra_moeda"]); expect(result.current.carregando).toBe(false);
  expect(urls()[0]).toBe("/api/app/lancamentos?mes=2026-10&limite=50");
  await act(async () => { result.current.carregarMais(); });
  expect(ids(result)).toEqual(["a", "b"]); expect(result.current.motivos).toEqual(["conexao_pausada"]); expect(urls()[1]).toBe("/api/app/lancamentos?mes=2026-10&limite=50&cursor=c2");
});
it("ids repetidos entre páginas não duplicam", async () => {
  mockLer.mockResolvedValueOnce(pag(["a", "b"], "c2")).mockResolvedValueOnce(pag(["b", "c"]));
  const { result } = montar(); await act(async () => {}); await act(async () => { result.current.carregarMais(); });
  expect(ids(result)).toEqual(["a", "b", "c"]);
});
it("`carregarMais` duplo no mesmo instante faz UMA requisição só (uma página por vez)", async () => {
  const p2 = presa(); mockLer.mockResolvedValueOnce(pag(["a"], "c2")).mockReturnValueOnce(p2.promessa);
  const { result } = montar(); await act(async () => {});
  await act(async () => { result.current.carregarMais(); result.current.carregarMais(); });
  expect(mockLer).toHaveBeenCalledTimes(2); await solta(p2, pag(["b"])); expect(ids(result)).toEqual(["a", "b"]);
});
it("trocar a consulta zera a lista na hora e pede a nova do começo (sem cursor)", async () => {
  const b1 = presa(); mockLer.mockResolvedValueOnce(pag(["a"], "c2")).mockReturnValueOnce(b1.promessa);
  const { result, rerender } = montar("mes=2026-10"); await act(async () => {});
  rerender({ consulta: "mes=2026-09" }); expect(ids(result)).toEqual([]); expect(result.current.proximo).toBeNull(); expect(result.current.carregando).toBe(true);
  expect(urls()[1]).toBe("/api/app/lancamentos?mes=2026-09"); await solta(b1, pag(["x"])); expect(ids(result)).toEqual(["x"]);
});
describe("resposta de consulta VELHA (a consulta mudou com a página em voo)", () => {
  async function comPaginaVelhaEmVoo() {
    const a = presa(), b = presa(); mockLer.mockReturnValueOnce(a.promessa).mockReturnValueOnce(b.promessa);
    const h = montar("mes=2026-10"); await act(async () => {}); h.rerender({ consulta: "mes=2026-09" });
    return { ...h, a, b };
  }
  it("sucesso velho não entra na lista nova", async () => {
    const { result, a, b } = await comPaginaVelhaEmVoo(); await solta(a, pag(["velho"], "cv"));
    expect(ids(result)).toEqual([]); expect(result.current.proximo).toBeNull(); // a nova ainda está em voo
    await solta(b, pag(["novo"])); expect(ids(result)).toEqual(["novo"]); expect(result.current.proximo).toBeNull();
  });
  it("sucesso velho que chega DEPOIS da nova não a sobrescreve", async () => {
    const { result, a, b } = await comPaginaVelhaEmVoo(); await solta(b, pag(["novo"])); await solta(a, pag(["velho"], "cv"));
    expect(ids(result)).toEqual(["novo"]); expect(result.current.proximo).toBeNull();
  });
  it("erro velho não vira erro (nem 402) da consulta nova", async () => {
    const { result, a, b } = await comPaginaVelhaEmVoo(); await act(async () => { a.no(new ErroDeApi(402, "plano")); await a.promessa.catch(() => {}); });
    expect(result.current.erro).toBeNull(); expect(mockP.falhou).not.toHaveBeenCalled(); await solta(b, pag(["novo"])); expect(ids(result)).toEqual(["novo"]);
  });
  it("o fim da leitura velha não destrava a nova: segue 'carregando' até a nova chegar", async () => {
    const { result, a, b } = await comPaginaVelhaEmVoo(); await solta(a, pag(["velho"]));
    expect(result.current.carregando).toBe(true); await solta(b, pag(["novo"])); expect(result.current.carregando).toBe(false);
  });
  it("desmontar com a página em voo: o erro que chega depois não derruba o portão (sem `p.falhou`)", async () => {
    const a = presa(); mockLer.mockReturnValueOnce(a.promessa); const { unmount } = montar(); await act(async () => {}); unmount();
    await act(async () => { a.no(new ErroDeApi(402, "plano")); await a.promessa.catch(() => {}); }); expect(mockP.falhou).not.toHaveBeenCalled();
  });
});
it("o cleanup aborta a leitura em voo (troca de consulta e desmontagem)", async () => {
  mockLer.mockReturnValue(new Promise(() => {})); const { rerender, unmount } = montar("mes=2026-10"); await act(async () => {});
  const sinal = (n: number) => (mockLer.mock.calls[n]![2] as AbortController).signal;
  expect(sinal(0).aborted).toBe(false); rerender({ consulta: "mes=2026-09" }); expect(sinal(0).aborted).toBe(true); expect(sinal(1).aborted).toBe(false); unmount(); expect(sinal(1).aborted).toBe(true);
});
describe("erros", () => {
  it("402 chama `p.falhou` e o erro aparece; 'Tentar de novo' (carregarMais sem cursor) recarrega do início", async () => {
    const e = new ErroDeApi(402, "plano"); mockLer.mockRejectedValueOnce(e).mockResolvedValueOnce(pag(["a"]));
    const { result } = montar(); await act(async () => {});
    expect(mockP.falhou).toHaveBeenCalledWith(e); expect(result.current.erro).toBe("Não conseguimos carregar agora. Tente novamente."); expect(result.current.carregando).toBe(false);
    await act(async () => { result.current.carregarMais(); }); expect(result.current.erro).toBeNull(); expect(ids(result)).toEqual(["a"]); expect(urls()[1]).toBe(urls()[0]);
  });
  it("`RequisicaoSuperada` (troca de conta) é silenciosa: nem erro nem `p.falhou`", async () => {
    mockLer.mockRejectedValueOnce(new RequisicaoSuperada()); const { result } = montar(); await act(async () => {});
    expect(result.current.erro).toBeNull(); expect(mockP.falhou).not.toHaveBeenCalled();
  });
});
describe("o que refaz a leitura", () => {
  it.each([
    ["troca de usuário", (r: (p: { consulta: string }) => void) => { mockP.usuario = { user_id: 2 }; r({ consulta: "mes=2026-10" }); }],
    ["carga completa do provider (`versao`)", (r: (p: { consulta: string }) => void) => { mockP.versao = 1; r({ consulta: "mes=2026-10" }); }],
    ["`dia` na consulta (Extrato do dia)", (r: (p: { consulta: string }) => void) => r({ consulta: "mes=2026-10&dia=2026-10-03" })],
  ])("%s: lista zerada e nova requisição", async (_nome, mudar) => {
    mockLer.mockResolvedValue(pag(["a"])); const { result, rerender } = montar("mes=2026-10"); await act(async () => {});
    expect(mockLer).toHaveBeenCalledTimes(1); await act(async () => { mudar(rerender as never); }); expect(mockLer).toHaveBeenCalledTimes(2); expect(ids(result)).toEqual(["a"]);
  });
  it("controle positivo: rerender sem mudança nenhuma não refaz", async () => {
    mockLer.mockResolvedValue(pag(["a"])); const { rerender } = montar("mes=2026-10"); await act(async () => {}); rerender({ consulta: "mes=2026-10" }); await act(async () => {});
    expect(mockLer).toHaveBeenCalledTimes(1);
  });
});

