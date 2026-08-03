# statement_import.py
"""
Importação de extrato bancário em CSV e PDF.

Complementa o ofx_import.py: nem todo banco exporta OFX com facilidade (e no
celular o usuário quase sempre só tem o PDF do extrato). O fluxo é o mesmo do
OFX — parse → launches → import bulk idempotente — mas a fonte é:

  - CSV: exportado pelo internet banking (Nubank, Inter, BB, genéricos).
    Detecta delimitador (';' ',' tab), linha de cabeçalho e mapeia colunas
    por nome normalizado (data / valor / descrição / crédito+débito / tipo).
  - PDF: extrai o texto com pypdf e reconhece linhas de movimentação no
    padrão brasileiro: data no início + valor monetário (com sufixo C/D
    opcional, ex.: Sicoob "1.234,56D"). PDFs escaneados (imagem) não têm
    texto extraível e são rejeitados com mensagem amigável.

Dedupe: CSV/PDF não têm FITID como o OFX, então o external_id é derivado de
(data, valor, memo, nº da ocorrência no arquivo) — estável entre re-imports
do mesmo período, e o índice de ocorrência preserva compras idênticas no
mesmo dia. Quando o CSV traz um identificador próprio (ex.: Nubank), ele é
usado no lugar do hash.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import Counter
from datetime import datetime, date, time
from decimal import Decimal, InvalidOperation

from utils_date import _tz
from utils_text import normalize_text, INTERNAL_MOVEMENT_CATEGORIES

# Mesmo cap defensivo do OFX (ver nota em ofx_import.py) — alinhado com o
# limite prático dos canais (WhatsApp/Discord) e evita DoS de parser.
MAX_STATEMENT_BYTES = 8 * 1024 * 1024  # 8 MB
MAX_STATEMENT_ROWS = 20_000


class StatementParseError(ValueError):
    """Erro de parse com mensagem já amigável pro usuário final."""


# ─────────────────────────────────────────────────────────────────────────────
# Primitivas de parse (data e valor em formato brasileiro)
# ─────────────────────────────────────────────────────────────────────────────

_DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y")


def parse_br_date(raw: str) -> date | None:
    s = (raw or "").strip()
    if not s:
        return None
    # "02/08/2026 04:58" → só a parte da data
    s = s.split()[0] if s.split() else s
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_br_amount(raw: str) -> Decimal | None:
    """
    Aceita os formatos comuns em extratos brasileiros:
      "1.234,56"  "-1234,56"  "R$ 1.234,56"  "(1.234,56)"  "1.234,56-"
      "1.234,56D" / "1.234,56 C" (sufixo débito/crédito)
      "-1234.56"  "1234.56" (decimal com ponto, ex.: CSV do Nubank)
    Retorna Decimal com sinal (débito negativo) ou None se não parsear.
    """
    s = (raw or "").strip().replace("\xa0", " ")
    if not s:
        return None
    neg = False
    s = re.sub(r"r\$", "", s, flags=re.I).strip()
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1].strip()
    m = re.search(r"([cd])\s*$", s, flags=re.I)
    if m:
        if m.group(1).lower() == "d":
            neg = True
        s = s[: m.start()].strip()
    if s.endswith("-"):
        neg = True
        s = s[:-1].strip()
    if s.startswith("+"):
        s = s[1:].strip()
    if s.startswith("-"):
        neg = True
        s = s[1:].strip()
    s = s.replace(" ", "")
    if not s:
        return None

    if "," in s and "." in s:
        # o separador que aparece por último é o decimal
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif "." in s:
        # "1.234" é milhar; "12.34" é decimal
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
            s = s.replace(".", "")

    try:
        val = Decimal(s)
    except InvalidOperation:
        return None
    return -val if neg else val


# ─────────────────────────────────────────────────────────────────────────────
# CSV
# ─────────────────────────────────────────────────────────────────────────────

def _decode_bytes(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _is_date_header(c: str) -> bool:
    return c in {"data", "date", "dia", "dt"} or (c.startswith("data") and "valor" not in c)


def _is_value_header(c: str) -> bool:
    return c in {"amount", "value", "montante"} or c.startswith("valor")


def _is_desc_header(c: str) -> bool:
    bases = (
        "descricao", "description", "historico", "lancamento", "titulo",
        "title", "memo", "detalhe", "movimentacao", "observacao", "estabelecimento",
    )
    return any(c == b or c.startswith(b) for b in bases)


def _is_id_header(c: str) -> bool:
    return c in {"identificador", "id", "id transacao", "codigo da transacao", "codigo"}


def _is_balance_header(c: str) -> bool:
    return c.startswith("saldo")


def _is_credit_header(c: str) -> bool:
    return "credito" in c or c in {"entrada", "entradas", "deposito", "depositos"}


def _is_debit_header(c: str) -> bool:
    return "debito" in c or c in {"saida", "saidas", "retirada", "retiradas"}


def _is_type_header(c: str) -> bool:
    return c.startswith("tipo") or c in {"natureza", "operacao"}


_DEBIT_TYPE_VALUES = {"d", "debito", "debit", "saida", "pagamento", "despesa", "compra"}
_CREDIT_TYPE_VALUES = {"c", "credito", "credit", "entrada", "deposito", "receita"}


def _find_csv_header(text: str) -> tuple[str, int, list[str]] | None:
    """
    Procura a linha de cabeçalho nas primeiras linhas do arquivo (extratos do
    Inter, por exemplo, têm um preâmbulo antes da tabela). Retorna
    (delimitador, índice da linha, células normalizadas) ou None.
    """
    lines = text.splitlines()
    for idx, line in enumerate(lines[:30]):
        if not line.strip():
            continue
        for delim in (";", ",", "\t"):
            if delim not in line:
                continue
            try:
                cells = next(csv.reader([line], delimiter=delim))
            except StopIteration:
                continue
            norm = [normalize_text(c) for c in cells]
            has_date = any(_is_date_header(c) for c in norm)
            has_value = any(_is_value_header(c) for c in norm)
            has_credit_debit = any(_is_credit_header(c) for c in norm) and any(
                _is_debit_header(c) for c in norm
            )
            if has_date and (has_value or has_credit_debit):
                return delim, idx, norm
    return None


def parse_csv_statement(data: bytes) -> list[dict]:
    """
    Parseia um CSV de extrato bancário e retorna transações:
      [{"posted_at": date, "amount": Decimal (assinado), "memo": str,
        "external_id": str | None}, ...]
    Lança StatementParseError com mensagem amigável quando não reconhece.
    """
    text = _decode_bytes(data)

    header = _find_csv_header(text)
    if header is None:
        raise StatementParseError(
            "Não reconheci as colunas desse CSV. Extratos precisam ter pelo "
            "menos uma coluna de data e uma de valor (ex.: Data, Descrição, Valor)."
        )
    delim, header_idx, cells = header

    # Fatura de cartão do Nubank (date,title,amount): não é extrato de conta —
    # importar como conta corrente duplicaria os gastos do cartão.
    if cells[:3] == ["date", "title", "amount"]:
        raise StatementParseError(
            "Esse CSV parece ser uma fatura de cartão de crédito (formato Nubank). "
            "Para faturas, use o arquivo OFX exportado pelo app do cartão."
        )

    date_col = next((i for i, c in enumerate(cells) if _is_date_header(c)), None)
    value_col = next((i for i, c in enumerate(cells) if _is_value_header(c)), None)
    credit_col = next((i for i, c in enumerate(cells) if _is_credit_header(c)), None)
    debit_col = next((i for i, c in enumerate(cells) if _is_debit_header(c)), None)
    id_col = next((i for i, c in enumerate(cells) if _is_id_header(c)), None)
    type_col = next((i for i, c in enumerate(cells) if _is_type_header(c)), None)
    desc_cols = [
        i for i, c in enumerate(cells)
        if _is_desc_header(c) and not _is_balance_header(c)
    ]

    body = "\n".join(text.splitlines()[header_idx + 1:])
    rows = csv.reader(io.StringIO(body), delimiter=delim)

    txs: list[dict] = []
    for row in rows:
        if len(txs) >= MAX_STATEMENT_ROWS:
            raise StatementParseError(
                f"CSV com transações demais (máx {MAX_STATEMENT_ROWS})."
            )
        if not row or all(not c.strip() for c in row):
            continue

        def cell(i: int | None) -> str:
            return row[i].strip() if i is not None and i < len(row) else ""

        posted_at = parse_br_date(cell(date_col))
        if posted_at is None:
            continue  # "SALDO ANTERIOR", subtotais, rodapés etc.

        amount: Decimal | None = None
        if value_col is not None:
            amount = parse_br_amount(cell(value_col))
        if amount is None and (credit_col is not None or debit_col is not None):
            credit = parse_br_amount(cell(credit_col))
            debit = parse_br_amount(cell(debit_col))
            if credit:
                amount = abs(credit)
            elif debit:
                amount = -abs(debit)
        if amount is None or amount == 0:
            continue

        # Coluna "Tipo" (D/C, Débito/Crédito) define o sinal quando o valor
        # vem sem sinal no CSV.
        tipo_raw = normalize_text(cell(type_col))
        if tipo_raw and amount > 0 and tipo_raw in _DEBIT_TYPE_VALUES:
            amount = -amount

        memo = " ".join(c for c in (cell(i) for i in desc_cols) if c).strip()
        if not memo:
            memo = "(sem descrição)"

        ext_id = cell(id_col) or None

        txs.append(
            {"posted_at": posted_at, "amount": amount, "memo": memo, "external_id": ext_id}
        )

    return txs


# ─────────────────────────────────────────────────────────────────────────────
# PDF
# ─────────────────────────────────────────────────────────────────────────────

# Data no início da linha de movimentação: 02/08/2026, 02/08/26, 02-08-2026
_PDF_LINE_DATE_RE = re.compile(r"^\s*(\d{2}[/.-]\d{2}[/.-]\d{2,4})\b")

# Valor monetário brasileiro, com sufixo C/D opcional (Sicoob: "1.234,56D").
# Exige decimal com vírgula pra não confundir com horários/nº de documento.
_PDF_AMOUNT_RE = re.compile(
    r"(?<![\d,.])"
    r"(-?\s?(?:R\$\s?)?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2})(?!\d)"
    r"(?:\s?([CDcd])(?![A-Za-z0-9]))?"
)

# Linhas de saldo (SALDO ANTERIOR, SALDO DO DIA, SALDO BLOQUEADO...) não são
# movimentações — importá-las duplicaria valores.
_PDF_SKIP_RE = re.compile(r"\bsaldo\b", re.I)

# ── Layout Nubank ────────────────────────────────────────────────────────────
# O extrato PDF do Nubank não tem "data + valor na mesma linha": a data é um
# cabeçalho de dia ("01 AGO 2026 Total de entradas + 450,00"), as transações
# vêm abaixo com a descrição em uma ou mais linhas e o valor SEM SINAL no fim
# do bloco. O sinal vem da seção corrente ("Total de entradas"/"Total de
# saídas") ou, na falta dela, de palavras-chave da descrição.

_PT_MONTHS = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}

_NU_DAY_RE = re.compile(
    r"^\s*(\d{1,2})\s+(JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)\s+(\d{4})\b(.*)$",
    re.I,
)

# valor no FIM da linha (fecha um bloco de transação)
_NU_AMOUNT_END_RE = re.compile(
    r"(?:R\$\s?)?((?:\d{1,3}(?:\.\d{3})+|\d+),\d{2})\s*$"
)

# rodapé/cabeçalho de página do Nubank — nunca é descrição de transação
_NU_NOISE_PREFIXES = (
    "o saldo liquido", "nao nos responsabilizamos", "asseguramos a autenticidade",
    "nu financeira", "nu pagamentos", "cnpj", "tem alguma duvida",
    "caso a solucao", "extrato gerado", "saldo inicial", "saldo final",
    "rendimento liquido",
)
_NU_NOISE_CONTAINS = ("valores em r", "cpf agencia conta", "ouvidoria")

# "Saldo final do período R$ 586,00" — usado pra reconciliar o saldo da conta
_NU_FINAL_BALANCE_RE = re.compile(
    r"saldo\s+final\s+do\s+per[íi]odo\s*\n?\s*R\$\s*(-?\s?[\d.,]+)",
    re.I,
)

_INFER_CREDIT_KEYWORDS = ("recebid", "estorno", "rendimento", "deposito", "reembolso", "resgate")
_INFER_DEBIT_KEYWORDS = (
    "enviad", "pagamento", "compra", "aplicacao", "debito", "boleto",
    "tarifa", "saque", "recarga",
)


def _infer_sign(desc_norm: str) -> int:
    for kw in _INFER_CREDIT_KEYWORDS:
        if kw in desc_norm:
            return 1
    for kw in _INFER_DEBIT_KEYWORDS:
        if kw in desc_norm:
            return -1
    return -1  # sem pista, assume saída (maioria das movimentações)


def _is_nubank_noise(line_norm: str) -> bool:
    return any(line_norm.startswith(p) for p in _NU_NOISE_PREFIXES) or any(
        c in line_norm for c in _NU_NOISE_CONTAINS
    )


def looks_like_nubank_statement(text: str) -> bool:
    has_day_header = any(_NU_DAY_RE.match(l.strip()) for l in text.splitlines())
    return has_day_header and "movimentacoes" in normalize_text(text)


def parse_nubank_pdf_text(text: str) -> list[dict]:
    """Parseia o texto do extrato PDF do Nubank (layout em blocos por dia)."""
    txs: list[dict] = []
    cur_date: date | None = None
    section = 0  # +1 entradas, -1 saídas, 0 desconhecida
    desc_parts: list[str] = []
    started = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line_norm = normalize_text(line)

        if not started:
            if line_norm == "movimentacoes":
                started = True
            continue

        day_m = _NU_DAY_RE.match(line)
        if day_m:
            d, mon, y = int(day_m.group(1)), day_m.group(2).lower(), int(day_m.group(3))
            try:
                cur_date = date(y, _PT_MONTHS[mon], d)
            except ValueError:
                cur_date = None
            desc_parts = []
            rest_norm = normalize_text(day_m.group(4))
            if "total de entradas" in rest_norm:
                section = 1
            elif "total de saidas" in rest_norm:
                section = -1
            else:
                section = 0
            continue

        # troca de seção dentro do mesmo dia ("Total de saídas - 450,00")
        if line_norm.startswith("total de entradas"):
            section = 1
            desc_parts = []
            continue
        if line_norm.startswith("total de saidas"):
            section = -1
            desc_parts = []
            continue

        if _is_nubank_noise(line_norm):
            desc_parts = []
            continue

        amount_m = _NU_AMOUNT_END_RE.search(line)
        if amount_m and cur_date is not None:
            valor = parse_br_amount(amount_m.group(1))
            if valor is None or valor == 0:
                desc_parts = []
                continue
            desc = " ".join(desc_parts + [line[: amount_m.start()].strip()])
            desc = re.sub(r"\s{2,}", " ", desc).strip()[:200] or "(sem descrição)"
            sign = section or _infer_sign(normalize_text(desc))
            txs.append(
                {
                    "posted_at": cur_date,
                    "amount": abs(valor) * sign,
                    "memo": desc,
                    "external_id": None,
                }
            )
            desc_parts = []
            if len(txs) > MAX_STATEMENT_ROWS:
                raise StatementParseError(
                    f"PDF com transações demais (máx {MAX_STATEMENT_ROWS})."
                )
            continue

        # linha de descrição (transação pode quebrar em várias linhas)
        if cur_date is not None:
            desc_parts.append(line)

    return txs


def extract_pdf_final_balance(text: str) -> Decimal | None:
    """Extrai o "Saldo final do período" (Nubank) pra reconciliar o saldo da
    conta, como o OFX faz com o LEDGERBAL. Retorna None se não existir."""
    m = _NU_FINAL_BALANCE_RE.search(text)
    if not m:
        return None
    return parse_br_amount(m.group(1))


def _extract_pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # tenta senha vazia (alguns bancos "criptografam" sem senha real)
            try:
                if not reader.decrypt(""):
                    raise StatementParseError(
                        "Esse PDF está protegido por senha. Remova a senha "
                        "(ou exporte o extrato em OFX/CSV) e envie de novo."
                    )
            except StatementParseError:
                raise
            except Exception:
                raise StatementParseError(
                    "Esse PDF está protegido por senha. Remova a senha "
                    "(ou exporte o extrato em OFX/CSV) e envie de novo."
                )
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except StatementParseError:
        raise
    except PdfReadError as e:
        raise StatementParseError(f"Não consegui abrir esse PDF ({e}).")


def parse_pdf_statement(data: bytes) -> list[dict]:
    """Extrai transações de um PDF de extrato (texto, não escaneado)."""
    return parse_pdf_statement_text(_extract_pdf_text(data))


def parse_pdf_statement_text(text: str) -> list[dict]:
    """Despacha entre os layouts conhecidos de extrato em PDF."""
    if looks_like_nubank_statement(text):
        txs = parse_nubank_pdf_text(text)
        if txs:
            return txs
    return _parse_generic_pdf_text(text)


def _parse_generic_pdf_text(text: str) -> list[dict]:
    """
    Layout genérico (Sicoob, Itaú, BB...): data no início da linha + pelo
    menos um valor monetário na mesma linha. Quando a linha tem mais de um
    valor (colunas valor + saldo), o primeiro é o valor da movimentação e o
    último é o saldo. Linhas seguintes sem data/valor são continuação da
    descrição.
    """
    txs: list[dict] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        date_m = _PDF_LINE_DATE_RE.match(line)
        amounts = list(_PDF_AMOUNT_RE.finditer(line))

        if not date_m:
            # continuação de descrição da movimentação anterior
            if txs and not amounts and not _PDF_SKIP_RE.search(line):
                prev = txs[-1]
                if len(prev["memo"]) < 160:
                    prev["memo"] = f"{prev['memo']} {line}".strip()[:200]
            continue

        if not amounts:
            continue  # linha de cabeçalho/período com data mas sem valor
        if _PDF_SKIP_RE.search(line):
            continue  # SALDO ANTERIOR / SALDO DO DIA etc.

        posted_at = parse_br_date(date_m.group(1))
        if posted_at is None:
            continue

        # 1 valor → é a movimentação; 2+ → primeiro é o valor, último o saldo
        num_raw, suffix = amounts[0].group(1), amounts[0].group(2)
        amount = parse_br_amount(f"{num_raw}{suffix or ''}")
        if amount is None or amount == 0:
            continue

        desc = line[date_m.end():amounts[0].start()].strip()
        # se o valor vem antes da descrição na linha, pega o que sobra depois
        if not desc:
            desc = line[amounts[-1].end():].strip()
        desc = re.sub(r"\s{2,}", " ", desc) or "(sem descrição)"

        txs.append(
            {"posted_at": posted_at, "amount": amount, "memo": desc, "external_id": None}
        )
        if len(txs) > MAX_STATEMENT_ROWS:
            raise StatementParseError(
                f"PDF com transações demais (máx {MAX_STATEMENT_ROWS})."
            )

    return txs


# ─────────────────────────────────────────────────────────────────────────────
# Import (mesmo pipeline idempotente do OFX)
# ─────────────────────────────────────────────────────────────────────────────

def build_statement_external_id(
    posted_at: date, amount: Decimal, memo_norm: str, occurrence: int
) -> str:
    """external_id determinístico pra dedupe entre re-imports. O índice de
    ocorrência diferencia compras idênticas no mesmo dia dentro do arquivo."""
    payload = f"{posted_at.isoformat()}|{amount}|{memo_norm}|{occurrence}"
    return "stmt:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def import_statement_bytes(
    user_id: int, data: bytes, filename: str | None, kind: str
) -> dict:
    """
    Lê um extrato CSV ('csv') ou PDF ('pdf'), transforma em launches e chama
    o mesmo bulk idempotente do OFX. Retorna o report pro bot responder.
    """
    # imports tardios: mantém os parsers utilizáveis/testáveis sem DB
    from db import import_ofx_launches_bulk
    from ofx_import import load_user_rules_norm, resolve_category

    if kind not in {"csv", "pdf"}:
        raise ValueError(f"kind inválido: {kind!r}")
    if not data:
        raise StatementParseError("Arquivo vazio.")
    if len(data) > MAX_STATEMENT_BYTES:
        raise StatementParseError(
            f"Arquivo muito grande (máx {MAX_STATEMENT_BYTES // (1024 * 1024)} MB)."
        )

    ledger_balance: Decimal | None = None
    if kind == "csv":
        txs = parse_csv_statement(data)
    else:
        pdf_text = _extract_pdf_text(data)
        txs = parse_pdf_statement_text(pdf_text)
        # extratos que informam o saldo final (Nubank) permitem reconciliar
        ledger_balance = extract_pdf_final_balance(pdf_text)

    if not txs:
        tipo_arquivo = "CSV" if kind == "csv" else "PDF"
        raise StatementParseError(
            f"Não encontrei transações nesse {tipo_arquivo}. "
            "Se for um extrato escaneado (imagem), tente exportar o arquivo "
            "OFX ou CSV pelo app do banco."
        )

    file_hash = hashlib.sha256(data).hexdigest()
    tz = _tz()

    rules_norm = load_user_rules_norm(user_id)

    launches_rows: list[dict] = []
    occurrences: Counter[tuple] = Counter()
    min_d: date | None = None
    max_d: date | None = None

    for trn in txs:
        posted_at: date = trn["posted_at"]
        amount: Decimal = trn["amount"]
        memo: str = trn["memo"]

        if min_d is None or posted_at < min_d:
            min_d = posted_at
        if max_d is None or posted_at > max_d:
            max_d = posted_at

        if amount < 0:
            tipo = "despesa"
            valor = -amount
            delta = -valor
        else:
            tipo = "receita"
            valor = amount
            delta = +valor

        memo_norm = normalize_text(memo)
        categoria = resolve_category(memo_norm, rules_norm)
        is_internal = normalize_text(categoria) in INTERNAL_MOVEMENT_CATEGORIES

        if trn.get("external_id"):
            external_id = f"stmt:{trn['external_id']}"
        else:
            key = (posted_at, amount, memo_norm)
            external_id = build_statement_external_id(
                posted_at, amount, memo_norm, occurrences[key]
            )
            occurrences[key] += 1

        launches_rows.append(
            {
                "tipo": tipo,
                "valor": valor,
                "delta": delta,
                "categoria": categoria,
                "nota": memo,
                "external_id": external_id,
                "posted_at": posted_at,
                "criado_em": datetime.combine(posted_at, time(12, 0), tzinfo=tz),
                "currency": "BRL",
                "is_internal_movement": is_internal,
                "ofx_meta": {
                    "memo": memo,
                    "amount_signed": float(amount),
                    "posted_at": posted_at.isoformat(),
                    "filename": filename,
                    "source": kind,
                },
            }
        )

    result = import_ofx_launches_bulk(
        user_id,
        launches_rows,
        file_hash=file_hash,
        bank_id=None,
        acct_id=None,
        acct_type=f"statement_{kind}",
        dt_start=min_d,
        dt_end=max_d,
    )

    result["filename"] = filename
    result["source"] = kind
    result["ledger_balance"] = ledger_balance

    # Reconciliação (mesma regra do OFX): só quando o arquivo informa o saldo
    # final E este é o extrato mais recente já importado. Sem saldo no arquivo,
    # o saldo da conta fica ajustado só pelos deltas inseridos (feito no bulk).
    can_reconcile = False
    try:
        from db import set_balance, get_last_ofx_import_end_date

        last_dt_end = get_last_ofx_import_end_date(user_id)
        if ledger_balance is not None and max_d is not None:
            if last_dt_end is None or max_d >= last_dt_end:
                can_reconcile = True
    except Exception:
        can_reconcile = False

    if can_reconcile:
        result["new_balance"] = set_balance(user_id, ledger_balance)
        result["reconciled"] = True
    else:
        result["reconciled"] = False

    return result
