import { voltarAoInicio } from "@/features/openFinance/navegacao";
import { router, useFocusEffect, type Href } from "expo-router";
import { useCallback, useRef, useState } from "react";
import { Image, View } from "react-native";
import { SessaoExpirada, RequisicaoSuperada } from "@/api/client";
import { carregarAcessoBancario, type AcessoBancario } from "@/features/openFinance/acesso";
import { useSessao } from "@/features/auth/sessao";
import { SEM_SENHA } from "@/features/openFinance/volta";
import { Button } from "@/ui/componentes/Button";
import { Banner } from "@/ui/componentes/Banner";
import { Screen } from "@/ui/componentes/Screen";
import { Texto } from "@/ui/componentes/Texto";
import { TemaProvider } from "@/ui/tema";
import { espaco } from "@/ui/tokens";

export default function ConectarBanco() {
  const sessao = useSessao();
  const [acesso, setAcesso] = useState<AcessoBancario | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [rodada, setRodada] = useState(0);
  const entradaEmVoo = useRef(false);
  const [entrando, setEntrando] = useState(false);
  useFocusEffect(useCallback(() => {
    entradaEmVoo.current = false;
    setEntrando(false);
  }, []));
  const navegar = (destino: Href, substituir = false) => {
    if (entradaEmVoo.current) return;
    entradaEmVoo.current = true; setEntrando(true);
    try { if (substituir && destino === "/") voltarAoInicio(); else if (substituir) router.dismissTo(destino); else router.push(destino); }
    catch { entradaEmVoo.current = false; setEntrando(false); setErro("Não conseguimos abrir a tela. Tente de novo."); }
  };
  const abrirAutorizacao = () => {
    if (entradaEmVoo.current) return;
    entradaEmVoo.current = true; setEntrando(true);
    try { router.push("/autorizando"); }
    catch { entradaEmVoo.current = false; setEntrando(false); setErro("Não conseguimos abrir a autorização. Tente de novo."); }
  };
  useFocusEffect(useCallback(() => {
    let ativo = true;
    setErro(null);
    void carregarAcessoBancario().then((a) => { if (ativo) setAcesso(a); }).catch((e: unknown) => {
      if (!ativo || e instanceof RequisicaoSuperada) return;
      if (e instanceof SessaoExpirada) sessao.expirou(e.detalhe);
      else setErro("Não conseguimos confirmar seu acesso. Tente de novo.");
    });
    return () => { ativo = false; };
  }, [rodada]));
  const podeConectar = acesso?.fase === "conectar" || acesso?.fase === "inicio";
  const mensagem = acesso?.fase === "senha" ? SEM_SENHA : acesso?.fase === "cobranca-pendente"
    ? "Há uma cobrança pendente. Seu acesso atual permanece, mas novas conexões bancárias não estão disponíveis enquanto a cobrança é regularizada."
    : acesso?.fase === "sem-acesso" ? "Sua conta ainda não tem um plano com acesso ao app. A contratação pelo iPhone estará disponível em uma próxima atualização."
    : acesso?.fase === "sem-open-finance" ? "Seu acesso atual não permite conectar bancos. Confira as condições da sua conta."
    : "Conecte seu banco e deixe o Piggy organizar sua grana. Para começar, precisamos receber a primeira sincronização.";
  return <TemaProvider acesso><Screen><View style={{ gap: espaco.lg, paddingVertical: espaco.xl, flexGrow: 1 }}>
    <Texto variante="legenda" tom="inkMuted">SEU DINHEIRO, MAIS CLARO</Texto>
    {/* eslint-disable-next-line @typescript-eslint/no-require-imports */}
    <Image source={require("../../assets/brand/piggy-3d.png")} accessible={false} resizeMode="contain" style={{ width: "100%", height: 220 }} />
    <Texto variante="display" accessibilityRole="header">Vamos conhecer sua grana?</Texto>
    <Texto tom="inkMuted">{acesso ? mensagem : "Conferindo sua conta…"}</Texto>
    {erro && <Banner tom="danger" mensagem={erro} />}
    {podeConectar && <>
      <View style={{ gap: espaco.sm }}>
        <Texto variante="rotulo">Você autoriza no seu banco</Texto>
        <Texto tom="inkMuted">O Open Finance compartilha os dados que você autorizar. Sua senha bancária não é guardada pelo PigBank. Você pode desconectar quando quiser.</Texto>
        <Texto variante="legenda" tom="inkMuted">Contas, movimentações, cartões e investimentos, conforme o consentimento disponível no seu banco.</Texto>
      </View>
      <Button rotulo="Conectar meu banco" tamanho="L" desativado={entrando} onPress={abrirAutorizacao} />
      <Button rotulo="Ver meus bancos" variante="secondary" desativado={entrando} onPress={() => navegar("/conexoes")} />
      {acesso?.fase === "inicio" && <Button rotulo="Continuar para o Início" variante="ghost" desativado={entrando} onPress={() => navegar("/", true)} />}
    </>}
    <Button rotulo="Conferir de novo" variante="ghost" onPress={() => setRodada((v) => v + 1)} />
    <Button rotulo="Configurações" variante="ghost" desativado={entrando} onPress={() => navegar("/configuracoes")} />
    <Button rotulo="Sair da conta" variante="ghost" onPress={() => void sessao.sair().then((ok) => { if (!ok) setErro("Não conseguimos sair. Tente de novo."); })} />
  </View></Screen></TemaProvider>;
}
