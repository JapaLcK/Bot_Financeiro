/**
 * A máquina de `criarConta.ts` e o serviço (`cadastrar`/`confirmarCadastro`),
 * com `fetch` falso e o dublê do cofre — mesma receita de `entrar.test.ts`.
 * A tela fica em `criar_conta_tela.test.tsx`.
 */
import * as authService from "@/services/auth";
import { entrar } from "@/services/auth";
import { FalhaNoCofre, lerCredenciais } from "@/storage/secure";
import {
  ERRO_TELEFONE,
  NOME_MAX,
  apagaSenhaNaFase,
  cadastrar,
  confirmar,
  normalizarTelefone,
  reenviar,
  validar,
  type DadosCadastro,
} from "@/features/auth/criarConta";

import telefones from "../../../tests/fixtures/telefones_cadastro.json";
import { GENERICO, chamadas, credencialDe, fetchFalso, prepararCaso, resposta, rotear, segurar, type Rota } from "./auth_apoio";

const VALIDOS: DadosCadastro = { nome: "Ana", email: "ana@x.com", telefone: "(11) 99999-8888", senha: "s3nha-boa" };
const DIGITADO: DadosCadastro = { nome: "  Ana  ", email: " ana@x.com ", telefone: "(11) 99999-8888", senha: " s3nha-boa " };
const CORPO = { email: "ana@x.com", password: " s3nha-boa ", name: "Ana", phone: "11999998888" };
const ENVIADO = { status: "verification_sent", email: "ana@x.com" };

beforeEach(prepararCaso);
afterEach(() => jest.restoreAllMocks());

describe("telefone: a mesma tabela do pytest (tests/fixtures/telefones_cadastro.json)", () => {
  it("a fixture traz a mensagem do servidor, igual à do app", () => {
    expect(ERRO_TELEFONE).toBe(telefones.mensagem);
  });

  it.each(telefones.casos)("normalizarTelefone($entrada) → $e164", ({ entrada, e164 }) => {
    expect(normalizarTelefone(entrada)).toBe(e164);
    expect(validar({ ...VALIDOS, telefone: entrada }).telefone).toBe(e164 === null ? telefones.mensagem : undefined);
  });

  it("dígito arábico-índico é recusado: o cliente é MAIS rígido que o servidor, nunca mais frouxo", () => {
    expect(normalizarTelefone("١١٩٩٩٩٩٨٨٨٨")).toBeNull();
  });
});

describe("validar", () => {
  it("dados válidos: nenhum erro", () => {
    expect(validar(VALIDOS)).toEqual({});
  });

  it.each<[string, Partial<DadosCadastro>, string]>([
    ["nome de 1 letra depois do trim", { nome: "  a  " }, "nome"],
    ["nome acima do teto", { nome: "x".repeat(NOME_MAX + 1) }, "nome"],
    ["e-mail sem @", { email: "ana.x.com" }, "email"],
    ["e-mail sem domínio", { email: "ana@x" }, "email"],
    ["senha de 7", { senha: "1234567" }, "senha"],
  ])("%s: erro só naquele campo", (_nome, troca, campo) => {
    expect(Object.keys(validar({ ...VALIDOS, ...troca }))).toEqual([campo]);
  });

  it("fronteiras aceitas: nome de 2 com espaços em volta, nome no teto, senha de 8", () => {
    expect(validar({ ...VALIDOS, nome: " Al ", senha: "12345678" })).toEqual({});
    expect(validar({ ...VALIDOS, nome: "x".repeat(NOME_MAX) })).toEqual({});
  });
});

describe("cadastrar (F/S)", () => {
  it("200: vai ao código; corpo aparado, telefone só com dígitos, header do app e sem Authorization", async () => {
    rotear({ "/auth/register": () => resposta(200, ENVIADO) });

    await expect(cadastrar(DIGITADO)).resolves.toEqual({ fase: "codigo", email: "ana@x.com" });

    expect(chamadas()).toEqual([{ caminho: "/auth/register", auth: undefined, corpo: CORPO }]);
    const cabecalhos = (fetchFalso.mock.calls[0]?.[1] as RequestInit).headers as Record<string, string>;
    expect(cabecalhos["X-PigBank-Client"]).toBe("app");
  });

  it.each<[string, Rota, string]>([
    ["429", () => resposta(429, { detail: "Muitas tentativas. Aguarde alguns minutos e tente novamente." }), "Muitas tentativas. Aguarde alguns minutos e tente novamente."],
    ["400 de telefone", () => resposta(400, { detail: ERRO_TELEFONE }), ERRO_TELEFONE],
    ["500", () => resposta(500, { detail: "psycopg.OperationalError" }), "Tivemos um problema aqui. Tente de novo em instantes."],
    ["rede fora", () => Promise.reject(new TypeError("Network request failed")), GENERICO],
  ])("%s: volta ao formulário com o texto certo", async (_nome, rota, aviso) => {
    rotear({ "/auth/register": rota });
    await expect(cadastrar(VALIDOS)).resolves.toEqual({ fase: "formulario", aviso });
  });

  it("reenviar manda o MESMO corpo, telefone e nome incluídos, e avisa que enviou", async () => {
    rotear({ "/auth/register": () => resposta(200, ENVIADO) });

    await cadastrar(DIGITADO);
    await expect(reenviar(DIGITADO)).resolves.toEqual({ fase: "codigo", email: "ana@x.com", info: "Enviamos um novo código." });

    const [primeiro, segundo] = chamadas();
    expect(segundo?.corpo).toEqual(primeiro?.corpo);
  });

  it("reenvio com 429: continua no código, com o aviso", async () => {
    rotear({ "/auth/register": () => resposta(429, { detail: "Muitas tentativas." }) });
    await expect(reenviar(VALIDOS)).resolves.toEqual({ fase: "codigo", email: "ana@x.com", aviso: "Muitas tentativas." });
  });
});

describe("confirmar (V)", () => {
  it("200: grava access e refresh no cofre e autentica uma vez", async () => {
    rotear({ "/auth/verify-email": () => resposta(200, credencialDe("ana@x.com")) });
    const autenticar = jest.fn();

    await expect(confirmar("ana@x.com", "123456", autenticar)).resolves.toBeNull();

    expect(chamadas()).toEqual([{ caminho: "/auth/verify-email", auth: undefined, corpo: { email: "ana@x.com", code: "123456" } }]);
    expect(autenticar).toHaveBeenCalledTimes(1);
    await expect(lerCredenciais()).resolves.toEqual({ access: "access-ana", refresh: "rt_ana" });
  });

  it("CONTROLE POSITIVO — 400: fica no código com o aviso do servidor, cofre vazio, sem autenticar", async () => {
    rotear({ "/auth/verify-email": () => resposta(400, { detail: "Código inválido." }) });
    const autenticar = jest.fn();

    await expect(confirmar("ana@x.com", "000000", autenticar)).resolves.toEqual({
      fase: "codigo",
      email: "ana@x.com",
      aviso: "Código inválido.",
    });
    expect(autenticar).not.toHaveBeenCalled();
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it.each<[string, Rota]>([
    ["500", () => resposta(500, {})],
    ["rede fora", () => Promise.reject(new TypeError("Network request failed"))],
    ["200 com forma errada (ContratoInvalido)", () => resposta(200, { token: "x" })],
  ])("%s: ambíguo, fica no código com o aviso genérico", async (_nome, rota) => {
    rotear({ "/auth/verify-email": rota });
    await expect(confirmar("ana@x.com", "123456", jest.fn())).resolves.toEqual({ fase: "codigo", email: "ana@x.com", aviso: GENERICO });
  });

  it("FalhaNoCofre: vira erro-cofre, sem autenticar", async () => {
    // Mesmo motivo do caso irmão de `entrar.test.ts`: a falha real (desfazer
    // que falha numa corrida) é cara de montar; aqui se prova o MAPEAMENTO.
    jest.spyOn(authService, "confirmarCadastro").mockRejectedValueOnce(new FalhaNoCofre(new Error("keychain recusou")));
    const autenticar = jest.fn();

    await expect(confirmar("ana@x.com", "123456", autenticar)).resolves.toEqual({ fase: "erro-cofre" });
    expect(autenticar).not.toHaveBeenCalled();
  });

  // Controle negativo do par (medido): `confirmarCadastro` sem `tentativa()`
  // gravando com `ultimaTentativa` lido na hora deixa o 1º vermelho; guardando
  // a vez sem avançá-la deixa o 2º vermelho.
  it("corrida: verify do cadastro em voo, `entrar()` de OUTRA conta começa depois — o cofre termina com a outra conta", async () => {
    const portao = segurar();
    rotear({
      "/auth/verify-email": async () => {
        await portao.promessa;
        return resposta(200, credencialDe("ana@x.com"));
      },
    });
    const autenticar = jest.fn();

    const cadastro = confirmar("ana@x.com", "123456", autenticar);
    await entrar("bia@x.com", "s3nha");
    portao.soltar();

    // Superada volta ao código sem aviso (não é `null`: a tela ficaria presa em "verificando").
    await expect(cadastro).resolves.toEqual({ fase: "codigo", email: "ana@x.com" });
    expect(autenticar).not.toHaveBeenCalled();
    await expect(lerCredenciais()).resolves.toEqual({ access: "access-bia", refresh: "rt_bia" });
  });

  it("corrida inversa: login de OUTRA conta em voo, o cadastro confirma depois — o login atrasado não sobrescreve", async () => {
    const portao = segurar();
    rotear({
      "/auth/login": async () => {
        await portao.promessa;
        return resposta(200, credencialDe("bia@x.com"));
      },
      "/auth/verify-email": () => resposta(200, credencialDe("ana@x.com")),
    });

    const login = entrar("bia@x.com", "s3nha");
    await confirmar("ana@x.com", "123456", jest.fn());
    portao.soltar();

    await expect(login).rejects.toBeInstanceOf(authService.EntradaSuperada);
    await expect(lerCredenciais()).resolves.toEqual({ access: "access-ana", refresh: "rt_ana" });
  });
});

it("a senha só é apagada ao voltar ao formulário ou no erro do cofre (#578)", () => {
  const apaga = (["formulario", "enviando", "codigo", "verificando", "reenviando", "erro-cofre"] as const).filter(apagaSenhaNaFase);
  expect(apaga).toEqual(["formulario", "erro-cofre"]);
});
