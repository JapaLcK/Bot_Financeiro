import type { Msg } from "../lib/conversation";
import { ICON } from "../lib/brand";
import { PREPARANDO } from "../lib/conversaIa";
import { TextoPiggy } from "./TextoPiggy";

// A resposta do Piggy quando o chat vai à IA (lib/conversaIa.ts): o último passo enquanto pensa, o texto que chega aos poucos, o texto final
// INTEIRO (nunca cortado nem palavra a palavra: é o que o leitor de tela anuncia), o erro, a parada e a cota. Os cards chegam no PR 3: cada
// bloco vira um marcador neutro, sem número.
export const SEM_CARD = "Este card ainda não aparece aqui.";

export function PiggyIa({ m }: { m: Msg }) {
  const texto = typeof m.text === "string" ? m.text : "";
  const voo = m.estado === "enviando", status = voo && !m.parcial; // chegado o 1º texto, o status sai (como no app)
  const passo = m.passos?.[m.passos.length - 1]; // sem .at (Safari 14)
  return (
    <>
      <p className="msg-autor"><img src={ICON} alt="" width={22} height={22} /><b>Piggy</b></p>
      {/* Em voo, o anel e o último passo são enfeite (aria-hidden); quem fala com o leitor de tela é o texto sr-only, como na resposta de agente. */}
      {status && <p className="msg-text" data-carga aria-busy="true">{PREPARANDO}</p>}
      {status && <p className="msg-carga" aria-hidden="true"><i className="msg-carga-anel" /><span className="msg-carga-frase">{passo ?? PREPARANDO}</span></p>}
      {m.blocos?.map((_, i) => <p key={i} className="msg-text msg-bloco">{SEM_CARD}</p>)}
      <TextoPiggy texto={voo || m.estado === "parou" ? m.parcial ?? "" : texto} />
      {m.estado === "parou" && <p className="msg-text msg-nota">{texto}</p>}
      {m.estado === "cota" && <div className="msg-acoes"><a className="btn btn-primary" href="/precos">Ver planos</a></div>}
    </>
  );
}
