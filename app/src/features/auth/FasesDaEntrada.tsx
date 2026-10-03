import type { ReactNode } from "react";

import { CodigoMfa } from "@/features/auth/CodigoMfa";
import { CompletarCadastroSocial } from "@/features/auth/CompletarCadastroSocial";
import { MENSAGEM_ERRO_COFRE, tentarDeNovo, type EstadoEntrar } from "@/features/auth/entrar";
import { Banner } from "@/ui/componentes/Banner";

interface Props {
  estado: EstadoEntrar;
  aplicar: (e: EstadoEntrar) => void;
  autenticar: () => void;
  /** O que a tela mostra nas fases dela (formulário, enviando, Google, Apple). */
  children: ReactNode;
}

/**
 * As fases que o Entrar e a Boas-vindas dividem: erro do cofre, código do MFA
 * e cadastro de quem entrou pelo Google ou pela Apple sem conta — na MESMA
 * rota, nunca por parâmetro (o desafio e o token são credenciais).
 */
export function FasesDaEntrada({ estado, aplicar, autenticar, children }: Props) {
  if (estado.fase === "erro-cofre") {
    return (
      <Banner
        tom="danger"
        mensagem={estado.mensagem ?? MENSAGEM_ERRO_COFRE}
        acao={{ rotulo: "Tentar de novo", onPress: () => aplicar(tentarDeNovo()) }}
      />
    );
  }
  if (estado.fase === "mfa" || estado.fase === "verificando") {
    return <CodigoMfa estado={estado} autenticar={autenticar} aplicar={aplicar} />;
  }
  if (estado.fase === "cadastro-social" || estado.fase === "criando-social") {
    return <CompletarCadastroSocial estado={estado} autenticar={autenticar} aplicar={aplicar} />;
  }
  return <>{children}</>;
}
