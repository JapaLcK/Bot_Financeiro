import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ICON } from "../lib/brand";
import { ErroApi, apiPost, guiaQuery } from "../lib/v2";
import { href, origemDaTela, route } from "../router";
import { dicaDe, pedirDica } from "./Dica";
import { abrirGuia } from "./Guia";

// A tela Ajuda (/ajuda, #/ajuda): contato, o que aprender e o feedback. O feedback vai por e-mail para o time
// (api/v2/feedback.py): o destino mora só no servidor, nunca neste bundle.
const TIPOS = [
  { v: "ideia", nome: "Ideia", dica: "O que faria o PigBank ficar melhor pra você?" },
  { v: "problema", nome: "Problema", dica: "O que aconteceu, e em qual tela?" },
  { v: "elogio", nome: "Elogio", dica: "O que você curtiu?" },
] as const;
const MAX = 4000;
const MUITAS = "Muitas tentativas. Aguarde alguns minutos e tente novamente."; // texto do próprio front (o `message` do servidor é ignorado, ver `falha`)

const falha = (e: unknown) =>
  e instanceof ErroApi && e.status === 429 ? MUITAS
  : e instanceof ErroApi && e.status === 422 ? `Escreva uma mensagem de até ${MAX} caracteres.`
  : "Não foi possível enviar agora. Sua mensagem continua aqui; tente de novo.";

const Instagram = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
    <rect x="3.5" y="3.5" width="17" height="17" rx="5" /><circle cx="12" cy="12" r="4" /><circle cx="17" cy="7" r="0.6" fill="currentColor" />
  </svg>
);

type Linha = { titulo: string; sub?: string; icone?: ReactNode; interna?: boolean; nova?: boolean } & ({ href: string; onClick?: never } | { onClick: () => void; href?: never });
function Item({ titulo, sub, icone, interna, nova, href: destino, onClick }: Linha) {
  const corpo = <>
    <span className="ajuda-ico" aria-hidden="true">{icone}</span>
    <span className="ajuda-txt"><span>{titulo}</span>{sub && <small>{sub}</small>}</span>
    <i className={`ph ${interna || onClick ? "ph-caret-right" : "ph-arrow-square-out"}`} aria-hidden="true" />
  </>;
  return <li>{destino != null
    ? <a className="ajuda-linha" href={destino} {...(nova ? { target: "_blank", rel: "noopener" } : {})}>{corpo}</a>
    : <button type="button" className="ajuda-linha" onClick={onClick}>{corpo}</button>}</li>;
}

function Feedback() {
  const [tipo, setTipo] = useState<(typeof TIPOS)[number]["v"]>("ideia");
  const [texto, setTexto] = useState("");
  const campo = useRef<HTMLTextAreaElement>(null);
  const titulo = useRef<HTMLHeadingElement>(null);
  const envio = useMutation({ mutationFn: (c: { tipo: typeof tipo; mensagem: string }) => apiPost("/feedback", c) });
  const voando = useRef(false); // síncrona: o `isPending` só chega no render seguinte, e dois cliques no mesmo tick passariam por ele
  const vazio = !texto.trim();
  const enviar = (e: FormEvent) => {
    e.preventDefault();
    if (vazio || voando.current) return;
    voando.current = true;
    envio.mutate({ tipo, mensagem: texto.trim() }, { onSettled: () => { voando.current = false; } });
  };
  useEffect(() => { if (envio.isSuccess) titulo.current?.focus({ preventScroll: true }); }, [envio.isSuccess]); // uma vez por sucesso, não a cada render
  const outro = () => { envio.reset(); setTexto(""); setTipo("ideia"); requestAnimationFrame(() => campo.current?.focus()); };

  if (envio.isSuccess) {
    return (
      <section className="ajuda-card ajuda-feedback" aria-labelledby="ajuda-t-fb">
        <div className="ajuda-ok">
          <i className="ph ph-check-circle" aria-hidden="true" />
          <div>
            <h2 id="ajuda-t-fb" tabIndex={-1} ref={titulo}>Recebemos, valeu</h2>
            <p>Seu feedback chegou no time do PigBank.</p>
          </div>
          <button type="button" className="btn btn-ghost ajuda-btn" onClick={outro}>Enviar outro</button>
        </div>
      </section>
    );
  }
  return (
    <section className="ajuda-card ajuda-feedback" aria-labelledby="ajuda-t-fb">
      <form onSubmit={enviar}>
        <div>
          <h2 id="ajuda-t-fb">Conte o que achou</h2>
          <p>Vai direto pro time do PigBank.</p>
        </div>
        <div className="ajuda-tipos" role="radiogroup" aria-label="Tipo de feedback">
          {TIPOS.map((t) => (
            <label key={t.v}>
              <input type="radio" name="tipo" value={t.v} checked={tipo === t.v} onChange={() => setTipo(t.v)} />
              <span>{t.nome}</span>
            </label>
          ))}
        </div>
        <label className="ajuda-msg">
          Sua mensagem
          <textarea ref={campo} rows={7} maxLength={MAX} value={texto} onChange={(e) => setTexto(e.target.value)} placeholder={TIPOS.find((t) => t.v === tipo)!.dica} />
        </label>
        <p className="ajuda-nota">Se precisar responder, usamos o e-mail da sua conta.</p>
        {envio.isError && <p className="ajuda-erro" role="alert">{falha(envio.error)}</p>}
        <button type="submit" className="btn btn-primary ajuda-btn" disabled={vazio || envio.isPending}>Enviar feedback</button>
      </form>
    </section>
  );
}

export function AjudaTela() {
  // De onde a pessoa veio (router.ts): a linha "Como funciona" só existe quando aquela tela tem dica para o plano.
  const origem = origemDaTela();
  const guia = useQuery(guiaQuery).data;
  const comDica = origem && dicaDe(guia, origem) ? origem : null;
  return (
    <div className="span-12 ajuda-grade">
      <section className="ajuda-card ajuda-contato" aria-labelledby="ajuda-t-contato">
        <h2 id="ajuda-t-contato">Fale com a gente</h2>
        <ul>
          <Item href={href("/piggy")} interna titulo="Converse com o Piggy" sub="Tira dúvidas aqui no painel" icone={<img src={ICON} alt="" width={28} height={28} />} />
          <Item href="/wa" nova titulo="WhatsApp do Piggy" sub="O mesmo Piggy, no seu WhatsApp" icone={<i className="ph ph-whatsapp-logo" />} />
          <Item href="mailto:suporte@pigbankai.com" titulo="suporte@pigbankai.com" sub="Respondemos em até 1 dia útil" icone={<i className="ph ph-envelope" />} />
          <Item href="/suporte/contato" titulo="Formulário de contato" sub="Escreva pelo site" icone={<i className="ph ph-note-pencil" />} />
          <Item href="https://instagram.com/pigbank.oficial" nova titulo="@pigbank.oficial" sub="Instagram do PigBank" icone={<Instagram />} />
        </ul>
      </section>
      <section className="ajuda-card ajuda-aprender" aria-labelledby="ajuda-t-aprender">
        <h2 id="ajuda-t-aprender">Aprenda a usar</h2>
        <ul>
          <Item onClick={abrirGuia} titulo="Guia do painel" icone={<i className="ph ph-question" />} />
          {comDica && <Item onClick={() => pedirDica(comDica)} titulo={`Como funciona ${route(comDica).label}`} icone={<i className="ph ph-info" />} />}
          <Item href="/suporte" titulo="Central de ajuda" icone={<i className="ph ph-lifebuoy" />} />
          <Item href="/comandos" titulo="O que pedir ao Piggy" icone={<i className="ph ph-chat-circle" />} />
        </ul>
      </section>
      <Feedback />
    </div>
  );
}
