import { useId, type ReactNode } from "react";
import type { CustoMensal, Meta } from "../lib/api-v2.gen";
import { moneyText } from "../lib/format.js";
import { ehNegativo, ehZero, fraseChegada, fraseCusto, fraseRitmo, mesAno, type Frase } from "../lib/metas-textos";
import { Selos } from "./Selos";

// Motivos que explicam o alvo (não o saldo da fonte): não pedem "a conferir" no guardado.
const DO_ALVO = ["custo_desconhecido", "plano_sem_custo_mensal"];
export { DO_ALVO }; // a pílula do chat (MetaPilula.tsx) usa a mesma lista

// A frase de um motivo com o botão que resolve (planos ou conectar banco).
export function FraseComAcao({ f }: { f: Frase }) {
  return (
    <>
      <p className="meta-nota">{f.texto}</p>
      {f.acao === "planos" && <a className="btn btn-ghost" href="/precos">Ver planos</a>}
      {f.acao === "banco" && <a className="btn btn-ghost" href="/settings?view=open-finance">Conectar banco</a>}
    </>
  );
}

function Ritmo({ m, mostra }: { m: Meta; mostra: boolean }) {
  const id = useId();
  const r = m.ritmo;
  if (!mostra) return <p className="num">—</p>;
  if (r.valor === null) return <FraseComAcao f={fraseRitmo(r)} />;
  const texto = moneyText(r.valor)!;
  const neg = ehNegativo(r.valor);
  const proprios = r.motivos.filter((c) => !(m.motivos as readonly string[]).includes(c));
  return (
    <>
      <p className={`meta-numero num ${neg ? "warn" : ehZero(r.valor) ? "" : "gain"}`}>{neg || ehZero(r.valor) ? texto : `+${texto}`}/mês</p>
      <p className="faint meta-nota">inclui rendimento e variação</p>
      {r.parcial && (
        <p className="selos">
          <span className="selo" aria-describedby={id}>{r.dias_de_historico === null ? "medição parcial" : `medido em ${r.dias_de_historico} ${r.dias_de_historico === 1 ? "dia" : "dias"}`}</span>
          <span id={id} className="sr-only">menos de 3 meses: ainda pode mudar bastante</span>
        </p>
      )}
      <Selos motivos={proprios} />
    </>
  );
}

function Precisa({ m, editar }: { m: Meta; editar: () => void }) {
  if (m.prazo_passou && m.data_alvo) {
    return <><p>O prazo era {mesAno(m.data_alvo)}</p><button type="button" className="btn btn-ghost" onClick={editar}>Mudar prazo</button></>;
  }
  if (!m.data_alvo) return <p className="meta-nota">Sem prazo. Escolha uma data e eu digo quanto guardar por mês.</p>;
  const por = m.por_mes && moneyText(m.por_mes);
  return <p className="meta-numero num">{por ? `${por}/mês até ${mesAno(m.data_alvo)}` : `Prazo: ${mesAno(m.data_alvo)}`}</p>;
}

export function MetaCartao({ m, custo, editar }: { m: Meta; custo: CustoMensal; editar: (definirValor?: boolean) => void }) {
  const id = useId();
  const reserva = m.tipo === "reserva";
  const atual = m.atual === null ? null : moneyText(m.atual);
  const alvo = m.alvo === null ? null : moneyText(m.alvo);
  const batida = m.falta !== null && ehZero(m.falta);
  const conferir = m.atual !== null && ehZero(m.atual) && m.motivos.some((c) => !DO_ALVO.includes(c));
  const chegada = !batida && atual !== null ? fraseChegada(m) : null;
  const custoTxt = reserva && custo.valor ? moneyText(custo.valor) : null;
  const avisoCusto = custoTxt && m.alvo ? fraseCusto(custo) : null; // com valor, o motivo vira "o valor pode estar velho"
  const sub = reserva ? `Reserva de emergência · ${m.meses} ${m.meses === 1 ? "mês" : "meses"} das suas contas fixas${custoTxt && m.alvo ? ` (${m.meses} × ${custoTxt})` : ""}` : m.fonte.rotulo;
  const valores: ReactNode = atual === null ? <span className="num">—</span>
    : <><span className="num">{atual}</span>{alvo ? <> de <span className="num">{alvo}</span></> : " guardado"}</>;
  return (
    <article className="meta" aria-labelledby={`${id}-h`}>
      <header className="meta-topo">
        <h3 id={`${id}-h`}>{m.nome}</h3>
        <button type="button" className="btn btn-ghost" aria-label={`Editar ${m.nome}`} onClick={() => editar()}><i className="ph ph-pencil-simple" aria-hidden="true" />Editar</button>
      </header>
      {sub && <p className="faint meta-nota">{sub}{reserva && m.fonte.rotulo ? ` · ${m.fonte.rotulo}` : ""}</p>}
      {avisoCusto && <p className="meta-nota">{avisoCusto}</p>}
      <p className="meta-valores">{valores}{conferir && <> <span className="selo">a conferir</span></>}</p>
      {atual === null && <FraseComAcao f={fraseRitmo(m.ritmo)} />}
      {m.percentual !== null && (
        <div className={`meter${batida ? " ok" : ""}`} role="progressbar" aria-label={`Progresso de ${m.nome}`} aria-valuemin={0} aria-valuemax={100}
          aria-valuenow={m.percentual} aria-valuetext={`${m.percentual}%: ${atual} de ${alvo}`}>
          <span style={{ transform: `scaleX(${m.percentual / 100})` }} />
        </div>
      )}
      {batida ? <p className="gain meta-batida"><i className="ph ph-check-circle" aria-hidden="true" />Meta batida</p>
        : m.falta !== null && <p className="meta-falta">Falta <span className="num">{moneyText(m.falta)}</span></p>}
      {reserva && m.alvo === null && (
        <>
          <p className="meta-nota">{fraseCusto(custo) ?? "Não consegui calcular suas contas fixas."}</p>
          <button type="button" className="btn btn-ghost" onClick={() => editar(true)}>Definir o valor eu mesmo</button>
        </>
      )}
      {!batida && (
        <div className="meta-ritmos">
          <section aria-label="O que precisa"><h4>Precisa</h4><Precisa m={m} editar={() => editar()} /></section>
          <section aria-label="Seu ritmo"><h4>Seu ritmo</h4><Ritmo m={m} mostra={atual !== null} /></section>
        </div>
      )}
      {chegada && <p className="meta-chegada">{chegada}</p>}
      <Selos motivos={m.motivos} />
    </article>
  );
}
