"""IP real do cliente atrás de Cloudflare → Railway (issue #766).

O uvicorn só confia em `X-Forwarded-For` vindo de 127.0.0.1 (`forwarded_allow_ips`
padrão), e no Railway o peer TCP é o proxy dele, em 100.64.x.x — então
`request.client.host` (e o `get_remote_address` do slowapi) é o IP do proxy, não o
do usuário. Regra em dois passos:

1. **Conexão.** Só se o peer está em `PROXY_RAILWAY`, vale a ÚLTIMA entrada do
   `X-Forwarded-For` (a que o Railway escreve; as anteriores o cliente controla).
   Entrada inválida ou cabeçalho ausente → o peer. Peer fora da faixa → o peer, e
   os cabeçalhos são IGNORADOS.
2. **Cliente.** Se a conexão é um IP da Cloudflare (`CLOUDFLARE_FAIXAS`) e há
   exatamente um `CF-Connecting-IP` que é IP válido, público (`_publico`) e sem
   zona IPv6, ele é o cliente. Senão, a conexão.

`PROXY_RAILWAY` é SUPOSIÇÃO apoiada na medição do dono em produção (todas as chaves
`ip:` de `auth_rate_limits` em 100.64.x.x); o Railway não documenta a faixa. A
única suposição de segurança da regra é que o Railway ESCREVE a última entrada do
XFF; se ele passasse a repassar o XFF do cliente sem acrescentar, o IP ficaria
falsificável — a sonda abaixo vigia isso (`xff_entradas`, `xri_igual_conexao`).

Ressalva de segurança: a borda do Railway é alcançável sem passar pela Cloudflare,
por isso o `CF-Connecting-IP` só vale quando a conexão É da Cloudflare — sem essa
checagem qualquer um forja o IP. Quem passa pela Cloudflare não forja o IP de
outra pessoa (a Cloudflare sobrescreve o `CF-Connecting-IP`, pela documentação
dela; não verificado aqui). Risco residual aceito: Worker/zona do atacante sai de
IPs da Cloudflare e escreve o cabeçalho que quiser (pior caso: divide balde de
rate limit com outro tráfego de Worker). O fechamento completo (Transform Rule
com cabeçalho secreto) está fora do plano da #766.

Lista desatualizada falha para o lado SEGURO: um IP novo da Cloudflare fora da
lista cai no IP da borda (a granularidade de antes desta mudança), nunca abre
falsificação. Atualizar = PR que muda a constante.
"""
from __future__ import annotations

import ipaddress
import sys
from threading import Thread
from typing import Any

from core.system_event_log import log_system_event_sync


PROXY_RAILWAY = ipaddress.ip_network("100.64.0.0/10")

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
# memória e despejar periodicamente.
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


def _cabecalhos(headers: Any, nome: str) -> list[str]:
    """Todos os valores do cabeçalho; `getlist` (Starlette) só se existir —
    há chamador que passa objeto sem `.headers` ou com um dict simples."""
    if headers is None:
        return []
    if hasattr(headers, "getlist"):
        return list(headers.getlist(nome))
    valor = headers.get(nome)
    return [] if valor is None else [valor]


def _sonda(details: dict) -> None:
    """Grava em `system_event_logs` cada combinação NOVA de sinais. Só booleanos
    e contagens: nunca IP, user agent ou path."""
    chave = tuple(details.values())
    if chave in _SONDA_VISTAS:
        return
    _SONDA_VISTAS.add(chave)
    try:
        # Thread daemon e `log_system_event_sync` (que já engole a falha do
        # banco), nunca `logging`: um warning aqui reentraria no
        # `_DashboardHandler`. O except amplo cobre o que sobra — a thread não
        # subir —, porque a sonda é medição e não pode derrubar o login.
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
    xff = [e.strip() for v in _cabecalhos(headers, "x-forwarded-for") for e in v.split(",")]
    ultima = _ip(xff[-1]) if xff else None
    conexao = ultima or peer_ip
    conexao_cf = any(conexao in faixa for faixa in CLOUDFLARE_FAIXAS)

    cf = _cabecalhos(headers, "cf-connecting-ip")
    cf_ip = _ip(cf[0]) if len(cf) == 1 else None
    # Só IP público: loopback/privado/CGNAT/doc colidiriam com o fallback
    # "127.0.0.1" ou com chaves gravadas antes (100.64.x.x) — tabela, linha 5.
    usa_cf = conexao_cf and cf_ip is not None and _publico(cf_ip)
    cliente = cf_ip if usa_cf else conexao

    xri = _cabecalhos(headers, "x-real-ip")
    # cf_valido: um único CF e IP válido (sem zona), global ou não.
    # cf_global: esse IP passa em `_publico` (false = Pseudo-IPv4 240/4,
    # privado, multicast...); com conexao_cf=true, cf_global=false significa
    # fonte="conexao".
    _sonda({
        "xff_entradas": len(xff) if len(xff) < 3 else "3+",
        "conexao_cf": conexao_cf,
        "cf_presente": bool(cf),
        "cf_valido": cf_ip is not None,
        "cf_global": _publico(cf_ip) if cf_ip else None,
        "cf_igual_conexao": (cf_ip == conexao) if cf_ip else None,
        "xri_igual_conexao": ultima is not None and len(xri) == 1 and _ip(xri[0]) == ultima,
        "fonte": "cf" if usa_cf else ("conexao" if ultima else "peer"),
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
