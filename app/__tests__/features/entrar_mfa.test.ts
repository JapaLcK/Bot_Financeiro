/**
 * A máquina M/V de `entrar.ts`: verificar (código MFA), alternar modo, voltar
 * e as falhas — inclusive o desafio que sobrevive a um 429/5xx e o que morre
 * a um 400/404 (o desafio já foi consumido no servidor mesmo com código
 * errado — db/mfa.py:351, defeito conhecido e fora de escopo aqui).
 */
import * as authService from "@/services/auth";
import { FalhaNoCofre } from "@/storage/secure";
import {
  alternarModo,
  tentarDeNovo,
  tocar,
  verificar,
  voltar,
  type EstadoEntrar,
  type EstadoMfa,
} from "@/features/auth/entrar";

import { chamadas, gravador, prepararCaso, resposta, rotear, segurar, type Rota } from "./auth_apoio";

const M: EstadoMfa = { fase: "mfa", desafio: "d-1", email: "m@x.com", modo: "totp" };

beforeEach(prepararCaso);

describe("verificar (M/V)", () => {
  it("200: autentica", async () => {
    rotear({ "/auth/mfa/verify-login": () => resposta(200, { user_id: 1, email: M.email, access_token: "a", refresh_token: "r", dashboard_token: "d", expires_in: 900 }) });
    const { aplicar } = gravador<EstadoEntrar>();
    const autenticar = jest.fn();

    await tocar(() => verificar(M.desafio, M.email, M.modo, "123456", autenticar), aplicar);

    expect(autenticar).toHaveBeenCalledTimes(1);
  });

  it.each<[string, Rota, string]>([
    ["400", () => resposta(400, { detail: "Código inválido." }), "Código inválido. Entre de novo."],
    ["404", () => resposta(404, { detail: "Usuário não encontrado." }), "Usuário não encontrado. Entre de novo."],
  ])("%s: o desafio morreu no servidor — volta ao FORMULÁRIO, não fica em M", async (_nome, verify, aviso) => {
    rotear({ "/auth/mfa/verify-login": verify });
    const { aplicados, aplicar } = gravador<EstadoEntrar>();

    await tocar(() => verificar(M.desafio, M.email, M.modo, "000000", jest.fn()), aplicar);

    expect(aplicados).toEqual([{ fase: "formulario", aviso }]);
  });

  it("429: o desafio continua vivo — fica em M com o detalhe do servidor", async () => {
    rotear({ "/auth/mfa/verify-login": () => resposta(429, { detail: "Muitas tentativas. Tente de novo em instantes." }) });
    const { aplicados, aplicar } = gravador<EstadoEntrar>();

    await tocar(() => verificar(M.desafio, M.email, M.modo, "000000", jest.fn()), aplicar);

    expect(aplicados).toEqual([{ ...M, aviso: "Muitas tentativas. Tente de novo em instantes." }]);
  });

  it("5xx/rede: o desafio continua vivo, aviso genérico", async () => {
    rotear({ "/auth/mfa/verify-login": () => Promise.reject(new TypeError("Network request failed")) });
    const { aplicados, aplicar } = gravador<EstadoEntrar>();

    await tocar(() => verificar(M.desafio, M.email, M.modo, "000000", jest.fn()), aplicar);

    expect(aplicados).toEqual([{ ...M, aviso: "Algo deu errado. Tente de novo." }]);
  });

  it("200 com forma errada (ContratoInvalido): abandona o MFA, volta ao formulário genérico", async () => {
    rotear({ "/auth/mfa/verify-login": () => resposta(200, { ok: true }) });
    const { aplicados, aplicar } = gravador<EstadoEntrar>();

    await tocar(() => verificar(M.desafio, M.email, M.modo, "000000", jest.fn()), aplicar);

    expect(aplicados).toEqual([{ fase: "formulario", aviso: "Algo deu errado. Tente de novo." }]);
  });

  it("FalhaNoCofre: vira erro-cofre (mesmo raciocínio do teste equivalente em entrar.test.ts)", async () => {
    jest.spyOn(authService, "verificarMfa").mockRejectedValueOnce(new FalhaNoCofre(new Error("keychain recusou")));
    const { aplicados, aplicar } = gravador<EstadoEntrar>();

    await tocar(() => verificar(M.desafio, M.email, M.modo, "000000", jest.fn()), aplicar);

    expect(aplicados).toEqual([{ fase: "erro-cofre" }]);
  });

  it("código colado com espaço no meio ('123 456') chega sem espaço ao servidor", async () => {
    let corpoEnviado: unknown;
    rotear({
      "/auth/mfa/verify-login": (o) => {
        corpoEnviado = JSON.parse(String(o.body));
        return resposta(200, { user_id: 1, email: M.email, access_token: "a", refresh_token: "r", dashboard_token: "d", expires_in: 900 });
      },
    });
    const { aplicar } = gravador<EstadoEntrar>();

    await tocar(() => verificar(M.desafio, M.email, "totp", "123 456", jest.fn()), aplicar);

    expect(corpoEnviado).toMatchObject({ code: "123456", use_backup: false });
  });

  it.each(["BACKUP-01", "backup-01"])("código de backup %s vai com use_backup=true, sem alteração de caixa", async (codigo) => {
    let corpoEnviado: unknown;
    rotear({
      "/auth/mfa/verify-login": (o) => {
        corpoEnviado = JSON.parse(String(o.body));
        return resposta(400, { detail: "Código inválido." });
      },
    });
    const { aplicar } = gravador<EstadoEntrar>();

    await tocar(() => verificar(M.desafio, M.email, "backup", codigo, jest.fn()), aplicar);

    expect(corpoEnviado).toMatchObject({ code: codigo, use_backup: true });
  });

  it("dígito não-ASCII no código: não quebra, só vira 400 do servidor (regra de negócio, não parsing local)", async () => {
    rotear({ "/auth/mfa/verify-login": () => resposta(400, { detail: "Código inválido." }) });
    const { aplicados, aplicar } = gravador<EstadoEntrar>();

    // "١٢٣٤٥٦" — dígitos arábico-índicos, 6 caracteres.
    await tocar(() => verificar(M.desafio, M.email, "totp", "١٢٣٤٥٦", jest.fn()), aplicar);

    expect(aplicados).toEqual([{ fase: "formulario", aviso: "Código inválido. Entre de novo." }]);
  });

  describe("duplo envio do MFA (controle negativo e positivo)", () => {
    it("CONTROLE NEGATIVO — sem a guarda de `tocar`, o auto-envio e o toque em Verificar disparam DUAS requisições, e a segunda toma 'expirada' mesmo com o código certo", async () => {
      rotear({
        "/auth/mfa/verify-login": () => resposta(400, { detail: "Sessão MFA expirada. Faça login novamente." }),
      });
      // Chama `verificar` DIRETO, sem `tocar()`: as duas "origens" (auto-envio
      // do 6º dígito e o toque em Verificar) do componente real chamam a MESMA
      // função — é isso que este teste desliga de propósito.
      await Promise.allSettled([
        verificar(M.desafio, M.email, M.modo, "123456", jest.fn()),
        verificar(M.desafio, M.email, M.modo, "123456", jest.fn()),
      ]);

      expect(chamadas().filter((c) => c.caminho === "/auth/mfa/verify-login")).toHaveLength(2);
    });

    it("CONTROLE POSITIVO — com `tocar()`, envio legítimo autentica com UMA requisição só", async () => {
      const chegou = segurar();
      const portao = segurar();
      rotear({
        "/auth/mfa/verify-login": async () => {
          chegou.soltar();
          await portao.promessa;
          return resposta(200, { user_id: 1, email: M.email, access_token: "a", refresh_token: "r", dashboard_token: "d", expires_in: 900 });
        },
      });
      const { aplicar } = gravador<EstadoEntrar>();
      const autenticar = jest.fn();

      // O auto-envio do 6º dígito...
      const autoEnvio = tocar(() => verificar(M.desafio, M.email, M.modo, "123456", autenticar), aplicar);
      await chegou.promessa;
      // ...coincidindo com o toque em "Verificar" — a MESMA chamada, ação já em voo.
      const toqueNoBotao = tocar(() => verificar(M.desafio, M.email, M.modo, "123456", autenticar), aplicar);
      portao.soltar();
      await Promise.all([autoEnvio, toqueNoBotao]);

      expect(chamadas().filter((c) => c.caminho === "/auth/mfa/verify-login")).toHaveLength(1);
      expect(autenticar).toHaveBeenCalledTimes(1);
    });
  });
});

describe("alternarModo (M -> M)", () => {
  it("troca totp <-> backup mantendo desafio e e-mail", () => {
    expect(alternarModo(M)).toEqual({ ...M, modo: "backup" });
    expect(alternarModo({ ...M, modo: "backup" })).toEqual({ ...M, modo: "totp" });
  });

  it("limpa um aviso anterior ao trocar de modo", () => {
    expect(alternarModo({ ...M, aviso: "Código inválido." })).toEqual({ ...M, modo: "backup" });
  });
});

describe("voltar (M -> F) e tentarDeNovo (X -> F)", () => {
  it("voltar descarta o desafio, sem aviso", () => {
    expect(voltar()).toEqual({ fase: "formulario" });
  });

  it("tentarDeNovo só devolve o formulário — nenhuma operação é refeita sozinha", () => {
    expect(tentarDeNovo()).toEqual({ fase: "formulario" });
  });
});
