/** Excluir conta (C1): a sessão morre ou a conta troca com o DELETE em voo. Separado de conta_excluir por causa do teto de linhas. */
import { act, screen, waitFor } from "expo-router/testing-library";
import { z } from "zod";

import { chamar } from "@/api/client";
import { NAO_CONFIRMOU } from "@/features/conta/exclusao";
import { chaveLayout, PERFIS } from "@/features/painel/catalogo";
import { guardarCredenciais, lerCredenciais } from "@/storage/secure";

import { cofre, resposta, segurar } from "./auth_apoio";
import { abrir, chamadasA, digitar, esperarBotao, responderAlerta, sessaoMorta, tocar } from "./conta_apoio";
import { drenar, preparar } from "./seguranca_sem_senha_apoio";

beforeEach(() => {
  preparar();
  jest.restoreAllMocks();
  Reflect.deleteProperty(cofre, "delete");
});

const abrirExcluir = (extra: Parameters<typeof abrir>[2]) => abrir("/excluir-conta", {}, extra).then(() => esperarBotao("Excluir conta"));

async function excluir() {
  digitar("Senha", "s3nha");
  digitar("Código do e-mail", "123456");
  await tocar("Excluir conta");
}

const layouts = (uid: number) => PERFIS.map((p) => cofre.has(chaveLayout(uid, p.id)));
const semearLayouts = () => {
  for (const uid of [7, 8]) for (const p of PERFIS) cofre.set(chaveLayout(uid, p.id), '["contas"]');
};
const DELETE = "/auth/account";
const B = { access: "access-b", refresh: "rt_b" };
/** Mesma sessão de A (jti igual), antes e depois de uma rotação do refresh. */
const ACCESS_A = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiAiMSIsICJqdGkiOiAic2Vzc2FvLUEifQ.assinatura";
const semLogoutDeB = () => !chamadasA("/auth/logout").some((c) => c.auth === "Bearer rt_b");
/** Roda `acao` quando o primeiro layout de 7 é apagado: o ponto exato entre o DELETE e o `sair`. */
const aoApagarLayouts = (acao: () => Promise<void>) => {
  const apagar = Map.prototype.delete.bind(cofre);
  cofre.delete = (k: string) => {
    if (k === PERFIS.map((p) => chaveLayout(7, p.id))[0]) void acao();
    return apagar(k);
  };
};

describe("Excluir conta — sessão morre com o DELETE em voo", () => {
  /** O servidor revoga as sessões ao marcar a conta: outra requisição do app, enquanto o DELETE roda, toma 401 de sessão. */
  it("a sessão revogada no meio do DELETE e o 200 depois: layouts apagados, sai da conta e avisa que não deu para confirmar", async () => {
    const seg = segurar();
    await abrirExcluir({
      [DELETE]: async () => { await seg.promessa; return resposta(200, { status: "deleted" }); },
      "/auth/refresh": () => resposta(401, { detail: "invalid_refresh_token" }),
      "/paralela": sessaoMorta,
    });
    semearLayouts();
    const alerta = responderAlerta("Excluir");

    await excluir();
    await chamar("/paralela", z.any(), {}).catch(() => undefined);
    await act(async () => { seg.soltar(); await drenar(); });

    await waitFor(() => expect(alerta).toHaveBeenCalledWith(NAO_CONFIRMOU));
    expect(layouts(7)).toEqual(PERFIS.map(() => false));
    expect(layouts(8)).toEqual(PERFIS.map(() => true));
    await expect(lerCredenciais()).resolves.toBeNull();
    await waitFor(() => expect(screen).toHavePathname("/boas-vindas"));
  });

  it("401 de sessão na PRÓPRIA requisição: o servidor não executou nada, então vai a /entrar e os layouts ficam", async () => {
    await abrirExcluir({ [DELETE]: sessaoMorta, "/auth/refresh": () => resposta(401, { detail: "invalid_refresh_token" }) });
    semearLayouts();
    responderAlerta("Excluir");

    await excluir();

    await waitFor(() => expect(screen).toHavePathname("/entrar"));
    expect(layouts(7)).toEqual(PERFIS.map(() => true));
    expect(layouts(8)).toEqual(PERFIS.map(() => true));
  });

  it("outra conta entrou durante o DELETE: o 200 tardio apaga os layouts da antiga, mas não chama /auth/logout nem esvazia o cofre da nova", async () => {
    const seg = segurar();
    await abrirExcluir({ [DELETE]: async () => { await seg.promessa; return resposta(200, { status: "deleted" }); } });
    semearLayouts();
    const alerta = responderAlerta("Excluir");

    await excluir();
    await guardarCredenciais({ access: "access-b", refresh: "rt_b" });
    await act(async () => { seg.soltar(); await drenar(); });

    await waitFor(() => expect(layouts(7)).toEqual(PERFIS.map(() => false)));
    expect(semLogoutDeB()).toBe(true);
    expect(alerta).not.toHaveBeenCalledWith(NAO_CONFIRMOU);
    await expect(lerCredenciais()).resolves.toEqual({ access: "access-b", refresh: "rt_b" });
  });

  it("sucesso do DELETE e outra conta entra no cofre enquanto os layouts saem: não desloga nem esvazia o cofre da nova", async () => {
    await abrirExcluir({ [DELETE]: () => resposta(200, { status: "deleted" }) });
    semearLayouts();
    const alerta = responderAlerta("Excluir");
    aoApagarLayouts(() => guardarCredenciais(B));

    await excluir();
    await act(async () => { await drenar(); });

    await waitFor(() => expect(layouts(7)).toEqual(PERFIS.map(() => false)));
    expect(semLogoutDeB()).toBe(true);
    expect(alerta).not.toHaveBeenCalledWith("Conta excluída");
    await expect(lerCredenciais()).resolves.toEqual(B);
    expect(screen.getByRole("button", { name: "Excluir conta" })).toBeTruthy(); // sheet solta, sessão de B de pé
  });

  it("outra conta termina o login DURANTE o /auth/logout: o 'outra' sai do cofre depois da espera, B intacto e a sessão segue autenticada", async () => {
    await abrirExcluir({
      [DELETE]: () => resposta(200, { status: "deleted" }),
      "/auth/logout": async () => { await guardarCredenciais(B); return resposta(200, {}); },
    });
    semearLayouts();
    const alerta = responderAlerta("Excluir");

    await excluir();
    await act(async () => { await drenar(); });

    await waitFor(() => expect(chamadasA("/auth/logout")).toHaveLength(1));
    expect(semLogoutDeB()).toBe(true);
    expect(alerta).not.toHaveBeenCalledWith("Conta excluída");
    await expect(lerCredenciais()).resolves.toEqual(B);
    expect(screen.getByRole("button", { name: "Excluir conta" })).toBeTruthy(); // não foi para Entrar
  });

  it("a MESMA sessão rotaciona o refresh enquanto os layouts saem: sai mesmo assim (jti igual)", async () => {
    await abrirExcluir({ [DELETE]: () => resposta(200, { status: "deleted" }) });
    await guardarCredenciais({ access: ACCESS_A, refresh: "rt_s" });
    semearLayouts();
    const alerta = responderAlerta("Excluir");
    aoApagarLayouts(() => guardarCredenciais({ access: ACCESS_A.replace("assinatura", "outra"), refresh: "rt_s2" }));

    await excluir();
    await act(async () => { await drenar(); });

    await waitFor(() => expect(alerta).toHaveBeenCalledWith("Conta excluída"));
    expect(chamadasA("/auth/logout")).toHaveLength(1);
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("a leitura do cofre falha antes do DELETE e B está no cofre no fim: não sai às cegas, B intacto, Banner Sair", async () => {
    await abrirExcluir({ [DELETE]: async () => { await guardarCredenciais(B); return resposta(200, { status: "deleted" }); } });
    semearLayouts();
    const alerta = responderAlerta("Excluir");
    cofre.has = () => { Reflect.deleteProperty(cofre, "has"); throw new Error("keychain recusou ler"); };

    await excluir();
    await act(async () => { await drenar(); });

    await waitFor(() => expect(screen.getByRole("button", { name: "Sair" })).toBeTruthy());
    expect(semLogoutDeB()).toBe(true);
    expect(alerta).not.toHaveBeenCalledWith("Conta excluída");
    await expect(lerCredenciais()).resolves.toEqual(B);
  });

  it("superada com B no cofre: não desloga B nem avisa", async () => {
    const seg = segurar();
    await abrirExcluir({
      [DELETE]: async () => { await seg.promessa; return resposta(200, { status: "deleted" }); },
      "/auth/refresh": () => resposta(401, { detail: "invalid_refresh_token" }),
      "/paralela": sessaoMorta,
    });
    semearLayouts();
    const alerta = responderAlerta("Excluir");

    await excluir();
    await chamar("/paralela", z.any(), {}).catch(() => undefined);
    await guardarCredenciais(B);
    await act(async () => { seg.soltar(); await drenar(); });

    expect(semLogoutDeB()).toBe(true);
    expect(alerta).not.toHaveBeenCalledWith(NAO_CONFIRMOU);
    await expect(lerCredenciais()).resolves.toEqual(B);
  });
});
