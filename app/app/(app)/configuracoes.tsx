import { router } from "expo-router";
import { useState } from "react";
import { Linking, View } from "react-native";
import { baseUrl } from "@/api/client";
import { useSessao } from "@/features/auth/sessao";
import { Button } from "@/ui/componentes/Button";
import { Banner } from "@/ui/componentes/Banner";
import { Screen } from "@/ui/componentes/Screen";
import { Texto } from "@/ui/componentes/Texto";
import { espaco } from "@/ui/tokens";

export default function Configuracoes() {
  const sessao = useSessao();
  const [erro, setErro] = useState<string | null>(null);
  const [saindo, setSaindo] = useState(false);
  return <Screen sobCabecalho><View style={{ gap: espaco.lg, paddingVertical: espaco.xl }}>
    <Texto variante="secao">Sua conta, do seu jeito</Texto>
    <Texto tom="inkMuted">Cuide das suas conexões e da segurança do PigBank.</Texto>
    <Button rotulo="Bancos conectados" variante="secondary" onPress={() => router.push("/conexoes")} />
    <Button rotulo="Segurança" variante="secondary" icone="Lock" onPress={() => router.push("/seguranca")} />
    <Button rotulo="Teste Open Finance" variante="ghost" onPress={() => router.push("/teste-pluggy")} />
    <Button rotulo="Falar com o suporte" variante="ghost" onPress={() => { void Linking.openURL(`${baseUrl()}/suporte`).catch(() => setErro("Não conseguimos abrir o suporte. Tente de novo.")); }} />
    <Texto variante="legenda" tom="inkMuted">A assinatura pela Apple será disponibilizada em uma próxima atualização.</Texto>
    {erro && <Banner tom="danger" mensagem={erro} />}
    <Button rotulo="Sair" variante="secondary" carregando={saindo} onPress={() => {
      setSaindo(true); setErro(null);
      void sessao.sair().then((ok) => { if (!ok) { setSaindo(false); setErro("Não conseguimos sair. Tente de novo."); } });
    }} />
  </View></Screen>;
}
