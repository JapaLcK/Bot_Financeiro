# parsers.py
"""
Parsers naturais: interpretam mensagens do usuário em ações estruturadas.
"""

from datetime import date
from utils_date import extract_date_from_text, now_tz
from utils_text import (
    normalize_text,
    contains_word,
    LOCAL_RULES,
    extract_keyword_for_memory,
    parse_money,
)
from db import upsert_category_rule
from ai_router import classify_category_with_gpt  # se for de lá; ajuste se seu nome for outro
from db import get_memorized_category            # se for db; ajuste se estiver em outro arquivo


# --- PARSER DE RECEITA / DESPESA (COLE AQUI) ---
# Faz o parse de uma mensagem natural de receita/despesa e classifica a categoria (com fallback no GPT)
# Faz parse de receita/despesa e usa memória + GPT para categorizar
# Faz parse de receita/despesa, usa memória de categorias e fallback no GPT (e aprende automaticamente)
def parse_receita_despesa_natural(user_id: int, text: str):
    # 0) extrai data do texto (se tiver) e remove do texto
    dt_evento, text_clean = extract_date_from_text(text)
    if dt_evento is None:
        dt_evento = now_tz()

    # normaliza forte (acentos + pontuação)
    raw_norm = normalize_text(text_clean)
    if not raw_norm:
        return None

    valor = parse_money(text_clean)
    if valor is None:
        return None

    verbos_despesa = ["gastei", "paguei", "comprei", "cartao", "cartão", "debitei"]
    verbos_receita = ["recebi", "ganhei", "salario", "salário", "pix recebido", "reembolso"]

    eh_despesa = any(normalize_text(v) in raw_norm for v in verbos_despesa)
    eh_receita = any(normalize_text(v) in raw_norm for v in verbos_receita)

    if not (eh_despesa or eh_receita):
        return None

    tipo = "despesa" if eh_despesa and not eh_receita else "receita" if eh_receita and not eh_despesa else None
    if tipo is None:
        return None

    # 1) memória primeiro (prioridade total)
    categoria = get_memorized_category(user_id, raw_norm)
    if not categoria:
        # 2) regra local
        categoria = "outros"
        for keywords, cat in LOCAL_RULES:
            for kw in keywords:
                kw_norm = normalize_text(kw)
                if kw_norm and (contains_word(raw_norm, kw_norm) or kw_norm in raw_norm):
                    categoria = cat
                    break
            if categoria != "outros":
                break

        # 3) fallback GPT
        if categoria == "outros":
            try:
                categoria_gpt = classify_category_with_gpt(raw_norm)
                if categoria_gpt:
                    categoria = categoria_gpt
            except Exception as e:
                print("Erro IA categoria:", e)
                categoria = "outros"

        # 4) salva memória com keyword (NÃO salva a frase inteira)
        try:
            kw = extract_keyword_for_memory(raw_norm)
            if kw:
                upsert_category_rule(user_id, kw, categoria)
        except Exception as e:
            print("Erro salvando memória categoria:", e)

    return {
        "tipo": tipo,
        "valor": valor,
        "categoria": categoria,
        "nota": raw_norm,
        "criado_em": dt_evento, 
    }

