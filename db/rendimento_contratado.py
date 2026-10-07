"""Taxa contratada de posições BRL próprias. Nunca calcula rentabilidade recebida."""
from datetime import datetime, timezone

from .open_finance_state import _TERMINAL
from .patrimonio import (BANCO_VELHO, POSICOES_BANCO_SQL, desatualizada, fora_do_sync,
                        ler_conexoes, ultima_geracao)


def ler(cur, user_id: int) -> dict:
    # Mesmo recorte latest por identidade/conexão do patrimônio; os ids são
    # somente chaves de query e nunca saem na projeção pública.
    cur.execute(POSICOES_BANCO_SQL, (user_id,))
    posicoes = [p for p in cur.fetchall() if p["connection_status"] not in _TERMINAL
                and p["currency"] == "BRL" and p["status"] != "TOTAL_WITHDRAWAL"]
    conexoes, estados = ler_conexoes(cur, user_id)
    por_id = {c["id"]: c for c in conexoes}
    ultima = ultima_geracao(cur, user_id, "open_finance_investments")
    limite = datetime.now(timezone.utc) - BANCO_VELHO
    itens = []
    for p in posicoes:
        cur.execute("""select i.name, c.institution_name, s.contract_rate,
                              s.contract_rate_type, s.observed_at
                         from open_finance_investments i
                         join open_finance_connections c on c.id=i.connection_id
                         left join lateral (
                           select s.contract_rate, s.contract_rate_type, s.observed_at
                             from open_finance_investment_snapshots s
                            where s.connection_id=i.connection_id
                              and s.provider_investment_id=i.provider_investment_id
                              and s.collection_confirmed
                            order by s.observed_at desc limit 1
                         ) s on true
                        where c.user_id=%s and i.connection_id=%s
                          and i.provider_investment_id=%s""",
                    (user_id, p["connection_id"], p["provider_investment_id"]))
        r = cur.fetchone()
        taxa = r["contract_rate"]
        tipo = r["contract_rate_type"]
        informada = taxa is not None and taxa.is_finite() and bool(tipo)
        motivos = [] if informada else ["taxa_contratada_ausente"]
        c = por_id[p["connection_id"]]
        if desatualizada(c, estados[str(c["id"])], limite):
            motivos.append("banco_desatualizado")
        if fora_do_sync(p, ultima):
            motivos.append("posicao_fora_do_ultimo_sync")
        if (r["observed_at"] is not None and p["updated_at"] is not None
                and r["observed_at"] < p["updated_at"]):
            motivos.append("taxa_de_coleta_anterior")
        itens.append({"nome": r["name"], "instituicao": r["institution_name"],
                      "taxa": taxa if informada else None,
                      "tipo_taxa": tipo if informada else None,
                      "observado_em": r["observed_at"], "motivos": motivos})
    return {"tipo": "contratado", "itens": itens,
            "motivos": [] if itens else ["sem_posicoes_brl"]}
