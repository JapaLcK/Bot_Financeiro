import { ErroDeApi, SessaoExpirada } from "@/api/client";
import type { Conexao, Refresh } from "@/api/schemas/openFinance";
import { acompanhar, atualizar, AUTORIZE_NO_APP, classificar, COLETA_VENCIDA, OCIOSO, type Deps, type EstadoAtualizar } from "@/features/painel/atualizarBanco";

// A máquina pura do "Atualizar agora", com relógio falso e rede injetada. A conversa pela rota está em `painel_atualizar_banco.test.tsx`.
const con = (id: string, state: string, detail: string | null = null, extra: Partial<Conexao> = {}): Conexao => ({
  id: 1, status: "ACTIVE", status_reason: null, last_sync_at: "2026-10-10T12:00:00Z", reconnected_at: null, provider_item_id: id, institution_name: "Nubank",
  ui: { state, label: state, detail }, ...extra,
});
const item = (id: string, state: string, still = false, detail: string | null = null) => ({ item_id: id, institution: "Nubank", state, label: state, detail, still_updating: still });
const refresh = (items: ReturnType<typeof item>[], connections: Conexao[] = []): Refresh => ({ connections, sync: { items } });

/** Relógio falso: `esperar` avança o tempo; cada `ler` anota o instante (para provar cadência e ausência de sobreposição). */
function mundo(opcoes: { enviar?: () => Promise<Refresh>; lista?: (n: number, t: number) => Conexao[] | Error } = {}) {
  const w = { t: 0, leituras: [] as number[], esperas: [] as number[], estados: [] as EstadoAtualizar[], sucessos: 0, avisos: 0, recarregas: 0, falhas: [] as unknown[], falas: [] as string[], vivo: true, emVoo: 0, maxEmVoo: 0 };
  const d: Deps = {
    agora: () => w.t,
    esperar: async (ms) => { w.esperas.push(ms); w.t += ms; },
    atual: () => w.vivo,
    enviar: opcoes.enviar ?? (async () => refresh([item("it_1", "updated")])),
    ler: async () => {
      w.leituras.push(w.t); w.maxEmVoo = Math.max(w.maxEmVoo, ++w.emVoo);
      try { const r = opcoes.lista?.(w.leituras.length, w.t) ?? [con("it_1", "updated")]; if (r instanceof Error) throw r; return r; } finally { w.emVoo--; }
    },
    aoMudar: (e) => w.estados.push(e),
    recarregar: () => { w.recarregas++; },
    falhou: (e) => w.falhas.push(e),
    sucesso: () => { w.sucessos++; }, aviso: () => { w.avisos++; }, anunciar: (t) => w.falas.push(t),
  };
  return { w, d, ultimo: () => w.estados[w.estados.length - 1] };
}

describe("classificar: o ui.state do servidor vira uma ação (b > a > a0 > c)", () => {
  it.each([
    ["updated (velho por 48 h ou tentativa falha)", [con("a", "updated")], "atualizar"],
    ["partial", [con("a", "partial")], "atualizar"],
    ["error_recoverable", [con("a", "error_recoverable")], "atualizar"],
    ["no_accounts", [con("a", "no_accounts")], "atualizar"],
    ["updating com coleta vencida", [con("a", "updating", COLETA_VENCIDA)], "atualizar"],
    ["updating sem detalhe: já coletando", [con("a", "updating")], "acompanhar"],
    ["updating 'Ainda não sincronizou'", [con("a", "updating", "Ainda não sincronizou")], "acompanhar"],
    ["needs_user_action 'Reautorize o banco'", [con("a", "needs_user_action", "Reautorize o banco")], "reconectar"],
    ["needs_user_action sem detalhe", [con("a", "needs_user_action")], "reconectar"],
    ["needs_user_action de DISPOSITIVO", [con("a", "needs_user_action", AUTORIZE_NO_APP)], "ver"],
    ["item_missing", [con("a", "item_missing")], "ver"],
    ["paused", [con("a", "paused")], "ver"],
    ["removed", [con("a", "removed")], "ver"],
    ["estado que o app não conhece", [con("a", "estado_novo")], "ver"],
    ["sem conexão nenhuma", [], "ver"],
    ["b vence a", [con("a", "partial"), con("b", "needs_user_action", "Reautorize o banco")], "reconectar"],
    ["a vence a0 e c", [con("a", "updating"), con("b", "paused"), con("c", "partial")], "atualizar"],
    ["a0 vence c", [con("a", "paused"), con("b", "updating")], "acompanhar"],
  ] as const)("%s", (_, lista, tipo) => { expect(classificar([...lista], true).tipo).toBe(tipo); });
  it("needs_user_action SEM provider_item_id não tem como reautorizar: (c) com a instrução do servidor", () => {
    expect(classificar([con("x", "needs_user_action", "Reautorize o banco", { provider_item_id: null })], true)).toEqual({ tipo: "ver", texto: "Nubank: Reautorize o banco." });
  });
  it("reconectar leva o item e o nome do banco; sem limite no plano vira (c) com o detalhe do servidor", () => {
    const l = [con("it_1", "needs_user_action", "Reautorize o banco")];
    expect(classificar(l, true)).toEqual({ tipo: "reconectar", itemId: "it_1", banco: "Nubank" });
    expect(classificar(l, false)).toEqual({ tipo: "ver", texto: "Nubank: Reautorize o banco." });
  });
  it("(c) de dispositivo traz a instrução do servidor; vários bancos viram uma frase por banco; sem ponto duplicado", () => {
    expect(classificar([con("a", "needs_user_action", AUTORIZE_NO_APP)], true)).toEqual({ tipo: "ver", texto: "Nubank: Autorize o acesso no app do banco." });
    const dois = [con("a", "paused", "Plano pausado."), con("b", "item_missing", "Banco não encontrado", { institution_name: "Itaú" })];
    expect(classificar(dois, true)).toEqual({ tipo: "ver", texto: "Nubank: Plano pausado. Itaú: Banco não encontrado." });
  });
});

describe("atualizar (a): o POST", () => {
  it("tudo em dia: sucesso(), a recarga pedida uma vez e o estado volta a ocioso", async () => {
    const { w, d, ultimo } = mundo();
    await atualizar(d);
    expect(w.estados[0]).toEqual({ fase: "enviando" }); expect(w.falas).toEqual(["Atualizando os dados do banco"]);
    expect(ultimo()).toEqual(OCIOSO); expect(w.sucessos).toBe(1); expect(w.avisos).toBe(0); expect(w.recarregas).toBe(1); expect(w.leituras).toEqual([]);
  });
  it("rate_limited conta como em dia (acabou de atualizar)", async () => {
    const { w, d, ultimo } = mundo({ enviar: async () => refresh([item("it_1", "rate_limited")]) });
    await atualizar(d); expect(ultimo()).toEqual(OCIOSO); expect(w.sucessos).toBe(1);
  });
  it("item com problema: resultado com a instrução do servidor, aviso(), recarga, e NADA de acompanhar", async () => {
    const { w, d, ultimo } = mundo({ enviar: async () => refresh([item("it_1", "partial", false, "Cartão desatualizado desde 12/08")]) });
    await atualizar(d);
    expect(ultimo()).toEqual({ fase: "resultado", texto: "Nubank: Cartão desatualizado desde 12/08." }); expect(w.avisos).toBe(1); expect(w.sucessos).toBe(0); expect(w.recarregas).toBe(1); expect(w.leituras).toEqual([]);
  });
  it("sem laço: a rodada que termina com problema fica em 'resultado' (a tela não oferece o botão), não em ocioso", async () => {
    const { d, ultimo } = mundo({ enviar: async () => refresh([item("it_1", "partial", false, "Parte dos dados ainda não veio")]) });
    await atualizar(d); expect(ultimo()!.fase).toBe("resultado");
  });
  it.each([["rede", new TypeError("Network request failed")], ["5xx", new ErroDeApi(500, "x")], ["429", new ErroDeApi(429, "x")], ["404", new ErroDeApi(404, "x")]])("falha (%s): mensagem, aviso() e o botão volta (ocioso com falha), sem recarga", async (_, erro) => {
    const { w, d, ultimo } = mundo({ enviar: async () => { throw erro; } });
    await atualizar(d);
    expect(ultimo()).toEqual({ fase: "ocioso", falha: true }); expect(w.falas).toContain("Não conseguimos atualizar agora. Tente de novo."); expect(w.avisos).toBe(1); expect(w.recarregas).toBe(0);
  });
  it("401 (sessão): entrega ao p.falhou e fica em silêncio; 402 (portão cancela a operação): idem", async () => {
    for (const erro of [new SessaoExpirada(), new ErroDeApi(402, "x")]) {
      const { w, d, ultimo } = mundo({ enviar: async () => { throw erro; } });
      d.falhou = (e) => { w.falhas.push(e); if (e instanceof ErroDeApi && e.status === 402) w.vivo = false; }; // o p.falhou real cancela no portão
      await atualizar(d);
      expect(w.falhas).toEqual([erro]); expect(ultimo()).toEqual(OCIOSO); expect(w.avisos).toBe(0); expect(w.falas).toEqual(["Atualizando os dados do banco"]);
    }
  });
  it("operação cancelada no meio do POST (puxar para atualizar, troca de conta): ocioso, sem mensagem, sem recarga extra", async () => {
    const { w, d, ultimo } = mundo({ enviar: async () => { w.vivo = false; return refresh([item("it_1", "partial")]); } });
    await atualizar(d); expect(ultimo()).toEqual(OCIOSO); expect(w.avisos + w.sucessos + w.recarregas).toBe(0);
    const outra = mundo({ enviar: async () => { outra.w.vivo = false; throw Object.assign(new Error("Aborted"), { name: "AbortError" }); } });
    await atualizar(outra.d); expect(outra.ultimo()).toEqual(OCIOSO); expect(outra.w.leituras).toEqual([]); expect(outra.w.avisos).toBe(0);
  });
  it.each([["401", new SessaoExpirada()], ["402", new ErroDeApi(402, "x")]])("estouro do prazo do POST e a leitura de depois dá %s: vai para o p.falhou (nada de 'resultado' mascarando), em silêncio", async (_, erro) => {
    const { w, d, ultimo } = mundo({ enviar: async () => { throw Object.assign(new Error("Aborted"), { name: "AbortError" }); }, lista: () => erro });
    d.falhou = (e) => { w.falhas.push(e); if (e instanceof ErroDeApi && e.status === 402) w.vivo = false; }; // o p.falhou real cancela no portão
    await atualizar(d);
    expect(w.falhas).toEqual([erro]); expect(ultimo()).toEqual(OCIOSO); expect(w.avisos).toBe(0); expect(w.recarregas).toBe(0);
  });
  it("estouro do prazo do POST e a leitura de depois cai por rede: a falha aprovada e o botão volta", async () => {
    const { w, d, ultimo } = mundo({ enviar: async () => { throw Object.assign(new Error("Aborted"), { name: "AbortError" }); }, lista: () => new TypeError("rede") });
    await atualizar(d); expect(ultimo()).toEqual({ fase: "ocioso", falha: true }); expect(w.avisos).toBe(1);
  });
  it("estouro do prazo do POST (AbortError com a operação viva): lê a lista e acompanha quem está coletando", async () => {
    const { w, d, ultimo } = mundo({
      enviar: async () => { throw Object.assign(new Error("Aborted"), { name: "AbortError" }); },
      lista: (n) => n === 1 ? [con("it_1", "updating")] : n === 2 ? [con("it_1", "updating")] : [con("it_1", "updated", null, { last_sync_at: "2026-10-10T13:00:00Z" })],
    });
    await atualizar(d);
    expect(w.leituras).toEqual([0, 5_000, 15_000]); expect(ultimo()).toEqual(OCIOSO); expect(w.sucessos).toBe(1); expect(w.recarregas).toBe(1);
  });
});

describe("acompanhar: cadência 5/10/20/40/60 s, sequencial, teto de 5 min", () => {
  const SYNC_VELHO = "2026-10-10T12:00:00Z", SYNC_NOVO = "2026-10-10T13:00:00Z";
  const comColeta = refresh([item("it_1", "updated", true)], [con("it_1", "updated")]);
  it("still_updating: GETs em 5, 15, 35 e 75 s; no GET em que o last_sync_at muda, recarrega e volta a ocioso; nunca dois GETs ao mesmo tempo", async () => {
    const { w, d, ultimo } = mundo({ enviar: async () => comColeta, lista: (n) => [con("it_1", "updated", null, { last_sync_at: n < 4 ? SYNC_VELHO : SYNC_NOVO })] });
    await atualizar(d);
    expect(w.leituras).toEqual([5_000, 15_000, 35_000, 75_000]); expect(w.esperas).toEqual([5_000, 10_000, 20_000, 40_000]);
    expect(w.maxEmVoo).toBe(1); expect(ultimo()).toEqual(OCIOSO); expect(w.sucessos).toBe(1); expect(w.recarregas).toBe(1);
    expect(w.estados.map((e) => e.fase)).toEqual(["enviando", "acompanhando", "ocioso"]);
  });
  it("depois dos 40 s a cadência fica em 60 s", async () => {
    const { w, d } = mundo({ enviar: async () => comColeta, lista: (n) => [con("it_1", "updated", null, { last_sync_at: n < 7 ? SYNC_VELHO : SYNC_NOVO })] });
    await atualizar(d); expect(w.esperas).toEqual([5_000, 10_000, 20_000, 40_000, 60_000, 60_000, 60_000]);
  });
  it("teto de 5 min EXATOS: a última espera é cortada, a última leitura cai em 300 s e o texto é o 'demorando' (sem recarga)", async () => {
    const { w, d, ultimo } = mundo({ enviar: async () => comColeta, lista: () => [con("it_1", "updating")] });
    await atualizar(d);
    expect(ultimo()).toEqual({ fase: "resultado", texto: "O banco está demorando. Seus dados aparecem aqui quando terminar." });
    expect(w.leituras).toEqual([5_000, 15_000, 35_000, 75_000, 135_000, 195_000, 255_000, 300_000]); expect(w.esperas.at(-1)).toBe(45_000);
    expect(w.recarregas).toBe(0); expect(w.sucessos).toBe(0); expect(w.avisos).toBe(0);
  });
  it("nenhuma leitura deu certo até o teto (nunca confirmou nada): a falha aprovada com aviso(), não o 'demorando'", async () => {
    const { w, d, ultimo } = mundo({ enviar: async () => comColeta, lista: () => new TypeError("rede") });
    await atualizar(d);
    expect(ultimo()).toEqual({ fase: "resultado", texto: "Não conseguimos atualizar agora. Tente de novo." }); expect(w.leituras.at(-1)).toBe(300_000); expect(w.avisos).toBe(1); expect(w.falas.at(-1)).toBe("Não conseguimos atualizar agora. Tente de novo."); expect(w.recarregas).toBe(0);
  });
  it("uma leitura boa no meio já basta para o 'demorando' (o app soube que o banco segue coletando)", async () => {
    const { w, d, ultimo } = mundo({ enviar: async () => comColeta, lista: (n) => n === 3 ? [con("it_1", "updating")] : new TypeError("rede") });
    await atualizar(d); expect(ultimo()).toEqual({ fase: "resultado", texto: "O banco está demorando. Seus dados aparecem aqui quando terminar." }); expect(w.avisos).toBe(0);
  });
  it("GET 429 no acompanhamento segue a cadência (como 5xx e rede); a leitura seguinte conclui", async () => {
    const { w, d, ultimo } = mundo({ enviar: async () => comColeta, lista: (n) => n === 1 ? new ErroDeApi(429, "x") : [con("it_1", "updated", null, { last_sync_at: SYNC_NOVO })] });
    await atualizar(d); expect(w.leituras).toEqual([5_000, 15_000]); expect(w.falhas).toEqual([]); expect(ultimo()).toEqual(OCIOSO); expect(w.sucessos).toBe(1);
  });
  it("GET que falha por rede/5xx segue a cadência; a leitura seguinte conclui", async () => {
    const { w, d, ultimo } = mundo({ enviar: async () => comColeta, lista: (n) => n === 1 ? new TypeError("rede") : n === 2 ? new ErroDeApi(503, "x") : [con("it_1", "updated", null, { last_sync_at: SYNC_NOVO })] });
    await atualizar(d); expect(w.leituras).toEqual([5_000, 15_000, 35_000]); expect(ultimo()).toEqual(OCIOSO); expect(w.sucessos).toBe(1);
  });
  it("GET com 401: p.falhou e silêncio (nada de mensagem)", async () => {
    const { w, d, ultimo } = mundo({ enviar: async () => comColeta, lista: () => new SessaoExpirada() });
    await atualizar(d); expect(w.falhas).toHaveLength(1); expect(ultimo()).toEqual(OCIOSO); expect(w.leituras).toHaveLength(1); expect(w.avisos).toBe(0);
  });
  it("o item sai da coleta com problema: a instrução do servidor fica (resultado), aviso() e recarga", async () => {
    const { w, d, ultimo } = mundo({ enviar: async () => comColeta, lista: () => [con("it_1", "needs_user_action", "Reautorize o banco")] });
    await atualizar(d); expect(ultimo()).toEqual({ fase: "resultado", texto: "Nubank: Reautorize o banco." }); expect(w.avisos).toBe(1); expect(w.recarregas).toBe(1);
  });
  it("o item sumiu da lista: acompanhar para (nada a esperar) e recarrega", async () => {
    const { w, d, ultimo } = mundo({ enviar: async () => comColeta, lista: () => [] });
    await atualizar(d); expect(w.leituras).toHaveLength(1); expect(ultimo()).toEqual(OCIOSO); expect(w.recarregas).toBe(1);
  });
  it("operação cancelada durante a espera (segundo plano, puxar para atualizar): para sem ler e sem mensagem", async () => {
    const { w, d, ultimo } = mundo({ enviar: async () => comColeta });
    d.esperar = async () => { w.vivo = false; };
    await atualizar(d); expect(w.leituras).toEqual([]); expect(ultimo()).toEqual(OCIOSO); expect(w.avisos + w.recarregas + w.sucessos).toBe(0);
  });
  it("(a0) sem POST: acompanhar direto, parando no last_sync_at novo", async () => {
    const { w, d, ultimo } = mundo({ lista: (n) => [con("it_1", n < 2 ? "updating" : "updated", null, { last_sync_at: n < 2 ? SYNC_VELHO : SYNC_NOVO })] });
    await acompanhar(d, new Map([["it_1", SYNC_VELHO]]));
    expect(w.leituras).toEqual([5_000, 15_000]); expect(ultimo()).toEqual(OCIOSO); expect(w.sucessos).toBe(1);
  });
});
