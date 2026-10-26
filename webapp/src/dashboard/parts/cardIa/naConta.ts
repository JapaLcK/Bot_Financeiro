import { useEffect } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { ContaTrocada, antesDaConta, comPrazo, daConta } from "../../conta/api";
import { lerContaFresca } from "../../lib/v2";

// A guarda de conta dos cards da IA que leem ou escrevem fora do bloco (lançamentos, simulador): o servidor lê o usuário da SESSÃO, não do `uid` do card,
// então a sessão de outra conta devolveria os números DELA sob este card. A conta é conferida ANTES (nenhuma requisição sai com a sessão de outra) e DEPOIS
// (a troca durante a requisição não desenha nada). Quem usa: LancamentosDoCard e simulador (a conferência inteira) e o CardAgente (só `useContaTrocada`;
// ele confere com `daConta` direto, sem a de antes).
const PRAZO_MS = 15_000; // o `comLimite` do app
// O prazo cobre a chamada INTEIRA: as leituras do perfil (antes e depois) não levam sinal, então o corte rejeita por cima delas.
const cortavel = <T,>(p: Promise<T>, s: AbortSignal) => Promise.race([p, new Promise<never>((_, no) => {
  const corta = () => no(new DOMException("Aborted", "AbortError"));
  if (s.aborted) corta(); else s.addEventListener("abort", corta, { once: true });
})]);

/** `f` (a requisição e a conferência do contrato dela) entre a conferência de antes e a de depois, dentro do prazo; `signal` é o da query (cancelar a query corta tudo). */
export const naConta = <T,>(qc: QueryClient, uid: number, signal: AbortSignal, f: (s: AbortSignal) => Promise<T>) =>
  comPrazo((s) => cortavel((async () => {
    await antesDaConta(qc, uid);
    return daConta(uid, s, await f(s));
  })(), s), PRAZO_MS, signal);

/** `true` quando a leitura falhou por `ContaTrocada`: o perfil é relido (o `contaDaSessao` apaga a conversa da conta anterior) e o card some com ela. */
export function useContaTrocada(erro: unknown) {
  const qc = useQueryClient(), trocada = erro instanceof ContaTrocada;
  useEffect(() => { if (trocada) void lerContaFresca(qc).catch(() => {}); }, [trocada, qc]);
  return trocada;
}
