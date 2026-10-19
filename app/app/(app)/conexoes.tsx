import { router, useFocusEffect, type Href } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, View } from "react-native";
import { RequisicaoSuperada, SessaoExpirada, conferirLinhagem } from "@/api/client";
import type { BancoLido } from "@/api/schemas/painel";
import type { Conector, Conexao } from "@/api/schemas/openFinance";
import { useSessao } from "@/features/auth/sessao";
import { useBloqueio } from "@/features/bloqueio/bloqueio";
import { useForeground } from "@/features/openFinance/useForeground";
import { textoDaFalha } from "@/features/auth/entrar";
import { bancos as lerBancos, conectores as lerConectores, conexoes, desconectarBanco, limiteBancario } from "@/services/openFinance";
import { perfil } from "@/services/auth";
import { lerCredenciais } from "@/storage/secure";
import { usePrivacidade } from "@/features/painel/privacidade";
import { BancoCard } from "@/features/openFinance/conexoes/BancoCard";
import { BancoDetalhe } from "@/features/openFinance/conexoes/BancoDetalhe";
import { disponiveis, juntar } from "@/features/openFinance/conexoes/juntar";
import { ConectarOutro } from "@/features/openFinance/conexoes/ConectarOutro";
import { Vaga } from "@/features/openFinance/conexoes/Vagas";
import { Disponiveis } from "@/features/openFinance/conexoes/Disponiveis";
import { SemBancos } from "@/features/openFinance/conexoes/SemBancos";
import { Banner } from "@/ui/componentes/Banner";
import { Button } from "@/ui/componentes/Button";
import { GrupoDeLinhas } from "@/ui/componentes/ListRow";
import { Screen } from "@/ui/componentes/Screen";
import { Texto } from "@/ui/componentes/Texto";
import { espaco } from "@/ui/tokens";

/** O que `/api/app/bancos` trouxe. `null` = não veio (403, 404, 5xx, rede, abort, schema): a tela segue sem números. */
type Numeros = { total: number | null; bancos: BancoLido[] } | null;
type Dados = { uid: number; lista: Conexao[]; podeAdicionar: boolean; mensagem: string | null; permiteReconectar: boolean; max: number | null; emUso: number; numeros: Numeros;
  /** O catálogo da Pluggy, para os Disponíveis. `undefined` = lendo; `null` = não veio (falha) ou sem vaga (nem foi pedido). */
  conectores: Conector[] | null | undefined };
const NAO_CONECTADO = "Esse banco não está mais conectado.";
export default function Conexoes() {
  const sessao = useSessao();
  const { oculto } = usePrivacidade();
  const travado = useBloqueio().estado.fase === "travado";
  const ativo = useForeground();
  const [dados, setDados] = useState<Dados | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  // A falha de uma ação da FOLHA (remover, Acompanhar, Reconectar) é de uma conexão: só a folha dela a mostra (e a tela, com a folha fechada); `erro` fica para o resto.
  const [falhaFolha, setFalhaFolha] = useState<{ id: number; msg: string } | null>(null);
  const [rodada, setRodada] = useState(0);
  const [carregando, setCarregando] = useState(false);
  const [removendo, setRemovendo] = useState<number | null>(null);
  // O detalhe é uma Folha aberta por ESTADO (sem rota nem link); só guarda o id, e lê tudo de `dados`.
  const [detalheId, setDetalheId] = useState<number | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [sumiuId, setSumiuId] = useState<number | null>(null); // o banco do aviso NAO_CONECTADO: se voltar à lista, o aviso sai
  const remocaoEmVoo = useRef(false);
  const entradaEmVoo = useRef(false);
  const [entrando, setEntrando] = useState(false);
  useFocusEffect(useCallback(() => {
    entradaEmVoo.current = false;
    setEntrando(false);
    return () => { setDetalheId(null); setAviso(null); setFalhaFolha(null); }; // no blur, não no foco: ao voltar a folha não pisca
  }, []));
  const falhou = (msg: string) => (detalheId === null ? setErro(msg) : setFalhaFolha({ id: detalheId, msg }));
  const navegar = (destino: Href) => {
    if (entradaEmVoo.current) return;
    entradaEmVoo.current = true; setEntrando(true);
    try { router.push(destino); }
    catch { entradaEmVoo.current = false; setEntrando(false); falhou("Não conseguimos abrir a tela. Tente de novo."); }
  };
  const abrirAutorizacao = ({ itemId, conector }: { itemId?: string; conector?: number } = {}) => {
    if (entradaEmVoo.current || remocaoEmVoo.current) return;
    entradaEmVoo.current = true; setEntrando(true);
    try { router.push(itemId || conector ? { pathname: "/autorizando", params: itemId ? { itemId } : { conector: String(conector) } } : "/autorizando"); }
    catch { entradaEmVoo.current = false; setEntrando(false); falhou("Não conseguimos abrir a autorização. Tente de novo."); }
  };
  // "há N min" dos cartões: UM relógio para a tela toda, só em primeiro plano; ao voltar, acerta na hora.
  const [agora, setAgora] = useState(Date.now);
  useEffect(() => {
    if (!ativo) return;
    setAgora(Date.now());
    const t = setInterval(() => setAgora(Date.now()), 60_000);
    return () => clearInterval(t);
  }, [ativo]);
  const montado = useRef(true);
  useEffect(() => { montado.current = true; return () => { montado.current = false; }; }, []);
  useFocusEffect(useCallback(() => {
    if (!ativo || travado) return;
    let cancelado = false;
    const controlador = new AbortController();
    setCarregando(true); setErro(null); setFalhaFolha(null);
    void (async () => {
      const esperada = await lerCredenciais();
      const p = await perfil();
      if (p.app_access !== true) throw new Error("Acesso indisponível. Confira sua conta no Início.");
      // Em paralelo e OPCIONAL: qualquer falha vira "sem números" e nunca derruba a tela nem as ações (a promessa nunca rejeita).
      // Só o 401 terminal é dito à sessão (o cliente já limpou o cofre); `expirou` é idempotente, então o `catch` principal pegar o mesmo erro não repete o aviso.
      const numeros: Promise<Numeros> = lerBancos(controlador).then((b) => ({ total: b.total, bancos: b.bancos }), (e: unknown) => {
        if (e instanceof SessaoExpirada && !cancelado) sessao.expirou(e.detalhe);
        return null;
      });
      // A conta trocada ENTRE o /auth/me e estas leituras as faz sair com o token novo e o uid antigo: o 403 de uid alheio vira RequisicaoSuperada.
      const conferir = async (e: unknown): Promise<never> => { if (!(e instanceof SessaoExpirada)) await conferirLinhagem(esperada); throw e; };
      const l = await limiteBancario(p.user_id).catch(conferir);
      // Os Disponíveis só valem com vaga: sem ela o catálogo nem é pedido. Mesma regra do `numeros`: opcional, nunca rejeita, só o 401 terminal chega à sessão.
      const catalogo: Promise<Conector[] | null> | null = l.pode_adicionar ? lerConectores(p.user_id, controlador).then((r) => r.connectors, (e: unknown) => {
        if (e instanceof SessaoExpirada && !cancelado) sessao.expirou(e.detalhe);
        return null;
      }) : null;
      const s = await conexoes(p.user_id, controlador).catch(conferir);
      if (cancelado) return;
      // A lista e as ações aparecem com o snapshot; /bancos (lento ou pendurado) só completa os números depois, sem segurar a tela. `cancelado` descarta resposta de leitura velha.
      setDados({ uid: p.user_id, lista: s.connections, podeAdicionar: l.pode_adicionar,
        mensagem: l.message, permiteReconectar: l.of_banks_max !== 0, max: l.of_banks_max, emUso: l.em_uso, numeros: null, conectores: catalogo ? undefined : null });
      void numeros.then((n) => { if (!cancelado) setDados((d) => d && { ...d, numeros: n }); });
      void catalogo?.then((c) => { if (!cancelado) setDados((d) => d && { ...d, conectores: c }); });
    })().catch((e: unknown) => {
      if (cancelado) return;
      if (e instanceof RequisicaoSuperada) { setDados(null); setDetalheId(null); setAviso(null); setFalhaFolha(null); setRodada((v) => v + 1); return; } // a conta trocou: some o dado da anterior e relê
      if (e instanceof SessaoExpirada) sessao.expirou(e.detalhe);
      else setErro(e instanceof Error ? e.message : textoDaFalha(e));
    }).finally(() => { if (!cancelado) setCarregando(false); });
    return () => { cancelado = true; controlador.abort(); };
  }, [ativo, travado, rodada]));
  const remover = async (c: Conexao) => {
    if (!montado.current || !dados || entradaEmVoo.current || remocaoEmVoo.current) return;
    remocaoEmVoo.current = true; setRemovendo(c.id); setErro(null); setFalhaFolha(null);
    try {
      try { await desconectarBanco(dados.uid, c.id); }
      catch (e) {
        // Resposta perdida não exige segundo DELETE: ausência no servidor confirma.
        if (e instanceof SessaoExpirada || e instanceof RequisicaoSuperada) throw e;
        const s = await conexoes(dados.uid);
        if (s.connections.some((item) => item.id === c.id)) throw e;
      }
      if (montado.current) { setDetalheId((d) => (d === c.id ? null : d)); setAviso(`${c.institution_name ?? "Seu banco"} desconectado.`); setRodada((v) => v + 1); }
    } catch (e) {
      if (!montado.current || e instanceof RequisicaoSuperada) return;
      if (e instanceof SessaoExpirada) sessao.expirou(e.detalhe);
      else setFalhaFolha({ id: c.id, msg: textoDaFalha(e) });
    } finally { remocaoEmVoo.current = false; if (montado.current) setRemovendo(null); }
  };
  const pedirRemocao = (c: Conexao) => {
    if (!montado.current || entradaEmVoo.current || remocaoEmVoo.current) return;
    Alert.alert("Desconectar este banco?",
      "Os dados importados deste banco serão removidos. Seus outros bancos e lançamentos manuais serão preservados.",
      [{ text: "Manter banco", style: "cancel" }, { text: "Desconectar", style: "destructive", onPress: () => { void remover(c); } }]);
  };
  const itens = dados ? juntar(dados.lista, dados.numeros?.bancos ?? []) : [];
  const selIndice = itens.findIndex((i) => i.conexao.id === detalheId);
  const sel = itens[selIndice];
  // O banco sumiu entre a lista e o detalhe (outra sessão o removeu, ou a releitura o tirou): fecha a folha e diz por quê, nunca uma folha vazia.
  useEffect(() => {
    if (detalheId === null || !dados || sel) return;
    setDetalheId(null); setSumiuId(detalheId); setAviso(NAO_CONECTADO);
  }, [detalheId, dados, sel]);
  useEffect(() => {
    if (sumiuId === null || !dados?.lista.some((c) => c.id === sumiuId)) return;
    setSumiuId(null); setAviso((a) => (a === NAO_CONECTADO ? null : a));
  }, [sumiuId, dados]);
  const sugestoes = dados?.conectores ? disponiveis(dados.conectores, dados.lista.map((c) => c.institution_id)) : [];
  const bloqueado = entrando || !dados?.podeAdicionar || removendo !== null;
  return <Screen sobCabecalho onAtualizar={() => setRodada((v) => v + 1)} atualizando={carregando}><View style={{ gap: espaco.lg, paddingVertical: espaco.xl }}>
    {aviso && <Banner tom="info" mensagem={aviso} />}
    {erro && <Banner tom="danger" mensagem={erro} />}
    {falhaFolha && !sel && <Banner tom="danger" mensagem={falhaFolha.msg} />}
    {!dados && !erro && <Texto tom="inkMuted">Conferindo seus bancos…</Texto>}
    {erro && <Button rotulo="Conferir de novo" variante="secondary" onPress={() => setRodada((v) => v + 1)} />}
    {dados?.lista.length === 0 && <SemBancos desativado={entrando || !dados.podeAdicionar} aoConectar={() => abrirAutorizacao()} />}
    {dados && dados.lista.length > 0 && dados.max ? <Vaga vagas={{ max: dados.max, emUso: dados.emUso }} /> : null}
    {dados && dados.lista.length > 0 && <GrupoDeLinhas>{itens.map((item, i) => <BancoCard key={item.conexao.id} item={item} indice={i} aoAbrir={() => { setAviso(null); setDetalheId(item.conexao.id); }} />)}</GrupoDeLinhas>}
    {sel && dados && <BancoDetalhe item={sel} indice={selIndice} numeros={!!dados.numeros} oculto={oculto} agora={agora} podeReconectar={dados.permiteReconectar} entrando={entrando} removendoId={removendo} falha={falhaFolha?.id === sel.conexao.id ? falhaFolha.msg : null}
      fechar={() => setDetalheId(null)}
      aoAcompanhar={() => navegar({ pathname: "/open-finance-volta", params: { itemId: sel.conexao.provider_item_id!, modo: "acompanhar" } })}
      aoReconectar={() => abrirAutorizacao({ itemId: sel.conexao.provider_item_id! })} aoDesconectar={() => pedirRemocao(sel.conexao)} />}
    {dados?.mensagem && <Banner tom="info" mensagem={dados.mensagem} />}
    {sugestoes.length > 0 && <Disponiveis conectores={sugestoes} desativado={bloqueado} aoConectar={(id) => abrirAutorizacao({ conector: id })} aoVerTodos={() => abrirAutorizacao()} />}
    {dados && dados.lista.length > 0 && sugestoes.length === 0 && dados.conectores !== undefined && <ConectarOutro desativado={bloqueado} aoConectar={() => abrirAutorizacao()} />}
  </View></Screen>;
}
