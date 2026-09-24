/**
 * A máquina M/V de `entrar.ts`: verificar (código MFA), alternar modo, voltar
 * e as falhas — inclusive o desafio que sobrevive a um 429 e a um código
 * errado (`mfa_code_invalid`: gasta uma das 5 tentativas do desafio, que
 * continua vivo) e o que morre a um 400 `mfa_challenge_expired`/404.
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

  it("400 mfa_code_invalid: código errado com tentativas sobrando — fica em M, mesmo desafio, com o detalhe do servidor", async () => {
    rotear({ "/auth/mfa/verify-login": () => resposta(400, { detail: "Código inválido.", code: "mfa_code_invalid" }) });
    const { aplicados, aplicar } = gravador<EstadoEntrar>();

    await tocar(() => verificar(M.desafio, M.email, M.modo, "000000", jest.fn()), aplicar);

    expect(aplicados).toEqual([{ ...M, aviso: "Código inválido." }]);
  });

  it.each<[string, Rota, string]>([
    ["400 sem code (backend antes do #524)", () => resposta(400, { detail: "Código inválido." }), "Código inválido. Entre de novo."],
    ["404", () => resposta(404, { detail: "Usuário não encontrado." }), "Usuário não encontrado. Entre de novo."],
    ["400 mfa_challenge_expired (expirado)", () => resposta(400, { detail: "Sessão MFA expirada. Faça login novamente.", code: "mfa_challenge_expired" }), "Sessão MFA expirada. Faça login novamente. Entre de novo."],
    ["400 mfa_challenge_expired (5ª tentativa)", () => resposta(400, { detail: "Muitas tentativas. Faça login novamente.", code: "mfa_challenge_expired" }), "Muitas tentativas. Faça login novamente. Entre de novo."],
    ["400 com code desconhecido", () => resposta(400, { detail: "Código inválido.", code: "xyz" }), "Código inválido. Entre de novo."],
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

  it("rede sem resposta (fetch rejeita antes de chegar): ambíguo, o desafio continua vivo — fica em M", async () => {
    rotear({ "/auth/mfa/verify-login": () => Promise.reject(new TypeError("Network request failed")) });
    const { aplicados, aplicar } = gravador<EstadoEntrar>();

    await tocar(() => verificar(M.desafio, M.email, M.modo, "000000", jest.fn()), aplicar);

    expect(aplicados).toEqual([{ ...M, aviso: "Algo deu errado. Tente de novo." }]);
  });

  it("500 (apontamento Codex #3): o que o servidor fez com o desafio é ambíguo — volta ao FORMULÁRIO", async () => {
    rotear({ "/auth/mfa/verify-login": () => resposta(500, { detail: "boom, traceback cru" }) });
    const { aplicados, aplicar } = gravador<EstadoEntrar>();

    await tocar(() => verificar(M.desafio, M.email, M.modo, "000000", jest.fn()), aplicar);

    // client.ts força a mensagem genérica em qualquer 5xx (nunca o traceback cru).
    expect(aplicados).toEqual([
      { fase: "formulario", aviso: "Tivemos um problema aqui. Tente de novo em instantes. Entre de novo." },
    ]);
  });

  it("tempo limite (AbortError do comLimite, 15s): ambíguo — volta ao FORMULÁRIO", async () => {
    rotear({ "/auth/mfa/verify-login": () => Promise.reject(new DOMException("The operation was aborted.", "AbortError")) });
    const { aplicados, aplicar } = gravador<EstadoEntrar>();

    await tocar(() => verificar(M.desafio, M.email, M.modo, "000000", jest.fn()), aplicar);

    expect(aplicados).toEqual([{ fase: "formulario", aviso: "Algo deu errado. Tente de novo. Entre de novo." }]);
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

  describe("Voltar durante uma verificação em voo (B2 — geração + abandono invalidam o resultado tardio)", () => {
    it.each<[string, () => Response]>([
      ["429", () => resposta(429, { detail: "Muitas tentativas." })],
      ["400 mfa_code_invalid", () => resposta(400, { detail: "Código inválido.", code: "mfa_code_invalid" })],
    ])("CONTROLE POSITIVO — %s chega DEPOIS do Voltar: a fase mfa não ressuscita, fica formulário", async (_nome, falha) => {
      const portao = segurar();
      rotear({
        "/auth/mfa/verify-login": async () => {
          await portao.promessa;
          return falha();
        },
      });
      const { aplicados, aplicar } = gravador<EstadoEntrar>();

      const emVoo = tocar(() => verificar(M.desafio, M.email, M.modo, "000000", jest.fn()), aplicar);
      // Mesma chamada que o botão "Voltar" do CodigoMfa faz — por fora da fila.
      aplicar(voltar());
      portao.soltar();
      await emVoo;

      // Só o `voltar()` chegou a `aplicados`; a falha tardia nunca voltou a mostrar M.
      expect(aplicados).toEqual([{ fase: "formulario" }]);
    });

    // Não há "CONTROLE NEGATIVO" aqui chamando `verificar()` direto: essa
    // versão foi removida porque não discriminava nada — `verificar()`
    // sozinho nunca passa pela checagem de geração de `enfileirar` (ela vive
    // em `entrar.ts`, não em `verificar()`), então aplicar o resultado à mão
    // "sem checar geração" dá o mesmo resultado com ou sem a guarda real no
    // código (CLAUDE.md §3: "se o resultado sai igual com e sem o conserto,
    // o grupo não mede nada").
    //
    // A verificação de que a guarda importa é MANUAL, e precisa desligar as
    // DUAS proteções ao mesmo tempo — hoje elas se sobrepõem para este
    // cenário: `abandonarEntrada()` (chamada por `voltar()`) já faz a falha
    // tardia virar `EntradaSuperada` nos services ANTES de `enfileirar`
    // sequer olhar a geração, então desligar só uma das duas não basta para
    // ver vermelho. Comentar (a) `geracao === minhaGeracao` em `enfileirar`
    // E (b) a chamada a `abandonarEntrada()` dentro de `voltar()`, ao mesmo
    // tempo, e rodar o teste ACIMA — aí sim os dois casos ficam vermelhos
    // (aplicados termina com `{...M, aviso: <detalhe>}` por cima do
    // formulário). Restaurar os dois devolve o verde.
    //
    // Verificado manualmente (também para o caso `mfa_code_invalid`):
    // desligando só uma das duas, os dois casos seguem verdes; as DUAS, os
    // dois ficam vermelhos; restaurando, voltam a passar.
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
