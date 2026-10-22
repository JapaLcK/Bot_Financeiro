import { useEffect, useId, useRef, useState } from "react";
import { avaliarIa, type Msg } from "../lib/conversation";
import { ICON } from "../lib/brand";
import { MOTIVOS, PREPARANDO, TEXTO_MAX, type Motivo } from "../lib/conversaIa";
import { TextoPiggy } from "./TextoPiggy";

// A resposta do Piggy quando o chat vai à IA (lib/conversaIa.ts): o último passo enquanto pensa, o texto que chega aos poucos, o texto final
// INTEIRO (nunca cortado nem palavra a palavra: é o que o leitor de tela anuncia), o erro, a parada e a cota. Os cards chegam no PR 3: cada
// bloco vira um marcador neutro, sem número.
export const SEM_CARD = "Este card ainda não aparece aqui.";

/** O campo do "Outro": o texto mora aqui, então some com ele ao fechar; falhou = o campo segue aberto com o texto. Corta por code point (o servidor conta assim; um 🐷 vale 2 em UTF-16). */
function Comentario({ id }: { id: number }) {
  const [texto, setTexto] = useState(""), ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => ref.current?.focus(), []);
  return (
    <div className="msg-comentario">
      <textarea ref={ref} rows={2} value={texto} aria-label="Comentário sobre a resposta" placeholder="Conte o que houve (opcional)"
        onChange={(e) => setTexto(Array.from(e.target.value).slice(0, TEXTO_MAX).join(""))} />
      <button type="button" className="btn btn-primary" disabled={!texto.trim()} onClick={() => void avaliarIa(id, -1, "outro", texto.trim())}>Enviar</button>
    </div>
  );
}

/** 👍👎 da última resposta, e sob o 👎 os motivos. Os botões não ganham `disabled` durante o POST (tiraria o foco de quem está neles): `avaliarIa` ignora o clique. */
function Avaliar({ m }: { m: Msg }) {
  const rotulo = useId(), outro = useRef<HTMLButtonElement>(null);
  // Enviar o "Outro" fecha o campo com o foco dentro: sem isto ele cai no <body>. Só devolve se caiu lá (quem foi para outro lugar fica onde está).
  useEffect(() => { if (m.avisoAvaliar === "obrigado" && document.activeElement === document.body) outro.current?.focus(); }, [m.avisoAvaliar]);
  const nota = (n: 1 | -1) => void avaliarIa(m.id, m.feedback === n ? null : n);
  return (
    <>
      <div className="msg-avaliar" role="group" aria-label="Avaliar resposta">
        <button type="button" className="icon-btn" aria-label="Gostei da resposta" aria-pressed={m.feedback === 1} onClick={() => nota(1)}><i className="ph ph-thumbs-up" aria-hidden="true" /></button>
        <button type="button" className="icon-btn" aria-label="Não gostei da resposta" aria-pressed={m.feedback === -1} onClick={() => nota(-1)}><i className="ph ph-thumbs-down" aria-hidden="true" /></button>
      </div>
      {m.feedback === -1 && (
        <>
          <p className="msg-nota" id={rotulo}>O que houve?</p>
          <div className="msg-motivos" role="group" aria-labelledby={rotulo}>
            {(Object.keys(MOTIVOS) as Motivo[]).map((k) => (
              <button key={k} ref={k === "outro" ? outro : undefined} type="button" className="chip" aria-pressed={m.motivo === k} onClick={() => void avaliarIa(m.id, -1, m.motivo === k ? undefined : k)}>{MOTIVOS[k]}</button>
            ))}
          </div>
          {m.motivo === "outro" && m.avisoAvaliar !== "obrigado" && <Comentario id={m.id} />}
        </>
      )}
      {m.avisoAvaliar === "falha" && <p className="msg-nota" role="alert">Não conseguimos registrar sua avaliação.</p>}
      {m.avisoAvaliar === "obrigado" && <p className="msg-nota" role="status">Obrigado pelo retorno</p>}
    </>
  );
}

export function PiggyIa({ m, ultimo }: { m: Msg; ultimo: boolean }) {
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
      {ultimo && m.estado === "pronto" && typeof m.messageId === "number" && <Avaliar m={m} />}
      {m.estado === "cota" && <div className="msg-acoes"><a className="btn btn-primary" href="/precos">Ver planos</a></div>}
    </>
  );
}
