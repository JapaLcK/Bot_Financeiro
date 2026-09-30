/**
 * A tabela da trava (`features/bloqueio/maquina.ts`), linha a linha, sem
 * React, sem relógio e sem AppState: cada caso é um estado de partida, uma
 * sequência de eventos com o horário de cada um, e o que tem de sair.
 */
import { inicial, transicao, type Efeito, type Estado, type Evento } from "@/features/bloqueio/maquina";

const T0 = 1_000_000;

/** Aplica os eventos em ordem, acumulando os efeitos. `[evento, agora]`. */
function rodar(de: Estado, passos: [Evento, number?][]) {
  let estado = de;
  const efeitos: Efeito[] = [];
  for (const [ev, agora] of passos) {
    const r = transicao(estado, ev, agora ?? T0);
    estado = r.estado;
    efeitos.push(...r.efeitos);
  }
  return { estado, efeitos, pedidos: efeitos.filter((e) => e.tipo === "pedir").length };
}

const app = (valor: "active" | "inactive" | "background"): Evento => ({ tipo: "app", valor });

/** Boot com sessão salva, trava ligada e aparelho com Face ID: prompt em voo. */
function travadoNoBoot(): Estado {
  return rodar(inicial(true), [[{ tipo: "boot" }], [{ tipo: "leu", geracao: 1, desligada: false, semCodigo: false }]]).estado;
}

/** Já liberado (pilha montada), trava ligada. */
function livre(): Estado {
  return rodar(travadoNoBoot(), [[{ tipo: "resultado", geracao: 1, liberou: true }]]).estado;
}

/** Sai para o fundo em T0 e volta `ms` depois. */
function foraPor(de: Estado, ms: number) {
  return rodar(de, [[app("inactive"), T0], [app("background"), T0], [app("active"), T0 + ms]]);
}

describe("maquina — sessão", () => {
  it("boot (verificando → autenticado): lendo, efeito ler, geração nova", () => {
    const r = rodar(inicial(true), [[{ tipo: "boot" }]]);
    expect(r.estado).toMatchObject({ fase: "lendo", geracao: 1 });
    expect(r.efeitos).toEqual([{ tipo: "ler", geracao: 1 }]);
  });

  it("login (anonimo → autenticado): livre, jaLiberou, SEM prompt; só lê a preferência", () => {
    const r = rodar(inicial(true), [[{ tipo: "login" }]]);
    expect(r.estado).toMatchObject({ fase: "livre", jaLiberou: true, preferencia: "ligada" });
    expect(r.pedidos).toBe(0);
    expect(r.efeitos).toEqual([{ tipo: "ler", geracao: 1 }]);
  });

  it("inerte: AppState só atualiza appAtivo", () => {
    const r = rodar(inicial(true), [[app("background")]]);
    expect(r.estado).toEqual({ ...inicial(false) });
  });

  it("saiu (sair/expirou) de qualquer fase: inerte, zerado, geração nova", () => {
    const r = rodar(travadoNoBoot(), [[{ tipo: "saiu" }]]);
    expect(r.estado).toEqual({ ...inicial(true), geracao: 2 });
  });

  it("sucesso de um prompt da sessão anterior não abre a trava da sessão nova (geração)", () => {
    const novaSessao = rodar(travadoNoBoot(), [[{ tipo: "saiu" }], [{ tipo: "login" }]]).estado;
    const travadoDeNovo = foraPor(novaSessao, 61_000).estado;
    expect(travadoDeNovo).toMatchObject({ fase: "travado", geracao: 3 });
    const r = rodar(travadoDeNovo, [[{ tipo: "resultado", geracao: 1, liberou: true }]]);
    expect(r.estado.fase).toBe("travado");
  });
});

describe("maquina — leitura", () => {
  it.each([
    ["preferência desligada não trava", { desligada: true, semCodigo: false }, "livre"],
    ["nível NONE não trava", { desligada: false, semCodigo: true }, "livre"],
    ["ligada e com código trava", { desligada: false, semCodigo: false }, "travado"],
  ] as const)("%s", (_nome, leitura, fase) => {
    const r = rodar(inicial(true), [[{ tipo: "boot" }], [{ tipo: "leu", geracao: 1, ...leitura }]]);
    expect(r.estado.fase).toBe(fase);
    expect(r.pedidos).toBe(fase === "travado" ? 1 : 0);
  });

  it("travado com o app fora de foco: não pede agora, pede no active", () => {
    const r = rodar(inicial(false), [[{ tipo: "boot" }], [{ tipo: "leu", geracao: 1, desligada: false, semCodigo: false }]]);
    expect(r.estado).toMatchObject({ fase: "travado", pedirAoAtivar: true, autenticando: false });
    expect(r.pedidos).toBe(0);
    expect(rodar(r.estado, [[app("active")]]).pedidos).toBe(1);
  });

  it("leitura de outra geração é ignorada", () => {
    const r = rodar(inicial(true), [[{ tipo: "boot" }], [{ tipo: "leu", geracao: 99, desligada: true, semCodigo: false }]]);
    expect(r.estado.fase).toBe("lendo");
  });
});

describe("maquina — travado", () => {
  it("sucesso: livre, jaLiberou", () => {
    expect(livre()).toMatchObject({ fase: "livre", jaLiberou: true, autenticando: false, falhou: false });
  });

  it("falha: continua travado, falhou, sem prompt em voo", () => {
    const r = rodar(travadoNoBoot(), [[{ tipo: "resultado", geracao: 1, liberou: false }]]);
    expect(r.estado).toMatchObject({ fase: "travado", falhou: true, autenticando: false });
  });

  it("inactive depois de cancelar não pede de novo", () => {
    const cancelado = rodar(travadoNoBoot(), [[{ tipo: "resultado", geracao: 1, liberou: false }]]).estado;
    expect(rodar(cancelado, [[app("inactive")], [app("active")]]).pedidos).toBe(0);
  });

  it("background depois de cancelar pede de novo no active", () => {
    const cancelado = rodar(travadoNoBoot(), [[{ tipo: "resultado", geracao: 1, liberou: false }]]).estado;
    expect(rodar(cancelado, [[app("background")], [app("active")]]).pedidos).toBe(1);
  });

  it("o active do próprio prompt (inactive→active com ele em voo) não pede em dobro", () => {
    expect(rodar(travadoNoBoot(), [[app("inactive")], [app("active")]]).pedidos).toBe(0);
  });

  it("background com o prompt em voo: o active não pede em dobro enquanto ele não responder", () => {
    expect(rodar(travadoNoBoot(), [[app("background")], [app("active")]]).pedidos).toBe(0);
  });

  it("tocar em Desbloquear pede; com um prompt em voo, não", () => {
    const cancelado = rodar(travadoNoBoot(), [[{ tipo: "resultado", geracao: 1, liberou: false }]]).estado;
    expect(rodar(cancelado, [[{ tipo: "desbloquear" }], [{ tipo: "desbloquear" }]]).pedidos).toBe(1);
  });
});

describe("maquina — livre, voltando do fundo", () => {
  it("61 s trava, fecha o teclado e pede uma vez", () => {
    const r = foraPor(livre(), 61_000);
    expect(r.estado).toMatchObject({ fase: "travado", jaLiberou: true, autenticando: true, saiuEm: null, foiAoFundo: false });
    // `descobrir` por último: a tampa nativa só sai com a trava já decidida por baixo.
    expect(r.efeitos).toEqual([{ tipo: "fecharTeclado" }, { tipo: "pedir", geracao: 1 }, { tipo: "descobrir" }]);
  });

  it("59 s não trava", () => {
    const r = foraPor(livre(), 59_000);
    expect(r.estado).toMatchObject({ fase: "livre", saiuEm: null, foiAoFundo: false });
    expect(r.efeitos).toEqual([{ tipo: "descobrir" }]);
  });

  it("exatamente 60 s não trava (comparação estrita)", () => {
    expect(foraPor(livre(), 60_000).estado.fase).toBe("livre");
  });

  it("hora voltou (diferença negativa) trava", () => {
    expect(foraPor(livre(), -1).estado.fase).toBe("travado");
  });

  it("Central de Controle 5 min (só inactive) não trava", () => {
    const r = rodar(livre(), [[app("inactive"), T0], [app("active"), T0 + 300_000]]);
    expect(r.estado.fase).toBe("livre");
  });

  it("background processado atrasado ainda trava: o carimbo é do primeiro inactive", () => {
    const r = rodar(livre(), [[app("inactive"), T0], [app("background"), T0 + 59_000], [app("active"), T0 + 61_000]]);
    expect(r.estado.fase).toBe("travado");
  });

  it("preferência desligada não trava", () => {
    const desligada = rodar(livre(), [[{ tipo: "preferencia", valor: "desligada" }]]).estado;
    expect(foraPor(desligada, 600_000).estado.fase).toBe("livre");
  });

  it("login não trava na primeira volta rápida, e a leitura da preferência vale depois", () => {
    const logado = rodar(inicial(true), [[{ tipo: "login" }], [{ tipo: "leu", geracao: 1, desligada: true, semCodigo: false }]]).estado;
    expect(logado.preferencia).toBe("desligada");
    expect(foraPor(logado, 600_000).estado.fase).toBe("livre");
  });

  it("inactive/background marcam o app fora de foco e carimbam a saída", () => {
    expect(rodar(livre(), [[app("inactive")]]).estado).toMatchObject({ fase: "livre", appAtivo: false, saiuEm: T0 });
  });
});

describe("maquina — tampa nativa", () => {
  const partidas: [string, () => Estado][] = [
    ["inerte", () => inicial(false)],
    ["lendo", () => rodar(inicial(false), [[{ tipo: "boot" }]]).estado],
    ["travado", () => ({ ...travadoNoBoot(), appAtivo: false })],
    ["livre", () => ({ ...livre(), appAtivo: false })],
  ];

  it.each(partidas)("%s: todo active manda descobrir", (_fase, de) => {
    expect(transicao(de(), app("active"), T0).efeitos).toContainEqual({ tipo: "descobrir" });
  });

  it.each(partidas)("%s: inactive e background não descobrem", (_fase, de) => {
    for (const valor of ["inactive", "background"] as const) {
      expect(transicao(de(), app(valor), T0).efeitos).not.toContainEqual({ tipo: "descobrir" });
    }
  });

  it("active com appAtivo já verdadeiro (inactive+active no mesmo render) também descobre", () => {
    expect(transicao(livre(), app("active"), T0).efeitos).toContainEqual({ tipo: "descobrir" });
  });
});
