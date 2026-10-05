"""IP real do cliente atrás de Cloudflare → Railway (issue #766).

O uvicorn só confia em `X-Forwarded-For` vindo de 127.0.0.1 (`forwarded_allow_ips`
padrão), e no Railway o peer TCP é o proxy dele, em 100.64.x.x — então
`request.client.host` (e o `get_remote_address` do slowapi) é o IP do proxy, não o
do usuário. Regra em dois passos:

1. **Conexão.** Só se o peer está em `PROXY_RAILWAY`, vale a ÚLTIMA entrada do
   `X-Forwarded-For` (a que o Railway escreve; as anteriores o cliente controla).
   Entrada inválida ou cabeçalho ausente → o peer. Peer fora da faixa → o peer, e
   os cabeçalhos são IGNORADOS.
2. **Cliente.** Se a borda é confiável e há exatamente um `CF-Connecting-IP`
   que é IP válido, público (`_publico`) e sem zona IPv6, ele é o cliente.
   Senão, a conexão. Borda confiável:
   - **com `CLOUDFLARE_ORIGIN_SECRET`** (≥ 32 chars, lida a cada chamada): só
     quando chega exatamente um `x-pigbank-cf-secret` igual a ela (tempo
     constante, sem `strip`; duplicado, vazio ou diferente não confere). A
     faixa da conexão deixa de importar: em produção a última entrada do XFF é
     um IP de infra, não da Cloudflare (sondas #791/#808), e o segredo é o que
     prova que a requisição passou pela regra da Cloudflare (`SEGREDO_CABECALHO`).
     Com o segredo certo, a conexão só sai no resultado se o `CF-Connecting-IP`
     vier ausente ou inválido; com ele errado ou ausente, sai sempre a conexão.
   - **sem a env, ou curta:** a conexão é um IP da Cloudflare
     (`CLOUDFLARE_FAIXAS`) — o comportamento anterior, inalterado.

`PROXY_RAILWAY` é SUPOSIÇÃO apoiada na medição do dono em produção (todas as chaves
`ip:` de `auth_rate_limits` em 100.64.x.x); o Railway não documenta a faixa. Com ou
sem segredo, peer fora dela ignora todos os cabeçalhos. Sem a env, a única
suposição de segurança é que o Railway ESCREVE a última entrada do XFF; se ele
passasse a repassar o XFF do cliente sem acrescentar, o IP ficaria falsificável —
a sonda abaixo vigia isso (`xff_entradas`, `xri_igual_conexao`).

Ressalva de segurança: a borda do Railway é alcançável sem passar pela Cloudflare.
Sem a env, o `CF-Connecting-IP` só vale quando a conexão É da Cloudflare, e o risco
residual é o Worker/zona do atacante, que sai de IPs da Cloudflare e escreve o
cabeçalho que quiser. Com a env, esse caminho fica desligado e o risco residual
passa a ser o VAZAMENTO do segredo (quem o tem forja qualquer IP batendo direto
no Railway); o remédio é a rotação (trocar a env e a regra). Quem passa pela
Cloudflare não forja o IP de outra pessoa, nem o segredo, se a regra da
Cloudflare DEFINE o cabeçalho (sobrescreve o do cliente) — pela documentação
dela; não verificado aqui. Regra em "Adicionar" faz chegar dois valores, e
duplicado não confere (falha fechado). O valor do segredo nunca vai para a
sonda, log, print ou exceção.

Lista desatualizada falha para o lado SEGURO: um IP novo da Cloudflare fora da
lista cai no IP da borda (a granularidade de antes desta mudança), nunca abre
falsificação. Atualizar = PR que muda a constante.
"""
from __future__ import annotations

import ipaddress
import os
import sys
from threading import Thread
from typing import Any, Callable

from core.secure_compare import constant_time_eq
from core.system_event_log import log_system_event_sync


PROXY_RAILWAY = ipaddress.ip_network("100.64.0.0/10")

# Cabeçalho que a regra da Cloudflare DEFINE com o segredo. Sem prefixo `cf-`,
# que a Cloudflare reserva (SUPOSIÇÃO). Configuração: `.env.example`.
SEGREDO_CABECALHO = "x-pigbank-cf-secret"
SEGREDO_ENV = "CLOUDFLARE_ORIGIN_SECRET"
_SEGREDO_MINIMO = 32
_AVISOU_SEGREDO_CURTO = False

# Fonte: https://www.cloudflare.com/ips-v4 e https://www.cloudflare.com/ips-v6,
# baixadas em 2026-10-03. Para conferir:
#   curl -s https://www.cloudflare.com/ips-v4 https://www.cloudflare.com/ips-v6
CLOUDFLARE_FAIXAS = tuple(ipaddress.ip_network(f) for f in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
    "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
))

# Prefixos que embutem um IPv4 qualquer (`64:ff9b::7f00:1` = 127.0.0.1) e que o
# `is_global` do Python 3.13.2 chama de global: NAT64 64:ff9b::/96 (RFC 6052) e
# IPv4-compatível ::/96 (RFC 4291 §2.5.5.1, obsoleto). O 64:ff9b:1::/48 (RFC
# 8215) já sai não-global no 3.13.2; fica na lista para não depender da versão.
_EMBUTEM_IPV4 = tuple(ipaddress.ip_network(f) for f in ("64:ff9b::/96", "64:ff9b:1::/48", "::/96"))

# ponytail: registra só a PRESENÇA de cada combinação de sinais, uma vez por
# processo (não conta volume nem guarda IP). Se precisar de proporção, contar em
# memória e despejar periodicamente. Teto: quem bate direto no Railway escolhe
# os cabeçalhos e alcança milhares de combinações, então o processo grava no
# máximo `_SONDA_TETO` (= linhas em system_event_logs e threads por processo);
# depois disso a sonda fica cega até o próximo deploy/restart, que zera o set.
# Cegar não muda nenhum IP: a sonda só mede. Se o teto encher em produção,
# trocar por contador em memória.
_SONDA_TETO = 256
_SONDA_VISTAS: set[tuple] = set()


def _ip(valor: Any) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """IP canônico, ou None. Zona IPv6 (`fe80::1%eth0`) é recusada: o
    `ipaddress` a aceita e a carrega no `str()`, e a chave sairia com ela."""
    texto = str(valor).strip()
    if "%" in texto:
        return None
    try:
        ip = ipaddress.ip_address(texto)
    except ValueError:
        return None
    return getattr(ip, "ipv4_mapped", None) or ip


def _publico(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Pode ser origem de cliente na internet: global, não multicast (nunca é
    origem, e o `is_global` o aceita) e fora de `_EMBUTEM_IPV4`."""
    return ip.is_global and not ip.is_multicast and not any(ip in r for r in _EMBUTEM_IPV4)


def _tipo(ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None) -> str:
    """Categoria do IP para a sonda — o conjunto de valores é fechado."""
    if ip is None:
        return "ausente"
    if any(ip in faixa for faixa in CLOUDFLARE_FAIXAS):
        return "cf"
    if ip in PROXY_RAILWAY:
        return "railway"
    if ip.is_private:
        return "privado"
    return "publico" if _publico(ip) else "outro"


def _ate_3(n: int) -> int | str:
    return n if n < 3 else "3+"


def _cabecalhos(headers: Any, nome: str) -> list[str]:
    """Todos os valores do cabeçalho; `getlist` (Starlette) só se existir —
    há chamador que passa objeto sem `.headers` ou com um dict simples."""
    if headers is None:
        return []
    if hasattr(headers, "getlist"):
        return list(headers.getlist(nome))
    valor = headers.get(nome)
    return [] if valor is None else [valor]


def _segredo_esperado() -> str | None:
    """O segredo da env, ou None (ausente ou curto = comportamento anterior).
    Curto avisa UMA vez por processo em stderr, sem o valor nem o comprimento."""
    global _AVISOU_SEGREDO_CURTO
    valor = (os.getenv(SEGREDO_ENV) or "").strip()
    if not valor:
        return None
    if len(valor) < _SEGREDO_MINIMO:
        if not _AVISOU_SEGREDO_CURTO:
            _AVISOU_SEGREDO_CURTO = True
            print(f"[client_ip] {SEGREDO_ENV} curto demais: ignorado", file=sys.stderr)
        return None
    return valor


def _sonda(montar: Callable[[], dict]) -> None:
    """Grava em `system_event_logs` cada combinação NOVA de sinais. Só booleanos
    e contagens: nunca IP, user agent ou path."""
    try:
        # Thread daemon e `log_system_event_sync` (que já engole a falha do
        # banco), nunca `logging`: um warning aqui reentraria no
        # `_DashboardHandler`. O except amplo cobre o que sobra — montar os
        # details ou a thread não subir —, porque a sonda é medição e não pode
        # derrubar o login.
        details = montar()
        chave = tuple(details.values())
        if chave in _SONDA_VISTAS or len(_SONDA_VISTAS) >= _SONDA_TETO:
            return
        _SONDA_VISTAS.add(chave)
        Thread(
            target=log_system_event_sync,
            args=("info", "client_ip_sonda", "sonda do IP real (#766)"),
            kwargs={"source": "core.client_ip", "user_id": None, "details": details},
            daemon=True,
        ).start()
    except Exception as exc:
        print(f"[client_ip] sonda não gravada: {type(exc).__name__}", file=sys.stderr)


def client_ip(conn: Any) -> str | None:
    """IP do cliente de um `Request`/`WebSocket` (ou objeto que os imite).

    Sem `client` → None. Peer que não é IP (o "testclient" do TestClient) volta
    como está. IPv4 mapeado em IPv6 sai como IPv4; o resto, na forma canônica.
    """
    client = getattr(conn, "client", None)
    peer = getattr(client, "host", None) if client else None
    if peer is None:
        return None
    peer_ip = _ip(peer)
    if peer_ip is None:
        return peer
    if peer_ip not in PROXY_RAILWAY:
        return str(peer_ip)

    headers = getattr(conn, "headers", None)
    xff_valores = _cabecalhos(headers, "x-forwarded-for")
    xff = [e.strip() for v in xff_valores for e in v.split(",")]
    ultima = _ip(xff[-1]) if xff else None
    conexao = ultima or peer_ip
    conexao_cf = any(conexao in faixa for faixa in CLOUDFLARE_FAIXAS)

    esperado = _segredo_esperado()
    seg = _cabecalhos(headers, SEGREDO_CABECALHO)
    segredo_ok = (esperado is not None and len(seg) == 1 and isinstance(seg[0], str)
                  and constant_time_eq(seg[0], esperado))
    borda_confiavel = segredo_ok if esperado is not None else conexao_cf

    cf = _cabecalhos(headers, "cf-connecting-ip")
    cf_ip = _ip(cf[0]) if len(cf) == 1 else None
    # Só IP público: loopback/privado/CGNAT/doc colidiriam com o fallback
    # "127.0.0.1" ou com chaves gravadas antes (100.64.x.x) — tabela, linha 5.
    usa_cf = borda_confiavel and cf_ip is not None and _publico(cf_ip)
    cliente = cf_ip if usa_cf else conexao

    xri = _cabecalhos(headers, "x-real-ip")
    xri_ip = _ip(xri[0]) if len(xri) == 1 else None  # duplicado conta como ausente
    primeira = _ip(xff[0]) if xff else None
    # cf_valido: um único CF e IP válido (sem zona), global ou não.
    # cf_global: esse IP passa em `_publico` (false = Pseudo-IPv4 240/4,
    # privado, multicast...); com conexao_cf=true, cf_global=false significa
    # fonte="conexao". *_tipo: categoria de `_tipo`. segredo_*: só booleanos
    # (alarmes: presente e não ok = segredo errado ou regra em "Adicionar";
    # presente e não configurado = regra criada e env ausente/curta).
    _sonda(lambda: {
        "xff_entradas": _ate_3(len(xff)),
        "conexao_cf": conexao_cf,
        "cf_presente": bool(cf),
        "cf_valido": cf_ip is not None,
        "cf_global": _publico(cf_ip) if cf_ip else None,
        "cf_igual_conexao": (cf_ip == conexao) if cf_ip else None,
        "xri_igual_conexao": ultima is not None and xri_ip == ultima,
        "fonte": "cf" if usa_cf else ("conexao" if ultima else "peer"),
        "xff_cabecalhos": _ate_3(len(xff_valores)),
        "xff_primeira_tipo": _tipo(primeira),
        "xff_ultima_tipo": _tipo(ultima),
        "xri_tipo": _tipo(xri_ip),
        "xri_igual_primeira": (xri_ip == primeira) if xri_ip and primeira else None,
        "cf_igual_primeira": (cf_ip == primeira) if cf_ip and primeira else None,
        "segredo_configurado": esperado is not None,
        "segredo_presente": bool(seg),
        "segredo_ok": segredo_ok,
    })
    return str(cliente)


def rate_limit_key(conn: Any) -> str:
    """Chave de rate limit: o `client_ip`, com IPv6 agrupado na rede /64 (um
    aparelho troca de endereço dentro do /64 que recebe). Sem `client` →
    "127.0.0.1", o mesmo fallback do `get_remote_address` do slowapi."""
    ip = client_ip(conn)
    if ip is None:
        return "127.0.0.1"
    parsed = _ip(ip)
    if isinstance(parsed, ipaddress.IPv6Address):
        return str(ipaddress.ip_network(f"{parsed}/64", strict=False))
    return ip
