import { router } from "expo-router";
import { useState } from "react";
import { Linking, View } from "react-native";
import { baseUrl } from "@/api/client";
import { useSessao } from "@/features/auth/sessao";
import { Banner } from "@/ui/componentes/Banner";
import { Icone } from "@/ui/componentes/Icone";
import { GrupoDeLinhas, ListRow } from "@/ui/componentes/ListRow";
import { Screen } from "@/ui/componentes/Screen";
import { espaco } from "@/ui/tokens";

export default function Configuracoes() {
  const sessao = useSessao();
  const [erro, setErro] = useState<string | null>(null);
  const [saindo, setSaindo] = useState(false);
  return <Screen sobCabecalho><View style={{ gap: espaco.lg, paddingVertical: espaco.xl }}>
    <GrupoDeLinhas>
      <ListRow titulo="Bancos conectados" chevron onPress={() => router.push("/conexoes")} />
      <ListRow titulo="Segurança" chevron onPress={() => router.push("/seguranca")} />
    </GrupoDeLinhas>
    <GrupoDeLinhas>
      <ListRow titulo="Teste Open Finance" chevron onPress={() => router.push("/teste-pluggy")} />
      <ListRow titulo="Falar com o suporte" trailing={<Icone nome="ArrowSquareOut" tamanho={20} tom="inkFaint" />} onPress={() => { void Linking.openURL(`${baseUrl()}/suporte`).catch(() => setErro("Não conseguimos abrir o suporte. Tente de novo.")); }} />
    </GrupoDeLinhas>
    {erro && <Banner tom="danger" mensagem={erro} />}
    <GrupoDeLinhas>
      <ListRow titulo="Sair" tom="danger" carregando={saindo} onPress={() => {
        setSaindo(true); setErro(null);
        void sessao.sair().then((ok) => { if (!ok) { setSaindo(false); setErro("Não conseguimos sair. Tente de novo."); } });
      }} />
    </GrupoDeLinhas>
  </View></Screen>;
}
