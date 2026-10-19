import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { mesAtual, mesesAnteriores, nomeMes } from "@/features/painel/catalogo";
import { fetchFalso, prepararCaso, resposta, segurar } from "./auth_apoio";
import { appVai, desligarTrava, drenar, ligarTrava, liberar, pendentes } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
// Volta de uma rota EMPURRADA (Configurações, folha de meta) para a aba: o acesso é revalidado em segundo plano e a tela não vira o portão; a carga seguinte é silenciosa. A volta CURTA do fundo também é silenciosa (decisão do dono, reverte o #897: P3, B1, F1-F5); só a trava biométrica fecha o portão (F3).
jest.setTimeout(30_000);
type Seg = ReturnType<typeof segurar>;
const vistos = new Map<string, number>(), presas = new Map<string, { n: number; seg: Seg }>();
let contasTotal: string, contasStatus: number, onboardingCompleto: boolean, meStatus: number, corte: string | undefined, mesEsquisito: string | null;
/** Prende a PRÓXIMA chamada a `path` (a contagem desde o início do caso é instável: o boot também lê). */
const prender = (path: string) => { const seg = segurar(); presas.set(path, { n: (vistos.get(path) ?? 0) + 1, seg }); return seg; };
const caminho = (u: unknown) => new URL(String(u)).pathname;
const chamadas = (path: string) => fetchFalso.mock.calls.filter(([u]) => caminho(u) === path);
function servidor() {
  const base = async (url: string, req: RequestInit) => {
    const path = caminho(url), n = (vistos.get(path) ?? 0) + 1; vistos.set(path, n);
    const p = presas.get(path); if (p?.n === n) await p.seg.promessa;
    const bela = (req.headers as Record<string, string>).Authorization === "Bearer access-bela", mes = new URL(url).searchParams.get("mes");
    if (path === "/auth/me") return meStatus !== 200 ? resposta(meStatus, { error: { code: "subscription_required", message: "x" } }) : bela ? resposta(200, { user_id: 2, display_name: "Bela", app_access: true }) : resposta(200, { user_id: 1, display_name: "Ana", app_access: true, ...(corte ? { history_earliest_date: corte } : {}) });
    if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: onboardingCompleto, completed_at: onboardingCompleto ? "2026-10-05T12:00:00Z" : null });
    if (/^\/open-finance\/\d\/limite$/.test(path)) return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
    if (path === "/api/app/contas" && contasStatus !== 200) return resposta(contasStatus, { error: { code: contasStatus === 403 ? "pro_required" : "x", message: "x" } });
    if (path === "/api/app/contas") return resposta(200, { ...fixture[path], total: contasTotal });
    if (path === "/api/app/mes-detalhes" && mes === mesEsquisito) return resposta(200, { ...fixture[path], mes, categorias: [{ categoria: "Alimentação", valor: "555.55", quantidade: 1 }] });
    if (path === "/api/app/resumo-do-mes" && mes === mesEsquisito) return resposta(200, { ...fixture[path], mes, entrou: "1234.56", saiu: "1234.56" });
    return resposta(200, (fixture as Record<string, unknown>)[path.replace(/^\/(goals|cards|installments|analytics|insights)\/2\//, "/$1/1/")] ?? {});
  };
  // fetch real: abortar rejeita no cliente (o blur aborta as leituras em voo).
  fetchFalso.mockImplementation((u: string, r: RequestInit) => { const s = base(u, r), sg = r?.signal; return !sg ? s : new Promise((res, rej) => { const err = () => rej(Object.assign(new Error("Aborted"), { name: "AbortError" })); if (sg.aborted) err(); sg.addEventListener("abort", err); s.then(res, rej); }); });
}
beforeEach(async () => {
  prepararCaso(); desligarTrava(); vistos.clear(); presas.clear(); contasTotal = "8123.45"; contasStatus = 200; onboardingCompleto = true; meStatus = 200; corte = undefined; mesEsquisito = null;
  await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" }); servidor();
});
afterEach(() => jest.restoreAllMocks());
const ir = async (rota: string) => { await act(async () => { router.push(rota as never); await drenar(); }); await waitFor(() => expect(screen).toHavePathname(rota)); };
const voltar = async () => { await act(async () => { router.back(); await drenar(); }); };
const assentar = async () => { for (let i = 0; i < 10; i++) await act(drenar); };
const saldo = (re: RegExp) => screen.queryAllByLabelText(re).length > 0;
const refrescando = () => screen.getByTestId("tela").props.refreshControl.props.refreshing;
const abrirEm = async (url: string, pronto: () => unknown) => { renderRouter("./app", { initialUrl: url }); await waitFor(() => expect(pronto()).toBeTruthy()); await assentar(); };
const emResumo = () => abrirEm("/resumo", () => screen.getByTestId("painel-conta") && saldo(/8[.]?123/));
/** Nada de portão: sem "Conferindo seu acesso…", sem o botão e com a aba e a conta no lugar. */
const semPortao = () => { expect(screen.queryByText("Conferindo seu acesso…")).toBeNull(); expect(screen.queryByRole("button", { name: "Conferir acesso" })).toBeNull(); expect(screen.getByTestId("painel-conta")).toBeTruthy(); expect(screen.getByLabelText("Aba Gastos")).toBeTruthy(); };
const mudarParaMesAnterior = async () => {
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: nomeMes(mesAtual()).slice(0, 3) })); await drenar(); });
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: nomeMes(mesesAnteriores(mesAtual(), 2)[1]!) })); await drenar(); });
};

describe("CONSERTO: a volta de rota empurrada não troca a tela pelo portão", () => {
  it("A1: revalidação em voo: tela e dado ficam, sem RefreshControl; o dado novo entra quando chega", async () => {
    await emResumo(); await ir("/configuracoes");
    const me = prender("/auth/me"), contas = prender("/api/app/contas"); await voltar(); await waitFor(() => expect(screen).toHavePathname("/resumo")); await assentar();
    expect(chamadas("/auth/me").length).toBeGreaterThan(1); semPortao(); expect(screen.queryByLabelText("Carregando dados")).toBeNull(); expect(refrescando()).toBe(false);
    contasTotal = "1111.11"; me.soltar(); await assentar(); // a releitura de contas está presa: o valor velho fica
    expect(saldo(/8[.]?123/)).toBe(true); expect(screen.queryByLabelText("Carregando dados")).toBeNull(); expect(refrescando()).toBe(false); semPortao();
    contas.soltar(); await waitFor(() => expect(saldo(/1[.]?111/)).toBe(true)); await assentar(); expect(saldo(/8[.]?123/)).toBe(false); expect(refrescando()).toBe(false);
  });
  it("A2: o Extrato não remonta: a busca digitada continua lá", async () => {
    await abrirEm("/extrato", () => screen.getByLabelText("Buscar lançamento")); fireEvent.changeText(screen.getByLabelText("Buscar lançamento"), "mercado");
    await ir("/configuracoes"); const me = prender("/auth/me"); await voltar(); await assentar();
    expect(screen.getByLabelText("Buscar lançamento").props.value).toBe("mercado"); me.soltar(); await assentar(); expect(screen.getByLabelText("Buscar lançamento").props.value).toBe("mercado");
  });
  it("A3: a 1ª leitura de contas abortada pelo blur: ao voltar o card fica pronto, sem 'Carregando dados' preso", async () => {
    const presa = prender("/api/app/contas"); await abrirEm("/resumo", () => screen.getByTestId("painel-conta")); expect(screen.getAllByLabelText("Carregando dados").length).toBeGreaterThan(0);
    await ir("/configuracoes"); presa.soltar(); await voltar(); await waitFor(() => expect(saldo(/8[.]?123/)).toBe(true)); await assentar();
    expect(screen.queryByLabelText("Carregando dados")).toBeNull(); expect(refrescando()).toBe(false); semPortao();
  });
  it("A4: 503 na revalidação não derruba a tela, e o dado é relido mesmo assim", async () => {
    await emResumo(); await ir("/configuracoes"); const antes = chamadas("/api/app/contas").length, contas = prender("/api/app/contas");
    meStatus = 503; await voltar(); await assentar();
    semPortao(); expect(saldo(/8[.]?123/)).toBe(true); expect(screen.queryByLabelText("Carregando dados")).toBeNull(); expect(chamadas("/api/app/contas").length).toBeGreaterThan(antes); // a releitura presa não troca o saldo por skeleton
    contas.soltar(); await assentar(); expect(saldo(/8[.]?123/)).toBe(true); expect(screen.queryByLabelText("Carregando dados")).toBeNull();
  });
  it("A5: com leituras silenciosas em voo, trocar o mês não deixa o RefreshControl aceso depois das mensais", async () => {
    await emResumo(); await ir("/configuracoes"); const contas = prender("/api/app/contas"); await voltar(); await assentar();
    expect(refrescando()).toBe(false); const mes = mesesAnteriores(mesAtual(), 2)[1]!;
    await mudarParaMesAnterior(); await waitFor(() => expect(chamadas("/api/app/resumo-do-mes").some(([u]) => new URL(String(u)).searchParams.get("mes") === mes)).toBe(true)); await assentar();
    expect(refrescando()).toBe(false); contas.soltar(); await assentar(); expect(refrescando()).toBe(false);
  });
});

describe("CONTROLES POSITIVOS: o portão continua onde tem de continuar", () => {
  it("P1: onboarding deixou de estar completo: volta ao Início, sem o painel", async () => {
    await emResumo(); await ir("/configuracoes"); onboardingCompleto = false; await voltar(); await assentar();
    expect(screen.queryByTestId("painel-conta")).toBeNull(); expect(screen.queryByLabelText("Aba Gastos")).toBeNull();
  });
  it("P2: 402 na revalidação: sem o painel", async () => {
    await emResumo(); await ir("/configuracoes"); meStatus = 402; await voltar(); await assentar();
    expect(screen.queryByTestId("painel-conta")).toBeNull(); expect(saldo(/8[.]?123/)).toBe(false);
  });
  it("P3: a volta curta do segundo plano NÃO passa pelo portão: a tela fica enquanto o /auth/me está presa", async () => {
    await emResumo(); await appVai("background"); const me = prender("/auth/me"); await appVai("active"); await assentar();
    semPortao(); expect(saldo(/8[.]?123/)).toBe(true); expect(chamadas("/auth/me").length).toBeGreaterThan(1); me.soltar(); await assentar(); semPortao();
  });
  it("P4: corte de histórico novo na Configurações: o dado do mês velho não fica sob o mês novo, e as mensais pedem o novo", async () => {
    const velho = mesesAnteriores(mesAtual(), 3)[2]!, intermediario = mesesAnteriores(mesAtual(), 2)[1]!; mesEsquisito = velho;
    await emResumo(); await act(async () => { fireEvent.press(screen.getByRole("button", { name: nomeMes(mesAtual()).slice(0, 3) })); await drenar(); });
    await act(async () => { fireEvent.press(screen.getByRole("button", { name: nomeMes(velho) })); await drenar(); }); await waitFor(() => expect(saldo(/1[.]?234/)).toBe(true));
    await ir("/configuracoes"); corte = `${intermediario}-01`; const resumo = prender("/api/app/resumo-do-mes"); await voltar(); await assentar();
    expect(saldo(/1[.]?234/)).toBe(false); // o corte empurra o mês para `intermediario`: a leitura dele está presa
    expect(chamadas("/api/app/resumo-do-mes").some(([u]) => new URL(String(u)).searchParams.get("mes") === intermediario)).toBe(true); resumo.soltar(); await assentar(); expect(saldo(/1[.]?234/)).toBe(false);
  });
  it("P5: outra conta na volta: Bela, busca da Ana zerada, mensais com o token da Bela", async () => {
    await abrirEm("/extrato", () => screen.getByLabelText("Buscar lançamento")); fireEvent.changeText(screen.getByLabelText("Buscar lançamento"), "mercado");
    await ir("/configuracoes"); await guardarCredenciais({ access: "access-bela", refresh: "rt_bela" }); const antes = fetchFalso.mock.calls.length; await voltar(); await assentar();
    expect(screen.getByLabelText("Buscar lançamento").props.value).toBe(""); await act(async () => { router.navigate("/resumo"); await drenar(); }); await waitFor(() => expect(screen.getByText("Bom dia, Bela")).toBeTruthy());
    const mensais = fetchFalso.mock.calls.slice(antes).filter(([u]) => ["/api/app/resumo-do-mes", "/api/app/mes-detalhes"].includes(caminho(u)));
    expect(mensais.length).toBeGreaterThan(0); for (const [, r] of mensais) expect((r.headers as Record<string, string>).Authorization).toBe("Bearer access-bela");
  });
});

describe("CONSERTO: o fundo curto numa rota empurrada também é silencioso; chave trocada durante a validação não mostra o dado velho", () => {
  const pediu = (path: string, q: string, v: string) => chamadas(path).some(([u]) => new URL(String(u)).searchParams.get(q) === v);
  const fundoEVolta = async () => { await ir("/configuracoes"); await appVai("background"); await appVai("active"); const me = prender("/auth/me"); await voltar(); await assentar(); return me; };
  it("B1: fundo ESTANDO na rota empurrada: a volta não passa pelo portão; o saldo fica até o /auth/me responder", async () => {
    await emResumo(); const me = await fundoEVolta();
    semPortao(); expect(saldo(/8[.]?123/)).toBe(true);
    me.soltar(); await assentar(); semPortao(); expect(saldo(/8[.]?123/)).toBe(true);
  });
  it("B1b: onboarding revogado durante o fundo + /auth/me presa: depois da validação cai no Início, sem painel nem saldo", async () => {
    await emResumo(); onboardingCompleto = false; const me = await fundoEVolta();
    semPortao(); expect(saldo(/8[.]?123/)).toBe(true); // a tela velha fica até a validação responder (igual ao refoco de rota empurrada)
    me.soltar(); await assentar(); expect(screen.queryByTestId("painel-conta")).toBeNull(); expect(screen.queryByLabelText("Aba Gastos")).toBeNull(); expect(saldo(/8[.]?123/)).toBe(false);
  });
  it("B2: mês trocado com a validação presa: o número do mês velho não fica sob o chip do novo; solta, e a leitura do novo sai", async () => {
    const velho = mesesAnteriores(mesAtual(), 3)[2]!, novo = mesesAnteriores(mesAtual(), 2)[1]!; mesEsquisito = velho;
    await emResumo(); await act(async () => { fireEvent.press(screen.getByRole("button", { name: nomeMes(mesAtual()).slice(0, 3) })); await drenar(); });
    await act(async () => { fireEvent.press(screen.getByRole("button", { name: nomeMes(velho) })); await drenar(); }); await waitFor(() => expect(saldo(/1[.]?234/)).toBe(true));
    await ir("/configuracoes"); const me = prender("/auth/me"); await voltar(); await assentar();
    await act(async () => { fireEvent.press(screen.getByRole("button", { name: nomeMes(velho).slice(0, 3) })); await drenar(); });
    await act(async () => { fireEvent.press(screen.getByRole("button", { name: nomeMes(novo) })); await drenar(); }); await assentar();
    expect(saldo(/1[.]?234/)).toBe(false); expect(pediu("/api/app/resumo-do-mes", "mes", novo)).toBe(false); // nenhuma leitura antes de o acesso ser reconfirmado
    me.soltar(); await waitFor(() => expect(pediu("/api/app/resumo-do-mes", "mes", novo)).toBe(true)); await assentar(); expect(saldo(/1[.]?234/)).toBe(false); expect(saldo(/7[.]?200/)).toBe(true);
  });
  it("B3: pull-to-refresh durante a validação presa: depois dela a leitura acende o RefreshControl (não é silenciosa)", async () => {
    await emResumo(); await ir("/configuracoes"); const me = prender("/auth/me"); await voltar(); await assentar();
    act(() => { screen.getByTestId("tela").props.refreshControl.props.onRefresh(); }); await assentar();
    const resumo = prender("/api/app/resumo-do-mes"); me.soltar(); await waitFor(() => expect(refrescando()).toBe(true)); resumo.soltar(); await waitFor(() => expect(refrescando()).toBe(false));
  });
  it("B2b: dias da previsão trocados com a validação presa: o card não mostra a previsão de 30 dias sob o chip de 60; solta, e ?dias=60 sai", async () => {
    await emResumo(); const chip = () => screen.queryByRole("button", { name: "60 dias" }); await waitFor(() => expect(chip()).toBeTruthy());
    await ir("/configuracoes"); const me = prender("/auth/me"); await voltar(); await assentar();
    await act(async () => { fireEvent.press(chip()!); await drenar(); }); await assentar();
    expect(saldo(/10[.]?459/)).toBe(false); expect(pediu("/api/app/previsao", "dias", "60")).toBe(false);
    me.soltar(); await waitFor(() => expect(pediu("/api/app/previsao", "dias", "60")).toBe(true));
  });
});

describe("CONSERTO: a releitura silenciosa não segura dado revogado nem de outra conta", () => {
  const refoco = async () => { await emResumo(); await ir("/configuracoes"); };
  it("C1: 403 pro_required de recurso no refoco: o saldo velho SOME e aparece a mensagem de negado", async () => {
    await refoco(); contasStatus = 403; await voltar(); await assentar();
    expect(saldo(/8[.]?123/)).toBe(false); expect(screen.getAllByText(/não está disponível/).length).toBeGreaterThan(0);
  });
  it("C1b (controle positivo): 500 no mesmo refoco mantém o saldo velho, sem erro", async () => {
    await refoco(); contasStatus = 500; await voltar(); await assentar();
    expect(saldo(/8[.]?123/)).toBe(true); expect(screen.queryAllByText(/não está disponível|Não conseguimos carregar/).length).toBe(0); semPortao();
  });
  it("C2: credencial trocada + /auth/me 503 no refoco: portão com erro, sem a Ana na tela e sem ler dado com o token da Bela", async () => {
    await refoco(); await guardarCredenciais({ access: "access-bela", refresh: "rt_bela" }); const antes = fetchFalso.mock.calls.length; meStatus = 503; await voltar(); await assentar();
    expect(saldo(/8[.]?123/)).toBe(false); expect(screen.queryByTestId("painel-conta")).toBeNull(); expect(screen.getByRole("button", { name: "Conferir acesso" })).toBeTruthy();
    expect(fetchFalso.mock.calls.slice(antes).filter(([u, r]) => caminho(u).startsWith("/api/app/") && (r.headers as Record<string, string>).Authorization === "Bearer access-bela")).toEqual([]);
  });
  it("Z14: a conta troca durante a revalidação presa: portão, sem o saldo da Ana", async () => {
    await refoco(); const me = prender("/auth/me"); await voltar(); await assentar();
    await guardarCredenciais({ access: "access-bela", refresh: "rt_bela" }); me.soltar(); await assentar();
    expect(saldo(/8[.]?123/)).toBe(false); expect(screen.queryByTestId("painel-conta")).toBeNull();
  });
});

describe("CONSERTO: o pull-to-refresh acende o spinner só quando é pull", () => {
  const pull = async () => { act(() => { screen.getByTestId("tela").props.refreshControl.props.onRefresh(); }); await assentar(); };
  it("S11: depois de um refoco silencioso completo, puxar para atualizar acende o spinner e depois apaga", async () => {
    await emResumo(); await ir("/configuracoes"); await voltar(); await assentar(); expect(refrescando()).toBe(false);
    const resumo = prender("/api/app/resumo-do-mes"); await pull(); expect(refrescando()).toBe(true); resumo.soltar(); await waitFor(() => expect(refrescando()).toBe(false));
  });
  it("S9: um pull comum (fora de validação) não vaza: o refoco seguinte continua silencioso", async () => {
    await emResumo(); await pull(); await assentar(); await ir("/configuracoes"); const contas = prender("/api/app/contas"); await voltar(); await assentar();
    expect(refrescando()).toBe(false); expect(saldo(/8[.]?123/)).toBe(true); expect(screen.queryByLabelText("Carregando dados")).toBeNull(); contas.soltar(); await assentar();
  });
  it("D1: pull em voo, blur abortando e volta com TODAS as leituras silenciosas presas: o spinner antigo apaga já", async () => {
    await emResumo(); const resumo = prender("/api/app/resumo-do-mes"); await pull(); expect(refrescando()).toBe(true);
    await ir("/configuracoes"); const todas = segurar(), impl = fetchFalso.getMockImplementation()!; // só o acesso passa; toda leitura de recurso espera
    fetchFalso.mockImplementation((u: string, r: RequestInit) => /^\/(api\/app|cards|installments|analytics|insights)\//.test(caminho(u)) ? todas.promessa.then(() => impl(u, r)) : impl(u, r));
    await voltar(); await assentar(); expect(refrescando()).toBe(false);
    resumo.soltar(); todas.soltar(); await assentar(); expect(refrescando()).toBe(false);
  });
  it("S7: pull durante a validação que falha com 503: depois dela o spinner acende", async () => {
    await emResumo(); await ir("/configuracoes"); const me = prender("/auth/me"); await voltar(); await assentar(); await pull();
    meStatus = 503; const resumo = prender("/api/app/resumo-do-mes"); me.soltar(); await waitFor(() => expect(refrescando()).toBe(true)); resumo.soltar(); await waitFor(() => expect(refrescando()).toBe(false));
  });
});

describe("CONSERTO: trocar o mês durante a validação presa também tira o detalhe do mês velho (aba Gastos)", () => {
  it("B2c: o total de categorias do mês velho não fica sob o chip do novo; solta, e o do novo entra", async () => {
    const velho = mesesAnteriores(mesAtual(), 3)[2]!, novo = mesesAnteriores(mesAtual(), 2)[1]!; mesEsquisito = velho;
    await abrirEm("/gastos", () => screen.getByTestId("tela")); await act(async () => { fireEvent.press(screen.getByRole("button", { name: nomeMes(mesAtual()).slice(0, 3) })); await drenar(); });
    await act(async () => { fireEvent.press(screen.getByRole("button", { name: nomeMes(velho) })); await drenar(); }); await waitFor(() => expect(saldo(/555/)).toBe(true));
    await ir("/configuracoes"); const me = prender("/auth/me"); await voltar(); await assentar();
    await act(async () => { fireEvent.press(screen.getByRole("button", { name: nomeMes(velho).slice(0, 3) })); await drenar(); });
    await act(async () => { fireEvent.press(screen.getByRole("button", { name: nomeMes(novo) })); await drenar(); }); await assentar();
    expect(saldo(/555/)).toBe(false); me.soltar(); await assentar(); expect(saldo(/555/)).toBe(false); expect(saldo(/980/)).toBe(true);
  });
});

describe("CONSERTO (decisão do dono): fundo curto é silencioso; só a trava biométrica mostra o portão", () => {
  it("F1: fundo→volta rápida com /auth/me presa: tela, saldo e conta ficam (inclusive no fundo), sem RefreshControl; solta, e a recarga silenciosa traz o valor novo", async () => {
    await emResumo(); await appVai("inactive"); await appVai("background"); expect(saldo(/8[.]?123/)).toBe(true); expect(screen.getByLabelText("Aba Gastos")).toBeTruthy(); // a tampa nativa cobre o app switcher; a árvore não desmonta
    const me = prender("/auth/me"); await appVai("active"); await assentar();
    semPortao(); expect(saldo(/8[.]?123/)).toBe(true); expect(refrescando()).toBe(false); expect(screen.queryByLabelText("Carregando dados")).toBeNull();
    contasTotal = "1111.11"; me.soltar(); await waitFor(() => expect(saldo(/1[.]?111/)).toBe(true)); await assentar(); expect(saldo(/8[.]?123/)).toBe(false); semPortao(); expect(refrescando()).toBe(false);
  });
  it("F2: fundo curto com acesso revogado (onboarding incompleto): depois da validação cai no Início, sem painel", async () => {
    await emResumo(); await appVai("background"); onboardingCompleto = false; const me = prender("/auth/me"); await appVai("active"); await assentar();
    semPortao(); me.soltar(); await assentar();
    expect(screen.queryByTestId("painel-conta")).toBeNull(); expect(screen.queryByLabelText("Aba Gastos")).toBeNull(); expect(saldo(/8[.]?123/)).toBe(false);
  });
  it("F2b: fundo curto com 402 em /auth/me: sem painel nem saldo", async () => {
    await emResumo(); await appVai("background"); meStatus = 402; const me = prender("/auth/me"); await appVai("active"); await assentar();
    semPortao(); me.soltar(); await assentar();
    expect(screen.queryByTestId("painel-conta")).toBeNull(); expect(screen.queryByLabelText("Aba Gastos")).toBeNull(); expect(saldo(/8[.]?123/)).toBe(false);
  });
  it("F3: fundo LONGO (>60 s) com trava ligada: a trava cobre; liberada, aparece 'Conferindo seu acesso…' até a validação, e o saldo não aparece antes", async () => {
    pendentes.length = 0; ligarTrava(); renderRouter("./app", { initialUrl: "/resumo" });
    await waitFor(() => expect(pendentes.length).toBe(1)); await liberar(); await waitFor(() => expect(saldo(/8[.]?123/)).toBe(true)); await assentar();
    await appVai("inactive"); await appVai("background"); jest.setSystemTime(Date.now() + 61_000); await appVai("active"); await assentar();
    expect(pendentes.length).toBe(1); expect(saldo(/8[.]?123/)).toBe(false); expect(screen.queryByText("Conferindo seu acesso…")).toBeNull(); // travado: nem saldo nem portão por baixo
    const me = prender("/auth/me"); await liberar(); await assentar();
    expect(screen.getByText("Conferindo seu acesso…")).toBeTruthy(); expect(saldo(/8[.]?123/)).toBe(false); expect(screen.queryByTestId("painel-conta")).toBeNull();
    me.soltar(); await waitFor(() => expect(saldo(/8[.]?123/)).toBe(true)); semPortao();
  });
  it("F6: trava LIGADA, 59 s fora: sem portão, sem pedir a trava, e o saldo fica", async () => {
    pendentes.length = 0; ligarTrava(); renderRouter("./app", { initialUrl: "/resumo" });
    await waitFor(() => expect(pendentes.length).toBe(1)); await liberar(); await waitFor(() => expect(saldo(/8[.]?123/)).toBe(true)); await assentar();
    await appVai("inactive"); await appVai("background"); jest.setSystemTime(Date.now() + 59_000); const me = prender("/auth/me"); await appVai("active"); await assentar();
    expect(pendentes.length).toBe(0); semPortao(); expect(saldo(/8[.]?123/)).toBe(true); me.soltar(); await assentar(); semPortao();
  });
  it("F4: fundo curto com credencial trocada + /auth/me 503: portão com erro, sem a Ana na tela e sem ler dado com o token da Bela", async () => {
    await emResumo(); await appVai("background"); await guardarCredenciais({ access: "access-bela", refresh: "rt_bela" }); const antes = fetchFalso.mock.calls.length; meStatus = 503; await appVai("active"); await assentar();
    expect(saldo(/8[.]?123/)).toBe(false); expect(screen.queryByTestId("painel-conta")).toBeNull(); expect(screen.getByRole("button", { name: "Conferir acesso" })).toBeTruthy();
    expect(fetchFalso.mock.calls.slice(antes).filter(([u, r]) => caminho(u).startsWith("/api/app/") && (r.headers as Record<string, string>).Authorization === "Bearer access-bela")).toEqual([]);
  });
  it("F5: leitura em voo abortada pelo fundo: ao voltar, nenhum card fica preso em 'Carregando dados' (Resumo e Gastos)", async () => {
    const presa = prender("/api/app/contas"); await abrirEm("/resumo", () => screen.getByTestId("painel-conta")); expect(screen.getAllByLabelText("Carregando dados").length).toBeGreaterThan(0);
    await appVai("inactive"); await appVai("background"); presa.soltar(); await appVai("active"); await waitFor(() => expect(saldo(/8[.]?123/)).toBe(true)); await assentar();
    expect(screen.queryByLabelText("Carregando dados")).toBeNull(); expect(refrescando()).toBe(false); semPortao();
    await act(async () => { router.navigate("/gastos"); await drenar(); }); await assentar(); expect(screen.queryByLabelText("Carregando dados")).toBeNull();
  });
});
