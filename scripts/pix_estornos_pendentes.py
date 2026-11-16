"""Estornos do Pix sem o marcador `pix_estornos_recebidos` (#1437): grava o que falta e drena os abertos.

É o passo 4 do roteiro do deploy do #1437 (`docs/CLAUDE.md`), rodado COM A PAUSA DA CONCESSÃO ATIVA
(#1574). A versão anterior ao #1437 não gravava o marcador; um `RECEIVED` velho drenado depois dela
concederia acesso sobre dinheiro devolvido.

Duas fontes, e um id que aparece nas duas conta uma vez:
1. a outbox: todo `PAYMENT_REFUNDED` com `payload_enc` ainda não purgado (até 7 dias), processado ou
   não. Decifra com finalidade própria (`pix_estornos_pendentes`) e lê só o `payment.id` e a referência;
2. o Asaas: `GET /v3/payments?status=REFUNDED`, todas as páginas (`asaas.listar_estornados`), que alcança
   o estorno de evento já purgado. Filtro e paginação ainda a conferir no sandbox; por isso o script
   NÃO confia no filtro: item com `status` diferente de `REFUNDED` (ou sem `status`) não vira marcador,
   conta em `asaas_status_inesperado` e segura o código 1 (um pendente marcado nunca mais concederia).
   A paginação por `offset` pode pular um item que mude de status durante a execução: rodar a
   simulação de novo resolve. Pendência do sandbox: se um estorno PARCIAL aparecer como `REFUNDED`
   nesta listagem, ele seria marcado e bloquearia (a D1 diz que o parcial nunca bloqueia); a listagem
   sozinha não distingue os dois.

Sem `--aplicar` é simulação: lê e conta, não grava nem drena. Com `--aplicar`:
- grava o marcador que falta, com a mesma função da recepção (`gravar`: classifica `nosso`,
  `alheio` ou `nao_identificado`; nada é apagado);
- roda `drenar_evento` em cada `PAYMENT_REFUNDED` ainda aberto. O dreno não levanta: o `revoke` que
  falha deixa o evento aberto, com o marcador gravado (P-R3b: a retomada não espera por ele).

Saída: só contagens, sem id, referência, valor ou cliente; a exceção é o `event_id` (id opaco do
Asaas, sem conteúdo) de cada linha da fila que não decifra. Código 0: nenhum estorno sem marcador e
nenhuma falha. Código 1: sobrou estorno sem marcador (na simulação, qualquer um), ou falhou ler o
Asaas, gravar ou decifrar uma linha da fila, ou a listagem trouxe status que não é `REFUNDED`. `abertos_depois` é informativo. Rodar de novo é seguro.

Linha ilegível (`falha_fila`) SEGURA o código 1, de propósito: não se sabe de que pagamento ela é.
As outras linhas seguem sendo lidas e marcadas. Como resolver: (1) confira `PII_ENCRYPTION_KEY` e as
versões antigas da chave (a causa comum) e rode de novo; (2) se a chave está certa, a linha está
corrompida: confirme `falha_asaas: 0` (a listagem do Asaas cobre todo estorno da conta, de qualquer
idade) e purgue só ela, como a purga de 7 dias faria: `update pix_webhook_events set payload_enc =
null, last_error = null, purged_at = now() where event_id = '<event_id impresso>';`. Depois disso a
simulação volta a 0.

Rodar (o dono, na raiz do repositório, com as variáveis de produção, incluindo `PII_ENCRYPTION_KEY`,
`ASAAS_API_KEY` e `ASAAS_CONTA`):
    DATABASE_URL='postgres://...' .venv/bin/python scripts/pix_estornos_pendentes.py [--aplicar]

Teste: tests/test_pix_estornos_pendentes.py.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.crypto import PiiAccessContext, decrypt_pii_optional  # noqa: E402
from core.services import asaas  # noqa: E402
from core.services.pix_drain import drenar_evento  # noqa: E402
from db.connection import get_conn  # noqa: E402
from db.pix_estornos import ID_DO_PAGAMENTO, estorno_registrado, gravar  # noqa: E402

_ABERTOS = ("select event_id from pix_webhook_events"
            " where event_type = 'PAYMENT_REFUNDED' and processed_at is null")


def _da_fila(n: Counter) -> list[tuple[str, dict]]:
    """`(event_id, payment)` de cada `PAYMENT_REFUNDED` ainda com payload. A linha que não decifra
    conta em `falha_fila`, sai com o `event_id` (sem conteúdo) e não esconde as outras."""
    with get_conn() as conn:
        linhas = conn.execute("select event_id, payload_enc from pix_webhook_events"
                              " where event_type = 'PAYMENT_REFUNDED' and payload_enc is not null"
                              " order by received_at").fetchall()
    saida = []
    for linha in linhas:
        try:
            bruto = decrypt_pii_optional(linha["payload_enc"], ctx=PiiAccessContext(
                purpose="pix_estornos_pendentes", actor="system:pix_estornos_pendentes",
                subject_user_id=None, field="asaas_webhook_payload"))
        except Exception as exc:  # noqa: BLE001 — chave errada ou linha corrompida; o tipo basta
            n["falha_fila"] += 1
            print(f"ilegivel na fila: {linha['event_id']} ({type(exc).__name__})")
            continue
        try:
            pagamento = json.loads(bruto or "{}").get("payment")
        except (ValueError, AttributeError):
            pagamento = None
        saida.append((linha["event_id"], pagamento if isinstance(pagamento, dict) else {}))
    return saida


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Estornos do Pix sem marcador (#1437).")
    parser.add_argument("--aplicar", action="store_true", help="grava os marcadores e drena os abertos")
    aplicar = parser.parse_args(argv).aplicar
    n, vistos = Counter(), set()
    fontes = (("fila", lambda: _da_fila(n)),
              ("asaas", lambda: [("pix_estornos_pendentes", p if isinstance(p, dict) else {})
                                 for p in asaas.listar_estornados()]))
    for nome, ler in fontes:
        try:
            itens = ler()
        except Exception as exc:  # noqa: BLE001 — o tipo basta; a mensagem pode trazer a URL
            n[f"falha_{nome}"] += 1
            print(f"falha ao ler {nome}: {type(exc).__name__}")
            continue
        for event_id, pagamento in itens:
            n[nome] += 1
            if nome == "asaas" and pagamento.get("status") != "REFUNDED":
                n["asaas_status_inesperado"] += 1  # filtro ignorado: um pendente marcado nunca mais concederia
                continue
            pid = pagamento.get("id")
            if not (isinstance(pid, str) and ID_DO_PAGAMENTO.fullmatch(pid)):
                n["sem_id_valido"] += 1
                continue
            if pid in vistos or estorno_registrado(pid):
                n["com_marcador"] += pid not in vistos
                vistos.add(pid)
                continue
            vistos.add(pid)
            n["sem_marcador"] += 1
            if aplicar:
                try:
                    with get_conn() as conn:
                        with conn.cursor() as cur:
                            gravar(pid, pagamento.get("externalReference"), event_id, cur)
                        conn.commit()
                    n["gravados"] += 1
                except Exception as exc:  # noqa: BLE001
                    n["falha_gravar"] += 1
                    print(f"falha ao gravar: {type(exc).__name__}")
    with get_conn() as conn:
        abertos = [r["event_id"] for r in conn.execute(_ABERTOS).fetchall()]
    n["abertos_antes"] = len(abertos)
    if aplicar:
        for event_id in abertos:
            drenar_evento(event_id)
        with get_conn() as conn:
            n["abertos_depois"] = len(conn.execute(_ABERTOS).fetchall())
    falta = n["sem_marcador"] - n["gravados"]
    falhas = n["falha_fila"] + n["falha_asaas"] + n["falha_gravar"] + n["asaas_status_inesperado"]
    print("modo:", "aplicar" if aplicar else "simulacao")
    for chave in ("fila", "asaas", "asaas_status_inesperado", "sem_id_valido", "com_marcador", "sem_marcador", "gravados",
                  "falha_fila", "falha_asaas", "falha_gravar", "abertos_antes", "abertos_depois"):
        print(f"{chave}: {n[chave]}")
    print(f"resultado: {'pendente' if falta or falhas else 'ok'} (sem marcador restante: {falta}, falhas: {falhas})")
    return 1 if falta or falhas else 0


if __name__ == "__main__":
    sys.exit(main())
