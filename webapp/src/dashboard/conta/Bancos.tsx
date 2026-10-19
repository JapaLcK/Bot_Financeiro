import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { ErroApi, contaQuery, contasQuery } from "../lib/v2";
import { moneyText } from "../lib/format.js";
import { bancosValidos, barraDosBancos } from "../lib/bancos";
import { ICON } from "../lib/brand";
import { OF } from "../parts/guia-falas";
import { Selos } from "../parts/Selos";
import { route } from "../router";
import { ContaTrocada, bancosQuery, comPrazoLeitura, conexoesQuery, desconectarBanco, limiteQuery, uidValido, type Conexao } from "./api";
import { BancoCard, nomeDoBanco } from "./BancoCard";
import { Confirma } from "./Confirma";

type Alvo = { uid: number; id: number; nome: string };

// Bancos conectados (/conta/bancos): o estado de cada conexão vem do snapshot do Open Finance (`/open-finance/{uid}`, o uid
// só da sessão), os valores de `/api/v2/bancos` e as vagas de `/limite`. Só o snapshot é obrigatório: sem `/bancos` os cards ficam sem números.
// Desconectar um banco (DELETE /open-finance/{uid}/connections/{id}, um por vez; o `uid` é o do clique). Resposta perdida ou erro: a
// verdade é o snapshot relido (sem o id = foi). Plano e tabela de estados: docs/plano-bancos-conectados-painel.md, W2.
// O topo é o `total` do servidor (o cliente não soma para exibir); a barra só compara e some se algo deixar o número incerto.
export function ContaBancos() {
  // Foco relê a conta (como o Perfil): trocar de conta em outra aba muda o uid, e o do cache velho pediria o /open-finance de outra pessoa.
  const conta = useQuery({ ...contaQuery, refetchOnWindowFocus: true });
  const uid = conta.data?.user_id;
  const conexoes = useQuery(conexoesQuery(uid));
  const limite = useQuery(limiteQuery(uid));
  const bancos = useQuery(bancosQuery(uid));
  const qc = useQueryClient();
  const [alvo, setAlvo] = useState<Alvo | null>(null); // diálogo aberto; cobre também o voo e a releitura
  const [aviso, setAviso] = useState("");
  const [falha, setFalha] = useState<{ texto: string; id?: number } | null>(null);
  const [fim, setFim] = useState(false); // 401 no DELETE: a sessão acabou
  const foi = useRef(false); // o card que abriu o diálogo sumiu: o foco vai ao título
  const abriu = useRef<HTMLButtonElement | null>(null);
  useEffect(() => { if (!alvo) abriu.current?.focus(); }, [alvo]); // o botão estava disabled quando o <dialog> fechou: o foco volta só depois

  // `/bancos` e `/limite` são opcionais, menos no 401 (a sessão acabou) e na conta trocada: nos dois, nada do cache fica na tela.
  const erro = [conta.error, conexoes.error].find((e) => e)
    ?? [bancos.error, limite.error].find((e) => (e instanceof ErroApi && e.status === 401) || e instanceof ContaTrocada) ?? null;
  const trocada = bancos.error instanceof ContaTrocada;
  useEffect(() => { if (trocada) conta.refetch(); }, [trocada]); // relê o perfil uma vez por troca
  const lista = erro ? undefined : conexoes.data; // releitura que falhou: o erro com "Tentar de novo", nunca a lista velha
  const semUid = conta.isSuccess && !uidValido(uid);
  const sessao = fim || (erro instanceof ErroApi && erro.status === 401); // sessão acabada esconde o que ficou em cache
  const b = bancos.isError || !bancosValidos(bancos.data) ? undefined : bancos.data; // releitura que falhou ou fora do contrato: sem números, nunca valor velho
  const lim = limite.isError ? undefined : limite.data;
  const porId = new Map((b?.bancos ?? []).map((x) => [x.conexao_id, x]));
  const barra = b && lista ? barraDosBancos(b, lista) : [];
  const topo = b ? Number(b.total) : NaN;
  const vagas = lim && typeof lim.em_uso === "number" && typeof lim.of_banks_max === "number" && lim.of_banks_max > 0 ? `${lim.em_uso} de ${lim.of_banks_max} bancos` : null;
  // Sem vaga: a mensagem do servidor no lugar do link; sem mensagem, nenhum link (não convida a uma ação que o servidor recusa).
  // Com o /limite ainda no ar, nada: o link apareceria e sumiria quando a resposta dissesse que não há vaga.
  const conectar = limite.isLoading ? null : lim?.pode_adicionar !== false ? <a className="btn btn-primary btn-conectar" href={OF}>Conectar banco</a>
    : typeof lim.message === "string" && lim.message.trim() ? <p className="faint">{lim.message}</p> : null;

  const abrir = (c: Conexao, botao: HTMLButtonElement) => {
    if (alvo || !uidValido(uid)) return;
    abriu.current = botao;
    setAviso("");
    setFalha(null);
    setAlvo({ uid, id: c.id, nome: nomeDoBanco(c) });
  };
  // NUNCA lança (o Confirma fecha sempre depois). 401 = fim; 403 = nada apagado e o uid é velho (sem releitura com ele); o resto
  // (404, 402, 503, 5xx, rede, prazo) é ambíguo: relê o snapshot, e só ele diz se o banco saiu.
  const desconectar = async (a: Alvo) => {
    const chave = conexoesQuery(a.uid).queryKey;
    const saiu = () => {
      qc.setQueryData<Conexao[]>(chave, (d) => d && d.filter((c) => c.id !== a.id));
      qc.resetQueries({ queryKey: bancosQuery(a.uid).queryKey }); // o topo e os valores eram dele: reset, não invalidate (invalidate manteria o total velho na tela)
      qc.resetQueries({ queryKey: limiteQuery(a.uid).queryKey });
      qc.invalidateQueries({ queryKey: chave });
      qc.invalidateQueries({ queryKey: contasQuery.queryKey });
      setAviso(`${a.nome} desconectado.`);
      foi.current = true;
    };
    try {
      await desconectarBanco(a.uid, a.id);
      return saiu();
    } catch (e) {
      const status = e instanceof ErroApi ? e.status : 0;
      if (status === 401) { setFim(true); foi.current = true; return; }
      // 403, ou a conta já não é a do clique (foco trocou o perfil): nada apagado e a chave do uid velho está inativa, reler o snapshot dele não diria nada
      if (status === 403 || qc.getQueryData<{ user_id?: number }>(contaQuery.queryKey)?.user_id !== a.uid) { setFalha({ texto: "Não deu para desconectar. Recarregue a página e tente de novo." }); foi.current = true; conta.refetch(); return; }
      const soltou = await comPrazoLeitura(qc.invalidateQueries({ queryKey: chave })); // null = estourou o prazo: o diálogo não fica preso
      if (qc.getQueryState(chave)?.status === "error") { foi.current = true; return; } // a página mostra o próprio erro de carga
      if (soltou !== null && !qc.getQueryData<Conexao[]>(chave)?.some((c) => c.id === a.id)) return saiu();
      setFalha({ texto: (e instanceof ErroApi && e.detalhe) || `Não deu para desconectar ${a.nome}. Tente de novo.`, id: a.id });
    }
  };
  const fechar = () => {
    setAlvo(null);
    if (foi.current || !abriu.current?.isConnected) { foi.current = false; abriu.current = null; document.getElementById("page-title")?.focus(); }
  };
  const falhaVisivel = falha && (falha.id === undefined || lista?.some((c) => c.id === falha.id)) ? falha : null; // falha de um card que sumiu não fica

  return (
    <>
      <header className="page-head">
        <h1 id="page-title" tabIndex={-1}>{route("/conta/bancos").title}</h1>
      </header>
      <div className="page-grid">
        {!lista && !semUid && !erro ? <p role="status" className="faint span-12">Carregando…</p>
          : !lista || sessao ? (
            <section className="panel span-12 empty" role="alert">
              <p>{sessao ? "Sua sessão terminou." : "Não deu para carregar seus bancos."}</p>
              {sessao ? <button type="button" className="btn btn-ghost retry" onClick={() => location.reload()}>Recarregar</button>
                : <button type="button" className="btn btn-ghost retry" onClick={() => { conta.refetch(); if (uidValido(uid)) { conexoes.refetch(); bancos.refetch(); limite.refetch(); } }}>Tentar de novo</button>}
            </section>
          ) : (
            <>
              {b && lista.length > 0 && (
                <section className="panel span-12 bancos-topo" aria-label="Total nos bancos">
                  <p className="faint">Em conta e investido</p>
                  <b className="num bancos-total">{moneyText(b.total) ?? "—"}</b>
                  <Selos motivos={b.motivos} />
                  {barra.length > 0 && (
                    <div className="comp-bar" role="img" aria-label={barra.map((f) => `${f.nome} ${Math.round((Number(f.total) / topo) * 100)}%`).join(", ")}>
                      {barra.map((f) => <span key={f.conexao_id} style={{ flexGrow: Number(f.total) / topo, background: f.cor }} />)}
                    </div>
                  )}
                </section>
              )}
              {vagas && <p className="faint span-12">{vagas}</p>}
              {lista.length === 0 ? (
                <section className="panel span-12 empty">
                  <img src={ICON} alt="" width={48} height={48} />
                  <p>Nenhum banco conectado.</p>
                  {conectar}
                </section>
              ) : (
                <>
                  {falhaVisivel && <p role="alert" className="span-12 warn">{falhaVisivel.texto}</p>}
                  <ul className="span-12 bancos-lista" aria-label="Bancos">
                    {lista.map((c, i) => <BancoCard key={c.id} c={c} i={i} v={bancos.isPending ? "…" : porId.get(c.id) ?? "—"} bloqueado={!!alvo} onDesconectar={(b) => abrir(c, b)} />)}
                  </ul>
                  {conectar && <div className="span-12 bancos-conectar">{conectar}</div>}
                </>
              )}
            </>
          )}
      </div>
      <p role="status" className="sr-only">{aviso}</p>
      {alvo && <Confirma titulo={`Desconectar ${alvo.nome}?`} texto="Os dados importados deste banco serão removidos. Seus outros bancos e lançamentos manuais serão preservados."
        botao="Desconectar" pendente="Desconectando…" cancelar="Manter banco" agir={() => desconectar(alvo)} fechar={fechar} />}
    </>
  );
}
