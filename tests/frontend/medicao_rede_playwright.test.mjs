import assert from "node:assert/strict";
import { test } from "node:test";
import { instrumentarRede } from "../../scripts/lib/medicao_rede_playwright.mjs";

class CdpFalso {
  handlers = new Map();

  on(evento, handler) { this.handlers.set(evento, handler); }
  async send() {}
  async detach() {}
  emitir(evento, dados) { this.handlers.get(evento)?.(dados); }
}

test("conta bytes recebidos antes de uma resposta ser abortada", async () => {
  const cdp = new CdpFalso();
  const page = {
    context: () => ({ newCDPSession: async () => cdp }),
    waitForTimeout: async () => {},
  };
  const rede = await instrumentarRede(page, { throttle: false, rede: {} });
  cdp.emitir("Network.requestWillBeSent", {
    requestId: "video", request: { url: "https://pigbankai.com/brand/vsl.mp4" }, type: "Media",
  });
  cdp.emitir("Network.responseReceived", {
    requestId: "video", type: "Media", response: { url: "https://pigbankai.com/brand/vsl.mp4", status: 206 },
  });
  cdp.emitir("Network.dataReceived", { requestId: "video", encodedDataLength: 512 });
  cdp.emitir("Network.loadingFailed", { requestId: "video" });

  assert.deepEqual(rede.recursos(), [{
    url: "https://pigbankai.com/brand/vsl.mp4", tipo: "Media", status: 206,
    bytes: 512, estado: "interrompido",
  }]);
});

test("considera a contagem final do CDP para recursos concluídos", async () => {
  const cdp = new CdpFalso();
  const page = {
    context: () => ({ newCDPSession: async () => cdp }),
    waitForTimeout: async () => {},
  };
  const rede = await instrumentarRede(page, { throttle: false, rede: {} });
  cdp.emitir("Network.requestWillBeSent", {
    requestId: "css", request: { url: "https://pigbankai.com/style.css" }, type: "Stylesheet",
  });
  cdp.emitir("Network.loadingFinished", { requestId: "css", encodedDataLength: 128 });

  assert.equal(rede.recursos()[0].bytes, 128);
  assert.equal(rede.recursos()[0].estado, "concluido");
});

test("preserva status e bytes do salto anterior em um redirecionamento", async () => {
  const cdp = new CdpFalso();
  const page = {
    context: () => ({ newCDPSession: async () => cdp }),
    waitForTimeout: async () => {},
  };
  const rede = await instrumentarRede(page, { throttle: false, rede: {} });
  cdp.emitir("Network.requestWillBeSent", {
    requestId: "pagina", request: { url: "https://pigbankai.com/antiga" }, type: "Document",
  });
  cdp.emitir("Network.requestWillBeSent", {
    requestId: "pagina", request: { url: "https://pigbankai.com/nova" }, type: "Document",
    redirectResponse: { url: "https://pigbankai.com/antiga", status: 301, encodedDataLength: 96 },
  });

  assert.deepEqual(rede.recursos()[0], {
    url: "https://pigbankai.com/antiga", tipo: "Document", status: 301,
    bytes: 96, estado: "concluido",
  });
});

test("aquecimento só termina depois do recurso da primeira origem", async () => {
  const cdp = new CdpFalso();
  let aguardou = false;
  const page = {
    context: () => ({ newCDPSession: async () => cdp }),
    waitForTimeout: async () => {
      aguardou = true;
      cdp.emitir("Network.loadingFinished", { requestId: "video", encodedDataLength: 256 });
    },
  };
  const rede = await instrumentarRede(page, { throttle: false, rede: {} });
  cdp.emitir("Network.requestWillBeSent", {
    requestId: "video", request: { url: "https://pigbankai.com/brand/vsl.mp4" }, type: "Media",
  });

  await rede.esperarCacheDaOrigem("https://pigbankai.com", 100);

  assert.equal(aguardou, true);
  assert.equal(rede.recursos()[0].estado, "concluido");
});
