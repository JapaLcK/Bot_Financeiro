import { ErroDeApi, _esquecerRotacoes, chamar } from "../api/client";
import {
  loginSchema,
  perfilSchema,
  respostaLoginSchema,
  type Perfil,
} from "../api/schemas/auth";
import {
  FalhaNoCofre,
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

/**
 * Envolve uma tentativa de entrada INTEIRA — requisição, decisão e gravação.
 *
 * Cobre os dois desfechos, e isso é o ponto. O erro de uma tentativa superada
 * também é ruído: a conta A responde 401 e a pessoa veria "senha incorreta"
 * enquanto a entrada da conta B, que ela pediu depois, estava indo bem.
 * Descartar o sucesso e mostrar a falha seria escolher o pior dos dois.
 *
 * E o invólucro vai até o FIM, não só até a resposta do servidor: uma falha de
 * gravação também precisa ser lida à luz de quem chegou depois. Manter a
 * conferência espalhada por cada ponto de saída foi exatamente como o ramo do
 * desafio ficou de fora antes, nesta mesma revisão — com um lugar só, não há
 * saída para esquecer.
 */
async function tentativa<T>(executar: (vez: number) => Promise<T>): Promise<T> {
  const minhaVez = ++ultimaTentativa;
  try {
    const r = await executar(minhaVez);
    if (minhaVez !== ultimaTentativa) throw new EntradaSuperada();
    return r;
  } catch (e) {
    // `FalhaNoCofre` NUNCA vira `EntradaSuperada`. Ela diz que o estado da
    // sessão no aparelho ficou desconhecido, e traduzi-la em "outra tentativa
    // assumiu" trocaria um problema que precisa aparecer por uma corrida
    // silenciosa — a pessoa veria a tela seguir normalmente com o cofre
    // possivelmente guardando a sessão errada.
    if (e instanceof FalhaNoCofre) throw e;
    if (minhaVez !== ultimaTentativa) throw new EntradaSuperada();
    throw e;
  }
}

export async function entrar(email: string, senha: string): Promise<Entrada> {
  return tentativa(async (minhaVez): Promise<Entrada> => {
    const r = await chamar("/auth/login", respostaLoginSchema, {
      metodo: "POST",
      corpo: { email, password: senha },
      semAuth: true,
    });
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
  });
}

/** Completa a entrada de quem tem dois fatores. `backup` usa código de reserva. */
export async function verificarMfa(
  desafio: string,
  codigo: string,
  backup = false,
): Promise<Perfil> {
  return tentativa(async (minhaVez) => {
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
  });
}

export async function perfil(): Promise<Perfil> {
  return chamar("/auth/me", perfilSchema);
}

export async function temSessao(): Promise<boolean> {
  return (await lerCredenciais()) !== null;
}

/**
 * Sair: apaga o que está no aparelho e dispara o aviso ao servidor sem
 * esperar por ele.
 *
 * A limpeza vem PRIMEIRO e não depende da rede: um logout feito no metrô, com
 * o fetch do Android sem timeout, podia ficar pendurado para sempre — e com a
 * limpeza no `finally` de depois da resposta, a credencial continuava no
 * keychain enquanto isso, e o próximo a abrir o app entraria na conta de quem
 * achou que tinha saído.
 *
 * A revogação é disparada e esquecida (`void ... .catch`): esperar a resposta
 * não dava durabilidade nenhuma (ela já era engolida antes) e tinha custo
 * concreto — a tela ficaria em "carregando" até a rede resolver, com o cofre
 * já vazio.
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
    // Esquece a linhagem SÓ se a sessão apagada era mesmo a desta saída. Se
    // outra conta assumiu o cofre no meio, a linhagem já é dela, e apagá-la
    // faria as renovações em voo daquela conta virarem fim de sessão.
    //
    // ponytail: esta guarda NÃO tem teste que a discrimine, e a ausência é
    // declarada em vez de escondida. Para alcançá-la, a outra conta precisa
    // entrar E rotacionar dentro da janela da limpeza do cofre — entrar
    // sozinho já zera a linhagem, então só a rotação seguinte a recria. Montar
    // isso exigiria reentrância no dublê de rede, e o teste ficaria medindo o
    // andaime. A guarda fica porque é correta e custa uma condição; quem for
    // mexer aqui não deve confiar em vermelho para perceber que a quebrou.
    if (await limparSessaoDe(daSaida.access, daSaida.refresh)) {
      _esquecerRotacoes();
    }
  } finally {
    // Sem `await`: a resposta não muda nada por aqui (a rejeição é engolida) e
    // esperá-la só atrasaria a tela, com o cofre já limpo. O `.catch` evita a
    // rejeição não tratada — o `fetch` em si já saiu de forma síncrona, dentro
    // de `enviar`, então os testes que contam chamadas logo após `await
    // sair()` continuam vendo o logout.
    void chamar("/auth/logout", perfilSchema.partial(), {
      metodo: "POST",
      // O REFRESH token como credencial, não o access. O servidor revoga a
      // sessão por qualquer um dos dois, mas o access pode estar expirado — e é
      // o caso mais comum de todos, um app parado por mais de quinze minutos.
      // Com um access vencido ele não decodifica nada e não revoga nada, e o
      // logout voltaria 200 sem ter encerrado sessão nenhuma. O refresh dura
      // catorze dias e resolve a sessão sozinho.
      credencial: { access: daSaida.refresh, refresh: daSaida.refresh },
    }).catch(() => undefined);
  }
}
