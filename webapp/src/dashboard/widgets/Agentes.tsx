import { useQuery } from "@tanstack/react-query";
import { useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { AGENTES, acessoDe, agentesQuery, type Acesso } from "../lib/agentes";
import { agenteCena } from "../lib/brand";
import { contaQuery } from "../lib/v2";
import { Seg } from "../parts/Seg";

// Esboço da tela de Agentes (/painel#/agentes): três formatos da mesma lista, para escolher um.
// O estado de cada agente (ativo, energia, plano) vem do mesmo /agents/{uid} da barra do Piggy.
// ponytail: frase/texto copiados de frontend/agents.html e o ritmo de frontend/preview_agentes.html;
// quando um formato vencer, a copy vira uma fonte só (ou vem do servidor) e o teste de catálogo passa a conferir.
const SOBRE: Record<string, { frase: string; texto: string; ritmo: string; foco: string }> = {
  xerife: { frase: "De olho nos excessos.", texto: "Avisa sobre gastos fora do padrão e categorias que ultrapassam os limites que você definiu.", ritmo: "Diário", foco: "55%" },
  reporter: { frase: "Seu mês, em pauta.", texto: "Reúne entradas, saídas, aportes e quanto sobrou em um resumo mensal das suas finanças.", ritmo: "Mensal", foco: "43%" },
  carteiro: { frase: "Vencimentos no radar.", texto: "Traz lembretes de vencimento dos seus boletos no feed para ajudar você a se organizar.", ritmo: "Contínuo", foco: "62%" },
  detetive: { frase: "Os detalhes importam.", texto: "Identifica recorrências, assinaturas e cobranças que podem estar duplicadas.", ritmo: "Semanal", foco: "58%" },
  cofre: { frase: "Cada caixinha conta.", texto: "Acompanha aportes, saídas e o progresso das caixinhas vinculadas ao Open Finance.", ritmo: "Dia do salário", foco: "48%" },
  barao: { frase: "Saldo parado em foco.", texto: "Observa o dinheiro em conta corrente e mostra uma estimativa de rendimento comparada ao CDI.", ritmo: "Diário", foco: "52%" },
  faria_limer: { frase: "Sua carteira à vista.", texto: "Apresenta o valor, o resultado e a concentração da sua carteira de ações e fundos imobiliários.", ritmo: "Mensal", foco: "49%" },
};

type Estado = Acesso | "off";
const SELO: Record<Estado, string> = { ready: "No seu time", activate: "Disponível", no_energy: "Sem energia", upgrade: "Fora do plano", off: "Em breve" };
const ACAO: Record<Estado, string> = { ready: "Conversar", activate: "Ativar", no_energy: "Liberar energia", upgrade: "Ver planos", off: "" };
// ponytail: toda ação leva à /agents, que já ativa e conversa; trocar por ativar()/conversar() quando o formato for escolhido.
const DESTINO = "/agents";

function useElenco() {
  const conta = useQuery(contaQuery);
  const uid = conta.isFetching || conta.isError ? undefined : conta.data?.user_id;
  const d = useQuery(agentesQuery(uid)).data; // sem dados (carregando, erro): a tela mostra o catálogo sem estado
  const lista = AGENTES.map((a) => ({
    ...a, ...SOBRE[a.kind],
    estado: d ? ((acessoDe(d, a.kind) ?? "off") as Estado) : null,
    custo: d?.catalog.find((c) => c.kind === a.kind)?.energy_cost,
  }));
  const energia = d?.energy_enabled ? { usada: Number(d.energy_used || 0), total: Number(d.energy_budget || 0) } : null;
  return { lista, energia };
}
type Agente = ReturnType<typeof useElenco>["lista"][number];

const Selo = ({ a }: { a: Agente }) => a.estado && <span className="ag-selo" data-tom={a.estado}><i aria-hidden="true" />{SELO[a.estado]}</span>;
const Custo = ({ a }: { a: Agente }) => a.custo != null && <span className="ag-custo" title="Energia que ocupa do seu plano"><i className="ph ph-lightning" aria-hidden="true" />{a.custo}</span>;
const Acao = ({ a }: { a: Agente }) => a.estado && a.estado !== "off" && (
  <a className={`btn ${a.estado === "ready" || a.estado === "activate" ? "btn-primary" : "btn-ghost"} ag-acao`} href={DESTINO}>
    {ACAO[a.estado]}<span className="ag-acao-seta" aria-hidden="true"><i className="ph ph-arrow-right" /></span>
  </a>
);

// --- 1. Galeria: faixas lado a lado; a escolhida abre e mostra o agente inteiro. ---
function Galeria({ lista }: { lista: Agente[] }) {
  const [ativo, setAtivo] = useState(0);
  return (
    <ul className="ag-galeria">
      {lista.map((a, i) => {
        const on = i === ativo;
        return (
          <li key={a.kind} className="ag-faixa" data-on={on || undefined} style={{ "--foco": a.foco } as CSSProperties}
            onPointerEnter={(e) => e.pointerType === "mouse" && setAtivo(i)}>
            <img src={agenteCena(a.kind)} alt="" loading="lazy" decoding="async" />
            <button type="button" className="ag-faixa-alvo" aria-expanded={on} aria-controls={`ag-g-${a.kind}`} onClick={() => setAtivo(i)} onFocus={() => setAtivo(i)}>
              <span className="ag-faixa-nome">{a.nome}</span>
              {a.estado === "ready" && <span className="ag-faixa-ponto" aria-label="no seu time" />}
            </button>
            <div className="ag-faixa-info" id={`ag-g-${a.kind}`} inert={!on}>
              <span className="ag-olho">{a.tema}</span>
              <h2 className="ag-nome">{a.nome}</h2>
              <p className="ag-frase">{a.frase}</p>
              <p className="ag-texto">{a.texto}</p>
              <div className="ag-meta"><Selo a={a} /><span className="ag-ritmo"><i className="ph ph-clock" aria-hidden="true" />{a.ritmo}</span><Custo a={a} /></div>
              <Acao a={a} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

// --- 2. Carrossel: capas em perspectiva; a do centro ganha a ficha embaixo. ---
function Carrossel({ lista }: { lista: Agente[] }) {
  const [ativo, setAtivo] = useState(Math.floor(lista.length / 2)); // começa no meio: capas dos dois lados
  const toque = useRef<{ x: number; moveu: boolean } | null>(null);
  const ir = (i: number) => setAtivo(Math.max(0, Math.min(lista.length - 1, i)));
  const onKey = (e: KeyboardEvent) => {
    const passo = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (passo) { e.preventDefault(); ir(ativo + passo); }
  };
  const a = lista[ativo];
  return (
    <section className="ag-cf" aria-roledescription="carrossel" aria-label="Agentes">
      <div className="ag-palco" tabIndex={0} onKeyDown={onKey}
        onPointerDown={(e) => { toque.current = { x: e.clientX, moveu: false }; }}
        onPointerMove={(e) => { if (toque.current && Math.abs(e.clientX - toque.current.x) > 8) toque.current.moveu = true; }}
        onPointerUp={(e) => { const t = toque.current; if (t && Math.abs(e.clientX - t.x) > 40) ir(ativo + (e.clientX < t.x ? 1 : -1)); }}>
        {lista.map((x, i) => {
          const d = i - ativo, ad = Math.abs(d), s = Math.sign(d);
          const tx = d === 0 ? 0 : s * (56 + (ad - 1) * 22);
          return (
            <button key={x.kind} type="button" className="ag-capa" tabIndex={-1} aria-hidden={d !== 0}
              data-on={d === 0 || undefined} data-longe={ad > 2 || undefined}
              style={{ "--tx": `${tx}%`, "--rot": `${-s * 30}deg`, "--esc": 1 - Math.min(ad, 3) * 0.1, "--z": -ad * 120, "--sombra": Math.min(ad * 0.28, 0.8), "--foco": x.foco, zIndex: 10 - ad } as CSSProperties}
              onClick={() => { if (!toque.current?.moveu) setAtivo(i); toque.current = null; }}>
              <img src={agenteCena(x.kind)} alt="" loading="lazy" decoding="async" draggable={false} />
              <span className="ag-capa-nome">{x.nome}</span>
            </button>
          );
        })}
      </div>
      <div className="ag-cf-barra">
        <button type="button" className="icon-btn" aria-label="Agente anterior" disabled={ativo === 0} onClick={() => ir(ativo - 1)}><i className="ph ph-arrow-left" /></button>
        <div className="ag-pontos" role="tablist" aria-label="Escolha um agente">
          {lista.map((x, i) => <button key={x.kind} type="button" role="tab" aria-selected={i === ativo} aria-label={x.nome} onClick={() => setAtivo(i)} />)}
        </div>
        <button type="button" className="icon-btn" aria-label="Próximo agente" disabled={ativo === lista.length - 1} onClick={() => ir(ativo + 1)}><i className="ph ph-arrow-right" /></button>
      </div>
      <article className="ag-ficha" key={a.kind} aria-live="polite">
        <div>
          <span className="ag-olho">{a.tema} · {ativo + 1} de {lista.length}</span>
          <h2 className="ag-nome">{a.nome}</h2>
          <p className="ag-frase">{a.frase}</p>
        </div>
        <div className="ag-ficha-dir">
          <p className="ag-texto">{a.texto}</p>
          <div className="ag-meta"><Selo a={a} /><span className="ag-ritmo"><i className="ph ph-clock" aria-hidden="true" />{a.ritmo}</span><Custo a={a} /></div>
          <Acao a={a} />
        </div>
      </article>
    </section>
  );
}

// --- 3. Elenco: quem já trabalha para você em destaque, o resto da equipe embaixo. ---
function Cartao({ a, grande }: { a: Agente; grande?: boolean }) {
  return (
    <li className="ag-casca" data-grande={grande || undefined} style={{ "--foco": a.foco } as CSSProperties}>
      <article className="ag-nucleo">
        <figure className="ag-cartao-img"><img src={agenteCena(a.kind)} alt="" loading="lazy" decoding="async" /><Selo a={a} /></figure>
        <div className="ag-cartao-corpo">
          <div className="ag-cartao-topo"><h3 className="ag-nome">{a.nome}</h3><Custo a={a} /></div>
          <p className="ag-frase">{a.frase}</p>
          <p className="ag-texto">{a.texto}</p>
          <div className="ag-cartao-pe"><span className="ag-ritmo"><i className="ph ph-clock" aria-hidden="true" />{a.ritmo}</span><Acao a={a} /></div>
        </div>
      </article>
    </li>
  );
}
function Elenco({ lista }: { lista: Agente[] }) {
  const time = lista.filter((a) => a.estado === "ready");
  const resto = lista.filter((a) => a.estado !== "ready");
  return (
    <div className="ag-elenco">
      {time.length > 0 && (
        <section>
          <h2 className="ag-sec">No seu time <span>{time.length}</span></h2>
          <ul className="ag-bento" data-destaque={time.length >= 3 || undefined}>{time.map((a, i) => <Cartao key={a.kind} a={a} grande={i === 0 && time.length >= 3} />)}</ul>
        </section>
      )}
      <section>
        <h2 className="ag-sec">{time.length ? "Para completar o time" : "Todos os agentes"} <span>{resto.length}</span></h2>
        <ul className="ag-bento ag-bento-resto">{resto.map((a) => <Cartao key={a.kind} a={a} />)}</ul>
      </section>
    </div>
  );
}

function Energia({ usada, total }: { usada: number; total: number }) {
  return (
    <div className="ag-energia" role="img" aria-label={`Energia: ${usada} de ${total} em uso`}>
      <span className="ag-energia-rot"><i className="ph ph-lightning" aria-hidden="true" />Energia <b>{usada}</b> de {total}</span>
      <span className="ag-energia-barra" aria-hidden="true">
        {Array.from({ length: Math.min(total, 30) }, (_, i) => <i key={i} data-on={i < usada || undefined} />)}
      </span>
    </div>
  );
}

type Vista = "galeria" | "carrossel" | "elenco";
const VISTAS: { value: Vista; label: string }[] = [{ value: "galeria", label: "Galeria" }, { value: "carrossel", label: "Carrossel" }, { value: "elenco", label: "Elenco" }];

export function AgentesPainel() {
  const { lista, energia } = useElenco();
  const [vista, setVista] = useState<Vista>("galeria");
  return (
    <div className="span-12 ag">
      <div className="ag-topo">
        {energia ? <Energia {...energia} /> : <span />}
        <div className="ag-vista"><span className="ag-esboco">Esboço</span><Seg label="Formato da tela" options={VISTAS} value={vista} onChange={setVista} /></div>
      </div>
      <div className="ag-vista-corpo" key={vista}>
        {vista === "galeria" ? <Galeria lista={lista} /> : vista === "carrossel" ? <Carrossel lista={lista} /> : <Elenco lista={lista} />}
      </div>
    </div>
  );
}
