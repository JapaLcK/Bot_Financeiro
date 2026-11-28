/**
 * "Acesso de visualização" na Segurança, pelo roteador de verdade (só `fetch`, cofre e Alert são dublês): a seção aparece ou some pelo
 * `acesso_leitura` do /api/app/me; criar, trocar e desativar chamam as rotas certas com o código do e-mail; cada recusa tem texto próprio.
 * Os controles negativos (mutante, vermelho, restaura) estão no relato do PR.
 */
import { screen, waitFor } from "expo-router/testing-library";

import { chamadas, resposta } from "./auth_apoio";
import { abrir, chamadasA, digitar, esperarBotao, metodoDe, tocar } from "./conta_apoio";
import { preparar } from "./seguranca_sem_senha_apoio";

const ME = "/api/app/me";
const LEITURA = "/api/app/conta/leitura";
const CODIGO = `${LEITURA}/codigo`;
const erro = (status: number, code: string) => resposta(status, { error: { code, message: "x" } });

beforeEach(() => {
  preparar();
  jest.restoreAllMocks();
});

const abrirSeg = (freio: object | "falha", estado: { ativo: boolean; usuario: string | null }, extra: Parameters<typeof abrir>[2] = {}) =>
  abrir("/seguranca", {}, {
    [ME]: freio === "falha" ? () => resposta(403, { error: { code: "open_finance_onboarding_required", message: "x" } }) : () => resposta(200, freio),
    [LEITURA]: () => resposta(200, estado),
    [CODIGO]: () => resposta(200, { status: "sent" }),
    ...extra,
  });

describe("Acesso de visualização — o freio", () => {
  it.each([[{ acesso_leitura: false }], [{}]])("fora do freio %j e sem credencial: a seção não existe", async (freio) => {
    await abrirSeg(freio, { ativo: false, usuario: null });
    await waitFor(() => expect(chamadasA(LEITURA).length).toBe(1));
    expect(screen.queryByText("Acesso de visualização")).toBeNull();
  });

  it.each([[{ acesso_leitura: false }], [{ acesso_leitura: false, somente_leitura: false }]])("fora do freio %j e com credencial ativa: usuário e só Desativar", async (freio) => {
    let estado = { ativo: true, usuario: "sindico.ana" as string | null };
    await abrirSeg(freio, estado, { [LEITURA]: (o) => (o.method === "DELETE" ? resposta(200, (estado = { ativo: false, usuario: null })) : resposta(200, estado)) });
    await screen.findByText("Usuário: sindico.ana");
    expect(screen.queryByRole("button", { name: "Trocar senha" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Criar acesso" })).toBeNull();
    await tocar("Desativar acesso");
    await tocar("Enviar código para meu e-mail");
    digitar("Código do e-mail", "111222");
    await tocar("Desativar");
    expect(metodoDe(LEITURA)).toBe("DELETE");
    await screen.findByText("Acesso de visualização desativado.");
    expect(screen.queryByRole("button", { name: "Criar acesso" })).toBeNull();
  });

  it.each([[{ acesso_leitura: false, somente_leitura: true }], ["falha" as const]])("leitor ou /me que falha %j: sem a seção e sem ler a credencial", async (freio) => {
    await abrirSeg(freio, { ativo: true, usuario: "sindico.ana" });
    await waitFor(() => expect(chamadasA(ME).length).toBe(1));
    expect(screen.queryByText("Acesso de visualização")).toBeNull();
    expect(chamadasA(LEITURA)).toEqual([]);
  });

  it("com o freio e desativado: só Criar acesso", async () => {
    await abrirSeg({ acesso_leitura: true }, { ativo: false, usuario: null });
    await esperarBotao("Criar acesso");
    expect(screen.getByText("Quem entra com este usuário vê a conta inteira e não consegue lançar, editar nem pagar.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Trocar senha" })).toBeNull();
  });
});

describe("Acesso de visualização — criar, trocar, desativar", () => {
  it("criar: o código só sai no toque, o PUT leva usuário, senha e código, e a senha some", async () => {
    let estado = { ativo: false, usuario: null as string | null };
    await abrirSeg({ acesso_leitura: true }, estado, { [LEITURA]: (o) => (o.method === "PUT" ? resposta(200, (estado = { ativo: true, usuario: "sindico.ana" })) : resposta(200, estado)) });
    await esperarBotao("Criar acesso");
    await tocar("Criar acesso");
    digitar("Usuário", "sindico.ana");
    digitar("Senha", "senha-de-leitura-1");
    digitar("Confirmar senha", "senha-de-leitura-1");
    expect(chamadasA(CODIGO)).toEqual([]);
    await tocar("Enviar código para meu e-mail");
    expect(chamadasA(CODIGO).length).toBe(1);
    digitar("Código do e-mail", "123456");
    await tocar("Salvar");
    expect(chamadas().filter((c) => c.caminho === LEITURA && c.corpo)).toEqual([expect.objectContaining({ corpo: { usuario: "sindico.ana", senha: "senha-de-leitura-1", codigo: "123456" } })]);
    expect(metodoDe(LEITURA)).toBe("PUT");
    await screen.findByText("Usuário: sindico.ana");
    expect(screen.queryByDisplayValue("senha-de-leitura-1")).toBeNull();
    await esperarBotao("Trocar senha");
  });

  it("senhas diferentes não chamam o PUT", async () => {
    await abrirSeg({ acesso_leitura: true }, { ativo: false, usuario: null });
    await esperarBotao("Criar acesso");
    await tocar("Criar acesso");
    digitar("Usuário", "sindico.ana");
    digitar("Senha", "senha-de-leitura-1");
    digitar("Confirmar senha", "outra-senha-9999");
    digitar("Código do e-mail", "123456");
    await tocar("Salvar");
    await screen.findByText("As senhas não são iguais.");
    expect(chamadas().filter((c) => c.caminho === LEITURA && c.corpo)).toEqual([]);
  });

  it("trocar: o usuário vem preenchido e o PUT leva a senha nova", async () => {
    await abrirSeg({ acesso_leitura: true }, { ativo: true, usuario: "sindico.ana" });
    await esperarBotao("Trocar senha");
    await tocar("Trocar senha");
    expect(screen.getByLabelText("Usuário").props.value).toBe("sindico.ana");
    digitar("Nova senha", "nova-senha-123");
    digitar("Confirmar senha", "nova-senha-123");
    await tocar("Enviar código para meu e-mail");
    digitar("Código do e-mail", "654321");
    await tocar("Salvar");
    expect(chamadas().filter((c) => c.caminho === LEITURA && c.corpo).map((c) => c.corpo)).toEqual([{ usuario: "sindico.ana", senha: "nova-senha-123", codigo: "654321" }]);
  });

  it("desativar: DELETE com {codigo}, e volta a Criar acesso", async () => {
    let estado = { ativo: true, usuario: "sindico.ana" as string | null };
    await abrirSeg({ acesso_leitura: true }, estado, { [LEITURA]: (o) => (o.method === "DELETE" ? resposta(200, (estado = { ativo: false, usuario: null })) : resposta(200, estado)) });
    await esperarBotao("Desativar acesso");
    await tocar("Desativar acesso");
    await tocar("Enviar código para meu e-mail");
    digitar("Código do e-mail", "111222");
    await tocar("Desativar");
    expect(metodoDe(LEITURA)).toBe("DELETE");
    expect(chamadas().filter((c) => c.caminho === LEITURA && c.corpo).map((c) => c.corpo)).toEqual([{ codigo: "111222" }]);
    await esperarBotao("Criar acesso");
  });
});

describe("Acesso de visualização — recusas do servidor", () => {
  it.each([
    [409, "usuario_em_uso", "Esse usuário já está em uso. Escolha outro."],
    [422, "leitura_invalida", "Usuário de 4 a 40 caracteres (minúsculas, números, ponto, hífen ou _; sem @) e senha de 8 a 72 caracteres."],
    [400, "codigo_invalido", "Código incorreto. Confira o e-mail e tente de novo."],
    [400, "codigo_expirado", "O código expirou. Peça um novo."],
    [429, "rate_limited", "Muitas tentativas. Tente de novo mais tarde."],
    [403, "acesso_leitura_indisponivel", "O acesso de visualização ainda não está disponível para a sua conta."],
  ])("%i %s tem texto próprio e o formulário continua aberto", async (status, code, texto) => {
    await abrirSeg({ acesso_leitura: true }, { ativo: false, usuario: null }, { [LEITURA]: (o) => (o.method === "PUT" ? erro(status, code) : resposta(200, { ativo: false, usuario: null })) });
    await esperarBotao("Criar acesso");
    await tocar("Criar acesso");
    digitar("Usuário", "sindico.ana");
    digitar("Senha", "senha-de-leitura-1");
    digitar("Confirmar senha", "senha-de-leitura-1");
    digitar("Código do e-mail", "123456");
    await tocar("Salvar");
    await screen.findByText(texto);
    expect(screen.getByLabelText("Senha").props.value).toBe("senha-de-leitura-1");
  });

  it.each([
    [422, "leitura_invalida", /Usuário de 4 a 40/, "123456"],
    [403, "acesso_leitura_indisponivel", /ainda não está disponível/, "123456"],
    [400, "codigo_invalido", /Código incorreto/, ""],
    [400, "codigo_expirado", /O código expirou/, ""],
    [409, "usuario_em_uso", /já está em uso/, ""],
  ])("%i %s: o campo do código fica com %j", async (status, code, texto, restante) => {
    await abrirSeg({ acesso_leitura: true }, { ativo: false, usuario: null }, { [LEITURA]: (o) => (o.method === "PUT" ? erro(status, code) : resposta(200, { ativo: false, usuario: null })) });
    await esperarBotao("Criar acesso");
    await tocar("Criar acesso");
    digitar("Usuário", "sindico.ana");
    digitar("Senha", "senha-de-leitura-1");
    digitar("Confirmar senha", "senha-de-leitura-1");
    digitar("Código do e-mail", "123456");
    await tocar("Salvar");
    await screen.findByText(texto);
    expect(screen.getByLabelText("Código do e-mail").props.value).toBe(restante);
  });
});
