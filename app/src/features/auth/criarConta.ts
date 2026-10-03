import { ContratoInvalido, ErroDeApi } from "@/api/client";
import { GENERICO, textoDaFalha } from "@/features/auth/entrar";
import { EntradaSuperada, cadastrar as cadastrarNoServidor, confirmarCadastro } from "@/services/auth";
import { FalhaNoCofre } from "@/storage/secure";

/**
 * A máquina de estados de Criar conta, sem JSX e sem `react-native` — mesmo
 * desenho de `entrar.ts`. Nome, e-mail, telefone e senha NUNCA entram aqui:
 * ficam no `useState` da tela (nem estado, nem parâmetro de rota, nem log).
 */
export type EstadoCriarConta =
  | { fase: "formulario"; aviso?: string }
  | { fase: "enviando" }
  | { fase: "codigo"; email: string; aviso?: string; info?: string }
  | { fase: "verificando"; email: string }
  | { fase: "reenviando"; email: string }
  | { fase: "erro-cofre" };

export type EstadoCodigoEmail = Extract<EstadoCriarConta, { fase: "codigo" | "verificando" | "reenviando" }>;

export interface DadosCadastro {
  nome: string;
  email: string;
  telefone: string;
  senha: string;
}

export type ErrosCadastro = Partial<Record<keyof DadosCadastro, string>>;

/**
 * Espelho das fronteiras de `auth_register` (`frontend/finance_bot_websocket_custom.py`).
 * `tests/test_app_cadastro_espelho.py` lê estes três valores daqui e bate no
 * endpoint real: mudar um lado sem o outro fica vermelho.
 */
export const SENHA_MIN = 8;
export const NOME_MIN = 2;
export const NOME_MAX = 50;

/** O texto exato do 400 do servidor (`utils_phone.py`); a fixture guarda o mesmo. */
export const ERRO_TELEFONE = "Informe um número de WhatsApp válido com DDD.";

/** A regex do `cadastro.html`. O servidor não valida formato, e e-mail inválido vira 500 no envio. */
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * Espelho de `normalize_phone_e164` (`utils_phone.py`): só dígitos, tira `00`
 * do começo, 10 ou 11 dígitos ganham `55`, aceita de 12 a 15. A tabela de
 * casos é UMA, `tests/fixtures/telefones_cadastro.json`, lida pelo pytest e
 * pelo Jest. `\D` do JS é só ASCII e o do Python é Unicode: um dígito
 * arábico-índico vira `null` aqui — o cliente é mais rígido, nunca mais frouxo.
 */
export function normalizarTelefone(cru: string): string | null {
  let digitos = cru.replace(/\D+/g, "");
  if (digitos.startsWith("00")) digitos = digitos.slice(2);
  if (digitos.length === 10 || digitos.length === 11) digitos = `55${digitos}`;
  return digitos.length >= 12 && digitos.length <= 15 ? digitos : null;
}

/** Conta como o `len()` do Python (pontos de código), não como `.length` (UTF-16). */
const tamanho = (s: string) => [...s].length;

/** Legenda do WhatsApp nos dois cadastros (Criar conta e Google). */
export const LEGENDA_WHATSAPP = "Use o mesmo número com que você vai falar com o Piggy.";

/** Nome e WhatsApp: o que o cadastro pelo Google também pede (`complete-signup`). */
export function validarPerfil(d: Pick<DadosCadastro, "nome" | "telefone">): ErrosCadastro {
  const erros: ErrosCadastro = {};
  const nome = tamanho(d.nome.trim());
  if (nome < NOME_MIN) erros.nome = `Informe seu nome (pelo menos ${NOME_MIN} letras).`;
  else if (nome > NOME_MAX) erros.nome = `O nome deve ter no máximo ${NOME_MAX} caracteres.`;
  if (normalizarTelefone(d.telefone) === null) erros.telefone = ERRO_TELEFONE;
  return erros;
}

/** Erros por campo; objeto vazio = pode enviar. Nenhuma requisição sai com erro aqui. */
export function validar(d: DadosCadastro): ErrosCadastro {
  const erros = validarPerfil(d);
  if (!EMAIL.test(d.email.trim())) erros.email = "Digite um e-mail válido.";
  if (tamanho(d.senha) < SENHA_MIN) erros.senha = `A senha precisa ter pelo menos ${SENHA_MIN} caracteres.`;
  return erros;
}

/**
 * Quais fases apagam o campo Senha ao entrar nelas — a regra do #578, mesma
 * de `entrar.ts`. O iOS só oferece "Salvar senha" se o campo sai da tela
 * PREENCHIDO (na troca formulário → código), e o reenvio precisa dela.
 */
const APAGA_SENHA: Record<EstadoCriarConta["fase"], boolean> = {
  formulario: true, // falha do register ou "Voltar" do código: nunca mostra a senha de antes
  "erro-cofre": true,
  enviando: false, // o campo continua na tela, desativado; no sucesso sai daqui preenchido
  codigo: false, // o reenvio manda o mesmo corpo, senha incluída
  verificando: false,
  reenviando: false,
};

export function apagaSenhaNaFase(fase: EstadoCriarConta["fase"]): boolean {
  return APAGA_SENHA[fase];
}

/**
 * O corpo do register, igual no primeiro envio e no reenvio. O telefone vai
 * só com os dígitos ASCII: o servidor recebe exatamente a string que
 * `normalizarTelefone` validou, e quem prefixa `55` continua sendo ele.
 */
async function pedirCodigo(d: DadosCadastro): Promise<string> {
  const email = d.email.trim();
  await cadastrarNoServidor({ email, senha: d.senha, nome: d.nome.trim(), telefone: d.telefone.replace(/\D+/g, "") });
  return email;
}

/** F/S → C no 200; qualquer falha volta ao formulário com o texto seguro. */
export async function cadastrar(d: DadosCadastro): Promise<EstadoCriarConta> {
  try {
    return { fase: "codigo", email: await pedirCodigo(d) };
  } catch (e) {
    return { fase: "formulario", aviso: textoDaFalha(e) };
  }
}

/** R → C: o código antigo morre no servidor; a falha fica no código, com aviso. */
export async function reenviar(d: DadosCadastro): Promise<EstadoCriarConta> {
  try {
    return { fase: "codigo", email: await pedirCodigo(d), info: "Enviamos um novo código." };
  } catch (e) {
    return { fase: "codigo", email: d.email.trim(), aviso: textoDaFalha(e) };
  }
}

/**
 * V → resultado. `null` só no sucesso (o `Stack.Protected` troca a rota assim
 * que `autenticar()` roda). Na `EntradaSuperada` volta ao código sem aviso:
 * `null` ali deixava a tela presa em "verificando", e com a saída da rota
 * bloqueada nessa fase, sem saída. Limite conhecido: a superação pode vir do
 * Voltar do MFA de OUTRA conta (`abandonarEntrada()`, contador compartilhado —
 * ver `services/auth.ts`); aí a conta já existe no servidor, a sessão é
 * descartada sem aviso e retentar o código diz "já utilizado". Recuperação:
 * Entrar com e-mail e senha.
 * 5xx, rede, tempo limite e contrato quebrado são ambíguos: se o servidor
 * criou a conta, a próxima tentativa diz "já utilizado" e a pessoa entra pelo
 * Entrar.
 */
export async function confirmar(
  email: string,
  codigo: string,
  autenticar: () => void,
): Promise<EstadoCriarConta | null> {
  try {
    await confirmarCadastro(email, codigo);
    autenticar();
    return null;
  } catch (e) {
    if (e instanceof EntradaSuperada) return { fase: "codigo", email };
    if (e instanceof FalhaNoCofre) return { fase: "erro-cofre" };
    const doServidor = e instanceof ErroDeApi && !(e instanceof ContratoInvalido) && e.status < 500;
    return { fase: "codigo", email, aviso: doServidor ? textoDaFalha(e) : GENERICO };
  }
}
