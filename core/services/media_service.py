# core/services/media_service.py
"""
Serviço de processamento de mídia: áudio e imagens.

Fluxo de áudio:
  1. Recebe bytes de arquivo de voz (.ogg, .mp3, .m4a, .wav, .webm)
  2. Transcreve via Whisper API
  3. Retorna o texto transcrito

Fluxo de imagem:
  1. Recebe bytes de imagem (.jpg, .png, .webp, .gif)
  2. Envia para GPT-4o Vision com prompt financeiro
  3. Retorna dict estruturado com os dados extraídos (valor, alvo, tipo, etc.)
     ou None se a imagem não contiver informação financeira clara.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tipos de arquivo suportados
# ---------------------------------------------------------------------------

AUDIO_EXTENSIONS = {".ogg", ".mp3", ".m4a", ".wav", ".webm", ".mpeg", ".mp4"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

AUDIO_CONTENT_TYPES = {
    "audio/ogg", "audio/mpeg", "audio/mp4", "audio/wav",
    "audio/webm", "video/webm", "audio/x-wav", "audio/m4a",
}
IMAGE_CONTENT_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/gif",
}


def is_audio_attachment(filename: str, content_type: str) -> bool:
    fn = (filename or "").lower()
    ct = (content_type or "").lower().split(";")[0].strip()
    ext = _ext(fn)
    return ext in AUDIO_EXTENSIONS or ct in AUDIO_CONTENT_TYPES


def is_image_attachment(filename: str, content_type: str) -> bool:
    fn = (filename or "").lower()
    ct = (content_type or "").lower().split(";")[0].strip()
    ext = _ext(fn)
    return ext in IMAGE_EXTENSIONS or ct in IMAGE_CONTENT_TYPES


def _ext(filename: str) -> str:
    parts = filename.rsplit(".", 1)
    return f".{parts[-1]}" if len(parts) == 2 else ""


# ---------------------------------------------------------------------------
# Transcrição de áudio (Whisper)
# ---------------------------------------------------------------------------

def transcribe_audio(data: bytes, filename: str) -> str | None:
    """
    Transcreve áudio usando Whisper da OpenAI.
    Retorna o texto transcrito ou None em caso de falha.
    """
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        logger.warning("[media_service] OPENAI_API_KEY não configurada — transcrição indisponível.")
        return None

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        # Whisper precisa de um file-like com nome para inferir o formato
        ext = _ext(filename.lower()) or ".ogg"
        audio_file = io.BytesIO(data)
        audio_file.name = f"audio{ext}"

        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="pt",          # força português para maior precisão
            response_format="text",
        )

        transcription = (response or "").strip()
        logger.info("[media_service] Transcrição: %r", transcription[:120])
        return transcription if transcription else None

    except Exception as e:
        logger.error("[media_service] Erro ao transcrever áudio: %s", e)
        return None


# ---------------------------------------------------------------------------
# Análise de imagem (GPT-4o Vision)
# ---------------------------------------------------------------------------

_IMAGE_SYSTEM_PROMPT = """Você é um assistente financeiro que analisa imagens enviadas pelo usuário.

Seu objetivo é identificar se a imagem contém informações financeiras (recibo, nota fiscal, comprovante de pagamento, extrato, conta, boleto, screenshot de transferência, etc.) e extrair os dados relevantes.

REGRAS:
1. Retorne SOMENTE JSON. Nenhum texto antes ou depois.
2. Se a imagem NÃO contiver informação financeira clara, retorne: {"tem_dado_financeiro": false}
3. Se contiver, extraia os campos que conseguir identificar com segurança.
4. Nunca invente valores. Se não conseguir ler um número claramente, omita o campo ou coloque null.
5. tipo deve ser "despesa" para gastos/pagamentos e "receita" para recebimentos/depósitos.
6. Para categoria, use: alimentação | transporte | saúde | moradia | lazer | educação | assinaturas | pets | compras online | beleza | outros

FORMATO OBRIGATÓRIO (JSON puro):
{
  "tem_dado_financeiro": true,
  "tipo": "despesa" | "receita",
  "valor": <número ou null>,
  "alvo": "<estabelecimento/destinatário ou null>",
  "data": "<YYYY-MM-DD ou null>",
  "categoria": "<categoria ou null>",
  "descricao_extra": "<observação curta sobre o que foi identificado>"
}

EXEMPLOS:
- Foto de cupom fiscal do supermercado → {"tem_dado_financeiro": true, "tipo": "despesa", "valor": 87.50, "alvo": "Pão de Açúcar", "data": null, "categoria": "alimentação", "descricao_extra": "Cupom fiscal de supermercado"}
- Screenshot de Pix recebido → {"tem_dado_financeiro": true, "tipo": "receita", "valor": 500.00, "alvo": "João Silva", "data": "2026-04-20", "categoria": "outros", "descricao_extra": "Comprovante de Pix recebido"}
- Foto de paisagem → {"tem_dado_financeiro": false}
"""


def analyze_image(data: bytes, filename: str) -> dict[str, Any] | None:
    """
    Analisa uma imagem com GPT-4o Vision para extrair dados financeiros.
    Retorna dict com os dados ou None em caso de falha.
    """
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        logger.warning("[media_service] OPENAI_API_KEY não configurada — análise de imagem indisponível.")
        return None

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        # Determina o mime type para o base64
        ext = _ext(filename.lower())
        mime_map = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
        }
        mime = mime_map.get(ext, "image/jpeg")

        b64 = base64.standard_b64encode(data).decode("utf-8")
        image_url = f"data:{mime};base64,{b64}"

        response = client.chat.completions.create(
            model="gpt-4o",
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _IMAGE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": image_url, "detail": "high"},
                        },
                        {
                            "type": "text",
                            "text": "Analise esta imagem e extraia os dados financeiros conforme as instruções.",
                        },
                    ],
                },
            ],
            max_tokens=500,
        )

        raw = response.choices[0].message.content or "{}"
        result = json.loads(raw)
        logger.info("[media_service] Análise de imagem: %r", result)
        return result

    except Exception as e:
        logger.error("[media_service] Erro ao analisar imagem: %s", e)
        return None
