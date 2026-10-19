/**
 * O corte do `sinal` (prazo do `comLimite` ou abort de quem chamou) sai como `AbortError` na raiz (`api/client.ts`: `executar` no corpo, `enviar` antes dos headers),
 * e a máquina do "Atualizar agora" (#1076 PR 2) trata o prazo como transitório. 4xx e 5xx mantêm o status.
 * Os `fetch` daqui simulam a FORMA do expo/fetch lida no código-fonte (Expo 57.0.30): o corte do corpo rejeita com `AbortError`; o corte antes dos headers, com um `FetchError` de `name: "Error"`.
 */
import { ContratoInvalido, ErroDeApi, RequisicaoSuperada, SessaoExpirada } from "@/api/client";
import { tocar, verificar, type EstadoEntrar, type EstadoMfa } from "@/features/auth/entrar";
import { transitoria } from "@/features/openFinance/volta";
import { acompanhar, atualizar, OCIOSO, type Deps, type EstadoAtualizar } from "@/features/painel/atualizarBanco";
import { atualizarBancos, conexoes } from "@/services/openFinance";
import { guardarCredenciais } from "@/storage/secure";
import { gravador, prepararCaso, resposta, rotear, S } from "./auth_apoio";

const ABORTO = "The operation was aborted.";
const aoAbortar = (o: RequestInit, erro: () => unknown) => new Promise<never>((_, rej) => o.signal?.addEventListener("abort", () => rej(erro())));
// 2xx (ou o status dado) com headers e CORPO que nunca chega: o json() só rejeita no abort do sinal (o expo/fetch real: FetchResponse.abort erra o body stream).
const travado = (o: RequestInit, status = 200) => ({ ok: status < 300, status, headers: { get: () => null }, json: () => aoAbortar(o, () => new DOMException(ABORTO, "AbortError")) }) as unknown as Response;
// A forma do `FetchError` do expo/fetch: um Error comum, `name: "Error"`.
const fetchError = () => new Error("fetch failed: Fetch request has been canceled");

const UP = { id: 11, status: "ACTIVE", status_reason: null, reconnected_at: null, institution_name: "Nubank", last_sync_at: "2026-10-10T12:00:00Z", provider_item_id: "it", ui: { state: "updating", label: "x", detail: null } };
const OKC = { ...UP, last_sync_at: "2026-10-10T13:00:00Z", ui: { state: "updated", label: "ok", detail: null } };

let leituras = 0;
beforeEach(async () => { prepararCaso(); await guardarCredenciais(S); leituras = 0; jest.useFakeTimers(); });
afterEach(() => jest.useRealTimers());

function mundo(vivo = () => true) {
  const ctrl = new AbortController();
  const w = { estados: [] as EstadoAtualizar[], sucessos: 0, avisos: 0, falas: [] as string[] };
  const d: Deps = {
    agora: Date.now, esperar: (ms) => new Promise((r) => setTimeout(r, ms)), atual: vivo,
    enviar: () => atualizarBancos(7, ctrl), ler: async () => (await conexoes(7, ctrl)).connections,
    aoMudar: (e) => w.estados.push(e), recarregar: () => {}, falhou: () => {}, transitoria,
    silencioso: (e) => e instanceof SessaoExpirada || e instanceof RequisicaoSuperada,
    sucesso: () => { w.sucessos++; }, aviso: () => { w.avisos++; }, anunciar: (t) => w.falas.push(t),
  };
  return { ctrl, w, d, ultimo: () => w.estados[w.estados.length - 1] };
}

describe("o serviço: o corte do sinal", () => {
  it("C1: 200 com corpo travado aos 15 s sai como AbortError, e é transitória (não é contrato quebrado)", async () => {
    rotear({ "/open-finance/7": (o: RequestInit) => travado(o) });
    const p = conexoes(7).catch((e) => e);
    await jest.advanceTimersByTimeAsync(16_000);
    const e = await p;
    expect(e).not.toBeInstanceOf(ContratoInvalido);
    expect(e.name).toBe("AbortError");
    expect(transitoria(e)).toBe(true);
  });
  it("C2 (positivo do 3a): 200 com corpo que NÃO é JSON e o sinal VIVO continua ContratoInvalido", async () => {
    rotear({ "/open-finance/7": () => ({ ok: true, status: 200, headers: { get: () => null }, json: () => Promise.reject(new SyntaxError("Unexpected end of JSON input")) }) as unknown as Response });
    const e = await conexoes(7).catch((x) => x);
    expect(e).toBeInstanceOf(ContratoInvalido);
    expect(transitoria(e)).toBe(false);
  });
  it("C3: 403 com corpo travado até o prazo continua ErroDeApi com o status (o corte só vale para o 2xx)", async () => {
    rotear({ "/open-finance/7": (o: RequestInit) => travado(o, 403) });
    const p = conexoes(7).catch((e) => e);
    await jest.advanceTimersByTimeAsync(16_000);
    const e = await p;
    expect(e).toBeInstanceOf(ErroDeApi);
    expect(e.status).toBe(403);
  });
  it("C5 (positivo): rede fora com o sinal VIVO segue sendo o erro de rede, não AbortError", async () => {
    rotear({ "/open-finance/7": () => Promise.reject(new TypeError("Network request failed")) });
    const e = await conexoes(7).catch((x) => x);
    expect(e).toBeInstanceOf(TypeError);
    expect(e.name).not.toBe("AbortError");
  });
});

describe("a máquina do Atualizar agora", () => {
  it("C4: 1ª leitura com corpo travado (15 s) e a 2ª em dia: segue a cadência e conclui em sucesso", async () => {
    rotear({ "/open-finance/7": (o: RequestInit) => (leituras++ === 0 ? travado(o) : resposta(200, { ok: true, connections: [OKC] })) });
    const { w, d, ultimo } = mundo();
    const p = acompanhar(d, new Map([["it", UP.last_sync_at]]));
    await jest.advanceTimersByTimeAsync(40_000);
    await p;
    expect(ultimo()).toEqual(OCIOSO); expect(w.sucessos).toBe(1); expect(w.avisos).toBe(0); expect(leituras).toBe(2);
  });
  it("C4: POST com corpo travado aos 60 s: lê o snapshot UMA vez e conclui (não vira 'ocioso + falha')", async () => {
    rotear({ "/open-finance/7/refresh": (o: RequestInit) => travado(o), "/open-finance/7": () => { leituras++; return resposta(200, { ok: true, connections: [OKC] }); } });
    const { w, d, ultimo } = mundo();
    const p = atualizar(d);
    await jest.advanceTimersByTimeAsync(61_000);
    await p;
    expect(leituras).toBe(1); expect(ultimo()).toEqual(OCIOSO); expect(w.sucessos).toBe(1); expect(w.avisos).toBe(0);
  });
  it("C4 (positivo): abort EXTERNO no meio do corpo (troca de conta, X, rodada nova): para calado, sem aviso nem fala de falha", async () => {
    rotear({ "/open-finance/7": (o: RequestInit) => travado(o) });
    let vivo = true;
    const { ctrl, w, d, ultimo } = mundo(() => vivo);
    const p = acompanhar(d, new Map([["it", UP.last_sync_at]]));
    await jest.advanceTimersByTimeAsync(6_000); // a 1ª espera (5 s) passou e a leitura está presa no corpo
    vivo = false; ctrl.abort();
    await jest.advanceTimersByTimeAsync(1);
    await p;
    expect(ultimo()).toEqual(OCIOSO); expect(w.avisos).toBe(0); expect(w.sucessos).toBe(0); expect(w.falas.filter((t) => /Não conseguimos/.test(t))).toEqual([]);
  });
  it("C5: POST pendurado SEM headers aos 60 s (o FetchError do expo): lê o snapshot UMA vez e conclui", async () => {
    rotear({ "/open-finance/7/refresh": (o: RequestInit) => aoAbortar(o, fetchError) as unknown as Response, "/open-finance/7": () => { leituras++; return resposta(200, { ok: true, connections: [OKC] }); } });
    const { w, d, ultimo } = mundo();
    const p = atualizar(d);
    await jest.advanceTimersByTimeAsync(61_000);
    await p;
    expect(leituras).toBe(1); expect(ultimo()).toEqual(OCIOSO); expect(w.sucessos).toBe(1); expect(w.avisos).toBe(0);
  });
});

describe("o MFA (entrar.ts): tempo limite antes dos headers", () => {
  const M: EstadoMfa = { fase: "mfa", desafio: "d-1", email: "m@x.com", modo: "totp" };
  it("C5b: o FetchError depois dos 15 s volta ao FORMULÁRIO ('Entre de novo'), não fica em M", async () => {
    rotear({ "/auth/mfa/verify-login": (o: RequestInit) => aoAbortar(o, fetchError) as unknown as Response });
    const { aplicados, aplicar } = gravador<EstadoEntrar>();
    const p = tocar(() => verificar(M.desafio, M.email, M.modo, "000000", jest.fn()), aplicar);
    await jest.advanceTimersByTimeAsync(16_000);
    await p;
    expect(aplicados).toEqual([{ fase: "formulario", aviso: "Algo deu errado. Tente de novo. Entre de novo." }]);
  });
  it("C5b (positivo): rede fora com o sinal VIVO continua em M (o mesmo caso de entrar_mfa.test.ts)", async () => {
    rotear({ "/auth/mfa/verify-login": () => Promise.reject(new TypeError("Network request failed")) });
    const { aplicados, aplicar } = gravador<EstadoEntrar>();
    await tocar(() => verificar(M.desafio, M.email, M.modo, "000000", jest.fn()), aplicar);
    expect(aplicados).toEqual([{ ...M, aviso: "Algo deu errado. Tente de novo." }]);
  });
});
