/**
 * A fase G de `google.ts` (`continuarComGoogle`), linhas 1–7 da máquina do
 * plano: com os serviços reais, `fetch` falso, o dublê do cofre e a folha do
 * Google dublada (`openAuthSessionAsync`).
 */
import { abandonarEntrada } from "@/services/auth";
import { lerCredenciais } from "@/storage/secure";
import { tocar, type EstadoEntrar } from "@/features/auth/entrar";
import { CADASTRO_GOOGLE_EXPIRADO, continuarComGoogle } from "@/features/auth/google";

import { GENERICO, chamadas, falharEscrita, gravador, prepararCaso, resposta, segurar, type Rota } from "./auth_apoio";
import { CODIGO_INVALIDO, PENDENTE, abrirFolha, rotasGoogle, voltaDoGoogle } from "./google_apoio";

beforeEach(() => {
  prepararCaso();
  rotasGoogle();
});

async function google(autenticar = jest.fn()) {
  const { aplicados, aplicar } = gravador<EstadoEntrar>();
  await tocar(() => continuarComGoogle(autenticar), aplicar);
  return aplicados;
}

describe("continuarComGoogle (G)", () => {
  it("6 — abre o start com app=2 e volta por pigbank://auth; o código vai no corpo da troca e a sessão é gravada", async () => {
    voltaDoGoogle("pigbank://auth?code=code-ana");
    const autenticar = jest.fn();

    expect(await google(autenticar)).toEqual([{ fase: "google" }]);

    expect(abrirFolha).toHaveBeenCalledWith("http://backend.teste/auth/google/start?app=2", "pigbank://auth");
    expect(chamadas()).toEqual([{ caminho: "/auth/google/exchange", auth: undefined, corpo: { code: "code-ana" } }]);
    expect(autenticar).toHaveBeenCalledTimes(1);
    await expect(lerCredenciais()).resolves.toEqual({ access: "access-ana", refresh: "rt_ana" });
  });

  it("6a — conta com MFA: vai ao código TOTP, sem gravar nada", async () => {
    voltaDoGoogle("pigbank://auth?code=code-ana");
    rotasGoogle({
      "/auth/google/exchange": () => resposta(200, { mfa_required: true, mfa_challenge: "ch-g", email: "ana@x.com" }),
    });
    const autenticar = jest.fn();

    expect(await google(autenticar)).toEqual([{ fase: "mfa", desafio: "ch-g", email: "ana@x.com", modo: "totp" }]);
    expect(autenticar).not.toHaveBeenCalled();
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it.each(["cancel", "dismiss", "locked"] as const)("3 — folha fechada (%s): formulário sem aviso, nenhuma requisição", async (type) => {
    voltaDoGoogle({ type });
    expect(await google()).toEqual([{ fase: "formulario" }]);
    expect(chamadas()).toEqual([]);
  });

  it("4 — openAuthSessionAsync rejeita: formulário com o genérico", async () => {
    abrirFolha.mockReset();
    abrirFolha.mockRejectedValue(new Error("outra sessão aberta"));
    expect(await google()).toEqual([{ fase: "formulario", aviso: GENERICO }]);
  });

  it.each<[string, string | undefined]>([
    ["cancelado", undefined],
    ["falha", "Não deu para entrar com o Google. Tente de novo."],
    ["email_nao_verificado", "Seu e-mail no Google ainda não foi verificado. Verifique no Google e tente de novo."],
    ["conta_em_exclusao", "Esta conta está agendada para exclusão."],
    ["expirou", GENERICO],
  ])("5 — ?erro=%s: formulário com o texto do app, nenhuma requisição", async (codigo, aviso) => {
    voltaDoGoogle(`pigbank://auth?erro=${codigo}`);
    expect(await google()).toEqual([{ fase: "formulario", aviso }]);
    expect(chamadas()).toEqual([]);
  });

  it("5c — URL de outro app: genérico, e o código dela NUNCA vai à troca", async () => {
    voltaDoGoogle("pigbankai://auth?code=code-ana");
    expect(await google()).toEqual([{ fase: "formulario", aviso: GENERICO }]);
    expect(chamadas()).toEqual([]);
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it.each<[string, Rota, string]>([
    ["6b 400 google_code_invalid", () => resposta(400, CODIGO_INVALIDO), CODIGO_INVALIDO.detail],
    ["6c 403 exclusão", () => resposta(403, { detail: "Esta conta está agendada para exclusão em 2026-10-01." }), "Esta conta está agendada para exclusão em 2026-10-01."],
    ["6c 429", () => resposta(429, { detail: "Muitas tentativas." }), "Muitas tentativas."],
    ["6d 500", () => resposta(500, { detail: "psycopg boom" }), GENERICO],
    ["6d rede fora", () => Promise.reject(new TypeError("Network request failed")), GENERICO],
    ["6d tempo limite", () => Promise.reject(new DOMException("Abortado", "AbortError")), GENERICO],
    ["6d contrato quebrado", () => resposta(200, { ok: true }), GENERICO],
  ])("%s: formulário com o texto certo, sem sessão", async (_nome, troca, aviso) => {
    voltaDoGoogle("pigbank://auth?code=code-ana");
    rotasGoogle({ "/auth/google/exchange": troca });
    const autenticar = jest.fn();

    expect(await google(autenticar)).toEqual([{ fase: "formulario", aviso }]);
    expect(autenticar).not.toHaveBeenCalled();
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("6e — o cofre recusa gravar: fase X", async () => {
    voltaDoGoogle("pigbank://auth?code=code-ana");
    falharEscrita(true);
    expect(await google()).toEqual([{ fase: "erro-cofre" }]);
  });

  // Controle negativo (medido): `EntradaSuperada` devolvendo `null` deixa este
  // vermelho — nada é aplicado e a tela ficaria presa em G.
  it("6f — superada no meio da troca: volta ao formulário sem aviso, nunca `null`, e não grava", async () => {
    voltaDoGoogle("pigbank://auth?code=code-ana");
    const portao = segurar();
    rotasGoogle({
      "/auth/google/exchange": async () => {
        await portao.promessa;
        return resposta(200, { user_id: 1, email: "ana@x.com", access_token: "access-ana", refresh_token: "rt_ana", dashboard_token: "d", expires_in: 900 });
      },
    });
    const { aplicados, aplicar } = gravador<EstadoEntrar>();
    const autenticar = jest.fn();
    const p = tocar(() => continuarComGoogle(autenticar), aplicar);
    for (let i = 0; i < 20; i++) await Promise.resolve();
    abandonarEntrada();
    portao.soltar();
    await p;

    expect(aplicados).toEqual([{ fase: "formulario" }]);
    expect(autenticar).not.toHaveBeenCalled();
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("7 — conta nova: busca o pré-cadastro e vai a C com o nome sugerido, sem gravar sessão", async () => {
    voltaDoGoogle("pigbank://auth?onboarding=gso_bia");
    expect(await google()).toEqual([
      { fase: "google-cadastro", token: "gso_bia", email: PENDENTE.email, nome: PENDENTE.name_hint },
    ]);
    expect(chamadas().map((c) => c.caminho)).toEqual(["/auth/google/pending/gso_bia"]);
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("7 — o token vai codificado no caminho", async () => {
    voltaDoGoogle("pigbank://auth?onboarding=a%2F..%2Fb");
    rotasGoogle({ "/auth/google/pending/a%2F..%2Fb": () => resposta(200, PENDENTE) });
    expect((await google())[0]).toMatchObject({ fase: "google-cadastro", token: "a/../b" });
  });

  it.each<[string, Rota, string]>([
    ["404", () => resposta(404, { detail: "Cadastro expirado ou inválido." }), CADASTRO_GOOGLE_EXPIRADO],
    ["500", () => resposta(500, {}), GENERICO],
    ["429", () => resposta(429, { detail: "Muitas tentativas." }), GENERICO],
    ["rede fora", () => Promise.reject(new TypeError("Network request failed")), GENERICO],
  ])("7 — pré-cadastro %s: formulário", async (_nome, pendente, aviso) => {
    voltaDoGoogle("pigbank://auth?onboarding=gso_bia");
    rotasGoogle({ "/auth/google/pending/gso_bia": pendente });
    expect(await google()).toEqual([{ fase: "formulario", aviso }]);
  });
});
