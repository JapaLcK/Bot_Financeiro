"""
core/services/ai_chat/valor_do_usuario.py — o dinheiro que a IA grava tem que
ter sido escrito pelo usuário.

`add_launch` e irmãs gravam sem confirmação; sem esta âncora o modelo grava o
`valor` que ele escolhe (medido: copiou "50"/"450,73" dos exemplos do system
prompt). Vale em todo canal e com a flag WA_IA_PRIMEIRO desligada.

Só texto do USUÁRIO conta: linhas assistant e tool nunca (é o que impede copiar
exemplo do bot). A guarda lê valor SÓ EM DÍGITOS (decisão do dono): quem escreve o valor por
extenso ("cinquenta") não ancora e é perguntado "Qual é o valor?" (falso-rejeite aceito, fail-closed).
Leitores reaproveitados: `ai_guard.collect_evidence` (dígitos BRL, já descarta "dia 20", "12x",
"em 2026", "#50"), `_extract_valor` (multiplicador em dígito: "1,2 mil") e `utils_text.extract_parcelas`
(contagem de parcelas, a mesma do `parcelar` do cartão; contagem não é valor, então "doze vezes" vale).
Só uma regex de leitura é daqui: `_NAO_VALOR_RE` (número que não é valor: CPF, data, cartão final).
"""
from __future__ import annotations

import json
import re

import db
from core.observability import log_system_event_sync
from core.services.ai_guard import _MASCARA_NAO_MONETARIA_RE, _NAO_MONETARIO_DO_USUARIO_RE, collect_evidence
from parsers import _extract_valor
from utils_text import PT_PHRASE, extract_parcelas

from ._context import CURRENT_PLATFORM, CURRENT_USER_MESSAGE

# ponytail: a mensagem atual + até 3 ANTERIORES do usuário desde o último corte
# (`system`). "gastei 500" + 2 mensagens de outro assunto + a atual ainda grava;
# com 3 entre elas é recusado. Janela por tempo/turno só se isso aparecer no log
# `ai_valor_sem_ancora`.
_MENSAGENS = 3

# Número que a mensagem do usuário traz e NÃO é valor (o `ai_guard` já cobre dia/Nx/
# ano com "em|de"): CPF/CNPJ, data (dd/mm, dd-mm, "12 de outubro"), hora, porcentagem e "palavra + dígitos"
# (cartão final 1234, ano 2026, conta/ag/cep/tel). Local de propósito: o
# `_MASCARA_NAO_MONETARIA_RE` do ai_guard alimenta a guarda de afirmações. Os dígitos
# depois da palavra pedem 4+. `conta`/`cartão`/`final` sozinhos NÃO mascaram: vêm antes de
# dinheiro ("paguei a conta 1200", "valor final 1500"); só "cartão final"/"conta final" 1234.
_NAO_VALOR_RE = re.compile(
    r"\d{3}\.\d{3}\.\d{3}-\d{2}|\d{2}\.\d{3}\.\d{3}/\d{4}-?\d{2}"
    r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b|\b\d{1,2}:\d{2}\b|\d+(?:[.,]\d+)?\s*%"
    # data ISO ("2026-10-20") e pontuada ("10.12.2026"); "1.000.000" e "12.345" (milhar) e "49.90" (decimal) não casam
    r"|\b\d{4}-\d{1,2}-\d{1,2}\b|(?<![\d.,])\d{1,2}\.\d{1,2}\.\d{2,4}\b"
    # data com hífen ("12-10", "12-10-2026") e por extenso ("12 de outubro [de 2026]"): só "N de MÊS", nunca "50 em março"
    r"|(?<![\d.,-])\d{1,2}-\d{1,2}(?:-\d{2,4})?\b"
    r"|(?<![\d.,])\d{1,2}\s+de\s+(?:janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro"
    r"|dezembro|jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)\b(?:\s+de\s+\d{2,4})?"
    r"|\b(?:(?:cart[aã]o|conta)\s+final|ag[eê]ncia|ag|ano|n[º°]|n[uú]mero|cep|telefone|tel|cel)\.?\s*:?\s*\d[\d\-]{3,}"
    # quantidade/duração/medida: "daqui 10 dias", "5 km", "3 pessoas". Nunca mil/milhão ("2 mil") nem real/conto/centavo.
    # Sem outro dígito logo depois: "paguei 300 dia 20" é dinheiro + data. ponytail: "1500 mês" (sem "por/ao") mascara.
    r"|(?<![\d.,])\d+(?:[.,]\d+)*\s*(?:dias?|semanas?|m[eê]s|meses|anos?|horas?|h|minutos?|min|km|kg|g|ml|l|litros?"
    r"|metros?|m2|m²|unidades?|un|pessoas?)\b(?!\s*\d)",
    re.IGNORECASE,
)

# Resposta terminal (sem nova ida ao modelo) e o que o histórico guarda: sem o
# número inventado, senão o ai_guard o leria como evidência.
RECUSA = (
    json.dumps({"status": "valor_nao_informado", "message": "Nada foi gravado: o valor não está no "
               "que o usuário escreveu. Use só número que ele digitar; exemplo não é dado do usuário."},
               ensure_ascii=False),
    "🐷 Não anotei nada: não achei esse valor no que você escreveu. Qual é o valor?",
)
# O valor está no texto, a contagem de parcelas não: "não achei o valor" mentiria.
RECUSA_PARCELAS = (
    json.dumps({"status": "parcelas_nao_informadas", "message": "Nada foi gravado: a quantidade de parcelas "
               "não está no que o usuário escreveu. Pergunte quantas parcelas."}, ensure_ascii=False),
    "🐷 Não anotei nada: em quantas parcelas foi? Ex.: 12x.",
)
# Outra chamada da mesma rodada já deu resposta terminal (gravou, erro de validação ou a
# pergunta de forma de pagamento): a resposta não pode dizer "nada" nem esconder a recusa.
RECUSA_PARCIAL = "🐷 Não anotei um dos itens: não achei o valor dele no que você escreveu."
RECUSA_PARCIAL_PARCELAS = "🐷 Não anotei um dos itens: em quantas parcelas foi? Ex.: 12x."


def _textos_do_usuario(user_id: int) -> list[str]:
    textos: list[str] = []
    for m in db.ai_get_last_messages(user_id, 30):  # da mais nova para a mais velha
        if m["role"] == "system":  # turno encerrado fora da IA: o que veio antes já foi atendido
            break
        if m["role"] == "user":
            textos.append(m["content"] or "")
    atual = CURRENT_USER_MESSAGE.get()
    if textos and textos[0].strip() == atual:  # o runner já gravou a atual: não conta duas vezes
        textos = textos[1:]
    return textos[:_MENSAGENS] + [atual]


# "50 centavos" são 50 centavos (não R$ 50); "30 reais e 50 centavos" fica com o `_extract_valor`.
# O lookbehind exige dígito antes de "reais e": "trinta reais e 50 centavos" (extenso) lê o 50 como centavos.
_CENTAVOS_RE = re.compile(r"(?<!\d reais e )(?<!\d real e )\b(\d{1,9})\s+centavos?\b", re.IGNORECASE)


# "gastei 49.90" é 49,90: o `collect_evidence` lê todo ponto como milhar (499000) e abriria a porta
# para o modelo gravar R$ 4.990. Ponto + 1-2 dígitos, sem mais dígito nem ".dígito" depois, é decimal;
# 3 dígitos ("1.200", "12.345", "1.000.000") seguem milhar e data ("10.12.2026") fica como está.
# ponytail: "1,234.56" (formato US) não é normalizado; a leitura de hoje é mantida.
_PONTO_DECIMAL_RE = re.compile(r"(?<![\d.,])(\d+)\.(\d{1,2})(?!\d|\.\d|,\d)")

# "2 mil" / "2,5 milhões" / "1 bilhão": o dígito é componente do multiplicador. Só o valor expandido (o do
# `_extract_valor`) ancora; o "2" (R$ 2) e o "mil" solto (R$ 1.000) não, senão o modelo que derruba o
# multiplicador grava 1000x menos. "1.200" (milhar) e "5k" não casam. ponytail: "2 mil e 500" mantém o 500 como
# R$ 500 (já ancorava); o composto 2.500 vem do `_extract_valor`.
_MULT_DIGITOS_RE = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)*\s*(?:mil(?:h[ãa]o|h[õo]es)?|bilh[ãa]o|bilh[õo]es)\b", re.IGNORECASE)


# "12x de 50" / "12x R$ 50" / "12 parcelas a 50": o valor depois do "de"/"a"/"R$" é da PARCELA e o `valor` das tools de
# cartão é o TOTAL. Esse trecho não ancora o total sozinho: com parcelas>1 só `parcelas × valor-de-parcela lido` o ancora
# (um produto com qualquer outro número lido, como o 50 de "comprei por 50 em 12x", seria invenção). A contagem pode vir
# por extenso ("doze vezes": contagem não é valor); o VALOR só em dígito (o grupo 1). "12x 50" (solto, sem de/a/R$) é
# ambíguo e fica fora: o 50 segue ancorando como total. ponytail: "dez vezes de 2 mil e 500" (dígito + "e 500") não compõe.
_PARCELA_RE = re.compile(
    rf"\b(?:\d+\s*|(?:{PT_PHRASE})\s+)(?:x|vezes?|parcelas?)\s+(?:(?:de|a)\s+(?:r\$\s*)?|r\$\s*)"
    r"(\d[\d.,]*(?:\s*(?:mil(?:h[ãa]o|h[õo]es)?|bilh[ãa]o|bilh[õo]es)\b)?(?:\s+centavos?\b)?)", re.IGNORECASE)


def _centavos_lidos(textos: list[str]) -> set[int]:
    textos = [_PONTO_DECIMAL_RE.sub(r"\1,\2", _NAO_VALOR_RE.sub(" ", t)) for t in textos]
    lidos = {int(n) for t in textos for n in _CENTAVOS_RE.findall(t)}
    textos = [_CENTAVOS_RE.sub(" ", t) for t in textos]  # "50 centavos" não vira também "R$ 50"
    lidos |= set(collect_evidence([], "\n".join(_MULT_DIGITOS_RE.sub(" ", t) for t in textos))[0])
    for t in textos:
        limpo = _NAO_MONETARIO_DO_USUARIO_RE.sub(" ", _MASCARA_NAO_MONETARIA_RE.sub(" ", t))
        # sem dígito o `_extract_valor` cairia no extenso da mensagem inteira: só dígito ancora
        v = _extract_valor(limpo) if re.search(r"\d", limpo) else None
        if v and 0 < v < 1e12:  # também descarta NaN/inf, que o parse_money pode devolver
            lidos.add(round(v * 100))
    return lidos


def _parcelas(bruto, textos: list[str]) -> tuple[int, bool]:
    """(contagem do modelo, o usuário a escreveu?). >1 só vale se ele escreveu essa
    contagem ("12x", "em 12 vezes", "em 12", "doze vezes"); 1/ausente/lixo (Infinity, NaN,
    lista) é 1 e vale."""
    try:
        p = int(bruto or 1)
    except (TypeError, ValueError, OverflowError):
        return 1, True
    return p, p <= 1 or p in {extract_parcelas(t.lower()) for t in textos}


def ancora_valor(user_id: int, tool, args: dict) -> tuple[dict, str | None]:
    """(args, acao). `acao`: None = valor ancorado (ou nada a conferir); "recusa" =
    campo obrigatório sem âncora; "recusa_parcelas" = idem, mas o valor está no texto e
    a contagem de parcelas não; "descarta" = campo opcional sem âncora, já
    removido dos args devolvidos."""
    campo = getattr(tool, "valor_do_usuario", None)  # getattr: o runner trata assim todo atributo de tool
    if campo is None or not isinstance(args, dict) or args.get(campo) is None:
        return args, None
    try:
        v = round(abs(float(args[campo])) * 100)
    except (TypeError, ValueError, OverflowError):  # ilegível/NaN/inf: o execute recusa
        return args, None
    textos = _textos_do_usuario(user_id)
    lidos = _centavos_lidos(textos)
    tem_parcelas = "parcelas" in tool.schema["function"]["parameters"].get("properties", {})
    p, escrita = _parcelas(args.get("parcelas") if tem_parcelas else None, textos)
    # com parcelas>1, o valor lido só no "Nx de <valor>" é de parcela: não é o total; só `parcelas × esse valor` é
    fora, de_parcela = lidos, set()
    if p > 1:
        # o "em 12" nu ("comprei a TV em 12") é a contagem, não R$ 12 (depois do _PARCELA_RE: "em 12x de 50" é dele)
        fora = _centavos_lidos([re.sub(rf"\bem\s+{p}\b(?![.,]\d)", " ", _PARCELA_RE.sub(" ", t)) for t in textos])
        de_parcela = _centavos_lidos([m[1].rstrip(".,") for t in textos for m in _PARCELA_RE.finditer(t)])
    if escrita and (v in fora or v in {c * p for c in de_parcela}):
        return args, None
    acao = "recusa" if campo in tool.schema["function"]["parameters"].get("required", []) else "descarta"
    log_system_event_sync("info", "ai_valor_sem_ancora", "valor da IA sem âncora no texto do usuário",
                          source="ai_chat/valor_do_usuario", user_id=user_id,
                          details={"tool": tool.name, "acao": acao, "motivo": "valor" if escrita else "parcelas",
                                   "plataforma": CURRENT_PLATFORM.get(), "texto_tem_numero": bool(lidos)})
    if acao == "recusa" and not escrita:
        acao = "recusa_parcelas"  # só o retorno: o log acima segue "recusa" + motivo
    return ({k: x for k, x in args.items() if k != campo} if acao == "descarta" else args), acao
