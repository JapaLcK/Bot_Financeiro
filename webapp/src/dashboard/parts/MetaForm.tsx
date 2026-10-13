import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useIsMutating, useMutation, useQueryClient } from "@tanstack/react-query";
import type { CustoMensal, EdicaoMeta, Fonte, Meta, MetaId, NovaMeta } from "../lib/api-v2.gen";
import { ErroApi, apiPost } from "../lib/v2";
import { moneyText, normalizaValor, valorInvalido } from "../lib/format.js";
import { CAMPOS_META, ERRO_CAMPO, MESES_PADRAO, falhaDaEscrita, fraseCusto, hojeNoFuso, type CampoMeta, type Falha } from "../lib/metas-textos";
import { CRIAR_SENHA } from "./Entrada";
import { NATIVE, useDialogo } from "./dialog";
import { MetaFonte, type FonteCampos } from "./MetaFonte";

type Acao = { acao: "criar"; corpo: NovaMeta } | { acao: "editar"; corpo: EdicaoMeta } | { acao: "apagar"; corpo: MetaId };
const CHAVE = ["metas", "escrita"];
const NOME_RESERVA = "Reserva de emergência";
type Campos = { nome: string; tipo: "meta" | "reserva"; alvo: string; meses: string; data_alvo: string; fonte: FonteCampos };

const paraFonte = (f: FonteCampos): Fonte => f.tipo === "banco" ? { tipo: "banco", banco: f.banco } : f.tipo === "conta" ? { tipo: "conta", conta: Number(f.conta) } : { tipo: "investido" };

// Criar, editar, converter reserva em meta (`valor`) e apagar. Edição manda só o que mudou
// contra a foto de quando abriu: reenviar o objeto do GET dá 422 (`meses: null` numa meta).
export function MetaForm({ meta, custo, temReserva, comecar, fechar }: {
  meta?: Meta; custo: CustoMensal; temReserva: boolean; comecar?: "reserva" | "valor"; fechar: () => void;
}) {
  const id = useId();
  const qc = useQueryClient();
  const { dlg, fecharDialog, fundo, props } = useDialogo(fechar);
  const novo = !meta;
  const converte = comecar === "valor";
  const inicial = useRef<Campos>({
    nome: meta?.nome ?? (comecar === "reserva" ? NOME_RESERVA : ""), tipo: meta?.tipo ?? (comecar === "reserva" ? "reserva" : "meta"),
    alvo: meta?.alvo ?? "", meses: String(meta?.meses ?? MESES_PADRAO), data_alvo: meta?.data_alvo ?? "",
    fonte: { tipo: meta?.fonte.tipo ?? "investido", banco: meta?.fonte.banco ?? "", conta: meta?.fonte.conta_id ? String(meta.fonte.conta_id) : "" },
  }).current;
  const [c, setC] = useState<Campos>(inicial);
  const [apagar, setApagar] = useState(false);
  // O Tipo fica escolhível desde a abertura (Nova meta sem reserva) ou, depois do 409, para o usuário virar Meta.
  const [escolheTipo, setEscolheTipo] = useState(novo && !comecar && !temReserva);
  const [falha, setFalha] = useState<Falha>({ geral: "", campos: {} });
  const voando = useRef(false); // síncrona: dois cliques/Enter no mesmo tick não passam por `pendente`
  const m = useMutation({
    mutationKey: CHAVE, retry: false, networkMode: "always",
    mutationFn: (a: Acao) => a.acao === "criar" ? apiPost("/metas", a.corpo) : a.acao === "editar" ? apiPost("/metas/editar", a.corpo) : apiPost("/metas/apagar", a.corpo),
    onSettled: () => qc.invalidateQueries({ queryKey: ["metas"] }),
  });
  const pendente = useIsMutating({ mutationKey: CHAVE }) > 0;
  const reserva = c.tipo === "reserva" && !converte;
  useEffect(() => {
    const campo = CAMPOS_META.find((k) => falha.campos[k]);
    if (campo && !pendente) dlg.current?.querySelector<HTMLElement>(`[name="${campo}"]`)?.focus();
  }, [falha, pendente]);
  const naTela = useRef(false); // o botão clicado (Apagar, Voltar) sai do DOM: o foco vai para o controle seguinte
  useEffect(() => {
    if (apagar === naTela.current) return;
    naTela.current = apagar;
    dlg.current?.querySelector<HTMLElement>(apagar ? "button[type=submit]" : "[data-apagar]")?.focus();
  }, [apagar]);
  const mudar = (k: keyof Campos, v: string) => setC((x) => ({ ...x, [k]: v }));

  const enviar = async (e: FormEvent) => {
    e.preventDefault();
    if (voando.current || pendente) return;
    const campos: Falha["campos"] = {};
    const alvo = normalizaValor(c.alvo);
    const nome = c.nome.trim();
    const fonte = paraFonte(c.fonte);
    const fonteMudou = novo || JSON.stringify(fonte) !== JSON.stringify(paraFonte(inicial.fonte));
    const meses = c.meses.trim();
    let acao: Acao;
    if (apagar) acao = { acao: "apagar", corpo: { id: meta!.id } };
    else {
      if ((novo || nome !== inicial.nome) && (!nome || nome.length > 60)) campos.nome = ERRO_CAMPO.nome;
      if (!reserva && (novo || converte || c.alvo !== inicial.alvo) && valorInvalido(alvo)) campos.alvo = ERRO_CAMPO.alvo;
      if (reserva && (novo || meses !== inicial.meses) && !/^([1-9]|1\d|2[0-4])$/.test(meses)) campos.meses = ERRO_CAMPO.meses;
      if (c.data_alvo && c.data_alvo !== inicial.data_alvo && c.data_alvo < hojeNoFuso()) campos.data_alvo = ERRO_CAMPO.data_alvo;
      if (fonteMudou && ((fonte.tipo === "banco" && !fonte.banco) || (fonte.tipo === "conta" && !c.fonte.conta))) campos.fonte = ERRO_CAMPO.fonte;
      if (Object.keys(campos).length) { setFalha({ geral: "Confira os campos indicados.", campos }); return; }
      if (novo) {
        acao = { acao: "criar", corpo: { nome, tipo: c.tipo, fonte, ...(reserva ? { meses: Number(meses) } : { alvo }), ...(c.data_alvo ? { data_alvo: c.data_alvo } : {}) } };
      } else {
        const corpo: EdicaoMeta = { id: meta.id };
        if (converte) { corpo.tipo = "meta"; corpo.alvo = alvo; }
        if (!converte && nome !== inicial.nome) corpo.nome = nome; // convertendo, o campo nome nem aparece
        if (!converte && !reserva && c.alvo !== inicial.alvo) corpo.alvo = alvo;
        if (reserva && meses !== inicial.meses) corpo.meses = Number(meses);
        if (c.data_alvo !== inicial.data_alvo) corpo.data_alvo = c.data_alvo || null;
        if (fonteMudou) corpo.fonte = fonte;
        if (Object.keys(corpo).length === 1) { setFalha({ geral: "Nenhum campo foi alterado.", campos: {} }); return; }
        acao = { acao: "editar", corpo };
      }
    }
    voando.current = true;
    setFalha({ geral: "", campos: {} });
    try { await m.mutateAsync(acao); if (dlg.current?.isConnected) fecharDialog(); }
    catch (err) {
      if (err instanceof ErroApi && err.code === "reserva_ja_existe") setEscolheTipo(true);
      if (apagar && err instanceof ErroApi && err.code === "meta_nao_encontrada") { if (dlg.current?.isConnected) fecharDialog(); } // já foi: apagar de novo dá no mesmo
      else setFalha(falhaDaEscrita(err, apagar));
    }
    finally { voando.current = false; }
  };

  const erro = (k: CampoMeta) => falha.campos[k] && <span id={`${id}-${k}`} className="warn">{falha.campos[k]}</span>;
  const desc = (k: CampoMeta) => ({ "aria-invalid": !!falha.campos[k], "aria-describedby": falha.campos[k] ? `${id}-${k}` : undefined });
  const titulo = novo ? (c.tipo === "reserva" ? "Montar reserva de emergência" : "Nova meta") : apagar ? `Apagar ${meta.nome}?` : converte ? "Definir o valor eu mesmo" : "Editar meta";
  const campoTexto = (k: "nome" | "alvo", label: string) => (
    <label>{label}
      <input className="field" name={k} type="text" inputMode={k === "alvo" ? "decimal" : undefined} maxLength={k === "nome" ? 60 : undefined}
        value={c[k]} disabled={pendente} onChange={(e) => mudar(k, e.target.value)} {...desc(k)} />
      {erro(k)}
    </label>
  );
  return <>
    <dialog {...props} className={`lanc-form meta-form${NATIVE ? "" : " lanc-form-fb"}`} aria-labelledby={`${id}-t`}>
      <form onSubmit={enviar} noValidate>
        <header><h2 id={`${id}-t`}>{titulo}</h2><button className="btn btn-quiet" type="button" onClick={fecharDialog} aria-label="Fechar">Fechar</button></header>
        {apagar ? <p>A meta some da lista. O dinheiro nos seus bancos não muda.</p> : <div className="lanc-campos">
          {converte && <p className="meta-nota">A reserva vira uma meta comum, com o valor que você escolher. Dá para criar outra reserva depois.</p>}
          {!converte && campoTexto("nome", "Nome")}
          {escolheTipo && (
            <label>Tipo<select className="field" name="tipo" disabled={pendente} value={c.tipo} onChange={(e) => setC((x) => ({ ...x, tipo: e.target.value as Campos["tipo"], nome: e.target.value === "reserva" && !x.nome ? NOME_RESERVA : x.nome }))}>
              <option value="meta">Meta</option><option value="reserva" disabled={temReserva}>Reserva de emergência</option>
            </select></label>
          )}
          {reserva ? <>
            <label>Meses das suas contas fixas
              <input className="field" name="meses" type="text" inputMode="numeric" value={c.meses} disabled={pendente} onChange={(e) => mudar("meses", e.target.value)} {...desc("meses")} />
              {erro("meses")}
            </label>
            <p className="meta-nota">{custo.valor ? `Hoje suas contas fixas somam ${moneyText(custo.valor)} por mês. ` : ""}{fraseCusto(custo, true)}</p>
          </> : campoTexto("alvo", "Quanto quer juntar")}
          {!converte && <>
            <label>Data-alvo (opcional)
              <input className="field" name="data_alvo" type="date" min={hojeNoFuso()} value={c.data_alvo} disabled={pendente} onChange={(e) => mudar("data_alvo", e.target.value)} {...desc("data_alvo")} />
              {erro("data_alvo")}
            </label>
            {!novo && c.data_alvo && <button type="button" className="btn btn-quiet" disabled={pendente} onClick={() => { mudar("data_alvo", ""); dlg.current?.querySelector<HTMLElement>('[name="data_alvo"]')?.focus(); }}>Tirar prazo</button>}
            <MetaFonte id={id} valor={c.fonte} onChange={(f) => setC((x) => ({ ...x, fonte: f }))} desabilitado={pendente} erro={falha.campos.fonte} padraoConta={novo} />
          </>}
        </div>}
        {falha.geral && <p role="alert">{falha.geral}</p>}
        {falha.acao === "planos" && <a className="btn btn-ghost" href="/precos">Ver planos</a>}
        {falha.acao === "senha" && <a className="btn btn-primary" href={CRIAR_SENHA.href}>{CRIAR_SENHA.texto}</a>}
        <footer>
          {!novo && !apagar && <button className="btn btn-quiet" type="button" data-apagar disabled={pendente} onClick={() => setApagar(true)}>Apagar</button>}
          {apagar && <button className="btn btn-ghost" type="button" onClick={() => setApagar(false)}>Voltar</button>}
          <button className="btn btn-ghost" type="button" onClick={fecharDialog}>Cancelar</button>
          <button className="btn btn-primary" type="submit" disabled={pendente}>{pendente ? "Salvando…" : apagar ? "Confirmar apagar" : "Salvar"}</button>
        </footer>
      </form>
    </dialog>
    {!NATIVE && <div className="lanc-scrim" aria-hidden="true" onClick={fundo} />}
  </>;
}
