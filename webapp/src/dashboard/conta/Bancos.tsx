import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ErroApi, contaQuery, contasQuery } from "../lib/v2";
import { bancosValidos, disponiveis } from "../lib/bancos";
import { ICON } from "../lib/brand";
import { ATUALIZANDO_SOZINHO, FALHA } from "../lib/atualizarBanco";
import { useAtualizarBanco } from "../parts/usarAtualizarBanco";
import { route } from "../router";
import { ContaTrocada, bancosQuery, comPrazoLeitura, conectoresQuery, conexoesQuery, desconectarBanco, limiteQuery, uidValido, type Conexao } from "./api";
import { BancoCard, SEM_RECONEXAO, nomeDoBanco } from "./BancoCard";
import { BancoDetalhe } from "./BancoDetalhe";
import { Confirma } from "./Confirma";
import { ConectarLink, Disponiveis, seletorOF } from "./Disponiveis";
import { MenuBanco } from "./MenuBanco";
import { iniciar, textoDaFalha, textoDoServidor, type Resultado, type Sessao } from "./reconectar";
import { ResumoBancos } from "./ResumoBancos";
import { SegurancaBancos } from "./SegurancaBancos";

type Alvo = { uid: number; id: number; nome: string };

// Bancos conectados (/conta/bancos): o estado de cada conexão vem do snapshot do Open Finance (`/open-finance/{uid}`, o uid
// só da sessão), os valores de `/api/v2/bancos` (só no detalhe), as vagas de `/limite` e o catálogo de `/connectors` ("Disponíveis para conectar").
// Só o snapshot é obrigatório: sem `/bancos` o detalhe fica sem números; sem `/connectors` a seção some.
// Desconectar um banco (DELETE /open-finance/{uid}/connections/{id}, um por vez; o `uid` é o do clique). Resposta perdida ou erro: a
// verdade é o snapshot relido (sem o id = foi). Plano e tabela de estados: docs/plano-bancos-conectados-painel.md, W2.
// Conectar: o seletor é o módulo legado /open-finance-connect.js (iniciado aqui ao montar, destruído ao desmontar); sem ele o link vai ao /settings.
// Layout em uma coluna de cards (resumo, "Meus bancos conectados", "Disponíveis para conectar", segurança). Em cada banco: "Detalhes" (os valores), "Sincronizar" e o menu "…" (Reconectar, Desconectar).
// "Sincronizar" é o MESMO "Atualizar agora" do aviso do topo (`useAtualizarBanco`, sem o início automático): o servidor só atualiza todos os bancos de uma vez, então os botões das linhas travam juntos.
// Reconectar (W3, `conta/reconectar.ts`): o MESMO `init`; os callbacks do módulo vão à reconexão em curso (`emCurso`) quando há uma, senão ao seletor. O detalhe fecha
// ANTES de o widget abrir (o <dialog> modal, na top layer, o cobriria); o resultado aparece na lista. Uma conexão por vez, contra o Desconectar e o seletor também.
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
  const [falha, setFalha] = useState<{ texto: string; id?: number; uid?: number } | null>(null); // `uid`: a falha de uma RECONEXÃO é da conta do clique e não aparece na conta que a substituir
  const [fim, setFim] = useState(false); // 401 no DELETE: a sessão acabou
  const abriu = useRef<HTMLButtonElement | null>(null);
  const [det, setDet] = useState<number | null>(null); // o banco com o detalhe aberto
  const linha = useRef<HTMLButtonElement | null>(null); // a linha que abriu o detalhe: o foco volta a ela
  const emCurso = useRef<Sessao | null>(null); // a reconexão em curso (síncrona: dois cliques no mesmo tick); vai a null depois da releitura
  const [recon, setRecon] = useState(false); // o mesmo, para pintar: trava as ações do detalhe
  const dialogo = useRef(false); // idem para o diálogo do Desconectar: `alvo` (estado) está velho no mesmo tick; ligada em `abrir`, desligada em `fechar`
  const [menu, setMenu] = useState<number | null>(null); // o banco com o menu "…" aberto: um por vez
  const sync = useAtualizarBanco({ ativo: true, semMotivo: false, dentro: true, automatico: false }); // a rodada do "Sincronizar" (a máquina do "Atualizar agora")
  const sincronizando = sync.maquina.fase === "enviando" || sync.maquina.fase === "acompanhando";
  const montada = useRef(true); // a reconexão de uma montagem antiga não mexe no foco da página nova
  const pendente = useRef<boolean | null>(null); // fim com o widget ainda por cima: o foco volta (à linha, ou ao título se `true`) quando o widget sair (o `onClose` tardio)
  const focar = (titulo: boolean) => { // só refs: o efeito do seletor guarda a 1ª render
    if (!montada.current) return;
    if (!titulo && linha.current?.isConnected) linha.current.focus(); else document.getElementById("page-title")?.focus();
  };
  useEffect(() => { montada.current = true; return () => { montada.current = false; }; }, []);

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
    const viva = () => (emCurso.current && !emCurso.current.acabou() ? emCurso.current : null); // a reconexão que já terminou (releitura em voo) não engole o que vem do seletor
    seletor.init({
      apiBase: location.origin, userId: uid, guardaDuploClique: true,
      onConnectStart: () => { pendente.current = null; if (vivo.on) setErroOF(""); }, // toda conexão nova (seletor ou reconexão) zera o foco que a anterior deixou pendente
      onOpen: () => viva()?.onOpen(),
      onClose: () => { const s = viva(); if (s) s.onClose(); else if (pendente.current !== null) { focar(pendente.current); pendente.current = null; } },
      onConnected: () => { const s = viva(); if (s) return s.onConnected(); mudou(); if (vivo.on) { setErroOF(""); if (pendente.current === null) document.getElementById("page-title")?.focus(); } }, // o "+" que abriu o seletor pode ter saído da lista: o foco vai ao título (com `pendente` o widget de uma reconexão ainda está por cima: o `onClose` dele devolve o foco)
      onError: (msg: string, status?: number) => { const s = viva(); if (s) s.onError(msg, status); else if (vivo.on) setErroOF((status === undefined ? msg : textoDoServidor({ status, msg })) || "Não foi possível conectar o banco."); }, // com status o texto é o do servidor: só o de pessoa (a mesma regra da reconexão); sem status é do próprio módulo
      notify: (msg: string, tipo: string) => { if (vivo.on) (tipo === "error" ? setErroOF(msg) : setAviso(msg)); }, // o "ok" da reconexão é coberto pelo aviso do `terminou`, que vem depois da releitura
    });
    return () => {
      vivo.on = false;
      setAviso(""); setErroOF(""); pendente.current = null; // o que era da conta (ou da tela) que saiu não fica no status da nova (a `falha` fica: o alerta do 403 do Desconectar nasce da troca de conta; a de reconexão tem `uid` e some sozinha na conta nova)
      if (seletor.busy()) return;
      seletor.destroy(); // aborta o connect-token em voo: a reconexão que ainda não abriu o widget não chega ao fim
      emCurso.current?.cancelar();
      emCurso.current = null;
      setRecon(false);
    };
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
  // O gesto de abrir o seletor ("Conectar banco", "Ver todos", o "Conectar" de cada disponível): sai o toast da tentativa anterior e o foco que uma reconexão deixou pendente (o `onClose`
  // tardio dela não pode mover o foco para a linha atrás do overlay do seletor). O `onConnectStart` repete o zeramento para quem não passa por aqui.
  const aoAbrirSeletor = () => { setErroOF(""); pendente.current = null; };
  const conectar = pode ? <ConectarLink className="btn btn-primary banco-conectar" aoAbrir={aoAbrirSeletor}><i className="ph ph-plus" aria-hidden="true" />Conectar banco</ConectarLink> : avisoVaga;
  // Catálogo opcional: só lê com vaga e a lista na tela; falha = sem seção, nunca erro de página. "Já conectado" é por id (institution_id × id do conector).
  const mostra = pode && !!lista && !sessao && !erro;
  const catalogo = useQuery(conectoresQuery(uid, mostra));
  const lojas = mostra && !catalogo.isError ? disponiveis(catalogo.data ?? [], (lista ?? []).map((c) => c.institution_id)) : [];
  const vDe = (c: Conexao) => (bancos.isPending ? "…" : porId.get(c.id) ?? "—");
  const iDet = lista && !sessao ? lista.findIndex((c) => c.id === det) : -1;
  const sel = iDet >= 0 && lista ? lista[iDet] : undefined; // a conexão que sai (desconectada, ou lista nova) leva o detalhe junto
  useEffect(() => { if (det !== null && !sel) { setDet(null); document.getElementById("page-title")?.focus(); } }, [det, sel]); // sem Confirma aberto o foco ia ao body (com ele, o título está inerte e o efeito de baixo cuida)
  // Fechado o Confirma: o botão que o abriu (o do detalhe, ou o "…" da linha) segue na tela e recebe o foco de volta (estava disabled quando o <dialog> fechou); o banco/a página saiu: o foco vai ao título.
  // Só consome `abriu` (setado em `abrir`): sem diálogo aberto por nós, a página não rouba o foco ao carregar nem o X do detalhe perde o foco num refetch.
  useEffect(() => {
    if (alvo || !abriu.current) return;
    if (abriu.current.isConnected) abriu.current.focus(); else document.getElementById("page-title")?.focus();
    abriu.current = null;
  }, [alvo, !!sel]);
  useEffect(() => { if (menu !== null && !lista?.some((c) => c.id === menu)) { setMenu(null); document.getElementById("page-title")?.focus(); } }, [menu, lista]); // o banco do menu aberto saiu do snapshot: o menu não reabre sozinho quando ele voltar
  const fecharDet = () => { setDet(null); if (linha.current?.isConnected) linha.current.focus(); }; // o Safari não foca o botão ao clicar: o foco volta explícito

  const abrir = (c: Conexao, botao: HTMLButtonElement) => {
    if (alvo || emCurso.current || sync.emVoo() || !uidValido(uid)) return;
    dialogo.current = true;
    abriu.current = botao;
    sync.calar(); // o anúncio da rodada anterior do "Sincronizar" sai da região viva
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
  const fechar = () => { dialogo.current = false; setAlvo(null); };
  // Fim da reconexão (NUNCA lança). 401 = sessão; 403 = o uid é velho (nada de reler com ele); a conta trocada no meio cai no mesmo ramo (o POST do save vai à conta do clique e,
  // com a sessão já de outra conta, o servidor o recusa; aqui nada é dito sobre a nova). Prazo/"ocupado": o servidor nem viu, não relê. O resto relê as leituras e só então libera os botões.
  // `semFoco`: o widget segue aberto por cima da página, o foco fica onde está e volta no `onClose` tardio do widget (`pendente`). O foco volta à linha; ao título se o banco sumiu, 401 ou 403.
  const terminou = async (a: Alvo, r: Resultado, semFoco: boolean, eu: Sessao) => {
    const contaMudou = () => qc.getQueryData<{ user_id?: number }>(contaQuery.queryKey)?.user_id !== a.uid;
    const status = r && !r.ok ? r.status : undefined;
    const trocada = contaMudou();
    let titulo = status === 401 || status === 403;
    if (semFoco && !trocada) pendente.current = titulo; // (só se a conta ainda é a do clique: na nova o foco não é nosso) antes do await: o `onClose` tardio pode chegar durante a releitura
    if (status === 401) setFim(true);
    if (status === 403 || trocada) conta.refetch();
    else if (!titulo && !(r && !r.ok && r.tipo)) {
      const chave = conexoesQuery(a.uid).queryKey;
      [bancosQuery, limiteQuery].forEach((q) => qc.invalidateQueries({ queryKey: q(a.uid).queryKey }));
      qc.invalidateQueries({ queryKey: contasQuery.queryKey });
      await comPrazoLeitura(qc.invalidateQueries({ queryKey: chave }));
      titulo = !qc.getQueryData<Conexao[]>(chave)?.some((c) => c.id === a.id); // o banco sumiu na releitura
    }
    if (!contaMudou() && montada.current) { // a releitura pode ter durado: com a conta trocada (ou a página fora) nada é dito, e o foco não é tocado
      setAviso(r?.ok ? `${a.nome} reconectado.` : ""); // sai o "Reconectando…"
      if (r && !r.ok && status !== 401) { const f = textoDaFalha(r, a.nome); setFalha({ texto: f.texto, id: f.semId ? undefined : a.id, uid: a.uid }); }
      if (!semFoco) focar(titulo);
    }
    if (emCurso.current === eu) { emCurso.current = null; setRecon(false); } // defensiva: a releitura desta sessão não solta a trava de uma reconexão mais nova (a da conta nova)
  };
  // `botao`: o "…" da linha quando vem do menu (o foco volta a ele); do detalhe, `linha` já é o botão que o abriu.
  const reconectarBanco = (c: Conexao, botao?: HTMLButtonElement) => {
    const seletor = seletorOF();
    if (alvo || dialogo.current || emCurso.current || sync.emVoo() || !seletor || !uidValido(uid) || !c.provider_item_id) return;
    if (botao) linha.current = botao;
    const a = { uid, id: c.id, nome: nomeDoBanco(c) };
    const s = emCurso.current = iniciar((r, semFoco) => { terminou(a, r, semFoco, s); }, () => seletor.destroy());
    setRecon(true);
    sync.calar();
    setAviso(`Reconectando ${a.nome}…`);
    setFalha(null);
    fecharDet(); // antes do widget: o <dialog> modal o cobriria
    if (seletor.reconnect(c.provider_item_id) === false) s.ocupado(); // há conexão de outro mount ou do seletor: ela segue, e o save dela também
  };
  // Reconectar não gasta vaga (P1 do `/limite`): a regra é a do app (`provider_item_id`, `of_banks_max !== 0`, estado fora de SEM_RECONEXAO), e o módulo tem de estar carregado.
  const reconectavel = (c: Conexao) => !!c.provider_item_id && lim?.of_banks_max !== 0 && !SEM_RECONEXAO.includes(c.ui.state) && !!seletorOF();
  const falhaVisivel = falha && (falha.uid === undefined || falha.uid === uid) && (falha.id === undefined || lista?.some((c) => c.id === falha.id)) ? falha : null; // falha de um card que sumiu não fica; a com id só aparece no detalhe daquele banco (ou acima da lista, sem detalhe aberto), a sem id em qualquer um

  // Os refs, não o estado (velho no mesmo tick): reconexão, diálogo do Desconectar e seletor ocupado não andam junto com a rodada (o inverso está em `abrir` e `reconectarBanco`).
  // Não zera o `aviso` (é de outra ação, p. ex. "X desconectado."): a região viva mostra os dois.
  const sincronizar = () => { if (alvo || dialogo.current || emCurso.current || seletorOF()?.busy()) return; sync.atualizar(); };
  const m = sync.maquina; // o que a rodada do "Sincronizar" diz na tela (o mesmo texto vai à região viva, `sync.dito`): sucesso é calado, a lista nova é o aviso
  const textoSync = m.fase === "acompanhando" ? ATUALIZANDO_SOZINHO : m.fase === "resultado" ? m.texto : m.fase === "ocioso" && m.falha ? FALHA : "";
  useEffect(() => { if (!sincronizando && !textoSync) sync.calar(); }, [sincronizando, textoSync]); // fim da rodada sem texto próprio (sucesso, `rate_limited`): "Atualizando os dados do banco" não fica na região viva
  const idMeus = "bancos-meus";

  return (
    <>
      <header className="page-head">
        <h1 id="page-title" tabIndex={-1}>{route("/conta/bancos").title}</h1>
        <p className="page-lede">Gerencie suas conexões via Open Finance.</p>
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
            <div className="span-12 bancos-pag">
              <ResumoBancos lista={lista} vagas={vagas} conectar={conectar} />
              {falhaVisivel && !sel && lista.length > 0 && <p role="alert" className="warn">{falhaVisivel.texto}</p>}
              {lista.length === 0 ? (
                <section className="panel empty">
                  <img src={ICON} alt="" width={48} height={48} />
                  <p>Nenhum banco conectado.</p>
                </section>
              ) : (
                <section className="panel banco-sec" aria-labelledby={idMeus}>
                  <header className="banco-sec-cab"><h2 id={idMeus}>Meus bancos conectados</h2><p>Bancos que compartilham seus dados via Open Finance.</p></header>
                  {textoSync && <p className={`banco-sync${m.fase === "acompanhando" ? "" : " warn"}`}>{textoSync}</p>}
                  <ul className="banco-lista" aria-label="Bancos">
                    {lista.map((c, i) => (
                      <BancoCard key={c.id} c={c} i={i} sincronizando={sincronizando} bloqueado={!!alvo || recon} onSincronizar={sincronizar}
                        onAbrir={(b) => { if (alvo || emCurso.current) return; setMenu(null); linha.current = b; setDet(c.id); }}
                        menu={<MenuBanco nome={nomeDoBanco(c)} aberto={menu === c.id} bloqueado={!!alvo || recon || sincronizando} alternar={() => setMenu(menu === c.id ? null : c.id)} fechar={() => setMenu(null)}
                          onReconectar={reconectavel(c) ? (b) => reconectarBanco(c, b) : undefined} onDesconectar={(b) => abrir(c, b)} />} />
                    ))}
                  </ul>
                </section>
              )}
              <Disponiveis lista={lojas} aoAbrir={aoAbrirSeletor} />
              <SegurancaBancos />
            </div>
          )}
      </div>
      <p role="status" className="sr-only">{aviso}{sync.dito.t && <span key={sync.dito.n}>{sync.dito.t}</span>}</p>
      {erroOF && createPortal(<div role="alert" className="banco-toast"><span>{erroOF}</span><button type="button" aria-label="Dispensar aviso" onClick={() => setErroOF("")}><i className="ph ph-x" aria-hidden="true" /></button></div>, document.body)}
      {sel && <BancoDetalhe c={sel} i={iDet} v={vDe(sel)} bloqueado={!!alvo || recon} sync={sincronizando} falha={falhaVisivel && (falhaVisivel.id === undefined || falhaVisivel.id === sel.id) ? falhaVisivel.texto : undefined} onDesconectar={(b) => abrir(sel, b)} onReconectar={reconectavel(sel) ? () => reconectarBanco(sel) : undefined} fechar={fecharDet} />}
      {alvo && <Confirma titulo={`Desconectar ${alvo.nome}?`} texto="Os dados importados deste banco serão removidos. Seus outros bancos e lançamentos manuais serão preservados."
        botao="Desconectar" pendente="Desconectando…" cancelar="Manter banco" agir={() => desconectar(alvo)} fechar={fechar} />}
    </>
  );
}
