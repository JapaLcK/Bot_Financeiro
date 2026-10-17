import { useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { ErroApi, FUSO, contaQuery, instante } from "../lib/v2";
import { route } from "../router";
import { gerarLinkWhatsApp, segurancaQuery, telefoneBR, type Campo } from "./api";
import { ContatoForm } from "./ContatoForm";

const EDITAR: Record<Campo, string> = { nome: "Alterar nome", email: "Alterar e-mail", telefone: "Alterar telefone" };
const CELULAR = /Android|iPhone|iPad|iPod|Mobile/i; // o mesmo teste do home.html e do nav-auth.js
const mesEAno = new Intl.DateTimeFormat("pt-BR", { timeZone: FUSO, month: "long", year: "numeric" });

// Perfil da Conta (/conta/perfil): cada dado é um ícone e o valor; a palavra só existe para o leitor de tela.
export function ContaPerfil() {
  const conta = useQuery({ ...contaQuery, refetchOnWindowFocus: true });
  const uid = conta.data?.user_id;
  const seg = useQuery(segurancaQuery(uid));
  const [aberto, setAberto] = useState<Campo | null>(null);
  const [aviso, setAviso] = useState("");
  const [conectando, setConectando] = useState(false);
  const [erroWa, setErroWa] = useState("");
  const voandoWa = useRef(false); // síncrona: dois cliques no mesmo tick não passam por `conectando`
  const lapis = useRef<Partial<Record<Campo, HTMLButtonElement | null>>>({});

  const dados = seg.data;
  const erro = [conta.error, seg.error].find((e) => e) ?? null;
  const semUid = conta.isSuccess && !uid;
  const carregando = !dados && !semUid && !erro;
  const sessao = erro instanceof ErroApi && erro.status === 401;
  const desde = dados?.created_at ? instante(dados.created_at) : NaN;

  const conectar = async () => {
    if (voandoWa.current) return;
    voandoWa.current = true;
    setConectando(true);
    setErroWa("");
    // No computador a janela abre no PRÓPRIO clique (depois do await o navegador a bloquearia) e recebe o link na volta.
    const celular = CELULAR.test(navigator.userAgent || "");
    const janela = celular ? null : window.open("", "_blank");
    try {
      const link = (await gerarLinkWhatsApp()).whatsapp_link;
      if (!link) throw new ErroApi(200, "sem_link");
      if (!/^(https|whatsapp):\/\//i.test(link)) throw new ErroApi(200, "link_invalido"); // nunca javascript: / data: no href
      if (janela) { janela.opener = null; janela.location.href = link; }
      else location.assign(link); // celular, ou janela barrada
    } catch (e) {
      janela?.close();
      setErroWa(e instanceof ErroApi && e.code === "sem_link" ? "WhatsApp ainda não configurado neste ambiente."
        : e instanceof ErroApi && e.detalhe ? e.detalhe : "Não foi possível gerar seu link do WhatsApp agora.");
    } finally { voandoWa.current = false; setConectando(false); }
  };

  const vazio = <><span aria-hidden="true">—</span><span className="sr-only">Não definido</span></>;
  const linha = (icone: string, rotulo: string, valor: ReactNode, acao?: ReactNode, extra?: ReactNode) => (
    <div>
      <dt><i className={`ph ${icone}`} aria-hidden="true" /><span className="sr-only">{rotulo}</span></dt>
      <dd>{valor}</dd>
      {acao && <dd>{acao}</dd>}
      {extra}
    </div>
  );
  const editavel = (campo: Campo, icone: string, rotulo: string, texto: unknown) => linha(icone, rotulo, (typeof texto === "string" && texto.trim()) || vazio,
    <button type="button" className="icon-btn" aria-label={EDITAR[campo]} ref={(el) => { lapis.current[campo] = el; }} onClick={() => { setAviso(""); setAberto(campo); }}>
      <i className="ph ph-pencil-simple" aria-hidden="true" />
    </button>);

  return (
    <>
      <header className="page-head">
        <h1 id="page-title" tabIndex={-1}>{route("/conta/perfil").title}</h1>
      </header>
      <div className="page-grid">
        <section className="panel span-12 conta-perfil" aria-label="Sua conta">
          {carregando ? <p role="status" className="faint">Carregando…</p>
            : !dados ? (
              <div className="empty" role="alert">
                <p>{sessao ? "Sua sessão terminou." : "Não deu para carregar seu perfil."}</p>
                {sessao ? <button type="button" className="btn btn-ghost retry" onClick={() => location.reload()}>Recarregar</button>
                  : <button type="button" className="btn btn-ghost retry" onClick={() => { conta.refetch(); seg.refetch(); }}>Tentar de novo</button>}
              </div>
            ) : (
              <dl className="detail-facts col">
                {editavel("nome", "ph-user", "Nome", dados.display_name)}
                {editavel("email", "ph-envelope", "E-mail", dados.email)}
                {editavel("telefone", "ph-phone", "Telefone", typeof dados.phone === "string" ? telefoneBR(dados.phone) : null)}
                {conta.data?.whatsapp_linked
                  ? linha("ph-whatsapp-logo", "WhatsApp", <><i className="ph ph-check-circle perfil-ok" aria-hidden="true" /><span className="sr-only">Conectado</span></>)
                  : linha("ph-whatsapp-logo", "WhatsApp", <button type="button" className="btn btn-ghost" disabled={conectando} onClick={conectar}>Conectar</button>, null,
                    erroWa && <dd className="perfil-erro"><span role="alert" className="warn">{erroWa}</span></dd>)}
                {Number.isFinite(desde) && linha("ph-calendar-blank", "Membro desde", `Desde ${mesEAno.format(new Date(desde))}`)}
              </dl>
            )}
          <p role="status" className="sr-only">{aviso}</p>
        </section>
      </div>
      {aberto && uid && dados && (
        <ContatoForm campo={aberto} uid={uid} seguranca={dados} salvou={setAviso}
          fechar={() => { const c = aberto; setAberto(null); lapis.current[c]?.focus(); }} />
      )}
    </>
  );
}
