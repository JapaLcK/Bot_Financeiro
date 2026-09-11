/** Instrumentação CDP para medir bytes já recebidos, inclusive em respostas abertas. */
export async function instrumentarRede(page, { throttle, rede }) {
  const cdp = await page.context().newCDPSession(page);
  const ativos = new Map();
  const recursos = [];
  let sequencia = 0;

  const atual = id => ativos.get(id);
  const registrar = evento => {
    const anterior = atual(evento.requestId);
    if (anterior) {
      const redirecionamento = evento.redirectResponse;
      anterior.url = redirecionamento?.url || anterior.url;
      anterior.status = redirecionamento?.status ?? anterior.status;
      anterior.bytes = Math.max(anterior.bytes, redirecionamento?.encodedDataLength || 0);
      anterior.concluido = true;
      anterior.estado = "concluido";
    }
    const recurso = {
      id: `${evento.requestId}:${sequencia += 1}`,
      requestId: evento.requestId,
      url: evento.request.url,
      metodo: evento.request.method || "GET",
      tipo: evento.type || "Other",
      status: null,
      bytes: 0,
      concluido: false,
      estado: "em_andamento",
    };
    ativos.set(evento.requestId, recurso);
    recursos.push(recurso);
  };
  const finalizar = (evento, estado) => {
    const recurso = atual(evento.requestId);
    if (!recurso) return;
    recurso.bytes = Math.max(recurso.bytes, evento.encodedDataLength || 0);
    recurso.concluido = estado === "concluido";
    recurso.estado = estado;
    recurso.erro = evento.errorText || evento.blockedReason || null;
  };

  cdp.on("Network.requestWillBeSent", registrar);
  cdp.on("Network.responseReceived", evento => {
    const recurso = atual(evento.requestId);
    if (!recurso) return;
    recurso.url = evento.response.url;
    recurso.tipo = evento.type || recurso.tipo;
    recurso.status = evento.response.status;
    // O acumulado inicial inclui os cabeçalhos; os próximos dataReceived
    // acrescentam o corpo. loadingFinished reconcilia o total sem somá-lo de novo.
    recurso.bytes = Math.max(recurso.bytes, evento.response.encodedDataLength || 0);
  });
  cdp.on("Network.dataReceived", evento => {
    const recurso = atual(evento.requestId);
    if (recurso) recurso.bytes += evento.encodedDataLength || 0;
  });
  cdp.on("Network.loadingFinished", evento => finalizar(evento, "concluido"));
  cdp.on("Network.loadingFailed", evento =>
    finalizar(evento, evento.canceled ? "interrompido" : "falhou"));

  await cdp.send("Network.enable");
  if (throttle) {
    await cdp.send("Network.emulateNetworkConditions", rede);
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
  }

  const cacheaveisDaOrigem = origem => recursos.filter(recurso =>
    ["GET", "HEAD"].includes(recurso.metodo) && new URL(recurso.url).origin === origem,
  );
  const pendentesDaOrigem = origem => cacheaveisDaOrigem(origem)
    .filter(recurso => recurso.estado === "em_andamento");
  const falhosDaOrigem = origem => cacheaveisDaOrigem(origem)
    .filter(recurso => recurso.estado === "falhou");

  return {
    recursos: () => recursos.map(recurso => ({
      url: recurso.url,
      tipo: recurso.tipo,
      status: recurso.status,
      bytes: recurso.bytes,
      estado: recurso.estado,
    })),
    async esperarCacheDaOrigem(origem, limiteMs) {
      const inicio = Date.now();
      while (true) {
        const falhos = falhosDaOrigem(origem);
        if (falhos.length) {
          const detalhes = falhos.map(recurso =>
            `${recurso.url} (${recurso.erro || "erro de rede"})`).join(", ");
          throw new Error(`cache falhou ao aquecer: ${detalhes}`);
        }
        if (!pendentesDaOrigem(origem).length) return;
        if (Date.now() - inicio >= limiteMs) {
          const pendentes = pendentesDaOrigem(origem).map(recurso => recurso.url).join(", ");
          throw new Error(`cache não aqueceu dentro de ${limiteMs} ms: ${pendentes}`);
        }
        await page.waitForTimeout(100);
      }
    },
    detach: () => cdp.detach(),
  };
}
