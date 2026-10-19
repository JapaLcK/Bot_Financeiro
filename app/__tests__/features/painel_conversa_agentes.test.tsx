import { StyleSheet } from "react-native";
import { act, fireEvent, screen, waitFor, within } from "expo-router/testing-library";
import * as estado from "@/features/painel/conversaEstado";
import { useEstadoPainel } from "@/features/painel/provider";
import { AGENTES } from "@/features/painel/agentes";
import { claro, escuro } from "@/ui/tokens";
import { fetchFalso, prepararCaso, resposta, segurar } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import { abrir, apertar, bela, campo, caminho, digitar, enviar, ir, posts, reiniciar, respostaFluxo, srv, trocarParaBela, voltar, type Segurar } from "./conversa_apoio";
// O `@Nome` do chat da aba Piggy: /agents/{uid}/… com o fetch falso (o servidor real e o modelo não entram aqui).
jest.setTimeout(30_000);
const cat = (uid = 1, over: object = {}, status: string | null = "active") => ({ catalog: AGENTES.map((a) => ({ kind: a.kind, nome: a.nome, disponivel: true, status, energy_cost: 2 })), can_chat: true, can_activate: true, energy_enabled: true, energy_budget: 10, energy_used: uid === 1 ? 2 : 3, ...over });
type Ag = { catalogo: (uid: string) => object; getStatus: number; segurarGet: Segurar | null; chat: (n: number, corpo: { message: string; context: string | null }, req: RequestInit) => Response | Promise<Response>; ativar: (n: number) => Response | Promise<Response>; nChat: number; nAtivar: number };
const ag: Ag = { catalogo: (uid) => cat(Number(uid)), getStatus: 200, segurarGet: null, chat: () => resposta(200, { reply: "Resposta", context: null }), ativar: () => resposta(200, { ok: true }), nChat: 0, nAtivar: 0 };
const dos = () => fetchFalso.mock.calls.filter(([u]: [string]) => caminho(u).startsWith("/agents"));
const getsDeAgentes = () => dos().filter(([u]) => /^\/agents\/\d+$/.test(caminho(u)));
const chats = () => dos().filter(([u]) => /\/chat$/.test(caminho(u)));
const corpoDe = (c: [string, RequestInit]) => JSON.parse(String(c[1].body));
/** Rotas /agents por cima do `servidor()` de `conversa_apoio` (o resto passa direto). Abortar rejeita no cliente, como o fetch real. */
function ligar() {
  Object.assign(ag, { catalogo: (uid: string) => cat(Number(uid)), getStatus: 200, segurarGet: null, chat: () => resposta(200, { reply: "Resposta", context: null }), ativar: () => resposta(200, { ok: true }), nChat: 0, nAtivar: 0 });
  const base = fetchFalso.getMockImplementation()!;
  fetchFalso.mockImplementation((url: string, req: RequestInit) => {
    const m = /^\/agents\/(\d+)(?:\/(\w+)\/(chat|activate))?$/.exec(caminho(url));
    if (!m) return base(url, req);
    const r = (async () => {
      if (!m[2]) { if (ag.segurarGet) await ag.segurarGet.promessa; return ag.getStatus === 200 ? resposta(200, ag.catalogo(m[1]!)) : resposta(ag.getStatus, { detail: "x" }); }
      return m[3] === "chat" ? ag.chat(++ag.nChat, JSON.parse(String(req.body)), req) : ag.ativar(++ag.nAtivar);
    })();
    const sg = req?.signal; return !sg ? r : new Promise((res, rej) => { const err = () => rej(Object.assign(new Error("Aborted"), { name: "AbortError" })); if (sg.aborted) err(); sg.addEventListener("abort", err); r.then(res, rej); });
  });
}
beforeEach(async () => { prepararCaso(); desligarTrava(); await reiniciar(); ligar(); });
afterEach(() => { jest.restoreAllMocks(); });
const resp = (reply: string, extra: object = {}) => resposta(200, { reply, context: null, ...extra });
const erro = (status: number, detail: unknown) => resposta(status, { detail });
const toque = async (nome: string | RegExp) => apertar(nome);
const cabecalho = (nome: string, etiqueta: string) => within(screen.getByTestId("fala-agente")).getByLabelText(`${nome}, ${etiqueta.toLowerCase()}`);

it("rode a conversa: @Xerife vai ao agente (sem stream), com menção em brandInk; depois o Piggy; o 2º envio ao agente leva o context da 1ª resposta", async () => {
  ag.chat = (n) => resp(n === 1 ? "Nada fora do padrão." : "Segue igual.", { context: n === 1 ? "ctx1" : "ctx2" });
  await abrir(); await enviar("@Xerife quanto gastei?");
  await waitFor(() => expect(screen.getByText("Nada fora do padrão.")).toBeTruthy());
  expect(chats()).toHaveLength(1); expect(caminho(chats()[0]![0])).toBe("/agents/1/xerife/chat"); expect(corpoDe(chats()[0]!)).toEqual({ message: "quanto gastei?", context: null });
  expect(posts()).toHaveLength(0); cabecalho("Xerife", "AGENTE"); expect(campo().props.value).toBe("");
  expect([claro.brandInk, escuro.brandInk]).toContain(StyleSheet.flatten(screen.getByText("@Xerife").props.style).color);
  await enviar("oi"); await waitFor(() => expect(screen.getByText("Resposta do Piggy")).toBeTruthy());
  expect(posts()).toHaveLength(1); expect(within(screen.getByTestId("fala-piggy")).getByText("Piggy")).toBeTruthy(); expect(chats()).toHaveLength(1);
  await enviar("@Xerife e agora?"); await waitFor(() => expect(screen.getByText("Segue igual.")).toBeTruthy());
  expect(corpoDe(chats()[1]!)).toEqual({ message: "e agora?", context: "ctx1" });
});

it("lista do @: '@' mostra os grupos, '@rep' só o Repórter, tocar no item deixa '@Repórter ' no campo", async () => {
  ag.catalogo = (u) => cat(Number(u), {}, null); await abrir(); await digitar("@");
  await waitFor(() => expect(screen.getByText("Ativar para conversar")).toBeTruthy());
  for (const a of AGENTES) expect(screen.getByRole("button", { name: `${a.nome}, ${a.tema}` })).toBeTruthy();
  await digitar("@rep"); await waitFor(() => expect(screen.queryByRole("button", { name: /^Xerife/ })).toBeNull());
  await toque("Repórter, Resumo do mês"); expect(campo().props.value).toBe("@Repórter ");
});
it("controle da borda: 'x@gmail.com' não abre a lista e nada toca /agents; texto sem '@' enviado também não", async () => {
  await abrir(); await digitar("x@gmail.com"); await act(drenar);
  expect(screen.queryByText("Agentes ativos")).toBeNull(); expect(dos()).toHaveLength(0);
  await apertar("Enviar mensagem"); await waitFor(() => expect(screen.getByText("Resposta do Piggy")).toBeTruthy()); expect(dos()).toHaveLength(0); expect(posts()).toHaveLength(1);
});
it("catálogo ainda carregando: o envio ESPERA e vai ao agente (e o 2º toque não duplica)", async () => {
  const g = segurar(); ag.segurarGet = g; await abrir(); await digitar("@Xerife oi");
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Enviar mensagem" })); fireEvent.press(screen.getByRole("button", { name: "Enviar mensagem" })); await drenar(); });
  expect(chats()).toHaveLength(0); expect(posts()).toHaveLength(0);
  await act(async () => { g.soltar(); await drenar(); }); await waitFor(() => expect(chats()).toHaveLength(1)); expect(posts()).toHaveLength(0);
});
it("404 do beta: sem lista, e a mensagem com '@' vai ao Piggy", async () => {
  ag.getStatus = 404; await abrir(); await digitar("@Xerife oi"); await act(drenar);
  expect(screen.queryByText("Agentes ativos")).toBeNull(); expect(screen.queryByRole("button", { name: /^Xerife/ })).toBeNull();
  await apertar("Enviar mensagem"); await waitFor(() => expect(posts()).toHaveLength(1)); expect(chats()).toHaveLength(0);
});

describe("estados do agente", () => {
  it("inativo: etiqueta, custo e UMA ativação com dois toques; depois vem o chat", async () => {
    const g = segurar(); ag.catalogo = (u) => cat(Number(u), {}, null); ag.ativar = async () => { await g.promessa; return resposta(200, { ok: true }); }; ag.chat = () => resp("Ativado e respondendo.");
    await abrir(); await enviar("@Xerife oi"); await waitFor(() => cabecalho("Xerife", "NÃO ATIVO")); expect(screen.getByText("Ativar o Xerife usa ⚡2 de energia. Você tem 8 livre.")).toBeTruthy();
    await act(async () => { const b = screen.getByRole("button", { name: "Ativar e responder" }); fireEvent.press(b); fireEvent.press(b); await drenar(); });
    expect(ag.nAtivar).toBe(1); await act(async () => { g.soltar(); await drenar(); });
    await waitFor(() => expect(screen.getByText("Ativado e respondendo.")).toBeTruthy()); expect(ag.nAtivar).toBe(1); expect(chats()).toHaveLength(1);
  });
  it("sem energia: etiqueta e texto do estado, sem botão de planos, sem chamada ao chat", async () => {
    ag.catalogo = (u) => cat(Number(u), { energy_used: 10 }, null); await abrir(); await enviar("@Xerife oi");
    await waitFor(() => cabecalho("Xerife", "SEM ENERGIA")); expect(screen.getByText("O Xerife usa ⚡2 de energia e você tem 0 livre.")).toBeTruthy();
    expect(chats()).toHaveLength(0); expect(screen.queryByRole("button", { name: /planos/i })).toBeNull();
  });
  it("sem plano: texto do PigBank+", async () => {
    ag.catalogo = (u) => cat(Number(u), { can_chat: false }); await abrir(); await enviar("@Xerife oi");
    await waitFor(() => expect(screen.getByText("Conversar com o Xerife faz parte do PigBank+.")).toBeTruthy()); expect(screen.queryByRole("button", { name: /planos/i })).toBeNull();
  });
  it("redirect: 'Perguntar ao Carteiro' cria um turno para o carteiro; desligado com o Piggy em voo", async () => {
    ag.chat = (n) => n === 1 ? resp("Isso é com o Carteiro.", { redirects: [{ kind: "carteiro", name: "Carteiro", question: "Que contas vencem?", access: "ready" }, { kind: "x", name: "X", question: "q", access: "ready" }] }) : resp("Duas contas.");
    await abrir(); await enviar("@Xerife contas?"); await waitFor(() => screen.getByRole("button", { name: "Perguntar ao Carteiro" })); expect(screen.queryByRole("button", { name: /Perguntar ao X$/ })).toBeNull();
    const f = respostaFluxo(); srv.chat = () => f; await enviar("oi"); // Piggy em voo
    expect(screen.getByRole("button", { name: "Perguntar ao Carteiro" }).props.accessibilityState.disabled).toBe(true);
    await act(async () => { f.empurrar({ tipo: "fim", reply: "ok", usage: { used: 3, limit: 100 }, message_id: 3, chips: [] }); f.fechar(); await drenar(); });
    await toque("Perguntar ao Carteiro"); await waitFor(() => expect(screen.getByText("Duas contas.")).toBeTruthy());
    expect(caminho(chats()[1]![0])).toBe("/agents/1/carteiro/chat"); expect(corpoDe(chats()[1]!).message).toBe("Que contas vencem?");
  });
});

describe("erros", () => {
  const falha = async (r: () => Response | Promise<Response>) => { ag.chat = r; await abrir(); await enviar("@Xerife oi"); };
  it("422 retryable:true: 'Tentar de novo' reenvia a mesma mensagem", async () => {
    let n = 0; await falha(() => ++n === 1 ? erro(422, { error: "answer_rejected", message: "A resposta não passou na conferência.", retryable: true }) : resp("Agora foi."));
    await waitFor(() => expect(screen.getByText("A resposta não passou na conferência.")).toBeTruthy()); await toque("Tentar de novo");
    await waitFor(() => expect(screen.getByText("Agora foi.")).toBeTruthy()); expect(corpoDe(chats()[1]!)).toEqual({ message: "oi", context: null });
  });
  it("503 quota_unavailable retryable:false: o texto do servidor (formato ChatError), sem o botão", async () => {
    await falha(() => erro(503, { error: "quota_unavailable", message: "Não consegui confirmar sua cota agora.", retryable: false }));
    await waitFor(() => expect(screen.getByText("Não consegui confirmar sua cota agora.")).toBeTruthy()); expect(screen.queryByRole("button", { name: "Tentar de novo" })).toBeNull();
  });
  it("500 com detail em texto cru: texto genérico (nunca o do servidor) e sem o botão", async () => {
    await falha(() => erro(500, "psycopg.OperationalError"));
    await waitFor(() => expect(screen.getByText("Não foi possível obter a resposta agora. Tente novamente em instantes.")).toBeTruthy()); expect(screen.queryByText(/psycopg/)).toBeNull(); expect(screen.queryByRole("button", { name: "Tentar de novo" })).toBeNull();
  });
  it("503 model_busy retryable:true: com o botão", async () => {
    await falha(() => erro(503, { error: "model_busy", retryable: true })); await waitFor(() => screen.getByRole("button", { name: "Tentar de novo" }));
  });
  it("falha de rede (TypeError): com o botão", async () => {
    await falha(() => { throw new TypeError("Network request failed"); }); await waitFor(() => screen.getByRole("button", { name: "Tentar de novo" }));
  });
  it("429: texto próprio, sem o botão", async () => {
    await falha(() => erro(429, "Too many")); await waitFor(() => expect(screen.getByText("Muitas mensagens em pouco tempo. Aguarde um instante antes de tentar novamente.")).toBeTruthy()); expect(screen.queryByRole("button", { name: "Tentar de novo" })).toBeNull();
  });
  it("400 invalid_context: o próximo envio sai com context:null", async () => {
    let n = 0; ag.chat = () => ++n === 1 ? resp("Primeira.", { context: "velho" }) : n === 2 ? erro(400, { error: "invalid_context" }) : resp("Terceira.");
    await abrir(); await enviar("@Xerife um"); await waitFor(() => screen.getByText("Primeira.")); await enviar("@Xerife dois"); await waitFor(() => expect(screen.getAllByText(/Não foi possível/).length).toBe(1));
    expect(corpoDe(chats()[1]!).context).toBe("velho"); await enviar("@Xerife tres"); await waitFor(() => screen.getByText("Terceira.")); expect(corpoDe(chats()[2]!).context).toBeNull();
  });
  it("403 com detail em texto (sessão/CSRF): texto de sessão, sem o botão; password_required: texto de senha", async () => {
    await falha(() => erro(403, "Acesso negado para este usuário")); await waitFor(() => expect(screen.getByText("Sua sessão terminou. Entre de novo.")).toBeTruthy()); expect(screen.queryByRole("button", { name: "Tentar de novo" })).toBeNull();
    ag.chat = () => erro(403, { error: "password_required" }); await enviar("@Xerife de novo");
    await waitFor(() => expect(screen.getByText("Crie a senha da sua conta pelo link que enviamos por e-mail para conversar com os agentes.")).toBeTruthy());
  });
});

it("isolamento: Ana pergunta ao Xerife com o POST segurado e a Bela entra; nada da Ana (turno, cota) em render com uid 2, o sinal da Ana aborta e o 1º envio da Bela sai sem o context da Ana", async () => {
  const g = segurar(); ag.chat = async (n, _c, req) => { if (n === 1) return resp("Primeira da Ana", { context: "ctx-da-ana" }); if (n === 2) { await g.promessa; return resp("Resposta da Ana", { context: "ctx-tardio", usage: { used: 90, limit: 100 } }); } expect(bela(req)).toBe(true); return resp("Resposta da Bela"); };
  const real = estado.useConversa, vistos: { uid: unknown; c: ReturnType<typeof real> }[] = [];
  jest.spyOn(estado, "useConversa").mockImplementation(() => { const c = real(); vistos.push({ uid: useEstadoPainel().usuario?.user_id, c }); return c; });
  await abrir(); await enviar("@Xerife primeira"); await waitFor(() => screen.getByText("Primeira da Ana")); await enviar("@Xerife pergunta da Ana"); await waitFor(() => expect(chats()).toHaveLength(2));
  const post = chats()[1]!;
  await trocarParaBela(); await waitFor(() => expect(vistos.some((v) => v.uid === 2)).toBe(true)); expect(post[1].signal.aborted).toBe(true);
  await act(async () => { g.soltar(); await drenar(); await drenar(); });
  for (const { c } of vistos.filter((v) => v.uid === 2)) { expect(c.turnos).toEqual([]); expect(c.uso?.used).not.toBe(90); expect(c.agentes.catalogo === null || c.agentes.catalogo.energy_used === 3).toBe(true); }
  expect(screen.queryByText(/da Ana/)).toBeNull();
  await digitar("@"); await waitFor(() => expect(getsDeAgentes().some(([u]) => caminho(u) === "/agents/2")).toBe(true));
  await digitar("@Xerife oi"); await apertar("Enviar mensagem"); await waitFor(() => expect(screen.getByText("Resposta da Bela")).toBeTruthy());
  const dela = chats().find(([u]) => caminho(u) === "/agents/2/xerife/chat")!; expect(corpoDe(dela).context).toBeNull(); expect(bela(dela[1])).toBe(true);
});
it("valores ocultos: o turno de agente mostra só a mensagem de oculto", async () => {
  ag.chat = () => resp("Gastou R$ 123"); await abrir(); await enviar("@Xerife quanto?"); await waitFor(() => screen.getByText("Gastou R$ 123"));
  await act(async () => { fireEvent.press(screen.getByLabelText(/valores/i)); await drenar(); });
  expect(screen.queryByText("Gastou R$ 123")).toBeNull(); expect(screen.getByText(estado_OCULTA())).toBeTruthy();
});
const estado_OCULTA = () => "Mensagem oculta enquanto os valores estão privados.";
it("cota cheia: 'Ativar e responder', 'Tentar de novo' e 'Perguntar ao' ficam desligados (a cota da IA é a do Piggy)", async () => {
  ag.catalogo = (u) => { const d = cat(Number(u)); d.catalog.find((a) => a.kind === "carteiro")!.status = null; return d; };
  ag.chat = (n) => n === 1 ? erro(503, { error: "agent_unavailable", message: "Fora do ar agora.", retryable: true })
    : resp("Isso é com o Carteiro.", { usage: { used: 100, limit: 100 }, redirects: [{ kind: "carteiro", name: "Carteiro", question: "Que contas vencem?", access: "activate" }] });
  await abrir(); await enviar("@Carteiro oi"); await waitFor(() => screen.getByRole("button", { name: "Ativar e responder" }));
  await enviar("@Xerife a"); await waitFor(() => screen.getByRole("button", { name: "Tentar de novo" }));
  const des = (nome: string) => screen.getByRole("button", { name: nome }).props.accessibilityState.disabled;
  expect(des("Ativar e responder")).toBe(false); expect(des("Tentar de novo")).toBe(false); // com cota: ligados
  await enviar("@Xerife b"); await waitFor(() => screen.getByRole("button", { name: "Perguntar ao Carteiro" }));
  expect(des("Ativar e responder")).toBe(true); expect(des("Tentar de novo")).toBe(true); expect(des("Perguntar ao Carteiro")).toBe(true);
  await toque("Ativar e responder"); await toque("Perguntar ao Carteiro"); await act(drenar);
  expect(ag.nAtivar).toBe(0); expect(ag.nChat).toBe(2);
});
it("429 quota_exhausted do agente (outro cliente gastou o resto): a cota fica cheia na tela e o campo trava", async () => {
  ag.chat = () => erro(429, { error: "quota_exhausted", message: "Você atingiu a cota mensal compartilhada da IA." });
  await abrir(); await enviar("@Xerife oi"); await waitFor(() => expect(screen.getByText("Você atingiu a cota mensal compartilhada da IA.")).toBeTruthy());
  await digitar("@Xerife de novo"); expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeDisabled();
  await apertar("Enviar mensagem"); await act(drenar); expect(ag.nChat).toBe(1); expect(posts()).toHaveLength(0);
});
it("429 sem quota_exhausted (rajada): o campo segue livre", async () => {
  ag.chat = () => erro(429, { error: "rate_limited", message: "Calma." });
  await abrir(); await enviar("@Xerife oi"); await waitFor(() => expect(screen.getByText("Calma.")).toBeTruthy());
  await digitar("@Xerife de novo"); expect(screen.getByRole("button", { name: "Enviar mensagem" })).not.toBeDisabled();
});
it("cota só sobe: a resposta atrasada de outro agente (leitura velha) não destrava a cota que já encheu", async () => {
  const g = segurar(); ag.chat = async (n) => { if (n === 1) { await g.promessa; return resp("Velha", { usage: { used: 99, limit: 100 } }); } return resp("Nova", { usage: { used: 100, limit: 100 } }); };
  await abrir(); await enviar("@Xerife a"); await enviar("@Carteiro b"); await waitFor(() => expect(screen.getByText("Nova")).toBeTruthy());
  await act(async () => { g.soltar(); await drenar(); }); await waitFor(() => expect(screen.getByText("Velha")).toBeTruthy());
  await digitar("@Xerife de novo"); expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeDisabled();
});
it("cota só sobe também no Piggy: o fim atrasado do Piggy (99) não destrava a cota que o agente encheu (100)", async () => {
  const g = segurar(); ag.chat = async () => { await g.promessa; return resp("Do agente", { usage: { used: 100, limit: 100 } }); };
  await abrir(); await enviar("@Xerife a"); const f = respostaFluxo(); srv.chat = () => f; await enviar("oi"); // agente e Piggy em voo
  await act(async () => { g.soltar(); await drenar(); }); await waitFor(() => expect(screen.getByText("Do agente")).toBeTruthy());
  await act(async () => { f.empurrar({ tipo: "fim", reply: "Do Piggy", usage: { used: 99, limit: 100 }, message_id: 9, chips: [] }); f.fechar(); await drenar(); });
  await waitFor(() => expect(screen.getByText("Do Piggy")).toBeTruthy());
  await digitar("mais uma"); expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeDisabled();
});
it("cota: a usage da resposta do agente aparece como '85 de 100 mensagens utilizadas'", async () => {
  ag.chat = () => resp("Ok", { usage: { used: 85, limit: 100 } }); await abrir(); await enviar("@Xerife oi");
  await waitFor(() => expect(screen.getByText("85 de 100 mensagens utilizadas")).toBeTruthy());
});
it("fechar com o agente em voo: ao voltar à aba o turno sumiu, a resposta tardia não aparece e o context não vaza", async () => {
  const g = segurar(); ag.chat = async (n) => { if (n === 1) { await g.promessa; return resp("Resposta tardia", { context: "tardio" }); } return resp("Nova"); };
  await abrir(); await enviar("@Xerife um"); await waitFor(() => expect(chats()).toHaveLength(1));
  await ir("/resumo"); await voltar();
  expect(screen.queryByTestId("fala-agente")).toBeNull();
  await act(async () => { g.soltar(); await drenar(); await drenar(); }); expect(screen.queryByText("Resposta tardia")).toBeNull();
  await enviar("@Xerife dois"); await waitFor(() => screen.getByText("Nova")); expect(corpoDe(chats()[1]!).context).toBeNull();
});

// Guardas da espera do catálogo e do voo (cada uma tem controle negativo: sem ela, o teste fica vermelho).
it("texto digitado durante a espera do catálogo não é apagado", async () => {
  const g = segurar(); ag.segurarGet = g; await abrir(); await digitar("@Xerife oi");
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Enviar mensagem" })); await drenar(); });
  await digitar("outra coisa que eu escrevi enquanto esperava");
  await act(async () => { g.soltar(); await drenar(); });
  await waitFor(() => expect(chats()).toHaveLength(1));
  expect(campo().props.value).toBe("outra coisa que eu escrevi enquanto esperava");
});
it("Piggy em voo durante a espera do catálogo: o agente não nasce atrás do Piggy", async () => {
  const g = segurar(); ag.segurarGet = g; const f = respostaFluxo(); srv.chat = () => f; await abrir(); await digitar("@Xerife oi");
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Enviar mensagem" })); await drenar(); });
  await act(async () => { fireEvent.press(within(screen.getByTestId("sugestoes-piggy")).getAllByRole("button")[0]!); await drenar(); });
  await act(async () => { g.soltar(); await drenar(); });
  expect(posts()).toHaveLength(1); expect(chats()).toHaveLength(0); expect(screen.queryAllByTestId("fala-agente")).toHaveLength(0);
  await act(async () => { f.empurrar({ tipo: "fim", reply: "ok", usage: { used: 3, limit: 100 }, message_id: 3, chips: [] }); f.fechar(); await drenar(); });
});
it("2ª pergunta ao mesmo agente com a 1ª em voo não sai (context em ordem)", async () => {
  const g = segurar(); ag.chat = async (n) => { if (n === 1) await g.promessa; return resp("R" + n); };
  await abrir(); await enviar("@Xerife um"); await waitFor(() => expect(chats()).toHaveLength(1));
  await enviar("@Xerife dois"); await act(drenar); expect(chats()).toHaveLength(1);
  await act(async () => { g.soltar(); await drenar(); });
});
it("troca de conta durante a espera do catálogo: a mensagem da Ana não sai pela Bela", async () => {
  const g = segurar(); ag.segurarGet = g; await abrir(); await digitar("@Xerife segredo da Ana");
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Enviar mensagem" })); await drenar(); });
  await trocarParaBela(); await act(async () => { g.soltar(); await drenar(); await drenar(); });
  expect([...posts(), ...chats()].filter(([, r]) => bela(r) && /segredo/.test(String(r.body)))).toHaveLength(0);
});
it("catálogo lido antes da ativação, chegando depois do novo, não o sobrescreve", async () => {
  const base = fetchFalso.getMockImplementation()!; let n = 0, velho: ReturnType<typeof segurar> | null = null;
  fetchFalso.mockImplementation((url: string, req: RequestInit) => {
    if (!/^\/agents\/\d+$/.test(caminho(url))) return base(url, req);
    const dados = ag.catalogo("1"), seg = ++n === 3 ? (velho = segurar()) : null; // o 3º GET (anterior à ativação) é segurado
    return (async () => { if (seg) await seg.promessa; return resposta(200, dados); })();
  });
  ag.catalogo = () => cat(1, {}, null); ag.ativar = () => { ag.catalogo = () => cat(1, {}, "active"); return resposta(200, { ok: true }); };
  await abrir(); await enviar("@Xerife oi"); await waitFor(() => cabecalho("Xerife", "NÃO ATIVO"));
  while (n < 2) { await digitar("@"); await digitar(""); }
  await digitar(""); await digitar("@"); await act(drenar);
  await toque("Ativar e responder"); await waitFor(() => screen.getByText("Resposta")); await act(drenar);
  await act(async () => { velho?.soltar(); await drenar(); });
  await digitar("@Xerife outra"); await apertar("Enviar mensagem"); await act(drenar);
  expect(screen.queryAllByRole("button", { name: "Ativar e responder" })).toHaveLength(0); expect(chats()).toHaveLength(2);
});
it("catálogo falhou depois da espera: vai ao Piggy e o texto novo do campo fica", async () => {
  const g = segurar(); ag.segurarGet = g; ag.getStatus = 500; await abrir(); await digitar("@Xerife oi");
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Enviar mensagem" })); await drenar(); });
  await digitar("texto novo");
  await act(async () => { g.soltar(); await drenar(); });
  await waitFor(() => expect(posts()).toHaveLength(1)); expect(campo().props.value).toBe("texto novo");
});
