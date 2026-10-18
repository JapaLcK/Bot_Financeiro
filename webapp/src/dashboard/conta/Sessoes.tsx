import { useId, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ErroApi } from "../lib/v2";
import { NATIVE, useDialogo } from "../parts/dialog";
import { DETALHES, comPrazoLeitura, encerrarOutras, encerrarSessao, falhaDe, horaRelativa, sessoesQuery, type Falha, type Sessao, type SessoesAtivas } from "./api";

const MOVEL = /iOS|iPadOS|Android|PigBank app/; // o rótulo do servidor (core/sessions.py: device_label); sem critério melhor, o resto é computador

// O diálogo de confirmação. `agir` NUNCA lança (o pai guarda a falha); terminado, o diálogo sempre fecha.
function Confirma({ titulo, texto, botao, agir, fechar }: { titulo: string; texto: string; botao: string; agir: () => Promise<void>; fechar: () => void }) {
  const id = useId();
  const voando = useRef(false); // síncrona: dois cliques/Enter no mesmo tick não passam por `pendente`; também trava o fechar
  const [pendente, setPendente] = useState(false);
  const { dlg, fecharDialog, fundo, props } = useDialogo(fechar, () => voando.current);
  const ir = async () => {
    if (voando.current) return;
    voando.current = true;
    setPendente(true);
    await agir();
    voando.current = false; // o fechar está travado em voo
    if (dlg.current?.isConnected) fecharDialog();
  };
  return <>
    <dialog {...props} className={`lanc-form${NATIVE ? "" : " lanc-form-fb"}`} aria-labelledby={`${id}-t`}>
      <div className="cofre">
        <h2 id={`${id}-t`}>{titulo}</h2>
        <p>{texto}</p>
        <footer>
          <button className="btn btn-ghost" type="button" onClick={fecharDialog} disabled={pendente}>Cancelar</button>
          <button className="btn btn-primary" type="button" onClick={ir} disabled={pendente}>{pendente ? "Encerrando…" : botao}</button>
        </footer>
      </div>
    </dialog>
    {!NATIVE && <div className="lanc-scrim" aria-hidden="true" onClick={fundo} />}
  </>;
}

// Dispositivos conectados (dentro de /conta/configuracoes): as sessões ativas, o encerrar de cada uma e o das outras. Só rotas antigas
// (/settings/{uid}/sessions); o `uid` já chega validado. A sessão atual não tem botão (o servidor também recusa: 400).
export function Sessoes({ uid }: { uid: number }) {
  const id = useId();
  const qc = useQueryClient();
  const chave = sessoesQuery(uid).queryKey;
  const q = useQuery(sessoesQuery(uid));
  const [confirma, setConfirma] = useState<string | "outras" | null>(null);
  const [aviso, setAviso] = useState("");
  const [falha, setFalha] = useState<Falha | null>(null);
  const titulo = useRef<HTMLHeadingElement>(null);
  const foi = useRef(false); // encerrou: a linha (e o botão que abriu o diálogo) some, então o foco vai ao título

  const dados = q.isError ? undefined : q.data; // erro de carga nunca mostra a lista velha
  const sessao = q.error instanceof ErroApi && q.error.status === 401; // sessão acabada esconde o que ficou em cache
  const lista = dados?.sessions ?? [];
  const atual = (s: { jti: string; is_current: boolean }) => s.is_current || (!!dados?.current_jti && s.jti === dados.current_jti);
  const outras = lista.filter((s) => !atual(s));

  // Depois do DELETE: a lista sem a(s) revogada(s) na hora, e a releitura a confirma. O GET que já estava em voo trouxe a lista de ANTES
  // e não pode repintar a sessão revogada: o invalidateQueries (cancelRefetch, o padrão) o cancela, no mesmo tick do setQueryData;
  // um cancelQueries antes seria redundante (medido: tirá-lo não reprova o teste do GET em voo; tirar o invalidate reprova).
  const agir = async (jti: string | null, executar: () => Promise<string>) => {
    setAviso("");
    setFalha(null);
    const alvo = (s: Sessao, d: SessoesAtivas) => (jti ? s.jti === jti : !(s.is_current || s.jti === d.current_jti)); // o que o botão que abriu o diálogo encerra
    try {
      const anuncio = await executar();
      qc.setQueryData<SessoesAtivas>(chave, (d) => d && { ...d, sessions: d.sessions.filter((s) => !alvo(s, d)) });
      setAviso(anuncio);
      foi.current = true;
    } catch (e) {
      setFalha(falhaDe(e, jti ? "Erro ao encerrar sessão" : "Erro ao encerrar outras sessões", DETALHES)); // o 404 ("já encerrada") sai como veio
      const soltou = await comPrazoLeitura(qc.invalidateQueries({ queryKey: chave })); // null = estourou o prazo: o diálogo não fica preso
      const d = qc.getQueryData<SessoesAtivas>(chave); // a releitura pode tirar a linha (404) ou virar bloco de erro (o dado velho fica no cache): sem botão, o foco cairia no <body>
      if (soltou === null || qc.getQueryState(chave)?.status === "error" || !d?.sessions.some((s) => alvo(s, d))) foi.current = true;
      return;
    }
    qc.invalidateQueries({ queryKey: chave });
  };
  const confirmar = confirma === "outras"
    ? { titulo: "Encerrar todas as outras sessões?", texto: "Todos os outros dispositivos serão deslogados imediatamente. Esta sessão atual continua ativa.", botao: "Encerrar outras",
      agir: () => agir(null, async () => `${(await encerrarOutras(uid)).revoked || 0} sessão(ões) encerrada(s)`) }
    : confirma ? { titulo: "Encerrar esta sessão?", texto: "O dispositivo será deslogado imediatamente.", botao: "Encerrar",
      agir: () => agir(confirma, async () => { await encerrarSessao(uid, confirma); return "Sessão encerrada"; }) } : null;

  return (
    <section className="panel span-12 conta-perfil conta-sessoes" aria-labelledby={`${id}-t`}>
      <h2 id={`${id}-t`} ref={titulo} tabIndex={-1}>Dispositivos conectados</h2>
      {q.isPending ? <p role="status" className="faint">Carregando…</p>
        : !dados ? (
          <div className="empty" role="alert">
            <p>{sessao ? "Sua sessão terminou." : "Não foi possível carregar as sessões."}</p>
            {sessao ? <button type="button" className="btn btn-ghost retry" onClick={() => location.reload()}>Recarregar</button>
              : <button type="button" className="btn btn-ghost retry" onClick={() => q.refetch()}>Tentar de novo</button>}
          </div>
        ) : !lista.length ? <p className="faint">Nenhuma sessão ativa.</p> : (
          <ul className="sess-lista">
            {lista.map((s, i) => (
              <li key={s.jti}>
                <i className={`ph ${MOVEL.test(String(s.device_label)) ? "ph-device-mobile" : "ph-desktop"}`} aria-hidden="true" />
                <div>
                  <b id={`${id}-${i}`}>{s.device_label}</b>{atual(s) && <> <span className="selo">Esta sessão</span></>}
                  <span className="sess-meta">{[s.ip || "IP desconhecido", s.last_seen_at && horaRelativa(s.last_seen_at) && `ativa ${horaRelativa(s.last_seen_at)}`].filter(Boolean).join(" · ")}</span>
                </div>
                {!atual(s) && <button type="button" className="btn btn-ghost" aria-describedby={`${id}-${i}`} onClick={() => { setAviso(""); setFalha(null); setConfirma(s.jti); }}>Encerrar</button>}
              </li>
            ))}
          </ul>
        )}
      {outras.length > 0 && <button type="button" className="btn btn-ghost sess-todas" onClick={() => { setAviso(""); setFalha(null); setConfirma("outras"); }}>Encerrar todas as outras</button>}
      {falha && <div role="alert" className="sess-falha"><span className="warn">{falha.texto}</span>
        {falha.acao === "recarregar" && <button type="button" className="btn btn-ghost" onClick={() => location.reload()}>Recarregar</button>}</div>}
      <p role="status" className="sr-only">{aviso}</p>
      {confirmar && <Confirma {...confirmar} fechar={() => { setConfirma(null); if (foi.current) { foi.current = false; titulo.current?.focus(); } }} />}
    </section>
  );
}
