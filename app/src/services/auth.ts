import { ErroDeApi, _esquecerRotacoes, chamar } from "../api/client";
import {
  loginSchema,
  perfilSchema,
  respostaLoginSchema,
  type Perfil,
} from "../api/schemas/auth";
import {
  guardarCredenciaisSe,
  lerCredenciais,
  limparSessaoDe,
} from "../storage/secure";

/**
 * O resultado de uma tentativa de entrada.
 *
 * Conta com dois fatores NÃO devolve credencial no primeiro passo: devolve um
 * desafio. Tratar as duas respostas com um esquema só transformava o login
 * legítimo dessas contas em erro de contrato — e a pessoa via "resposta
 * inesperada" em vez da tela de código.
 */
/**
 * Ordem de INÍCIO das tentativas de entrada, não de término.
 *
 * Duas entradas podem se sobrepor: alguém erra a conta, corrige e envia de novo
 * antes de a primeira responder. Sem senha de chegada, o resultado aplicado é o
 * de quem TERMINA por último — e a pessoa acaba logada na conta que ela já tinha
 * abandonado, com a tela mostrando a outra. Com a senha, a tentativa velha
 * descobre que foi superada e descarta o próprio resultado.
 */
let ultimaTentativa = 0;

/** Esta entrada foi superada por outra mais nova; o resultado dela é descartado. */
export class EntradaSuperada extends ErroDeApi {
  constructor() {
    super(409, "Outra tentativa de entrada assumiu.");
    this.name = "EntradaSuperada";
  }
}

export type Entrada =
  | { fase: "pronta"; perfil: Perfil }
  | { fase: "mfa"; desafio: string; email: string };

export async function entrar(email: string, senha: string): Promise<Entrada> {
  const minhaVez = ++ultimaTentativa;
  const r = await chamar("/auth/login", respostaLoginSchema, {
    metodo: "POST",
    corpo: { email, password: senha },
    semAuth: true,
  });
  // A conferência vem ANTES de qualquer retorno, inclusive o do desafio: um
  // desafio velho levaria a tela para a etapa de código da conta ERRADA, e o
  // usuário digitaria o token de uma conta para completar a entrada de outra.
  if (minhaVez !== ultimaTentativa) throw new EntradaSuperada();
  if ("mfa_required" in r) {
    return { fase: "mfa", desafio: r.mfa_challenge, email: r.email };
  }
  // A conferência acontece DENTRO da gravação, não antes: entre um passo e o
  // outro caberia uma entrada mais nova, e o aparelho ficaria logado nesta
  // enquanto a tela mostra a outra.
  const gravou = await guardarCredenciaisSe(
    () => minhaVez === ultimaTentativa,
    { access: r.access_token, refresh: r.refresh_token },
  );
  if (!gravou) throw new EntradaSuperada();
  _esquecerRotacoes();
  return {
    fase: "pronta",
    perfil: { user_id: r.user_id, email: r.email, plan: r.plan },
  };
}

/** Completa a entrada de quem tem dois fatores. `backup` usa código de reserva. */
export async function verificarMfa(
  desafio: string,
  codigo: string,
  backup = false,
): Promise<Perfil> {
  const minhaVez = ++ultimaTentativa;
  const r = await chamar("/auth/mfa/verify-login", loginSchema, {
    metodo: "POST",
    corpo: { challenge: desafio, code: codigo, use_backup: backup },
    semAuth: true,
  });
  const gravou = await guardarCredenciaisSe(
    () => minhaVez === ultimaTentativa,
    { access: r.access_token, refresh: r.refresh_token },
  );
  if (!gravou) throw new EntradaSuperada();
  _esquecerRotacoes();
  return { user_id: r.user_id, email: r.email, plan: r.plan };
}

export async function perfil(): Promise<Perfil> {
  return chamar("/auth/me", perfilSchema);
}

export async function temSessao(): Promise<boolean> {
  return (await lerCredenciais()) !== null;
}

/**
 * Sair: avisa o servidor e apaga o que está no aparelho — nesta ordem, mas o
 * apagar acontece mesmo se o aviso falhar.
 *
 * Se a limpeza dependesse da rede, um logout feito no metrô deixaria a
 * credencial no keychain e o próximo a abrir o app entraria na conta de quem
 * achou que tinha saído.
 *
 * A requisição fala pela sessão que INICIOU a saída, e a limpeza identifica
 * essa sessão pelo `jti`, não pelo refresh token. Os dois detalhes vêm do mesmo
 * lugar: entre a captura e o fim há tempo de rede, e nesse tempo ou outra conta
 * entra (e não pode ser deslogada por engano) ou esta mesma sessão é renovada
 * por outra tela (e não pode escapar da saída com um refresh novo).
 */
export async function sair(): Promise<void> {
  // Toda entrada em voo fica inválida: sair é a intenção mais recente, e um
  // login que terminasse depois gravaria credencial numa sessão que o usuário
  // acabou de encerrar.
  ultimaTentativa += 1;
  const daSaida = await lerCredenciais();
  // Sem sessão capturada não há logout a fazer, e TENTAR é pior que não fazer:
  // a requisição releria o cofre e poderia sair autenticada por uma conta que
  // entrou depois. Saída duplicada ou tardia cai exatamente aqui.
  if (!daSaida) return;
  try {
    await chamar("/auth/logout", perfilSchema.partial(), {
      metodo: "POST",
      credencial: daSaida,
    });
  } catch {
    // Silêncio de propósito: o servidor revoga por expiração de qualquer forma.
  } finally {
    _esquecerRotacoes();
    await limparSessaoDe(daSaida.access, daSaida.refresh);
  }
}
