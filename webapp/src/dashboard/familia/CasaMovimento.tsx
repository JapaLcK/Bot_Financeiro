import { useState } from "react";
import { ERRO_VALOR, normalizaValor, valorInvalido } from "../lib/format.js";
import { mover, type Tipo } from "./casa";
import { CampoTexto, CasaDialogo } from "./CasaDialogo";

// Guardar ou tirar de uma meta, lançar ou desfazer gasto num orçamento: um valor, que o servidor soma (ou subtrai) no estado dele. Sem `versao`: o movimento nunca sobrescreve, soma.
export type Movimento = { tipo: Tipo; ref: string; sinal: "somar" | "subtrair"; titulo: string };
export function CasaMovimento({ uid, m, fechar }: { uid: number; m: Movimento; fechar: () => void }) {
  const [valor, setValor] = useState("");
  const norm = normalizaValor(valor);
  return <CasaDialogo uid={uid} tipo={m.tipo} titulo={m.titulo} rotulo="Confirmar" fechar={fechar}
    validar={() => valorInvalido(norm) ? { texto: ERRO_VALOR, campo: "valor" } : null}
    enviar={() => mover(m.tipo, m.ref, m.sinal, norm)}
    filhos={({ falha, pendente }) => <CampoTexto nome="valor" rotulo="Valor (R$)" valor={valor} mudou={setValor} modo="decimal" pendente={pendente} invalido={falha?.campo === "valor"} />} />;
}
