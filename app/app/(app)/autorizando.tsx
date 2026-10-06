import { router, useFocusEffect, useLocalSearchParams } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { View } from "react-native";
import { PluggyConnect } from "react-native-pluggy-connect";
import { RequisicaoSuperada, SessaoExpirada, ErroDeApi } from "@/api/client";
import { iniciarConexaoBancaria } from "@/features/openFinance/acesso";
import { definirWidgetAberto, itemDoLink } from "@/features/openFinance/volta";
import { useSessao } from "@/features/auth/sessao";
import { capturarItemBancario, descartarPreparacaoBancaria, TentativaBancariaPendente, type SubstituicaoBancaria } from "@/storage/secure";
import { Banner } from "@/ui/componentes/Banner";
import { Button } from "@/ui/componentes/Button";
import { Screen } from "@/ui/componentes/Screen";
import { Texto } from "@/ui/componentes/Texto";
import { espaco } from "@/ui/tokens";

export default function Autorizando() {
  const itemId = itemDoLink(useLocalSearchParams().itemId) ?? undefined;
  const sessao = useSessao();
  const [token, setToken] = useState<Awaited<ReturnType<typeof iniciarConexaoBancaria>> | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [pendente, setPendente] = useState<SubstituicaoBancaria | null>(null);
  const [substituir, setSubstituir] = useState<SubstituicaoBancaria | undefined>();
  const pendenteAtual = useRef<SubstituicaoBancaria | null>(null);
  const confirmando = useRef(false);
  const focado = useRef(true);
  const encerrado = useRef(false);
  const ativo = useRef(true);
  const itemPendente = useRef<string | null>(null);
  useFocusEffect(useCallback(() => {
    focado.current = true;
    return () => { focado.current = false; };
  }, []));
  useEffect(() => {
    let cancelado = false;
    ativo.current = true;
    setToken(null);
    setErro(null); setPendente(null); pendenteAtual.current = null;
    void iniciarConexaoBancaria(itemId, () => cancelado || !ativo.current || !focado.current || encerrado.current, substituir).then(async (t) => {
      if (!cancelado && ativo.current && !encerrado.current) { definirWidgetAberto(true); setToken(t); }
      else await descartarPreparacaoBancaria(t.tentativa_id);
    }).catch((e: unknown) => {
      if (cancelado || !ativo.current) return;
      if (e instanceof RequisicaoSuperada) {
        if (substituir) setErro("Sua sessão mudou. Volte e confira sua conexão.");
        return;
      }
      if (e instanceof TentativaBancariaPendente) {
        pendenteAtual.current = e.tentativa;
        setPendente(e.tentativa);
      } else if (e instanceof SessaoExpirada) sessao.expirou(e.detalhe);
      else setErro(e instanceof ErroDeApi ? e.detalhe : "Não conseguimos iniciar a conexão. Tente de novo.");
    }).finally(() => { if (!cancelado) confirmando.current = false; });
    return () => { cancelado = true; ativo.current = false; definirWidgetAberto(false); };
  }, [itemId, substituir]);
  const escolher = (nova: boolean, retomar = false) => {
    if (!ativo.current || !focado.current || encerrado.current || confirmando.current || !pendente || pendenteAtual.current !== pendente) return;
    confirmando.current = true; pendenteAtual.current = null;
    if (nova) setSubstituir(pendente);
    else {
      encerrado.current = true;
      if (retomar) router.replace("/open-finance-volta");
      else if (router.canGoBack()) router.back();
      else router.dismissTo("/");
    }
  };
  const fechar = async (item?: string) => {
    if (item && itemDoLink(item)) itemPendente.current = item;
    // Fechar deduplica navegação; um evento posterior ainda pode trazer a única
    // pista do item. O nonce impede callback deste widget afetar outra tentativa.
    if (encerrado.current) {
      if (item && itemDoLink(item)) {
        try { await capturarItemBancario(item, token?.tentativa_id); }
        catch { if (ativo.current) setErro("Não conseguimos guardar o retorno do banco. Tente fechar novamente."); }
      }
      return;
    }
    encerrado.current = true;
    try {
      if (itemPendente.current) await capturarItemBancario(itemPendente.current, token?.tentativa_id);
      if (!ativo.current) return;
      definirWidgetAberto(false);
      router.replace("/open-finance-volta");
    } catch {
      encerrado.current = false;
      setErro("Não conseguimos guardar o retorno do banco. Tente fechar novamente.");
    }
  };
  return <Screen rolar={pendente ? true : false}><View style={{ flex: 1, gap: espaco.md, paddingVertical: espaco.md }}>
    <Texto variante="secao" accessibilityRole="header">Autorizando no banco</Texto>
    <Texto variante="legenda" tom="inkMuted">Conclua a autorização no seu banco. Depois, volte ao PigBank.</Texto>
    {erro && <Banner tom="danger" mensagem={erro} />}
    {pendente ? <>
      <Banner tom="warning" mensagem="Há uma conexão pendente. Iniciar outra tentativa substitui a retomada desta conexão. O banco ainda pode concluir a autorização anterior." />
      <Button rotulo="Retomar conexão" onPress={() => escolher(false, true)} />
      <Button rotulo="Iniciar nova tentativa" variante="secondary" onPress={() => escolher(true)} />
      <Button rotulo="Cancelar" variante="ghost" onPress={() => escolher(false)} />
    </> : token ? <View style={{ flex: 1 }}><PluggyConnect connectToken={token.accessToken} includeSandbox={token.includeSandbox}
      language="pt" connectorTypes={["PERSONAL_BANK"]} updateItem={itemId} forceOauthInBrowser
      onSuccess={(d) => { void fechar(d.item?.id); }} onError={(d) => { void fechar(d?.data?.item?.id); }} onClose={() => { void fechar(); }} />
    </View> : !erro && <Texto tom="inkMuted">Preparando uma conexão segura…</Texto>}
    {!pendente && <Button rotulo={erro ? "Voltar e tentar de novo" : "Fechar e conferir conexão"} variante="secondary" onPress={() => { if (erro && !token) router.back(); else void fechar(); }} />}
  </View></Screen>;
}
