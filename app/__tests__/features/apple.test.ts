/**
 * A fase A de `apple.ts` (`continuarComApple`), linhas A1–A14 da máquina do
 * plano do item 7: com os serviços reais, `fetch` falso, o dublê do cofre, a
 * folha da Apple dublada (`signInAsync`) e o SHA-256 do Node no `expo-crypto`.
 */
import { FALHA_APPLE, continuarComApple } from "@/features/auth/apple";
import { tocar, type EstadoEntrar } from "@/features/auth/entrar";
import { abandonarEntrada } from "@/services/auth";
import { lerCredenciais } from "@/storage/secure";

import { PENDENTE_APPLE, falhaDaApple, folhaApple, rotasApple, voltaDaApple } from "./apple_apoio";
import { GENERICO, chamadas, credencialDe, falharEscrita, gravador, prepararCaso, resposta, segurar, type Rota } from "./auth_apoio";

// O `crypto` do Node, sem `@types/node` no app: só o que o teste usa.
const { createHash } = jest.requireActual<{
  createHash: (algoritmo: string) => { update: (dado: string) => { digest: (formato: "hex") => string } };
}>("crypto");

beforeEach(() => {
  prepararCaso();
  rotasApple();
  voltaDaApple();
});

async function apple(autenticar = jest.fn()) {
  const { aplicados, aplicar } = gravador<EstadoEntrar>();
  await tocar(() => continuarComApple(autenticar), aplicar);
  return aplicados;
}

const trocas = () => chamadas().filter((c) => c.caminho === "/auth/apple/exchange");

describe("continuarComApple (A)", () => {
  it("A1/A6 — pede nome e e-mail, troca o token e grava a sessão", async () => {
    const autenticar = jest.fn();
    expect(await apple(autenticar)).toEqual([{ fase: "apple" }]);

    expect(folhaApple).toHaveBeenCalledTimes(1);
    expect(folhaApple.mock.calls[0]![0]).toMatchObject({ requestedScopes: [0, 1] });
    expect(trocas()).toEqual([
      { caminho: "/auth/apple/exchange", auth: undefined, corpo: { identity_token: "id-token-ana", nonce: expect.any(String), name: null } },
    ]);
    expect(autenticar).toHaveBeenCalledTimes(1);
    await expect(lerCredenciais()).resolves.toEqual({ access: "access-ana", refresh: "rt_ana" });
  });

  // Controle negativo (medido): mandar o hash ao backend no lugar do cru deixa
  // este vermelho.
  it("nonce — a Apple recebe o SHA-256 hex do cru que vai ao backend; cru de 36 caracteres e novo a cada toque", async () => {
    await apple();
    await apple();

    const crus = trocas().map((c) => c.corpo.nonce as string);
    const enviadosApple = folhaApple.mock.calls.map(([o]) => o?.nonce);
    expect(crus).toHaveLength(2);
    for (const [i, cru] of crus.entries()) {
      expect(cru).toHaveLength(36);
      expect(enviadosApple[i]).toBe(createHash("sha256").update(cru).digest("hex"));
    }
    expect(crus[0]).not.toBe(crus[1]);
  });

  it.each([
    [{ givenName: "Ana", familyName: "Souza" }, "Ana Souza"],
    [{ givenName: "Ana", familyName: null }, "Ana"],
    [{ givenName: null, familyName: null }, null],
  ])("o nome da 1ª autorização vai à troca: %j → %j", async (partes, nome) => {
    const vazio = { namePrefix: null, middleName: null, nameSuffix: null, nickname: null };
    voltaDaApple({ fullName: { ...vazio, ...partes } });
    await apple();
    expect(trocas()[0]!.corpo.name).toBe(nome);
  });

  // Controles negativos (medidos): sem o corte, o 1º vira 120; com `.slice`
  // em UTF-16, o 2º leva meio emoji (surrogate solto).
  it.each([
    ["nome > 100: chega com 100 code points (o `max_length` do backend)", "a".repeat(120), "a".repeat(100)],
    ["emoji na fronteira: o corte não parte o par substituto", "a".repeat(99) + "😀😀", "a".repeat(99) + "😀"],
  ])("%s", async (_caso, givenName, nome) => {
    voltaDaApple({ fullName: { givenName, familyName: null, namePrefix: null, middleName: null, nameSuffix: null, nickname: null } });
    await apple();
    expect(trocas()[0]!.corpo.name).toBe(nome);
  });

  // Controle negativo (medido): tratar `ERR_REQUEST_CANCELED` como erro deixa
  // este vermelho.
  it("A3 — fechou a folha: formulário SEM aviso e nenhuma requisição", async () => {
    falhaDaApple("ERR_REQUEST_CANCELED");
    expect(await apple()).toEqual([{ fase: "formulario" }]);
    expect(chamadas()).toEqual([]);
  });

  it.each([
    "ERR_REQUEST_FAILED",
    "ERR_REQUEST_UNKNOWN",
    "ERR_REQUEST_NOT_HANDLED",
    "ERR_REQUEST_INVALID_RESPONSE",
    "ERR_REQUEST_NOT_INTERACTIVE",
  ])("A4 — %s: formulário com o aviso da Apple e nenhuma requisição", async (code) => {
    falhaDaApple(code);
    expect(await apple()).toEqual([{ fase: "formulario", aviso: FALHA_APPLE }]);
    expect(chamadas()).toEqual([]);
  });

  it("A5 — credencial sem identityToken: aviso e nenhuma requisição", async () => {
    voltaDaApple({ identityToken: null });
    expect(await apple()).toEqual([{ fase: "formulario", aviso: FALHA_APPLE }]);
    expect(chamadas()).toEqual([]);
  });

  it("A7 — conta com MFA: vai ao código TOTP sem gravar sessão", async () => {
    rotasApple({ "/auth/apple/exchange": () => resposta(200, { mfa_required: true, mfa_challenge: "ch-a", email: "ana@x.com" }) });
    const autenticar = jest.fn();
    expect(await apple(autenticar)).toEqual([{ fase: "mfa", desafio: "ch-a", email: "ana@x.com", modo: "totp" }]);
    expect(autenticar).not.toHaveBeenCalled();
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("A8 — conta nova: C da Apple com o e-mail e o nome sugerido, sem gravar sessão", async () => {
    voltaDaApple({ identityToken: "id-token-leo" });
    const autenticar = jest.fn();
    expect(await apple(autenticar)).toEqual([
      { fase: "cadastro-social", provedor: "apple", token: "gso_leo", email: PENDENTE_APPLE.email, nome: PENDENTE_APPLE.name_hint },
    ]);
    expect(autenticar).not.toHaveBeenCalled();
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it.each<[string, number, string]>([
    ["400 apple_token_invalid", 400, "Não deu para entrar com a Apple. Tente de novo."],
    ["400 apple_sem_email", 400, "A Apple não enviou um e-mail verificado. Tente de novo ou entre com e-mail e senha."],
    ["403 exclusão agendada", 403, "Esta conta está agendada para exclusão."],
    ["429", 429, "Muitas tentativas."],
  ])("A9 — %s: formulário com o detail", async (_nome, status, detail) => {
    rotasApple({ "/auth/apple/exchange": () => resposta(status, { detail, code: "x" }) });
    expect(await apple()).toEqual([{ fase: "formulario", aviso: detail }]);
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it.each<[string, Rota]>([
    ["503 apple_indisponivel", () => resposta(503, { detail: "Não deu para falar com a Apple agora.", code: "apple_indisponivel" })],
    ["500", () => resposta(500, { detail: "psycopg boom" })],
    ["rede fora", () => Promise.reject(new TypeError("Network request failed"))],
    ["tempo limite", () => Promise.reject(new DOMException("Abortado", "AbortError"))],
    ["contrato quebrado", () => resposta(200, { ok: true })],
  ])("A10 — %s: formulário com o genérico", async (_nome, rota) => {
    rotasApple({ "/auth/apple/exchange": rota });
    expect(await apple()).toEqual([{ fase: "formulario", aviso: GENERICO }]);
  });

  it("A11 — o cofre recusa gravar: X", async () => {
    falharEscrita(true);
    expect(await apple()).toEqual([{ fase: "erro-cofre" }]);
  });

  // Controle negativo (medido): `null` na `EntradaSuperada` deixa este
  // vermelho — nada é aplicado e a tela ficaria presa em A.
  it("A12 — troca superada no meio: formulário sem aviso, nunca `null`, nada gravado", async () => {
    const portao = segurar();
    rotasApple({
      "/auth/apple/exchange": async () => {
        await portao.promessa;
        return resposta(200, credencialDe("ana@x.com"));
      },
    });
    const { aplicados, aplicar } = gravador<EstadoEntrar>();
    const autenticar = jest.fn();
    const p = tocar(() => continuarComApple(autenticar), aplicar);
    for (let i = 0; i < 20; i++) await Promise.resolve();
    abandonarEntrada();
    portao.soltar();
    await p;

    expect(aplicados).toEqual([{ fase: "formulario" }]);
    expect(autenticar).not.toHaveBeenCalled();
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("A14 — toque duplo: UMA folha e UMA troca", async () => {
    const { aplicar } = gravador<EstadoEntrar>();
    await Promise.all([tocar(() => continuarComApple(jest.fn()), aplicar), tocar(() => continuarComApple(jest.fn()), aplicar)]);
    expect(folhaApple).toHaveBeenCalledTimes(1);
    expect(trocas()).toHaveLength(1);
  });
});
