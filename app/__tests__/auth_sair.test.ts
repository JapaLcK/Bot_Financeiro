import { SessaoExpirada, TEMPO_LIMITE_AUTH_MS } from "@/api/client";
import { perfil, sair } from "@/services/auth";
import { guardarCredenciais, lerCredenciais } from "@/storage/secure";

/**
 * O caso (ii) do plano de `sair()`: uma renovação REAL (via `/auth/refresh`,
 * não uma gravação incondicional de teste) que termina depois da limpeza do
 * logout. Separado de `auth.test.ts` porque aquele arquivo bateu no teto de
 * linhas do eslint.
 */
const cofre = (globalThis as unknown as { __cofreDeTeste: Map<string, string> })
  .__cofreDeTeste;
const fetchFalso = jest.fn();

/** Mesma sessão de A, com `jti` igual (só o payload do JWT importa aqui). */
const ACCESS_A = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiAiMSIsICJqdGkiOiAic2Vzc2FvLUEifQ.assinatura";
const ACCESS_A2 = ACCESS_A.replace("assinatura", "outra");

beforeEach(() => {
  fetchFalso.mockReset();
  globalThis.fetch = fetchFalso as unknown as typeof fetch;
  cofre.clear();
});

function resposta(status: number, corpo: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => corpo,
  } as Response;
}

it("renovação real que termina DEPOIS da limpeza não regrava a sessão", async () => {
  // A renovação de verdade usa `trocarSe`, que relê o cofre dentro da fila:
  // com o cofre já vazio, o refresh de origem não casa com nada guardado e a
  // troca não grava. Uma gravação incondicional de teste (auth.test.ts) não
  // prova este caminho — só o `/auth/refresh` de verdade o exercita.
  await guardarCredenciais({ access: ACCESS_A, refresh: "rt_A" });
  let soltarChegou: () => void = () => {};
  const chegouAoRefresh = new Promise<void>((r) => (soltarChegou = r));
  let soltarPortao: () => void = () => {};
  const portaoDoRefresh = new Promise<void>((r) => (soltarPortao = r));
  fetchFalso.mockImplementation(async (u: string, o: RequestInit) => {
    const url = String(u);
    if (url.includes("/auth/me")) {
      const auth = (o.headers as Record<string, string>)["Authorization"];
      // Só o access RENOVADO (A2) autentica. Se `/auth/me` aceitasse qualquer
      // token, a retentativa do `executar` limparia o cofre de qualquer jeito
      // e o caso não discriminaria a guarda de `trocarSe` (ver mutação abaixo).
      return auth === `Bearer ${ACCESS_A2}`
        ? resposta(200, { user_id: 1, email: "a@x.com", plan: "free" })
        : resposta(401, { detail: "expirado" });
    }
    if (url.includes("/auth/refresh")) {
      soltarChegou();
      await portaoDoRefresh;
      return resposta(200, {
        access_token: ACCESS_A2,
        refresh_token: "rt_A2",
        dashboard_token: "d",
        expires_in: 900,
      });
    }
    return resposta(200, {}); // /auth/logout
  });

  const chamadaDePerfil = perfil();
  await chegouAoRefresh;
  await sair();
  soltarPortao();

  await expect(chamadaDePerfil).rejects.toBeInstanceOf(SessaoExpirada);
  await expect(lerCredenciais()).resolves.toBeNull();
});

/**
 * #458: a revogação no servidor é ESPERADA, com o tempo limite de auth. O cofre
 * continua vazio antes de qualquer rede (motivo do #433) — o que muda é só
 * quando `sair()` resolve.
 */
describe("sair() espera a revogação no servidor (#458)", () => {
  afterEach(() => jest.useRealTimers());

  const drenar = async () => {
    for (let i = 0; i < 20; i++) await Promise.resolve();
  };

  it("só resolve depois da resposta do /auth/logout", async () => {
    await guardarCredenciais({ access: ACCESS_A, refresh: "rt_A" });
    let soltarChegou: () => void = () => {};
    const chegou = new Promise<void>((r) => (soltarChegou = r));
    let soltarPortao: () => void = () => {};
    const portao = new Promise<void>((r) => (soltarPortao = r));
    fetchFalso.mockImplementation(async () => {
      soltarChegou();
      await portao;
      return resposta(200, {});
    });

    let feito = false;
    const s = sair().then(() => (feito = true));
    await chegou;
    await drenar();
    expect(feito).toBe(false);
    await expect(lerCredenciais()).resolves.toBeNull();

    soltarPortao();
    await s;
    expect(feito).toBe(true);
  });

  it("logout pendurado: sair() resolve no tempo limite sem rejeitar, com o cofre já vazio", async () => {
    jest.useFakeTimers();
    await guardarCredenciais({ access: ACCESS_A, refresh: "rt_A" });
    let soltarChegou: () => void = () => {};
    const chegou = new Promise<void>((r) => (soltarChegou = r));
    // O dublê padrão ignora `signal`; este só rejeita quando o sinal aborta,
    // como o `fetch` de verdade.
    fetchFalso.mockImplementation(
      (_u: string, o: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          soltarChegou();
          o.signal?.addEventListener("abort", () => reject(new DOMException("Abortado", "AbortError")));
        }),
    );

    let feito = false;
    const s = sair().then(() => (feito = true));
    await chegou;
    await expect(lerCredenciais()).resolves.toBeNull();

    await jest.advanceTimersByTimeAsync(TEMPO_LIMITE_AUTH_MS - 1);
    expect(feito).toBe(false);
    await jest.advanceTimersByTimeAsync(1);
    expect(feito).toBe(true);
    await s;
  });

  it.each([
    ["401", () => Promise.resolve(resposta(401, { detail: "expirado" }))],
    ["500", () => Promise.resolve(resposta(500, {}))],
    ["rede fora", () => Promise.reject(new TypeError("Network request failed"))],
  ])("revogação que falha (%s) não faz sair() rejeitar, e o cofre fica vazio", async (_nome, falha) => {
    await guardarCredenciais({ access: ACCESS_A, refresh: "rt_A" });
    fetchFalso.mockImplementation(falha);

    await expect(sair()).resolves.toBeUndefined();
    await expect(lerCredenciais()).resolves.toBeNull();
  });
});
