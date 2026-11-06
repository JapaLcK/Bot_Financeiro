import { useState } from "react";
import { ERRO_VALOR, normalizaValor, valorInvalido } from "../lib/format.js";
import { criar, editar, type MetaCasa } from "./casa";
import { CampoTexto, CasaDialogo } from "./CasaDialogo";

// Criar ou editar uma meta da casa. O PUT troca a meta inteira e leva a `versao` LIDA quando o diálogo abriu (`meta` é a foto dessa hora, não a lista que pode ter mudado depois): outro membro mudou = 409.
export function CasaMetaForm({ uid, meta, fechar, apagar }: { uid: number; meta?: MetaCasa; fechar: () => void; apagar?: () => void }) {
  const [nome, setNome] = useState(meta?.nome ?? "");
  const [alvo, setAlvo] = useState(meta ? meta.alvo.replace(".", ",") : "");
  const [prazo, setPrazo] = useState(meta?.prazo ?? "");
  const norm = normalizaValor(alvo);
  return <CasaDialogo uid={uid} tipo="metas" titulo={meta ? "Editar meta" : "Nova meta"} rotulo="Salvar" fechar={fechar} apagar={apagar}
    validar={() => !nome.trim() ? { texto: "Dê um nome.", campo: "nome" } : valorInvalido(norm) ? { texto: ERRO_VALOR, campo: "valor" } : null}
    enviar={() => { const corpo = { nome: nome.trim(), alvo: norm, prazo: prazo || null }; return meta ? editar("metas", meta.ref, { ...corpo, versao: meta.versao }) : criar("metas", corpo); }}
    filhos={({ falha, pendente }) => <>
      <CampoTexto nome="nome" rotulo="Nome" valor={nome} mudou={setNome} max={60} pendente={pendente} invalido={falha?.campo === "nome"} />
      <CampoTexto nome="alvo" rotulo="Quanto juntar (R$)" valor={alvo} mudou={setAlvo} modo="decimal" pendente={pendente} invalido={falha?.campo === "valor"} />
      <label>Prazo (opcional)
        <input className="field" name="prazo" type="date" value={prazo} disabled={pendente} onChange={(e) => setPrazo(e.target.value)} aria-invalid={falha?.campo === "prazo" || undefined} />
      </label>
    </>} />;
}
