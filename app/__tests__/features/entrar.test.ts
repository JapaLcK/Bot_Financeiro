/**
 * A máquina F/E de `entrar.ts`: enviar (login), as falhas e o toque duplo.
 * A fase M/V (código MFA) fica em `entrar_mfa.test.ts`.
 *
 * Com os serviços reais (`services/auth.ts`, `api/client.ts`), `fetch` falso
 * e o dublê do cofre — mesma receita de `inicio.test.ts` (Fase 1, removido).
 */
import * as authService from "@/services/auth";
import { FalhaNoCofre, lerCredenciais } from "@/storage/secure";
import { enviar, tocar, type EstadoEntrar } from "@/features/auth/entrar";

import {
  GENERICO,
  chamadas,
  credencialDe,
  gravador,
  prepararCaso,
  resposta,
  rotear,
  segurar,
  type Rota,
} from "./auth_apoio";

beforeEach(prepararCaso);

describe("enviar (F/E)", () => {
  it("200 sem MFA: autentica e grava a credencial (e-mail aparado, senha crua)", async () => {
    rotear();
    const { aplicar } = gravador<EstadoEntrar>();
    const autenticar = jest.fn();

    await tocar(() => enviar(" ana@x.com ", " s3nha ", autenticar), aplicar);

    expect(autenticar).toHaveBeenCalledTimes(1);
    const [login] = chamadas();
    expect(login).toMatchObject({ caminho: "/auth/login", corpo: { email: "ana@x.com", password: " s3nha " } });
    await expect(lerCredenciais()).resolves.toEqual({ access: "access-ana", refresh: "rt_ana" });
  });

  it("200 com mfa_required: vira fase mfa em modo totp, sem gravar nada", async () => {
    rotear({
      "/auth/login": () => resposta(200, { mfa_required: true, mfa_challenge: "d-1", email: "m@x.com" }),
    });
    const { aplicados, aplicar } = gravador<EstadoEntrar>();
    const autenticar = jest.fn();

    await tocar(() => enviar("m@x.com", "s", autenticar), aplicar);

    expect(aplicados).toEqual([{ fase: "mfa", desafio: "d-1", email: "m@x.com", modo: "totp" }]);
    expect(autenticar).not.toHaveBeenCalled();
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it.each<[string, Rota, string]>([
    ["401", () => resposta(401, { detail: "E-mail ou senha incorretos." }), "E-mail ou senha incorretos."],
    ["429", () => resposta(429, { detail: "Muitas tentativas. Aguarde alguns minutos e tente novamente." }), "Muitas tentativas. Aguarde alguns minutos e tente novamente."],
    ["403 (outro 4xx)", () => resposta(403, { detail: "Conta agendada para exclusão." }), "Conta agendada para exclusão."],
  ])("%s: volta ao formulário com o detalhe do servidor", async (_nome, login, aviso) => {
    rotear({ "/auth/login": login });
    const { aplicados, aplicar } = gravador<EstadoEntrar>();

    await tocar(() => enviar("a@x.com", "s", jest.fn()), aplicar);

    expect(aplicados).toEqual([{ fase: "formulario", aviso }]);
  });

  it("500: nunca o corpo cru do servidor (client.ts já troca por uma mensagem segura antes do `detail`)", async () => {
    rotear({ "/auth/login": () => resposta(500, { detail: "psycopg.OperationalError: boom" }) });
    const { aplicados, aplicar } = gravador<EstadoEntrar>();

    await tocar(() => enviar("a@x.com", "s", jest.fn()), aplicar);

    expect(aplicados).toEqual([
      { fase: "formulario", aviso: "Tivemos um problema aqui. Tente de novo em instantes." },
    ]);
  });

  it.each<[string, Rota]>([
    ["rede fora", () => Promise.reject(new TypeError("Network request failed"))],
    ["200 com forma errada (ContratoInvalido)", () => resposta(200, { token: "x" })],
  ])("%s: aviso genérico", async (_nome, login) => {
    rotear({ "/auth/login": login });
    const { aplicados, aplicar } = gravador<EstadoEntrar>();

    await tocar(() => enviar("a@x.com", "s", jest.fn()), aplicar);

    expect(aplicados).toEqual([{ fase: "formulario", aviso: GENERICO }]);
  });

  it("FalhaNoCofre: vira erro-cofre", async () => {
    // O cofre ficar com o estado da sessão DESCONHECIDO (`FalhaNoCofre`) é uma
    // corrida real em `services/auth.ts` (gravação vence, uma tentativa mais
    // nova assume, e o DESFAZER falha) — a mesma classe de corrida que o
    // `sair()` de lá já documenta como cara demais de reproduzir sem medir o
    // andaime (comentário `ponytail` em `services/auth.ts`). O que este teste
    // cobre é O MAPEAMENTO em `entrar.ts` (linha "E+FalhaNoCofre→X" da
    // tabela): dado que o serviço lança `FalhaNoCofre`, a tela vai para X.
    jest.spyOn(authService, "entrar").mockRejectedValueOnce(new FalhaNoCofre(new Error("keychain recusou")));
    const { aplicados, aplicar } = gravador<EstadoEntrar>();

    await tocar(() => enviar("a@x.com", "s", jest.fn()), aplicar);

    expect(aplicados).toEqual([{ fase: "erro-cofre" }]);
  });

  it("EntradaSuperada (outra tentativa mais nova venceu): nada é aplicado", async () => {
    jest.spyOn(authService, "entrar").mockRejectedValueOnce(new authService.EntradaSuperada());
    const { aplicados, aplicar } = gravador<EstadoEntrar>();
    const autenticar = jest.fn();

    await tocar(() => enviar("a@x.com", "s", autenticar), aplicar);

    expect(aplicados).toEqual([]);
    expect(autenticar).not.toHaveBeenCalled();
  });

  describe("toque duplo em Entrar (controle negativo e positivo)", () => {
    it("CONTROLE NEGATIVO — sem a guarda de `tocar`, dois toques disparam DOIS logins", async () => {
      rotear();
      const { aplicar } = gravador<EstadoEntrar>();
      const autenticar = jest.fn();

      // Chama `enviar` DIRETO, sem passar por `tocar()`: é a guarda que este
      // teste desliga de propósito, para provar que ela é necessária.
      await Promise.all([
        enviar("a@x.com", "s1", autenticar).then((e) => e && aplicar(e)),
        enviar("a@x.com", "s2", autenticar).then((e) => e && aplicar(e)),
      ]);

      expect(chamadas().filter((c) => c.caminho === "/auth/login")).toHaveLength(2);
    });

    it("CONTROLE POSITIVO — com `tocar()`, o segundo toque (ação em voo) é ignorado: UM login só", async () => {
      const chegou = segurar();
      const portao = segurar();
      rotear({
        "/auth/login": async (o) => {
          chegou.soltar();
          await portao.promessa;
          const { email } = JSON.parse(String(o.body)) as { email: string };
          return resposta(200, credencialDe(email));
        },
      });
      const { aplicar } = gravador<EstadoEntrar>();
      const autenticar = jest.fn();

      const primeiro = tocar(() => enviar("a@x.com", "s", autenticar), aplicar);
      await chegou.promessa;
      // Segundo toque com o primeiro ainda em voo — deve ser ignorado.
      const segundo = tocar(() => enviar("a@x.com", "s", autenticar), aplicar);
      portao.soltar();
      await Promise.all([primeiro, segundo]);

      expect(chamadas().filter((c) => c.caminho === "/auth/login")).toHaveLength(1);
      expect(autenticar).toHaveBeenCalledTimes(1);
    });
  });

  it("sai da tela em voo: a credencial é gravada e `autenticar` roda mesmo sem `aplicar` sendo usado depois", async () => {
    rotear();
    const autenticar = jest.fn();
    // Ninguém chama `aplicar` daqui pra frente — simula a tela desmontada: a
    // promise de `enviar` continua e ainda assim autentica.
    await enviar("a@x.com", "s", autenticar);
    expect(autenticar).toHaveBeenCalledTimes(1);
    await expect(lerCredenciais()).resolves.toEqual({ access: "access-a", refresh: "rt_a" });
  });
});
