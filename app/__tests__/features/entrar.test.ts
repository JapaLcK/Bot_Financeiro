/**
 * A máquina F/E de `entrar.ts`: enviar (login), as falhas e o toque duplo.
 * A fase M/V (código MFA) fica em `entrar_mfa.test.ts`.
 *
 * Com os serviços reais (`services/auth.ts`, `api/client.ts`), `fetch` falso
 * e o dublê do cofre — mesma receita de `inicio.test.ts` (Fase 1, removido).
 */
import { TEMPO_LIMITE_AUTH_MS } from "@/api/client";
import * as authService from "@/services/auth";
import { FalhaNoCofre, lerCredenciais } from "@/storage/secure";
import { apagaSenhaNaFase, enviar, tocar, verificar, voltar, type EstadoEntrar } from "@/features/auth/entrar";

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

  it("CONTROLE POSITIVO — 401 e depois senha certa: a segunda tentativa sai e autentica (a guarda não trava a fila num erro)", async () => {
    let chamadasLogin = 0;
    rotear({
      "/auth/login": (o) => {
        chamadasLogin += 1;
        const { password, email } = JSON.parse(String(o.body)) as { password: string; email: string };
        return password === "certa"
          ? resposta(200, credencialDe(email))
          : resposta(401, { detail: "E-mail ou senha incorretos." });
      },
    });
    const { aplicados, aplicar } = gravador<EstadoEntrar>();
    const autenticar = jest.fn();

    await tocar(() => enviar("a@x.com", "errada", autenticar), aplicar);
    expect(aplicados).toEqual([{ fase: "formulario", aviso: "E-mail ou senha incorretos." }]);

    await tocar(() => enviar("a@x.com", "certa", autenticar), aplicar);

    expect(chamadasLogin).toBe(2);
    expect(autenticar).toHaveBeenCalledTimes(1);
    await expect(lerCredenciais()).resolves.toEqual({ access: "access-a", refresh: "rt_a" });
  });
});

/**
 * I1: um `verify`/`login` que nunca responde (nem sucesso, nem erro) não pode
 * travar a fila (`emVoo` de `entrar.ts`) para sempre — outra ação (login de
 * outra conta) precisa conseguir rodar.
 *
 * Dois jeitos de isso acontecer, com desfechos diferentes:
 * - SEM abandono explícito (ninguém tocou "Voltar"): só o tempo limite de
 *   `comLimite()` (15s) resolve, abortando a requisição.
 * - COM "Voltar" (I-A/I-B): `abandonar()`/`abandonarEntrada()` liberam a
 *   fila NA HORA, sem esperar nada — é o conserto desta rodada.
 */
describe("timeout de auth (I1): fetch pendurado não trava a fila para sempre", () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  it("sem Voltar: só o tempo limite de 15s libera a fila (não há abandono, então não há atalho)", async () => {
    rotear({
      // Simula o `fetch` real: só resolve/rejeita quando o `AbortSignal` que
      // `comLimite()` amarrou em `sinal` dispara — sem isto o dublê de fetch
      // (que ignora `signal` por padrão) nunca reagiria ao timeout.
      "/auth/mfa/verify-login": (o) =>
        new Promise((_resolve, reject) => {
          o.signal?.addEventListener("abort", () => reject(new DOMException("Abortado", "AbortError")));
        }),
    });
    const { aplicados, aplicar } = gravador<EstadoEntrar>();
    const autenticarVelho = jest.fn();

    const pendurado = tocar(() => verificar("d-1", "velho@x.com", "totp", "000000", autenticarVelho), aplicar);
    // Um segundo toque AGORA (sem Voltar) é engolido — a ação anterior ainda
    // está "em voo" e ninguém a abandonou; o login da conta nova nunca chega
    // a sair (só a verify pendurada disparou, de forma síncrona).
    const engolido = tocar(() => enviar("nova@x.com", "s", jest.fn()), aplicar);
    expect(chamadas().filter((c) => c.caminho === "/auth/login")).toHaveLength(0);

    await jest.advanceTimersByTimeAsync(TEMPO_LIMITE_AUTH_MS);
    await Promise.all([pendurado, engolido]);

    // Sem abandono, o timeout do `comLimite()` (AbortError, apontamento Codex
    // #3) é ambíguo: a requisição provavelmente chegou ao servidor, e o que
    // ela fez com o desafio não se sabe, então a tela volta ao FORMULÁRIO —
    // nunca autentica sozinha, e nunca prende a pessoa num `mfa` com desafio
    // que pode estar morto.
    expect(autenticarVelho).not.toHaveBeenCalled();
    expect(aplicados.at(-1)).toEqual({ fase: "formulario", aviso: `${GENERICO} Entre de novo.` });
  });

  it("CONTROLE POSITIVO (I-A/I-B) — verify pendurado; Voltar; a fila libera NA HORA (sem esperar o timeout), e a conta NOVA autentica sem tocar a antiga", async () => {
    rotear({
      "/auth/login": (o) => resposta(200, credencialDe((JSON.parse(String(o.body)) as { email: string }).email)),
      "/auth/mfa/verify-login": (o) =>
        new Promise((_resolve, reject) => {
          o.signal?.addEventListener("abort", () => reject(new DOMException("Abortado", "AbortError")));
        }),
    });
    const { aplicados, aplicar } = gravador<EstadoEntrar>();
    const autenticarVelho = jest.fn();
    const autenticarNovo = jest.fn();

    const pendurado = tocar(() => verificar("d-1", "velho@x.com", "totp", "000000", autenticarVelho), aplicar);
    // Mesma ação do botão "Voltar" — abandona a verificação em voo.
    aplicar(voltar());

    // Toque em "Entrar" para outra conta, no MESMO tick (sem avançar
    // timer nenhum): antes do conserto isto era engolido até o timeout de
    // 15s; agora `emVoo` já foi liberado por `voltar()`, e ENFILEIRA de
    // verdade.
    const novo = tocar(() => enviar("nova@x.com", "s", autenticarNovo), aplicar);
    await Promise.all([pendurado, novo]);

    expect(chamadas().filter((c) => c.caminho === "/auth/login" && c.corpo.email === "nova@x.com")).toHaveLength(1);
    expect(autenticarVelho).not.toHaveBeenCalled();
    expect(autenticarNovo).toHaveBeenCalledTimes(1);
    // A credencial da conta NOVA está lá — a resposta abandonada do verify
    // (abortada) nunca chegou a gravar nada por cima.
    await expect(lerCredenciais()).resolves.toEqual({ access: "access-nova", refresh: "rt_nova" });
    // E nunca voltou a aparecer como M na tela.
    expect(aplicados.some((e) => e.fase === "mfa")).toBe(false);
  });
});

/**
 * `abandonarEntrada()` (services/auth.ts), isolada da fila de `entrar.ts`:
 * o par POSITIVO/NEGATIVO abaixo muda só ISSO — chamar ou não
 * `abandonarEntrada()` no meio de uma tentativa em voo — com o resto do
 * cenário idêntico, para provar que é ELA quem faz a resposta atrasada virar
 * `EntradaSuperada` em vez de gravar a credencial por cima da nova conta.
 */
describe("abandonarEntrada() (services/auth.ts)", () => {
  it("CONTROLE NEGATIVO — sem abandonarEntrada(), uma resposta atrasada grava normalmente", async () => {
    const portao = segurar();
    rotear({ "/auth/login": async () => { await portao.promessa; return resposta(200, credencialDe("ana@x.com")); } });

    const p = authService.entrar("ana@x.com", "s").then(
      () => "ok",
      (e: Error) => e.name,
    );
    portao.soltar();

    expect(await p).toBe("ok");
    await expect(lerCredenciais()).resolves.toEqual({ access: "access-ana", refresh: "rt_ana" });
  });

  it("CONTROLE POSITIVO — com abandonarEntrada() no meio, a MESMA resposta atrasada vira EntradaSuperada e não grava nada", async () => {
    const portao = segurar();
    rotear({ "/auth/login": async () => { await portao.promessa; return resposta(200, credencialDe("ana@x.com")); } });

    const p = authService.entrar("ana@x.com", "s").then(
      () => "ok",
      (e: Error) => e.name,
    );
    authService.abandonarEntrada();
    portao.soltar();

    expect(await p).toBe("EntradaSuperada");
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("aborta a requisição em voo — não fica pendurada até o tempo limite de 15s", async () => {
    rotear({
      "/auth/login": (o) =>
        new Promise((_resolve, reject) => {
          o.signal?.addEventListener("abort", () => reject(new DOMException("Abortado", "AbortError")));
        }),
    });

    const p = authService.entrar("ana@x.com", "s").then(
      () => "ok",
      (e: Error) => e.name,
    );
    await Promise.resolve();
    authService.abandonarEntrada();

    // Resolve pelo ABORT, sem avançar nenhum timer — se `abandonarEntrada()`
    // não abortasse de verdade, esta promise ficaria pendurada e o teste
    // estouraria o tempo limite do Jest, não devolveria "EntradaSuperada".
    expect(await p).toBe("EntradaSuperada");
  });
});

describe("apagaSenhaNaFase", () => {
  // Tabela escrita à mão, não copiada do módulo: `Record` obriga a decidir
  // aqui também quando uma fase nova aparecer.
  const esperado: Record<EstadoEntrar["fase"], boolean> = {
    formulario: true,
    "erro-cofre": true,
    enviando: false,
    mfa: false, // o objetivo do PR: o campo sai da tela preenchido
    verificando: false,
  };

  it.each(Object.entries(esperado) as [EstadoEntrar["fase"], boolean][])("%s → %s", (fase, apaga) => {
    expect(apagaSenhaNaFase(fase)).toBe(apaga);
  });
});
