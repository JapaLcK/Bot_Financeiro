/**
 * Perfil (P1, só leitura): de onde vem o "Conectado", o que acontece sem credencial e cada estado de erro.
 * Os controles negativos (mutante, vermelho, restaura) estão no relato do PR.
 */
import { Linking } from "react-native";
import { act, fireEvent, screen, waitFor } from "expo-router/testing-library";

import { resposta, segurar } from "./auth_apoio";
import { CONTATO, LINK, abrirPerfil, abrirUrl, comWhatsApp, postsDoLink, tocar } from "./perfil_apoio";
import { appVai, drenar, preparar } from "./seguranca_sem_senha_apoio";

beforeEach(() => {
  preparar();
  jest.mocked(Linking.openURL).mockReset().mockResolvedValue(true as never);
});

const FALHA = "Não conseguimos abrir o WhatsApp agora. Tente de novo.";
const esperarDados = () => waitFor(() => expect(screen.getByLabelText("Nome, Sofia Lima")).toBeTruthy());

describe("Perfil — dados", () => {
  it("mostra nome, e-mail, telefone formatado (sem esconder dígitos) e 'Desde <mês de ano>' no fuso de Brasília", async () => {
    await abrirPerfil({ contato: { created_at: "2026-04-01T01:00:00Z" } }); // 31/03 em Brasília
    await esperarDados();
    expect(screen.getByLabelText("E-mail, sofia@x.com")).toBeTruthy();
    expect(screen.getByLabelText("Telefone, (11) 98765-4321")).toBeTruthy();
    expect(screen.getByText("(11) 98765-4321")).toBeTruthy();
    expect(screen.getByLabelText("Membro, Desde março de 2026")).toBeTruthy();
  });

  it.each([["nulo", null], ["inválido", "ontem"]])("created_at %s: a linha 'Membro' some, sem crash", async (_nome, created_at) => {
    await abrirPerfil({ contato: { created_at } });
    await esperarDados();
    expect(screen.queryByLabelText(/^Membro/)).toBeNull();
  });

  it("e-mail só no /auth/me (o do contato vem nulo): conta sem senha ganha o EsqueciSenha, não o texto sem e-mail", async () => {
    await abrirPerfil({ me: { precisa_criar_senha: true }, contato: { email: null } });
    await waitFor(() => expect(screen.getByLabelText("Nome, Sofia Lima")).toBeTruthy());
    expect(screen.getByText("Definir senha")).toBeTruthy();
    expect(screen.queryByText(/Defina uma senha pelo site/)).toBeNull();
  });

  it("dado vazio mostra '—' e o leitor de tela lê 'Não definido'", async () => {
    await abrirPerfil({ contato: { display_name: null, phone: null } });
    await waitFor(() => expect(screen.getByLabelText("Nome, Não definido")).toBeTruthy());
    expect(screen.getByLabelText("Telefone, Não definido")).toBeTruthy();
    expect(screen.getAllByText("—")).toHaveLength(2);
  });
});

describe("Perfil — Conectado vem de identities, nunca de whatsapp_verified_at", () => {
  it("A) marcador nulo + identidade de WhatsApp: Conectado, sem Conectar, e o telefone da identidade não entra na tela", async () => {
    await abrirPerfil({ contato: { whatsapp_verified_at: null, ...comWhatsApp } });
    await esperarDados();
    expect(screen.getByLabelText("WhatsApp, Conectado")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Conectar" })).toBeNull();
    expect(screen.queryByText(/90000-0000/)).toBeNull();
  });

  it("B) marcador preenchido + sem identidade de WhatsApp: Conectar", async () => {
    await abrirPerfil({ contato: { whatsapp_verified_at: "2026-03-04T15:00:00Z", identities: [] } });
    await esperarDados();
    expect(screen.getByLabelText("WhatsApp, Não conectado")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Conectar" })).toBeTruthy();
  });

  it.each([
    ["conectado", { whatsapp_verified_at: null, ...comWhatsApp }, "WhatsApp, Conectado"],
    ["não conectado", { identities: [] }, "WhatsApp, Não conectado"],
  ])("a palavra WhatsApp não é texto visível (%s), só o rótulo acessível", async (_n, contato, rotulo) => {
    await abrirPerfil({ contato });
    await esperarDados();
    expect(screen.queryByText("WhatsApp")).toBeNull();
    expect(screen.getByLabelText(rotulo)).toBeTruthy();
  });
});

describe("Perfil — Conectar", () => {
  it("200 com o link do WhatsApp: abre UMA vez, com o link, e o POST vai com corpo vazio", async () => {
    await abrirPerfil();
    await esperarDados();
    await tocar("Conectar");
    expect(abrirUrl()).toHaveBeenCalledTimes(1);
    expect(abrirUrl()).toHaveBeenCalledWith(LINK);
    expect(postsDoLink()).toEqual([expect.objectContaining({ corpo: {} })]);
  });

  it("403 password_required: EsqueciSenha no lugar, sem erro genérico, sem Conectar e sem abrir nada", async () => {
    await abrirPerfil({ link: () => resposta(403, { detail: { error: "password_required" } }) });
    await esperarDados();
    await tocar("Conectar");
    expect(screen.getByText("Definir senha")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Enviar link" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Conectar" })).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(abrirUrl()).not.toHaveBeenCalled();
  });

  it.each([
    ["detail em texto", { detail: "Acesso negado" }],
    ["outro error", { detail: { error: "pro_required" } }],
  ])("403 que NÃO é password_required (%s): aviso de falha do WhatsApp, não o EsqueciSenha", async (_nome, corpo) => {
    await abrirPerfil({ link: () => resposta(403, corpo) });
    await esperarDados();
    await tocar("Conectar");
    expect(screen.getByText(FALHA)).toBeTruthy();
    expect(screen.queryByText("Definir senha")).toBeNull();
    expect(screen.getByRole("button", { name: "Conectar" })).toBeTruthy();
    expect(abrirUrl()).not.toHaveBeenCalled();
  });

  it("precisa_criar_senha: zero POST no link-code, EsqueciSenha na tela e sem Conectar", async () => {
    await abrirPerfil({ me: { precisa_criar_senha: true } });
    await esperarDados();
    expect(screen.getByText("Definir senha")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Conectar" })).toBeNull();
    expect(postsDoLink()).toHaveLength(0);
  });

  it.each([
    ["wa.me abre o seletor de contatos", "https://wa.me/?text=vincular%20ABC123"],
    ["javascript:", "javascript:alert(1)"],
    ["outro host com o caminho do WhatsApp", "https://evil.com/api.whatsapp.com/send?x=1"],
    ["usuário@host disfarçado", "https://api.whatsapp.com@evil.com/send?x=1"],
    ["http sem TLS", "http://api.whatsapp.com/send?x=1"],
    ["só a âncora ^ barra (o link bom aparece no meio)", "javascript:alert(1)//https://api.whatsapp.com/send?x=1"],
  ])("link reprovado (%s): não abre, mostra aviso e o botão fica", async (_nome, link) => {
    await abrirPerfil({ link: () => resposta(200, { whatsapp_link: link }) });
    await esperarDados();
    await tocar("Conectar");
    expect(abrirUrl()).not.toHaveBeenCalled();
    expect(screen.getByText(FALHA)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Conectar" })).toBeTruthy();
  });

  it("link vazio (servidor sem número): some o Conectar e aparece texto curto", async () => {
    await abrirPerfil({ link: () => resposta(200, { whatsapp_link: "" }) });
    await esperarDados();
    await tocar("Conectar");
    expect(screen.queryByRole("button", { name: "Conectar" })).toBeNull();
    expect(screen.getByText("O WhatsApp ainda não está disponível. Tente mais tarde.")).toBeTruthy();
    expect(abrirUrl()).not.toHaveBeenCalled();
  });

  it("429 (limite de 15/h), 500 e openURL rejeitado: aviso curto (o do 429 não manda tentar de novo) e o botão fica", async () => {
    const casos = [
      [() => resposta(429, { detail: "x" }), "Muitas tentativas. Tente mais tarde."],
      [() => resposta(500, { detail: "boom" }), FALHA],
    ] as const;
    for (const [link, aviso] of casos) {
      preparar();
      await abrirPerfil({ link });
      await esperarDados();
      await tocar("Conectar");
      expect(screen.getByText(aviso)).toBeTruthy();
      if (aviso !== FALHA) expect(screen.queryByText(FALHA)).toBeNull();
      expect(screen.getByRole("button", { name: "Conectar" })).toBeTruthy();
      screen.unmount();
    }
    preparar();
    jest.mocked(Linking.openURL).mockRejectedValueOnce(new Error("sem app"));
    await abrirPerfil();
    await esperarDados();
    await tocar("Conectar");
    expect(screen.getByText(FALHA)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Conectar" })).toBeTruthy();
  });

  it("durante o POST o botão fica ocupado (busy) e volta ao normal quando termina", async () => {
    const presa = segurar();
    await abrirPerfil({ link: async () => { await presa.promessa; return resposta(500, { detail: "boom" }); } });
    await esperarDados();
    const busy = () => screen.getByRole("button", { name: "Conectar" }).props.accessibilityState.busy;
    expect(busy()).toBe(false);
    await tocar("Conectar");
    expect(busy()).toBe(true);
    presa.soltar();
    await act(drenar);
    expect(busy()).toBe(false);
  });

  it("duplo toque no mesmo instante: um POST só", async () => {
    const presa = segurar();
    await abrirPerfil({ link: async () => { await presa.promessa; return resposta(200, { whatsapp_link: LINK }); } });
    await esperarDados();
    const botao = screen.getByRole("button", { name: "Conectar" });
    await act(async () => {
      fireEvent.press(botao);
      fireEvent.press(botao);
      await drenar();
    });
    presa.soltar();
    await act(drenar);
    expect(postsDoLink()).toHaveLength(1);
    expect(abrirUrl()).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["200 velho", () => resposta(200, CONTATO)],
    ["402", () => resposta(402, { detail: { error: "plan_selection_required" } })],
  ])("carga antiga solta por último (%s) não sobrescreve a mais nova", async (_nome, velha) => {
    let n = 0;
    const presa = segurar();
    await abrirPerfil({}, {
      "/settings/1/security": async () => {
        n += 1;
        if (n === 2) { await presa.promessa; return velha(); } // a 2ª carga fica presa
        return resposta(200, n === 1 ? CONTATO : { ...CONTATO, ...comWhatsApp });
      },
    });
    await esperarDados();
    await appVai("background");
    await appVai("active");
    await waitFor(() => expect(n).toBe(2));
    await appVai("background");
    await appVai("active");
    await waitFor(() => expect(screen.getByLabelText("WhatsApp, Conectado")).toBeTruthy());
    presa.soltar();
    await act(drenar);
    expect(n).toBe(3);
    expect(screen.getByLabelText("WhatsApp, Conectado")).toBeTruthy();
    expect(screen.queryByText("Escolha um plano para ver seu perfil.")).toBeNull();
  });

  it("volta do WhatsApp (background e active): relê e passa a mostrar Conectado", async () => {
    const conta = { ...CONTATO } as Record<string, unknown>;
    await abrirPerfil({}, { "/settings/1/security": () => resposta(200, conta) });
    await esperarDados();
    expect(screen.getByRole("button", { name: "Conectar" })).toBeTruthy();
    Object.assign(conta, comWhatsApp); // o bot processou o "vincular"
    await appVai("background");
    await appVai("active");
    await waitFor(() => expect(screen.getByLabelText("WhatsApp, Conectado")).toBeTruthy());
  });
});

describe("Perfil — estados do GET", () => {
  it("402: estado do plano, texto fixo, sem retry", async () => {
    await abrirPerfil({}, { "/settings/1/security": () => resposta(402, { detail: { error: "plan_selection_required" } }) });
    await waitFor(() => expect(screen.getByText("Escolha um plano para ver seu perfil.")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Tentar de novo" })).toBeNull();
  });

  it("403 de conta em exclusão: estado próprio, sem retry; outro 403 é erro com Tentar de novo", async () => {
    await abrirPerfil({}, { "/settings/1/security": () => resposta(403, { detail: "Esta conta está agendada para exclusão em 2026-11-01." }) });
    await waitFor(() => expect(screen.getByText("Esta conta está agendada para exclusão.")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Tentar de novo" })).toBeNull();
    screen.unmount();

    preparar();
    await abrirPerfil({}, { "/settings/1/security": () => resposta(403, { detail: "Acesso negado" }) });
    await waitFor(() => expect(screen.getByRole("button", { name: "Tentar de novo" })).toBeTruthy());
    expect(screen.queryByText("Acesso negado")).toBeNull();
    expect(screen.queryByText("Esta conta está agendada para exclusão.")).toBeNull();
  });

  it("500: Tentar de novo, e o toque rebusca", async () => {
    let fora = true;
    await abrirPerfil({}, { "/settings/1/security": () => (fora ? resposta(500, { detail: "boom" }) : resposta(200, CONTATO)) });
    await waitFor(() => expect(screen.getByRole("button", { name: "Tentar de novo" })).toBeTruthy());
    fora = false;
    await tocar("Tentar de novo");
    await esperarDados();
  });

  it("contrato quebrado (sem identities): erro com Tentar de novo, não crash", async () => {
    const { identities: _fora, ...semIdentities } = CONTATO;
    await abrirPerfil({}, { "/settings/1/security": () => resposta(200, semIdentities) });
    await waitFor(() => expect(screen.getByRole("button", { name: "Tentar de novo" })).toBeTruthy());
  });

  it("recarga que falha com dados na tela mantém a tela", async () => {
    let fora = false;
    await abrirPerfil({}, { "/settings/1/security": () => (fora ? resposta(500, { detail: "boom" }) : resposta(200, CONTATO)) });
    await esperarDados();
    fora = true;
    await appVai("background");
    await appVai("active");
    expect(screen.getByLabelText("Nome, Sofia Lima")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Tentar de novo" })).toBeNull();
  });
});

describe("Configurações", () => {
  it("a linha Perfil abre /perfil", async () => {
    await abrirPerfil({}, {}, "/configuracoes");
    await waitFor(() => expect(screen.getByRole("button", { name: "Perfil" })).toBeTruthy());
    await tocar("Perfil");
    await waitFor(() => expect(screen).toHavePathname("/perfil"));
    await esperarDados();
  });
});
