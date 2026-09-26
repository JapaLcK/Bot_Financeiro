import { Linking } from "react-native";

import { baseUrl } from "@/api/client";
import { Banner } from "@/ui/componentes/Banner";
import { Texto } from "@/ui/componentes/Texto";

const AVISO_LINK = "Não conseguimos abrir a página. Tente de novo em instantes.";

interface Props {
  /** O `openURL` falhou. Fica com a tela, que o apaga ao digitar e ao trocar de fase. */
  aviso: boolean;
  definirAviso: (v: boolean) => void;
}

/**
 * "Ao criar a conta, você aceita…" com os dois links, nos dois cadastros
 * (Criar conta e Google). Sem caixa de seleção: tocar em "Criar conta" é o
 * aceite. Termos e Privacidade moram no site, que é o mesmo servidor da API.
 */
export function Termos({ aviso, definirAviso }: Props) {
  const abrirNoSite = async (rota: string) => {
    definirAviso(false);
    try {
      await Linking.openURL(`${baseUrl()}${rota}`);
    } catch {
      definirAviso(true);
    }
  };

  return (
    <>
      <Texto variante="legenda" tom="inkMuted" style={{ textAlign: "center" }}>
        Ao criar a conta, você aceita os{" "}
        <Texto variante="legenda" tom="brandInk" accessibilityRole="link" onPress={() => void abrirNoSite("/termos")}>
          Termos de Uso
        </Texto>{" "}
        e a{" "}
        <Texto variante="legenda" tom="brandInk" accessibilityRole="link" onPress={() => void abrirNoSite("/privacy")}>
          Política de Privacidade
        </Texto>
        .
      </Texto>
      {aviso ? <Banner tom="warning" mensagem={AVISO_LINK} /> : null}
    </>
  );
}
