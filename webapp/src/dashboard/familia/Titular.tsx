import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { Confirma } from "../conta/Confirma";
import { dissolverFamilia, familiaQuery, lerDissolucao, primeiroNome, removerMembro, type Dissolucao, type Membro, type Titular as VisaoTitular } from "./api";
import { Convite } from "./Convite";
import { focarTitulo, type useEscrita } from "./escrita";
import { VerMembro } from "./VerMembro";

// Visão do titular: lugares, membros (remover), convite e desfazer a família. Cada escrita reler a família ao fim.
export function Titular({ v, escrita, uid, perfilFalhou }: { v: VisaoTitular; escrita: ReturnType<typeof useEscrita>; uid: number | undefined; perfilFalhou: (() => void) | undefined }) {
  const id = useId();
  const qc = useQueryClient();
  const { ocupado, rodar } = escrita;
  const [remover, setRemover] = useState<Membro | null>(null);
  const [aberto, setAberto] = useState<string | null>(null); // o ref do membro cujo resumo está aberto; o dado fica na memória do VerMembro, nunca aqui
  // O nonce da dissolução vive só aqui, no estado do diálogo: sai do estado ao fechar e nunca é reaproveitado (um novo "Desfazer" lê outro).
  const [previa, setPrevia] = useState<Dissolucao | null>(null);
  const reler = () => qc.invalidateQueries({ queryKey: familiaQuery.queryKey }); // sem `await` dentro do `agir`: a releitura (5xx = ~3 s de retries; pendurada = nunca) não prende a confirmação
  useEffect(focarTitulo, [v.membros]); // remover: a linha (e o "Remover" que o diálogo devolveria ao foco) saiu da tela
  useEffect(focarTitulo, [aberto]); // fechar o painel: o "Voltar" saiu da tela (ao abrir, o foco vai ao título do painel: o VerMembro o faz, porque o "Ver" fica na lista hidden)
  const lugares = v.membros.length + 1; // +1: o titular ocupa um lugar (a mesma conta do servidor)
  const cheio = lugares >= v.capacidade;

  const desfazer = async () => { await rodar(async () => setPrevia(await lerDissolucao())); };
  const n = previa?.membros ?? 0;
  const verMembro = v.membros.find((m) => m.ref === aberto); // sumiu da família depois de uma releitura: o painel fecha
  useEffect(() => { if (aberto && !verMembro) setAberto(null); }, [verMembro]); // e esquece o ref: se ele voltar na lista, o painel NÃO reabre (reabrir = ler sem "Ver")

  // A lista fica montada (hidden) sob o painel: o link de convite recém-gerado mora no estado do Convite e não pode se perder.
  return <>
    {verMembro && <VerMembro key={verMembro.ref} uid={uid} perfilFalhou={perfilFalhou} membro={verMembro} voltar={() => setAberto(null)} />}
    <div className="fam-corpo" hidden={!!verMembro}>
    <p>{lugares} de {v.capacidade} lugares</p>
    {v.membros.length > 0 && <ul className="fam-lista">
      {v.membros.map((m, i) => (
        <li key={m.ref}>
          <i className="ph ph-users-three" aria-hidden="true" />
          <div><b id={`${id}-${i}`}>{primeiroNome(m.nome)}</b>{m.status === "suspenso" && <> <span className="selo">Suspenso</span></>}</div>
          <span className="fam-acoes-membro">
            {m.status === "ativo" && m.permissoes.includes("agregados")
              ? <button type="button" className="btn btn-ghost" aria-describedby={`${id}-${i}`} onClick={() => setAberto(m.ref)}>Ver</button>
              : <span className="faint">Não compartilha</span>}
            <button type="button" className="btn btn-ghost" aria-describedby={`${id}-${i}`} aria-disabled={ocupado || undefined} onClick={() => setRemover(m)}>Remover</button>
          </span>
        </li>
      ))}
    </ul>}
    <Convite cheio={cheio} escrita={escrita} />
    <div className="fam-acoes">
      <button type="button" className="btn btn-ghost" aria-disabled={ocupado || undefined} onClick={desfazer}>Desfazer família</button>
    </div>
    </div>
    {remover && <Confirma titulo={`Remover ${primeiroNome(remover.nome)}?`} texto={`${primeiroNome(remover.nome)} perde o acesso ao plano da família.`} botao="Remover" pendente="Removendo…"
      fechar={() => setRemover(null)} agir={async () => { await rodar(async () => { await removerMembro(remover.ref); void reler(); }); }} />}
    {previa && <Confirma titulo="Desfazer a família?" texto={n ? `${n} ${n === 1 ? "pessoa perde" : "pessoas perdem"} o acesso.` : "A família será desfeita."} botao="Desfazer" pendente="Desfazendo…"
      fechar={() => setPrevia(null)} agir={async () => { await rodar(async () => { await dissolverFamilia(previa.confirmacao); void reler(); }); }} />}
  </>;
}
