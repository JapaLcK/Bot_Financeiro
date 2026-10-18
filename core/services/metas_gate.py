"""Gate do `create_meta`: a mensagem do turno É um comando de criar? A FRASE é fechada por uma gramática positiva
(`fullmatch`, fail-closed): o que não encaixa de ponta a ponta (hedge, cancelamento, citação, pergunta com ou sem "?",
emoji, qualquer palavra sobrando) vira "Só confirmando", que custa um "sim". Falso negativo é aceito de propósito; falso
positivo grava meta que ninguém pediu. O que NÃO é fechado pela gramática é o SLOT DO NOME (texto livre): ali há uma
lista (`_STOP`/`_RAD`: hesitação, qualificador, hedge) e a regra de infinitivo. Resíduo: "cria uma meta de 5000 pra X"
com X uma palavra de hedge desconhecida cria a meta "X" (sem mover dinheiro; a resposta ensina "apaga a meta X").

    [pode|por favor|pf] cria|crie|criar|faz|fazer [a|uma|nova|minha]* meta
        ( [de] VALOR [pra|pro|para|chamada NOME]  |  (chamada|pra|pro|para NOME | Nome-Capitalizado) [de] VALOR )
        [até DATA] [, por favor|pf|valeu|obrigado] [. | !]
    [pode|por favor|pf] registra|registre|registrar|cadastra|cadastre|cadastrar  (uma|nova meta | meta chamada)  ...igual
    [pode|por favor|pf] qualquer verbo acima (a|uma|minha) reserva de emergência [de N meses] [cortesia]

NOME antes do valor é 1 palavra e só vale com `chamada|pra|pro|para` na frente ou Capitalizada no texto original (não
é "uns 5000", "ficticia de 5000"). "registra" só com "uma meta"/"meta chamada" (senão pode ser depósito). Fora de
propósito: "abre" (abrir = ver), "faça", "me cria", "da pra/podia criar" (pergunta sem "?").
Com `args` (o que o modelo pôs na tool) o gate confere o que a FRASE disse: valor, tipo (meta × reserva), nome, prazo
(`data_alvo` só com "até", e exatamente a data dita: dia, ou fim do mês, ou 31/12), meses e `fonte` (nunca vem na frase: a padrão sai por código). Divergiu: pergunta.
"""
from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

_N = r"(?!0+\b)(?:\d{1,3}(?:\.\d{3}){1,2}|\d{1,9})"
# ano nu (2020-2099) não é valor: "cria a meta de 2028" pode ser o nome; com R$/k/mil/reais é dinheiro
_NUM = rf"(?:r\$ ?{_N}|(?!20[2-9]\d\b(?! ?(?:k|mil|reais))){_N})(?:,\d{{1,2}})?(?: ?(?:k|mil))?(?: reais)?"
_EXT = {"um": 1, "dois": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10,
        "quinze": 15, "vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50, "cem": 100}
_VALOR = rf"(?:{_NUM}|(?:(?:{'|'.join(_EXT)}) mil|mil)(?: reais)?)"
_STOP = (r"e|ou|mas|se|nao|n|nn|so|ainda|depois|antes|quando|amanha|hoje|agora|talvez|sei|la|sla|ne|que|tal|acha|acho|"
         r"cria|crie|criar|faz|fazer|apaga|deleta|esquece|deixa|cancela|ignora|desisti|mentira|duvida|rola|"
         r"pode|hein|ai|entao|tambem|uma|um|outra|outro|mais|pfv|pf|favor|valeu|obrigado|obrigada|vlw|ok|blz|kk+|conta|"
         r"tipo|uns|cerca|aproximadamente|digamos|supondo|quase|perto|algo|pelo|menos|maximo|minimo|ate|volta|"
         r"jamais|nunca|sempre|ontem|nada|quem|ninguem|"
         r"gastos?|despesas?|investimentos?|orcamentos?|caixinhas?|cofrinhos?|categorias?|cartao|cartoes|faturas?|receitas?|saldo|pix")
_RAD = r"brincad|mentir|ficti|hipote|simula|imagin|exemplo|fake|teste|zoeira|sonho|fantasi"  # radicais de hedge
_NEG = rf"(?!(?:{_STOP})\b|{_RAD})(?![a-z]*(?:ar|er|ir)\b)"  # nome não é hedge nem infinitivo ("pra ver", "pra celular")
_PAL = rf"{_NEG}[a-z][a-z0-9]*"
_CAP = rf"{_NEG}(?-i:[A-Z][A-Za-z0-9]*)"  # Capitalizada no texto ORIGINAL ("cria a meta Viagem de 5 mil")
_NOME = rf"{_PAL}(?: (?:de|da|do) {_PAL})?"
_MES = "janeiro|fevereiro|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro"
_DATA = rf" ate (?:(?:(?:dia )?\d{{1,2}} de )?(?:{_MES})(?: de)? \d{{4}}|\d{{4}}|\d{{1,2}}/\d{{1,2}}/\d{{4}}|\d{{1,2}}/\d{{4}})"
_FIM = r"(?:,? (?:por favor|pf|valeu|obrigado|obrigada|vlw))?[.!]?"
_PRE = r"(?:(?:pode|por favor,?|pf,?) )?"
_CRIA = rf"{_PRE}(?:cria|crie|criar|faz|fazer) (?:(?:a|uma|nova|minha) )*meta(?: nova)?"
_REG = (rf"{_PRE}(?:registra|registre|registrar|cadastra|cadastre|cadastrar) "
        r"(?:(?:uma|nova) (?:nova )?meta(?: nova)?|(?:a |uma )?meta(?= chamad[ao] ))")
_CORPO = (rf" de (?P<v1>{_VALOR})(?: (?:pra|pro|para|pras|pros|chamada|chamado) (?P<n1>{_NOME}))?"
          rf"| (?:(?:chamada|chamado|pra|pro|para) (?P<n2>{_PAL})|(?P<n3>{_CAP})) (?:de )?(?P<v2>{_VALOR})")
_RESERVA = (rf"{_PRE}(?:cria|crie|criar|faz|fazer|registra|registre|registrar|cadastra|cadastre|cadastrar) "
            r"(?P<res>(?:a|uma|minha) reserva de emergencia(?: de (?P<mes>\d{1,2}) meses)?)")
_COMANDO = re.compile(rf"(?:(?:{_CRIA}|{_REG})(?:{_CORPO})(?P<dt>{_DATA})?|{_RESERVA}){_FIM}", re.I)


def _sem_acento(texto: str) -> str:  # mantém a caixa: a nomeação Capitalizada vale no texto original
    t = unicodedata.normalize("NFKD", texto or "")
    return re.sub(r" +", " ", "".join(c for c in t if not unicodedata.combining(c))).strip(" ")


def _valor(t: str) -> Decimal | None:
    t = re.sub(r"r\$|reais| ", "", t.lower())
    mult = 1000 if t.endswith(("k", "mil")) else 1
    t = re.sub(r"(k|mil)$", "", t)
    try:
        if not t:
            return Decimal(mult)
        if t in _EXT:
            return Decimal(_EXT[t] * mult)
        t = t.replace(".", "").replace(",", ".") if "," in t or re.fullmatch(r"\d{1,3}(\.\d{3})+", t) else t
        return Decimal(t) * mult
    except InvalidOperation:
        return None


def _prazo_confere(dt: str | None, iso: object) -> bool:
    """Sem "até" na frase, sem `data_alvo`. Com "até", a `data_alvo` é EXATAMENTE a data que a frase diz: o dia dito;
    só mês = último dia do mês (o contrato da tool); só ano = 31/12. Data impossível ("31/02") pergunta."""
    if dt is None:
        return iso is None
    if not (isinstance(iso, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso[:10])):
        return False
    ano = int(dt[-4:])
    nome = re.search(_MES, dt, re.I)
    num = re.search(r"(\d{1,2})/\d{4}$", dt)
    mes = _MES.split("|").index(nome.group(0).lower()) + 1 if nome else (int(num.group(1)) if num else None)
    dia = re.match(r" ate (?:dia )?(\d{1,2})(?: de |/\d{1,2}/)", dt)  # "até 10 de dezembro", "até 10/12/2027"
    try:
        if mes is None:
            esperado = date(ano, 12, 31)
        else:
            esperado = date(ano, mes, int(dia.group(1)) if dia else calendar.monthrange(ano, mes)[1])
    except ValueError:
        return False
    return iso[:10] == esperado.isoformat()


def _confere(m: re.Match, args: dict) -> bool:
    """O que o modelo pôs na tool tem de ser o que a FRASE disse. Tudo extraído do MESMO match da gramática."""
    if args.get("fonte") or (args.get("tipo") == "reserva") != (m.group("res") is not None):
        return False
    if m.group("res"):
        from db.metas import MESES_RESERVA
        try:
            meses = None if args.get("meses") is None else int(args["meses"])
        except (TypeError, ValueError):
            return False
        return args.get("data_alvo") is None and args.get("alvo") is None and (
            meses == int(m.group("mes")) if m.group("mes") else meses in (None, MESES_RESERVA))
    dito = m.group("n1") or m.group("n2") or m.group("n3")  # o nome só vale se foi dito NO SLOT do nome
    if not dito or _sem_acento(str(args.get("nome") or "")).lower() != dito.lower():
        return False
    if args.get("meses") is not None or not _prazo_confere(m.group("dt"), args.get("data_alvo")):
        return False
    try:
        return _valor(m.group("v1") or m.group("v2")) == Decimal(str(args.get("alvo")).strip())
    except InvalidOperation:
        return False


def pede_criar_meta_ja(texto: str, args: dict | None = None) -> bool:
    """`True` só se a frase inteira é o comando (e, com `args`, a tool diz o mesmo que a frase)."""
    m = _COMANDO.fullmatch(_sem_acento(texto))
    return bool(m) and (args is None or _confere(m, args))
