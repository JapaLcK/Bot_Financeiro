/**
 * A tabela da trava biométrica, como função pura: `transicao(estado, evento,
 * agora)` devolve o próximo estado e os efeitos que o provider executa. Nada de
 * relógio, AppState ou prompt aqui dentro — é isso que deixa cada linha da
 * tabela testável sem aparelho (`__tests__/features/bloqueio_maquina.test.ts`).
 *
 * Só age com sessão `autenticado`; fora dela fica `inerte`.
 */
export const PERIODO_DE_GRACA_MS = 60_000;

export type Fase = "inerte" | "lendo" | "travado" | "livre";
export type EstadoDoApp = "active" | "inactive" | "background";

export interface Estado {
  fase: Fase;
  /** Já liberou alguma vez nesta sessão: a pilha está montada por baixo. */
  jaLiberou: boolean;
  appAtivo: boolean;
  /** Carimbo do PRIMEIRO inactive/background desde o último active. */
  saiuEm: number | null;
  foiAoFundo: boolean;
  /** Houve background com a trava na frente: o próximo active pede de novo. */
  pedirAoAtivar: boolean;
  autenticando: boolean;
  falhou: boolean;
  preferencia: "ligada" | "desligada";
  /** Muda a cada entrada e saída de sessão; resultado de outra geração é descartado. */
  geracao: number;
}

export type Evento =
  | { tipo: "boot" } // sessão verificando → autenticado (abertura com sessão salva)
  | { tipo: "login" } // sessão anonimo → autenticado
  | { tipo: "saiu" } // sessão deixou de ser autenticado (sair, expirou)
  | { tipo: "leu"; geracao: number; desligada: boolean; semCodigo: boolean }
  | { tipo: "app"; valor: EstadoDoApp }
  | { tipo: "desbloquear" }
  | { tipo: "resultado"; geracao: number; liberou: boolean }
  | { tipo: "preferencia"; valor: Estado["preferencia"] };

export type Efeito =
  | { tipo: "ler"; geracao: number }
  | { tipo: "pedir"; geracao: number }
  | { tipo: "fecharTeclado" }
  // Tira a tampa nativa (`tampa.ts`). Vai DEPOIS dos outros: a trava já decidiu o que fica por baixo.
  | { tipo: "descobrir" };

export interface Transicao {
  estado: Estado;
  efeitos: Efeito[];
}

export function inicial(appAtivo: boolean): Estado {
  return {
    fase: "inerte",
    jaLiberou: false,
    appAtivo,
    saiuEm: null,
    foiAoFundo: false,
    pedirAoAtivar: false,
    autenticando: false,
    falhou: false,
    // Falha fechada: até a leitura chegar, vale ligada.
    preferencia: "ligada",
    geracao: 0,
  };
}

/** O prompt começa: marca em voo e consome o pedido pendente. */
function pedir(e: Estado, efeitos: Efeito[] = []): Transicao {
  if (e.autenticando) return { estado: e, efeitos };
  return {
    estado: { ...e, autenticando: true, pedirAoAtivar: false, falhou: false },
    efeitos: [...efeitos, { tipo: "pedir", geracao: e.geracao }],
  };
}

function nada(e: Estado): Transicao {
  return { estado: e, efeitos: [] };
}

export function transicao(e: Estado, ev: Evento, agora: number): Transicao {
  const r = decidir(e, ev, agora);
  // Todo `active`, em qualquer fase — sem olhar `appAtivo`: um inactive e um
  // active no mesmo render deixariam o booleano igual e a tampa presa.
  if (ev.tipo === "app" && ev.valor === "active") return { ...r, efeitos: [...r.efeitos, { tipo: "descobrir" }] };
  return r;
}

function decidir(e: Estado, ev: Evento, agora: number): Transicao {
  if (ev.tipo === "saiu") {
    if (e.fase === "inerte") return nada(e);
    return nada({ ...inicial(e.appAtivo), geracao: e.geracao + 1 });
  }
  if (ev.tipo === "preferencia") return nada({ ...e, preferencia: ev.valor });
  if ((ev.tipo === "leu" || ev.tipo === "resultado") && ev.geracao !== e.geracao) return nada(e);

  switch (e.fase) {
    case "inerte": {
      const geracao = e.geracao + 1;
      if (ev.tipo === "boot") return { estado: { ...e, fase: "lendo", geracao }, efeitos: [{ tipo: "ler", geracao }] };
      // Login nunca pede prompt; só lê a preferência para as voltas seguintes.
      if (ev.tipo === "login")
        return { estado: { ...e, fase: "livre", jaLiberou: true, geracao }, efeitos: [{ tipo: "ler", geracao }] };
      if (ev.tipo === "app") return nada({ ...e, appAtivo: ev.valor === "active" });
      return nada(e);
    }

    case "lendo": {
      if (ev.tipo === "app") return nada({ ...e, appAtivo: ev.valor === "active" });
      if (ev.tipo !== "leu") return nada(e);
      const preferencia = ev.desligada ? "desligada" : "ligada";
      if (ev.desligada || ev.semCodigo) return nada({ ...e, fase: "livre", jaLiberou: true, preferencia });
      const travado: Estado = { ...e, fase: "travado", preferencia, pedirAoAtivar: true };
      return e.appAtivo ? pedir(travado) : nada(travado);
    }

    case "travado": {
      if (ev.tipo === "app") {
        if (ev.valor === "active") {
          const ativo: Estado = { ...e, appAtivo: true, saiuEm: null, foiAoFundo: false };
          // Voltar só de inactive (o próprio prompt, a Central de Controle) não pede de novo.
          return ativo.pedirAoAtivar ? pedir(ativo) : nada(ativo);
        }
        return nada({
          ...e,
          appAtivo: false,
          saiuEm: e.saiuEm ?? agora,
          ...(ev.valor === "background" ? { foiAoFundo: true, pedirAoAtivar: true } : {}),
        });
      }
      if (ev.tipo === "desbloquear") return pedir(e);
      if (ev.tipo === "resultado") {
        if (ev.liberou) return nada({ ...e, fase: "livre", jaLiberou: true, autenticando: false, falhou: false });
        return nada({ ...e, autenticando: false, falhou: true });
      }
      return nada(e);
    }

    case "livre": {
      if (ev.tipo === "leu") return nada({ ...e, preferencia: ev.desligada ? "desligada" : "ligada" });
      if (ev.tipo !== "app") return nada(e);
      if (ev.valor !== "active") {
        return nada({
          ...e,
          appAtivo: false,
          saiuEm: e.saiuEm ?? agora,
          foiAoFundo: e.foiAoFundo || ev.valor === "background",
        });
      }
      const ativo: Estado = { ...e, appAtivo: true, saiuEm: null, foiAoFundo: false };
      if (!e.foiAoFundo || e.saiuEm === null || e.preferencia === "desligada") return nada(ativo);
      const fora = agora - e.saiuEm;
      // Diferença negativa = a hora do aparelho voltou: trava (não dá para confiar no intervalo).
      if (fora > PERIODO_DE_GRACA_MS || fora < 0) {
        return pedir({ ...ativo, fase: "travado" }, [{ tipo: "fecharTeclado" }]);
      }
      return nada(ativo);
    }
  }
}
