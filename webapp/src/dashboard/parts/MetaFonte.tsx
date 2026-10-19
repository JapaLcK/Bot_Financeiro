import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Fonte } from "../lib/api-v2.gen";
import { ErroApi, contasQuery, investidoQuery } from "../lib/v2";
import { href } from "../router";
import { Selos } from "./Selos";

// A fonte escolhida no formulário. `conta` é o id público de /contas, em texto (o <select>).
export type FonteCampos = { tipo: Fonte["tipo"]; banco: string; conta: string };

// Quanto do investimento a meta lê: tudo, um banco ou o saldo de uma conta. Sem o recurso de
// investimentos no plano (403 `pro_required` em /investido) só sobra a conta.
export function MetaFonte({ valor, onChange, desabilitado, erro, id, padraoConta }: {
  valor: FonteCampos; onChange: (f: FonteCampos) => void; desabilitado: boolean; erro?: string; id: string; padraoConta?: boolean;
}) {
  const inv = useQuery(investidoQuery);
  const contas = useQuery(contasQuery);
  const semPlano = inv.error instanceof ErroApi && inv.error.status === 403 && inv.error.code === "pro_required";
  const invFalhou = inv.isError && !semPlano;
  // Meta nova sem o plano: os rádios de investimento nem aparecem, então o padrão é a conta (nunca "investido" escondido, que dá 403).
  useEffect(() => { if (padraoConta && semPlano && valor.tipo !== "conta") onChange({ ...valor, tipo: "conta" }); }, [padraoConta, semPlano]);
  const bancos = inv.data?.por_banco.map((b) => b.banco) ?? [];
  const lista = contas.data?.contas.filter((c) => c.moeda === "BRL") ?? [];
  const escolhida = lista.find((c) => String(c.id) === valor.conta);
  const radio = (tipo: Fonte["tipo"], texto: string) => (
    <label className="meta-radio"><input type="radio" name="fonte" checked={valor.tipo === tipo} disabled={desabilitado}
      onChange={() => onChange({ ...valor, tipo })} />{texto}</label>
  );
  return (
    <fieldset className="meta-fonte" aria-describedby={erro ? `${id}-fonte` : undefined}>
      <legend>De onde vem o dinheiro</legend>
      {semPlano ? (
        <p className="meta-nota">Seu plano atual não inclui investimentos. Dá para usar o saldo de uma conta. <a href="/precos">Ver planos</a></p>
      ) : (
        <>
          {radio("investido", "Tudo que tenho investido")}
          {radio("banco", "Investido em um banco")}
          {valor.tipo === "banco" && (
            <>
              <select className="field" name="banco" aria-label="Banco" value={valor.banco} disabled={desabilitado || invFalhou}
                onChange={(e) => onChange({ ...valor, banco: e.target.value })}>
                <option value="" disabled>{inv.isPending ? "Carregando…" : "Escolha um banco"}</option>
                {valor.banco && !bancos.includes(valor.banco) && <option value={valor.banco}>{valor.banco} (não aparece mais)</option>}
                {bancos.map((b) => <option key={b} value={b}>{b}</option>)}
              </select>
              {invFalhou && <p className="meta-nota" role="alert">Não deu para carregar seus bancos. <button type="button" className="btn btn-quiet" onClick={() => inv.refetch()}>Tentar de novo</button></p>}
            </>
          )}
        </>
      )}
      {radio("conta", "Saldo de uma conta")}
      {valor.tipo === "conta" && (
        <>
          <select className="field" name="conta" aria-label="Conta" value={valor.conta} disabled={desabilitado || contas.isError}
            onChange={(e) => onChange({ ...valor, conta: e.target.value })}>
            <option value="" disabled>{contas.isPending ? "Carregando…" : "Escolha uma conta"}</option>
            {valor.conta && !escolhida && <option value={valor.conta}>Conta atual (não aparece mais)</option>}
            {lista.map((c) => <option key={c.id} value={c.id}>{[c.instituicao, c.nome].filter(Boolean).join(" · ") || "Conta"}</option>)}
          </select>
          {escolhida && <Selos motivos={escolhida.motivos} />}
          {contas.isSuccess && !lista.length && <p className="meta-nota">Você não tem conta em reais conectada. <a href={href("/conta/bancos")}>Conectar banco</a></p>}
          {contas.isError && <p className="meta-nota" role="alert">Não deu para carregar suas contas. <button type="button" className="btn btn-quiet" onClick={() => contas.refetch()}>Tentar de novo</button></p>}
        </>
      )}
      {erro && <span id={`${id}-fonte`} className="warn">{erro}</span>}
    </fieldset>
  );
}
