/**
 * `features/openFinance/volta.ts` com os serviços reais e o `fetch` dublado
 * (`rotear`), relógio injetado (ver `open_finance_volta_apoio.ts`). Numeração =
 * linhas da tabela estados × eventos do plano. `VIVO` é `updated` (a coleta já
 * terminou); o item em `updating` tem os casos dele em
 * `open_finance_volta_updating.test.ts`.
 */
import { conferirVolta, INTERVALO_MS, JANELA_MS, MAX_POSTS, SEM_SENHA } from "@/features/openFinance/volta";
import { guardarCredenciais } from "@/storage/secure";

import { chamadas, fetchFalso, GENERICO, prepararCaso, resposta, rotear, S, segurar } from "./auth_apoio";
import { caminhos, dependencias, falhas, ITEM, lista, posts, servidor, VIVO } from "./open_finance_volta_apoio";

beforeEach(async () => {
  prepararCaso();
  await guardarCredenciais(S);
});

describe("volta do OAuth — link", () => {
  it.each([
    ["ausente", undefined],
    ["vazio", ""],
    ["repetido (array)", [ITEM, "outro"]],
    ["com espaço", "item 1"],
    ["com barra", "a/../b"],
    ["longo demais", "a".repeat(65)],
  ])("4 — itemId %s: sem-item, zero pedidos (nem perfil)", async (_nome, valor) => {
    servidor({});
    const { d, estados } = dependencias();
    await conferirVolta(valor, d);
    expect(estados).toEqual([{ fase: "sem-item" }]);
    expect(fetchFalso).not.toHaveBeenCalled();
  });

  it("21 — o uid é o do perfil: todos os pedidos vão para /open-finance/1", async () => {
    servidor({});
    await conferirVolta(ITEM, dependencias().d);
    expect(caminhos()).toEqual(["/auth/me", "/open-finance/1", "/open-finance/1/pluggy-item"]);
  });
});

describe("volta do OAuth — sucesso", () => {
  it("5/6 — item já na lista (webhook adotou): conectado, SEM POST", async () => {
    servidor({ get: () => lista(VIVO) });
    const { d, estados } = dependencias();
    await conferirVolta(ITEM, d);
    expect(estados).toEqual([{ fase: "conferindo", instavel: false }, { fase: "conectado", ui: VIVO.ui }]);
    expect(caminhos()).toEqual(["/auth/me", "/open-finance/1"]);
  });

  it.each(["removed", "item_missing"])("7 — item na lista como %s: faz o POST", async (state) => {
    servidor({ get: () => lista({ ...VIVO, ui: { ...VIVO.ui, state } }) });
    const { d, ultimo } = dependencias();
    await conferirVolta(ITEM, d);
    expect(posts()).toHaveLength(1);
    expect(ultimo()).toEqual({ fase: "conectado", ui: VIVO.ui });
  });

  it("7/8 — sem o item: POST com o id; conectado pela lista da resposta", async () => {
    servidor({});
    const { d, estados } = dependencias();
    await conferirVolta(ITEM, d);
    expect(chamadas()[2]).toMatchObject({ caminho: "/open-finance/1/pluggy-item", corpo: { item: { id: ITEM } } });
    expect(estados).toEqual([{ fase: "conferindo", instavel: false }, { fase: "conectado", ui: VIVO.ui }]);
  });

  it("8 — POST 200 sem o item na lista: segue o polling e conecta quando aparece", async () => {
    let vez = 0;
    servidor({ get: () => (++vez < 3 ? lista() : lista(VIVO)), post: () => lista() });
    const { d, ultimo } = dependencias();
    await conferirVolta(ITEM, d);
    expect(ultimo()).toEqual({ fase: "conectado", ui: VIVO.ui });
    expect(posts()).toHaveLength(2);
  });
});

describe("volta do OAuth — falha final", () => {
  it.each([
    [403, { detail: "Este item não pertence a esta conta." }, "Este item não pertence a esta conta."],
    [409, { detail: "Este item já está vinculado a outra conta." }, "Este item já está vinculado a outra conta."],
    [404, { detail: "Item não existe na Pluggy." }, "Item não existe na Pluggy."],
    [402, { detail: { code: "OF_BANK_LIMIT", message: "Seu plano já tem o máximo de bancos." } }, "Seu plano já tem o máximo de bancos."],
    [400, { detail: "Item Pluggy sem id." }, "Item Pluggy sem id."],
  ])("9 — POST %i: erro com o texto do servidor, e para", async (status, corpo, texto) => {
    servidor({ post: () => resposta(status, corpo) });
    const { d, estados } = dependencias();
    await conferirVolta(ITEM, d);
    expect(estados[estados.length - 1]).toEqual({ fase: "erro", texto });
    expect(posts()).toHaveLength(1);
  });

  it("10 — 403 password_required: texto fixo, nunca o código cru", async () => {
    servidor({ get: () => resposta(403, { detail: { error: "password_required" } }) });
    const { d, ultimo } = dependencias();
    await conferirVolta(ITEM, d);
    expect(ultimo()).toEqual({ fase: "erro", texto: SEM_SENHA });
    expect(posts()).toHaveLength(0);
  });

  it("12 — sessão expirada: avisa a sessão e para", async () => {
    rotear({
      "/auth/me": () => resposta(401, { detail: "expirado" }),
      "/auth/refresh": () => resposta(401, { detail: "invalid_refresh_token" }),
    });
    const { d, expirou, estados } = dependencias();
    await conferirVolta(ITEM, d);
    expect(expirou).toHaveBeenCalledWith("Sua sessão expirou. Entre de novo.");
    expect(estados).toEqual([{ fase: "conferindo", instavel: false }]);
  });

  it("13 — a conta trocou no meio (RequisicaoSuperada): para em silêncio", async () => {
    servidor({
      get: async () => {
        await guardarCredenciais({ access: "access-bia", refresh: "rt_bia" });
        return lista();
      },
    });
    const { d, estados, expirou } = dependencias();
    await conferirVolta(ITEM, d);
    expect(estados).toEqual([{ fase: "conferindo", instavel: false }]);
    expect(expirou).not.toHaveBeenCalled();
    expect(posts()).toHaveLength(0);
  });

  it("14 — contrato quebrado: erro genérico, e para", async () => {
    servidor({ get: () => resposta(200, { ok: true, connections: "x" }) });
    const { d, ultimo } = dependencias();
    await conferirVolta(ITEM, d);
    expect(ultimo()).toEqual({ fase: "erro", texto: GENERICO });
  });
});

describe("volta do OAuth — instabilidade e prazo", () => {
  it.each(falhas)("11 — GET com %s: conferindo instável, tenta de novo em 3 s, nunca erro", async (_nome, falha) => {
    let vez = 0;
    servidor({ get: (o) => (++vez === 1 ? falha(o) : lista(VIVO)) });
    const { d, estados, relogio } = dependencias();
    await conferirVolta(ITEM, d);
    expect(estados).toEqual([
      { fase: "conferindo", instavel: false },
      { fase: "conferindo", instavel: true },
      { fase: "conectado", ui: VIVO.ui },
    ]);
    expect(relogio.t).toBe(3_000);
  });

  // Rede caída no refresh: `RenovacaoIndisponivel` com status 0 — não é 5xx, e mesmo assim é transitória.
  it("11 — renovação instável (RenovacaoIndisponivel): tenta de novo", async () => {
    let vez = 0;
    rotear({
      "/open-finance/1": () => (++vez === 1 ? resposta(401, { detail: "expirado" }) : lista(VIVO)),
      "/auth/refresh": () => Promise.reject(new TypeError("Network request failed")),
    });
    const { d, ultimo, expirou } = dependencias();
    await conferirVolta(ITEM, d);
    expect(ultimo()).toEqual({ fase: "conectado", ui: VIVO.ui });
    expect(expirou).not.toHaveBeenCalled();
  });

  it("7/11 — POST com 5xx repete no máximo 3 vezes; depois só GET até o prazo", async () => {
    servidor({ post: () => resposta(502, {}) });
    const { d, ultimo, relogio } = dependencias();
    await conferirVolta(ITEM, d);
    expect(posts()).toHaveLength(MAX_POSTS);
    expect(ultimo()).toEqual({ fase: "ainda-conferindo" });
    expect(relogio.t).toBe(JANELA_MS);
  });

  it("15 — janela esgotada: ainda-conferindo; chamar de novo com o relógio já no prazo antigo abre janela (a partir de agora) e orçamento de POSTs novos", async () => {
    servidor({ post: () => lista() });
    const relogio = { t: 0 };
    const mesmoRelogio = { agora: () => relogio.t, esperar: async (ms: number) => void (relogio.t += ms) };
    const gets = () => caminhos().filter((c) => c === "/open-finance/1");
    const tentativas = JANELA_MS / INTERVALO_MS + 1;

    const primeira = dependencias(mesmoRelogio);
    await conferirVolta(ITEM, primeira.d);
    expect(primeira.ultimo()).toEqual({ fase: "ainda-conferindo" });
    expect(relogio.t).toBe(JANELA_MS);
    expect(gets()).toHaveLength(tentativas);
    expect(posts()).toHaveLength(MAX_POSTS);

    fetchFalso.mockClear();
    const segunda = dependencias(mesmoRelogio);
    await conferirVolta(ITEM, segunda.d);
    expect(segunda.ultimo()).toEqual({ fase: "ainda-conferindo" });
    expect(relogio.t).toBe(2 * JANELA_MS);
    expect(gets()).toHaveLength(tentativas);
    expect(posts()).toHaveLength(MAX_POSTS);
  });

  it("16 — app ao fundo durante a espera, volta depois do prazo: UMA tentativa, depois o prazo", async () => {
    servidor({ post: () => lista() });
    const relogio = { t: 0 };
    const { d, ultimo } = dependencias({
      agora: () => relogio.t,
      esperar: async () => void (relogio.t += 10 * 60_000),
    });
    await conferirVolta(ITEM, d);
    expect(caminhos().filter((c) => c === "/open-finance/1")).toHaveLength(2);
    expect(ultimo()).toEqual({ fase: "ainda-conferindo" });
  });
});

describe("volta do OAuth — cancelamento", () => {
  it("17/18 — cancelado com o GET em voo: a resposta é ignorada, nenhum estado, nenhum pedido novo", async () => {
    const portao = segurar();
    servidor({
      get: async () => {
        await portao.promessa;
        return lista();
      },
    });
    const flag = { cancelado: false };
    const { d, estados } = dependencias({ cancelado: () => flag.cancelado });
    const rodando = conferirVolta(ITEM, d);
    await new Promise((r) => setTimeout(r, 0));
    flag.cancelado = true;
    portao.soltar();
    await rodando;
    expect(estados).toEqual([{ fase: "conferindo", instavel: false }]);
    expect(caminhos()).toEqual(["/auth/me", "/open-finance/1"]);
  });

  it("17 — cancelado durante a espera: não volta a pedir", async () => {
    servidor({ get: () => resposta(503, {}) });
    const flag = { cancelado: false };
    const { d, estados } = dependencias({
      cancelado: () => flag.cancelado,
      esperar: async () => void (flag.cancelado = true),
    });
    await conferirVolta(ITEM, d);
    expect(caminhos()).toEqual(["/auth/me", "/open-finance/1"]);
    expect(estados).toEqual([{ fase: "conferindo", instavel: false }, { fase: "conferindo", instavel: true }]);
  });

  it("20 — de novo com o mesmo item já registrado: conectado sem POST", async () => {
    let registrado = false;
    servidor({
      get: () => (registrado ? lista(VIVO) : lista()),
      post: () => ((registrado = true), lista(VIVO)),
    });
    await conferirVolta(ITEM, dependencias().d);
    fetchFalso.mockClear();
    const { d, ultimo } = dependencias();
    await conferirVolta(ITEM, d);
    expect(ultimo()).toEqual({ fase: "conectado", ui: VIVO.ui });
    expect(posts()).toHaveLength(0);
  });
});
