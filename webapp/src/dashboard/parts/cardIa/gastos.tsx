import { useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { uidValido } from "../../conta/api";
import { textos } from "../../lib/bancos";
import { diaCurto, existe } from "../../lib/extrato-dias";
import { medida, moneyText } from "../../lib/format.js";
import { obj, type MesDetalhes } from "../../lib/ia-cards";
import { categoriasQuery } from "../../lib/v2";
import { ddmm, saida } from "../../widgets/Compromissos";
import { Selos } from "../Selos";
import { Composicao, Expansivel, LinhasCat, type ItemCat } from "./Expansivel";
import { LancamentosDoCard } from "./LancamentosDoCard";
import { pctDe, soma } from "./decimais";
import { useMais } from "./mais";

// Os cards `categorias` ("Para onde vai") e `calendario` ("Dia a dia") do chat: o `recursos` do bloco é `{ detalhes: <MesDetalhes> }` (e, no categorias, `contas`, que
// aqui não se desenha). Os números são os do bloco (uma foto); tocar numa categoria ou num dia abre os lançamentos DELE aqui dentro (LancamentosDoCard: a conta da
// sessão é conferida antes e depois de cada página). Como no app: até 5 categorias, ou as 4 maiores e "Outras" (que abre as restantes), e os dias do mais novo ao
// mais velho, 7 por vez. "Sem categoria" (chave nula ou vazia) abre com `categoria=""`, como o app: o servidor junta vazio, nulo e "sem categoria" na mesma
// chave (`cat_key_sql`; tests/test_api_v2_lancamentos.py::test_categoria_sem_categoria_junta_null_vazio_e_o_nome_e_nao_pega_outra).
const MES = /^\d{4}-(0[1-9]|1[0-2])$/;
const det = (r: unknown) => (r as { detalhes?: MesDetalhes } | null)?.detalhes;
const mesOk = (d: unknown): d is MesDetalhes => obj(d) && typeof d.mes === "string" && MES.test(d.mes) && typeof d.ate === "string" && textos(d.motivos);
export const contratoCategorias = (r: unknown) => { const d = det(r); return mesOk(d) && existe(d.ate) && Array.isArray(d.categorias) && d.categorias.every((c) => obj(c) && moneyText(c.valor) !== null); };
export const contratoCalendario = (r: unknown) => { const d = det(r); return mesOk(d) && Array.isArray(d.dias) && d.dias.every((x) => obj(x) && typeof x.dia === "string" && moneyText(x.entrou) !== null && moneyText(x.saiu) !== null); };

const VISIVEIS = 5; // categorias à mostra; com mais, as 4 maiores e "Outras"
export const maiuscula = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export function CardCategorias({ d, uid }: { d: MesDetalhes; uid?: number }) {
  const catalogo = useQuery(categoriasQuery).data?.categorias ?? [];
  if (!d.categorias.length) return <p className="ci-vazio">Nenhum gasto em reais registrado neste período.</p>;
  const total = soma(d.categorias.map((c) => c.valor));
  const todas: ItemCat[] = d.categorias.map((c) => {
    const cat = catalogo.find((x) => x.chave === c.categoria);
    return {
      nome: maiuscula(cat?.nome || c.categoria || "Sem categoria"), valor: c.valor, pct: pctDe(c.valor, total), cor: cat?.cor ?? "var(--ink-3)",
      corpo: uidValido(uid) ? <LancamentosDoCard uid={uid} filtros={{ mes: d.mes, categoria: c.categoria ?? "", tipo: "saida" }} /> : undefined,
    };
  });
  const resto = todas.length > VISIVEIS ? todas.slice(VISIVEIS - 1) : [], restoValor = soma(resto.map((c) => c.valor));
  const itens = resto.length ? [...todas.slice(0, VISIVEIS - 1), { nome: "Outras", valor: restoValor, pct: pctDe(restoValor, total), cor: "var(--ink-3)", corpo: <LinhasCat itens={resto} /> }] : todas;
  return (
    <>
      <div className="ci-total">
        <span className="stat-value num">{moneyText(total)}</span>
        <p className="ci-leg">até {ddmm(d.ate)}</p>
      </div>
      <Composicao itens={itens} />
      <LinhasCat itens={itens} />
      <Selos motivos={d.motivos} />
    </>
  );
}

// "Sex · 17/10"; de outro ano que o do mês (a compra parcelada conserva o dia original): "Sex · 17/10/25". Sem "Hoje"/"Ontem" (a foto envelhece). Data que não existe: o texto cru.
const rotuloDia = (dia: string, mes: string) => existe(dia) ? diaCurto(dia, mes.slice(0, 4)) : dia;

export function CardCalendario({ d, uid }: { d: MesDetalhes; uid?: number }) {
  const [aberto, setAberto] = useState<string | null>(null), base = useId();
  const dias = [...d.dias].sort((a, b) => b.dia.localeCompare(a.dia));
  // Cada barra na sua escala (a de entrou, a de saiu): num mês com o salário, a de saiu ficaria um risco ao lado dele.
  const maior = (k: "entrou" | "saiu") => Math.max(1, ...dias.map((x) => medida(x[k])));
  const [maxEntrou, maxSaiu] = [maior("entrou"), maior("saiu")];
  const [vistos, mais] = useMais(dias, 7, ["dia", "dias"]);
  if (!dias.length) return <p className="ci-vazio">Ainda não há movimentos neste período.</p>;
  const barra = (valor: string, cor: string, maximo: number) => <span className="ci-trilho"><i style={{ background: cor, transform: `scaleX(${medida(valor) / maximo})` }} /></span>;
  return (
    <>
      <ul className="ci-lista">
        {vistos.map((x) => (
          <Expansivel key={x.dia} classe="ci-dia" regiao={`${base}-${x.dia}`} aberta={aberto === x.dia} alternar={() => setAberto(aberto === x.dia ? null : x.dia)}
            corpo={existe(x.dia) && uidValido(uid) ? <LancamentosDoCard uid={uid} filtros={{ mes: d.mes, dia: x.dia }} /> : undefined}
            miolo={<>
              <span className="ci-nome">{rotuloDia(x.dia, d.mes)}</span>
              <span className="ci-trilhos" aria-hidden="true">{barra(x.entrou, "var(--gain)", maxEntrou)}{barra(x.saiu, "var(--alert)", maxSaiu)}</span>
              <span className="ci-valores num">
                {medida(x.entrou) > 0 && <span className="gain"><span className="sr-only">Entrou </span>+{moneyText(x.entrou)}</span>}
                {(medida(x.saiu) > 0 || medida(x.entrou) === 0) && <span><span className="sr-only">Saiu </span>{saida(x.saiu)}</span>}
              </span>
            </>} />
        ))}
      </ul>
      {mais}
      <Selos motivos={d.motivos} />
    </>
  );
}
