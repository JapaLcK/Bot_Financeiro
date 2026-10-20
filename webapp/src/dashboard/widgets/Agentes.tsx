import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type CSSProperties } from "react";
import { uidValido } from "../conta/api";
import { AGENTES, acessoDe, agentesQuery, energiaDe, type Acesso } from "../lib/agentes";
import { agenteCena } from "../lib/brand";
import { ask, perguntarAoAgente } from "../lib/conversation";
import { DEMO, contaQuery, lerContaFresca } from "../lib/v2";
import { go } from "../router";

// A tela de Agentes (/painel#/agentes): uma galeria de faixas, a escolhida abre e mostra o agente inteiro.
// O estado de cada agente (ativo, energia, plano) vem do mesmo /agents/{uid} da barra do Piggy.
// ponytail: frase/texto copiados de frontend/agents.html e o ritmo de frontend/preview_agentes.html;
// a copy vira uma fonte só (ou vem do servidor) quando a /agents antiga sair, e o teste de catálogo passa a conferir.
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
// A pergunta de abertura de cada agente: "Conversar" e "Ativar" a mandam ao chat do painel (#/piggy), que já trata
// cada estado ("Ativar e responder" para quem não está ativo). Sem energia ou fora do plano, o botão vai a /precos,
// o mesmo destino do "Ver planos" do chat (parts/PiggyChat.tsx).
const PERGUNTA: Record<string, string> = {
  xerife: "Algum gasto fugiu do meu padrão?",
  reporter: "Como foi o meu mês?",
  carteiro: "Quais contas vencem nos próximos dias?",
  detetive: "Tem assinatura ou cobrança duplicada?",
  cofre: "Como estão as minhas caixinhas?",
  barao: "Tenho dinheiro parado que poderia render mais?",
  faria_limer: "Como está a minha carteira?",
};

/** Abre a conversa com o agente. Como o envio da barra do Piggy (parts/AskBar.tsx): relê a conta e o catálogo antes, porque a
 * sessão pode ter mudado noutra aba; sem conta conferida ou sem catálogo, a pergunta vai ao Piggy. */
function useAbrirConversa() {
  const qc = useQueryClient();
  const indo = useRef(false);
  return async (a: Agente) => {
    if (indo.current) return;
    indo.current = true;
    const pergunta = PERGUNTA[a.kind], original = `@${a.nome} ${pergunta}`;
    try {
      const u = DEMO ? 0 : (await lerContaFresca(qc)).user_id;
      const d = DEMO || uidValido(u) ? await qc.fetchQuery(agentesQuery(u)) : undefined;
      const acesso = d && acessoDe(d, a.kind);
      if (!acesso) ask({ text: original });
      else if (!perguntarAoAgente({ kind: a.kind, original, texto: pergunta, uid: u ?? 0, acesso, energia: energiaDe(d, a.kind) })) go("/piggy"); // já há pergunta em voo a ele: mostra a conversa
    } catch {
      ask({ text: original });
    } finally {
      indo.current = false;
    }
  };
}

function useElenco() {
  const conta = useQuery(contaQuery);
  const uid = conta.isFetching || conta.isError ? undefined : conta.data?.user_id;
  const q = useQuery(agentesQuery(uid));
  const d = q.data; // sem dados (carregando, erro): a tela mostra o catálogo sem estado nem botão
  const lista = AGENTES.map((a) => ({
    ...a, ...SOBRE[a.kind],
    estado: d ? ((acessoDe(d, a.kind) ?? "off") as Estado) : null,
    custo: d?.catalog.find((c) => c.kind === a.kind)?.energy_cost,
  }));
  // Plano sem energia (orçamento 0): sem barra; o botão de cada agente já diz "Ver planos".
  const energia = d?.energy_enabled && Number(d.energy_budget) > 0 ? { usada: Number(d.energy_used || 0), total: Number(d.energy_budget) } : null;
  return { lista, energia, erro: conta.isError || q.isError };
}
type Agente = ReturnType<typeof useElenco>["lista"][number];

const Selo = ({ a }: { a: Agente }) => a.estado && <span className="ag-selo" data-tom={a.estado}><i aria-hidden="true" />{SELO[a.estado]}</span>;
const Custo = ({ a }: { a: Agente }) => a.custo != null && <span className="ag-custo" title="Energia que ocupa do seu plano"><i className="ph ph-lightning" aria-hidden="true" />{a.custo}</span>;
const Seta = () => <span className="ag-acao-seta" aria-hidden="true"><i className="ph ph-arrow-right" /></span>;
const Acao = ({ a, abrir }: { a: Agente; abrir: (a: Agente) => void }) => {
  if (!a.estado || a.estado === "off") return null;
  if (a.estado === "no_energy" || a.estado === "upgrade") return <a className="btn btn-ghost ag-acao" href="/precos">{ACAO[a.estado]}<Seta /></a>;
  return <button type="button" className="btn btn-primary ag-acao" onClick={() => void abrir(a)}>{ACAO[a.estado]}<Seta /></button>;
};

function Galeria({ lista }: { lista: Agente[] }) {
  const [ativo, setAtivo] = useState(0);
  const abrir = useAbrirConversa();
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
              {a.estado === "ready" && <span className="ag-faixa-ponto" role="img" aria-label="no seu time" />}
            </button>
            <div className="ag-faixa-info" id={`ag-g-${a.kind}`} inert={!on}>
              <span className="ag-olho">{a.tema}</span>
              <h2 className="ag-nome">{a.nome}</h2>
              <p className="ag-frase">{a.frase}</p>
              <p className="ag-texto">{a.texto}</p>
              <div className="ag-meta"><Selo a={a} /><span className="ag-ritmo"><i className="ph ph-clock" aria-hidden="true" />{a.ritmo}</span><Custo a={a} /></div>
              <Acao a={a} abrir={abrir} />
            </div>
          </li>
        );
      })}
    </ul>
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

export function AgentesPainel() {
  const { lista, energia, erro } = useElenco();
  return (
    <div className="span-12 ag">
      {energia && <Energia {...energia} />}
      {erro && <p className="ag-aviso" role="status">Não deu para carregar o estado dos seus agentes agora. Tente de novo em instantes.</p>}
      <Galeria lista={lista} />
    </div>
  );
}
