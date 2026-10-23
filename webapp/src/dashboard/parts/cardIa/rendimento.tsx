import { useId, useState } from "react";
import { textos } from "../../lib/bancos";
import { medida, moneyText } from "../../lib/format.js";
import { obj, type Rendimento } from "../../lib/ia-cards";
import { FRASE_RITMO, ehNegativo, ehZero, hojeNoFuso, type Frase } from "../../lib/metas-textos";
import { FUSO } from "../../lib/v2";
import { TIPO } from "../CardInvestido";
import { Seg } from "../Seg";
import { Composicao, Expansivel } from "./Expansivel";
import { dataPtBr, lista } from "./legado";
import { useMais } from "./mais";

// O card `rendimento` ("Rendimentos", `{ rendimento: <GET /api/app/rendimento> }`, Pydantic: dinheiro e `pct` em texto). Só leitura do bloco: o total do Mês ou do Total, a barra por categoria
// (a cor e o ícone de cada tipo são os do card Investido) e as categorias que abrem os ativos. O servidor estima pelo saldo (db/rendimentos.py); aqui nada é recalculado. O Pydantic garante
// o tipo de cada campo, então o contrato confere só o dinheiro desenhado e a forma das listas. Diferença do app: um campo fora do contrato derruba o card inteiro (lá, só o item); sem o logo do banco.
type Rendimentos = NonNullable<Rendimento["rendimentos"]>;
type Categoria = Rendimentos["categorias"][number];
type Periodo = "mes" | "total";
const OPCOES: { value: Periodo; label: string }[] = [{ value: "mes", label: "Mês" }, { value: "total", label: "Total" }];
const FIM = { mes: "neste mês", total: "desde a aplicação" } as const;
const VISIVEIS = 5;

const din = (v: unknown) => v === null || moneyText(v) !== null;
const per = (p: unknown, comContagem = true) => obj(p) && din(p.valor) && (p.pct === null || typeof p.pct === "string") && (!comContagem || Number.isInteger(p.sem_dado));
const ativoOk = (a: unknown) => obj(a) && din(a.saldo) && per(a.mes, false) && per(a.total, false);
const categoriaOk = (c: unknown) => obj(c) && din(c.saldo) && per(c.mes) && per(c.total) && lista(c.ativos, ativoOk);
export const contratoRendimento = (r: unknown) => {
  const d = (r as { rendimento?: unknown } | null)?.rendimento;
  if (!obj(d) || d.tipo !== "contratado" || !textos(d.motivos)) return false;
  const x = d.rendimentos;
  return x === null || (obj(x) && din(x.saldo) && Number.isInteger(x.quantidade) && per(x.mes) && per(x.total) && typeof x.mes_desde === "string" && textos(x.motivos) && lista(x.categorias, categoriaOk));
};

const frase = (k: "plano_sem_investimentos" | "nenhum_investimento") => (FRASE_RITMO[k] as Frase).texto; // as duas são texto fixo
/** "+1,23%" / "−0,50%" / "0,00%" do texto do servidor (só exibição); ausente ou não numérico = `null` (a regra de `pctTexto` do app). */
const pctTexto = (pct: string | null) => { const v = Number(pct); if (!pct?.trim() || !Number.isFinite(v)) return null; return `${v === 0 ? "" : pct.startsWith("-") ? "−" : "+"}${pct.replace(/^[-+]/, "").replace(".", ",")}%`; };
// "dd/mm"; o ano só quando não é o corrente (em janeiro o "desde 31/12" é do ano passado)
const dia = (d: string) => { const t = dataPtBr(d); return t.length === 10 && d.slice(0, 4) === hojeNoFuso().slice(0, 4) ? t.slice(0, 5) : t; };
function sincronizadoEm(iso: string | null) {
  const d = iso ? new Date(iso) : null;
  if (!d || isNaN(d.getTime())) return null; // data inválida: some a frase, não inventa
  const f = (o: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat("pt-BR", { timeZone: FUSO, ...o }).format(d);
  return `Open Finance sincronizado em ${f({ day: "2-digit", month: "2-digit", ...(f({ year: "numeric" }) !== hojeNoFuso().slice(0, 4) && { year: "numeric" }) })} ${f({ hour: "2-digit", minute: "2-digit" })}.`;
}

/** Ganho em verde com "+", perda em tinta com "−" (oscilar não é erro, nada de vermelho); `null` é "Não informado", nunca zero. */
const ganhou = (v: string | null) => v !== null && !ehZero(v) && !ehNegativo(v);
function Ganho({ v, grande }: { v: string | null; grande?: boolean }) {
  if (v === null) return <span className="faint">Não informado</span>;
  return <span className={`num ${grande ? "stat-value" : "ci-saldo"}${ganhou(v) ? " gain" : ""}`}>{ganhou(v) && "+"}{moneyText(v)}</span>;
}
const Valores = ({ v, pct }: { v: string | null; pct: string | null }) => { const t = pctTexto(pct); return <span className="ci-valores"><Ganho v={v} />{t && <span className="ci-cap">{t}</span>}</span>; };

function Ativos({ ativos, k }: { ativos: Categoria["ativos"]; k: Periodo }) {
  // ponytail: a ordem usa o float do texto; a exibição segue exata. Nulos no fim, o resto do que mais rendeu.
  const ordem = [...ativos].sort((a, b) => (a[k].valor === null ? 1 : 0) - (b[k].valor === null ? 1 : 0) || Number(b[k].valor ?? 0) - Number(a[k].valor ?? 0));
  const [vistos, mais] = useMais(ordem, VISIVEIS, ["ativo", "ativos"]);
  if (!ativos.length) return <p className="ci-cap">Não informado</p>;
  return (
    <>
      <ul className="ci-subs">
        {vistos.map((a, i) => (
          <li key={i} className="ci-sub ci-duas">
            <span className="ci-dupla"><span>{a.nome ?? "Investimento"}</span><span className="faint">{[a.instituicao, a.saldo !== null && moneyText(a.saldo)].filter(Boolean).join(" · ")}</span></span>
            <Valores v={a[k].valor} pct={a[k].pct} />
          </li>
        ))}
      </ul>
      {mais}
    </>
  );
}

export function CardRendimento({ d }: { d: Rendimento }) {
  const [escolha, setEscolha] = useState<Periodo | null>(null), [aberta, setAberta] = useState<number | null>(null), base = useId(), r = d.rendimentos;
  if (!r) return <p className="ci-vazio">Não foi possível ler seus rendimentos agora.</p>;
  if (r.motivos.includes("plano_sem_investimentos")) return <p className="ci-vazio">{frase("plano_sem_investimentos")}</p>;
  if (!r.categorias.length) return <p className="ci-vazio">{frase("nenhum_investimento")}</p>;
  const k = escolha ?? (r.mes.valor === null ? "total" : "mes"), p = r[k], pct = pctTexto(p.pct), sincronizado = sincronizadoEm(r.sincronizado_em);
  const rodape = k === "mes"
    ? `Estimado pela variação do saldo desde ${dia(r.mes_desde)}, sem contar aportes. Ações, FIIs e fundos: só a variação de preço; dividendos não entram.`
    : "Saldo menos o valor aplicado, como o banco informa. Ações, FIIs e fundos não informam o custo e ficam sem rendimento.";
  const tipo = (c: Categoria) => TIPO[c.tipo] ?? TIPO.outros;
  const fatias = r.categorias.filter((c) => medida(c[k].valor) > 0).map((c) => ({ nome: c.rotulo, valor: c[k].valor!, pct: 0, cor: tipo(c).cor }));
  return (
    <>
      <Seg label="Período do rendimento" value={k} onChange={setEscolha} options={OPCOES} />
      <div className="ci-total">
        <div className="ci-manchete"><Ganho v={p.valor} grande />{pct && <span className={`selo${ganhou(p.valor) ? " gain" : ""}`}>{pct}</span>}</div>
        <p className="ci-leg">{FIM[k]}{r.saldo !== null && ` · saldo ${moneyText(r.saldo)}`}</p>
        {p.valor === null && k === "mes" && <p className="ci-leg">O rendimento do mês aparece quando houver a foto do fechamento do mês anterior.</p>}
        {p.valor !== null && p.sem_dado > 0 && <p className="ci-leg">{`${p.sem_dado} de ${r.quantidade} ativos sem rendimento informado`}</p>}
      </div>
      {fatias.length > 0 && <Composicao itens={fatias} />}
      <ul className="ci-lista">
        {r.categorias.map((c, i) => (
          <Expansivel key={i} classe="ci-rend" regiao={`${base}-${i}`} aberta={aberta === i} alternar={() => setAberta(aberta === i ? null : i)} corpo={<Ativos key={k} ativos={c.ativos} k={k} />}
            miolo={<><i className={tipo(c).icone} style={{ color: tipo(c).cor }} aria-hidden="true" /><span className="ci-dupla"><span className="ci-nome">{c.rotulo}</span><span className="ci-cap">{[c.ativos.length === 1 ? "1 ativo" : `${c.ativos.length} ativos`, c.saldo !== null && moneyText(c.saldo)].filter(Boolean).join(" · ")}</span></span><Valores v={c[k].valor} pct={c[k].pct} /></>} />
        ))}
      </ul>
      <p className="ci-cap">{[sincronizado, rodape].filter(Boolean).join(" ")}</p>
    </>
  );
}
