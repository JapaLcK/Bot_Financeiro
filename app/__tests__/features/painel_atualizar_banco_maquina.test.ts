import { ErroDeApi, RequisicaoSuperada, SessaoExpirada } from "@/api/client";
import type { Conexao, Refresh } from "@/api/schemas/openFinance";
import { transitoria } from "@/features/openFinance/volta";
import { acompanhar, acompanharLista, atualizar, AUTORIZE_NO_APP, classificar, COLETA_VENCIDA, OCIOSO, type Deps, type EstadoAtualizar } from "@/features/painel/atualizarBanco";

// A máquina pura do "Atualizar agora", com relógio falso e rede injetada. A conversa pela rota está em `painel_atualizar_banco.test.tsx`.
const con = (id: string, state: string, detail: string | null = null, extra: Partial<Conexao> = {}): Conexao => ({
  id: 1, status: "ACTIVE", status_reason: null, last_sync_at: "2026-10-10T12:00:00Z", reconnected_at: null, provider_item_id: id, institution_name: "Nubank",
  ui: { state, label: state, detail }, ...extra,
});
const item = (id: string, state: string, still = false, detail: string | null = null, reason: string | null = null) => ({ item_id: id, institution: "Nubank", state, label: state, detail, reason, still_updating: still });
const refresh = (items: ReturnType<typeof item>[], connections: Conexao[] = [], ok?: boolean): Refresh => ({ connections, sync: { items, ...(ok === undefined ? {} : { ok }) } });

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
    falhou: (e) => w.falhas.push(e), transitoria, silencioso: (e) => e instanceof SessaoExpirada || e instanceof RequisicaoSuperada,
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
  it("updating SEM provider_item_id não tem o que acompanhar: (c), e NUNCA (a0) (o acompanhamento é por id; sem id a rodada concluía na hora e recarregava em laço)", () => {
    expect(classificar([con("x", "updating", null, { provider_item_id: null })], true)).toEqual({ tipo: "ver", texto: "Nubank: updating." });
    expect(classificar([con("x", "updating", null, { provider_item_id: "" })], true).tipo).toBe("ver");
    // com outro updating COM id, o (a0) vale pelo que tem id
    expect(classificar([con("x", "updating", null, { provider_item_id: null }), con("y", "updating")], true).tipo).toBe("acompanhar");
    // a coleta vencida pede o POST, que não depende de id
    expect(classificar([con("x", "updating", COLETA_VENCIDA, { provider_item_id: null })], true).tipo).toBe("atualizar");
  });
  it("toda lista que o classificar manda ACOMPANHAR acompanha de verdade: acompanharLista muda para 'acompanhando' e NÃO recarrega antes da 1ª espera", () => {
    const sem = { provider_item_id: null };
    const listas: Conexao[][] = [
      [con("a", "updating")], [con("a", "updating", "Ainda não sincronizou")], [con("a", "updating", null, sem)], [con("a", "updating", null, { provider_item_id: "" })],
      [con("a", "updating", null, sem), con("b", "updating")], [con("a", "updating", null, sem), con("b", "paused")], [con("a", "paused"), con("b", "updating")],
    ];
    let acompanhou = 0;
    for (const l of listas) {
      if (classificar(l, true).tipo !== "acompanhar") continue;
      const { w, d } = mundo();
      d.esperar = () => new Promise<void>(() => {}); // a 1ª espera nunca volta: só o que a máquina faz antes dela
      void acompanharLista(d, l);
      expect(w.estados).toEqual([{ fase: "acompanhando" }]); expect(w.recarregas).toBe(0); acompanhou++;
    }
    expect(acompanhou).toBe(4);
  });
  it("needs_user_action SEM provider_item_id não tem como reautorizar: (c) com o nome do banco", () => {
    expect(classificar([con("x", "needs_user_action", "Reautorize o banco", { provider_item_id: null })], true)).toEqual({ tipo: "ver", texto: "Erro em Nubank." });
  });
  it("reconectar leva o item e o nome do banco; sem limite no plano vira (c) com o nome do banco", () => {
    const l = [con("it_1", "needs_user_action", "Reautorize o banco")];
    expect(classificar(l, true)).toEqual({ tipo: "reconectar", itemId: "it_1", banco: "Nubank" });
    expect(classificar(l, false)).toEqual({ tipo: "ver", texto: "Erro em Nubank." });
  });
  it("(c) de dispositivo: texto curto só com o banco; vários bancos viram uma frase", () => {
    expect(classificar([con("a", "needs_user_action", AUTORIZE_NO_APP)], true)).toEqual({ tipo: "ver", texto: "Erro em Nubank." });
    const dois = [con("a", "paused", "Plano pausado."), con("b", "item_missing", "Banco não encontrado", { institution_name: "Itaú" })];
    expect(classificar(dois, true)).toEqual({ tipo: "ver", texto: "Erro em Nubank, Itaú." });
  });
  it("a frase é curta: mesmo banco duas vezes aparece uma, 3 bancos entram todos, nome ausente ou vazio vira 'seu banco'", () => {
    const c = (id: string, nome: string | null) => con(id, "paused", "x", { institution_name: nome as never });
    const texto = (l: ReturnType<typeof con>[]) => (classificar(l, true) as { texto: string }).texto;
    expect(texto([c("a", "Nubank"), c("b", "Nubank")])).toBe("Erro em Nubank.");
    expect(texto([c("a", "A"), c("b", "B"), c("c", "C")])).toBe("Erro em A, B, C.");
    expect(texto([c("a", ""), c("b", null)])).toBe("Erro em seu banco.");
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
  it("updating SEM coleta (motivo de falha, ex. read_failed na 1ª conexão): é problema, não sucesso; a frase curta do banco", async () => {
    const { w, d, ultimo } = mundo({ enviar: async () => refresh([item("it_1", "updating", false, null, "read_failed")]) });
    await atualizar(d);
    expect(ultimo()).toEqual({ fase: "resultado", texto: "Erro em Nubank." }); expect(w.sucessos).toBe(0); expect(w.avisos).toBe(1); expect(w.leituras).toEqual([]);
  });
  it.each([[null], [""], ["ok"], ["no_accounts"], ["OK"]])("D1: updating com motivo de coleta (%p) e still_updating:false ACOMPANHA (como o web), não é 'em dia'; conclui no last_sync_at novo", async (reason) => {
    const { w, d, ultimo } = mundo({ enviar: async () => refresh([item("it_1", "updating", false, null, reason)], [con("it_1", "updating")]), lista: (n) => [con("it_1", n < 2 ? "updating" : "updated", null, { last_sync_at: n < 2 ? "2026-10-10T12:00:00Z" : "2026-10-10T13:00:00Z" })] });
    await atualizar(d);
    expect(w.estados.map((e) => e.fase)).toEqual(["enviando", "acompanhando", "ocioso"]); expect(w.leituras).toEqual([5_000, 15_000]); expect(ultimo()).toEqual(OCIOSO); expect(w.sucessos).toBe(1);
  });
  it("D1b (a lição 3 não regride): updating com motivo de falha e still_updating:false segue sendo resultado, sem leitura", async () => {
    const { w, d, ultimo } = mundo({ enviar: async () => refresh([item("it_1", "updating", false, null, "read_failed")]) });
    await atualizar(d); expect(ultimo()!.fase).toBe("resultado"); expect(w.leituras).toEqual([]); expect(w.sucessos).toBe(0);
  });
  it("D2: items:[] com ok:false é falha (o botão volta, aviso() e a fala), não 'em dia' (o web: refreshVerdict)", async () => {
    const { w, d, ultimo } = mundo({ enviar: async () => refresh([], [], false) });
    await atualizar(d);
    expect(ultimo()).toEqual({ fase: "ocioso", falha: true }); expect(w.avisos).toBe(1); expect(w.sucessos).toBe(0); expect(w.recarregas).toBe(0); expect(w.falas).toEqual(["Atualizando os dados do banco", "Não conseguimos atualizar agora. Tente de novo."]);
  });
  it.each([[true], [undefined]])("D2b (controle positivo): items:[] com ok:%p segue sendo sucesso", async (ok) => {
    const { w, d, ultimo } = mundo({ enviar: async () => refresh([], [], ok) });
    await atualizar(d); expect(ultimo()).toEqual(OCIOSO); expect(w.sucessos).toBe(1); expect(w.avisos).toBe(0);
  });
  it("um banco ainda coletando NÃO esconde a falha de outro (erro antes do neutro, como o web): resultado na hora, sem acompanhar", async () => {
    const { w, d, ultimo } = mundo({ enviar: async () => refresh([item("it_1", "updating", true), item("it_2", "partial", false, "Parte dos dados ainda não veio")]) });
    await atualizar(d);
    expect(ultimo()).toEqual({ fase: "resultado", texto: "Erro em Nubank." }); expect(w.sucessos).toBe(0); expect(w.avisos).toBe(1); expect(w.leituras).toEqual([]);
  });
  it("o item que ainda coleta também é julgado pelo estado (still_updating é independente do state no servidor): partial coletando é problema; rate_limited coletando acompanha", async () => {
    const a = mundo({ enviar: async () => refresh([item("it_1", "partial", true, "Parte dos dados ainda não veio")]) }); await atualizar(a.d);
    expect(a.ultimo()).toEqual({ fase: "resultado", texto: "Erro em Nubank." }); expect(a.w.leituras).toEqual([]);
    const b = mundo({ enviar: async () => refresh([item("it_1", "rate_limited", true)]), lista: () => [con("it_1", "updated", null, { last_sync_at: "2026-10-10T13:00:00Z" })] }); await atualizar(b.d);
    expect(b.w.leituras).toHaveLength(1); expect(b.w.sucessos).toBe(1);
  });
  it("item com problema: resultado com o nome do banco, aviso(), recarga, e NADA de acompanhar", async () => {
    const { w, d, ultimo } = mundo({ enviar: async () => refresh([item("it_1", "partial", false, "Cartão desatualizado desde 12/08")]) });
    await atualizar(d);
    expect(ultimo()).toEqual({ fase: "resultado", texto: "Erro em Nubank." }); expect(w.avisos).toBe(1); expect(w.sucessos).toBe(0); expect(w.recarregas).toBe(1); expect(w.leituras).toEqual([]);
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
  it("o item sai da coleta com problema: o banco com erro fica (resultado), aviso() e recarga", async () => {
    const { w, d, ultimo } = mundo({ enviar: async () => comColeta, lista: () => [con("it_1", "needs_user_action", "Reautorize o banco")] });
    await atualizar(d); expect(ultimo()).toEqual({ fase: "resultado", texto: "Erro em Nubank." }); expect(w.avisos).toBe(1); expect(w.recarregas).toBe(1);
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
