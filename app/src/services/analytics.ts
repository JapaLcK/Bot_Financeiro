import PostHog from "posthog-react-native";

/**
 * Catálogo TIPADO de eventos, num lugar só.
 *
 * O pedido era "não espalhar analytics manualmente por todo componente". A
 * defesa real não é disciplina, é o tipo: `Evento` é uma união fechada, então
 * um nome inventado na tela não compila. Nomes em `area.acao`.
 */
export type Evento =
  | "app.aberto"
  | "auth.login_ok"
  | "auth.login_falhou"
  | "auth.sessao_expirada"
  | "auth.logout";

let cliente: PostHog | null = null;

export function iniciarAnalytics(): void {
  const key = process.env.EXPO_PUBLIC_POSTHOG_KEY;
  if (!key) return;
  cliente = new PostHog(key, {
    host: process.env.EXPO_PUBLIC_POSTHOG_HOST,
  });
}

/**
 * Propriedade de evento é `string | number | boolean` e nada mais.
 *
 * Não é preciosismo: é o que impede alguém de mandar `{ saldo: 1234.56 }` para
 * um terceiro. Valor financeiro não sai daqui — nem em evento, nem em
 * propriedade, nem em identificação.
 */
export function rastrear(
  evento: Evento,
  props?: Record<string, string | number | boolean>,
): void {
  cliente?.capture(evento, props);
}

export function identificar(userId: number): void {
  cliente?.identify(String(userId));
}

export function esquecer(): void {
  cliente?.reset();
}
