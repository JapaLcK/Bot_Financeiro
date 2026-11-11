"""O livro das declarações "pelo banco" que a IA arma e confirma (ADR 0006, PR-B (b), D7=B).

Duas linhas por papel, chave única por usuário:
- `acao`: A = '<canal>:<hmac do id da mensagem que armou>:0', estado armada -> consumida -> confirmada, ou descartada;
- `sim` / `nao`: C = '<canal>:<hmac do id da mensagem>', a mensagem com forma de "sim" (ou de "não"), gravada na 1ª vez
  que chega; `acao` (só no `sim`) = a A cuja pendência ela consumiu (null = nenhuma).
`consumida` só nasce no caminho do "sim" (o runner ganhou o CAS da pendência com um "sim" visto agora pela 1ª vez), e o
escritor só grava uma ação `consumida` por ESSE "sim": payload vindo do modelo ou do histórico nunca autoriza gravação.
Sem FK para `launches`: apagar a declaração não apaga o histórico, e um "sim" velho nunca confirma outra ação. Nada é
podado (D9=B); sai só no reset e na exclusão da conta (`db/privacy.py`). Toda query filtra `user_id`.
"""
from __future__ import annotations

from .connection import get_conn


def registra_acao(user_id: int, chave: str, payload_hash: str) -> int:
    """A ação armada (o `ao_confirmar` da IA, ou a confirmação do roteador), com o HMAC do payload congelado. Repetir
    não muda nada. Devolve o `id` da linha (o botão da confirmação do roteador o leva)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""insert into ia_declaracao_livro (user_id, papel, chave, estado, payload_hash)
                       values (%s, 'acao', %s, 'armada', %s)
                       on conflict (user_id, chave) do nothing returning id""", (user_id, chave, payload_hash))
        linha = cur.fetchone()
        if linha is None:
            cur.execute("select id from ia_declaracao_livro where user_id = %s and chave = %s", (user_id, chave))
            linha = cur.fetchone()
        conn.commit()
    return linha["id"]


def registra_sim(user_id: int, chave: str, papel: str = "sim") -> bool:
    """True se a resposta chegou agora pela 1ª vez (o `insert` é o árbitro atômico das reentregas). `papel`: "sim" ou
    "nao", o que ela era na 1ª entrega; a reentrega decide por ele, nunca pelo texto de agora (C11)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""insert into ia_declaracao_livro (user_id, papel, chave) values (%s, %s, %s)
                       on conflict (user_id, chave) do nothing returning 1""", (user_id, papel, chave))
        novo = cur.fetchone() is not None
        conn.commit()
    return novo


def le(user_id: int, chave: str) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        return _le(cur, user_id, chave)


def _le(cur, user_id: int, chave: str) -> dict | None:
    cur.execute("""select papel, estado, acao, launch_id, user_seq from ia_declaracao_livro
                    where user_id = %s and chave = %s""", (user_id, chave))
    return cur.fetchone()


_ACAO = "select id, chave, estado, user_seq, criado_em from ia_declaracao_livro where user_id = %s and papel = 'acao'"


def acao_por_id(user_id: int, acao_id: int) -> dict | None:
    """A ação do botão `decl_*:<id>`, só se for deste usuário (id de outro = None)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(_ACAO + " and id = %s", (user_id, acao_id))
        return cur.fetchone()


def ultima_acao(user_id: int, enviado_em: int, janela) -> dict | None:
    """A última ação armada dentro da `janela` (timedelta) que não é com certeza posterior ao envio `enviado_em`
    (segundos Unix; a comparação é na precisão dele, então o empate conta como anterior)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(_ACAO + """ and criado_em >= now() - %s
                                and date_trunc('second', criado_em) <= to_timestamp(%s)
                              order by criado_em desc, id desc limit 1""", (user_id, janela, enviado_em))
        return cur.fetchone()


def consome(user_id: int, acao: str, sim: str) -> bool:
    """O "sim" `sim` consumiu (CAS) a pendência da ação `acao`: as duas linhas mudam juntas ou nenhuma muda."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""update ia_declaracao_livro set estado = 'consumida', mudou_em = now()
                        where user_id = %s and papel = 'acao' and chave = %s and estado = 'armada'""", (user_id, acao))
        if cur.rowcount == 1:
            cur.execute("""update ia_declaracao_livro set acao = %s, mudou_em = now()
                            where user_id = %s and papel = 'sim' and chave = %s and acao is null""", (acao, user_id, sim))
        if cur.rowcount != 1:
            conn.rollback()
            return False
        conn.commit()
    return True


def descarta(user_id: int, chave: str, de: str = "armada") -> None:
    """Encerra a ação: o "não" da confirmação (`de="armada"`; o abandono e a expiração são derivados, `armada` sem
    pendência viva) ou a única tentativa do "sim" que não gravou (`de="consumida"`)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""update ia_declaracao_livro set estado = 'descartada', mudou_em = now()
                        where user_id = %s and papel = 'acao' and chave = %s and estado = %s""", (user_id, chave, de))
        conn.commit()


def confirma_no_cursor(cur, user_id: int, acao: str, sim: str, launch_id: int, user_seq: int, payload_hash: str) -> None:
    """No cursor do escritor, sob o mutex dele e no mesmo commit do lançamento: só grava a ação que ESTE "sim" consumiu
    (`consumida` e ligada a ele) com o MESMO payload congelado ao armar (`payload_hash`; nulo, de antes da coluna, nunca
    bate). Senão levanta e a transação inteira volta: `DeclaracaoRepetida` (T3) quando a ação já foi gravada;
    `ValueError` quando ela não foi confirmada (armada, abandonada, descartada, inexistente, de outro "sim" ou com outro
    conteúdo)."""
    from .bank_declaracoes import DeclaracaoRepetida
    cur.execute("""update ia_declaracao_livro a set estado = 'confirmada', launch_id = %s, user_seq = %s, mudou_em = now()
                    where a.user_id = %s and a.papel = 'acao' and a.chave = %s and a.estado = 'consumida'
                      and a.payload_hash = %s
                      and exists (select 1 from ia_declaracao_livro s where s.user_id = a.user_id and s.papel = 'sim'
                                     and s.chave = %s and s.acao = a.chave)""",
                (launch_id, user_seq, user_id, acao, payload_hash, sim))
    if cur.rowcount != 1:
        linha = _le(cur, user_id, acao)
        if linha and linha["estado"] == "confirmada":
            raise DeclaracaoRepetida(linha["launch_id"], linha["user_seq"])
        raise ValueError("DECLARACAO_NAO_CONFIRMADA")
