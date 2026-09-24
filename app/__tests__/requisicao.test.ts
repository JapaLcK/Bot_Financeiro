/**
 * A FORMA da requisição que o app manda: credencial, cabeçalhos e cookie.
 *
 * Separado por assunto (§0.5): aqui não há ciclo de vida de sessão nem
 * tradução de erro, só o que sai no fio.
 */
import { z } from "zod";

import { USER_AGENT } from "@/api/aparelho";
import { ErroDeApi, SessaoExpirada, _resetRenovacao, chamar } from "@/api/client";
import {
  guardarCredenciais,
  lerCredenciais,
  limparCredenciais,
} from "@/storage/secure";

const schema = z.object({ ok: z.boolean() });

function resposta(status: number, corpo: unknown, cabecalhos: Record<string, string> = {}): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(cabecalhos),
    json: async () => corpo,
  } as Response;
}

const fetchFalso = jest.fn();

beforeEach(async () => {
  fetchFalso.mockReset();
  globalThis.fetch = fetchFalso as unknown as typeof fetch;
  _resetRenovacao();
  await limparCredenciais();
});

describe("credencial na requisição", () => {
  it("manda Bearer, o header do app e NENHUM cookie", async () => {
    await guardarCredenciais({ access: "tok-a", refresh: "rt_r" });
    fetchFalso.mockResolvedValue(resposta(200, { ok: true }));

    await chamar("/x", schema);

    const [, opcoes] = fetchFalso.mock.calls[0];
    expect(opcoes.headers["Authorization"]).toBe("Bearer tok-a");
    expect(opcoes.headers["X-PigBank-Client"]).toBe("app");
    // `credentials: omit` é o que impede o cookie jar do React Native de
    // guardar credencial ambiente sem querer — com ela, a escrita SEGUINTE
    // passaria a levar cookie e o servidor voltaria a exigir o par do CSRF.
    expect(opcoes.credentials).toBe("omit");
  });

  it("manda o User-Agent do app no login e na renovação", async () => {
    // É dele que o servidor tira o rótulo da sessão (core/sessions.py).
    fetchFalso.mockResolvedValue(resposta(200, { ok: true }));
    await chamar("/auth/login", schema, { metodo: "POST", semAuth: true });
    expect(fetchFalso.mock.calls[0][1].headers["User-Agent"]).toBe(USER_AGENT);

    fetchFalso.mockReset();
    await guardarCredenciais({ access: "velho", refresh: "rt_velho" });
    fetchFalso
      .mockResolvedValueOnce(resposta(401, {}))
      .mockResolvedValueOnce(
        resposta(200, { access_token: "n", refresh_token: "rt_n", dashboard_token: "d", expires_in: 900 }),
      )
      .mockResolvedValueOnce(resposta(200, { ok: true }));
    await chamar("/x", schema);
    const [url, opcoes] = fetchFalso.mock.calls[1];
    expect(url).toContain("/auth/refresh");
    expect(opcoes.headers["User-Agent"]).toBe(USER_AGENT);
  });

  it("não manda credencial em rota pública", async () => {
    await guardarCredenciais({ access: "tok-a", refresh: "rt_r" });
    fetchFalso.mockResolvedValue(resposta(200, { ok: true }));

    await chamar("/publica", schema, { semAuth: true });

    expect(fetchFalso.mock.calls[0][1].headers["Authorization"]).toBeUndefined();
  });

});

/**
 * `credencialSecundaria`: rotas em que o 401 pode ser da SENHA digitada, não
 * da sessão. Só o 401 de sessão leva `WWW-Authenticate` (frontend/routes/
 * shared.py, `WWW_AUTHENTICATE_401`).
 */
describe("credencialSecundaria", () => {
  const MARCA = { "WWW-Authenticate": 'Bearer realm="pigbank", error="invalid_token"' };
  const NOVAS = { access_token: "a2", refresh_token: "rt_2", dashboard_token: "d", expires_in: 900 };
  const setup = () => chamar("/auth/mfa/setup", schema, { metodo: "POST", credencialSecundaria: true });

  beforeEach(() => guardarCredenciais({ access: "a1", refresh: "rt_1" }));

  it("T1: 401 SEM a marca é erro da senha — uma requisição só, sessão de pé", async () => {
    fetchFalso.mockResolvedValue(resposta(401, { detail: "Senha incorreta." }));

    const erro = await setup().catch((e: unknown) => e);

    expect(erro).toBeInstanceOf(ErroDeApi);
    expect(erro).not.toBeInstanceOf(SessaoExpirada);
    expect((erro as ErroDeApi).detalhe).toBe("Senha incorreta.");
    expect(fetchFalso).toHaveBeenCalledTimes(1);
    await expect(lerCredenciais()).resolves.toEqual({ access: "a1", refresh: "rt_1" });
  });

  it("T2: 401 COM a marca (access vencido) renova, repete e entrega o 200", async () => {
    fetchFalso
      .mockResolvedValueOnce(resposta(401, { detail: "Token inválido ou expirado." }, MARCA))
      .mockResolvedValueOnce(resposta(200, NOVAS))
      .mockResolvedValueOnce(resposta(200, { ok: true }));

    await expect(setup()).resolves.toEqual({ ok: true });
    expect(fetchFalso.mock.calls.map(([u]) => String(u).replace("http://backend.teste", ""))).toEqual([
      "/auth/mfa/setup",
      "/auth/refresh",
      "/auth/mfa/setup",
    ]);
    expect(fetchFalso.mock.calls[2][1].headers["Authorization"]).toBe("Bearer a2");
  });

  it("T3: renovou e a repetição tomou 401 SEM a marca — erro da senha, e o cofre guarda o par NOVO", async () => {
    fetchFalso
      .mockResolvedValueOnce(resposta(401, { detail: "Token inválido ou expirado." }, MARCA))
      .mockResolvedValueOnce(resposta(200, NOVAS))
      .mockResolvedValueOnce(resposta(401, { detail: "Senha incorreta." }));

    const erro = await setup().catch((e: unknown) => e);

    expect(erro).not.toBeInstanceOf(SessaoExpirada);
    expect((erro as ErroDeApi).detalhe).toBe("Senha incorreta.");
    await expect(lerCredenciais()).resolves.toEqual({ access: "a2", refresh: "rt_2" });
  });

  it("T4: 401 com a marca e refresh recusado — fim de sessão de verdade", async () => {
    fetchFalso
      .mockResolvedValueOnce(resposta(401, { detail: "Token inválido ou expirado." }, MARCA))
      .mockResolvedValueOnce(resposta(401, { detail: "invalid_refresh_token" }));

    await expect(setup()).rejects.toBeInstanceOf(SessaoExpirada);
    await expect(lerCredenciais()).resolves.toBeNull();
  });
});
