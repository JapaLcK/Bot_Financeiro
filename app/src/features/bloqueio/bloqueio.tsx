import * as LocalAuthentication from "expo-local-authentication";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AppState, Keyboard } from "react-native";

import { useSessao, type EstadoSessao } from "@/features/auth/sessao";
import { gravarTravaDesligada, lerTravaDesligada } from "@/storage/secure";

import { inicial, transicao, type Efeito, type Estado, type EstadoDoApp, type Evento } from "./maquina";
import { descobrir, pular } from "./tampa";

/**
 * A trava biométrica do app: liga a tabela pura de `maquina.ts` ao mundo — a
 * fase da sessão, o `AppState`, o prompt do sistema e o cofre. É o ÚNICO
 * arquivo que importa `expo-local-authentication`.
 */

type Resultado = "ok" | "cancelado" | "erro";

interface Bloqueio {
  estado: Estado;
  desbloquear: () => void;
  /** Pede o Face ID ANTES de gravar (decisão do dono); cancelar não muda nada. */
  desligar: () => Promise<Resultado>;
  ligar: () => Promise<Resultado>;
  /** `null` = o aparelho não tem nem código: a trava fica inerte. */
  capacidade: () => Promise<"Face ID" | "Touch ID" | "o código do aparelho" | null>;
}

const Contexto = createContext<Bloqueio | null>(null);

export function useBloqueio(): Bloqueio {
  const contexto = useContext(Contexto);
  if (!contexto) throw new Error("useBloqueio() precisa de um <BloqueioProvider> por cima na árvore.");
  return contexto;
}

const PROMPT: LocalAuthentication.LocalAuthenticationOptions = {
  promptMessage: "Desbloquear o PigBank",
  cancelLabel: "Cancelar",
  fallbackLabel: "Usar código do iPhone",
  disableDeviceFallback: false,
};

/** Erros que dizem "não há como autenticar": reconfere o nível antes de prender alguém. */
const SEM_CREDENCIAL = ["not_enrolled", "passcode_not_set", "not_available"];

/** Nível ilegível vale como SECRET: tenta o prompt, e o erro cai no ramo com Sair. */
async function semCodigo(): Promise<boolean> {
  try {
    return (await LocalAuthentication.getEnrolledLevelAsync()) === LocalAuthentication.SecurityLevel.NONE;
  } catch {
    return false;
  }
}

/** O único que chama o prompt (trava e desligar): a tampa nativa não sobe sobre ele. */
async function autenticar(): Promise<boolean> {
  // Falha em avisar o nativo não impede o prompt: no pior caso a tampa pisca.
  await pular(true).catch(() => undefined);
  try {
    const r = await LocalAuthentication.authenticateAsync(PROMPT);
    if (r.success) return true;
    return SEM_CREDENCIAL.includes(r.error) && (await semCodigo());
  } catch {
    return false;
  } finally {
    void pular(false).catch(() => undefined);
  }
}

function eventoDaSessao(de: EstadoSessao["fase"], para: EstadoSessao["fase"]): Evento | null {
  if (para === "autenticado") return de === "anonimo" ? { tipo: "login" } : { tipo: "boot" };
  if (de === "autenticado") return { tipo: "saiu" };
  return null;
}

interface Caixa {
  m: Estado;
  /** Só cresce pelo fim; o efeito abaixo corta do começo o que já executou. */
  efeitos: Efeito[];
  faseSessao: EstadoSessao["fase"];
}

export function BloqueioProvider({ children }: { children: ReactNode }) {
  const fase = useSessao().estado.fase;
  const [caixa, setCaixa] = useState<Caixa>(() => ({
    m: inicial(AppState.currentState === "active"),
    efeitos: [],
    faseSessao: fase,
  }));

  // A mudança de sessão entra DURANTE o render, não num efeito: um efeito
  // deixaria um quadro com a pilha montada (e o Início buscando /auth/me)
  // antes de a trava cobrir a abertura com sessão salva.
  let atual = caixa;
  if (caixa.faseSessao !== fase) {
    const ev = eventoDaSessao(caixa.faseSessao, fase);
    const r = ev ? transicao(caixa.m, ev, Date.now()) : { estado: caixa.m, efeitos: [] };
    atual = { m: r.estado, efeitos: [...caixa.efeitos, ...r.efeitos], faseSessao: fase };
    setCaixa(atual);
  }

  const enviar = useCallback((ev: Evento) => {
    const agora = Date.now();
    setCaixa((c) => {
      const r = transicao(c.m, ev, agora);
      return { ...c, m: r.estado, efeitos: r.efeitos.length ? [...c.efeitos, ...r.efeitos] : c.efeitos };
    });
  }, []);

  // Uma tampa que subiu antes de o provider montar (ou antes de um reload em dev) sai aqui.
  useEffect(() => {
    descobrir();
  }, []);

  useEffect(() => {
    const assinatura = AppState.addEventListener("change", (valor) => {
      if (valor === "active" || valor === "inactive" || valor === "background") enviar({ tipo: "app", valor: valor as EstadoDoApp });
    });
    return () => assinatura.remove();
  }, [enviar]);

  useEffect(() => {
    const lote = caixa.efeitos;
    if (!lote.length) return;
    setCaixa((c) => ({ ...c, efeitos: c.efeitos.slice(lote.length) }));
    for (const ef of lote) {
      if (ef.tipo === "fecharTeclado") Keyboard.dismiss();
      if (ef.tipo === "descobrir") descobrir();
      if (ef.tipo === "pedir") {
        void (async () => {
          const liberou = (await semCodigo()) || (await autenticar());
          enviar({ tipo: "resultado", geracao: ef.geracao, liberou });
        })();
      }
      if (ef.tipo === "ler") {
        void (async () => {
          // Cofre ilegível = ligada (falha fechada).
          const [desligada, sem] = await Promise.all([lerTravaDesligada().catch(() => false), semCodigo()]);
          enviar({ tipo: "leu", geracao: ef.geracao, desligada, semCodigo: sem });
        })();
      }
    }
  }, [caixa.efeitos, enviar]);

  // Um desligar/ligar por vez: o switch fica travado enquanto isso, mas o
  // toque duplo chega antes do re-render.
  const emVoo = useRef(false);
  const mudar = useCallback(
    async (desligada: boolean): Promise<Resultado> => {
      if (emVoo.current) return "cancelado";
      emVoo.current = true;
      try {
        if (desligada && !(await autenticar())) return "cancelado";
        // Grava ANTES de mudar a memória: se o cofre falhar, a tela continua
        // dizendo o que o disco diz.
        await gravarTravaDesligada(desligada);
        enviar({ tipo: "preferencia", valor: desligada ? "desligada" : "ligada" });
        return "ok";
      } catch {
        return "erro";
      } finally {
        emVoo.current = false;
      }
    },
    [enviar],
  );

  const valor = useMemo<Bloqueio>(
    () => ({
      estado: atual.m,
      desbloquear: () => enviar({ tipo: "desbloquear" }),
      desligar: () => mudar(true),
      ligar: () => mudar(false),
      capacidade: async () => {
        const { SecurityLevel } = LocalAuthentication;
        const nivel = await LocalAuthentication.getEnrolledLevelAsync().catch(() => SecurityLevel.SECRET);
        if (nivel === SecurityLevel.NONE) return null;
        if (nivel === SecurityLevel.SECRET) return "o código do aparelho";
        const tipos = await LocalAuthentication.supportedAuthenticationTypesAsync().catch((): LocalAuthentication.AuthenticationType[] => []);
        if (tipos.includes(LocalAuthentication.AuthenticationType.FACIAL_RECOGNITION)) return "Face ID";
        if (tipos.includes(LocalAuthentication.AuthenticationType.FINGERPRINT)) return "Touch ID";
        return "o código do aparelho";
      },
    }),
    [atual.m, enviar, mudar],
  );

  return <Contexto.Provider value={valor}>{children}</Contexto.Provider>;
}
