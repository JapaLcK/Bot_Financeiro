import { useId, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ErroApi, comPrazoLeitura, contaQuery } from "../lib/v2";
import { href } from "../router";
import { HORAS, falhaDe, notificacoesQuery, salvarNotificacoes, telefoneBR, uidValido, type CorpoNotificacoes, type Falha, type Notificacoes } from "./api";

type Campo = Exclude<keyof CorpoNotificacoes, "daily_report_hour" | "daily_report_minute">;
const hhmm = (h: number, m: number) => `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;

// Preferências de notificação (dentro de /conta/configuracoes): as mesmas da /settings, cada uma salva sozinha ao mudar. Só rotas antigas
// (/settings/{uid}/notifications); o `uid` vem só do /auth/dashboard-profile. Cada dado é um ícone, o rótulo e o interruptor (como o Perfil).
export function ContaNotificacoes() {
  const uid = useQuery(contaQuery).data?.user_id;
  return uidValido(uid) ? <Preferencias key={uid} uid={uid} /> : null; // sem uid (ou protótipo) nenhum pedido sai
}

// No MÓDULO, não no componente: sair da rota e voltar remonta `Preferencias` e uma trava por instância liberaria um 2º PATCH com o 1º em voo
// (e o corpo do 2º sairia do cache velho). Síncrona: dois cliques no mesmo tick não passam por `previa`.
const voando = { current: false };

function Preferencias({ uid }: { uid: number }) {
  const id = useId();
  const qc = useQueryClient();
  const chave = notificacoesQuery(uid).queryKey;
  const q = useQuery(notificacoesQuery(uid));
  const [previa, setPrevia] = useState<Partial<Notificacoes> | null>(null); // o valor que o dono acabou de pedir, até o servidor responder
  const [ocupado, setOcupado] = useState(false); // travado do clique ao fim do salvamento, releitura da falha incluída (`previa` já volta antes, para o valor)
  const [aviso, setAviso] = useState("");
  const [falha, setFalha] = useState<Falha | null>(null);
  const [desligou, setDesligou] = useState(""); // D5: desligar "Insights financeiros" levou a adesão junto; nota VISÍVEL

  const erro = q.error instanceof ErroApi ? q.error : null;
  const sessao = erro?.status === 401; // sessão acabada esconde o que ficou em cache
  const semPlano = erro?.status === 402;
  const dados = q.isError ? undefined : q.data; // erro de carga nunca mostra o dado velho
  const d = dados && { ...dados, ...previa };

  // No máximo UM PATCH em voo. Sucesso: a tela segue a RESPOSTA (não o que foi clicado) e a releitura a confirma (o invalidate também cancela
  // um GET que já estava em voo, que traria o valor de antes). Falha: releitura SEMPRE, para o interruptor voltar ao que o servidor tem.
  const salvar = async (corpo: CorpoNotificacoes, pedido: Partial<Notificacoes> = corpo) => {
    if (voando.current) return;
    voando.current = true;
    setOcupado(true);
    setPrevia(pedido);
    setFalha(null);
    setDesligou("");
    setAviso("Salvando…");
    try {
      const antes = dados?.insight_alert_email_enabled === true;
      const r = await salvarNotificacoes(uid, corpo);
      qc.setQueryData(chave, r);
      if (antes && !r.insight_alert_email_enabled && !("insight_alert_email_enabled" in corpo)) setDesligou("Avisos do Piggy por e-mail desligados.");
      setAviso("Preferências salvas");
      qc.invalidateQueries({ queryKey: chave });
    } catch (e) {
      setAviso("");
      setFalha(falhaDe(e, "Não foi possível salvar. Tente de novo."));
      setPrevia(null); // a tela volta já ao último dado do servidor; a releitura (até 30 s) só o confirma
      await comPrazoLeitura(qc.invalidateQueries({ queryKey: chave })); // estourou o prazo: não prende a tela
    } finally { voando.current = false; setOcupado(false); setPrevia(null); }
  };

  const interruptor = (campo: Campo, rotulo: string, ligado: boolean, travado: boolean, selo?: string, espelho?: keyof Notificacoes, nota?: string, estado?: string) => (
    <li key={campo}>
      <label htmlFor={`${id}-${campo}`}>{rotulo}{selo && <> <span className="selo">{selo}</span></>}</label>
      {/* aria-disabled (não disabled) em voo: o foco do teclado não cai no <body>; a trava é o `voando`; `ocupado` a espelha */}
      <input id={`${id}-${campo}`} className="notif-switch" type="checkbox" role="switch" checked={ligado} disabled={travado} aria-disabled={ocupado ? true : undefined} aria-describedby={[nota && `${id}-${campo}-nota`, estado && `${id}-${campo}-estado`].filter(Boolean).join(" ") || undefined}
        onChange={(e) => salvar({ [campo]: e.target.checked }, { [campo]: e.target.checked, ...(espelho && { [espelho]: e.target.checked }) })} />
      {nota && <p id={`${id}-${campo}-nota`} className="notif-nota">{nota}</p>}
      {estado && <p id={`${id}-${campo}-estado`} className="notif-nota">{estado}</p>}
    </li>
  );
  const vazio = <><span aria-hidden="true">—</span><span className="sr-only">Não vinculado</span></>;
  const grupo = (rotulo: string, icone: string, valor: string | null, adicionar: string, itens: ReactNode) => (
    <div role="group" aria-labelledby={`${id}-${icone}`} className="notif-grupo">
      <h3 className="sr-only" id={`${id}-${icone}`}>{rotulo}</h3>
      <p className="notif-dado"><i className={`ph ${icone}`} aria-hidden="true" /><span>{valor || vazio}</span>
        {!valor && <a className="btn btn-ghost" href={href("/conta/perfil")}>{adicionar}</a>}</p>
      <ul>{itens}</ul>
    </div>
  );

  let corpo: ReactNode;
  if (q.isPending) corpo = <p role="status" className="faint">Carregando…</p>;
  // Sem plano, desligar o aviso é só no /settings (decisão do dono, §11 do plano B6): conta cortada nem chega ao /painel.
  else if (semPlano) corpo = (
    <div className="empty"><i className="ph ph-lock" aria-hidden="true" /><p>Indisponível sem plano ativo</p><a className="btn btn-ghost" href="/precos">Ver planos</a></div>
  );
  else if (!d || sessao) corpo = (
    <div className="empty" role="alert">
      <p>{sessao ? "Sua sessão terminou." : "Não foi possível carregar as notificações."}</p>
      {sessao ? <button type="button" className="btn btn-ghost retry" onClick={() => location.reload()}>Recarregar</button>
        : <button type="button" className="btn btn-ghost retry" onClick={() => q.refetch()}>Tentar de novo</button>}
    </div>
  );
  else {
    const semanal = d.weekly_report_available ? d.weekly_report_enabled : d.weekly_report_stored_enabled; // fora do plano, o gravado (para poder desligar)
    const aderiu = d.insight_alert_email_enabled, noPlano = d.insight_alert_email_available; // ligar pede tudo em ordem; desligar nunca trava
    const hora = d.daily_report_hour, fora = !HORAS.includes(hora) || d.daily_report_minute !== 0;
    corpo = <>
      {grupo("E-mail", "ph-envelope", d.email, "Adicionar e-mail", <>
        {interruptor("tip_email_enabled", "Dicas de uso", d.tip_email_enabled, !d.email_notifications_available)}
        {interruptor("insight_email_enabled", "Insights financeiros", d.insight_email_enabled, !d.email_notifications_available, undefined, undefined, d.insight_email_off_notice ?? undefined)}
        {d.insight_alert_email_notice && interruptor("insight_alert_email_enabled", "Avisos do Piggy por e-mail", aderiu,
          !aderiu && (!d.email_notifications_available || !noPlano || !d.insight_email_enabled),
          !noPlano ? (aderiu ? undefined : "Plus e Pro") : d.email_notifications_available && !d.insight_email_enabled ? "Ligue Insights financeiros" : undefined,
          undefined, d.insight_alert_email_notice, d.insight_alert_email_paused_notice ?? undefined)}
      </>)}
      {grupo("WhatsApp", "ph-whatsapp-logo", d.whatsapp_destination && telefoneBR(d.whatsapp_destination), "Conectar WhatsApp", <>
        {interruptor("whatsapp_updates_enabled", "Atualizações do Piggy", d.whatsapp_updates_enabled, !d.whatsapp_updates_available)}
        {interruptor("daily_report_enabled", "Resumo diário", d.daily_report_enabled, false)}
        {interruptor("weekly_report_enabled", "Resumo semanal", semanal, !d.weekly_report_available && !d.weekly_report_stored_enabled,
          d.weekly_report_available ? undefined : d.weekly_report_stored_enabled ? "Pausado no seu plano" : "Plus e Pro", "weekly_report_stored_enabled")}
        {interruptor("monthly_report_enabled", "Resumo mensal", d.monthly_report_enabled, false)}
        <li className="notif-hora">
          <i className="ph ph-clock" aria-hidden="true" />
          <label htmlFor={`${id}-hora`}>Horário dos resumos</label>
          <select id={`${id}-hora`} className="field" value={fora ? "atual" : String(hora)} aria-disabled={ocupado ? true : undefined}
            disabled={!d.daily_report_enabled && !d.weekly_report_enabled && !d.monthly_report_enabled}
            onChange={(e) => { const h = Number(e.target.value); if (h >= 0) salvar({ daily_report_hour: h, daily_report_minute: 0, daily_report_enabled: dados!.daily_report_enabled }, { daily_report_hour: h, daily_report_minute: 0 }); }}>
            {HORAS.map((h) => <option key={h} value={h}>{hhmm(h, 0)}</option>)}
            {fora && <option value="atual">{hhmm(hora, d.daily_report_minute)}</option>}
          </select>
        </li>
      </>)}
      {desligou && <p role="status" className="notif-desligou">{desligou}</p>}
      {falha && <div role="alert" className="sess-falha"><span className="warn">{falha.texto}</span>
        {falha.acao === "recarregar" && <button type="button" className="btn btn-ghost" onClick={() => location.reload()}>Recarregar</button>}</div>}
    </>;
  }

  return (
    <section className="panel span-12 conta-perfil conta-notif" aria-labelledby={`${id}-t`}>
      <h2 id={`${id}-t`}>Notificações</h2>
      {corpo}
      <p role="status" className="sr-only">{aviso}</p>
    </section>
  );
}
