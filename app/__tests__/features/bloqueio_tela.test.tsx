/**
 * A trava biométrica pelo roteador de verdade (`renderRouter("./app")`): o
 * `_layout.tsx` real, o `SessaoProvider` real, o cofre dublado e o prompt do
 * sistema dublado (`expo-local-authentication`, ver `jest.setup.js`).
 *
 * Sem `setTimeout(0)` dentro de `act` (ver `layout.test.tsx`): microtarefas
 * drenadas à mão, e `waitFor` para o resto. O relógio é o dos fake timers que o
 * próprio `renderRouter` liga (`jest.useFakeTimers()`): o tempo fora anda com
 * `jest.setSystemTime` — uma espiã em `Date.now` é sobrescrita por eles.
 */
import * as LA from "expo-local-authentication";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";

import { guardarCredenciais } from "@/storage/secure";

import { chamadas, cofre, falharApagar, me, prepararCaso, resposta, rotear, S, segurar } from "./auth_apoio";

declare const global: typeof globalThis & { __dispararAppState: (v: string) => void; __definirAppState: (v: string) => void };

const TRAVA = "pb.trava.desligada";
const OLA = "Bom dia, S";

const drenar = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
};

/** Cada prompt fica pendente até o teste responder. */
let pendentes: ((r: LA.LocalAuthenticationResult) => void)[] = [];
async function responder(r: LA.LocalAuthenticationResult) {
  await act(async () => {
    pendentes.shift()!(r);
    await drenar();
  });
}
const OK = { success: true } as const;
const CANCELOU = { success: false, error: "user_cancel" } as const;
const prompts = () => jest.mocked(LA.authenticateAsync).mock.calls.length;

async function appVai(valor: "active" | "inactive" | "background") {
  await act(async () => {
    global.__dispararAppState(valor);
    await drenar();
  });
}

/** Fundo por `ms` e volta: inactive, background, (o relógio salta), active. */
async function foraPor(ms: number) {
  await appVai("inactive");
  await appVai("background");
  andar(ms);
  await appVai("active");
}

const andar = (ms: number) => jest.setSystemTime(Date.now() + ms);

async function tocar(rotulo: string) {
  await act(async () => {
    fireEvent.press(screen.getByRole("button", { name: rotulo }));
    await drenar();
  });
}

/** A trava está na tela: o nome sem nenhuma tela do produto. */
const travaNaTela = () => screen.queryByRole("header", { name: "PigBank" }) !== null;
const meChamado = () => chamadas().some((c) => c.caminho === "/auth/me");

beforeEach(() => {
  prepararCaso();
  rotear();
  pendentes = [];
  global.__definirAppState("active");
  jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(LA.SecurityLevel.BIOMETRIC);
  jest.mocked(LA.authenticateAsync).mockClear().mockImplementation(() => new Promise((r) => pendentes.push(r)));
});

afterEach(() => {
  jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(LA.SecurityLevel.NONE);
  jest.mocked(LA.authenticateAsync).mockResolvedValue(OK);
});

describe("trava — abertura com sessão salva", () => {
  it("ligada e com Face ID: a trava no lugar da pilha, sem /auth/me; depois do sucesso, o Início", async () => {
    await guardarCredenciais(S);
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(prompts()).toBe(1));

    expect(travaNaTela()).toBe(true);
    expect(screen.queryByText(OLA)).toBeNull();
    expect(meChamado()).toBe(false);

    await responder(OK);
    await waitFor(() => expect(screen.getByText(OLA)).toBeTruthy());
    expect(meChamado()).toBe(true);
    expect(travaNaTela()).toBe(false);
  });

  it.each([
    ["preferência desligada", () => cofre.set(TRAVA, "1")],
    ["aparelho sem código (NONE)", () => jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(LA.SecurityLevel.NONE)],
  ])("%s: o Início direto, nenhum prompt", async (_nome, preparar) => {
    await guardarCredenciais(S);
    preparar();
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(screen.getByText(OLA)).toBeTruthy());
    expect(prompts()).toBe(0);
  });

  it("cofre ilegível na preferência: trava (falha fechada), mesmo com a chave de desligada gravada", async () => {
    await guardarCredenciais(S);
    cofre.set(TRAVA, "1");
    const has = cofre.has.bind(cofre);
    cofre.has = (k: string) => {
      if (k === TRAVA) throw new Error("keychain recusou ler");
      return has(k);
    };
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(prompts()).toBe(1));
    expect(travaNaTela()).toBe(true);
  });
});

describe("trava — login", () => {
  it("entrar pelo formulário nunca mostra a trava nem pede prompt", async () => {
    renderRouter("./app", { initialUrl: "/entrar" });
    await waitFor(() => expect(screen).toHavePathname("/entrar"));
    fireEvent.changeText(screen.getByLabelText("E-mail"), "s@x.com");
    fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha");
    await tocar("Entrar");

    await waitFor(() => expect(screen.getByText(OLA)).toBeTruthy());
    expect(travaNaTela()).toBe(false);
    expect(prompts()).toBe(0);
  });
});

/** Abre com sessão e libera: o Início montado, trava ligada. */
async function liberado() {
  await guardarCredenciais(S);
  renderRouter("./app", { initialUrl: "/" });
  await waitFor(() => expect(prompts()).toBe(1));
  await responder(OK);
  await waitFor(() => expect(screen.getByText(OLA)).toBeTruthy());
}

describe("trava — voltando do fundo", () => {
  it("61 s fora: a trava cobre o Início (sem desmontá-lo) e pede UMA vez", async () => {
    await liberado();
    const leiturasAntesDoFundo = chamadas().filter((c) => c.caminho === "/auth/me").length;
    await foraPor(61_000);

    expect(travaNaTela()).toBe(true);
    expect(prompts()).toBe(2);
    // A pilha continua montada por baixo: nada de buscar o perfil de novo.
    expect(chamadas().filter((c) => c.caminho === "/auth/me")).toHaveLength(leiturasAntesDoFundo);

    // O active que o próprio Face ID provoca ao fechar não pede em dobro.
    await appVai("inactive");
    await appVai("active");
    expect(prompts()).toBe(2);

    await responder(OK);
    expect(travaNaTela()).toBe(false);
    expect(screen.getByText(OLA)).toBeTruthy();
  });

  it("59 s fora: nada — nem trava, nem cobertura em JS (o fora de foco é da tampa nativa)", async () => {
    await liberado();
    await appVai("inactive");
    expect(travaNaTela()).toBe(false);

    await appVai("background");
    andar(59_000);
    await appVai("active");
    expect(travaNaTela()).toBe(false);
    expect(prompts()).toBe(1);
  });

  it("trava DESLIGADA: o inactive não desmonta o painel (é da tampa nativa); só o background (#897)", async () => {
    await guardarCredenciais(S);
    cofre.set(TRAVA, "1");
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(screen.getByText(OLA)).toBeTruthy());

    await appVai("inactive");
    expect(travaNaTela()).toBe(false);
    expect(screen.getByText(OLA)).toBeTruthy();
    expect(prompts()).toBe(0);

    await appVai("background");
    expect(screen.queryByText(OLA)).toBeNull();
    expect(screen.queryByTestId("painel-conta")).toBeNull();
    expect(prompts()).toBe(0);

    await appVai("active");
    await waitFor(() => expect(screen.getByText(OLA)).toBeTruthy());
    expect(screen).toHavePathname("/resumo");
    expect(travaNaTela()).toBe(false);
    expect(prompts()).toBe(0);
  });

  it("o código foi tirado do aparelho depois da abertura: a volta de 61 s não pede nem prende", async () => {
    await liberado();
    jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(LA.SecurityLevel.NONE);
    await foraPor(61_000);
    expect(prompts()).toBe(1);
    expect(travaNaTela()).toBe(false);
  });

  it.each([
    ["libera se o nível agora é NONE", LA.SecurityLevel.NONE, false],
    ["continua travado se ainda há código", LA.SecurityLevel.SECRET, true],
  ])("prompt responde passcode_not_set: reconfere o nível e %s", async (_nome, nivelDepois, travado) => {
    await liberado();
    await foraPor(61_000);
    jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(nivelDepois);
    await responder({ success: false, error: "passcode_not_set" });
    expect(travaNaTela()).toBe(travado);
  });

  it("cancelar mostra Desbloquear; inactive→active depois NÃO pede; tocar pede", async () => {
    await liberado();
    await foraPor(61_000);
    await responder(CANCELOU);

    expect(screen.getByRole("button", { name: "Desbloquear" })).toBeTruthy();
    await appVai("inactive");
    await appVai("active");
    expect(prompts()).toBe(2);

    await tocar("Desbloquear");
    expect(prompts()).toBe(3);
  });
});

describe("trava — Sair", () => {
  async function travadoNaAbertura() {
    await guardarCredenciais(S);
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(prompts()).toBe(1));
    await responder(CANCELOU);
  }

  it("Sair na trava vai para as Boas-vindas, sem pedir autenticação", async () => {
    await travadoNaAbertura();
    await tocar("Sair");
    await waitFor(() => expect(screen).toHavePathname("/boas-vindas"));
    expect(prompts()).toBe(1);
  });

  it("cofre recusando no Sair: Banner de erro e continua travado", async () => {
    await travadoNaAbertura();
    falharApagar(true);
    await tocar("Sair");
    await waitFor(() => expect(screen.getByText("Não conseguimos sair. Tente de novo.")).toBeTruthy());
    expect(travaNaTela()).toBe(true);
    expect(screen.queryByText(OLA)).toBeNull();
  });
});

describe("trava — sessão que acaba por baixo", () => {
  it("expirou com a trava por cima: Entrar com o aviso; o prompt que responde depois não muda nada", async () => {
    await guardarCredenciais(S);
    const perfil = segurar();
    rotear({
      "/auth/me": async () => {
        await perfil.promessa;
        return resposta(401, { detail: "expirado" });
      },
      "/auth/refresh": () => resposta(401, { detail: "invalid_refresh_token" }),
    });
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(prompts()).toBe(1));
    await responder(OK);
    await waitFor(() => expect(meChamado()).toBe(true));

    await foraPor(61_000);
    expect(prompts()).toBe(2);
    await act(async () => {
      perfil.soltar();
      await drenar();
    });

    await waitFor(() => expect(screen).toHavePathname("/entrar"));
    expect(screen.getByText("Sua sessão expirou. Entre de novo.")).toBeTruthy();
    await responder(OK);
    expect(screen).toHavePathname("/entrar");
    expect(travaNaTela()).toBe(false);
  });
});

describe("trava — preferência do aparelho (decisão Q2 do dono)", () => {
  it("desligada sobrevive a Sair e a um novo login: a chave continua e a volta de 10 min não trava", async () => {
    await guardarCredenciais(S);
    cofre.set(TRAVA, "1");
    rotear({ "/auth/me": me });
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(screen.getByText(OLA)).toBeTruthy());

    await tocar("Abrir minha conta");
    await tocar("Sair");
    await waitFor(() => expect(screen).toHavePathname("/boas-vindas"));
    expect(cofre.get(TRAVA)).toBe("1");

    await tocar("Já tenho conta");
    await waitFor(() => expect(screen).toHavePathname("/entrar"));
    fireEvent.changeText(screen.getByLabelText("E-mail"), "s@x.com");
    fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha");
    await tocar("Entrar");
    await waitFor(() => expect(screen.getByText(OLA)).toBeTruthy());

    await foraPor(600_000);
    expect(travaNaTela()).toBe(false);
    expect(prompts()).toBe(0);
    expect(cofre.get(TRAVA)).toBe("1");
  });
});
