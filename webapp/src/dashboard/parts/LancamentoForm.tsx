import { useEffect, useRef, useState, type FormEvent } from "react";
import type { Categoria, Conta, Edicao, Lancamento, NovoLancamento } from "../lib/api-v2.gen";
import { ErroApi } from "../lib/v2";
import { moneyIn } from "../lib/format.js";
import { LancamentoContexto } from "./LancamentoLinha";
import { NATIVE, hide, isOpen, show, untrap } from "./dialog";
import { type Escrita, type useLancamentoMutation } from "./useLancamentoMutation";

type Campos = "descricao" | "valor" | "data" | "categoria";
const ERROS = { descricao: "Informe uma descrição com até 200 caracteres.", valor: "Use um valor maior que zero, até 9 dígitos e 2 casas decimais, sem separador de milhares.", data: "Confira a data e a janela do seu plano.", categoria: "Escolha uma categoria disponível." };
const FOCUS = 'input:not(:disabled), select:not(:disabled), button:not(:disabled), [tabindex="-1"]';

export function LancamentoForm({ item, indisponivel, categorias, contas, escrita, fechar, conferir, rascunho, mes, historico }: {
  item?: Lancamento; indisponivel: boolean; categorias: Categoria[]; contas: Conta[];
  escrita: ReturnType<typeof useLancamentoMutation>; fechar: () => void; conferir: () => Promise<void>;
  rascunho?: NovoLancamento; mes: string; historico: boolean;
}) {
  const dlg = useRef<HTMLDialogElement>(null);
  const [campos, setCampos] = useState({ descricao: item?.descricao ?? rascunho?.descricao ?? "", valor: item?.valor ?? rascunho?.valor ?? "", data: item?.data ?? rascunho?.data ?? "", categoria: item?.categoria ?? rascunho?.categoria ?? "" });
  const inicial = useRef({ ...campos }).current;
  const [tipo, setTipo] = useState<"entrada" | "saida">(rascunho?.tipo ?? "saida");
  const [apagar, setApagar] = useState(false);
  const [erro, setErro] = useState("");
  const [errors, setErrors] = useState<Partial<Record<Campos, string>>>({});
  const [limite, setLimite] = useState(false);
  const novo = !item;
  const pode = (campo: Campos | "apagar") => !indisponivel && (novo || item.pode.includes(campo));
  const travado = escrita.pendente || escrita.bloqueado || indisponivel;
  useEffect(() => {
    const d = dlg.current;
    show(d, FOCUS);
    d?.querySelector<HTMLElement>(FOCUS)?.focus();
    const esc = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape" && !NATIVE && isOpen(d)) { e.preventDefault(); hide(d); fechar(); }
    };
    window.addEventListener("keydown", esc);
    return () => { window.removeEventListener("keydown", esc); if (isOpen(d)) hide(d); else untrap(); };
  }, []);
  useEffect(() => {
    const campo = Object.keys(errors)[0];
    if (campo) dlg.current?.querySelector<HTMLElement>(`[name="${campo}"]`)?.focus();
  }, [errors]);
  const fecharDialog = () => { hide(dlg.current); fechar(); };
  const mudar = (k: Campos, v: string) => setCampos((c) => ({ ...c, [k]: v }));
  const enviar = async (e: FormEvent) => {
    e.preventDefault();
    if (travado) return;
    setErro(""); setLimite(false);
    const falhas: Partial<Record<Campos, string>> = {};
    const valor = campos.valor.trim().replace(",", ".");
    if (!apagar) {
      if (pode("valor") && (novo || campos.valor !== inicial.valor) && (!/^\d{1,9}(\.\d{1,2})?$/.test(valor) || !/[1-9]/.test(valor))) falhas.valor = ERROS.valor;
      if (pode("descricao") && (novo || campos.descricao !== inicial.descricao) && (!campos.descricao.trim() || campos.descricao.length > 200)) falhas.descricao = ERROS.descricao;
      if (pode("data") && !novo && campos.data !== inicial.data && !campos.data) falhas.data = ERROS.data;
    }
    setErrors(falhas);
    if (Object.keys(falhas).length) return;
    let acao: Escrita;
    if (apagar) { if (!pode("apagar")) return; acao = { acao: "apagar", corpo: { id: item!.id } }; }
    else if (novo) acao = { acao: "criar", corpo: { tipo, valor, descricao: campos.descricao, categoria: campos.categoria || null, ...(campos.data ? { data: campos.data } : {}) } };
    else {
      const corpo: Edicao = { id: item.id };
      for (const k of ["descricao", "valor", "data", "categoria"] as const) {
        if (pode(k) && campos[k] && campos[k] !== inicial[k]) corpo[k] = k === "valor" ? valor : campos[k];
      }
      if (Object.keys(corpo).length === 1) { setErro("Nenhum campo foi alterado."); return; }
      acao = { acao: "editar", corpo };
    }
    try { if (await escrita.salvar(acao, item, mes, historico) && dlg.current?.isConnected) fecharDialog(); }
    catch (err) {
      if (!(err instanceof ErroApi) || err.status === 0 || err.status >= 500 || (err.status >= 200 && err.status < 300)) setErro("A gravação pode ter sido concluída. Atualize e confira a lista antes de repetir.");
      else if (err.status === 422) {
        const camposErro: Partial<Record<Campos, string>> = {};
        for (const d of err.details ?? []) { const k = d.loc[d.loc.length - 1] as Campos; if (k in ERROS) camposErro[k] = ERROS[k]; }
        setErrors(camposErro); setErro("Confira os campos indicados. Seu rascunho foi mantido.");
      } else if (err.status === 409) setErro("A permissão para alterar este lançamento mudou. Confira os campos disponíveis.");
      else if (err.status === 404) setErro("Este lançamento não está mais disponível.");
      else if (err.status === 403 && err.code === "plan_limit") { setErro("Você atingiu o limite de lançamentos do seu plano."); setLimite(true); }
      else if (err.status === 403) setErro("Recarregue a página antes de salvar novamente: a autorização de segurança expirou.");
      else setErro("Não foi possível salvar. Seu rascunho foi mantido.");
    }
  };
  const campo = (k: Campos, label: string, type = "text") => <label>{label}
    <input className="field" name={k} type={type} inputMode={k === "valor" ? "decimal" : undefined} maxLength={k === "descricao" ? 200 : undefined} value={campos[k]} disabled={!pode(k) || escrita.pendente} onChange={(e) => mudar(k, e.target.value)} aria-invalid={!!errors[k]} aria-describedby={errors[k] ? `erro-${k}` : undefined} />
    {errors[k] && <span id={`erro-${k}`} className="warn">{errors[k]}</span>}
  </label>;
  return <>
    <dialog ref={dlg} className={`lanc-form${NATIVE ? "" : " lanc-form-fb"}`} aria-labelledby="lanc-form-title" role={NATIVE ? undefined : "dialog"} aria-modal={NATIVE ? undefined : true} onKeyDown={(e) => {
        if (!NATIVE || e.key !== "Tab") return;
        const all = [...e.currentTarget.querySelectorAll<HTMLElement>(FOCUS)];
        const at = all.indexOf(document.activeElement as HTMLElement);
        if ((e.shiftKey && at === 0) || (!e.shiftKey && at === all.length - 1)) { e.preventDefault(); all[e.shiftKey ? all.length - 1 : 0]?.focus(); }
      }} onCancel={(e) => { e.preventDefault(); fecharDialog(); }} onClick={(e) => { if (e.target === dlg.current) fecharDialog(); }}>
      <form onSubmit={enviar} noValidate>
        <header><h2 id="lanc-form-title">{novo ? "Lançar na Carteira" : apagar ? "Apagar lançamento?" : "Detalhes do lançamento"}</h2><button className="btn btn-quiet" type="button" onClick={fecharDialog} aria-label="Fechar detalhes">Fechar</button></header>
        {novo ? <p className="w-lede">Só dinheiro em espécie, na Carteira Piggy.</p> : <LancamentoContexto item={item} contas={contas} />}
        {apagar ? <>
          <p>{item!.descricao ?? "Sem descrição"} · {moneyIn(Number(item!.valor), item!.moeda)}</p>
          {item!.fundido && <p role="alert">A transação do banco continua na sua lista; apagar este lançamento desfaz a junção.</p>}
        </> : <div className="lanc-campos">
          {novo ? <label>Tipo<select className="field" disabled={escrita.pendente} value={tipo} onChange={(e) => setTipo(e.target.value as typeof tipo)}><option value="saida">Saída</option><option value="entrada">Entrada</option></select></label> : <p>{item.interno ? "Movimento interno" : item.tipo === "entrada" ? "Entrada" : "Saída"} · {item.moeda}</p>}
          {campo("descricao", "Descrição")}{campo("valor", "Valor")}{campo("data", novo ? "Data (vazia usa hoje)" : "Data", "date")}
          <label>Categoria<select className="field" name="categoria" disabled={!pode("categoria") || escrita.pendente || !categorias.length} value={campos.categoria} onChange={(e) => mudar("categoria", e.target.value)} aria-invalid={!!errors.categoria} aria-describedby={errors.categoria ? "erro-categoria" : undefined}>
            <option value="" disabled={!novo}>{novo ? "Automática" : "Sem categoria"}</option>
            {campos.categoria && !categorias.some((c) => c.chave === campos.categoria) && <option value={campos.categoria}>{campos.categoria}</option>}
            {categorias.map((c) => <option key={c.chave} value={c.chave}>{c.nome}</option>)}
          </select>{errors.categoria && <span id="erro-categoria" className="warn">{errors.categoria}</span>}</label>
        </div>}
        {(erro || indisponivel) && <p role="alert">{indisponivel ? "Este lançamento não está mais disponível." : erro}</p>}
        {limite && <a className="btn btn-ghost" href="/precos">Ver planos</a>}
        {escrita.bloqueado && <div className="lanc-conferir"><p>A resposta se perdeu. Atualize a lista e confira os itens antes de salvar novamente.</p><button type="button" className="btn btn-ghost" disabled={escrita.pendente} onClick={conferir}>Atualizar para conferir</button></div>}
        <footer>
          {!novo && pode("apagar") && !apagar && <button className="btn btn-quiet" type="button" disabled={travado} onClick={() => setApagar(true)}>Apagar</button>}
          {apagar && <button className="btn btn-ghost" type="button" onClick={() => setApagar(false)}>Voltar aos detalhes</button>}
          <button className="btn btn-ghost" type="button" onClick={fecharDialog}>{novo ? "Cancelar" : "Fechar"}</button>
          {(novo || item.pode.some((p) => p !== "apagar") || apagar) && <button className="btn btn-primary" type="submit" disabled={travado}>{escrita.pendente ? "Salvando…" : apagar ? "Confirmar apagar" : "Salvar"}</button>}
        </footer>
      </form>
    </dialog>
    {!NATIVE && <div className="lanc-scrim" aria-hidden="true" onClick={fecharDialog} />}
  </>;
}
