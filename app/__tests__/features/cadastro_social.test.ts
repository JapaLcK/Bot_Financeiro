/**
 * A fase K de `cadastroSocial.ts` (`criarContaSocial`), linhas 11–11g da
 * máquina, para o Google e a Apple, e o `validarPerfil` que C usa antes de sair
 * qualquer requisição.
 */
import { abandonarEntrada } from "@/services/auth";
import { lerCredenciais } from "@/storage/secure";
import { ERRO_TELEFONE, NOME_MAX, validarPerfil } from "@/features/auth/criarConta";
import { tocar, type EstadoEntrar } from "@/features/auth/entrar";
import { ERRO_COFRE_SOCIAL, criarContaSocial } from "@/features/auth/cadastroSocial";

import { GENERICO, chamadas, credencialDe, falharEscrita, gravador, prepararCaso, resposta, segurar, type Rota } from "./auth_apoio";
import { rotasGoogle } from "./google_apoio";

const C = { provedor: "google" as const, token: "gso_bia", email: "bia@gmail.com", nome: " Bia Souza " };

beforeEach(() => {
  prepararCaso();
  rotasGoogle();
});

async function criar(autenticar = jest.fn(), telefone = "(11) 99999-8888", c: typeof C | typeof A = C) {
  const { aplicados, aplicar } = gravador<EstadoEntrar>();
  await tocar(() => criarContaSocial(c, telefone, autenticar), aplicar);
  return aplicados;
}

/** A conta nova da Apple, com o e-mail relay (P6). */
const A = { provedor: "apple" as const, token: "gso_leo", email: "x1y2@privaterelay.appleid.com", nome: "Leo" };

describe("criarContaSocial (K)", () => {
  it("11 — 200: corpo com nome aparado, telefone só em dígitos e o aceite dos Termos; grava a sessão e autentica", async () => {
    const autenticar = jest.fn();
    expect(await criar(autenticar)).toEqual([{ fase: "criando-social", provedor: "google", token: "gso_bia", email: "bia@gmail.com" }]);

    expect(chamadas()).toEqual([
      {
        caminho: "/auth/google/complete-signup",
        auth: undefined,
        corpo: { token: "gso_bia", name: "Bia Souza", phone: "11999998888", accepted_terms: true },
      },
    ]);
    expect(autenticar).toHaveBeenCalledTimes(1);
    await expect(lerCredenciais()).resolves.toEqual({ access: "access-bia", refresh: "rt_bia" });
  });

  it.each([
    ["11a telefone", 400, "Informe um número de WhatsApp válido com DDD."],
    ["11a nome", 400, "O nome deve ter entre 2 e 50 caracteres."],
    ["11d 429", 429, "Muitas tentativas."],
  ])("%s: fica em C com o detail do servidor", async (_nome, status, detail) => {
    rotasGoogle({ "/auth/google/complete-signup": () => resposta(status as number, { detail }) });
    expect(await criar()).toEqual([{ fase: "cadastro-social", ...C, aviso: detail }]);
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("11b — cadastro expirado: volta ao formulário com o detail (o token morreu)", async () => {
    const detail = "Cadastro expirado. Inicie novamente o login com Google.";
    rotasGoogle({ "/auth/google/complete-signup": () => resposta(400, { detail }) });
    expect(await criar()).toEqual([{ fase: "formulario", aviso: detail }]);
  });

  it.each<[string, Rota]>([
    ["500", () => resposta(500, { detail: "psycopg boom" })],
    ["rede fora", () => Promise.reject(new TypeError("Network request failed"))],
    ["tempo limite", () => Promise.reject(new DOMException("Abortado", "AbortError"))],
    ["contrato quebrado", () => resposta(200, { ok: true })],
  ])("11c — %s: fica em C com o genérico", async (_nome, rota) => {
    rotasGoogle({ "/auth/google/complete-signup": rota });
    const autenticar = jest.fn();
    expect(await criar(autenticar)).toEqual([{ fase: "cadastro-social", ...C, aviso: GENERICO }]);
    expect(autenticar).not.toHaveBeenCalled();
  });

  it("11e — o cofre recusa gravar: X com a mensagem de conta criada", async () => {
    falharEscrita(true);
    expect(await criar()).toEqual([{ fase: "erro-cofre", mensagem: ERRO_COFRE_SOCIAL.google }]);
  });

  it("11f — superada no meio: volta a C sem aviso e não grava", async () => {
    const portao = segurar();
    rotasGoogle({
      "/auth/google/complete-signup": async () => {
        await portao.promessa;
        return resposta(200, credencialDe("bia@gmail.com"));
      },
    });
    const { aplicados, aplicar } = gravador<EstadoEntrar>();
    const autenticar = jest.fn();
    const p = tocar(() => criarContaSocial(C, "11999998888", autenticar), aplicar);
    for (let i = 0; i < 20; i++) await Promise.resolve();
    abandonarEntrada();
    portao.soltar();
    await p;

    expect(aplicados).toEqual([{ fase: "cadastro-social", ...C, aviso: undefined }]);
    expect(autenticar).not.toHaveBeenCalled();
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("11g — toque duplo: UMA requisição", async () => {
    const { aplicar } = gravador<EstadoEntrar>();
    await Promise.all([
      tocar(() => criarContaSocial(C, "11999998888", jest.fn()), aplicar),
      tocar(() => criarContaSocial(C, "11999998888", jest.fn()), aplicar),
    ]);
    expect(chamadas()).toHaveLength(1);
  });
});

describe("criarContaSocial (K) — Apple", () => {
  const rotasApple = (rota: Rota) => rotasGoogle({ "/auth/apple/complete-signup": rota });

  it("11 — 200: vai ao complete-signup da APPLE, grava a sessão e autentica", async () => {
    rotasApple(() => resposta(200, credencialDe("leo@x.com")));
    const autenticar = jest.fn();
    expect(await criar(autenticar, undefined, A)).toEqual([{ fase: "criando-social", provedor: "apple", token: "gso_leo", email: A.email }]);
    expect(chamadas()).toEqual([
      {
        caminho: "/auth/apple/complete-signup",
        auth: undefined,
        corpo: { token: "gso_leo", name: "Leo", phone: "11999998888", accepted_terms: true },
      },
    ]);
    expect(autenticar).toHaveBeenCalledTimes(1);
    await expect(lerCredenciais()).resolves.toEqual({ access: "access-leo", refresh: "rt_leo" });
  });

  it("11b — o texto de expirado da Apple também volta ao formulário", async () => {
    const detail = "Cadastro expirado. Entre com a Apple de novo.";
    rotasApple(() => resposta(400, { detail }));
    expect(await criar(undefined, undefined, A)).toEqual([{ fase: "formulario", aviso: detail }]);
  });

  // Controle negativo (medido): o texto do cofre sem provedor (sempre o do
  // Google) deixa este vermelho.
  it("11e — o cofre recusa: X manda entrar com a APPLE de novo", async () => {
    rotasApple(() => resposta(200, credencialDe("leo@x.com")));
    falharEscrita(true);
    expect(await criar(undefined, undefined, A)).toEqual([
      { fase: "erro-cofre", mensagem: "Sua conta foi criada, mas não conseguimos abrir a sessão neste aparelho. Entre com a Apple de novo." },
    ]);
  });
});

describe("validarPerfil (C)", () => {
  it("nome e WhatsApp válidos: sem erro", () => {
    expect(validarPerfil({ nome: " Bia ", telefone: "(11) 99999-8888" })).toEqual({});
    expect(validarPerfil({ nome: "x".repeat(NOME_MAX), telefone: "11999998888" })).toEqual({});
  });

  it.each([
    [{ nome: "B", telefone: "11999998888" }, ["nome"]],
    [{ nome: "x".repeat(NOME_MAX + 1), telefone: "11999998888" }, ["nome"]],
    [{ nome: "Bia", telefone: "" }, ["telefone"]],
    [{ nome: "", telefone: "123" }, ["nome", "telefone"]],
  ])("%j → erro em %j", (d, campos) => {
    const erros = validarPerfil(d);
    expect(Object.keys(erros).sort()).toEqual(campos);
    if (erros.telefone) expect(erros.telefone).toBe(ERRO_TELEFONE);
  });
});
