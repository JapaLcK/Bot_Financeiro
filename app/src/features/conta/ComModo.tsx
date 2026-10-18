import { useEffect, useState, type ReactNode } from "react";
import { ActivityIndicator, View } from "react-native";

import type { Perfil } from "@/api/schemas/auth";
import { GENERICO } from "@/features/auth/entrar";
import { EsqueciSenha } from "@/features/auth/EsqueciSenha";
import { useFalha } from "@/features/seguranca/falha";
import { perfil as lerPerfil } from "@/services/auth";
import { Banner } from "@/ui/componentes/Banner";
import { Button } from "@/ui/componentes/Button";
import { Texto } from "@/ui/componentes/Texto";
import { useTema } from "@/ui/tema";
import { espaco } from "@/ui/tokens";

import { modoDaConta } from "./exclusao";

type Estado = { fase: "carregando" } | { fase: "erro"; mensagem: string } | { fase: "pronto"; conta: Perfil };

/**
 * O que as sheets de Exportar e Excluir têm em comum: ler o `/auth/me` ao abrir (o modo da conta decide os
 * campos) e, sem credencial, mandar definir a senha em vez de mostrar um formulário que o servidor recusa (409).
 * `acao` completa a frase do caso sem e-mail ("… para <acao>").
 */
export function ComModo({ acao, children }: { acao: string; children: (conta: Perfil, modo: "senha" | "codigo") => ReactNode }) {
  const { cores } = useTema();
  const falha = useFalha();
  const [estado, setEstado] = useState<Estado>({ fase: "carregando" });

  const carregar = async () => {
    setEstado({ fase: "carregando" });
    try {
      setEstado({ fase: "pronto", conta: await lerPerfil() });
    } catch (e) {
      const texto = falha(e);
      // `null` (sessão encerrada ou conta trocada): a rota sai sozinha, mas nunca deixa o spinner eterno.
      setEstado({ fase: "erro", mensagem: texto ?? GENERICO });
    }
  };

  // `[]` de propósito, como as telas de Segurança: `falha` muda a cada estado da sessão.
  useEffect(() => {
    void carregar();
  }, []);

  if (estado.fase === "carregando") return <ActivityIndicator color={cores.brand} accessibilityLabel="Carregando" />;
  if (estado.fase === "erro") {
    return (
      <View style={{ gap: espaco.lg }}>
        <Banner tom="danger" mensagem={estado.mensagem} />
        <Button rotulo="Tentar de novo" onPress={() => void carregar()} />
      </View>
    );
  }
  const { conta } = estado;
  const modo = modoDaConta(conta);
  if (modo !== "sem_credencial") return <>{children(conta, modo)}</>;
  const email = conta.email?.trim();
  return email ? (
    <EsqueciSenha emailFixo={email} />
  ) : (
    <Texto>Sua conta ainda não tem senha. Defina uma senha pelo site para {acao}.</Texto>
  );
}
