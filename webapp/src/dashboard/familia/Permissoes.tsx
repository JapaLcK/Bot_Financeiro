import { useId } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { familiaQuery, type Escopo, type Visao } from "./api";
import { liberar, revogar } from "./dados";
import type { useEscrita } from "./escrita";

// Membro: o que o titular pode ver. O estado vem SEMPRE da resposta do servidor (nada otimista): revogar o resumo devolve [] e desliga os dois.
export function Permissoes({ permissoes, escrita }: { permissoes: Escopo[]; escrita: ReturnType<typeof useEscrita> }) {
  const id = useId();
  const qc = useQueryClient();
  const { ocupado, rodar } = escrita;
  const alternar = (escopo: Escopo) => rodar(async () => {
    const novas = await (permissoes.includes(escopo) ? revogar : liberar)(escopo);
    qc.setQueryData(familiaQuery.queryKey, (): Visao => ({ papel: "membro", permissoes: novas }));
    void qc.invalidateQueries({ queryKey: familiaQuery.queryKey });
  });
  const interruptor = (escopo: Escopo, rotulo: string, travado: boolean) => (
    <li>
      <label htmlFor={`${id}-${escopo}`}>{rotulo}</label>
      {/* aria-disabled em voo (o foco não cai no body); a trava é o `voando` do useEscrita */}
      <input id={`${id}-${escopo}`} className="notif-switch" type="checkbox" role="switch" checked={permissoes.includes(escopo)} disabled={travado} aria-disabled={ocupado ? true : undefined}
        onChange={() => alternar(escopo)} />
    </li>
  );
  return <section className="fam-perm" aria-labelledby={`${id}-t`}>
    <h2 id={`${id}-t`}>O que o titular vê</h2>
    <ul>
      {interruptor("agregados", "Resumo do mês", false)}
      {interruptor("lancamentos", "Lançamentos", !permissoes.includes("agregados"))}
    </ul>
    <p className="faint">Saldo, contas, Pix e conversas nunca aparecem.</p>
  </section>;
}
