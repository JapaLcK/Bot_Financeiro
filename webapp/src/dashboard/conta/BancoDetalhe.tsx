import { useId, useRef } from "react";
import { FUSO, instante } from "../lib/v2";
import { haQuanto } from "../lib/format.js";
import { NATIVE, useDialogo } from "../parts/dialog";
import { corDoBanco } from "../lib/bancos";
import { Estado, Ladrilho, dinheiro, nomeDoBanco, plural, type Linha } from "./BancoCard";
import type { Conexao } from "./api";

const quando = new Intl.DateTimeFormat("pt-BR", { timeZone: FUSO, day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }); // sem dateStyle: Safari 14.0

// O detalhe de um banco: <dialog> modal (tela cheia no celular, painel à direita no desktop). Fecha com X, Esc e clique no fundo; o pai o desmonta
// quando a conexão some. `bloqueado` (há um diálogo/DELETE em andamento) trava o fechar e desabilita as ações; elas ficam em `.banco-det-acoes`
// (grid de uma coluna): outra ação entra ali como um irmão do botão, sem CSS novo. `sync` (o POST do "Sincronizar" saindo; na fase de coleta fica livre) só desabilita as ações: fechar segue livre. `onDesconectar` recebe o botão para o foco voltar a ele.
// `onReconectar` só vem quando o banco é reconectável (Bancos.tsx); quem o trata fecha este detalhe ANTES de abrir o widget da Pluggy (o <dialog> modal cobriria o widget).
// ponytail: no Safari < 15.4 (sem <dialog>) o Confirma por cima solta a trava de foco deste painel (parts/dialog.ts: uma trava por vez).
export function BancoDetalhe({ c, i, v, bloqueado, sync, falha, onDesconectar, onReconectar, fechar }: { c: Conexao; i: number; v: Linha; bloqueado: boolean; sync: boolean; falha?: string; onDesconectar: (botao: HTMLButtonElement) => void; onReconectar?: () => void; fechar: () => void }) {
  const id = useId();
  const trava = useRef(false);
  trava.current = bloqueado; // lido pelo Esc/cancel/fundo de useDialogo, que guarda a função da 1ª render
  const { fecharDialog, fundo, props } = useDialogo(fechar, () => trava.current);
  const nome = nomeDoBanco(c);
  const ms = instante(c.last_sync_at);
  const linha = typeof v === "string" ? null : v;
  const contagens = linha ? [
    { icone: "ph-bank", texto: plural(linha.contas, "conta", "contas")[0], n: linha.contas },
    { icone: "ph-credit-card", texto: plural(linha.cartoes, "cartão", "cartões")[0], n: linha.cartoes },
    { icone: "ph-chart-line-up", texto: plural(linha.investimentos, "investimento", "investimentos")[0], n: linha.investimentos },
  ].filter((x) => x.texto) : [];
  return <>
    <dialog {...props} className={`banco-det${NATIVE ? "" : " banco-det-fb"}`} aria-labelledby={`${id}-t`}>
      <div className="banco-det-in">
        <div className="banco-det-cab"><button type="button" className="banco-det-x" aria-label="Fechar" onClick={fecharDialog}><i className="ph ph-x" aria-hidden="true" /></button></div>
        <div className="banco-det-corpo">
          <div className="banco-det-id">
            <Ladrilho nome={nome} logo={c.logo_url} cor={corDoBanco(c.institution_name ?? "", i)} grande />
            <h2 id={`${id}-t`}>{nome}</h2>
            <Estado c={c} />
            {c.ui.detail && <p>{c.ui.detail}</p>}
          </div>
          <b className="num banco-det-total">{dinheiro(v, linha?.total ?? null)}</b>
          <dl className="banco-det-dados">
            {Number.isFinite(ms) && <div><dt><i className="ph ph-clock" aria-hidden="true" />Última atualização</dt><dd><time dateTime={c.last_sync_at!} title={quando.format(new Date(ms))}>{haQuanto(ms)}</time></dd></div>}
            <div><dt className="com-marca"><i className="ph ph-wallet" aria-hidden="true" />Em conta<span className="marca cor-conta" aria-hidden="true" /></dt><dd className="num">{dinheiro(v, linha?.saldo_conta ?? null)}</dd></div>
            <div><dt className="com-marca"><i className="ph ph-chart-line-up" aria-hidden="true" />Investido<span className="marca cor-inv" aria-hidden="true" /></dt><dd className="num">{dinheiro(v, linha?.investido ?? null)}</dd></div>
            {contagens.length > 0 && (
              <div><dt className="sr-only">Contagens</dt><dd className="banco-det-cont">{contagens.map((x) => <span key={x.icone}><i className={`ph ${x.icone}`} aria-hidden="true" /><b className="num" aria-hidden="true">{x.n}</b><span className="sr-only">{x.texto}</span></span>)}</dd></div>
            )}
          </dl>
          <p className="banco-det-seguro"><i className="ph ph-lock" aria-hidden="true" />Conexão regulamentada pelo Banco Central, oficial pelo Open Finance.</p>
          {falha && <p role="alert" className="warn">{falha}</p>}
        </div>
        <div className="banco-det-acoes">
          {onReconectar && <button type="button" className={`btn ${c.ui.state === "needs_user_action" ? "btn-primary" : "btn-ghost"}`} disabled={bloqueado || sync} onClick={onReconectar}><i className="ph ph-arrows-clockwise" aria-hidden="true" />Reconectar banco</button>}
          <button type="button" className="btn banco-det-desc" disabled={bloqueado || sync} onClick={(e) => onDesconectar(e.currentTarget)}><i className="ph ph-trash" aria-hidden="true" />Desconectar banco</button>
        </div>
      </div>
    </dialog>
    {!NATIVE && <div className="lanc-scrim" aria-hidden="true" onClick={fundo} />}
  </>;
}
