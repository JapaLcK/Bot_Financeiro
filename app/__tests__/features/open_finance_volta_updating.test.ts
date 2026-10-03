/**
 * `features/openFinance/volta.ts` com o item em `updating` ("Atualizando…"): o
 * servidor devolve esse estado enquanto a coleta termina (medido no iPhone,
 * Nubank: ~42 s depois do `onSuccess`, depois `updated`). Só `updating` segue
 * consultando; visto o item nesta chamada, nunca mais POST. Numeração = N1–N11
 * do plano (N12 veio do Tester); A1–A13 = linhas da tabela estados × eventos.
 */
import { conferirVolta, INTERVALO_MS, JANELA_MS } from "@/features/openFinance/volta";
import { guardarCredenciais } from "@/storage/secure";

import { prepararCaso, resposta, rotear, S, segurar } from "./auth_apoio";
import { ATUALIZANDO, comEstado, dependencias, emSequencia, falhas, gets, ITEM, lista, posts, servidor, VIVO } from "./open_finance_volta_apoio";

const conferindo = (instavel: boolean) => ({ fase: "conferindo", instavel });
const conectado = (c: { ui: unknown }) => ({ fase: "conectado", ui: c.ui });
const atualizando = () => lista(ATUALIZANDO);

beforeEach(async () => {
  prepararCaso();
  await guardarCredenciais(S);
});

describe("volta do OAuth — item em updating segue consultando", () => {
  it("N1 (A1/A3/A4) — GET updating, updating, updated: mostra cada leitura, 3 GETs a cada 3 s, sem POST", async () => {
    servidor({ get: emSequencia(atualizando, atualizando, () => lista(VIVO)) });
    const { d, estados, relogio } = dependencias();
    await conferirVolta(ITEM, d);
    expect(estados).toEqual([conferindo(false), conectado(ATUALIZANDO), conectado(ATUALIZANDO), conectado(VIVO)]);
    expect(gets()).toHaveLength(3);
    expect(posts()).toHaveLength(0);
    expect(relogio.t).toBe(2 * INTERVALO_MS);
  });

  it("N2 (A2) — GET sem o item, POST devolve updating: segue consultando e termina em updated com 1 POST só", async () => {
    servidor({ get: emSequencia(() => lista(), () => lista(VIVO)), post: atualizando });
    const { d, estados } = dependencias();
    await conferirVolta(ITEM, d);
    expect(estados).toEqual([conferindo(false), conectado(ATUALIZANDO), conectado(VIVO)]);
    expect(posts()).toHaveLength(1);
  });

  it("N3 (A10) — sempre updating: a janela inteira de GETs, sem POST, e termina em organizando (nunca ainda-conferindo)", async () => {
    servidor({ get: atualizando });
    const { d, estados, ultimo, relogio } = dependencias();
    await conferirVolta(ITEM, d);
    expect(ultimo()).toEqual({ fase: "organizando" });
    expect(relogio.t).toBe(JANELA_MS);
    expect(gets()).toHaveLength(JANELA_MS / INTERVALO_MS + 1);
    expect(posts()).toHaveLength(0);
    expect(estados).not.toContainEqual({ fase: "ainda-conferindo" });
  });

  it("N4 (A6) — visto em updating, depois some da lista: 0 POST, e nenhum estado (nem instável) entre o conectado(updating) e o organizando", async () => {
    servidor({ get: emSequencia(atualizando, () => lista()) });
    const { d, estados } = dependencias();
    await conferirVolta(ITEM, d);
    expect(posts()).toHaveLength(0);
    expect(estados).toEqual([conferindo(false), conectado(ATUALIZANDO), { fase: "organizando" }]);
  });

  it.each(["removed", "item_missing"])("N5 (A5) — visto em updating, depois %s: mostra e para, SEM POST", async (state) => {
    servidor({ get: emSequencia(atualizando, () => lista(comEstado(state))) });
    const { d, estados } = dependencias();
    await conferirVolta(ITEM, d);
    expect(estados).toEqual([conferindo(false), conectado(ATUALIZANDO), conectado(comEstado(state))]);
    expect(gets()).toHaveLength(2);
    expect(posts()).toHaveLength(0);
  });

  it.each(["updated", "partial", "error_recoverable", "needs_user_action", "no_accounts", "paused", "xyz"])(
    "N10 (A4) — 1º GET já em %s: mostra e para em 1 GET (só updating repolla)",
    async (state) => {
      servidor({ get: () => lista(comEstado(state)) });
      const { d, estados } = dependencias();
      await conferirVolta(ITEM, d);
      expect(estados).toEqual([conferindo(false), conectado(comEstado(state))]);
      expect(gets()).toHaveLength(1);
      expect(posts()).toHaveLength(0);
    },
  );
});

describe("volta do OAuth — falhas com o item em updating", () => {
  it.each(falhas)("N6 (A7) — updating, %s, updated: nenhum 'conferindo' depois do 1º conectado; termina em updated", async (_nome, falha) => {
    servidor({ get: emSequencia(atualizando, falha, () => lista(VIVO)) });
    const { d, estados } = dependencias();
    await conferirVolta(ITEM, d);
    expect(estados).toEqual([conferindo(false), conectado(ATUALIZANDO), conectado(VIVO)]);
  });

  it("N7 (A12) — 503 ANTES de ver o item, depois updating, updated: o instável sai sem 'conferindo' novo no meio", async () => {
    servidor({ get: emSequencia(() => resposta(503, {}), atualizando, () => lista(VIVO)) });
    const { d, estados } = dependencias();
    await conferirVolta(ITEM, d);
    expect(estados).toEqual([conferindo(false), conferindo(true), conectado(ATUALIZANDO), conectado(VIVO)]);
  });

  it.each([
    ["403", () => resposta(403, { detail: "Este item não pertence a esta conta." })],
    ["404", () => resposta(404, { detail: "Item não existe na Pluggy." })],
    ["contrato quebrado", () => resposta(200, { ok: true, connections: "x" })],
  ])("N8 (A8) — updating, depois GET com %s: organizando, nunca erro", async (_nome, falha) => {
    servidor({ get: emSequencia(atualizando, falha) });
    const { d, estados } = dependencias();
    await conferirVolta(ITEM, d);
    expect(estados).toEqual([conferindo(false), conectado(ATUALIZANDO), { fase: "organizando" }]);
  });

  it("N9 (A9) — updating, depois sessão expirada: avisa a sessão, nada emitido depois", async () => {
    rotear({
      "/open-finance/1": emSequencia(atualizando, () => resposta(401, { detail: "expirado" })),
      "/auth/refresh": () => resposta(401, { detail: "invalid_refresh_token" }),
    });
    const { d, estados, expirou } = dependencias();
    await conferirVolta(ITEM, d);
    expect(expirou).toHaveBeenCalledWith("Sua sessão expirou. Entre de novo.");
    expect(estados).toEqual([conferindo(false), conectado(ATUALIZANDO)]);
  });

  it("N9 (A9) — updating, depois a conta trocou (RequisicaoSuperada): para em silêncio", async () => {
    servidor({
      get: emSequencia(atualizando, async () => {
        await guardarCredenciais({ access: "access-bia", refresh: "rt_bia" });
        return lista(ATUALIZANDO);
      }),
    });
    const { d, estados, expirou } = dependencias();
    await conferirVolta(ITEM, d);
    expect(estados).toEqual([conferindo(false), conectado(ATUALIZANDO)]);
    expect(expirou).not.toHaveBeenCalled();
    expect(gets()).toHaveLength(2);
  });
});

describe("volta do OAuth — cancelamento com o item em updating", () => {
  // O caso 17/18 de `open_finance_volta.test.ts` cancela com a lista VAZIA: lá a
  // ordem "confere o cancelamento, depois mostra" nunca importa. Aqui a resposta
  // em voo TRAZ o item em `updating`.
  it("N12 (A13) — cancelado com o GET em voo que traz o item em updating: nada emitido além do conferindo inicial", async () => {
    const portao = segurar();
    servidor({
      get: async () => {
        await portao.promessa;
        return lista(ATUALIZANDO);
      },
    });
    const flag = { cancelado: false };
    const { d, estados } = dependencias({ cancelado: () => flag.cancelado });
    const rodando = conferirVolta(ITEM, d);
    await new Promise((r) => setTimeout(r, 0));
    expect(gets()).toHaveLength(1);
    flag.cancelado = true;
    portao.soltar();
    await rodando;
    expect(estados).toEqual([conferindo(false)]);
    expect([gets().length, posts().length]).toEqual([1, 0]);
  });

  it("N12 (A13) — cancelado com o POST em voo que devolve o item em updating: nada emitido além do conferindo inicial", async () => {
    const portao = segurar();
    servidor({
      post: async () => {
        await portao.promessa;
        return lista(ATUALIZANDO);
      },
    });
    const flag = { cancelado: false };
    const { d, estados } = dependencias({ cancelado: () => flag.cancelado });
    const rodando = conferirVolta(ITEM, d);
    await new Promise((r) => setTimeout(r, 0));
    expect(posts()).toHaveLength(1);
    flag.cancelado = true;
    portao.soltar();
    await rodando;
    expect(estados).toEqual([conferindo(false)]);
    expect([gets().length, posts().length]).toEqual([1, 1]);
  });

  // Mede o cancelamento durante a espera. O laço tem DOIS guardas no caminho até
  // o GET seguinte: o de depois de `esperar` e o de depois de `uid ??=` (com o uid
  // já em cache não há `await` entre os dois). Qualquer um basta: tirar só um
  // deixa este caso verde; ele só fica vermelho sem os dois.
  it("N11 (A13) — updating, cancelado durante a espera: nenhum GET a mais, nada emitido depois", async () => {
    servidor({ get: atualizando });
    const flag = { cancelado: false };
    const { d, estados } = dependencias({
      cancelado: () => flag.cancelado,
      esperar: async () => void (flag.cancelado = true),
    });
    await conferirVolta(ITEM, d);
    expect(gets()).toHaveLength(1);
    expect(estados).toEqual([conferindo(false), conectado(ATUALIZANDO)]);
  });
});
