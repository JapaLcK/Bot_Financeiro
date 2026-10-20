// Detalhe da categoria com backend (/api/v2/gastos/categoria): a escolhida em "Para onde vai" ou,
// sem escolha, a maior do mês. Dinheiro é texto do servidor (moneyText); Number() só dá a altura das barras.
import { useQuery } from "@tanstack/react-query";
import { dayMonth, isoDay, medida, moneyText, monthShort } from "../lib/format.js";
import { useActions } from "../lib/actions";
import { mesDe } from "../lib/store.js";
import { DEMO, gastosCategoriaQuery, gastosQuery } from "../lib/v2";
import type { DashState } from "../lib/types";
import { Frame } from "../parts/Frame";
import { AvisoMoeda, estadoGastos } from "../parts/GastosEstado";
import { ORIGENS } from "../parts/LancamentoLinha";
import { IconeCategoria } from "../parts/IconeCategoria";
import { Selos } from "../parts/Selos";

export function GastosDetalhe({ s }: { s: DashState }) {
  const { set } = useActions();
  const mes = mesDe(s);
  const escolhida = s.filter.category;
  const geral = useQuery({ ...gastosQuery(mes), enabled: !escolhida && !DEMO }); // só para achar a maior do mês
  const chave = escolhida ?? geral.data?.categorias[0]?.chave ?? null;
  const q = useQuery({ ...gastosCategoriaQuery(mes, chave ?? ""), enabled: !!chave && !DEMO });
  const estado = estadoGastos(chave ? q : geral);
  if (estado || !chave) return <Frame id="cat-detalhe" title="Categoria" real>{estado ?? (geral.data?.saidas_em_outra_moeda ? <AvisoMoeda n={geral.data.saidas_em_outra_moeda} /> : <p className="faint">Nenhum gasto neste mês.</p>)}</Frame>;
  const d = q.data!; // sem estado, a consulta da categoria já respondeu

  const c = d.categoria;
  const parcial = d.historico_desde !== null; // o corte do plano cai dentro do mês escolhido
  const aberto = !!s.gastosAberto && "categoria" in s.gastosAberto && s.gastosAberto.categoria === chave; // o card de compras é o desta categoria
  const corrente = d.hoje.slice(0, 7);
  const pico = Math.max(...d.serie.map((x) => medida(x.valor)), 1);
  const picoLoja = Math.max(...d.estabelecimentos.map((p) => medida(p.valor)), 1);
  return (
    <Frame id="cat-detalhe" real
      title={<span className="detail-title"><IconeCategoria icone={c.icone} /><span className="detail-nome">{c.nome}</span>{!escolhida && <span className="faint detail-auto">maior gasto do mês</span>}</span>}
      aside={<button type="button" className="link" aria-expanded={aberto} aria-controls={aberto ? "gastos-compras" : undefined} onClick={() => set({ gastosAberto: aberto ? null : { categoria: chave } })}>Ver compras</button>}>
      <dl className="detail-facts">
        <div><dt>No mês{parcial && <span className="faint"> · parcial</span>}</dt><dd className="num">{moneyText(d.total) ?? "—"}</dd></div>
        <div><dt>Compras</dt><dd className="num">{d.compras ?? "—"}</dd></div>
        <div><dt>Valor médio</dt><dd className="num">{moneyText(d.valor_medio) ?? "—"}</dd></div>
      </dl>
      <AvisoMoeda n={d.saidas_em_outra_moeda} />
      <Selos motivos={d.motivos} />

      <div className="detail-cols">
        <section aria-label="Últimos três meses">
          <h3 className="detail-h">Últimos meses</h3>
          <ul className="months">
            {d.serie.map((x, i) => (
              <li key={x.mes} data-on={i === d.serie.length - 1 || undefined}>
                <span className="months-bar">{x.valor !== null && <i style={{ background: c.cor, transform: `scaleY(${medida(x.valor) / pico})` }} />}</span>
                {x.valor === null ? <span className="faint">fora do histórico</span> : <b className="num">{moneyText(x.valor)}</b>}
                <span className="faint">{monthShort(isoDay(`${x.mes}-01`))}{x.mes === corrente ? " · mês em andamento" : ""}</span>
              </li>
            ))}
          </ul>
        </section>
        <section aria-label="Onde mais gasta">
          <h3 className="detail-h">Onde mais gasta</h3>
          {d.estabelecimentos.length === 0 ? <p className="faint">Nenhum gasto nesta categoria no mês.</p> : (
            <ul className="places">
              {d.estabelecimentos.map((p) => (
                <li key={p.nome}>
                  <span className="place-name">{p.nome}</span>
                  <span className="faint num">{p.quantidade}×</span>
                  <b className="num">{moneyText(p.valor)}</b>
                  <span className="place-bar" aria-hidden="true"><i style={{ background: c.cor, transform: `scaleX(${medida(p.valor) / picoLoja})` }} /></span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {d.ultimas.length > 0 && (
        <section className="detail-recent" aria-label="Últimas compras">
          <h3 className="detail-h">Últimas compras</h3>
          <ul className="inv-list">
            {d.ultimas.map((l, i) => (
              <li key={i}>
                <span className="faint num">{dayMonth(isoDay(l.data))}</span>
                <span className="inv-name">{l.descricao}{l.mensagem && <q className="row-msg"> {l.mensagem}</q>}</span>
                <span className="faint">{ORIGENS[l.origem]}{l.data > d.hoje ? " · agendado" : ""}</span>
                <b className="num">{moneyText(l.valor)}</b>
              </li>
            ))}
          </ul>
        </section>
      )}
    </Frame>
  );
}
