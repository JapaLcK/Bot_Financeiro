import { useId, useState, type ReactNode } from "react";
import type { Contas } from "../../lib/api-v2.gen";
import { moneyText } from "../../lib/format.js";
import { corDoBancoNoCard, segmentosDeContas, textos } from "../../lib/bancos";
import { mesAtual } from "../../lib/v2";
import { logoDoBanco, uidValido } from "../../conta/api";
import { Ladrilho } from "../../conta/BancoCard";
import { saldoOu } from "../../widgets/Contas";
import { Selos } from "../Selos";
import { LancamentosDoCard } from "./LancamentosDoCard";

// O card `contas` do chat (o `recursos` do bloco é `{ contas: <GET /api/v2/contas> }`): o disponível, a composição em barra e uma linha por conta
// (a cor da barra é a bolinha da linha). Tocar numa conta do total abre os lançamentos dela aqui dentro, sem folha e sem link. Corpo fora do
// contrato não desenha meio número, e motivo que não é texto derrubaria o Selos (erro #31).
export const contratoContas = (r: unknown) => {
  const d = (r as { contas?: Contas } | null)?.contas;
  return !!d && moneyText(d.total) !== null && textos(d.motivos) && !!d.carteira && textos(d.carteira.motivos)
    && Array.isArray(d.contas) && d.contas.every((c) => !!c && typeof c === "object" && textos(c.motivos));
};

// O logo do banco: `logo_proprio` (arquivo do repo) ou `logo_url` (CDN da Pluggy) da conta, filtrados por `logoDoBanco` (a mesma regra da tela Bancos: outro host, http ou
// caminho estranho = sem logo). Bloco de servidor antigo não traz os campos: vira a inicial na cor do banco, como a imagem quebrada.
// O ladrilho (logo, ou a inicial na cor do banco; a carteira leva o ícone) e, quando a barra desenha, a bolinha na quina com a cor do segmento.
function Marca({ nome, logo, cor, carteira, ponto }: { nome: string; logo: string | null; cor: string; carteira?: boolean; ponto?: string }) {
  return (
    <span className="ci-marca">
      {carteira ? <span className="banco-ini ci-carteira" aria-hidden="true"><i className="ph ph-wallet" /></span> : <Ladrilho nome={nome} logo={logo} cor={cor} />}
      {ponto && (carteira || logo) && <span className="ci-ponto" style={{ background: ponto }} aria-hidden="true" />}
    </span>
  );
}

type LinhaProps = { nome: string; saldo: string | null; moeda: string; motivos: string[]; logo: string | null; cor: string; ponto?: string; carteira?: boolean; aberta?: boolean; alternar?: () => void; regiao?: string; corpo?: ReactNode };
function Linha({ nome, saldo, moeda, motivos, logo, cor, ponto, carteira, aberta, alternar, regiao, corpo }: LinhaProps) {
  const miolo = <>
    <Marca nome={nome} logo={logo} cor={cor} ponto={ponto} carteira={carteira} />
    <span className="ci-nome">{nome}</span>
    <span className="ci-saldo num">{saldoOu(saldo, moeda)}</span>
  </>;
  return (
    <li className="ci-conta">
      {alternar
        ? <button type="button" className="ci-linha" aria-expanded={!!aberta} aria-controls={aberta ? regiao : undefined} onClick={alternar}>{miolo}<i className="ph ph-caret-right ci-seta" aria-hidden="true" /></button>
        : <div className="ci-linha">{miolo}<span /></div>}
      <Selos motivos={motivos} />
      {aberta && <div id={regiao} className="ci-aberta">{corpo}</div>}
    </li>
  );
}

export function CardContas({ d, mes, uid }: { d: Contas; mes: string | null; uid?: number }) {
  const [aberta, setAberta] = useState<number | null>(null), [fora, setFora] = useState(false);
  const base = useId(), foraId = useId(), segs = segmentosDeContas(d);
  const cor = (chave: "carteira" | number) => segs.find((s) => s.chave === chave)?.cor;
  const foraDoTotal = d.contas.filter((c) => !c.no_total);
  const nome = (c: Contas["contas"][number]) => [c.instituicao, c.nome].filter(Boolean).join(" · ") || "Conta";
  return (
    <>
      <div className="ci-total">
        <span className="stat-value num">{saldoOu(d.total, "BRL")}</span>
        <p className="ci-leg">Disponível agora</p>
      </div>
      {segs.length > 0 && <div className="comp-bar" aria-hidden="true">{segs.map((s) => <span key={s.chave} style={{ flexGrow: s.valor, background: s.cor }} />)}</div>}
      <ul className="ci-lista">
        <Linha nome="Carteira Piggy" saldo={d.carteira.saldo} moeda="BRL" motivos={d.carteira.motivos} logo={null} cor="var(--ink-2)" ponto={cor("carteira")} carteira />
        {d.contas.filter((c) => c.no_total).map((c, n) => {
          const abre = uidValido(uid) && Number.isInteger(c.id) && c.id > 0, regiao = `${base}-${c.id}`;
          return <Linha key={c.id} nome={nome(c)} saldo={c.saldo} moeda={c.moeda} motivos={c.motivos} logo={logoDoBanco(c)} cor={cor(c.id) ?? corDoBancoNoCard(c.instituicao ?? "", n + 1)} ponto={cor(c.id)} regiao={regiao}
            aberta={aberta === c.id} alternar={abre ? () => setAberta(aberta === c.id ? null : c.id) : undefined}
            corpo={abre && aberta === c.id && <><p className="ci-cap">Lançamentos deste mês</p><LancamentosDoCard uid={uid} filtros={{ conta: c.id, mes: mes ?? mesAtual() }} /></>} />;
        })}
      </ul>
      {d.contas.length === 0 && <p className="ci-vazio">Nenhum banco conectado.</p>}
      {foraDoTotal.length > 0 && (
        <>
          <button type="button" className="btn btn-quiet contas-mais" aria-expanded={fora} aria-controls={fora ? foraId : undefined} onClick={() => setFora(!fora)}>
            <i className="ph ph-caret-right" aria-hidden="true" />ver contas fora do total ({foraDoTotal.length})
          </button>
          {fora && <ul id={foraId} className="ci-lista">{foraDoTotal.map((c, i) => <Linha key={c.id} nome={nome(c)} saldo={c.saldo} moeda={c.moeda} motivos={c.motivos} logo={logoDoBanco(c)} cor={corDoBancoNoCard(c.instituicao ?? "", i + 1)} />)}</ul>}
        </>
      )}
    </>
  );
}
