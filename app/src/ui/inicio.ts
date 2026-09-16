import { ContratoInvalido, ErroDeApi, SessaoExpirada } from "@/api/client";
import { entrar, perfil, sair, temSessao } from "@/services/auth";

/**
 * A lógica da tela de entrada, sem JSX e sem `react-native`: o Jest a exercita
 * com os serviços reais, e o `index.tsx` só desenha o estado.
 */
export type Estado =
  | { fase: "carregando" }
  | { fase: "entrada"; aviso?: string }
  | { fase: "pronto"; nome: string }
  | { fase: "erro"; mensagem: string; refazer: "abrir" | "sair" };

const GENERICO = "Algo deu errado. Tente de novo.";
const AVISO_MFA =
  "Sua conta usa verificação em duas etapas, que o app ainda não aceita. Por enquanto, entre pelo site.";

let pendentes = 0;
let fila: Promise<void> = Promise.resolve();

/**
 * As ações da tela rodam uma de cada vez, e o que dispara cada uma decide se
 * ela espera ou é ignorada.
 *
 * TOQUE com ação em andamento é ignorado. Numa fila, o segundo toque em Entrar
 * só chamaria `entrar()` depois de o primeiro gravar, e o `ultimaTentativa` de
 * `auth.ts` não teria mais o que derrubar: um 429 na segunda tentativa punha o
 * formulário na tela com a sessão da primeira no cofre.
 *
 * MONTAGEM espera na fila. A rota pode remontar com uma ação em voo, e um
 * `abrir` que corresse junto leria o cofre antes de um Sair limpar (mostrando
 * "Olá" com o cofre vazio) ou antes de um login gravar (mostrando o formulário
 * com a sessão gravada). Ignorar a montagem deixaria a tela nova em
 * `carregando` para sempre.
 *
 * Nenhum resultado é descartado. Duas montagens pendentes só vêm da rota
 * recriada duas vezes seguidas, e cada `abrir` roda sobre o cofre que o
 * anterior deixou: o resultado da primeira é substituído pelo da segunda, e um
 * aviso da primeira pode sumir (ela apaga a sessão expirada e avisa; a segunda
 * acha o cofre vazio e mostra o formulário sem aviso). O resultado de uma ação
 * de tela já desmontada vai para um `setState` sem efeito.
 *
 * O toque volta a valer quando a última ação termina: `pendentes` desce antes
 * do `aplicar`, no mesmo bloco síncrono, e o botão do estado novo só existe
 * depois do render.
 *
 * Pré-condição: `acao` nunca rejeita e `aplicar` nunca lança. Uma `acao` que
 * rejeita deixa `pendentes` acima de zero, e todo toque seguinte é ignorado até
 * o app reiniciar. Um `aplicar` que lança interrompe a fila com `pendentes` já
 * em zero: o toque seguinte ainda entra e fica em `carregando` para sempre, e
 * só a partir do outro todo toque é ignorado. As três ações deste arquivo têm
 * `try/catch`, e o `aplicar` da tela é o `setEstado`.
 *
 * ponytail: uma ação que nunca termina segura a fila, e a tela fica em
 * `carregando` até o app ser fechado. O caso real é o `fetch` sem timeout no
 * Android, cuja correção seria em `client.ts`. O spinner é o limite aceito:
 * deixar a montagem passar na frente é o que faz ela ler o cofre antes de a
 * limpeza do Sair terminar e mostrar um estado que não corresponde a ele —
 * hoje, o erro falso "Esta tela era de outra conta."
 */
function enfileirar(acao: () => Promise<Estado>, aplicar: (e: Estado) => void): Promise<void> {
  pendentes += 1;
  aplicar({ fase: "carregando" });
  fila = fila.then(async () => {
    const proximo = await acao();
    pendentes -= 1;
    aplicar(proximo);
  });
  return fila;
}

/** Botão. Ignorado enquanto houver ação em andamento; devolve a fila atual. */
export function tocar(acao: () => Promise<Estado>, aplicar: (e: Estado) => void): Promise<void> {
  return pendentes > 0 ? fila : enfileirar(acao, aplicar);
}

/** O `useEffect` da tela: sempre entra, atrás do que estiver em voo. */
export function montar(aplicar: (e: Estado) => void): Promise<void> {
  return enfileirar(abrir, aplicar);
}

/** Só para teste: zera a fila entre casos, como o `_resetRenovacao` do client. */
export function _resetTela(): void {
  pendentes = 0;
  fila = Promise.resolve();
}

/** Texto para a pessoa: nunca `TypeError`, rota ou JSON cru. */
function textoDaFalha(e: unknown): string {
  // Antes do `ErroDeApi`, de quem é subclasse: o detalhe dele cita a rota.
  if (e instanceof ContratoInvalido) return GENERICO;
  if (e instanceof ErroDeApi) return e.detalhe.trim() || GENERICO;
  return GENERICO;
}

/** `/auth/me` → nome. A mesma leitura para a abertura e para a entrada. */
async function lerTela(): Promise<Estado> {
  try {
    const p = await perfil();
    const nome = p.display_name?.trim() || p.email?.split("@")[0] || "por aí";
    return { fase: "pronto", nome };
  } catch (e) {
    // Só `SessaoExpirada` volta para a entrada. `RenovacaoIndisponivel` é
    // instabilidade do servidor, e pedir login por causa de um 500 é mentir
    // sobre o estado da conta.
    if (e instanceof SessaoExpirada) return { fase: "entrada", aviso: e.detalhe };
    return { fase: "erro", mensagem: textoDaFalha(e), refazer: "abrir" };
  }
}

export async function abrir(): Promise<Estado> {
  try {
    // Antes da rede: sem isso, abrir offline sem sessão mostraria a falha de
    // rede no lugar do formulário.
    if (!(await temSessao())) return { fase: "entrada" };
  } catch (e) {
    return { fase: "erro", mensagem: textoDaFalha(e), refazer: "abrir" };
  }
  return lerTela();
}

export async function entrarNaTela(email: string, senha: string): Promise<Estado> {
  try {
    // A senha vai como foi digitada: espaço nela é parte dela.
    const r = await entrar(email.trim(), senha);
    if (r.fase === "mfa") return { fase: "entrada", aviso: AVISO_MFA };
  } catch (e) {
    return { fase: "entrada", aviso: textoDaFalha(e) };
  }
  // `entrar` devolve perfil sem `display_name`; o nome vem do `/auth/me`.
  return lerTela();
}

export async function sairNaTela(): Promise<Estado> {
  try {
    await sair();
    return { fase: "entrada" };
  } catch {
    // Não afirma logout: a credencial pode ter ficado no aparelho, e por isso
    // o refazer é o Sair, não a abertura.
    return {
      fase: "erro",
      mensagem: "Não conseguimos encerrar a sessão neste aparelho. Tente de novo.",
      refazer: "sair",
    };
  }
}
