import { router, useFocusEffect, type Href } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, View } from "react-native";
import { RequisicaoSuperada, SessaoExpirada } from "@/api/client";
import type { Conexao } from "@/api/schemas/openFinance";
import { useSessao } from "@/features/auth/sessao";
import { useBloqueio } from "@/features/bloqueio/bloqueio";
import { useForeground } from "@/features/openFinance/useForeground";
import { textoDaFalha } from "@/features/auth/entrar";
import { conexoes, desconectarBanco, limiteBancario } from "@/services/openFinance";
import { perfil } from "@/services/auth";
import { Button } from "@/ui/componentes/Button";
import { Banner } from "@/ui/componentes/Banner";
import { Card } from "@/ui/componentes/Card";
import { ConnectionStatus } from "@/ui/componentes/ConnectionStatus";
import { Screen } from "@/ui/componentes/Screen";
import { Texto } from "@/ui/componentes/Texto";
import { espaco } from "@/ui/tokens";

type Dados = { uid: number; lista: Conexao[]; podeAdicionar: boolean; mensagem: string | null; permiteReconectar: boolean };
export default function Conexoes() {
  const sessao = useSessao();
  const travado = useBloqueio().estado.fase === "travado";
  const ativo = useForeground();
  const [dados, setDados] = useState<Dados | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [rodada, setRodada] = useState(0);
  const [carregando, setCarregando] = useState(false);
  const [removendo, setRemovendo] = useState<number | null>(null);
  const remocaoEmVoo = useRef(false);
  const entradaEmVoo = useRef(false);
  const [entrando, setEntrando] = useState(false);
  useFocusEffect(useCallback(() => {
    entradaEmVoo.current = false;
    setEntrando(false);
  }, []));
  const navegar = (destino: Href, substituir = false) => {
    if (entradaEmVoo.current) return;
    if (substituir) router.dismissTo(destino); else router.push(destino);
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
      const l = await limiteBancario(p.user_id);
      const s = await conexoes(p.user_id, controlador);
      if (!cancelado) setDados({ uid: p.user_id, lista: s.connections, podeAdicionar: l.pode_adicionar,
        mensagem: l.message, permiteReconectar: l.of_banks_max !== 0 });
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
  return <Screen sobCabecalho onAtualizar={() => setRodada((v) => v + 1)} atualizando={carregando}><View style={{ gap: espaco.lg, paddingVertical: espaco.xl }}>
    <Texto variante="secao">Sua grana, conectada</Texto>
    <Texto tom="inkMuted">Confira a autorização e a última sincronização de cada banco.</Texto>
    {erro && <Banner tom="danger" mensagem={erro} />}
    {!dados && !erro && <Texto tom="inkMuted">Conferindo seus bancos…</Texto>}
    {dados?.lista.length === 0 && <Texto>Nenhum banco conectado. Conecte seu banco para começar.</Texto>}
    {dados?.lista.map((c) => <Card key={c.id}><View style={{ gap: espaco.md }}>
      <Texto variante="secao">{c.institution_name ?? "Seu banco"}</Texto>
      <ConnectionStatus estado={c.ui.state} label={c.ui.label} detalhe={c.ui.detail ?? undefined} />
      <Texto variante="legenda" tom="inkMuted">{c.last_sync_at ? `Última sincronização: ${new Date(c.last_sync_at).toLocaleString("pt-BR")}` : "A primeira sincronização ainda não terminou."}</Texto>
      {c.ui.state === "updating" && c.provider_item_id && <Button rotulo="Acompanhar sincronização" variante="secondary" desativado={entrando} onPress={() => navegar({ pathname: "/open-finance-volta", params: { itemId: c.provider_item_id!, modo: "acompanhar" } })} />}
      {c.provider_item_id && dados.permiteReconectar && !["removed", "item_missing", "paused"].includes(c.ui.state) && <Button rotulo={`Reconectar ${c.institution_name ?? "banco"}`} variante="secondary" desativado={entrando || removendo !== null} onPress={() => abrirAutorizacao(c.provider_item_id!)} />}
      <Button rotulo={`Desconectar ${c.institution_name ?? "banco"}`} variante="ghost" carregando={removendo === c.id} desativado={entrando || (removendo !== null && removendo !== c.id)} onPress={() => pedirRemocao(c)} />
    </View></Card>)}
    {dados?.mensagem && <Banner tom="info" mensagem={dados.mensagem} />}
    <Button rotulo="Conectar outro banco" desativado={entrando || !dados?.podeAdicionar || removendo !== null} onPress={() => abrirAutorizacao()} />
    <Button rotulo="Conferir de novo" variante="secondary" onPress={() => setRodada((v) => v + 1)} />
    <Button rotulo="Voltar ao Início" variante="ghost" desativado={entrando} onPress={() => navegar("/", true)} />
  </View></Screen>;
}
