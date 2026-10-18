import { ContaPonte } from "./Ponte";
import { ContaSeguranca } from "./Seguranca";

// Segurança aqui; o resto das configurações segue na tela antiga (o link da ponte).
export const ContaConfiguracoes = () => <ContaPonte path="/conta/configuracoes" destino="/settings" acao="Abrir configurações"><ContaSeguranca /></ContaPonte>;
