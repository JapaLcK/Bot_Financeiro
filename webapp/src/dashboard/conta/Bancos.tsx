import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ErroApi, contaQuery, contasQuery } from "../lib/v2";
import { bancosValidos, disponiveis } from "../lib/bancos";
import { ICON } from "../lib/brand";
import { route } from "../router";
import { ContaTrocada, bancosQuery, comPrazoLeitura, conectoresQuery, conexoesQuery, desconectarBanco, limiteQuery, uidValido, type Conexao } from "./api";
import { BancoCard, nomeDoBanco } from "./BancoCard";
import { BancoDetalhe } from "./BancoDetalhe";
import { Confirma } from "./Confirma";
import { ConectarLink, Disponiveis, seletorOF } from "./Disponiveis";

type Alvo = { uid: number; id: number; nome: string };

// Bancos conectados (/conta/bancos): o estado de cada conexão vem do snapshot do Open Finance (`/open-finance/{uid}`, o uid
// só da sessão), os valores de `/api/v2/bancos` (só no detalhe), as vagas de `/limite` e o catálogo de `/connectors` ("Disponíveis para conectar").
// Só o snapshot é obrigatório: sem `/bancos` o detalhe fica sem números; sem `/connectors` a seção some.
// Desconectar um banco (DELETE /open-finance/{uid}/connections/{id}, um por vez; o `uid` é o do clique). Resposta perdida ou erro: a
// verdade é o snapshot relido (sem o id = foi). Plano e tabela de estados: docs/plano-bancos-conectados-painel.md, W2.
// Conectar: o seletor é o módulo legado /open-finance-connect.js (iniciado aqui ao montar, destruído ao desmontar); sem ele o link vai ao /settings.
export function ContaBancos() {
  // Foco relê a conta (como o Perfil): trocar de conta em outra aba muda o uid, e o do cache velho pediria o /open-finance de outra pessoa.
  const conta = useQuery({ ...contaQuery, refetchOnWindowFocus: true });
  const uid = conta.data?.user_id;
  const conexoes = useQuery(conexoesQuery(uid));
  const limite = useQuery(limiteQuery(uid));
  const bancos = useQuery(bancosQuery(uid));
  const [erroOF, setErroOF] = useState(""); // o que o seletor/widget avisa (teto do plano, falha ao salvar): visível, não só no leitor de tela
  const qc = useQueryClient();
  const [alvo, setAlvo] = useState<Alvo | null>(null); // diálogo aberto; cobre também o voo e a releitura
  const [aviso, setAviso] = useState("");
  const [falha, setFalha] = useState<{ texto: string; id?: number } | null>(null);
  const [fim, setFim] = useState(false); // 401 no DELETE: a sessão acabou
  const abriu = useRef<HTMLButtonElement | null>(null);
  const [det, setDet] = useState<number | null>(null); // o banco com o detalhe aberto
  const linha = useRef<HTMLButtonElement | null>(null); // a linha que abriu o detalhe: o foco volta a ela

  // `/bancos` e `/limite` são opcionais, menos no 401 (a sessão acabou) e na conta trocada: nos dois, nada do cache fica na tela.
  const erro = [conta.error, conexoes.error].find((e) => e)
    ?? [bancos.error, limite.error].find((e) => (e instanceof ErroApi && e.status === 401) || e instanceof ContaTrocada) ?? null;
  const trocada = bancos.error instanceof ContaTrocada;
  useEffect(() => { if (trocada) conta.refetch(); }, [trocada]); // relê o perfil uma vez por troca
  useEffect(() => {
    const seletor = seletorOF();
    if (!seletor || !uidValido(uid)) return;
    const mudou = () => [conexoesQuery(uid), limiteQuery(uid), bancosQuery(uid), conectoresQuery(uid), contasQuery].forEach((q) => qc.invalidateQueries({ queryKey: q.queryKey }));
    // Conexão em curso quando a página sai (voltar do navegador com o widget aberto): o item que a Pluggy autorizar DEPOIS ainda tem de ser salvo,
    // então o `destroy()` (que descarta os callbacks do widget) só vem sem conexão em curso. Os callbacks daquela conexão seguem valendo,
    // sem mexer em estado da página desmontada (`vivo`); a releitura é do cache global e vale.
    const vivo = { on: true };
    seletor.init({
      apiBase: location.origin, userId: uid, guardaDuploClique: true,
      onConnectStart: () => { if (vivo.on) setErroOF(""); },
      onConnected: () => { mudou(); if (vivo.on) { setErroOF(""); document.getElementById("page-title")?.focus(); } }, // o "+" que abriu o seletor pode ter saído da lista: o foco vai ao título
      onError: (msg: string) => { if (vivo.on) setErroOF(msg || "Não foi possível conectar o banco."); },
      notify: (msg: string, tipo: string) => { if (vivo.on) (tipo === "error" ? setErroOF(msg) : setAviso(msg)); },
    });
    return () => { vivo.on = false; if (!seletor.busy()) seletor.destroy(); };
  }, [uid]);
  useEffect(() => { // o aviso do seletor some sozinho (ou ao tocar nele)
    if (!erroOF) return;
    const t = setTimeout(() => setErroOF(""), 8000);
    return () => clearTimeout(t);
  }, [erroOF]);
  const lista = erro ? undefined : conexoes.data; // releitura que falhou: o erro com "Tentar de novo", nunca a lista velha
  const semUid = conta.isSuccess && !uidValido(uid);
  const sessao = fim || (erro instanceof ErroApi && erro.status === 401); // sessão acabada esconde o que ficou em cache
  const b = bancos.isError || !bancosValidos(bancos.data) ? undefined : bancos.data; // releitura que falhou ou fora do contrato: sem números, nunca valor velho
  const lim = limite.isError ? undefined : limite.data;
  const porId = new Map((b?.bancos ?? []).map((x) => [x.conexao_id, x]));
  const vagas = lim && typeof lim.em_uso === "number" && typeof lim.of_banks_max === "number" && lim.of_banks_max > 0 ? `${lim.em_uso} de ${lim.of_banks_max} bancos` : null;
  // Sem vaga: a mensagem do servidor no lugar do link; sem mensagem, nenhum link (não convida a uma ação que o servidor recusa).
  // Com o /limite ainda sem resposta, nada: o link (e o "+") apareceria e sumiria quando ela dissesse que não há vaga. Na 1ª falha
  // eles voltam (o /limite é opcional), sem esperar as novas tentativas.
  const pode = !(limite.isLoading && limite.failureCount === 0) && lim?.pode_adicionar !== false;
  const avisoVaga = !pode && typeof lim?.message === "string" && lim.message.trim() ? <p className="faint">{lim.message}</p> : null;
  const conectar = pode ? <ConectarLink className="btn btn-primary btn-conectar" aoAbrir={() => setErroOF("")}>Conectar banco</ConectarLink> : avisoVaga;
  // Catálogo opcional: só lê com vaga e a lista na tela; falha = sem seção, nunca erro de página. "Já conectado" é por id (institution_id × id do conector).
  const mostra = pode && !!lista && !sessao && !erro;
  const catalogo = useQuery(conectoresQuery(uid, mostra));
  const lojas = mostra && !catalogo.isError ? disponiveis(catalogo.data ?? [], (lista ?? []).map((c) => c.institution_id)) : [];
  const vDe = (c: Conexao) => (bancos.isPending ? "…" : porId.get(c.id) ?? "—");
  const iDet = lista && !sessao ? lista.findIndex((c) => c.id === det) : -1;
  const sel = iDet >= 0 && lista ? lista[iDet] : undefined; // a conexão que sai (desconectada, ou lista nova) leva o detalhe junto
  useEffect(() => { if (det !== null && !sel) { setDet(null); document.getElementById("page-title")?.focus(); } }, [det, sel]); // sem Confirma aberto o foco ia ao body (com ele, o título está inerte e o efeito de baixo cuida)
  // Fechado o Confirma: o detalhe segue aberto (o foco volta ao botão, que estava disabled quando o <dialog> fechou) ou o banco/a página saiu (o foco vai ao título).
  // Só consome `abriu` (setado em `abrir`): sem diálogo aberto por nós, a página não rouba o foco ao carregar nem o X do detalhe perde o foco num refetch.
  useEffect(() => {
    if (alvo || !abriu.current) return;
    if (sel) abriu.current.focus(); else document.getElementById("page-title")?.focus();
    abriu.current = null;
  }, [alvo, !!sel]);
  const fecharDet = () => { setDet(null); if (linha.current?.isConnected) linha.current.focus(); }; // o Safari não foca o botão ao clicar: o foco volta explícito

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
    };
    try {
      await desconectarBanco(a.uid, a.id);
      return saiu();
    } catch (e) {
      const status = e instanceof ErroApi ? e.status : 0;
      if (status === 401) { setFim(true); return; }
      // 403, ou a conta já não é a do clique (foco trocou o perfil): nada apagado e a chave do uid velho está inativa, reler o snapshot dele não diria nada
      if (status === 403 || qc.getQueryData<{ user_id?: number }>(contaQuery.queryKey)?.user_id !== a.uid) { setFalha({ texto: "Não deu para desconectar. Recarregue a página e tente de novo." }); conta.refetch(); return; }
      const soltou = await comPrazoLeitura(qc.invalidateQueries({ queryKey: chave })); // null = estourou o prazo: o diálogo não fica preso
      if (qc.getQueryState(chave)?.status === "error") return; // a página mostra o próprio erro de carga
      if (soltou !== null && !qc.getQueryData<Conexao[]>(chave)?.some((c) => c.id === a.id)) return saiu();
      setFalha({ texto: (e instanceof ErroApi && e.detalhe) || `Não deu para desconectar ${a.nome}. Tente de novo.`, id: a.id });
    }
  };
  const fechar = () => setAlvo(null);
  const falhaVisivel = falha && (falha.id === undefined || lista?.some((c) => c.id === falha.id)) ? falha : null; // falha de um card que sumiu não fica; a com id só aparece no detalhe daquele banco (ou acima da lista, sem detalhe aberto), a sem id em qualquer um

  return (
    <>
      <header className="page-head bancos-cab">
        <div className="bancos-tit">
          <h1 id="page-title" tabIndex={-1}>{route("/conta/bancos").title}</h1>
          <p className="page-lede">Gerencie as conexões via Open Finance.</p>
        </div>
        {pode && lista && lista.length > 0 && !sessao && <ConectarLink className="banco-mais" label="Conectar banco" aoAbrir={() => setErroOF("")}><i className="ph ph-plus" aria-hidden="true" /></ConectarLink>}
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
              {vagas && <p className="faint span-12">{vagas}</p>}
              {lista.length === 0 ? (
                <section className="panel span-12 empty">
                  <img src={ICON} alt="" width={48} height={48} />
                  <p>Nenhum banco conectado.</p>
                  {conectar}
                </section>
              ) : (
                <>
                  {falhaVisivel && !sel && <p role="alert" className="span-12 warn">{falhaVisivel.texto}</p>}
                  <ul className="span-12 bancos-col banco-grupo" aria-label="Bancos">
                    {lista.map((c, i) => <BancoCard key={c.id} c={c} i={i} onAbrir={(b) => { if (alvo) return; linha.current = b; setDet(c.id); }} />)}
                  </ul>
                  {avisoVaga && <div className="span-12 bancos-conectar">{avisoVaga}</div>}
                </>
              )}
              <Disponiveis lista={lojas} aoAbrir={() => setErroOF("")} />
            </>
          )}
      </div>
      <p role="status" className="sr-only">{aviso}</p>
      {erroOF && createPortal(<div role="alert" className="banco-toast"><span>{erroOF}</span><button type="button" aria-label="Dispensar aviso" onClick={() => setErroOF("")}><i className="ph ph-x" aria-hidden="true" /></button></div>, document.body)}
      {sel && <BancoDetalhe c={sel} i={iDet} v={vDe(sel)} bloqueado={!!alvo} falha={falhaVisivel && (falhaVisivel.id === undefined || falhaVisivel.id === sel.id) ? falhaVisivel.texto : undefined} onDesconectar={(b) => abrir(sel, b)} fechar={fecharDet} />}
      {alvo && <Confirma titulo={`Desconectar ${alvo.nome}?`} texto="Os dados importados deste banco serão removidos. Seus outros bancos e lançamentos manuais serão preservados."
        botao="Desconectar" pendente="Desconectando…" cancelar="Manter banco" agir={() => desconectar(alvo)} fechar={fechar} />}
    </>
  );
}
