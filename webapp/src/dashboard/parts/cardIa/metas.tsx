import type { ReactNode } from "react";
import type { Metas } from "../../lib/api-v2.gen";
import { MESES_PADRAO } from "../../lib/metas-textos";
import { ask, usePerguntando } from "../../lib/conversation";
import { useIa } from "../../lib/conversaIa";
import { uidValido } from "../../conta/api";
import { emOrdem } from "../../widgets/Metas";
import { Blocos, MetaPilula } from "../MetaPilula";
import { useMais } from "./mais";

// O card `metas` do chat: o `recursos` do bloco é `{ metas: <GET /api/v2/metas> }`. As pílulas são as do app (reserva primeiro, até 5 linhas, "+N" abre o resto).
// Criar é pedir à IA, como no app: a linha manda a frase abaixo AO CHAT, como se o usuário a tivesse digitado, com o dono do card (`ask` confere a conta
// ANTES e não envia se a sessão já é de outra). Editar e apagar também são com a IA. As duas frases são cópia de app/src/features/painel/metasChat.tsx
// (tests/frontend/card_ia_paridade.test.mjs compara).
export const PEDIDO_RESERVA = "Quero montar minha reserva de emergência";
export const PEDIDO_META = "Quero criar uma meta";
const VISIVEIS = 5; // pílulas à mostra, a linha "Montar reserva" incluída

const obj = (x: unknown): x is Record<string, unknown> => !!x && typeof x === "object" && !Array.isArray(x);
export const contratoMetas = (r: unknown) => {
  const d = (r as { metas?: unknown } | null)?.metas;
  return obj(d) && Array.isArray(d.metas) && obj(d.custo_mensal);
};

// `aria-disabled` e não `disabled`: tirar o foco do botão no meio de um turno desorienta o leitor de tela. Fechado o envio (turno em voo, cota acabada, conferência
// em andamento) ou sem dono do card, o toque não faz nada.
function LinhaPedido({ icone, rotulo, rotuloAcessivel, texto, uid, children }: { icone: string; rotulo: string; rotuloAcessivel: string; texto: string; uid?: number; children?: ReactNode }) {
  const { ocupado, cheia } = useIa(), perguntando = usePerguntando(), fechado = !uidValido(uid) || ocupado || cheia || perguntando;
  return (
    <button type="button" className="pilula pilula-pedido" aria-disabled={fechado} aria-label={rotuloAcessivel} onClick={() => { if (!fechado) ask({ text: texto, dono: uid }); }}>
      <span className="pilula-topo"><span className="pilula-icone"><i className={`ph ${icone}`} aria-hidden="true" /></span><span className="pilula-nome">{rotulo}</span></span>
      {children}
    </button>
  );
}

export function CardMetas({ d, uid }: { d: Metas; uid?: number }) {
  const ordem = emOrdem(d.metas), semReserva = !ordem.some((m) => m.tipo === "reserva");
  const [vistas, mais] = useMais(ordem, VISIVEIS - Number(semReserva), ["meta", "metas"]);
  return (
    <>
      <ul className="ci-metas">
        {semReserva && <li><LinhaPedido icone="ph-piggy-bank" rotulo="Montar reserva" rotuloAcessivel={`Montar reserva de emergência, ${MESES_PADRAO} meses, ainda não criada`} texto={PEDIDO_RESERVA} uid={uid}><Blocos n={MESES_PADRAO} cheios={0} /></LinhaPedido></li>}
        {ordem.length === 0 && <li><LinhaPedido icone="ph-plus" rotulo="Criar meta" rotuloAcessivel="Criar meta" texto={PEDIDO_META} uid={uid} /></li>}
        {vistas.map((m) => <li key={m.id}><MetaPilula m={m} custo={d.custo_mensal} /></li>)}
      </ul>
      {mais}
    </>
  );
}
