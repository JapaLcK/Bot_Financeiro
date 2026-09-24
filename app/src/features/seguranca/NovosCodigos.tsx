import { useState } from "react";

import { novosCodigos } from "@/services/mfa";

import { CodigosBackup } from "./CodigosBackup";
import { SenhaECodigo } from "./SenhaECodigo";

interface Props {
  /** O regenerate saiu: quem chama prende a sheet (os antigos já não valem). */
  aoEnviar: () => void;
  /** O regenerate falhou: quem chama solta a trava, sem sair. */
  aoFalhar: () => void;
  aoConcluir: () => void;
}

/** Senha + TOTP (o servidor não aceita backup aqui) → 10 códigos novos; os antigos deixam de valer. */
export function NovosCodigos({ aoEnviar, aoFalhar, aoConcluir }: Props) {
  const [codigos, setCodigos] = useState<string[] | null>(null);

  if (codigos) return <CodigosBackup codigos={codigos} onConcluir={aoConcluir} />;

  return (
    <SenhaECodigo
      titulo="Gerar novos códigos de backup"
      texto="Os códigos antigos param de funcionar. Confirme sua senha e o código do app autenticador."
      soTotp
      rotuloBotao="Gerar novos códigos"
      enviar={async (senha, codigo) => {
        aoEnviar();
        try {
          setCodigos((await novosCodigos(senha, codigo)).backup_codes);
        } catch (e) {
          aoFalhar();
          throw e;
        }
      }}
    />
  );
}
