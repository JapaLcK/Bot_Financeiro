import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { Confirma } from "../conta/Confirma";
import { dissolverFamilia, familiaQuery, lerDissolucao, removerMembro, type Dissolucao, type Membro, type Titular as VisaoTitular } from "./api";
import { Convite } from "./Convite";
import { focarTitulo, type useEscrita } from "./escrita";

const primeiroNome = (n: string) => n.trim().split(/\s+/)[0] || "Membro";

// Visão do titular: lugares, membros (remover), convite e desfazer a família. Cada escrita reler a família ao fim.
export function Titular({ v, escrita }: { v: VisaoTitular; escrita: ReturnType<typeof useEscrita> }) {
  const id = useId();
  const qc = useQueryClient();
  const { ocupado, rodar } = escrita;
  const [remover, setRemover] = useState<Membro | null>(null);
  // O nonce da dissolução vive só aqui, no estado do diálogo: sai do estado ao fechar e nunca é reaproveitado (um novo "Desfazer" lê outro).
  const [previa, setPrevia] = useState<Dissolucao | null>(null);
  const reler = () => qc.invalidateQueries({ queryKey: familiaQuery.queryKey });
  useEffect(focarTitulo, [v.membros]); // remover: a linha (e o "Remover" que o diálogo devolveria ao foco) saiu da tela
  const lugares = v.membros.length + 1; // +1: o titular ocupa um lugar (a mesma conta do servidor)
  const cheio = lugares >= v.capacidade;

  const desfazer = async () => { await rodar(async () => setPrevia(await lerDissolucao())); };
  const n = previa?.membros ?? 0;

  return <>
    <p>{lugares} de {v.capacidade} lugares</p>
    {v.membros.length > 0 && <ul className="fam-lista">
      {v.membros.map((m, i) => (
        <li key={m.ref}>
          <i className="ph ph-users-three" aria-hidden="true" />
          <div><b id={`${id}-${i}`}>{primeiroNome(m.nome)}</b>{m.status === "suspenso" && <> <span className="selo">Suspenso</span></>}</div>
          <button type="button" className="btn btn-ghost" aria-describedby={`${id}-${i}`} aria-disabled={ocupado || undefined} onClick={() => setRemover(m)}>Remover</button>
        </li>
      ))}
    </ul>}
    <Convite cheio={cheio} escrita={escrita} />
    <div className="fam-acoes">
      <button type="button" className="btn btn-ghost" aria-disabled={ocupado || undefined} onClick={desfazer}>Desfazer família</button>
    </div>
    {remover && <Confirma titulo={`Remover ${primeiroNome(remover.nome)}?`} texto={`${primeiroNome(remover.nome)} perde o acesso ao plano da família.`} botao="Remover" pendente="Removendo…"
      fechar={() => setRemover(null)} agir={async () => { await rodar(async () => { await removerMembro(remover.ref); await reler(); }); }} />}
    {previa && <Confirma titulo="Desfazer a família?" texto={n ? `${n} ${n === 1 ? "pessoa perde" : "pessoas perdem"} o acesso.` : "A família será desfeita."} botao="Desfazer" pendente="Desfazendo…"
      fechar={() => setPrevia(null)} agir={async () => { await rodar(async () => { await dissolverFamilia(previa.confirmacao); await reler(); }); }} />}
  </>;
}
