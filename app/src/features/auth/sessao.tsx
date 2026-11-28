import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { sair as sairNoServidor } from "@/services/auth";
import { lerCredenciais, leitorDe, type Credenciais } from "@/storage/secure";

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
  /** Sessão de leitor (credencial de visualização): vem da claim `ro` do access guardado no cofre, no boot e no login. Só esconde UI e o registro de push; o servidor barra a escrita. */
  leitor: boolean;
  /** Chamado assim que uma credencial é gravada (login ou MFA). */
  autenticar: () => void;
  /** O mesmo, para o login de visualização: a sessão é de leitor. */
  autenticarLeitor: () => void;
  /** `/auth/me` (ou qualquer chamada autenticada) tomou `SessaoExpirada`; só age em `autenticado`. */
  expirou: (aviso: string) => void;
  /**
   * Toque duplo é ignorado (guarda por `ref`, único provider da árvore), e
   * `expirou()` também, enquanto a saída estiver em voo. Só o Sair comum: a
   * saída de uma sessão `capturada` não usa a guarda (outra conta pode entrar
   * e tocar Sair no meio), e um `expirou()` nela leva a anônimo, o desejado.
   *
   * Resolve `true` quando a saída terminou — depois da resposta da revogação
   * no servidor ou do tempo limite dela — ou quando o toque foi ignorado
   * porque já havia uma saída em andamento; e `false` quando o cofre falhou
   * (a falha da revogação não conta: `services/auth.ts` a engole). Sem o
   * `false`, a falha do cofre passava em silêncio: o provider continuava
   * `autenticado` (correto, o invariante de pé) mas quem tocou "Sair" não via
   * nada, nem um erro nem uma segunda chance óbvia.
   *
   * `capturada`: a sessão de que quem chama sai (lida ANTES de uma ação longa), em vez de reler o cofre. Se o cofre
   * já tem OUTRA sessão, nada é apagado, o provider segue autenticado (invariante) e devolve `"outra"` (truthy).
   */
  sair: (capturada?: Credenciais | null) => Promise<boolean | "outra">;
  /** Só vale em `erro`: refaz a checagem de sessão do início. */
  tentarDeNovo: () => void;
}

const Contexto = createContext<Sessao | null>(null);

export function useSessao(): Sessao {
  const contexto = useContext(Contexto);
  if (!contexto) throw new Error("useSessao() precisa de um <SessaoProvider> por cima na árvore.");
  return contexto;
}

/** Só o `leitor`, sem exigir o provider: os componentes do painel que escondem ações para o leitor também são montados sozinhos nos testes (e, sem provider, o dono). */
export function useLeitor(): boolean {
  return useContext(Contexto)?.leitor ?? false;
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
  const [leitor, setLeitor] = useState(false);

  useEffect(() => {
    let cancelado = false;
    setEstado({ fase: "verificando" });
    (async () => {
      try {
        const cred = await lerCredenciais();
        if (!cancelado) { setLeitor(!!cred && leitorDe(cred.access)); setEstado(cred ? { fase: "autenticado" } : { fase: "anonimo" }); }
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
      leitor: estado.fase === "autenticado" && leitor,
      autenticar: () => { setLeitor(false); setEstado({ fase: "autenticado" }); },
      autenticarLeitor: () => { setLeitor(true); setEstado({ fase: "autenticado" }); },
      expirou: (aviso) => {
        // Com a saída comum em voo, um `SessaoExpirada` é efeito dela (o cofre já foi
        // limpo): virar anônimo aqui abriria Entrar antes de `sair()` terminar,
        // e o `.then` abaixo apagaria o estado de quem entrasse nesse meio.
        // E só age a partir de `autenticado`: um `SessaoExpirada` que chega
        // depois da saída terminar poria "sessão expirou" num logout limpo.
        if (saindoEmVoo.current) return;
        setEstado((s) => (s.fase === "autenticado" ? { fase: "anonimo", aviso } : s));
      },
      sair: (capturada) => {
        // `capturada` é a limpeza de UMA sessão antiga (exclusão de conta), não o Sair da pessoa: não usa
        // a guarda, senão engole o Sair da conta que entrou durante a espera do /auth/logout dela.
        const comum = capturada === undefined;
        if (comum) {
          if (saindoEmVoo.current) return Promise.resolve(true);
          saindoEmVoo.current = true;
        }
        return sairNoServidor(capturada)
          .then((outraSessao) => {
            if (outraSessao) return "outra" as const;
            setEstado({ fase: "anonimo" });
            return true;
          })
          .catch(() => {
            // Não afirma logout: a credencial pode ter ficado no aparelho
            // (`sair()` de services/auth.ts pode rejeitar por falha do
            // cofre). Fica autenticado — a tela pode tentar Sair de novo, e
            // o invariante (cofre tem credencial ⇔ provider autenticado)
            // continua de pé. O `false` é o que deixa a TELA mostrar isso,
            // em vez de o toque em "Sair" parecer não ter feito nada.
            return false;
          })
          .finally(() => {
            if (comum) saindoEmVoo.current = false;
          });
      },
      tentarDeNovo: () => setTentativa((t) => t + 1),
    }),
    [estado, leitor],
  );

  return <Contexto.Provider value={valor}>{children}</Contexto.Provider>;
}
