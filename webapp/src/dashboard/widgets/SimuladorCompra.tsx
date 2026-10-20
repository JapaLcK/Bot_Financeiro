// Simulador de compra real (POST /api/v2/simulador): só mostra o que a API devolveu. Dinheiro é o texto
// da API (moneyText); Number() só desenha o SVG. Envio por botão; 403/422/falha nunca viram curva.
import { useRef, useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { CenarioOut, Simulador, SimuladorIn } from "../lib/api-v2.gen";
import { ERRO_VALOR, moneyText, normalizaValor } from "../lib/format.js";
import { hojeNoFuso } from "../lib/metas-textos";
import { ErroApi, apiPost, meQuery, usePlan } from "../lib/v2";
import { CRIAR_SENHA } from "../parts/Entrada";
import { Frame } from "../parts/Frame";
import { MotivosPrevisao } from "../parts/Selos";
import { ConvitePro, ddmm } from "./Compromissos";
import { CurvaComparada } from "./CurvaComparada";
import { hora } from "./Previsao";

type Campo = "nome" | "preco" | "entrada" | "parcelas" | "juros_mensal_pct" | "data_compra" | "custos_unicos" | "despesa_mensal_nova";
type Cen = Record<Campo, string>;
const VAZIO: Cen = { nome: "", preco: "", entrada: "", parcelas: "", juros_mensal_pct: "", data_compra: "", custos_unicos: "", despesa_mensal_nova: "" };
const CAMPOS: { k: Campo; label: string; modo?: "decimal" | "numeric"; dica?: string }[] = [
  { k: "preco", label: "Preço", modo: "decimal" },
  { k: "entrada", label: "Entrada (opcional)", modo: "decimal" },
  { k: "parcelas", label: "Parcelas (vazio = à vista)", modo: "numeric" },
  { k: "juros_mensal_pct", label: "Juros ao mês, % (opcional)", modo: "decimal" },
  { k: "custos_unicos", label: "Custos únicos (opcional)", modo: "decimal" },
  { k: "despesa_mensal_nova", label: "Nova despesa mensal (opcional)", modo: "decimal" },
];
// `ERRO_VALOR` fala em "maior que zero": só o preço. Reserva, entrada, custos e despesa mensal aceitam 0.
const ERRO_ZERO_OK = "Use só números, com até 2 casas (ex.: 150,00).";
const ERRO_CAMPO: Record<Campo | "reserva_minima", string> = {
  reserva_minima: ERRO_ZERO_OK, nome: "Dê um nome de até 60 letras.", preco: ERRO_VALOR, entrada: ERRO_ZERO_OK,
  parcelas: "Número inteiro de 1 a 420.", juros_mensal_pct: "Percentual de 0 a 20, com até 4 casas (ex.: 1,99).", data_compra: "Data de hoje em diante.",
  custos_unicos: ERRO_ZERO_OK, despesa_mensal_nova: ERRO_ZERO_OK,
};
// Código estável do servidor (`type` do detalhe, `CODIGOS_ERRO` em api/v2/simulador.py) -> texto da pessoa. O campo vem no `loc`;
// o `msg` do servidor não é lido (fala com a IA). `dashboard_v2_simulador_compra.test.mjs` cobre todos os códigos.
const TEXTO_CODIGO: Record<string, string> = {
  entrada_maior_que_preco: "A entrada não pode ser maior que o preço.",
  entrada_parcial_sem_parcelas: "Informe em quantas parcelas ou tire a entrada.",
  entrada_igual_ao_preco: "Entrada igual ao preço: não sobra nada para parcelar.",
  juros_sem_parcelas: "Informe em quantas parcelas para usar juros.",
  data_no_passado: "A data da compra não pode ser anterior a hoje.",
  data_fora_do_calendario: "Essa data fica fora do calendário. Escolha outra.",
  financiado_pequeno_demais: "O valor financiado é pequeno demais para esse número de parcelas.",
  nomes_repetidos: "Dois cenários têm o mesmo nome. Dê um nome diferente a cada um.",
  dinheiro_casas: ERRO_ZERO_OK,
};
type Falha = { geral: string; reserva?: string; cenarios: Partial<Record<Campo | "cenario", string>>[]; acao?: "planos" | "senha" };

const decimal = (s: string) => normalizaValor(s) || undefined;
function corpoDe(reserva: string, cens: Cen[]): SimuladorIn {
  return {
    reserva_minima: decimal(reserva),
    cenarios: cens.map((c) => ({
      nome: c.nome.trim(), preco: normalizaValor(c.preco), entrada: decimal(c.entrada), custos_unicos: decimal(c.custos_unicos),
      despesa_mensal_nova: decimal(c.despesa_mensal_nova), juros_mensal_pct: decimal(c.juros_mensal_pct), data_compra: c.data_compra || undefined,
      // texto não numérico segue como veio: o servidor recusa (422) em vez de o cliente adivinhar
      parcelas: c.parcelas.trim() ? (/^\d+$/.test(c.parcelas.trim()) ? Number(c.parcelas) : (c.parcelas as unknown as number)) : undefined,
    })),
  };
}
// Mesma regra das outras telas: 401/402 o portão (`/me`) decide (relido em `enviar`); 403 sem código é o CSRF.
function falhaDe(e: unknown, n: number): Falha {
  const vazio = Array.from({ length: n }, () => ({}));
  const nao = "Não deu para calcular agora. Seu rascunho foi mantido.";
  // 2xx sem JSON (HTML de proxy) também cai aqui: a resposta não serve, o rascunho sim.
  if (!(e instanceof ErroApi) || e.status === 0 || e.status >= 500 || e.status < 400) return { geral: nao, cenarios: vazio };
  if (e.code === "pro_required") return { geral: "O simulador de compra é do Pro.", cenarios: vazio, acao: "planos" };
  if (e.code === "password_required") return { geral: "Crie sua senha para usar o simulador. Depois de criar, volte para o painel novo.", cenarios: vazio, acao: "senha" };
  if (e.status === 401) return { geral: "Sua sessão terminou. Recarregue a página.", cenarios: vazio };
  if (e.status === 402) return { geral: "Sua assinatura não está ativa. Recarregue a página para ver o que fazer.", cenarios: vazio };
  if (e.code === "open_finance_onboarding_required") return { geral: "Termine a conexão com seu banco para usar o simulador.", cenarios: vazio };
  if (e.status === 422) {
    const cenarios: Falha["cenarios"] = vazio.map(() => ({}));
    let geral = "Confira os campos indicados. Seu rascunho foi mantido.", reserva: string | undefined;
    const generico = "Confira os dados deste cenário.";
    for (const d of e.details ?? []) {
      const texto = TEXTO_CODIGO[d.type];
      if (d.loc[1] === "reserva_minima") { reserva = ERRO_CAMPO.reserva_minima; continue; }
      const i = d.loc[1] === "cenarios" && typeof d.loc[2] === "number" ? d.loc[2] : -1;
      const k = d.loc[3];
      if (i < 0 || !cenarios[i]) { if (texto) geral = texto; continue; }
      if (typeof k === "string" && k in ERRO_CAMPO) cenarios[i][k as Campo] = texto ?? ERRO_CAMPO[k as Campo];
      else cenarios[i].cenario = texto ?? generico;
    }
    return { geral, reserva, cenarios };
  }
  if (e.status === 429) return { geral: "Muitas simulações seguidas. Espere um pouco e tente de novo.", cenarios: vazio };
  if (e.status === 403) return { geral: "Recarregue a página antes de calcular de novo.", cenarios: vazio };
  return { geral: nao, cenarios: vazio };
}

function Grafico({ d, c }: { d: Simulador; c: CenarioOut }) {
  return <CurvaComparada antes={d.atual.trajetoria} depois={c.resumo.trajetoria} rotulos={["Sem a compra", "Com a compra"]} drawKey={`compra-${c.nome}`} />;
}

const reais = (s: string | null) => (s === null ? "indisponível" : moneyText(s) ?? "indisponível");

function Resultado({ d, c }: { d: Simulador; c: CenarioOut }) {
  const r = c.resumo, k = c.contrato, fora = k.parcelas_fora_do_horizonte;
  const pior = r.pior_dia;
  const queda = !!r.delta_vs_atual && r.delta_vs_atual.startsWith("-");
  return (
    <section className="comp-res" aria-label={`Resultado: ${c.nome}`}>
      <h3 className="prev-h">{c.nome}</h3>
      <dl className="hero-facts prev-facts">
        <div><dt>Saldo no dia {d.dias}</dt><dd>{reais(r.saldo_final)}</dd></div>
        <div><dt>Diferença sem a compra</dt><dd className={queda ? "warn" : ""}>{reais(r.delta_vs_atual)}</dd></div>
        <div><dt>Menor saldo</dt><dd className={pior?.saldo.startsWith("-") ? "warn" : ""}>{pior ? `${reais(pior.saldo)} em ${ddmm(pior.data)}` : "indisponível"}</dd></div>
        <div><dt>Dias abaixo da reserva</dt><dd>{r.dias_abaixo_da_reserva}{r.primeiro_dia_abaixo && ` (desde ${ddmm(r.primeiro_dia_abaixo)})`}</dd></div>
        <div><dt>Total pago no contrato</dt><dd>{reais(k.total_pago)}</dd></div>
        <div><dt>Juros no contrato</dt><dd>{reais(k.juros_totais)}</dd></div>
        {!k.a_vista && <div><dt>Parcela</dt><dd>{k.parcelas}× {reais(k.parcela)}</dd></div>}
        {fora.quantidade > 0 && <div><dt>Parcelas depois do dia {d.dias}</dt><dd>{fora.quantidade} ({reais(fora.valor)})</dd></div>}
      </dl>
      {r.compra_fora_da_janela && <p className="w-lede">A compra cai depois do dia {d.dias}: o saldo dentro da janela não muda.</p>}
      <Grafico d={d} c={c} />
    </section>
  );
}

function Formulario({ reserva, setReserva, cens, setCens, falha, pendente, enviar }: {
  reserva: string; setReserva: (v: string) => void; cens: Cen[]; setCens: (f: (x: Cen[]) => Cen[]) => void;
  falha: Falha; pendente: boolean; enviar: (e: FormEvent) => void;
}) {
  const muda = (i: number, k: Campo, v: string) => setCens((x) => x.map((c, j) => (j === i ? { ...c, [k]: v } : c)));
  return (
    <form className="comp-form" onSubmit={enviar} noValidate>
      <label className="comp-campo">Reserva mínima (opcional)
        <input className="field" name="reserva_minima" type="text" inputMode="decimal" value={reserva} disabled={pendente} aria-invalid={!!falha.reserva}
          aria-describedby={falha.reserva ? "comp-reserva" : undefined} onChange={(e) => setReserva(e.target.value)} />
        {falha.reserva && <span id="comp-reserva" className="warn">{falha.reserva}</span>}
      </label>
      {cens.map((c, i) => {
        const f = falha.cenarios[i] ?? {};
        const campo = (k: Campo, label: string, modo?: "decimal" | "numeric") => (
          <label className="comp-campo" key={k}>{label}
            <input className="field" name={`${k}-${i}`} type="text" inputMode={modo} maxLength={k === "nome" ? 60 : undefined} value={c[k]} disabled={pendente}
              aria-invalid={!!f[k]} aria-describedby={f[k] ? `comp-${i}-${k}` : undefined} onChange={(e) => muda(i, k, e.target.value)} />
            {f[k] && <span id={`comp-${i}-${k}`} className="warn">{f[k]}</span>}
          </label>
        );
        return (
          <fieldset className="comp-cen" key={i}>
            <legend>Cenário {i + 1}</legend>
            {campo("nome", "Nome")}
            {CAMPOS.map((x) => campo(x.k, x.label, x.modo))}
            <label className="comp-campo">Data da compra (opcional)
              <input className="field" name={`data_compra-${i}`} type="date" min={hojeNoFuso()} value={c.data_compra} disabled={pendente} aria-invalid={!!f.data_compra}
                aria-describedby={f.data_compra ? `comp-${i}-data_compra` : undefined} onChange={(e) => muda(i, "data_compra", e.target.value)} />
              {f.data_compra && <span id={`comp-${i}-data_compra`} className="warn">{f.data_compra}</span>}
            </label>
            {f.cenario && <p role="alert" className="warn">{f.cenario}</p>}
            {cens.length > 1 && <button type="button" className="btn btn-quiet" disabled={pendente} onClick={() => setCens((x) => x.filter((_, j) => j !== i))}>Tirar este cenário</button>}
          </fieldset>
        );
      })}
      {falha.geral && <p role="alert" className="warn">{falha.geral}</p>}
      {falha.acao === "planos" && <a className="btn btn-ghost" href="/precos">Ver planos</a>}
      {falha.acao === "senha" && <a className="btn btn-primary" href={CRIAR_SENHA.href}>{CRIAR_SENHA.texto}</a>}
      <div className="comp-acoes">
        {cens.length < 3 && <button type="button" className="btn btn-ghost" disabled={pendente} onClick={() => setCens((x) => [...x, VAZIO])}>Comparar com outro cenário</button>}
        <button type="submit" className="btn btn-primary" disabled={pendente}>{pendente ? "Calculando…" : "Calcular"}</button>
      </div>
    </form>
  );
}

export function SimuladorCompra() {
  const plano = usePlan();
  const qc = useQueryClient();
  const [reserva, setReserva] = useState("");
  const [cens, setCens] = useState<Cen[]>([VAZIO]);
  const [falha, setFalha] = useState<Falha>({ geral: "", cenarios: [] });
  const m = useMutation({ mutationKey: ["simulador"], retry: false, networkMode: "always", mutationFn: (b: SimuladorIn) => apiPost("/simulador", b) });
  const voando = useRef(false);
  // Resposta velha nunca fica ao lado de um erro novo: a mutação zera `data` ao recomeçar.
  const enviar = async (e: FormEvent) => {
    e.preventDefault();
    if (voando.current) return;
    voando.current = true;
    setFalha({ geral: "", cenarios: [] });
    try { await m.mutateAsync(corpoDe(reserva, cens)); }
    catch (err) {
      setFalha(falhaDe(err, cens.length));
      if (err instanceof ErroApi && (err.status === 401 || err.status === 402)) void qc.refetchQueries({ queryKey: meQuery.queryKey }); // o portão decide (lib/eventos.ts)
    }
    finally { voando.current = false; }
  };
  const d = m.data;
  if (plano !== "pro") return <div className="panel span-12"><Frame id="compra-form" title="Simular uma compra" real><ConvitePro texto="O simulador de compra é do Pro." /></Frame></div>;
  return (
    <>
      <div className="panel span-5">
        <Frame id="compra-form" title="Simular uma compra" real>
          <Formulario reserva={reserva} setReserva={setReserva} cens={cens} setCens={setCens} falha={falha} pendente={m.isPending} enviar={enviar} />
        </Frame>
      </div>
      <div className="panel span-7">
        <Frame id="compra-resultado" title="O que muda no seu saldo" real>
          {d ? (
            <div className="comp-corpo" aria-busy={m.isPending}>
              <p className="w-lede">Projeção condicional de {d.dias} dias, sem apontar vencedor. Calculada às {hora(d)}.</p>
              <MotivosPrevisao motivos={d.motivos} />
              {d.cenarios.map((c) => <Resultado key={c.nome} d={d} c={c} />)}
              <h3 className="prev-h">Premissas</h3>
              <ul className="premissas">{d.premissas.map((t) => <li key={t}>{t}</li>)}</ul>
            </div>
          ) : <p className="w-lede">{m.isPending ? "Calculando…" : "Preencha ao menos nome e preço e toque em Calcular."}</p>}
        </Frame>
      </div>
    </>
  );
}
