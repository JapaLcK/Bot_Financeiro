import { router, useFocusEffect, type Href } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, View } from "react-native";
import { RequisicaoSuperada, SessaoExpirada } from "@/api/client";
import type { BancoLido } from "@/api/schemas/painel";
import type { Conexao } from "@/api/schemas/openFinance";
import { useSessao } from "@/features/auth/sessao";
import { useBloqueio } from "@/features/bloqueio/bloqueio";
import { useForeground } from "@/features/openFinance/useForeground";
import { textoDaFalha } from "@/features/auth/entrar";
import { bancos as lerBancos, conexoes, desconectarBanco, limiteBancario } from "@/services/openFinance";
import { perfil } from "@/services/auth";
import { corDoBanco } from "@/features/painel/composicaoContas";
import { usePrivacidade } from "@/features/painel/privacidade";
import { BancoCard } from "@/features/openFinance/conexoes/BancoCard";
import { juntar, panorama } from "@/features/openFinance/conexoes/juntar";
import { ConectarOutro } from "@/features/openFinance/conexoes/ConectarOutro";
import { ResumoBancos, type Parte } from "@/features/openFinance/conexoes/ResumoBancos";
import { SemBancos } from "@/features/openFinance/conexoes/SemBancos";
import { Banner } from "@/ui/componentes/Banner";
import { Button } from "@/ui/componentes/Button";
import { Screen } from "@/ui/componentes/Screen";
import { Texto } from "@/ui/componentes/Texto";
import { useTema } from "@/ui/tema";
import { espaco } from "@/ui/tokens";

/** O que `/api/app/bancos` trouxe. `null` = não veio (403, 404, 5xx, rede, abort, schema): a tela segue sem números. */
type Numeros = { total: number | null; bancos: BancoLido[] } | null;
type Dados = { uid: number; lista: Conexao[]; podeAdicionar: boolean; mensagem: string | null; permiteReconectar: boolean; max: number | null; emUso: number; numeros: Numeros };
export default function Conexoes() {
  const sessao = useSessao();
  const { cores } = useTema();
  const { oculto } = usePrivacidade();
  const travado = useBloqueio().estado.fase === "travado";
  const ativo = useForeground();
  const [dados, setDados] = useState<Dados | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [rodada, setRodada] = useState(0);
  const [carregando, setCarregando] = useState(false);
  const [removendo, setRemovendo] = useState<number | null>(null);
  const [abertos, setAbertos] = useState<Record<number, boolean>>({});
  const remocaoEmVoo = useRef(false);
  const entradaEmVoo = useRef(false);
  const [entrando, setEntrando] = useState(false);
  useFocusEffect(useCallback(() => {
    entradaEmVoo.current = false;
    setEntrando(false);
  }, []));
  const navegar = (destino: Href) => {
    if (entradaEmVoo.current) return;
    entradaEmVoo.current = true; setEntrando(true);
    try { router.push(destino); }
    catch { entradaEmVoo.current = false; setEntrando(false); setErro("Não conseguimos abrir a tela. Tente de novo."); }
  };
  const abrirAutorizacao = (itemId?: string) => {
    if (entradaEmVoo.current || remocaoEmVoo.current) return;
    entradaEmVoo.current = true; setEntrando(true);
    try { router.push(itemId ? { pathname: "/autorizando", params: { itemId } } : "/autorizando"); }
    catch { entradaEmVoo.current = false; setEntrando(false); setErro("Não conseguimos abrir a autorização. Tente de novo."); }
  };
  const montado = useRef(true);
  useEffect(() => { montado.current = true; return () => { montado.current = false; }; }, []);
  useFocusEffect(useCallback(() => {
    if (!ativo || travado) return;
    let cancelado = false;
    const controlador = new AbortController();
    setCarregando(true); setErro(null);
    void (async () => {
      const p = await perfil();
      if (p.app_access !== true) throw new Error("Acesso indisponível. Confira sua conta no Início.");
      // Em paralelo e OPCIONAL: qualquer falha vira "sem números" e nunca derruba a tela nem as ações (a promessa nunca rejeita).
      const numeros: Promise<Numeros> = lerBancos(controlador).then((b) => ({ total: b.total, bancos: b.bancos }), () => null);
      const l = await limiteBancario(p.user_id);
      const s = await conexoes(p.user_id, controlador);
      if (cancelado) return;
      // A lista e as ações aparecem com o snapshot; /bancos (lento ou pendurado) só completa os números depois, sem segurar a tela. `cancelado` descarta resposta de leitura velha.
      setDados({ uid: p.user_id, lista: s.connections, podeAdicionar: l.pode_adicionar,
        mensagem: l.message, permiteReconectar: l.of_banks_max !== 0, max: l.of_banks_max, emUso: l.em_uso, numeros: null });
      void numeros.then((n) => { if (!cancelado) setDados((d) => d && { ...d, numeros: n }); });
    })().catch((e: unknown) => {
      if (cancelado || e instanceof RequisicaoSuperada) return;
      if (e instanceof SessaoExpirada) sessao.expirou(e.detalhe);
      else setErro(e instanceof Error ? e.message : textoDaFalha(e));
    }).finally(() => { if (!cancelado) setCarregando(false); });
    return () => { cancelado = true; controlador.abort(); };
  }, [ativo, travado, rodada]));
  const remover = async (c: Conexao) => {
    if (!montado.current || !dados || entradaEmVoo.current || remocaoEmVoo.current) return;
    remocaoEmVoo.current = true; setRemovendo(c.id); setErro(null);
    try {
      try { await desconectarBanco(dados.uid, c.id); }
      catch (e) {
        // Resposta perdida não exige segundo DELETE: ausência no servidor confirma.
        if (e instanceof SessaoExpirada || e instanceof RequisicaoSuperada) throw e;
        const s = await conexoes(dados.uid);
        if (s.connections.some((item) => item.id === c.id)) throw e;
      }
      if (montado.current) setRodada((v) => v + 1);
    } catch (e) {
      if (!montado.current || e instanceof RequisicaoSuperada) return;
      if (e instanceof SessaoExpirada) sessao.expirou(e.detalhe);
      else setErro(textoDaFalha(e));
    } finally { remocaoEmVoo.current = false; if (montado.current) setRemovendo(null); }
  };
  const pedirRemocao = (c: Conexao) => {
    if (!montado.current || entradaEmVoo.current || remocaoEmVoo.current) return;
    Alert.alert("Desconectar este banco?",
      "Os dados importados deste banco serão removidos. Seus outros bancos e lançamentos manuais serão preservados.",
      [{ text: "Manter banco", style: "cancel" }, { text: "Desconectar", style: "destructive", onPress: () => { void remover(c); } }]);
  };
  const itens = dados ? juntar(dados.lista, dados.numeros?.bancos ?? []) : [];
  const partes: Parte[] = itens.flatMap(({ conexao, banco }, i) => banco?.total && banco.total > 0 ? [{ nome: conexao.institution_name ?? "Seu banco", valor: banco.total, cor: corDoBanco(conexao.institution_name ?? "", i, cores) }] : []);
  const bloqueado = entrando || !dados?.podeAdicionar || removendo !== null;
  return <Screen sobCabecalho onAtualizar={() => setRodada((v) => v + 1)} atualizando={carregando}><View style={{ gap: espaco.lg, paddingVertical: espaco.xl }}>
    {erro && <Banner tom="danger" mensagem={erro} />}
    {!dados && !erro && <Texto tom="inkMuted">Conferindo seus bancos…</Texto>}
    {erro && !(dados && dados.lista.length > 0) && <Button rotulo="Conferir de novo" variante="secondary" onPress={() => setRodada((v) => v + 1)} />}
    {dados?.lista.length === 0 && <SemBancos desativado={entrando || !dados.podeAdicionar} aoConectar={() => abrirAutorizacao()} />}
    {dados && dados.lista.length > 0 && <ResumoBancos numeros={!!dados.numeros} total={dados.numeros?.total ?? null} partes={partes} panorama={panorama(dados.lista)} vagas={{ max: dados.max, emUso: dados.emUso }} oculto={oculto} aoConferir={() => setRodada((v) => v + 1)} />}
    {dados && itens.map(({ conexao: c, banco }, i) => <BancoCard key={c.id} item={{ conexao: c, banco }} numeros={!!dados.numeros} oculto={oculto} indice={i}
      aberto={abertos[c.id] ?? false} alternar={() => setAbertos((v) => ({ ...v, [c.id]: !v[c.id] }))} podeReconectar={dados.permiteReconectar} entrando={entrando} removendoId={removendo}
      aoAcompanhar={() => navegar({ pathname: "/open-finance-volta", params: { itemId: c.provider_item_id!, modo: "acompanhar" } })}
      aoReconectar={() => abrirAutorizacao(c.provider_item_id!)} aoDesconectar={() => pedirRemocao(c)} />)}
    {dados?.mensagem && <Banner tom="info" mensagem={dados.mensagem} />}
    {(!dados || dados.lista.length > 0) && <ConectarOutro desativado={bloqueado} aoConectar={() => abrirAutorizacao()} />}
  </View></Screen>;
}
