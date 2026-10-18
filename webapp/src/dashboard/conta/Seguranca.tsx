import { useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { ErroApi, contaQuery } from "../lib/v2";
import { DETALHES, enviarLinkSenha, falhaDe, mfaStatusQuery, temSenhaQuery, type Falha } from "./api";
import { MfaForm, type ModoMfa } from "./MfaForm";
import { SemSenha } from "./SemSenha";

const vazio = (palavra: string) => <><span aria-hidden="true">—</span><span className="sr-only">{palavra}</span></>;

// Segurança da conta (dentro de /conta/configuracoes): a senha (o link por e-mail, igual à /settings) e o MFA. Só rotas antigas
// (/settings/{uid}/password-reset, /auth/mfa/*); o `uid` vem só do /auth/dashboard-profile. Cada dado é um ícone e o valor;
// a palavra só existe para o leitor de tela (como o Perfil).
export function ContaSeguranca() {
  const uid = useQuery(contaQuery).data?.user_id;
  const uidOk = typeof uid === "number" && Number.isInteger(uid) && uid > 0; // 0, -3, 1.5 viram /settings/0/password-reset
  const mfa = useQuery(mfaStatusQuery);
  // Só `false` (conta só-Google) abre o diálogo "defina uma senha"; carregando ou falhou, o formulário abre e o 409 do servidor decide.
  const temSenha = useQuery(temSenhaQuery(true)).data?.temSenha;
  const [aberto, setAberto] = useState<ModoMfa | "sem-senha" | null>(null);
  const [aviso, setAviso] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [linkOk, setLinkOk] = useState("");
  const [linkFalha, setLinkFalha] = useState<Falha | null>(null);
  const voandoLink = useRef(false); // síncrona: dois cliques no mesmo tick não passam por `enviando`
  const botaoSenha = useRef<HTMLButtonElement>(null);

  // Falha do status (também a de uma releitura com dado velho em cache) nunca cai em "desativado": sem "Ativar".
  const dados = mfa.isError ? undefined : mfa.data;
  const sessao = mfa.error instanceof ErroApi && mfa.error.status === 401; // sessão acabada esconde o que ficou em cache
  const restantes = dados?.backup_codes_remaining;

  const mandarLink = async () => {
    if (voandoLink.current || !uidOk) return;
    voandoLink.current = true;
    setEnviando(true);
    setLinkOk("");
    setLinkFalha(null);
    try {
      const r = await enviarLinkSenha(uid);
      setLinkOk(typeof r.message === "string" ? r.message : "");
    } catch (e) {
      setLinkFalha(falhaDe(e, "Erro ao enviar reset", DETALHES));
    } finally { voandoLink.current = false; setEnviando(false); }
  };
  const abrir = (m: ModoMfa) => { setAviso(""); setAberto(temSenha === false ? "sem-senha" : m); };
  const acao = (m: ModoMfa, texto: string, primaria = false) => <button type="button" className={`btn ${primaria ? "btn-primary" : "btn-ghost"}`} onClick={() => abrir(m)}>{texto}</button>;
  const linha = (icone: string, rotulo: string, valor: ReactNode, extra: ReactNode) => (
    <div>
      <dt><i className={`ph ${icone}`} aria-hidden="true" /><span className="sr-only">{rotulo}</span></dt>
      <dd>{valor}</dd>
      {extra}
    </div>
  );
  const erro = (texto: string, botao: ReactNode) => <dd className="seg-extra"><span role="alert" className="warn">{texto}</span>{botao}</dd>;
  const mfaValor = dados ? (dados.enabled ? <><i className="ph ph-check-circle perfil-ok" aria-hidden="true" /><span className="sr-only">Ativada</span></> : vazio("Desativada"))
    : mfa.isPending ? <span className="faint" role="status">Carregando…</span> : null;
  const mfaExtra = sessao ? erro("Sua sessão terminou.", <button type="button" className="btn btn-ghost retry" onClick={() => location.reload()}>Recarregar</button>)
    : mfa.isError ? erro("Não deu para carregar a segurança da conta.", <button type="button" className="btn btn-ghost retry" onClick={() => mfa.refetch()}>Tentar de novo</button>)
      : dados && <>
        {dados.enabled && Number.isFinite(restantes) && <dd className="seg-extra">Códigos de backup restantes: {restantes} de 10</dd>}
        <dd className="seg-extra seg-acoes">
          {dados.enabled ? <>{acao("desativar", "Desativar")}{acao("regerar", "Gerar novos códigos de backup")}</> : acao("ativar", "Ativar")}
        </dd>
      </>;

  return (
    <>
      <section className="panel span-12 conta-perfil conta-seguranca" aria-labelledby="seg-t">
        <h2 id="seg-t">Segurança</h2>
        <dl className="detail-facts col">
          {linha("ph-lock-key", "Senha", temSenha === false ? vazio("Não definido") : <span aria-hidden="true">••••••••</span>, <>
            <dd className="seg-extra">
              <button type="button" className="btn btn-ghost" ref={botaoSenha} disabled={enviando || !uidOk} onClick={mandarLink}>
                {enviando ? "Enviando…" : temSenha === false ? "Definir senha" : "Resetar senha por e-mail"}
              </button>
            </dd>
            <dd className="seg-extra"><span role="status">{linkOk}</span></dd>
            {linkFalha && erro(linkFalha.texto, linkFalha.acao === "recarregar" && <button type="button" className="btn btn-ghost" onClick={() => location.reload()}>Recarregar</button>)}
          </>)}
          {linha(dados?.enabled ? "ph-shield-check" : "ph-shield", "Autenticação em 2 etapas", mfaValor, mfaExtra)}
        </dl>
        <p role="status" className="sr-only">{aviso}</p>
      </section>
      {aberto === "sem-senha" && <SemSenha fechar={() => setAberto(null)} definir={() => botaoSenha.current?.focus()} />}
      {aberto && aberto !== "sem-senha" && !sessao && <MfaForm modo={aberto} fechar={() => setAberto(null)} salvou={setAviso} />}
    </>
  );
}
