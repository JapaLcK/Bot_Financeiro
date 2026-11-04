import { useState } from "react";
import { ERRO_VALOR, normalizaValor, valorInvalido } from "../lib/format.js";
import { criar, editar, type OrcamentoCasa } from "./casa";
import { CampoTexto, CasaDialogo } from "./CasaDialogo";

// Criar ou editar um orçamento da casa (uma categoria por grupo, sem diferenciar caixa). Mesma regra da `versao` do CasaMetaForm.
export function CasaOrcamentoForm({ uid, orcamento, fechar, apagar }: { uid: number; orcamento?: OrcamentoCasa; fechar: () => void; apagar?: () => void }) {
  const [categoria, setCategoria] = useState(orcamento?.categoria ?? "");
  const [limite, setLimite] = useState(orcamento ? orcamento.limite.replace(".", ",") : "");
  const norm = normalizaValor(limite);
  return <CasaDialogo uid={uid} tipo="orcamentos" titulo={orcamento ? "Editar orçamento" : "Novo orçamento"} rotulo="Salvar" fechar={fechar} apagar={apagar}
    validar={() => !categoria.trim() ? { texto: "Escreva uma categoria.", campo: "categoria" } : valorInvalido(norm) ? { texto: ERRO_VALOR, campo: "valor" } : null}
    enviar={() => { const corpo = { categoria: categoria.trim(), limite: norm }; return orcamento ? editar("orcamentos", orcamento.ref, { ...corpo, versao: orcamento.versao }) : criar("orcamentos", corpo); }}
    filhos={({ falha, pendente }) => <>
      <CampoTexto nome="categoria" rotulo="Categoria" valor={categoria} mudou={setCategoria} max={80} pendente={pendente} invalido={falha?.campo === "categoria"} />
      <CampoTexto nome="limite" rotulo="Teto do mês (R$)" valor={limite} mudou={setLimite} modo="decimal" pendente={pendente} invalido={falha?.campo === "valor"} />
    </>} />;
}
