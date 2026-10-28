import { Component, useId, useState, type ReactNode } from "react";
import { SEM_CARD, type BlocoIa } from "../lib/conversaIa";
import { isoDay, monthShort, monthTitle, monthYear } from "../lib/format.js";
import { Seg } from "./Seg";
import { CardAssinaturasIa, contratoAssinaturasBloco } from "./cardIa/assinaturas";
import { CardCaixinhasLegado, contratoCaixinhasLegado } from "./cardIa/caixinhas";
import { CardFatura, CardParcelas, contratoFatura, contratoParcelas } from "./cardIa/cartao";
import { CardContas, contratoContas } from "./cardIa/contas";
import { CardCalendario, CardCategorias, contratoCalendario, contratoCategorias } from "./cardIa/gastos";
import { CardMetas, contratoMetas } from "./cardIa/metas";
import { CardPatrimonio, CardWealth, contratoPatrimonio } from "./cardIa/patrimonio";
import { CardPiggy, contratoPiggy } from "./cardIa/piggy";
import { CardCompromissos, CardHero, contratoCompromissos, contratoHero, diasDoBloco } from "./cardIa/previsao";
import { CardRenda, contratoRenda } from "./cardIa/renda";
import { CardRendimento, contratoRendimento } from "./cardIa/rendimento";
import { CardPeriodo, CardResumo, contratoPeriodo, contratoResumo, tituloDoPeriodo } from "./cardIa/resumo";
import { CardSimulador, contratoSimulador } from "./cardIa/simulador";

// Um card que a IA manda no chat (`bloco` do stream, lib/conversaIa.ts): `card` + 1 a 3 abas `{ mes, recursos }` com o dado lido NA HORA pelo servidor (uma foto).
// Desenhado do bloco, sem consulta ao servidor (as ações de cada card, como abrir lançamentos ou pedir algo ao chat, conferem a conta: parts/cardIa/). Sem `Frame`
// e sem o widget do Resumo: tudo inline, sem folha e sem link. Card desconhecido, aba fora do envelope ou recurso fora do contrato = o marcador, sem número.
// `desenha` recebe o `recursos` JÁ conferido por `contrato`; o tipo fundo é de cada card.
type Ctx = { uid?: number; mes: string | null };
type Def = { titulo: (recursos: unknown) => string; semMes?: true; contrato: (recursos: unknown) => boolean; desenha: (recursos: any, c: Ctx) => ReactNode }; // eslint-disable-line @typescript-eslint/no-explicit-any
// Uma entrada por linha, 2 espaços de recuo: o teste dos ids (§0.7) lê estas chaves. Títulos iguais aos do app (app/src/features/painel/catalogo.ts).
const CARDS: Record<string, Def> = {
  contas: { titulo: () => "Contas", contrato: contratoContas, desenha: (r, c) => <CardContas d={r.contas} {...c} /> },
  hero: { titulo: () => "Saldo previsto", contrato: contratoHero, desenha: (r) => <CardHero d={r.previsao} /> },
  compromissos: { titulo: (r) => `Próximos ${diasDoBloco(r)} dias`, contrato: contratoCompromissos, desenha: (r) => <CardCompromissos d={r.previsao} /> },
  patrimonio: { titulo: () => "Patrimônio", contrato: contratoPatrimonio, desenha: (r) => <CardPatrimonio d={r.patrimonio} /> },
  wealth: { titulo: () => "Onde está o dinheiro", contrato: contratoPatrimonio, desenha: (r) => <CardWealth d={r.patrimonio} /> },
  assinaturas: { titulo: () => "Assinaturas", contrato: contratoAssinaturasBloco, desenha: (r) => <CardAssinaturasIa d={r.assinaturas} /> },
  metas: { titulo: () => "Metas", contrato: contratoMetas, desenha: (r, c) => <CardMetas d={r.metas} uid={c.uid} /> },
  resumo: { titulo: () => "Resumo do mês", contrato: contratoResumo, desenha: (r) => <CardResumo resumo={r.resumo} detalhes={r.detalhes} /> },
  categorias: { titulo: () => "Para onde vai", contrato: contratoCategorias, desenha: (r, c) => <CardCategorias d={r.detalhes} uid={c.uid} /> },
  calendario: { titulo: () => "Dia a dia", contrato: contratoCalendario, desenha: (r, c) => <CardCalendario d={r.detalhes} uid={c.uid} /> },
  periodo: { titulo: tituloDoPeriodo, contrato: contratoPeriodo, desenha: (r) => <CardPeriodo d={r.periodo} /> },
  caixinhas: { titulo: () => "Caixinhas", contrato: contratoCaixinhasLegado, desenha: (r) => <CardCaixinhasLegado d={r.caixinhas} /> },
  fatura: { titulo: () => "Fatura do cartão", contrato: contratoFatura, desenha: (r) => <CardFatura d={r.cartoes} /> },
  parcelas: { titulo: () => "Parcelas futuras", contrato: contratoParcelas, desenha: (r) => <CardParcelas d={r.parcelas} /> },
  renda: { titulo: () => "Renda mês a mês", contrato: contratoRenda, desenha: (r) => <CardRenda d={r.renda} /> },
  piggy: { titulo: () => "Piggy notou", contrato: contratoPiggy, desenha: (r) => <CardPiggy d={r.insights} /> },
  rendimento: { titulo: () => "Rendimentos", contrato: contratoRendimento, desenha: (r) => <CardRendimento d={r.rendimento} /> },
  simulador: { titulo: () => "Simulador", semMes: true, contrato: contratoSimulador, desenha: (r, c) => <CardSimulador r={r} uid={c.uid} /> }, // o bloco leva `mes`, mas o título do app é só "Simulador"
};

const MES = /^\d{4}-(0[1-9]|1[0-2])$/;
const abaOk = (a: unknown) => {
  const x = a as { mes?: unknown; recursos?: unknown } | null;
  return !!x && (x.mes === null || (typeof x.mes === "string" && MES.test(x.mes))) && !!x.recursos && typeof x.recursos === "object" && !Array.isArray(x.recursos);
};

const Marcador = () => <p className="msg-text msg-bloco">{SEM_CARD}</p>;

// Um campo fundo malformado que passa pelo `contrato` lança no render e, sem isto, desmontaria a raiz do React (o /painel some): só este card cai.
// O Resumo (widgets/BlocosReais.tsx) o reusa e troca o marcador do chat pela frase do bloco (`fallback`).
export class LimiteCard extends Component<{ children: ReactNode; fallback?: ReactNode }, { caiu: boolean }> {
  state = { caiu: false };
  static getDerivedStateFromError() { return { caiu: true }; }
  render() { return this.state.caiu ? this.props.fallback ?? <Marcador /> : this.props.children; }
}

type Aba = { mes: string | null; recursos: unknown };
export function CardIa({ bloco, uid }: { bloco: BlocoIa; uid?: number }) {
  const id = useId(), [sel, setSel] = useState(0);
  const def = Object.prototype.hasOwnProperty.call(CARDS, bloco.card) ? CARDS[bloco.card] : undefined; // "constructor" não é card
  // Valida TODAS as abas antes de desenhar qualquer uma: uma aba ruim faz o card inteiro virar o marcador.
  if (!def || !bloco.abas.every(abaOk) || !bloco.abas.every((a) => def.contrato(a.recursos))) return <Marcador />;
  const abas = bloco.abas as Aba[], aba = abas[Math.min(sel, abas.length - 1)];
  // Rótulo único (é a identidade no `Seg`): "out" se todas são do mesmo ano, senão "out/26"; sem mês, "1", "2", "3".
  const mesmoAno = abas.every((a) => a.mes?.slice(0, 4) === abas[0].mes?.slice(0, 4));
  const rotulo = (a: Aba, i: number) => (a.mes ? (mesmoAno ? monthShort(isoDay(`${a.mes}-01`)) : monthYear(isoDay(`${a.mes}-01`))) : String(i + 1));
  return (
    <LimiteCard>
      <section className="panel msg-block card-ia" aria-labelledby={id}>
        <h2 id={id} className="card-ia-tit">{def.titulo(aba.recursos)}{aba.mes && !def.semMes && ` · ${monthTitle(aba.mes)}`}</h2>
        {abas.length > 1 && <Seg label="Mês do card" value={String(sel)} onChange={(v) => setSel(Number(v))} options={abas.map((a, i) => ({ value: String(i), label: rotulo(a, i) }))} />}
        <div key={sel} className="ci-corpo">{def.desenha(aba.recursos, { uid, mes: aba.mes })}</div>
      </section>
    </LimiteCard>
  );
}

/** O lugar do card enquanto o passo da IA diz qual vem (`previa`): só a forma, sem texto. */
export const Esqueleto = () => <div className="panel msg-block card-ia-esqueleto" aria-hidden="true" />;
