import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { NATIVE, useDialogo } from "../parts/dialog";
import { SENHA_VAZIA, falhaDaEscrita, salvarContato, segurancaQuery, telefoneBR, temSenhaQuery, type Campo, type CorpoContato, type Falha, type Seguranca } from "./api";

const TITULO: Record<Campo, string> = { nome: "Nome", email: "E-mail", telefone: "Telefone" };
const normaliza = (s: string) => s.trim().toLowerCase();
// Valor que não é texto (o contrato diz string | null, mas um número ou objeto não pode derrubar o painel) vira campo vazio.
const texto = (v: unknown) => typeof v === "string" ? v : "";
const inicialDe = (campo: Campo, s: Seguranca) => campo === "nome" ? texto(s.display_name) : campo === "email" ? texto(s.email) : telefoneBR(texto(s.phone));

// Editar um campo da conta, em modal. Sem `useMutation`: ele guardaria a senha nas `variables` do cache.
// `seguranca` é a leitura VIVA (o SSE e o foco a atualizam por trás); o rascunho nunca é reescrito por ela.
export function ContatoForm({ campo, uid, seguranca, fechar, salvou }: {
  campo: Campo; uid: number; seguranca: Seguranca; fechar: () => void; salvou: (aviso: string) => void;
}) {
  const id = useId();
  const qc = useQueryClient();
  const voando = useRef(false); // síncrona: dois cliques/Enter no mesmo tick não passam por `pendente`; também trava o fechar
  const { dlg, fecharDialog, fundo, props } = useDialogo(fechar, () => voando.current);
  const [inicial] = useState(() => inicialDe(campo, seguranca).trim());
  const [rascunho, setRascunho] = useState(inicial);
  const [senha, setSenha] = useState("");
  const [falha, setFalha] = useState<Falha | null>(null);
  const [pendente, setPendente] = useState(false);
  // Carregando ou falhou = pede a senha (o lado restritivo); só `false` (conta só-Google) a dispensa.
  const temSenha = useQuery(temSenhaQuery(campo === "email")).data?.temSenha;
  // Sem mudança (e-mail: o "igual" que não pede senha) não é escrita: o telefone estrangeiro de 11 dígitos, mostrado em máscara BR, não pode ser regravado.
  // E-mail: só há mudança se o rascunho difere do valor de ABERTURA (campo intocado não regrava o e-mail antigo por cima de uma troca feita em outra aba) e do VIVO.
  const mudou = campo === "email" ? normaliza(rascunho) !== normaliza(inicial) && normaliza(rascunho) !== normaliza(texto(seguranca.email)) : rascunho.trim() !== inicial;
  const pedeSenha = campo === "email" && temSenha !== false && mudou;

  useEffect(() => { dlg.current?.querySelector<HTMLElement>("[name=valor]")?.focus(); }, []); // o useDialogo foca o 1º controle, o "Fechar" do cabeçalho
  useEffect(() => {
    // Os campos desativados em voo perdem o foco: depois de qualquer falha ele volta (à senha, quando é ela o erro).
    if (falha && !pendente) dlg.current?.querySelector<HTMLElement>(falha.foco === "senha" ? "[name=senha]" : "[name=valor]")?.focus();
  }, [falha, pendente]);

  const enviar = async (e: FormEvent) => {
    e.preventDefault();
    if (voando.current) return;
    if (!mudou) { fecharDialog(); return; }
    if (pedeSenha && !senha) { setFalha({ texto: SENHA_VAZIA, foco: "senha" }); return; }
    const valor = rascunho.trim();
    const corpo: CorpoContato = campo === "nome" ? { display_name: valor } : campo === "telefone" ? { phone: valor }
      : { email: valor, ...(pedeSenha ? { current_password: senha } : {}) };
    voando.current = true;
    setPendente(true);
    setFalha(null);
    const chave = segurancaQuery(uid).queryKey;
    try {
      const resposta = await salvarContato(uid, corpo);
      // Um GET lento iniciado antes do PATCH não pode pintar o valor velho por cima da resposta.
      await qc.cancelQueries({ queryKey: chave });
      qc.setQueryData(chave, resposta);
      qc.invalidateQueries({ queryKey: ["conta"] });
      voando.current = false; // o fechar está travado em voo
      if (dlg.current?.isConnected) fecharDialog();
      salvou(campo === "nome" ? (valor ? "Nome atualizado" : "Nome removido") : campo === "email" ? "E-mail atualizado" : "Telefone atualizado");
    } catch (err) {
      const f = falhaDaEscrita(err, campo, temSenha);
      if (f.revalida) qc.invalidateQueries({ queryKey: ["conta"] });
      setFalha(f);
    } finally {
      setSenha(""); // depois de TODA tentativa
      voando.current = false;
      setPendente(false);
    }
  };

  return <>
    <dialog {...props} className={`lanc-form${NATIVE ? "" : " lanc-form-fb"}`} aria-labelledby={`${id}-t`}>
      <form onSubmit={enviar} noValidate>
        <header><h2 id={`${id}-t`}>{TITULO[campo]}</h2><button className="btn btn-quiet" type="button" onClick={fecharDialog} disabled={pendente} aria-label="Fechar">Fechar</button></header>
        <div className="lanc-campos">
          <input className="field" name="valor" aria-label={TITULO[campo]} value={rascunho} disabled={pendente} onChange={(e) => setRascunho(e.target.value)}
            {...(campo === "nome" ? { type: "text", autoComplete: "name", maxLength: 50 } : campo === "email" ? { type: "email", autoComplete: "email" }
              : { type: "tel", autoComplete: "tel", placeholder: rascunho ? undefined : "(11) 99999-9999" })} />
          {pedeSenha && <input className="field" name="senha" type="password" autoComplete="current-password" aria-label="Senha atual" placeholder="Senha atual"
            value={senha} disabled={pendente} onChange={(e) => setSenha(e.target.value)} />}
        </div>
        {falha && <p role="alert" className="warn">{falha.texto}</p>}
        {falha?.acao === "recarregar" && <button type="button" className="btn btn-ghost" onClick={() => location.reload()}>Recarregar</button>}
        {falha?.acao === "definir-senha" && <a className="btn btn-primary" href="/settings">Definir senha</a>}
        <footer>
          <button className="btn btn-ghost" type="button" onClick={fecharDialog} disabled={pendente}>Cancelar</button>
          <button className="btn btn-primary" type="submit" disabled={pendente}>{pendente ? "Salvando…" : "Salvar"}</button>
        </footer>
      </form>
    </dialog>
    {!NATIVE && <div className="lanc-scrim" aria-hidden="true" onClick={fundo} />}
  </>;
}
