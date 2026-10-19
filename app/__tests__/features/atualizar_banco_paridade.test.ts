import { ErroDeApi, RequisicaoSuperada, SessaoExpirada } from "@/api/client";
import type { Conexao, Refresh } from "@/api/schemas/openFinance";
import * as app from "@/features/painel/atualizarBanco";
import { JANELA_MS, transitoria } from "@/features/openFinance/volta";
import * as web from "../../../webapp/src/dashboard/lib/atualizarBanco";

/**
 * A máquina do "Atualizar agora" existe DUAS vezes, de propósito: `app/src/features/painel/atualizarBanco.ts` e o porto do /painel (`webapp/src/dashboard/lib/atualizarBanco.ts`, sem imports,
 * porque o typecheck do app segue a cadeia de imports). O que prende as duas é este arquivo: a MESMA tabela de cenários roda nos dois lados, com o mesmo mundo falso, e o TRAÇO inteiro tem de ser igual
 * (estados, falas, recarga, sucesso, aviso, os instantes de cada leitura e cada espera). Os textos e as constantes contra o servidor ficam em `tests/test_app_espelhos.py`.
 * O mapeamento de erro do web (ErroApi → transitória/silenciosa) NÃO é coberto aqui: os dois lados recebem as MESMAS funções, as do app (o do web está no Playwright, W5/W6).
 */
const SYNC_VELHO = "2026-10-10T12:00:00Z", SYNC_NOVO = "2026-10-10T13:00:00Z";
const con = (id: string | null, state: string, detail: string | null = null, sync = SYNC_VELHO, banco: string | null = "Nubank"): Conexao => ({
  id: 1, status: "ACTIVE", status_reason: null, last_sync_at: sync, reconnected_at: null, provider_item_id: id, institution_name: banco, ui: { state, label: `rotulo-${state}`, detail },
});
const item = (id: string, state: string, still = false, detail: string | null = null, reason: string | null = null, banco: string | null = "Nubank") =>
  ({ item_id: id, institution: banco, state, label: `rotulo-${state}`, detail, reason, still_updating: still });
const refresh = (items: ReturnType<typeof item>[], connections: Conexao[] = [], ok?: boolean): Refresh => ({ connections, sync: { items, ...(ok === undefined ? {} : { ok }) } });
const aborto = () => Object.assign(new Error("Aborted"), { name: "AbortError" });

type Modulo = typeof app | typeof web;
type Mundo = { enviar?: () => Promise<Refresh>; lista?: (n: number) => Conexao[] | Error };

/** Roda `entrada` num módulo com um mundo falso e devolve o traço. */
async function traco(m: Modulo, entrada: (m: Modulo, d: app.Deps) => Promise<void>, mundo: Mundo, vivoAte = Infinity) {
  const tr: unknown[] = []; let t = 0, leituras = 0;
  const d: app.Deps = {
    agora: () => t,
    esperar: async (ms) => { tr.push(["esperar", ms]); t += ms; },
    atual: () => t < vivoAte,
    enviar: mundo.enviar ?? (async () => refresh([item("it_1", "updated")])),
    ler: async () => { tr.push(["ler", t]); const r = mundo.lista?.(++leituras) ?? [con("it_1", "updated", null, SYNC_NOVO)]; if (r instanceof Error) throw r; return r; },
    aoMudar: (e) => tr.push(["estado", e]),
    recarregar: () => tr.push("recarregar"),
    falhou: (e) => tr.push(["falhou", e]),
    transitoria, silencioso: (e) => e instanceof SessaoExpirada || e instanceof RequisicaoSuperada,
    sucesso: () => tr.push("sucesso"), aviso: () => tr.push("aviso"), anunciar: (x) => tr.push(["fala", x]),
  };
  tr.push(["fim", await entrada(m, d).then(() => "ok", (e) => `lançou:${String(e)}`)]);
  return tr;
}
const igual = async (entrada: (m: Modulo, d: app.Deps) => Promise<void>, mundo: Mundo, vivoAte?: number) => {
  const a = await traco(app, entrada, mundo, vivoAte), w = await traco(web, entrada, mundo, vivoAte);
  expect(w).toEqual(a);
  return a;
};
const post = (m: Modulo, d: app.Deps) => m.atualizar(d);

const ESTADOS = ["updated", "rate_limited", "updating", "partial", "error_recoverable", "no_accounts", "needs_user_action", "item_missing", "paused", "removed", "estado_inventado", ""];
const MOTIVOS = [null, "", "ok", "OK", "no_accounts", "read_failed", "sync_failed"];
const DETALHES = [null, app.AUTORIZE_NO_APP, "Reautorize o banco."];
// Depois de uma coleta, a lista passa de `updating` para `updated` com o sync novo (o fim normal); com `updating` para sempre, vai até o teto.
const lista = (n: number) => [con("it_1", n < 3 ? "updating" : "updated", null, n < 3 ? SYNC_VELHO : SYNC_NOVO)];

describe("o porto do /painel e a máquina do app dão o MESMO traço", () => {
  it("POST com um item: cada estado × motivo × still_updating × detalhe", async () => {
    let n = 0;
    for (const state of ESTADOS) for (const reason of MOTIVOS) for (const still of [false, true]) for (const detail of DETALHES) {
      const tr = await igual(post, { enviar: async () => refresh([item("it_1", state, still, detail, reason)], [con("it_1", state)]), lista });
      expect(tr.length).toBeGreaterThan(1); n++;
    }
    expect(n).toBe(ESTADOS.length * MOTIVOS.length * 2 * DETALHES.length);
  });

  it("POST com dois itens (o erro de um não é escondido pelo outro, em qualquer ordem)", async () => {
    const pares = [["updating", true, null], ["updating", false, "ok"], ["updated", false, "ok"], ["partial", false, "Parte"], ["error_recoverable", true, "Erro"], ["estado_inventado", false, null], ["rate_limited", true, null]] as const;
    for (const [s1, k1, d1] of pares) for (const [s2, k2, d2] of pares) {
      await igual(post, { enviar: async () => refresh([item("it_1", s1, k1, d1, "ok"), item("it_2", s2, k2, d2, "ok", "Inter")], [con("it_1", s1), con("it_2", s2, null, SYNC_VELHO, "Inter")]), lista });
    }
  });

  it("items:[] com ok verdadeiro, falso e ausente; banco sem nome; detalhe de problema sem ponto e com ponto", async () => {
    for (const ok of [true, false, undefined]) await igual(post, { enviar: async () => refresh([], [], ok) });
    await igual(post, { enviar: async () => refresh([item("it_1", "partial", false, "Cartão.", null, null)]) });
    await igual(post, { enviar: async () => refresh([item("it_1", "partial", false, "Cartão", null, null)]) });
  });

  it("o POST lança: 500, 429, 404, rede, 401, 402, sessão, requisição superada; o AbortError do prazo com a leitura de depois em cada desfecho", async () => {
    for (const e of [new ErroDeApi(500, "x"), new ErroDeApi(429, "x"), new ErroDeApi(404, "x"), new TypeError("rede"), new ErroDeApi(402, "x"), new SessaoExpirada(), new RequisicaoSuperada()]) {
      await igual(post, { enviar: async () => { throw e; } });
    }
    const prazo = async (): Promise<Refresh> => { throw aborto(); };
    await igual(post, { enviar: prazo, lista: (n) => (n < 3 ? [con("it_1", "updating")] : [con("it_1", "updated", null, SYNC_NOVO)]) });
    await igual(post, { enviar: prazo, lista: () => [con("it_1", "updated")] });
    await igual(post, { enviar: prazo, lista: () => [con("it_1", "partial", "Parte dos dados")] });
    await igual(post, { enviar: prazo, lista: () => new TypeError("rede") });
    await igual(post, { enviar: prazo, lista: () => new ErroDeApi(402, "x") });
    await igual(post, { enviar: prazo, lista: () => [] });
  });

  it("a cadência até o teto: updating para sempre, GET que falha (rede, 5xx, 429, 404, 401), item que some, item que sai da coleta com problema", async () => {
    const coleta = refresh([item("it_1", "updated", true)], [con("it_1", "updated")]);
    const enviar = async () => coleta;
    await igual(post, { enviar, lista: () => [con("it_1", "updating")] });
    for (const e of [new TypeError("rede"), new ErroDeApi(503, "x"), new ErroDeApi(429, "x"), new ErroDeApi(404, "x"), new SessaoExpirada()]) {
      await igual(post, { enviar, lista: () => e });
      await igual(post, { enviar, lista: (n) => (n === 1 ? e : [con("it_1", "updated", null, SYNC_NOVO)]) });
      await igual(post, { enviar, lista: (n) => (n === 2 ? [con("it_1", "updating")] : e) });
    }
    await igual(post, { enviar, lista: () => [] });
    await igual(post, { enviar, lista: () => [con("it_1", "needs_user_action", "Reautorize o banco")] });
    await igual(post, { enviar, lista: () => [con("it_1", "estado_inventado")] });
    await igual(post, { enviar, lista: (n) => [con("it_1", "updated", null, n < 7 ? SYNC_VELHO : SYNC_NOVO)] });
  });

  it("a operação cai no meio (X, troca de conta): durante a espera, durante o POST e durante a leitura", async () => {
    const coleta = refresh([item("it_1", "updated", true)], [con("it_1", "updated")]);
    for (const ate of [0, 1, 5_000, 5_001, 15_000, 100_000]) await igual(post, { enviar: async () => coleta, lista: () => [con("it_1", "updating")] }, ate);
  });

  it("(a0) e o estouro do prazo: acompanharLista sobre listas de cada forma", async () => {
    const listas: Conexao[][] = [
      [], [con("it_1", "updating")], [con("it_1", "updated")], [con("it_1", "partial", "Parte")], [con(null, "updating")],
      [con("it_1", "updating"), con("it_2", "updating", null, SYNC_VELHO, "Inter")], [con("it_1", "updating"), con("it_2", "paused", "Pausado", SYNC_VELHO, "Inter")],
      [con("it_1", "needs_user_action", "Reautorize o banco"), con("it_2", "updating", null, SYNC_VELHO, "Inter")], [con("it_1", "estado_inventado")],
    ];
    for (const l of listas) {
      await igual((m, d) => m.acompanharLista(d, l), { lista });
      await igual((m, d) => m.acompanharLista(d, l), { lista: () => [con("it_1", "updating")] });
      await igual((m, d) => m.acompanhar(d, new Map(l.flatMap((c) => (c.provider_item_id ? [[c.provider_item_id, c.last_sync_at] as const] : [])))), { lista });
    }
  });

  it("classificar: a mesma ação para cada lista, com e sem vaga no plano", () => {
    const itens = [con("a", "updated"), con("b", "partial"), con("c", "updating"), con("d", "updating", app.COLETA_VENCIDA), con("e", "needs_user_action", "Reautorize o banco"),
      con("f", "needs_user_action", app.AUTORIZE_NO_APP), con(null, "needs_user_action", "Reautorize o banco"), con(null, "updating"), con("g", "paused", "Pausado"), con("h", "item_missing"), con("i", "removed"), con("j", "estado_inventado"), con("k", "error_recoverable"), con("l", "no_accounts")];
    const listas: Conexao[][] = [[], ...itens.map((c) => [c]), ...itens.flatMap((x, i) => itens.slice(i + 1).map((y) => [x, y]))];
    for (const l of listas) for (const pode of [true, false]) expect(web.classificar(l, pode)).toEqual(app.classificar(l, pode));
  });

  it("as constantes e os textos são os mesmos (o servidor e o settings.html ficam em test_app_espelhos.py)", () => {
    for (const k of ["CADENCIA_MS", "DEPOIS_MS", "MOTIVOS_COLETANDO", "AUTORIZE_NO_APP", "COLETA_VENCIDA", "ATUALIZANDO_SOZINHO", "FALHA", "AINDA"] as const) expect(web[k]).toEqual(app[k]);
    for (const k of ["SEGUE", "OK"] as const) expect([...web[k]]).toEqual([...app[k]]);
    expect(web.OCIOSO).toEqual(app.OCIOSO);
    expect(web.JANELA_MS).toBe(JANELA_MS);
  });
});
