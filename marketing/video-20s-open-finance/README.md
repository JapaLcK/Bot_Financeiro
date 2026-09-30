# Vídeo PigBank · Open Finance · 20s

Anúncio vertical (1080×1920, 60 fps) feito de uma única animação contínua: cada cena nasce de um
objeto da anterior (vórtice → celular → botão → linhas → hub → dashboard → donut → cartão → parcelas →
timeline → gráfico → dashboard claro → formas → linhas → escudo → contorno do Piggy → marca).
Só assets oficiais de `frontend/brand/` e `app/assets/brand/`; valores ilustrativos (há nota no vídeo).

| arquivo | papel |
|---|---|
| `index.html`, `main.js` | boot, fontes, imagens; `window.__seek(t)` desenha o quadro (motion blur por subamostragem + grão) |
| `lib.js` | easings, cores/temas, texto cinético (`rollText`), morph de caminhos |
| `items.js` | objetos financeiros (linhas, cartão, pílulas, mini gráficos) usados no caos e no app |
| `hook.js` | 0–3s: câmera em perspectiva, título que se espalha e é sugado pelo vórtice |
| `connect.js` | 3–6s: celular, toque, bordas do botão viram linhas, nós, hub |
| `dash.js`, `payoff.js` | 6–16s: dashboard no "mundo" e a câmera que viaja por ele; íris para o tema claro |
| `finale.js` | 16–20s: cards → formas → linhas, toggle → escudo, contorno do Piggy (traçado do alfa do mascote) |
| `scenes.js` | orquestra as passadas de tema (íris) |
| `soundtrack.py` | trilha e sound design sintetizados (numpy), 120 bpm, sincronizados aos mesmos instantes |
| `render.mjs` | seek quadro a quadro no Chromium → ffmpeg → MP4 |

As dependências Python ficam num venv local (`.venv/` está no `.gitignore`), fora do `requirements.txt`.

```bash
cd marketing/video-20s-open-finance
python3 -m venv .venv && .venv/bin/pip install numpy imageio-ffmpeg
.venv/bin/python soundtrack.py                          # out/soundtrack.wav
FFMPEG=$(.venv/bin/python -c "import imageio_ffmpeg as f; print(f.get_ffmpeg_exe())") \
  node render.mjs                                       # out/pigbank-open-finance-20s.mp4 (~10 min)
node render.mjs --stills 2.5,8.9                        # PNGs soltos, para revisar
node render.mjs --sheet 0,1,2,3 --cols 4                # folha de contato (out/sheet.png)
```

`render.mjs` precisa do pacote `playwright` resolvível a partir da raiz do repo (`npm ci`) e de um
Chromium; se o do Playwright não bater com a versão, passe `CHROMIUM=/caminho/chrome`.

Ao mudar um instante de animação, mude o efeito correspondente em `soundtrack.py`.
