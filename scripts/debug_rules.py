from dotenv import load_dotenv
load_dotenv(".env")

from db import list_user_category_rules
from utils_text import normalize_text

user_id = 477493407  # <-- o canônico que apareceu no print acima

rules = list_user_category_rules(user_id)
print("Regras do usuário:", rules)

texto_ofx = """
Transferência enviada pelo Pix - MM GROUP LTDA -
61.574.441/0001-91 - COOP CRESOL CONEXÃO Agência: 1912 Conta: 42698-9
"""

texto_norm = normalize_text(texto_ofx)

matched = None
for kw, cat in rules:
    if normalize_text(kw) in texto_norm:
        matched = cat
        break

print("Categoria encontrada:", matched)