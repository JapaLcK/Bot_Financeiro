/**
 * `carregar` da Segurança para conta sem senha: tabela statusMfa × perfil, sessão expirada (comum, da
 * carga superada, sem esperar a irmã, sem repetir). Os demais casos ficam em `seguranca_sem_senha.test.tsx`.
 */
import { act, fireEvent, screen, waitFor } from "expo-router/testing-library";

import { resposta, segurar } from "./auth_apoio";
import { MFA_DESLIGADO, abrir, appVai, drenar, preparar } from "./seguranca_sem_senha_apoio";

beforeEach(preparar);

describe("Segurança — carregar", () => {
  // Caminho COMUM: `/auth/reset-password` revoga as outras sessões e os refresh tokens, então a volta
  // do Safari encontra 401 + refresh 401 e a recarga em foreground leva a /entrar. É por isso que a
  // copy do bloco avisa que será preciso entrar de novo.
  it("X1 — COMUM: volta do Safari após o reset (401 + refresh 401) numa recarga SILENCIOSA leva a /entrar com o aviso", async () => {
    let morta = false;
    await abrir(
      { valor: false },
      {
        "/auth/mfa/status": () => (morta ? resposta(401, { detail: "expirado" }) : resposta(200, MFA_DESLIGADO)),
        "/auth/refresh": () => resposta(401, { detail: "invalid_refresh_token" }),
      },
    );
    await waitFor(() => expect(screen.getByText("Definir senha")).toBeTruthy());

    morta = true; // o reset de senha, feito no navegador, revogou a sessão do app
    await appVai("background");
    await appVai("active");

    await waitFor(() => expect(screen).toHavePathname("/entrar"));
    expect(screen.getByText("Sua sessão expirou. Entre de novo.")).toBeTruthy();
  });

  // Corrida real desta feature: o reset de senha revoga as sessões, e o /auth/me pode descobrir isso
  // ANTES do /auth/mfa/status (que ainda respondeu 200). O erro de sessão não pode virar "sem conta".
  it("sessão expirada descoberta só pelo /auth/me (mfa/status 200): vai a /entrar com o aviso, sem controles velhos", async () => {
    await abrir(
      { valor: false },
      {
        "/auth/me": () => resposta(401, { detail: "expirado" }),
        "/auth/refresh": () => resposta(401, { detail: "invalid_refresh_token" }),
      },
    );

    await waitFor(() => expect(screen).toHavePathname("/entrar"));
    expect(screen.getByText("Sua sessão expirou. Entre de novo.")).toBeTruthy();
    expect(screen.queryByText("Definir senha")).toBeNull();
    expect(screen.queryByRole("button", { name: "Ativar" })).toBeNull();
  });

  // Uma linha da tabela de `carregar` (statusMfa x perfil) por caso, na carga inicial. A sessão expirada
  // de QUALQUER das duas vence, em qualquer ordem de chegada: o `me` é atrasado nos mistos para o status
  // rejeitar primeiro (é o que o `Promise.all` puro não aguentava).
  describe("carregar — statusMfa × perfil", () => {
    const OK_STATUS = () => resposta(200, MFA_DESLIGADO);
    const SESSAO = () => resposta(401, { detail: "expirado" });
    const REDE = () => resposta(500, { detail: "x" });
    const depois = (r: () => Response) => async () => {
      await new Promise((ok) => setTimeout(ok, 30));
      return r();
    };
    const ME_OK = () => resposta(200, { user_id: 1, email: "s@x.com", display_name: "S", app_access: true, has_password: false });

    it.each([
      ["status sessão × perfil ok", SESSAO, ME_OK, "login"],
      ["status sessão × perfil sessão", SESSAO, SESSAO, "login"],
      ["status sessão × perfil recuperável", SESSAO, REDE, "login"],
      ["status recuperável × perfil sessão (status rejeita primeiro)", REDE, depois(SESSAO), "login"],
      ["status ok × perfil sessão (status resolve primeiro)", OK_STATUS, depois(SESSAO), "login"],
      ["status recuperável × perfil ok", REDE, ME_OK, "erro"],
      ["status recuperável × perfil recuperável", REDE, REDE, "erro"],
      ["status ok × perfil recuperável", OK_STATUS, REDE, "pronto"],
      ["status ok × perfil ok", OK_STATUS, ME_OK, "bloco"],
    ] as const)("%s -> %s", async (_nome, status, me, esperado) => {
      await abrir(
        { valor: false },
        { "/auth/mfa/status": status, "/auth/me": me, "/auth/refresh": () => resposta(401, { detail: "invalid_refresh_token" }) },
      );

      if (esperado === "login") {
        await waitFor(() => expect(screen).toHavePathname("/entrar"));
        expect(screen.getByText("Sua sessão expirou. Entre de novo.")).toBeTruthy();
      } else if (esperado === "erro") {
        await waitFor(() => expect(screen.getByRole("button", { name: "Tentar de novo" })).toBeTruthy());
        expect(screen.queryByRole("button", { name: "Ativar" })).toBeNull();
      } else if (esperado === "pronto") {
        await waitFor(() => expect(screen.getByRole("button", { name: "Ativar" })).toBeTruthy());
      } else {
        await waitFor(() => expect(screen.getByText("Definir senha")).toBeTruthy());
      }
      if (esperado === "login") {
        expect(screen.queryByText("Definir senha")).toBeNull();
        expect(screen.queryByRole("button", { name: "Ativar" })).toBeNull();
        expect(screen.queryByRole("button", { name: "Tentar de novo" })).toBeNull();
      }
    });
  });

  // Invariante: a geração governa só escritas de estado. A sessão expirada de uma carga ANTIGA (que ainda
  // esperava a outra chamada do allSettled) chega a `falha()` mesmo com uma carga nova já em curso.
  describe("carregar — sessão expirada de carga superada", () => {
    /**
     * Carga 1 (a do foco): status falha por rede na hora e o /auth/me fica preso até `soltar()`, quando
     * descobre a sessão revogada (401 + refresh 401). Carga 2 = volta do navegador, já em curso nessa hora.
     */
    async function cargaAntigaExpirada(carga2: { status: () => Response; me: () => Response }) {
      const preso = segurar();
      let statusVez = 0;
      let meVez = 0;
      await abrir(
        { valor: false },
        {
          "/auth/mfa/status": () => (statusVez++ === 0 ? resposta(500, { detail: "x" }) : carga2.status()),
          "/auth/me": async () => {
            if (meVez++ === 0) {
              await preso.promessa;
              return resposta(401, { detail: "expirado" });
            }
            return carga2.me();
          },
          "/auth/refresh": () => resposta(401, { detail: "invalid_refresh_token" }),
        },
      );
      await waitFor(() => expect(statusVez).toBe(1)); // a carga 1 já tem a falha de rede e espera o /auth/me
      await appVai("background");
      await appVai("active"); // carga 2 começa: a geração da carga 1 foi superada
      return preso;
    }

    it("a carga nova só tem falha de rede: a expiração da antiga ainda leva a /entrar", async () => {
      const preso = await cargaAntigaExpirada({ status: () => resposta(500, { detail: "x" }), me: () => resposta(500, { detail: "x" }) });
      await waitFor(() => expect(screen.getByRole("button", { name: "Tentar de novo" })).toBeTruthy()); // a nova pintou o erro

      preso.soltar();

      await waitFor(() => expect(screen).toHavePathname("/entrar"));
      expect(screen.getByText("Sua sessão expirou. Entre de novo.")).toBeTruthy();
    });

    it("duas expirações de gerações diferentes: avisa e navega uma vez só", async () => {
      const preso = await cargaAntigaExpirada({ status: () => resposta(401, { detail: "expirado" }), me: () => resposta(401, { detail: "expirado" }) });
      await waitFor(() => expect(screen).toHavePathname("/entrar")); // a carga nova expirou primeiro

      preso.soltar(); // e agora a antiga também
      await act(drenar);

      expect(screen).toHavePathname("/entrar");
      expect(screen.getAllByText("Sua sessão expirou. Entre de novo.")).toHaveLength(1);
    });
  });

  // Invariante 4: evento terminal de sessão propaga na hora, sem esperar a irmã. O /auth/me nunca resolve
  // (o limite do cliente é de 15 s) e o teste termina muito antes disso: o `waitFor` espera ~1 s.
  // O caso inverso (falha rápida só recuperável, irmã com SessaoExpirada depois) é a linha
  // "status recuperável × perfil sessão" da tabela, acima.
  describe("carregar — sessão expirada não espera a chamada irmã", () => {
    const PENDURADO = () => new Promise<Response>(() => undefined);
    const EXPIRADO = { "/auth/refresh": () => resposta(401, { detail: "invalid_refresh_token" }) };

    it("carga inicial: status expira e o /auth/me pendura -> /entrar sem esperar o limite", async () => {
      await abrir({ valor: false }, { ...EXPIRADO, "/auth/mfa/status": () => resposta(401, { detail: "expirado" }), "/auth/me": PENDURADO });
      await waitFor(() => expect(screen).toHavePathname("/entrar"));
      expect(screen.getByText("Sua sessão expirou. Entre de novo.")).toBeTruthy();
    });

    it("recarga em foreground com a tela em pronto: status expira e o /auth/me pendura -> /entrar", async () => {
      let revogada = false;
      await abrir(
        { valor: false },
        {
          ...EXPIRADO,
          "/auth/mfa/status": () => (revogada ? resposta(401, { detail: "expirado" }) : resposta(200, MFA_DESLIGADO)),
          "/auth/me": () => (revogada ? PENDURADO() : resposta(200, { user_id: 1, email: "s@x.com", app_access: true, has_password: false })),
        },
      );
      await waitFor(() => expect(screen.getByText("Definir senha")).toBeTruthy());

      revogada = true;
      await appVai("background");
      await appVai("active");

      await waitFor(() => expect(screen).toHavePathname("/entrar"));
      expect(screen.queryByText("Definir senha")).toBeNull();
    });
  });

  // Invariante 3: cada expiração é notificada uma única vez, na hora. O `/auth/me` pendente só rejeita
  // DEPOIS de a pessoa ter entrado de novo; o `falha()` tardio do catch derrubaria a sessão nova.
  it("expiração já notificada não se repete: o irmão tardio NÃO derruba a sessão nova", async () => {
    const preso = segurar();
    let statusVez = 0;
    let meVez = 0;
    await abrir(
      { valor: false },
      {
        "/auth/mfa/status": () => (statusVez++ === 0 ? resposta(401, { detail: "expirado" }) : resposta(200, MFA_DESLIGADO)),
        "/auth/me": async () => {
          if (meVez++ > 0) return resposta(200, { user_id: 1, email: "s@x.com", display_name: "S", app_access: true, has_password: true });
          await preso.promessa;
          return resposta(401, { detail: "expirado" });
        },
        "/auth/refresh": () => resposta(401, { detail: "invalid_refresh_token" }),
      },
    );
    await waitFor(() => expect(screen).toHavePathname("/entrar")); // status expirou na hora; o /auth/me ficou pendente

    // E-mail DIFERENTE do da sessão antiga: com o mesmo e-mail o refresh token coincide (`rt_s`) e o
    // 401 tardio da chamada antiga limparia, no cofre, a credencial da sessão nova.
    fireEvent.changeText(screen.getByLabelText("E-mail"), "novo@x.com");
    fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha");
    await act(async () => {
      fireEvent.press(screen.getByRole("button", { name: "Entrar" }));
      await drenar();
    });
    await waitFor(() => expect(screen).not.toHavePathname("/entrar")); // sessão NOVA autenticada

    preso.soltar(); // o /auth/me da carga antiga rejeita agora, com a sessão nova no ar
    await act(drenar);
    await act(drenar);

    expect(screen).not.toHavePathname("/entrar");
    expect(screen.queryByText("Sua sessão expirou. Entre de novo.")).toBeNull();
  });
});
