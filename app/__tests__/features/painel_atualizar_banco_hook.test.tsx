import { act, renderHook, waitFor } from "@testing-library/react-native";
import { ErroDeApi, SessaoExpirada } from "@/api/client";
import { __resetAtualizarBanco, useAtualizarBanco } from "@/features/painel/usarAtualizarBanco";
import { usePainel } from "@/features/painel/provider";
import { atualizarBancos, conexoes } from "@/services/openFinance";

// O isolamento por usuário do estado de módulo, SEM a rota: uma conta nova com a MESMA versão do painel (o provider NÃO remonta na troca de conta: `dono` muda e `versao` só sobe, então na prática a versão também difere; aqui ela é igualada de propósito)
// não pode ver nem herdar a rodada da outra. Pela rota a versão sempre muda e esconde o `uid`; aqui só o `uid` separa as duas.
jest.mock("@/features/painel/provider", () => ({ usePainel: jest.fn() }));
jest.mock("@/services/openFinance", () => ({ atualizarBancos: jest.fn(), conexoes: jest.fn(), limiteBancario: jest.fn() }));
let ops: { v: boolean }[], versaoAtual: number;
const painel = (uid: number) => ({ usuario: { user_id: uid }, versao: versaoAtual, operacao: () => { const o = { v: true }; ops.push(o); return { controlador: new AbortController(), atual: () => o.v }; }, atualizar: jest.fn(), falhou: jest.fn() });
const cancelarTudo = () => ops.forEach((o) => { o.v = false; }); // o `cancelar()` do provider derruba toda operação existente
const conexao = { id: 1, status: "ACTIVE", status_reason: null, last_sync_at: null, reconnected_at: null, provider_item_id: "it_1", institution_name: "Nubank", ui: { state: "partial", label: "partial", detail: null } };
let uid: number;
beforeEach(() => {
  __resetAtualizarBanco(); uid = 1; ops = []; versaoAtual = 1;
  jest.mocked(usePainel).mockImplementation(() => painel(uid) as never);
  jest.mocked(conexoes).mockResolvedValue({ connections: [conexao] } as never);
  jest.mocked(atualizarBancos).mockReturnValue(new Promise(() => {})); // o POST fica no ar
});
it("a rodada da conta 1 em voo não aparece para a conta 2 (mesma versão) e não trava o toque dela", async () => {
  const { result, rerender } = renderHook(() => useAtualizarBanco());
  await waitFor(() => expect(result.current.acao?.tipo).toBe("atualizar"));
  act(() => result.current.atualizar()); expect(result.current.maquina.fase).toBe("enviando"); expect(atualizarBancos).toHaveBeenCalledWith(1, expect.anything());
  uid = 2; rerender({});
  expect(result.current.maquina.fase).toBe("ocioso"); // nada da conta 1
  await waitFor(() => expect(conexoes).toHaveBeenCalledWith(2, expect.anything())); await waitFor(() => expect(result.current.acao?.tipo).toBe("atualizar"));
  act(() => result.current.atualizar()); expect(atualizarBancos).toHaveBeenLastCalledWith(2, expect.anything()); expect(atualizarBancos).toHaveBeenCalledTimes(2); // a trava é por conta
  expect(result.current.maquina.fase).toBe("enviando");
});

// ---- leitura adiada (a0 com uma rodada em voo): parte UMA vez quando a rodada acaba, e só se a leitura ainda vale ----
const lista = (state: string) => ({ connections: [{ ...conexao, ui: { state, label: state, detail: null } }] });
const adiada = async () => {
  jest.useFakeTimers();
  jest.mocked(conexoes).mockResolvedValue(lista("partial") as never);
  const r = renderHook(() => useAtualizarBanco());
  await waitFor(() => expect(r.result.current.acao?.tipo).toBe("atualizar"));
  let falha: (e: unknown) => void = () => {}, resolve: (v: unknown) => void = () => {}; jest.mocked(atualizarBancos).mockReturnValue(new Promise((ok, no) => { falha = no; resolve = ok; }) as never);
  act(() => r.result.current.atualizar());
  jest.mocked(conexoes).mockResolvedValue(lista("updating") as never); versaoAtual = 2; r.rerender({}); // bump SEM cancelar: a leitura nova vê o item em coleta, com a rodada ainda em voo
  await waitFor(() => expect(r.result.current.acao?.tipo).toBe("acompanhar")); expect(r.result.current.maquina.fase).toBe("enviando"); // partida adiada: nada de "atualizando sozinho"
  const assentar = () => act(async () => { for (let k = 0; k < 6; k++) await Promise.resolve(); });
  return { ...r, falha: async (e: unknown) => { falha(e); await assentar(); }, responde: async (v: unknown) => { resolve(v); await assentar(); } };
};
const lidasDepois = async (ms: number) => { const n = jest.mocked(conexoes).mock.calls.length; await act(async () => { jest.advanceTimersByTime(ms); for (let k = 0; k < 6; k++) await Promise.resolve(); }); return jest.mocked(conexoes).mock.calls.length - n; };
afterEach(() => jest.useRealTimers());
it("o POST falha: a leitura adiada parte sozinha (a máquina vira 'acompanhando' e lê na cadência)", async () => {
  const h = await adiada(); await h.falha(new ErroDeApi(500, "x"));
  expect(h.result.current.maquina.fase).toBe("acompanhando"); expect(await lidasDepois(5_000)).toBe(1);
});
it("...mas se o POST acaba COM resultado (problema), `reavaliar` não parte por cima: o texto do servidor fica e ninguém lê", async () => {
  const h = await adiada(); await h.responde({ connections: [], sync: { items: [{ item_id: "it_1", institution: "Nubank", state: "partial", label: "p", detail: "Parte dos dados ainda não veio", still_updating: false }] } });
  expect(h.result.current.maquina).toEqual({ fase: "resultado", texto: "Nubank: Parte dos dados ainda não veio." }); expect(await lidasDepois(120_000)).toBe(0);
});
it("a rodada de partida adiada acaba em silêncio (sessão) e NÃO parte de novo: uma vez só", async () => {
  const h = await adiada(); jest.mocked(conexoes).mockRejectedValue(new SessaoExpirada() as never); await h.falha(new ErroDeApi(500, "x"));
  expect(h.result.current.maquina.fase).toBe("acompanhando"); await lidasDepois(5_000); // a leitura da rodada cai em 401: para em silêncio
  expect(await lidasDepois(120_000)).toBe(0);
});
it("recarga que cancela antes de a rodada acabar: a leitura adiada perdeu a validade e nada parte", async () => {
  const h = await adiada(); cancelarTudo(); await h.falha(new ErroDeApi(500, "x"));
  expect(await lidasDepois(120_000)).toBe(0); expect(h.result.current.maquina.fase).not.toBe("acompanhando");
});

it("MGR2 (caminho da leitura, não do `reavaliar`): com o resultado visível, a leitura (a0) da recarga não parte nem fica pendente", async () => {
  jest.useFakeTimers(); jest.mocked(conexoes).mockResolvedValue(lista("partial") as never);
  const r = renderHook(() => useAtualizarBanco()); await waitFor(() => expect(r.result.current.acao?.tipo).toBe("atualizar"));
  jest.mocked(atualizarBancos).mockResolvedValue({ connections: [], sync: { items: [{ item_id: "it_1", institution: "Nubank", state: "partial", label: "p", detail: "Parte dos dados ainda não veio", still_updating: false }] } } as never);
  act(() => r.result.current.atualizar()); await waitFor(() => expect(r.result.current.maquina.fase).toBe("resultado"));
  jest.mocked(conexoes).mockResolvedValue(lista("updating") as never); versaoAtual = 2; r.rerender({}); // a recarga do `concluir` traz a leitura nova, agora em coleta
  await waitFor(() => expect(r.result.current.acao?.tipo).toBe("acompanhar"));
  expect(r.result.current.maquina.fase).toBe("resultado"); expect(await lidasDepois(120_000)).toBe(0);
  expect(r.result.current.maquina).toEqual({ fase: "resultado", texto: "Nubank: Parte dos dados ainda não veio." });
});

it("o fim da rodada da conta 1 não parte a leitura pendente da conta 2 (nem a dela, nem a de ninguém)", async () => {
  jest.useFakeTimers(); jest.mocked(conexoes).mockResolvedValue(lista("partial") as never);
  const fins: Record<number, (e: unknown) => void> = {};
  jest.mocked(atualizarBancos).mockImplementation(((u: number) => new Promise((_, no) => { fins[u] = no; })) as never);
  const r = renderHook(() => useAtualizarBanco()); await waitFor(() => expect(r.result.current.acao?.tipo).toBe("atualizar"));
  act(() => r.result.current.atualizar()); // rodada da conta 1 no ar
  uid = 2; r.rerender({}); await waitFor(() => expect(r.result.current.acao?.tipo).toBe("atualizar"));
  act(() => r.result.current.atualizar()); expect(r.result.current.maquina.fase).toBe("enviando"); // rodada da conta 2 no ar
  jest.mocked(conexoes).mockResolvedValue(lista("updating") as never); versaoAtual = 2; r.rerender({}); // a leitura (a0) da conta 2 fica PENDENTE (há rodada dela em voo)
  await waitFor(() => expect(r.result.current.acao?.tipo).toBe("acompanhar")); expect(r.result.current.maquina.fase).toBe("enviando");
  const n = jest.mocked(conexoes).mock.calls.length;
  await act(async () => { fins[1]!(new ErroDeApi(500, "x")); for (let k = 0; k < 6; k++) await Promise.resolve(); }); // acaba a rodada da conta 1
  expect(await lidasDepois(120_000)).toBe(0); expect(jest.mocked(conexoes).mock.calls.length).toBe(n);
  expect(r.result.current.maquina.fase).toBe("enviando"); // a rodada da conta 2 segue sua, intocada
});
