# Vídeo PigBank · 15s

Motion graphics vertical (1080×1920, 60 fps) com trilha sintetizada, sem sample de terceiros.
Registrar → Organizar → Open Finance → agentes → final, sobre o texto do hero
("Sua grana. Uma conversa. Tudo mais claro."). Só assets de `frontend/brand/`; valores ilustrativos.

- `index.html`: a composição. Aberta no navegador, toca em loop.
- `soundtrack.py`: gera `out/soundtrack.wav` (numpy). Os efeitos usam os mesmos instantes das animações.
- `render.mjs`: seek quadro a quadro no Chromium → ffmpeg → `out/pigbank-15s.mp4`.

As dependências Python daqui ficam fora do `requirements.txt` de propósito: são só para gerar o
vídeo e não vão para o app. Instale num venv à parte (o `.venv/` daqui está no `.gitignore`).

```bash
npm ci && npx playwright install chromium
V=marketing/video-15s/.venv
python3 -m venv $V && $V/bin/pip install numpy imageio-ffmpeg
$V/bin/python marketing/video-15s/soundtrack.py
FFMPEG=$($V/bin/python -c "import imageio_ffmpeg as f; print(f.get_ffmpeg_exe())") \
  node marketing/video-15s/render.mjs                                    # ~4 min
node marketing/video-15s/render.mjs --stills 2.5,8.9                    # só PNGs, para revisar
```

O `imageio-ffmpeg` traz um ffmpeg com libx264; qualquer outro com libx264 serve via `FFMPEG`.
Se já houver um Chromium na máquina, dá para pular o `playwright install` e passar
`CHROMIUM=/caminho/do/chrome`.
