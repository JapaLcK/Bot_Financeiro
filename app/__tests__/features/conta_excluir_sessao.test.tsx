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
    expect(chamadasA("/auth/logout")).toEqual([]);
    expect(alerta).not.toHaveBeenCalledWith(NAO_CONFIRMOU);
    await expect(lerCredenciais()).resolves.toEqual({ access: "access-b", refresh: "rt_b" });
  });
});
