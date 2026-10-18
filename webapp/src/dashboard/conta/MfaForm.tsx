import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { NATIVE, useDialogo } from "../parts/dialog";
import { ErroApi } from "../lib/v2";
import { DETALHES, ativarMfa, desativarMfa, falhaDe, iniciarMfa, mfaStatusQuery, regerarCodigos, type Falha } from "./api";
import { abrirCofre } from "./Cofre";

export type ModoMfa = "ativar" | "desativar" | "regerar";
const T2 = "O MFA foi ativado, mas os códigos não chegaram. Gere novos códigos.";
const T5 = "Não foi possível ativar agora. Tente de novo.";
const T_REGERAR = "Os códigos antigos podem ter sido invalidados. Gere novos códigos."; // regerar sem resposta: o servidor pode ter comitado
const RESERVA: Record<ModoMfa, string> = { ativar: "Erro ao iniciar setup", desativar: "Erro ao desativar", regerar: "Erro ao gerar códigos" };
const SEIS = /^\d{6}$/;
// Título, subtítulo e botão de cada passo; os rótulos dos campos moram no JSX.
const PASSO = {
  senha: ["Ativar autenticação em 2 etapas", "Confirme sua senha para começar.", "Continuar"],
  qr: ["Escaneie o QR code", "Use Google Authenticator, Authy, 1Password ou similar.", "Ativar MFA"],
  desativar: ["Desativar autenticação em 2 etapas", "Confirme com sua senha e código atual do app (ou um código de backup).", "Desativar"],
  regerar: ["Gerar novos códigos de backup", "Os códigos antigos serão invalidados. Confirme com senha e TOTP.", "Gerar novos"],
} as const;

// Ativar, desativar e regerar os códigos do MFA, em modal. Sem `useMutation`: ele guardaria senha e TOTP nas `variables` do cache.
// Senha e código saem do estado em TODA tentativa; o segredo e o QR vivem só aqui e somem com o modal. Os códigos de backup que
// o servidor devolve vão direto ao Cofre (conta/Cofre.tsx), que só abre depois deste modal fechar.
export function MfaForm({ modo: inicial, fechar, salvou }: { modo: ModoMfa; fechar: () => void; salvou: (aviso: string) => void }) {
  const id = useId();
  const qc = useQueryClient();
  const voando = useRef(false); // síncrona: dois cliques/Enter no mesmo tick não passam por `pendente`; também trava o fechar
  const { dlg, fecharDialog, fundo, props } = useDialogo(fechar, () => voando.current);
  const [modo, setModo] = useState(inicial);
  const [setup, setSetup] = useState<{ segredo: string; qr: string } | null>(null);
  const [senha, setSenha] = useState("");
  const [codigo, setCodigo] = useState("");
  const [falha, setFalha] = useState<Falha | null>(null);
  const [oferta, setOferta] = useState(false); // T2: o MFA ligou e os códigos não chegaram
  const [pendente, setPendente] = useState(false);
  const cfg = modo === "ativar" ? setup : null; // o passo do QR
  const qr = !!cfg;
  const passo = PASSO[modo === "ativar" ? (qr ? "qr" : "senha") : modo === "desativar" ? "desativar" : "regerar"];
  const botaoRegerar = useRef<HTMLButtonElement>(null);
  const campo = (n: string) => dlg.current?.querySelector<HTMLElement>(`[name=${n}]`);

  useEffect(() => { (qr ? campo("codigo") : campo("senha"))?.focus(); }, [qr]); // o useDialogo foca o "Fechar" do cabeçalho
  useEffect(() => { if (oferta) botaoRegerar.current?.focus(); }, [oferta]); // os campos escondidos levaram o foco ao <body>
  useEffect(() => {
    // Os campos desativados em voo perdem o foco: depois de qualquer falha ele volta (à senha, quando é ela o erro).
    if (falha && !pendente) (falha.foco === "senha" ? campo("senha") : campo("codigo") ?? campo("senha"))?.focus();
  }, [falha, pendente]);

  const enviar = async (e: FormEvent) => {
    e.preventDefault();
    if (voando.current) return;
    const cod = codigo.trim();
    if (modo === "ativar" && !qr && !senha) { setFalha({ texto: "Digite sua senha", foco: "senha" }); return; }
    if (qr && !SEIS.test(cod)) { setFalha({ texto: "Digite os 6 dígitos do app" }); return; }
    if (modo !== "ativar" && (!senha || !cod)) { setFalha({ texto: "Preencha senha e código", foco: senha ? undefined : "senha" }); return; }
    voando.current = true;
    setPendente(true);
    setFalha(null);
    let ligando = false;
    try {
      if (modo === "ativar" && !qr) {
        const r = await iniciarMfa(senha);
        // O QR vai num <img src>: só data URI de imagem.
        if (typeof r.secret !== "string" || !r.secret || typeof r.qr_code !== "string" || !r.qr_code.startsWith("data:image/")) throw new ErroApi(200, "setup_invalido");
        setSetup({ segredo: r.secret, qr: r.qr_code });
        return;
      }
      if (modo === "ativar") { ligando = true; await ativarMfa(cod); }
      else if (modo === "desativar") await desativarMfa(senha, cod);
      else await regerarCodigos(senha, cod);
      qc.invalidateQueries({ queryKey: mfaStatusQuery.queryKey });
      voando.current = false; // o fechar está travado em voo
      if (dlg.current?.isConnected) fecharDialog();
      if (modo === "desativar") salvou("MFA desativado");
      else abrirCofre();
    } catch (err) {
      const f = falhaDe(err, RESERVA[modo], DETALHES);
      if (err instanceof ErroApi && err.status === 409) qc.invalidateQueries({ queryKey: ["conta"] }); // "já ativado" ou conta sem senha: reconcilia
      if (ligando && err instanceof ErroApi && err.status === 409) {
        // O único 409 do enable é "MFA já está ativado": uma tentativa anterior comitou e a resposta (e a releitura) se perderam. Mesma saída do T2.
        setOferta(true);
        setFalha({ texto: T2 });
      } else if (f.revalida && ligando) {
        // O enable pode ter comitado antes de a resposta se perder: o status diz qual dos dois casos é este.
        const st = await qc.fetchQuery(mfaStatusQuery).catch(() => null);
        if (st?.enabled === true) setOferta(true);
        setFalha({ texto: st?.enabled === true ? T2 : T5 });
      } else {
        if (f.revalida) qc.invalidateQueries({ queryKey: mfaStatusQuery.queryKey });
        setFalha(f.revalida && modo === "regerar" ? { texto: T_REGERAR } : f);
      }
    } finally {
      setSenha(""); // depois de TODA tentativa
      setCodigo("");
      voando.current = false;
      setPendente(false);
    }
  };
  const irRegerar = () => { setModo("regerar"); setSetup(null); setOferta(false); setFalha(null); };

  return <>
    <dialog {...props} className={`lanc-form${NATIVE ? "" : " lanc-form-fb"}`} aria-labelledby={`${id}-t`}>
      <form onSubmit={enviar} noValidate>
        <header><h2 id={`${id}-t`}>{passo[0]}</h2><button className="btn btn-quiet" type="button" onClick={fecharDialog} disabled={pendente} aria-label="Fechar">Fechar</button></header>
        {!oferta && <p>{passo[1]}</p>}
        {!oferta && <div className="lanc-campos">
          {cfg && <>
            <div className="mfa-qr"><img src={cfg.qr} alt="QR code MFA" width={200} height={200} /></div>
            <p>Ou digite manualmente este segredo</p>
            <code className="mfa-segredo">{cfg.segredo}</code>
          </>}
          {!qr && <label>{modo === "ativar" ? "Senha atual" : "Senha"}
            <input className="field" name="senha" type="password" autoComplete="current-password" value={senha} disabled={pendente} onChange={(e) => setSenha(e.target.value)} />
          </label>}
          {(qr || modo !== "ativar") && <label>{qr ? "Código gerado pelo app (6 dígitos)" : modo === "desativar" ? "Código TOTP ou de backup" : "Código TOTP atual"}
            <input className="field" name="codigo" type="text" autoComplete="one-time-code" value={codigo} disabled={pendente} onChange={(e) => setCodigo(e.target.value)}
              {...(modo === "desativar" ? {} : { inputMode: "numeric" as const, maxLength: 6 })} />
          </label>}
        </div>}
        {falha && <p role="alert" className="warn">{falha.texto}</p>}
        {falha?.acao === "recarregar" && <button type="button" className="btn btn-ghost" onClick={() => location.reload()}>Recarregar</button>}
        {oferta && <button type="button" className="btn btn-primary" ref={botaoRegerar} onClick={irRegerar}>Gerar novos códigos de backup</button>}
        <footer>
          <button className="btn btn-ghost" type="button" onClick={fecharDialog} disabled={pendente}>Cancelar</button>
          {!oferta && <button className="btn btn-primary" type="submit" disabled={pendente}>{pendente ? "Enviando…" : passo[2]}</button>}
        </footer>
      </form>
    </dialog>
    {!NATIVE && <div className="lanc-scrim" aria-hidden="true" onClick={fundo} />}
  </>;
}
