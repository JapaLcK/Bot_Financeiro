"""
db/investments.py — Investimentos: criar, aportar, resgatar, juros e CDI.
"""
import calendar
import logging
import sys
import requests
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from psycopg.types.json import Jsonb
import psycopg

from utils_date import _tz, is_br_business_day

from .connection import get_conn
from .users import ensure_user

logger = logging.getLogger(__name__)
_warned_bcb_requests: set[tuple] = set()

# Cache-primeiro nas taxas do BCB (CLAUDE.md §5 — caminho do dashboard):
# série DIÁRIA é fresca enquanto não existe dia útil BR publicável depois do
# ponto mais novo (sexta cobre o fim de semana e feriadão; a segunda publicada
# invalida — regra de calendário, não de "N dias corridos", senão o accrual
# ficava até 2 dias úteis atrás servindo cache "fresco"). IPCA_12M é mensal
# com ~2 semanas de atraso: frescor por idade + memo de confirmação.
# ponytail: dia já cacheado é FINAL — revisão retroativa do BCB é ignorada;
# se um dia precisar ser recarregado, apague a linha de market_rates à mão.
SGS_MONTHLY_FRESH_DAYS = 45
# Timeout das chamadas SGS. Era 20 s; com cache-primeiro a rede só entra em
# cache frio, e 20 s pendurados seguravam o /data do dashboard inteiro.
SGS_TIMEOUT_SECONDS = 3

# ── Máquina de estados do cache de séries SGS (mapas diários) ────────────────
# cobertura = todo dia útil BR de [start, max(cache)] no cache (_sgs_cache_covers)
# cauda     = nenhum dia útil publicável em (max(cache), end] (_sgs_tail_is_fresh)
# memo      = a rede respondeu, e a resposta ainda vale, sobre um intervalo que
#             CONTÉM o que eu preciso perguntar (_sgs_answered_covers)
#
#  célula                                          → ação (o que o código faz)
#  1 cobertura FURADA (cabeça/meio)                → fetch da janela INTEIRA,
#    sempre; memo NUNCA consultado — dinheiro não depende dele
#  2 cobertura ok + cauda completa                 → 0 fetch (inclui end==newest
#    e ref futura: cauda vazia é completa). Nada é adiado: não há dia publicável.
#  3 cobertura ok + cauda incompleta + memo cobrindo (newest, end] → 0 fetch.
#    RESSALVA: se o BCB publicar dentro da validade do memo, esse dia entra na
#    próxima chamada após o vencimento — defere ≤2h (parcial/vazia) ou até a
#    meia-noite UTC (resposta completa), NUNCA perde: o accrual só avança
#    last_date até o último dia que ele viu.
#  4 cobertura ok + cauda incompleta + memo não cobre → fetch [newest+1, end]:
#    a dados que COMPLETAM a cauda → upsert; memo vale até a meia-noite UTC
#    b dados PARCIAIS ou vazia (pré-publicação) → upsert o que veio; memo vale
#      SGS_CONFIRM_SHORT — pega o ponto publicado à noite ainda no mesmo dia
#    c falha/timeout (None) ou 200+lixo (None) → NÃO grava memo; re-tenta já
#  5 cache VAZIO na janela → 0 fetch só se o memo cobre [start, end]; senão
#    fetch da janela e grava como a célula 4. Mesma RESSALVA da célula 3.
#    Instância real: manhã de segunda com lote acruado na sexta (janela
#    [sáb, seg] vazia até o BCB publicar) — sem a célula era 1 fetch por
#    abertura (~318 ms cada), o dia inteiro.
#  get_latest_*: mesma máquina, perguntando por (ref_date, hoje]; o /ultimos/N
#  responde sobre [ponto mais novo, hoje] e é isso que fica no memo. Na mensal
#  (IPCA_12M) o frescor é por idade (≤45d).
#
# Todo memo é ESCOPADO ao intervalo que a rede respondeu — um memo global
# ("não há ponto mais novo") parece equivalente e não é: ele cala uma janela
# histórica que nunca foi consultada. Isso já mordeu duas vezes (Codex-6 na
# janela vazia, e o mesmo padrão no memo de cauda), então existe UMA estrutura
# só e ela sempre carrega o intervalo.
#
# Sem memo, série sem ponto novo (IPCA_12M passa 40-70 dias com o mesmo
# ref_date; CDI parado num feriadão) pagaria um fetch idêntico A CADA chamada.
# ponytail: memo por processo (reseta no restart, não cruza workers). Teto de
# rede por série/dia/processo: 1 fetch em dia já publicado; ≤ 24h/2h = 12 em
# dia pré-publicação com tráfego contínuo (típico ≤ 6). Banco, se incomodar.
SGS_CONFIRM_SHORT = timedelta(hours=2)
# code → (validade, start, end) do último intervalo que a rede respondeu.
_sgs_answered: dict[str, tuple[datetime, date, date]] = {}


def _sgs_remember(code: str, start: date, end: date, *, complete: bool) -> None:
    """Guarda que a rede respondeu sobre [start, end]. `complete` = a resposta
    fechou a cauda pedida (vale até a meia-noite UTC); parcial/vazia vale
    SGS_CONFIRM_SHORT, para pegar a publicação do fim do dia."""
    now = datetime.now(timezone.utc)
    if complete:
        until = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        until = now + SGS_CONFIRM_SHORT
    _sgs_answered[code] = (until, start, end)


def _sgs_answered_covers(code: str, start: date, end: date) -> bool:
    """A rede já respondeu, e a resposta ainda vale, sobre um intervalo que
    CONTÉM [start, end]? Só então dá para pular o fetch."""
    a = _sgs_answered.get(code)
    return bool(a) and datetime.now(timezone.utc) < a[0] and a[1] <= start and end <= a[2]




# ──────────────────────────────────────────────────────────────────────────────
# Helpers de dias úteis e datas
# ──────────────────────────────────────────────────────────────────────────────

def _business_days_between(d1: date, d2: date) -> int:
    """
    Dias úteis entre d1 (exclusive) e d2 (inclusive), considerando seg-sex
    e feriados nacionais brasileiros (calendário ANBIMA aproximado).
    """
    if d2 <= d1:
        return 0
    days = 0
    cur = d1
    while cur < d2:
        cur = cur.fromordinal(cur.toordinal() + 1)
        if is_br_business_day(cur):
            days += 1
    return days


def _fmt_ddmmyyyy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _warn_bcb_once(key: tuple, message: str, *args) -> None:
    if key in _warned_bcb_requests:
        return
    _warned_bcb_requests.add(key)
    logger.warning(message, *args)


def _is_sgs_no_values_payload(payload) -> bool:
    if not isinstance(payload, dict):
        return False

    error = payload.get("erro")
    if not isinstance(error, dict):
        return False

    detail = str(error.get("detail") or "")
    return "Value(s) not found" in detail


def _decode_sgs_response(r: requests.Response, series_code: int, *, context: tuple) -> list[dict] | None:
    """None = payload não-decodificável/inesperado (trata-se como falha: não
    alimenta o memo _sgs_answered, re-tenta na próxima chamada);
    [] = resposta legítima "sem valores no período" (confirma o memo)."""
    try:
        payload = r.json()
    except Exception as e:
        _warn_bcb_once(
            (*context, "invalid_json", type(e).__name__, str(e)),
            "Resposta inválida da série SGS %s no BCB: %s",
            series_code,
            e,
        )
        return None

    if isinstance(payload, list):
        return payload

    if _is_sgs_no_values_payload(payload):
        logger.info("Sem valores publicados para série SGS %s no período consultado.", series_code)
        return []

    _warn_bcb_once(
        (*context, "unexpected_payload", str(payload)[:200]),
        "Resposta inesperada da série SGS %s no BCB: %s",
        series_code,
        payload,
    )
    return None


# ──────────────────────────────────────────────────────────────────────────────
# CDI — BCB SGS
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_sgs_series_json(series_code: int, start: date, end: date) -> list[dict] | None:
    """None = falha (rede/HTTP/timeout OU 200 com payload lixo — nada disso
    confirma o memo, re-tenta na próxima chamada); lista = resposta do BCB
    (vazia inclusive — "sem valores no período" é resposta, e alimenta o memo
    _sgs_answered)."""
    url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{series_code}/dados"
    params = {
        "formato": "json",
        "dataInicial": _fmt_ddmmyyyy(start),
        "dataFinal": _fmt_ddmmyyyy(end),
    }
    try:
        r = requests.get(url, params=params, timeout=SGS_TIMEOUT_SECONDS)
        if r.status_code == 404:
            data = _decode_sgs_response(r, series_code, context=("fetch_sgs_series_json", series_code, start, end))
            if data or _is_sgs_no_values_payload(r.json()):
                return data
        r.raise_for_status()
        return _decode_sgs_response(r, series_code, context=("fetch_sgs_series_json", series_code, start, end))
    except Exception as e:
        _warn_bcb_once(
            ("fetch_sgs_series_json", series_code, start, end, type(e).__name__, str(e)),
            "Falha ao buscar série SGS %s no BCB entre %s e %s: %s",
            series_code,
            start.isoformat(),
            end.isoformat(),
            e,
        )
        return None


def _fetch_sgs_latest_json(series_code: int, limit: int = 15) -> list[dict] | None:
    url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{series_code}/dados/ultimos/{limit}"
    params = {"formato": "json"}
    try:
        r = requests.get(url, params=params, timeout=SGS_TIMEOUT_SECONDS)
        if r.status_code == 404:
            data = _decode_sgs_response(r, series_code, context=("fetch_sgs_latest_json", series_code, limit))
            if data or _is_sgs_no_values_payload(r.json()):
                return data
        r.raise_for_status()
        return _decode_sgs_response(r, series_code, context=("fetch_sgs_latest_json", series_code, limit))
    except Exception as e:
        _warn_bcb_once(
            ("fetch_sgs_latest_json", series_code, limit, type(e).__name__, str(e)),
            "Falha ao buscar últimos valores da série SGS %s no BCB: %s",
            series_code,
            e,
        )
        return None  # mesmo contrato da _fetch_sgs_series_json: None = falha


def _sgs_tail_is_fresh(newest: date, end: date) -> bool:
    """Cauda completa: nenhum dia útil BR publicável em (newest, end].
    Sexta→domingo é fresco (0 fetch); sexta→segunda já não é."""
    d = newest + timedelta(days=1)
    while d <= end:
        if is_br_business_day(d):
            return False
        d += timedelta(days=1)
    return True


def _sgs_cache_covers(cached: dict[date, float], start: date, newest: date) -> bool:
    """True se TODO dia útil BR de [start, newest] está no cache.

    Buraco na CABEÇA ou no MEIO é juro não computado: o accrual avança
    last_date por cima dos dias faltantes e o rendimento some sem retentativa
    (dinheiro exibido). Cobertura furada ⇒ a janela inteira vai à rede
    (comportamento antigo), que cura o buraco. Falso-negativo possível: dia
    que o nosso calendário chama de útil mas o BCB não publica vira fetch da
    janela toda — correto, só não otimizado."""
    d = start
    while d <= newest:
        if is_br_business_day(d) and d not in cached:
            return False
        d += timedelta(days=1)
    return True


def _get_cdi_daily_map(cur, start: date, end: date) -> dict[date, float]:
    """
    Retorna {date: cdi_percent_per_day}.
    Usa cache em market_rates e busca do BCB o que estiver faltando.
    """
    if end <= start:
        return {}

    cur.execute(
        "select ref_date, value from market_rates "
        "where code='CDI' and ref_date >= %s and ref_date <= %s order by ref_date",
        (start, end),
    )
    cached = {row["ref_date"]: float(row["value"]) for row in cur.fetchall()}

    fetch_start = start
    if cached:
        newest = max(cached)
        if _sgs_cache_covers(cached, start, newest):
            fetch_start = newest + timedelta(days=1)  # só a cauda falta
            if _sgs_tail_is_fresh(newest, end) or \
                    _sgs_answered_covers("CDI", fetch_start, end):
                return cached  # cauda completa, ou a rede já respondeu por ela
        # cobertura furada: fetch_start segue em `start` (janela inteira),
        # e o memo NÃO é consultado — dinheiro não depende dele.
    elif _sgs_answered_covers("CDI", start, end):
        return cached  # célula 5: a rede já respondeu por ESTA janela

    data = _fetch_sgs_series_json(12, fetch_start, end)
    if not isinstance(data, list) or not data:
        if isinstance(data, list):  # []: "sem valores" pré-publicação → curto
            _sgs_remember("CDI", fetch_start, end, complete=False)
        return cached

    to_upsert = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            raw_date = item.get("data")
            raw_val = item.get("valor")
            if not raw_date or raw_val is None:
                continue
            d = datetime.strptime(raw_date, "%d/%m/%Y").date()
            v = float(str(raw_val).replace(",", "."))
            if d not in cached:
                to_upsert.append((d, v))
            cached[d] = v
        except Exception as e:
            _warn_bcb_once(
                ("invalid_bcb_item", str(item), type(e).__name__, str(e)),
                "Item inválido do BCB ignorado: %s | erro=%s",
                item,
                e,
            )

    if to_upsert:
        cur.executemany(
            "insert into market_rates(code, ref_date, value) values ('CDI', %s, %s) "
            "on conflict (code, ref_date) do update set value=excluded.value",
            to_upsert,
        )

    # Célula 4a×4b: resposta completou a cauda ⇒ dia cheio; parcial ⇒ curto.
    _sgs_remember("CDI", fetch_start, end,
                  complete=bool(cached) and _sgs_tail_is_fresh(max(cached), end))
    return cached


def _get_sgs_daily_map(cur, code: str, series_code: int, start: date, end: date) -> dict[date, float]:
    """Retorna {date: percent_per_day} para séries SGS diárias, com cache em market_rates."""
    if end <= start:
        return {}

    cur.execute(
        "select ref_date, value from market_rates "
        "where code=%s and ref_date >= %s and ref_date <= %s order by ref_date",
        (code, start, end),
    )
    cached = {row["ref_date"]: float(row["value"]) for row in cur.fetchall()}

    fetch_start = start
    if cached:
        newest = max(cached)
        if _sgs_cache_covers(cached, start, newest):
            fetch_start = newest + timedelta(days=1)  # só a cauda falta
            if _sgs_tail_is_fresh(newest, end) or \
                    _sgs_answered_covers(code, fetch_start, end):
                return cached  # cauda completa, ou a rede já respondeu por ela
        # cobertura furada: fetch_start segue em `start` (janela inteira), e o
        # memo NÃO é consultado. (Séries mensais nunca "cobrem" dias úteis ⇒
        # sempre janela inteira — comportamento antigo, correto.)
    elif _sgs_answered_covers(code, start, end):
        return cached  # célula 5: a rede já respondeu por ESTA janela

    data = _fetch_sgs_series_json(series_code, fetch_start, end)
    if not isinstance(data, list) or not data:
        if isinstance(data, list):  # []: "sem valores" pré-publicação → curto
            _sgs_remember(code, fetch_start, end, complete=False)
        return cached

    to_upsert = []
    for item in data:
        try:
            d = datetime.strptime(item["data"], "%d/%m/%Y").date()
            v = float(str(item["valor"]).replace(",", "."))
            if d not in cached:
                to_upsert.append((code, d, v))
            cached[d] = v
        except Exception as e:
            _warn_bcb_once(
                ("invalid_sgs_daily_item", code, str(item), type(e).__name__, str(e)),
                "Item inválido do SGS %s ignorado: %s | erro=%s",
                code,
                item,
                e,
            )

    if to_upsert:
        cur.executemany(
            "insert into market_rates(code, ref_date, value) values (%s, %s, %s) "
            "on conflict (code, ref_date) do update set value=excluded.value",
            to_upsert,
        )

    # Célula 4a×4b: resposta completou a cauda ⇒ dia cheio; parcial ⇒ curto.
    _sgs_remember(code, fetch_start, end,
                  complete=bool(cached) and _sgs_tail_is_fresh(max(cached), end))
    return cached


def _get_sgs_monthly_map(cur, code: str, series_code: int, start: date, end: date) -> dict[date, float]:
    """Retorna {ref_date: percent_per_month} para séries SGS mensais, com cache local."""
    return _get_sgs_daily_map(cur, code, series_code, start, end)


def _get_selic_daily_map(cur, start: date, end: date) -> dict[date, float]:
    """Taxa SELIC diária (% a.d.) no SGS/BCB (série 11)."""
    return _get_sgs_daily_map(cur, "SELIC_DAILY", 11, start, end)


def _get_ipca_monthly_map(cur, start: date, end: date) -> dict[date, float]:
    """IPCA mensal (% a.m.) no SGS/BCB (série 433)."""
    return _get_sgs_monthly_map(cur, "IPCA_MONTHLY", 433, start, end)


def _parse_sgs_latest(data, *, series: str) -> tuple[date, float] | None:
    """Extrai o (ref_date, valor) mais recente de uma resposta SGS em JSON.

    Item malformado é tolerado um a um; mas se a resposta veio com itens e
    NENHUM parseou (formato da API mudou?), loga warning — senão a taxa fica
    stale no cache local pra sempre, sem ninguém perceber.
    """
    latest: tuple[date, float] | None = None
    for item in data or []:
        try:
            d = datetime.strptime(item["data"], "%d/%m/%Y").date()
            v = float(str(item["valor"]).replace(",", "."))
            if latest is None or d > latest[0]:
                latest = (d, v)
        except Exception:
            continue
    if data and latest is None:
        logger.warning(
            "SGS %s: resposta com %d item(ns) mas nenhum parseou — caindo pro cache local",
            series, len(data),
        )
    return latest


def get_latest_cdi(cur) -> tuple[date, float] | None:
    """Retorna (data, valor_percent_ao_dia) da CDI mais recente."""
    return get_latest_market_rate(cur, "CDI", 12)


def get_latest_cdi_aa(cur) -> tuple[date, float] | None:
    """CDI a.a. (base 252) direto do SGS/BCB (série 4389)."""
    return get_latest_market_rate(cur, "CDI_AA", 4389)


def get_latest_market_rate(
    cur, code: str, series_code: int, fresh_days: int | None = None
) -> tuple[date, float] | None:
    """Retorna a taxa mais recente de uma série SGS: cache local fresco primeiro,
    rede em cache frio, e cache stale como fallback quando a rede falha.

    fresh_days=None (séries DIÁRIAS): fresco enquanto não existe dia útil BR
    publicável depois do ref_date. fresh_days=N (mensais, ex. IPCA_12M):
    fresco por idade. Nos dois casos o memo de confirmação da rede vale por
    cima (confirmou hoje que não há ponto novo ⇒ fresco até amanhã) — sem
    ele, série sem ponto novo pagaria um fetch idêntico a cada chamada."""
    cur.execute(
        "select ref_date, value from market_rates where code = %s order by ref_date desc limit 1",
        (code,),
    )
    row = cur.fetchone()
    if row:
        if fresh_days is None:
            fresh = _sgs_tail_is_fresh(row["ref_date"], date.today())
        else:
            fresh = (date.today() - row["ref_date"]).days <= fresh_days
        # Memo escopado: a pergunta aqui é sobre (ref_date, hoje].
        if fresh or _sgs_answered_covers(code, row["ref_date"] + timedelta(days=1), date.today()):
            return (row["ref_date"], float(row["value"]))

    latest = _parse_sgs_latest(
        _fetch_sgs_latest_json(series_code), series=f"{code} ({series_code})"
    )

    if latest:
        # O /ultimos/N responde sobre [ponto mais novo, hoje] — é esse o
        # intervalo que vai para o memo, nunca "a série inteira".
        if fresh_days is None:
            # Diária: ponto do último dia útil ⇒ completa (dia cheio); ponto
            # velho (pré-publicação de hoje) ⇒ curta, re-checa ainda hoje.
            _sgs_remember(code, latest[0], date.today(),
                          complete=_sgs_tail_is_fresh(latest[0], date.today()))
        else:  # mensal: o mais novo É a resposta
            _sgs_remember(code, latest[0], date.today(), complete=True)
        cur.execute(
            "insert into market_rates(code, ref_date, value) values (%s, %s, %s) "
            "on conflict (code, ref_date) do update set value = excluded.value",
            (code, latest[0], latest[1]),
        )
        return latest

    # Rede falhou: cache stale (já lido acima) é melhor que nada.
    return (row["ref_date"], float(row["value"])) if row else None


def get_latest_selic_aa(cur) -> tuple[date, float] | None:
    """Meta SELIC a.a. no SGS/BCB (série 432)."""
    return get_latest_market_rate(cur, "SELIC_AA", 432)


def get_latest_ipca_12m(cur) -> tuple[date, float] | None:
    """IPCA acumulado em 12 meses no SGS/BCB (série 13522)."""
    return get_latest_market_rate(cur, "IPCA_12M", 13522, fresh_days=SGS_MONTHLY_FRESH_DAYS)


def get_dashboard_market_rates() -> dict:
    """Taxas oficiais úteis para o dashboard, com datas de referência."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            rates = {
                "cdi_aa": get_latest_cdi_aa(cur),
                "selic_aa": get_latest_selic_aa(cur),
                "ipca_12m": get_latest_ipca_12m(cur),
            }
        conn.commit()

    return {
        key: (
            {"date": ref_date.isoformat(), "value": value}
            if ref_date is not None else None
        )
        for key, maybe_rate in rates.items()
        for ref_date, value in [maybe_rate or (None, None)]
    }


def get_latest_cdi_daily_pct() -> float:
    """Retorna CDI diária em % ao dia (ex: 0.0550)."""
    data = _fetch_sgs_latest_json(12)
    if not data:
        raise RuntimeError("CDI_DAILY_NOT_AVAILABLE")

    latest = None
    for item in data:
        latest = float(str(item["valor"]).replace(",", "."))

    if latest is None:
        raise RuntimeError("CDI_DAILY_NOT_AVAILABLE")

    return float(latest)


MONEY = Decimal("0.01")
ZERO = Decimal("0")
LOT_EPSILON = Decimal("0.000001")

# Saque/resgate a menos de 1 centavo do saldo real conta como "tudo": a tela mostra o
# saldo arredondado a 2 casas, mas o rendimento deixa frações de sub-centavo, então o
# usuário nunca consegue digitar o valor exato pra zerar a caixinha/investimento.
WITHDRAW_ALL_TOLERANCE = Decimal("0.01")

IOF_REGRESSIVE_RATES = {
    1: Decimal("0.96"),
    2: Decimal("0.93"),
    3: Decimal("0.90"),
    4: Decimal("0.86"),
    5: Decimal("0.83"),
    6: Decimal("0.80"),
    7: Decimal("0.76"),
    8: Decimal("0.73"),
    9: Decimal("0.70"),
    10: Decimal("0.66"),
    11: Decimal("0.63"),
    12: Decimal("0.60"),
    13: Decimal("0.56"),
    14: Decimal("0.53"),
    15: Decimal("0.50"),
    16: Decimal("0.46"),
    17: Decimal("0.43"),
    18: Decimal("0.40"),
    19: Decimal("0.36"),
    20: Decimal("0.33"),
    21: Decimal("0.30"),
    22: Decimal("0.26"),
    23: Decimal("0.23"),
    24: Decimal("0.20"),
    25: Decimal("0.16"),
    26: Decimal("0.13"),
    27: Decimal("0.10"),
    28: Decimal("0.06"),
    29: Decimal("0.03"),
}


def _money(value: Decimal | float | int | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _ir_rate_for_days(days: int, tax_profile: str | None) -> Decimal:
    if tax_profile == "etf_rf_15":
        return Decimal("0.15")
    if tax_profile == "exempt_ir_iof":
        return ZERO
    if days <= 180:
        return Decimal("0.225")
    if days <= 360:
        return Decimal("0.20")
    if days <= 720:
        return Decimal("0.175")
    return Decimal("0.15")


def _iof_rate_for_days(days: int, tax_profile: str | None) -> Decimal:
    if tax_profile != "regressive_ir_iof":
        return ZERO
    if days <= 0:
        return Decimal("0.96")
    return IOF_REGRESSIVE_RATES.get(days, ZERO)


def _taxes_for_gain(gain: Decimal, days: int, tax_profile: str | None) -> tuple[Decimal, Decimal]:
    gain = max(Decimal(str(gain)), ZERO)
    if gain <= 0 or tax_profile == "exempt_ir_iof":
        return ZERO, ZERO
    iof = _money(gain * _iof_rate_for_days(days, tax_profile))
    ir_base = max(gain - iof, ZERO)
    ir = _money(ir_base * _ir_rate_for_days(days, tax_profile))
    return iof, ir


def _growth_for_period(
    cur,
    balance: Decimal,
    period: str,
    rate_value: Decimal,
    last_date: date | None,
    today: date,
) -> tuple[Decimal, date | None]:
    if last_date is None:
        return balance, last_date

    n = _business_days_between(last_date, today)
    if n <= 0:
        return balance, last_date

    rate = float(rate_value)

    if period == "cdi":
        mult = rate
        start = last_date + timedelta(days=1)
        db_pkg = sys.modules.get("db")
        fetch_cdi_daily_map = getattr(db_pkg, "_get_cdi_daily_map", _get_cdi_daily_map)
        cdi_map = fetch_cdi_daily_map(cur, start, today)
        cdi_days = sorted(d for d in cdi_map.keys() if last_date < d <= today)
        if not cdi_days:
            return balance, last_date

        factor = 1.0
        for d in cdi_days:
            factor *= (1.0 + (cdi_map[d] / 100.0) * mult)
        return Decimal(str(float(balance) * factor)), cdi_days[-1]

    if period == "cdi_spread":
        start = last_date + timedelta(days=1)
        db_pkg = sys.modules.get("db")
        fetch_cdi_daily_map = getattr(db_pkg, "_get_cdi_daily_map", _get_cdi_daily_map)
        cdi_map = fetch_cdi_daily_map(cur, start, today)
        cdi_days = sorted(d for d in cdi_map.keys() if last_date < d <= today)
        if not cdi_days:
            return balance, last_date

        spread_daily = (1.0 + rate) ** (1.0 / 252.0) - 1.0
        factor = 1.0
        for d in cdi_days:
            factor *= (1.0 + (cdi_map[d] / 100.0)) * (1.0 + spread_daily)
        return Decimal(str(float(balance) * factor)), cdi_days[-1]

    if period == "selic_spread":
        start = last_date + timedelta(days=1)
        selic_map = _get_selic_daily_map(cur, start, today)
        selic_days = sorted(d for d in selic_map.keys() if last_date < d <= today)
        if not selic_days:
            return balance, last_date

        spread_daily = (1.0 + rate) ** (1.0 / 252.0) - 1.0
        factor = 1.0
        for d in selic_days:
            factor *= (1.0 + (selic_map[d] / 100.0)) * (1.0 + spread_daily)
        return Decimal(str(float(balance) * factor)), selic_days[-1]

    if period == "ipca_spread":
        start = (last_date.replace(day=1) + timedelta(days=32)).replace(day=1)
        ipca_map = _get_ipca_monthly_map(cur, start, today)
        ipca_months = sorted(d for d in ipca_map.keys() if last_date < d <= today)
        if not ipca_months:
            return balance, last_date

        spread_monthly = (1.0 + rate) ** (1.0 / 12.0) - 1.0
        factor = 1.0
        for d in ipca_months:
            factor *= (1.0 + (ipca_map[d] / 100.0)) * (1.0 + spread_monthly)
        return Decimal(str(float(balance) * factor)), ipca_months[-1]

    if period == "daily":
        daily_rate = rate
    elif period == "monthly":
        daily_rate = (1.0 + rate) ** (1.0 / 21.0) - 1.0
    elif period == "yearly":
        daily_rate = (1.0 + rate) ** (1.0 / 252.0) - 1.0
    else:
        daily_rate = 0.0

    if daily_rate > 0:
        return Decimal(str(float(balance) * (1.0 + daily_rate) ** n)), today
    return balance, today


def _insert_investment_lot(
    cur,
    user_id: int,
    inv_id: int,
    amount: Decimal,
    opened_at: date,
    last_date: date | None = None,
    rate: Decimal | float | None = None,
    period: str | None = None,
    maturity_date: date | None = None,
) -> int:
    """
    Insere um lote. Se rate/period não forem passados, herda do investimento-pai
    (mesmo comportamento histórico). Permite gravar taxa específica do aporte
    para ativos cuja taxa varia por compra (Tesouro IPCA+/Prefixado, Debênture,
    CRI/CRA IPCA+, CDB prefixado).
    """
    applied_date = last_date or opened_at

    if rate is None or period is None:
        cur.execute(
            "select rate, period from investments where id=%s and user_id=%s",
            (inv_id, user_id),
        )
        parent = cur.fetchone()
        if parent is not None:
            if rate is None:
                rate = parent["rate"]
            if period is None:
                period = parent["period"]

    rate_value = Decimal(str(rate)) if rate is not None else None

    cur.execute(
        """
        insert into investment_lots(
            user_id, investment_id, principal_initial, principal_remaining,
            balance, opened_at, last_date, status, rate, period, maturity_date
        )
        values (%s,%s,%s,%s,%s,%s,%s,'open',%s,%s,%s)
        returning id
        """,
        (
            user_id, inv_id, amount, amount, amount, opened_at, applied_date,
            rate_value, period, maturity_date,
        ),
    )
    return cur.fetchone()["id"]


def _ensure_investment_lots(cur, user_id: int, inv: dict) -> None:
    cur.execute(
        "select count(*) as total from investment_lots where user_id=%s and investment_id=%s",
        (user_id, inv["id"]),
    )
    if int(cur.fetchone()["total"] or 0) > 0:
        return

    balance = Decimal(str(inv["balance"] or 0))
    if balance <= 0:
        return

    opened_at = inv.get("purchase_date") or inv.get("last_date") or datetime.now(_tz()).date()
    last_date = inv.get("last_date") or opened_at
    _insert_investment_lot(
        cur, user_id, inv["id"], balance, opened_at, last_date,
        rate=inv.get("rate"), period=inv.get("period"),
        maturity_date=inv.get("maturity_date"),
    )


def _sync_investment_from_lots(cur, user_id: int, inv_id: int) -> Decimal:
    cur.execute(
        """
        select coalesce(sum(balance), 0) as balance, max(last_date) as last_date
        from investment_lots
        where user_id=%s and investment_id=%s and status='open'
        """,
        (user_id, inv_id),
    )
    totals = cur.fetchone()
    new_balance = Decimal(str(totals["balance"] or 0))
    new_last_date = totals["last_date"]
    if new_last_date is None:
        cur.execute("select last_date from investments where id=%s and user_id=%s", (inv_id, user_id))
        row = cur.fetchone()
        new_last_date = row["last_date"] if row else datetime.now(_tz()).date()
    cur.execute(
        "update investments set balance=%s, last_date=%s where id=%s and user_id=%s",
        (new_balance, new_last_date, inv_id, user_id),
    )
    return new_balance


def _fetch_lots_for_investments(cur, user_id: int, inv_ids: list[int]) -> dict[int, list[dict]]:
    if not inv_ids:
        return {}
    cur.execute(
        """
        select id, investment_id, principal_initial, principal_remaining, balance,
               opened_at, last_date, status, closed_at, rate, period, maturity_date
        from investment_lots
        where user_id=%s and investment_id = any(%s)
        order by investment_id, opened_at, id
        """,
        (user_id, inv_ids),
    )
    lots_by_inv: dict[int, list[dict]] = {int(inv_id): [] for inv_id in inv_ids}
    for row in cur.fetchall():
        lot = dict(row)
        lot["age_days"] = max(0, (datetime.now(_tz()).date() - lot["opened_at"]).days)
        lots_by_inv.setdefault(int(row["investment_id"]), []).append(lot)
    return lots_by_inv


# ──────────────────────────────────────────────────────────────────────────────
# Accrual (aplicação de juros)
# ──────────────────────────────────────────────────────────────────────────────

def accrue_investment_db(cur, user_id: int, inv_id: int, today: date | None = None):
    """
    Atualiza (balance, last_date) aplicando juros por dias úteis.
    daily → rate por dia útil
    monthly → rate distribuído em 21 dias úteis
    yearly → rate distribuído em 252 dias úteis
    cdi → aplica CDI diária do período multiplicada pelo mult (ex: 1.10 = 110% CDI)
    cdi_spread/selic_spread → aplica taxa diária oficial + spread anual convertido para dia útil
    ipca_spread → aplica IPCA mensal publicado + spread anual convertido para mês
    """
    if today is None:
        today = datetime.now(_tz()).date()

    cur.execute(
        """
        select id, balance, rate, period, last_date, purchase_date
        from investments
        where id=%s and user_id=%s for update
        """,
        (inv_id, user_id),
    )
    inv = cur.fetchone()
    if not inv:
        raise LookupError("INV_NOT_FOUND")

    _ensure_investment_lots(cur, user_id, inv)

    cur.execute(
        """
        select id, balance, last_date, rate, period
        from investment_lots
        where user_id=%s and investment_id=%s and status='open'
        order by opened_at, id
        for update
        """,
        (user_id, inv_id),
    )
    lots = cur.fetchall()
    if not lots:
        cur.execute(
            "update investments set balance=0 where id=%s and user_id=%s",
            (inv_id, user_id),
        )
        return ZERO

    for lot in lots:
        # Cada lote pode ter taxa/período próprios (Tesouro IPCA+, Prefixado,
        # Debêntures etc.). Lotes legados sem rate/period caem no fallback do
        # investimento — comportamento idêntico ao antigo.
        lot_rate = lot.get("rate")
        if lot_rate is None:
            lot_rate = inv["rate"]
        lot_period = lot.get("period") or inv["period"]
        new_balance, applied_until = _growth_for_period(
            cur,
            Decimal(str(lot["balance"] or 0)),
            lot_period,
            Decimal(str(lot_rate or 0)),
            lot["last_date"],
            today,
        )
        if new_balance != lot["balance"] or applied_until != lot["last_date"]:
            cur.execute(
                "update investment_lots set balance=%s, last_date=%s where id=%s and user_id=%s",
                (new_balance, applied_until or lot["last_date"], lot["id"], user_id),
            )

    return _sync_investment_from_lots(cur, user_id, inv_id)


# ──────────────────────────────────────────────────────────────────────────────
# CRUD de investimentos
# ──────────────────────────────────────────────────────────────────────────────

VALID_INVESTMENT_PERIODS = {
    "daily", "monthly", "yearly", "cdi", "cdi_spread", "ipca_spread", "selic_spread"
}

VALID_INVESTMENT_INDEXERS = {
    "daily", "monthly", "fixed", "pct_cdi", "cdi_spread", "ipca_spread", "selic_spread"
}

TAX_PROFILE_BY_ASSET_TYPE = {
    "LCI": "exempt_ir_iof",
    "LCA": "exempt_ir_iof",
    "CRI": "exempt_ir_iof",
    "CRA": "exempt_ir_iof",
    "ETF Renda Fixa": "etf_rf_15",
}


def _default_indexer_for_period(period: str) -> str:
    if period == "cdi":
        return "pct_cdi"
    if period == "yearly":
        return "fixed"
    return period


def _tax_profile_for_asset(asset_type: str | None) -> str:
    return TAX_PROFILE_BY_ASSET_TYPE.get(asset_type or "CDB", "regressive_ir_iof")

def create_investment(user_id: int, name: str, rate: float, period: str, nota: str | None = None):
    """
    Cria investimento. Retorna (launch_id, inv_name_canon).
    Se já existir, retorna (None, inv_name_canon).
    """
    ensure_user(user_id)
    name = (name or "").strip()
    if not name:
        raise ValueError("EMPTY_NAME")
    if period not in ("daily", "monthly", "yearly"):
        raise ValueError("BAD_PERIOD")

    r = Decimal(str(rate))
    if r <= 0:
        raise ValueError("BAD_RATE")

    criado_em = datetime.now(_tz())
    last_date = datetime.now(_tz()).date()

    with get_conn() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "insert into investments(user_id, name, balance, rate, period, last_date) "
                    "values (%s,%s,0,%s,%s,%s) returning name",
                    (user_id, name, r, period, last_date),
                )
                inv_name = cur.fetchone()["name"]
                created = True
            except psycopg.errors.UniqueViolation:
                conn.rollback()
                created = False
                with get_conn() as conn2:
                    with conn2.cursor() as cur2:
                        cur2.execute(
                            "select name from investments where user_id=%s and lower(name)=lower(%s)",
                            (user_id, name),
                        )
                        row = cur2.fetchone()
                        if not row:
                            raise
                        inv_name = row["name"]

            if not created:
                return None, inv_name

            efeitos = {
                "delta_conta": 0.0, "delta_pocket": None,
                "delta_invest": {"nome": inv_name, "delta": 0.0},
                "create_pocket": None,
                "create_investment": {"nome": inv_name, "rate": float(r), "period": period},
            }
            cur.execute(
                "insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos) "
                "values (%s,%s,%s,%s,%s,%s,%s) returning id",
                (user_id, "create_investment", Decimal("0"), inv_name, nota, criado_em, Jsonb(efeitos)),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, inv_name


def create_investment_db(
    user_id: int,
    name: str,
    rate: float,
    period: str,
    nota: str | None = None,
    *,
    asset_type: str | None = None,
    indexer: str | None = None,
    issuer: str | None = None,
    purchase_date: date | str | None = None,
    maturity_date: date | str | None = None,
    interest_payment_frequency: str | None = None,
    tax_profile: str | None = None,
    initial_amount: float | Decimal | None = None,
    initial_note: str | None = None,
    funding_source: dict | None = None,
):
    """
    Cria investimento (suporta period='cdi'). Retorna (launch_id, inv_id, canon_name).
    Se já existir, retorna (None, inv_id, canon_name).
    """
    ensure_user(user_id)
    name = (name or "").strip()
    if not name:
        raise ValueError("EMPTY_NAME")
    if period not in VALID_INVESTMENT_PERIODS:
        raise ValueError("INVALID_PERIOD")

    r = Decimal(str(rate))
    if r <= 0 and period != "selic_spread":
        raise ValueError("INVALID_RATE")

    criado_em = datetime.now(_tz())
    today = date.today()
    asset_type = (asset_type or "CDB").strip() or "CDB"
    indexer = (indexer or _default_indexer_for_period(period)).strip()
    if indexer not in VALID_INVESTMENT_INDEXERS:
        raise ValueError("INVALID_INDEXER")
    issuer = (issuer or "").strip() or None
    tax_profile = (tax_profile or _tax_profile_for_asset(asset_type)).strip()
    interest_payment_frequency = (interest_payment_frequency or "maturity").strip()
    if isinstance(purchase_date, str) and purchase_date:
        purchase_date = date.fromisoformat(purchase_date)
    if isinstance(maturity_date, str) and maturity_date:
        maturity_date = date.fromisoformat(maturity_date)
    initial = Decimal(str(initial_amount or 0))
    if initial < 0:
        raise ValueError("INITIAL_AMOUNT_INVALID")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into investments(
                    user_id, name, balance, rate, period, last_date,
                    asset_type, indexer, issuer, purchase_date, maturity_date,
                    interest_payment_frequency, tax_profile
                )
                values (%s,%s,0,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (user_id, name) do nothing
                returning id, name
                """,
                (
                    user_id, name, r, period, today,
                    asset_type, indexer, issuer, purchase_date, maturity_date,
                    interest_payment_frequency, tax_profile,
                ),
            )
            row = cur.fetchone()

            if row:
                inv_id, canon = row["id"], row["name"]
                created = True
            else:
                created = False
                cur.execute(
                    "select id, name from investments where user_id=%s and lower(name)=lower(%s)",
                    (user_id, name),
                )
                r2 = cur.fetchone()
                if not r2:
                    raise RuntimeError("INVESTMENT_LOOKUP_FAILED")
                inv_id, canon = r2["id"], r2["name"]

            if not created:
                conn.commit()
                return None, inv_id, canon

            efeitos = {
                "delta_conta": 0.0, "delta_pocket": None, "delta_invest": None,
                "create_pocket": None, "create_investment": {"nome": canon},
                "delete_pocket": None, "delete_investment": None,
                "investment_meta": {
                    "asset_type": asset_type,
                    "indexer": indexer,
                    "tax_profile": tax_profile,
                },
            }
            cur.execute(
                "insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos) "
                "values (%s,%s,%s,%s,%s,%s,%s) returning id",
                (user_id, "create_investment", Decimal("0"), canon, nota, criado_em, Jsonb(efeitos)),
            )
            launch_id = cur.fetchone()["id"]

            if initial > 0:
                # ver investment_deposit_from_account: funding_source=None debita a Carteira
                debita_carteira = funding_source is None
                cur.execute("select balance from accounts where user_id=%s for update", (user_id,))
                acc = cur.fetchone()
                if not acc:
                    raise RuntimeError("ACCOUNT_MISSING")
                if debita_carteira and acc["balance"] < initial:
                    raise ValueError("INSUFFICIENT_ACCOUNT")
                if not debita_carteira:
                    from .open_finance import assert_bank_covers
                    assert_bank_covers(cur, user_id, funding_source.get("of_account_id"), initial)

                if debita_carteira:
                    cur.execute(
                        "update accounts set balance = balance - %s where user_id=%s",
                        (initial, user_id),
                    )
                lot_opened_at = purchase_date or today
                lot_id = _insert_investment_lot(
                    cur, user_id, inv_id, initial, lot_opened_at, lot_opened_at,
                    rate=r, period=period, maturity_date=maturity_date,
                )
                _sync_investment_from_lots(cur, user_id, inv_id)

                deposit_effects = {
                    "delta_conta": -float(initial) if debita_carteira else 0,
                    "funding_source": funding_source,
                    "delta_pocket": None,
                    "delta_invest": {"nome": canon, "delta": float(initial)},
                    "create_pocket": None, "create_investment": None,
                    "investment_lot_create": {"lot_id": lot_id, "investment_id": inv_id},
                }
                cur.execute(
                    "insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos, is_internal_movement) "
                    "values (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        user_id,
                        "aporte_investimento",
                        initial,
                        canon,
                        initial_note or f"Aporte inicial em {canon}",
                        criado_em,
                        Jsonb(deposit_effects),
                        True,
                    ),
                )

        conn.commit()

    return launch_id, inv_id, canon


def delete_investment(user_id: int, investment_name: str, nota: str | None = None):
    """Exclui investimento (saldo=0). Retorna (launch_id, canon_name)."""
    ensure_user(user_id)
    investment_name = (investment_name or "").strip()
    if not investment_name:
        raise ValueError("EMPTY_NAME")

    criado_em = datetime.now(_tz())

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, name, balance, rate, period, last_date,
                       asset_type, indexer, issuer, purchase_date, maturity_date,
                       interest_payment_frequency, tax_profile
                from investments
                """
                "where user_id=%s and lower(name)=lower(%s) for update",
                (user_id, investment_name),
            )
            inv = cur.fetchone()
            if not inv:
                raise LookupError("INV_NOT_FOUND")

            inv_id, canon = inv["id"], inv["name"]
            if Decimal(str(inv["balance"])) != Decimal("0"):
                raise ValueError("INV_NOT_ZERO")

            cur.execute("delete from investments where id=%s", (inv_id,))

            efeitos = {
                "delta_conta": 0.0, "delta_pocket": None, "delta_invest": None,
                "create_pocket": None, "create_investment": None, "delete_pocket": None,
                "delete_investment": {
                    "nome": canon, "balance": 0.0,
                    "rate": float(inv["rate"]), "period": inv["period"],
                    "asset_type": inv.get("asset_type"),
                    "indexer": inv.get("indexer"),
                    "issuer": inv.get("issuer"),
                    "purchase_date": inv["purchase_date"].isoformat() if inv.get("purchase_date") else None,
                    "maturity_date": inv["maturity_date"].isoformat() if inv.get("maturity_date") else None,
                    "interest_payment_frequency": inv.get("interest_payment_frequency"),
                    "tax_profile": inv.get("tax_profile"),
                    "last_date": inv["last_date"].isoformat() if inv["last_date"]
                                 else datetime.now(_tz()).date().isoformat(),
                },
            }
            cur.execute(
                "insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos) "
                "values (%s,%s,%s,%s,%s,%s,%s) returning id",
                (user_id, "delete_investment", Decimal("0"), canon, nota, criado_em, Jsonb(efeitos)),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, canon


def list_investments(user_id: int):
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, name, balance, rate, period, last_date,
                       asset_type, indexer, issuer, purchase_date, maturity_date,
                       interest_payment_frequency, tax_profile
                """
                "from investments where user_id=%s order by balance desc, lower(name)",
                (user_id,),
            )
            rows = [dict(r) for r in cur.fetchall()]
            lots_by_inv = _fetch_lots_for_investments(cur, user_id, [int(r["id"]) for r in rows])
            for row in rows:
                row["lots"] = lots_by_inv.get(int(row["id"]), [])
            return rows


def list_users_with_investments() -> list[int]:
    """Retorna usuários que possuem ao menos um investimento cadastrado."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select distinct user_id from investments order by user_id")
            return [int(row["user_id"]) for row in cur.fetchall()]


def _project_to_today(
    cur,
    balance: Decimal,
    period: str,
    rate_value: Decimal,
    last_date: date | None,
    today: date,
) -> tuple[Decimal, date | None, int]:
    """
    Estima o saldo de last_date até today usando a última taxa conhecida como
    proxy para dias úteis ainda não publicados pelo BCB.

    NÃO persiste nada — somente para exibição. O saldo realizado em
    investment_lots continua sendo atualizado apenas com taxas oficialmente
    publicadas, então a projeção converge para o valor correto assim que o
    BCB publica os dados.

    Retorna (balance_projetado, data_alvo, dias_uteis_projetados).
    """
    if last_date is None or today <= last_date:
        return balance, last_date, 0

    n = _business_days_between(last_date, today)
    if n <= 0:
        return balance, last_date, 0

    rate = float(rate_value)

    if period in ("cdi", "cdi_spread"):
        cur.execute(
            "select value from market_rates where code='CDI' order by ref_date desc limit 1"
        )
        row = cur.fetchone()
        if not row:
            return balance, last_date, 0
        latest_cdi = float(row["value"])

        if period == "cdi":
            factor = (1.0 + (latest_cdi / 100.0) * rate) ** n
        else:
            spread_daily = (1.0 + rate) ** (1.0 / 252.0) - 1.0
            factor = ((1.0 + latest_cdi / 100.0) * (1.0 + spread_daily)) ** n
        return Decimal(str(float(balance) * factor)), today, n

    if period == "selic_spread":
        cur.execute(
            "select value from market_rates where code='SELIC_DAILY' order by ref_date desc limit 1"
        )
        row = cur.fetchone()
        if not row:
            return balance, last_date, 0
        latest_selic = float(row["value"])
        spread_daily = (1.0 + rate) ** (1.0 / 252.0) - 1.0
        factor = ((1.0 + latest_selic / 100.0) * (1.0 + spread_daily)) ** n
        return Decimal(str(float(balance) * factor)), today, n

    if period == "daily":
        daily_rate = rate
    elif period == "monthly":
        daily_rate = (1.0 + rate) ** (1.0 / 21.0) - 1.0
    elif period == "yearly":
        daily_rate = (1.0 + rate) ** (1.0 / 252.0) - 1.0
    else:
        return balance, last_date, 0

    if daily_rate > 0:
        return Decimal(str(float(balance) * (1.0 + daily_rate) ** n)), today, n
    return balance, last_date, 0


def accrue_all_investments(user_id: int, today: date | None = None):
    """Aplica juros em todos os investimentos do usuário e retorna a lista atualizada."""
    ensure_user(user_id)
    if today is None:
        today = datetime.now(_tz()).date()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select id from investments where user_id=%s for update", (user_id,))
            rows = cur.fetchall()

            for r in rows:
                accrue_investment_db(cur, user_id, r["id"], today=today)

            cur.execute(
                """
                select id, name, balance, rate, period, last_date,
                       asset_type, indexer, issuer, purchase_date, maturity_date,
                       interest_payment_frequency, tax_profile
                """
                "from investments where user_id=%s order by balance desc, lower(name)",
                (user_id,),
            )
            out = [dict(r) for r in cur.fetchall()]
            lots_by_inv = _fetch_lots_for_investments(cur, user_id, [int(r["id"]) for r in out])
            for row in out:
                row["lots"] = lots_by_inv.get(int(row["id"]), [])

                # Projeção por lote: cada lote acumula independente, então um lote
                # criado hoje não pode "esconder" o gap de projection de lotes mais
                # antigos via inv.last_date = MAX(...). Soma as projeções de cada
                # lote aberto; fallback no agregado se não houver lotes (cenário
                # legado pré-migração de lots).
                open_lots = [lot for lot in row["lots"] if lot.get("status") == "open"]
                if open_lots:
                    proj_total = Decimal("0")
                    proj_days = 0
                    proj_until = None
                    for lot in open_lots:
                        lot_rate = lot.get("rate") if lot.get("rate") is not None else row["rate"]
                        lot_period = lot.get("period") or row["period"]
                        lot_pb, lot_until, lot_days = _project_to_today(
                            cur,
                            Decimal(str(lot["balance"] or 0)),
                            lot_period,
                            Decimal(str(lot_rate or 0)),
                            lot["last_date"],
                            today,
                        )
                        proj_total += lot_pb
                        proj_days = max(proj_days, lot_days)
                        if lot_until and (proj_until is None or lot_until > proj_until):
                            proj_until = lot_until
                    row["projected_balance"] = float(proj_total)
                    row["projected_until"] = proj_until or row["last_date"]
                    row["projected_days"] = proj_days
                else:
                    projected_balance, projected_until, projected_days = _project_to_today(
                        cur,
                        Decimal(str(row["balance"] or 0)),
                        row["period"],
                        Decimal(str(row["rate"] or 0)),
                        row["last_date"],
                        today,
                    )
                    row["projected_balance"] = float(projected_balance)
                    row["projected_until"] = projected_until
                    row["projected_days"] = projected_days

        conn.commit()

    return out


def _canon_investment_name(cur, user_id: int, name: str) -> str | None:
    cur.execute(
        "select name from investments where user_id = %s and lower(name) = lower(%s)",
        (user_id, name),
    )
    row = cur.fetchone()
    return row["name"] if row else None


def investment_deposit_from_account(
    user_id: int,
    investment_name: str,
    amount: float,
    nota: str | None = None,
    *,
    rate: float | Decimal | None = None,
    period: str | None = None,
    purchase_date: date | str | None = None,
    funding_source: dict | None = None,
):
    """Conta → Investimento (com accrual antes). Retorna (launch_id, new_acc, new_inv, canon).

    `funding_source` diz de ONDE sai o dinheiro. `None` = Carteira (`accounts.balance`),
    que é o comportamento histórico: confere cobertura e debita. Um dict
    `{"kind": "bank", ...}` significa que o dinheiro está numa conta conectada por Open
    Finance — aí NÃO se toca em `accounts.balance` (ela fica zerada de propósito quando há
    banco conectado, senão o mesmo dinheiro conta 2x) e `delta_conta` vai a 0.

    Esse zero é o que mantém o desfazer correto: `delete_launch_and_rollback` só estorna a
    conta quando `delta_conta != 0` (db/accounts.py). Gravar -800 sem ter debitado faria
    desfazer CRIAR R$ 800 na Carteira. Mesmo padrão de `import_open_finance_launches`.
    Quem escolhe a origem é `core/services/funding.py`.


    Parâmetros opcionais (kwargs) para suportar ativos cuja taxa muda por compra
    (Tesouro IPCA+/Prefixado, Debêntures, CRI/CRA IPCA+, CDB prefixado):
    - rate: taxa específica deste aporte. Se None, herda do investimento.
    - period: indexador específico deste aporte. Se None, herda do investimento.
    - purchase_date: data da compra (opened_at do lote). Se None, usa hoje.
    """
    ensure_user(user_id)
    v = Decimal(str(amount))
    if v <= 0:
        raise ValueError("AMOUNT_INVALID")

    criado_em = datetime.now(_tz())
    today = datetime.now(_tz()).date()

    if isinstance(purchase_date, str) and purchase_date:
        purchase_date = date.fromisoformat(purchase_date)
    lot_opened_at = purchase_date or today
    if lot_opened_at > today:
        raise ValueError("PURCHASE_DATE_FUTURE")

    if period is not None and period not in VALID_INVESTMENT_PERIODS:
        raise ValueError("INVALID_PERIOD")

    if rate is not None:
        rate_dec = Decimal(str(rate))
        # selic_spread aceita 0 (taxa zero é o título Selic puro);
        # demais indexadores precisam de taxa positiva.
        effective_period = period
        if rate_dec < 0:
            raise ValueError("INVALID_RATE")
        if rate_dec == 0 and effective_period not in (None, "selic_spread"):
            raise ValueError("INVALID_RATE")
    else:
        rate_dec = None

    with get_conn() as conn:
        with conn.cursor() as cur:
            debita_carteira = funding_source is None
            cur.execute("select balance from accounts where user_id=%s for update", (user_id,))
            acc = cur.fetchone()
            if not acc:
                raise RuntimeError("ACCOUNT_MISSING")
            if debita_carteira and acc["balance"] < v:
                raise ValueError("INSUFFICIENT_ACCOUNT")
            if not debita_carteira:
                from .open_finance import assert_bank_covers
                assert_bank_covers(cur, user_id, funding_source.get("of_account_id"), v)

            cur.execute(
                "select id, name, rate, period from investments "
                "where user_id=%s and lower(name)=lower(%s) for update",
                (user_id, investment_name),
            )
            inv = cur.fetchone()
            if not inv:
                raise LookupError("INV_NOT_FOUND")

            inv_id, canon = inv["id"], inv["name"]
            # Accrual antes do aporte: lotes existentes ficam atualizados até hoje.
            # Como o lote novo abre hoje, ele não retroage.
            accrue_investment_db(cur, user_id, inv_id, today=today)

            if debita_carteira:
                cur.execute(
                    "update accounts set balance = balance - %s where user_id=%s returning balance",
                    (v, user_id),
                )
                new_acc = cur.fetchone()["balance"]
            else:
                new_acc = acc["balance"]

            # Se rate==0 foi passado para selic_spread, repassa explicitamente;
            # senão, None deixa o _insert herdar do investimento.
            lot_rate = rate_dec if rate is not None else None
            lot_period = period
            lot_id = _insert_investment_lot(
                cur, user_id, inv_id, v, lot_opened_at, lot_opened_at,
                rate=lot_rate, period=lot_period,
            )
            new_inv = _sync_investment_from_lots(cur, user_id, inv_id)

            efeitos = {
                "delta_conta": -float(v) if debita_carteira else 0,
                "funding_source": funding_source,
                "delta_pocket": None,
                "delta_invest": {"nome": canon, "delta": +float(v)},
                "create_pocket": None, "create_investment": None,
                "investment_lot_create": {
                    "lot_id": lot_id,
                    "investment_id": inv_id,
                    "rate": float(lot_rate) if lot_rate is not None else None,
                    "period": lot_period,
                    "opened_at": lot_opened_at.isoformat(),
                },
            }
            cur.execute(
                "insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos, is_internal_movement) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s) returning id",
                (user_id, "aporte_investimento", v, canon, nota, criado_em, Jsonb(efeitos), True),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, new_acc, new_inv, canon


def investment_withdraw_to_account(
    user_id: int,
    investment_name: str,
    amount: float | None = None,
    nota: str | None = None,
    *,
    withdraw_all: bool = False,
    funding_source: dict | None = None,
):
    """Investimento → Conta via PEPS/FIFO. Retorna (launch_id, new_acc, new_inv, canon, tax_summary).

    `funding_source` aqui é o DESTINO do resgate — espelho do aporte. Com origem `bank`
    o dinheiro volta para o banco, não para a Carteira: creditar a Carteira inflaria o
    saldo consolidado com o mesmo dinheiro que o sync vai trazer de volta.

    Se ``withdraw_all=True``, resgata o saldo cheio pós-rendimento (zera o investimento
    de forma atômica) e ignora ``amount``. Caso contrário resgata ``amount``; mas se o
    valor pedido ficar a menos de 1 centavo do saldo real, resgata tudo — a tela arredonda
    o saldo a 2 casas e o rendimento deixa frações de sub-centavo (ver ``WITHDRAW_ALL_TOLERANCE``).
    """
    ensure_user(user_id)
    if not withdraw_all:
        if amount is None:
            raise ValueError("AMOUNT_INVALID")
        v = Decimal(str(amount))
        if v <= 0:
            raise ValueError("AMOUNT_INVALID")

    criado_em = datetime.now(_tz())
    today = datetime.now(_tz()).date()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, name, tax_profile from investments "
                "where user_id=%s and lower(name)=lower(%s) for update",
                (user_id, investment_name),
            )
            inv = cur.fetchone()
            if not inv:
                raise LookupError("INV_NOT_FOUND")

            inv_id, canon = inv["id"], inv["name"]
            new_bal_before = accrue_investment_db(cur, user_id, inv_id, today=today)

            if withdraw_all or abs(new_bal_before - v) < WITHDRAW_ALL_TOLERANCE:
                # resgatar tudo (ou pediu ~tudo, dentro da tolerância de arredondamento da tela)
                v = new_bal_before
            elif new_bal_before < v:
                raise ValueError("INSUFFICIENT_INVEST")
            if v <= 0:
                raise ValueError("INSUFFICIENT_INVEST")

            cur.execute(
                """
                select id, principal_remaining, balance, opened_at, last_date, status
                from investment_lots
                where user_id=%s and investment_id=%s and status='open' and balance > 0
                order by opened_at, id
                for update
                """,
                (user_id, inv_id),
            )
            lots = cur.fetchall()
            remaining = v
            total_gross = ZERO
            total_net = ZERO
            total_iof = ZERO
            total_ir = ZERO
            lot_effects = []
            breakdown = []
            tax_profile = inv.get("tax_profile") or "regressive_ir_iof"

            for lot in lots:
                if remaining <= 0:
                    break

                lot_balance = Decimal(str(lot["balance"] or 0))
                if lot_balance <= 0:
                    continue
                lot_principal = Decimal(str(lot["principal_remaining"] or 0))
                take = min(lot_balance, remaining)

                if lot_balance <= lot_principal or lot_balance <= 0:
                    principal_part = min(take, lot_principal)
                    gain_part = ZERO
                else:
                    ratio = take / lot_balance
                    principal_part = min(lot_principal, lot_principal * ratio)
                    gain_part = max(take - principal_part, ZERO)

                age_days = max(0, (today - lot["opened_at"]).days)
                iof, ir = _taxes_for_gain(gain_part, age_days, tax_profile)
                net = take - iof - ir

                new_lot_balance = lot_balance - take
                new_lot_principal = max(lot_principal - principal_part, ZERO)
                closes = new_lot_balance <= LOT_EPSILON
                after_status = "closed" if closes else "open"
                after_balance = ZERO if closes else new_lot_balance
                after_principal = ZERO if closes else new_lot_principal

                lot_effects.append({
                    "lot_id": int(lot["id"]),
                    "before": {
                        "balance": float(lot_balance),
                        "principal_remaining": float(lot_principal),
                        "status": lot["status"],
                        "closed_at": None,
                    },
                    "after": {
                        "balance": float(after_balance),
                        "principal_remaining": float(after_principal),
                        "status": after_status,
                        "closed_at": today.isoformat() if closes else None,
                    },
                })
                breakdown.append({
                    "lot_id": int(lot["id"]),
                    "opened_at": lot["opened_at"].isoformat(),
                    "age_days": age_days,
                    "gross": float(take),
                    "principal": float(principal_part),
                    "gain": float(gain_part),
                    "iof": float(iof),
                    "ir": float(ir),
                    "net": float(net),
                    "ir_rate": float(_ir_rate_for_days(age_days, tax_profile)),
                    "iof_rate": float(_iof_rate_for_days(age_days, tax_profile)),
                })

                cur.execute(
                    """
                    update investment_lots
                    set balance=%s, principal_remaining=%s, status=%s, closed_at=%s, last_date=%s
                    where id=%s and user_id=%s
                    """,
                    (
                        after_balance,
                        after_principal,
                        after_status,
                        today if closes else None,
                        today,
                        lot["id"],
                        user_id,
                    ),
                )

                remaining -= take
                total_gross += take
                total_net += net
                total_iof += iof
                total_ir += ir

            if remaining > LOT_EPSILON:
                raise ValueError("INSUFFICIENT_INVEST")

            new_inv = _sync_investment_from_lots(cur, user_id, inv_id)

            if funding_source is None:
                cur.execute(
                    "update accounts set balance = balance + %s where user_id=%s returning balance",
                    (total_net, user_id),
                )
                new_acc = cur.fetchone()["balance"]
            else:
                cur.execute("select balance from accounts where user_id=%s", (user_id,))
                new_acc = cur.fetchone()["balance"]

            tax_summary = {
                "gross": float(total_gross),
                "net": float(total_net),
                "iof": float(total_iof),
                "ir": float(total_ir),
                "tax_profile": tax_profile,
                "method": "PEPS",
                "lots": breakdown,
            }
            efeitos = {
                "delta_conta": +float(total_net) if funding_source is None else 0,
                "funding_source": funding_source,
                "delta_pocket": None,
                "delta_invest": {"nome": canon, "delta": -float(total_gross)},
                "create_pocket": None, "create_investment": None,
                "investment_lot_withdrawals": lot_effects,
                "tax_summary": tax_summary,
            }
            cur.execute(
                "insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos, is_internal_movement) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s) returning id",
                (user_id, "resgate_investimento", total_gross, canon, nota, criado_em, Jsonb(efeitos), True),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, new_acc, new_inv, canon, tax_summary
