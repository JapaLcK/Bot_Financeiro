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
 * Evento SEM propriedade. É a fronteira, e ela é o tipo, não um comentário.
 *
 * A versão anterior aceitava `Record<string, string | number | boolean>` e o
 * comentário prometia que valor financeiro não passaria — mas
 * `rastrear("app.aberto", { saldo: 1234.56 })` compilava e mandava o saldo para
 * o PostHog. Promessa que o compilador não cobra é promessa que envelhece na
 * primeira pressa.
 *
 * Nenhum dos cinco eventos da Fase 1 precisa de propriedade, então o tipo mais
 * simples é também o mais seguro. Quando um evento precisar de contexto, quem o
 * adicionar declara o formato DAQUELE evento — uma união discriminada, com os
 * campos permitidos escritos. Aí a regra volta a ser verificável.
 */
export function rastrear(evento: Evento): void {
  cliente?.capture(evento);
}

export function identificar(userId: number): void {
  cliente?.identify(String(userId));
}

export function esquecer(): void {
  cliente?.reset();
}
