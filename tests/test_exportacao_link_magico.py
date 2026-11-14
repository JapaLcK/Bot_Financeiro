"""PR-11 B (#637), PB8: o código do link mágico `/d/{code}` não sai em `conversas_ia`, em nenhuma mensagem.

O T4 (`tests/test_exportacao_completa.py`) cobre o caminho rápido ("painel", uma linha `assistant`). Aqui: a mesma
mensagem com o código duas vezes, e a linha `tool` no formato do caminho com o modelo
(`core/services/ai_chat/runner.py`, `json.dumps({"status": "done", "message": ...})`).

CONTROLES rodados à mão, cada um deixa `test_codigo_sai_de_toda_mensagem_e_de_toda_ocorrencia` vermelho: trocar
só a 1ª ocorrência do código, e tratar só `role == "assistant"`.
"""
from __future__ import annotations

import io
import json
import re
import uuid
import zipfile

import db
from core.handlers.dashboard import open_dashboard
from core.services.ai_chat import runner
from db.privacy_export import build_user_export_zip

_LINK = re.compile(r"https?://\S+/d/([A-Za-z0-9_-]+)")


def test_codigo_sai_de_toda_mensagem_e_de_toda_ocorrencia():
    uid = 100_000_000 + uuid.uuid4().int % 800_000_000
    db.ensure_user(uid)
    rapido = runner.chat(uid, "painel")  # linha `assistant` do caminho rápido
    repetido = open_dashboard(uid)
    link = _LINK.search(repetido)
    db.ai_append_message(uid, "assistant", f"{repetido}\n\nSe não abrir, toque de novo: {link[0]}")
    da_ferramenta = open_dashboard(uid)
    db.ai_append_message(uid, "tool", json.dumps({"status": "done", "message": da_ferramenta}, ensure_ascii=False,
                                                 default=str), tool_call_id="call_1", tool_name="open_dashboard")
    codigos = [_LINK.search(t)[1] for t in (rapido, repetido, da_ferramenta)]
    assert len(set(codigos)) == 3

    z = zipfile.ZipFile(io.BytesIO(build_user_export_zip(uid)))
    texto = "".join(z.read(n).decode("utf-8") for n in z.namelist())
    assert [c for c in codigos if c in texto] == []
    # positivo: as mensagens continuam, só com o código trocado
    conversa = {m["role"] + str(m["content"].count("/d/")): m["content"]
                for m in json.loads(z.read("dados.json"))["dados"]["conversas_ia"] if m["role"] != "user"}
    assert conversa["assistant2"].count("/d/(removido)") == 2
    assert "/d/(removido)" in conversa["tool1"] and json.loads(conversa["tool1"])["status"] == "done"
    assert "/d/(removido)" in conversa["assistant1"]
