import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { sair as sairNoServidor, temSessao } from "@/services/auth";

/**
 * Estado de sessão do app inteiro. Monta UMA vez na raiz (`_layout.tsx`) e
 * substitui a fila de `montar()` de `src/ui/inicio.ts` (Fase 1): aquela fila
 * existia porque a tela de Início podia REMONTAR com uma ação em voo (rota
 * recriada); um provider que só monta uma vez na raiz do app não tem esse
 * problema — não há "montagem concorrente" para segurar.
 */
export type EstadoSessao =
  | { fase: "verificando" }
  | { fase: "anonimo"; aviso?: string }
  | { fase: "autenticado" }
  | { fase: "erro"; mensagem: string };

interface Sessao {
  estado: EstadoSessao;
  /** Chamado assim que uma credencial é gravada (login ou MFA). */
  autenticar: () => void;
  /** `/auth/me` (ou qualquer chamada autenticada) tomou `SessaoExpirada`. */
  expirou: (aviso: string) => void;
  /** Toque duplo é ignorado (guarda por `ref`, único provider da árvore). */
  sair: () => void;
  /** Só vale em `erro`: refaz a checagem de sessão do início. */
  tentarDeNovo: () => void;
}

const Contexto = createContext<Sessao | null>(null);

export function useSessao(): Sessao {
  const contexto = useContext(Contexto);
  if (!contexto) throw new Error("useSessao() precisa de um <SessaoProvider> por cima na árvore.");
  return contexto;
}

const MENSAGEM_FALHA_COFRE =
  "Não conseguimos abrir sua sessão neste aparelho. Tente de novo.";

export function SessaoProvider({ children }: { children: ReactNode }) {
  const [estado, setEstado] = useState<EstadoSessao>({ fase: "verificando" });
  // Incrementado por `tentarDeNovo()` para o efeito de boot rodar de novo —
  // `tentarDeNovo` só troca o ESTADO não bastaria: o efeito abaixo tem `[]`/
  // dependência fixa e não repetiria a leitura do cofre sozinho.
  const [tentativa, setTentativa] = useState(0);
  // Guarda de toque duplo do Sair: só este provider existe na árvore, então
  // um `ref` de instância já basta (sem precisar de estado de módulo).
  const saindoEmVoo = useRef(false);

  useEffect(() => {
    let cancelado = false;
    setEstado({ fase: "verificando" });
    (async () => {
      try {
        const tem = await temSessao();
        if (!cancelado) setEstado(tem ? { fase: "autenticado" } : { fase: "anonimo" });
      } catch {
        // Falha do cofre: o estado da sessão neste aparelho é desconhecido.
        if (!cancelado) setEstado({ fase: "erro", mensagem: MENSAGEM_FALHA_COFRE });
      }
    })();
    return () => {
      cancelado = true;
    };
  }, [tentativa]);

  const valor = useMemo<Sessao>(
    () => ({
      estado,
      autenticar: () => setEstado({ fase: "autenticado" }),
      expirou: (aviso) => setEstado({ fase: "anonimo", aviso }),
      sair: () => {
        if (saindoEmVoo.current) return;
        saindoEmVoo.current = true;
        void sairNoServidor()
          .then(() => setEstado({ fase: "anonimo" }))
          .catch(() => {
            // Não afirma logout: a credencial pode ter ficado no aparelho
            // (`sair()` de services/auth.ts pode rejeitar por falha do
            // cofre). Fica autenticado — a tela pode tentar Sair de novo, e
            // o invariante (cofre tem credencial ⇔ provider autenticado)
            // continua de pé.
          })
          .finally(() => {
            saindoEmVoo.current = false;
          });
      },
      tentarDeNovo: () => setTentativa((t) => t + 1),
    }),
    [estado],
  );

  return <Contexto.Provider value={valor}>{children}</Contexto.Provider>;
}
