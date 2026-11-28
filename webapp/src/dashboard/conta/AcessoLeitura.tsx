import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { NATIVE, useDialogo } from "../parts/dialog";
import { meQuery } from "../lib/v2";
import { definirLeitura, desativarLeitura, falhaLeitura, leituraQuery, pedirCodigoLeitura, type Falha } from "./api";

type Modo = "criar" | "trocar" | "desativar";
const TITULO: Record<Modo, string> = { criar: "Criar acesso de visualização", trocar: "Trocar a senha de visualização", desativar: "Desativar o acesso de visualização" };
const SEIS = /^\d{6}$/;

// Criar, trocar a senha e desativar, em modal. Sem `useMutation` (a senha ficaria nas `variables` do cache): usuário, senha e código vivem só no estado
// do formulário. O código é a reautenticação por e-mail (o mesmo da exclusão de conta): só sai quando a pessoa pede, e o servidor o consome no PUT/DELETE.
function LeituraForm({ modo, usuarioAtual, fechar, salvou }: { modo: Modo; usuarioAtual: string; fechar: () => void; salvou: (aviso: string) => void }) {
  const id = useId();
  const qc = useQueryClient();
  const voando = useRef(false);
  const { dlg, fecharDialog, fundo, props } = useDialogo(fechar, () => voando.current);
  const [usuario, setUsuario] = useState(usuarioAtual);
  const [senha, setSenha] = useState("");
  const [confirma, setConfirma] = useState("");
  const [codigo, setCodigo] = useState("");
  const [enviado, setEnviado] = useState(false);
  const [falha, setFalha] = useState<Falha | null>(null);
  const [pendente, setPendente] = useState(false);
  const desativar = modo === "desativar";
  const campo = (n: string) => dlg.current?.querySelector<HTMLElement>(`[name=${n}]`);
  useEffect(() => { campo(desativar ? "codigo-pedir" : modo === "criar" ? "usuario" : "senha")?.focus(); }, []); // o useDialogo foca o "Fechar" do cabeçalho
  useEffect(() => { if (enviado) campo("codigo")?.focus(); }, [enviado]);

  const rodar = async (f: () => Promise<void>, reserva: string) => {
    if (voando.current) return;
    voando.current = true;
    setPendente(true);
    setFalha(null);
    try { await f(); } catch (e) { setFalha(falhaLeitura(e, reserva)); setCodigo(""); } finally { voando.current = false; setPendente(false); }
  };
  const pedir = () => rodar(async () => { await pedirCodigoLeitura(); setEnviado(true); }, "Não foi possível enviar o código. Tente de novo.");
  const enviar = (e: FormEvent) => {
    e.preventDefault();
    if (!desativar && (!usuario.trim() || !senha)) return setFalha({ texto: "Preencha usuário e senha." });
    if (!desativar && senha !== confirma) return setFalha({ texto: "As senhas não são iguais." });
    if (!SEIS.test(codigo.trim())) return setFalha({ texto: enviado ? "Digite o código de 6 dígitos." : "Peça o código e digite os 6 dígitos." });
    return rodar(async () => {
      const r = desativar ? await desativarLeitura(codigo.trim()) : await definirLeitura(usuario.trim(), senha, codigo.trim());
      qc.setQueryData(leituraQuery.queryKey, r.ativo ? r : { ativo: false, usuario: null });
      voando.current = false;
      if (dlg.current?.isConnected) fecharDialog();
      salvou(desativar ? "Acesso de visualização desativado." : "Acesso de visualização salvo.");
    }, desativar ? "Não foi possível desativar. Tente de novo." : "Não foi possível salvar. Tente de novo.");
  };

  return <>
    <dialog {...props} className={`lanc-form${NATIVE ? "" : " lanc-form-fb"}`} aria-labelledby={`${id}-t`}>
      <form onSubmit={enviar} noValidate>
        <header><h2 id={`${id}-t`}>{TITULO[modo]}</h2><button className="btn btn-quiet" type="button" onClick={fecharDialog} disabled={pendente} aria-label="Fechar">Fechar</button></header>
        <p>{desativar ? "Quem entra com este usuário perde o acesso na hora." : "Quem entra com este usuário vê a conta inteira e não consegue lançar, editar nem pagar."}</p>
        <div className="lanc-campos">
          {!desativar && <>
            <label>Usuário
              <input className="field" name="usuario" type="text" autoComplete="off" autoCapitalize="none" spellCheck={false} value={usuario} disabled={pendente} onChange={(e) => setUsuario(e.target.value)} />
            </label>
            <label>{modo === "trocar" ? "Nova senha" : "Senha"}
              <input className="field" name="senha" type="password" autoComplete="new-password" value={senha} disabled={pendente} onChange={(e) => setSenha(e.target.value)} />
            </label>
            <label>Confirmar senha
              <input className="field" name="confirma" type="password" autoComplete="new-password" value={confirma} disabled={pendente} onChange={(e) => setConfirma(e.target.value)} />
            </label>
          </>}
          <button type="button" name="codigo-pedir" className="btn btn-ghost" disabled={pendente} onClick={pedir}>{enviado ? "Enviar outro código" : "Enviar código para meu e-mail"}</button>
          {enviado && <p role="status">Código enviado. Confira seu e-mail.</p>}
          <label>Código do e-mail (6 dígitos)
            <input className="field" name="codigo" type="text" inputMode="numeric" maxLength={6} autoComplete="one-time-code" value={codigo} disabled={pendente} onChange={(e) => setCodigo(e.target.value)} />
          </label>
        </div>
        {falha && <p role="alert" className="warn">{falha.texto}</p>}
        {falha?.acao === "recarregar" && <button type="button" className="btn btn-ghost" onClick={() => location.reload()}>Recarregar</button>}
        <footer>
          <button className="btn btn-ghost" type="button" onClick={fecharDialog} disabled={pendente}>Cancelar</button>
          <button className="btn btn-primary" type="submit" disabled={pendente}>{pendente ? "Enviando…" : desativar ? "Desativar" : "Salvar"}</button>
        </footer>
      </form>
    </dialog>
    {!NATIVE && <div className="lanc-scrim" aria-hidden="true" onClick={fundo} />}
  </>;
}

// "Acesso de visualização" (Configurações › Segurança): atrás do freio `acesso_leitura` do /me (ACESSO_LEITURA_USER_IDS). Leitor e quem está fora da lista não montam nada.
export function AcessoLeitura() {
  const liberado = useQuery(meQuery).data?.acesso_leitura === true;
  const q = useQuery({ ...leituraQuery, enabled: leituraQuery.enabled && liberado });
  const [aberto, setAberto] = useState<Modo | null>(null);
  const [aviso, setAviso] = useState("");
  if (!liberado) return null;
  const dados = q.isError ? undefined : q.data; // erro de carga nunca vira "desativado": sem "Criar acesso"
  const abrir = (m: Modo) => { setAviso(""); setAberto(m); };
  const botao = (m: Modo, texto: string, primaria = false) => <button type="button" className={`btn ${primaria ? "btn-primary" : "btn-ghost"}`} onClick={() => abrir(m)}>{texto}</button>;
  return (
    <>
      <section className="panel span-12 conta-perfil conta-seguranca" aria-labelledby="leit-t">
        <h2 id="leit-t">Acesso de visualização</h2>
        <p>Quem entra com este usuário vê a conta inteira e não consegue lançar, editar nem pagar.</p>
        {q.isPending && <p className="faint" role="status">Carregando…</p>}
        {q.isError && <p role="alert" className="warn">Não deu para carregar o acesso de visualização. <button type="button" className="btn btn-ghost retry" onClick={() => q.refetch()}>Tentar de novo</button></p>}
        {dados && (dados.ativo
          ? <><p>Usuário: <strong>{dados.usuario}</strong></p><div className="seg-extra seg-acoes">{botao("trocar", "Trocar senha")}{botao("desativar", "Desativar")}</div></>
          : <div className="seg-extra seg-acoes">{botao("criar", "Criar acesso", true)}</div>)}
        <p role="status" className="sr-only">{aviso}</p>
      </section>
      {aberto && <LeituraForm modo={aberto} usuarioAtual={aberto === "trocar" ? dados?.usuario ?? "" : ""} fechar={() => setAberto(null)} salvou={setAviso} />}
    </>
  );
}
