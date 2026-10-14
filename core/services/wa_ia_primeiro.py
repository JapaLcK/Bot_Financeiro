"""core/services/wa_ia_primeiro.py — no WhatsApp, a IA atende primeiro.

Com `WA_IA_PRIMEIRO` ligada, o `handle_incoming` manda o texto à IA antes do
classificador; o roteador determinístico só atende o que `fica_no_roteador`
reserva e o que a IA devolve sem resposta (`chat(..., ia_primeiro=True)` → None).

`WA_IA_PRIMEIRO_USER_IDS` (ids por vírgula), se preenchida, restringe a esses
usuários; preenchida sem nenhum id válido, desliga para todos. As duas são lidas a cada mensagem; trocar env no Railway reinicia o
serviço (~1 min).

`lancamento_com_certeza` decide se o `add_launch` da IA grava direto ou pede
"sim" antes (`precisa_confirmar_lancamento`, o `confirmar_se` da tool). Tudo
aqui é leitura, e toda leitura é do `user_id` dado.
"""
from __future__ import annotations

import logging
import os
import re
from contextvars import ContextVar

from utils_text import PT_NUM_ALT_NO_ARTICLE

logger = logging.getLogger(__name__)
_LIGADA = {"1", "true", "yes", "on"}

# Reconhecidos com confiança pelo classificador determinístico: o roteador
# responde sem gastar IA.
_INTENTS_DO_ROTEADOR = frozenset({
    "greeting", "help.tutorial", "account.link", "account.vincular",
    "emails.unsubscribe", "emails.resubscribe", "categories.create",
    "categories.delete", "cdi.check", "funds.add_ask", "funds.withdraw",
    "launches.undo", "credit.handle",
})
# confirm.yes/no ficam de fora: sem pendência "sim/não" não significa nada no roteador, e as
# ofertas de conveniência só se aceitam por botão (wa_runtime._send_reply_with_optional_buttons).
# Quem tem o histórico para entender o "sim" é a IA.
_PREFIXOS_DO_ROTEADOR = ("report.weekly", "report.monthly")

_NUMERO_RE = re.compile(r"\d+(?:[.,]\d+)*")

# Turno do WhatsApp em que a IA foi tentada e desistiu: o resto do turno (o
# roteador) não chama LLM de novo. Ligada e zerada pelo `handle_incoming`.
SEM_LLM_NO_TURNO: ContextVar[bool] = ContextVar("wa_ia_primeiro_sem_llm", default=False)


# Número por extenso (a mesma fonte do parser de valor, sem "um"/"uma", que são
# artigos: "comprei um tênis de 280").
_EXTENSO_RE = re.compile(rf"\b(?:{PT_NUM_ALT_NO_ARTICLE})\b")
_SO_VALOR = frozenset({"r", "rs", "real", "reais"})


def _forma_curta(sem_data: str, args: dict) -> bool:
    """"mercado 80", "uber 23", "77,90 mercado": tirando o número e "R$/reais"
    sobra EXATAMENTE UMA palavra, e ela é o alvo inteiro (normalizado). Com 2+
    palavras ("reembolso mercado 50", "uber aeroporto 23") é incerto: lista
    positiva, sem depender de a IA copiar o texto inteiro para o alvo."""
    from utils_text import normalize_text
    sobra = normalize_text(_NUMERO_RE.sub(" ", sem_data)).split()
    sobra = [p for p in sobra if p not in _SO_VALOR]
    alvo = normalize_text(str(args.get("alvo") or args.get("nota") or "")).split()
    return len(sobra) == 1 and alvo == sobra


def _categoria_serve(tipo: str, curta: bool, categoria: str) -> bool:
    """A categoria combina com o tipo: despesa nunca em categoria só de receita
    ("gastei 50 em dividendo" → rendimentos); na forma curta (sem verbo dando a
    direção), a despesa exige categoria de despesa PADRÃO — receita ("dividendo
    50") e categoria personalizada do usuário ficam incertas."""
    from utils_text import (CATEGORIAS_DE_DESPESA_PADRAO, CATEGORIAS_SO_DE_RECEITA,
                            normalize_text)
    cat = normalize_text(categoria or "")
    if tipo == "despesa" and cat in CATEGORIAS_SO_DE_RECEITA:
        return False
    return not (curta and cat not in CATEGORIAS_DE_DESPESA_PADRAO)


def forma_apoiada(args: dict, texto: str) -> bool:
    """A forma que a IA declarou é a que o texto do usuário declara (ausente:
    nada a corroborar). Vale em qualquer decisão do `fp.decidir`."""
    from core.handlers import forma_pagamento as fp
    forma_ia = args.get("forma_pagamento")
    return not forma_ia or fp.detectar(texto or "") == forma_ia


def ativo(user_id: int) -> bool:
    if (os.getenv("WA_IA_PRIMEIRO") or "").strip().lower() not in _LIGADA:
        return False
    raw = os.getenv("WA_IA_PRIMEIRO_USER_IDS")
    if not raw:  # ausente ou "" exato: sem restrição
        return True
    ids = {s.strip() for s in raw.split(",") if s.strip().isdigit()}
    if not ids:
        # Fail-closed: lista preenchida que não deu id nenhum (",", "  ",
        # "abc") é erro de configuração, não "liberar para todos".
        logger.warning("WA_IA_PRIMEIRO_USER_IDS sem id válido: IA primeiro desligada")
        return False
    return str(int(user_id)) in ids


def fica_no_roteador(user_id: int, text: str) -> bool:
    from core.intent_classifier import classify, vai_ao_tier3_por_marcador
    from parsers import split_financial_transactions

    r = classify(text, user_id=user_id, allow_ai=False)
    if r.confidence >= 0.55 and (r.intent in _INTENTS_DO_ROTEADOR
                                 or r.intent.startswith(_PREFIXOS_DO_ROTEADOR)):
        return True
    return vai_ao_tier3_por_marcador(text) or len(split_financial_transactions(text)) > 1


def lancamento_com_certeza(user_id: int, args: dict, texto_do_usuario: str) -> bool:
    """True só se valor, data, tipo e categoria que a IA mandou batem com o que
    o texto do usuário diz por si só, sem LLM:

    (a) valor: exatamente um número em dígitos e nenhum por extenso no texto
        (fora a data), igual ao da IA;
    (d) data: texto com data → `args["data"]` no mesmo dia; texto sem data →
        `data` ausente ou hoje. Dia no fuso do app (`_tz`, o mesmo do
        `extract_date_from_text` e do `_parse_iso_datetime_for_launch`);
    (c) tipo, por lista positiva: receita só com verbo de receita no começo;
        despesa só com verbo de saída no começo (`VERBOS_DE_SAIDA`) ou na forma
        curta ("mercado 80": sem o número e "R$/reais" sobra UMA palavra, e ela
        é o alvo inteiro); sem verbo e sem forma curta, incerto;
    (b) categoria: com hashtag, a IA ecoa a da hashtag E a regra local da nota
        não a contradiz (senão o cross-check do `add_from_entities` a trocaria);
        sem hashtag, regra local confiante na nota e no texto, iguais; a
        categoria tem de servir ao tipo (`_categoria_serve`): despesa não vai a
        categoria só de receita, e a forma curta (sem verbo) exige categoria de
        despesa padrão (`utils_text.CATEGORIAS_DE_DESPESA_PADRAO`);
    (e) forma_pagamento: se a IA declarou, o texto declara a mesma
        (`forma_apoiada`, `forma_pagamento.detectar`), também quando o
        `fp.decidir` dá BANCO (`precisa_confirmar_lancamento`); ausente segue o
        `fp.decidir`;
    (f) alvo e nota: cada um que veio aparece no texto como palavras inteiras
        (normalizado, sem acento nem caixa).
    (g) texto com negação ("nao", "nem", "nunca", "sem", "jamais", palavra
        inteira; `forma_pagamento.NEGACAO_RE`) ou pergunta ("?"): incerto.
    Parâmetro fora do schema a gravação ignora.
    """
    from core.handlers import forma_pagamento as fp
    from core.handlers.launches import MOTIVOS_CONFIANTES
    from core.services.ai_chat.tools.launches import _parse_iso_datetime_for_launch
    from core.services.category_service import infer_category
    from core.intent_classifier import VERBOS_DE_SAIDA
    from parsers import RECEITA_START_VERBS, _extract_explicit_category, _extract_valor
    from utils_date import _tz, extract_date_from_text, today_tz
    from utils_text import normalize_text

    texto = (texto_do_usuario or "").strip()
    # Negação ou pergunta ("não gastei 50…", "gastei 50…?"): não é um registro.
    norma = normalize_text(texto)
    if fp.NEGACAO_RE.search(norma) or "jamais" in norma.split() or "?" in texto:
        return False
    data_txt, sem_data = extract_date_from_text(texto)
    sem_data = sem_data or texto

    # (d) data: a gravação usa `args["data"]`; a do texto não chega lá. O dia
    # sai do MESMO parser da gravação: o que se aprova é o que se grava.
    data_ia = args.get("data")
    dia_ia = None                       # vazia: a gravação usa agora (= hoje)
    if data_ia:
        gravado = _parse_iso_datetime_for_launch(data_ia)
        if gravado is None:             # a gravação cairia em "agora": incerto
            return False
        dia_ia = gravado.astimezone(_tz()).date()
    if data_txt is not None:
        if dia_ia != data_txt.date():
            return False
    elif dia_ia not in (None, today_tz()):
        return False

    # (a) valor: EXATAMENTE um número em dígitos e nenhum por extenso (o
    # `_extract_valor` soma "cinquenta … dois" em 52), igual ao da IA.
    try:
        valor_ia = float(args.get("valor") or 0)
    except (TypeError, ValueError):
        return False
    valor_txt = _extract_valor(sem_data)
    if (valor_txt is None or abs(valor_txt - valor_ia) >= 0.005
            or len(_NUMERO_RE.findall(sem_data)) != 1
            or _EXTENSO_RE.search(normalize_text(sem_data))):
        return False

    # (c) tipo por lista positiva: receita só com verbo de receita no começo;
    # despesa só com verbo de saída no começo, ou na forma curta ("mercado 80").
    norm_sem_data = normalize_text(sem_data)
    curta = False
    if norm_sem_data.startswith(RECEITA_START_VERBS):
        tipo_do_texto = "receita"
    elif norm_sem_data.split()[:1] and norm_sem_data.split()[0] in VERBOS_DE_SAIDA:
        tipo_do_texto = "despesa"
    elif _forma_curta(sem_data, args):
        tipo_do_texto, curta = "despesa", True
    else:
        return False
    if str(args.get("tipo") or "").strip().lower() != tipo_do_texto:
        return False

    # (e) forma: declarada pela IA só vale se o texto declara a mesma.
    if not forma_apoiada(args, texto):
        return False

    # (f) alvo e nota: palavras inteiras do texto ("uber" não aprova "taxi" nem
    # casa dentro de "uberlandia").
    texto_norm = f" {normalize_text(texto)} "
    for campo in ("alvo", "nota"):
        bruto = str(args.get(campo) or "").strip()
        if bruto and (not normalize_text(bruto)
                      or f" {normalize_text(bruto)} " not in texto_norm):
            return False

    # (b) categoria: hashtag do usuário, ou regra determinística confiante.
    _, hashtag = _extract_explicit_category(texto)
    nota = str(args.get("nota") or "").strip() or str(args.get("alvo") or "").strip()
    local = infer_category(user_id, nota, None, allow_ai=False)
    if hashtag:
        # A hashtag só garante algo se a IA mandou a mesma categoria e se a
        # regra local da nota não a contradiz (o `add_from_entities` trocaria).
        cat_ia = str(args.get("categoria") or "").strip()
        cat_hashtag = infer_category(user_id, "", hashtag).category
        if not cat_ia or infer_category(user_id, "", cat_ia).category != cat_hashtag:
            return False
        return ((local.reason not in MOTIVOS_CONFIANTES or local.category == cat_hashtag)
                and _categoria_serve(tipo_do_texto, curta, cat_hashtag))
    if local.reason not in MOTIVOS_CONFIANTES:
        return False
    return (infer_category(user_id, texto, None, allow_ai=False).category == local.category
            and _categoria_serve(tipo_do_texto, curta, local.category))


def _decisao_no_whatsapp(user_id: int, args: dict) -> str | None:
    """O `fp.decidir` que o `_add_launch_execute` tomaria, só no WhatsApp com a
    flag e com tipo/valor válidos; None fora disso (a execução nem decide)."""
    from core.handlers import forma_pagamento as fp
    from core.services.ai_chat._context import CURRENT_PLATFORM
    from core.services.ai_chat.tools.launches import _TIPOS_VALIDOS, forma_declarada

    if not ativo(user_id) or CURRENT_PLATFORM.get() != "whatsapp":
        return None
    if str(args.get("tipo") or "").strip().lower() not in _TIPOS_VALIDOS:
        return None
    try:
        if float(args.get("valor") or 0) <= 0:
            return None
    except (TypeError, ValueError):
        return None
    return fp.decidir(user_id, forma_declarada(args))


def precisa_confirmar_lancamento(user_id: int, args: dict) -> bool:
    """`confirmar_se` do `add_launch`: no WhatsApp com a flag, pede "sim"
    antes de uma gravação que aconteceria e não tem certeza."""
    from core.handlers import forma_pagamento as fp
    from core.services.ai_chat._context import CURRENT_USER_MESSAGE

    decisao = _decisao_no_whatsapp(user_id, args)
    texto = CURRENT_USER_MESSAGE.get()
    if decisao == fp.BANCO:
        # A execução não grava (`msg_banco`): com a forma apoiada no texto fica
        # assim; inventada pela IA, o gasto em dinheiro sumiria — confirma.
        return not forma_apoiada(args, texto)
    if decisao not in (fp.CARTEIRA, fp.PERGUNTA):
        return False
    return not lancamento_com_certeza(user_id, args, texto)


def armaria_q40(user_id: int, args: dict) -> bool:
    """`arma_pendencia_no_execute` do `add_launch`: a execução armaria a
    pergunta da forma (Q40, `payment_method_choice`)."""
    from core.handlers import forma_pagamento as fp
    return _decisao_no_whatsapp(user_id, args) == fp.PERGUNTA
