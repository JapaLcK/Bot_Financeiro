// Envia via endpoint real /contact (Resend no backend). Sem depender de
  // cliente de e-mail no dispositivo (o mailto: antigo falhava sem handler).
  document.getElementById('contact-form').addEventListener('submit', async function(e){
    e.preventDefault();
    const box = document.getElementById('contact-msg');
    const btn = document.getElementById('contact-btn');
    function show(t, ok){ box.textContent = t; box.className = 'contact-msg show ' + (ok ? 'ok' : 'err'); }
    // O `detail` do 422 do FastAPI é uma LISTA de objetos ({loc,msg,type,input}):
    // truthy, e na tela vira "[object Object]". Só string passa; o resto cai no
    // fallback de cada chamada. Mesma regra do `_errDetail` (dashboard.js).
    function strDetail(d) { return d && typeof d.detail === "string" ? d.detail : ""; }
    const body = {
      name:    document.getElementById('nome').value.trim(),
      email:   document.getElementById('email').value.trim(),
      subject: document.getElementById('assunto').value.trim(),
      message: document.getElementById('msg').value.trim(),
      website: document.getElementById('website').value
    };
    if (!body.name || !body.email || !body.subject || !body.message) { show('Preencha nome, e-mail, assunto e mensagem.', false); return; }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(body.email)) { show('Digite um e-mail válido (ex.: seu@email.com).', false); return; }
    const csrf = decodeURIComponent((document.cookie.split('; ').find(function(r){ return r.indexOf('csrf_token=') === 0; }) || '').split('=')[1] || '');
    const headers = { 'Content-Type': 'application/json' };
    if (csrf) headers['x-csrf-token'] = csrf;
    btn.disabled = true; const orig = btn.textContent; btn.textContent = 'Enviando…';
    try {
      const res = await fetch('/contact', { method: 'POST', credentials: 'same-origin', headers: headers, body: JSON.stringify(body) });
      const data = await res.json().catch(function(){ return {}; });
      if (res.ok) { show('Mensagem enviada! Respondemos no seu e-mail em breve.', true); e.target.reset(); }
      else if (res.status === 429) { show('Muitas mensagens em pouco tempo. Tente mais tarde ou escreva pra contato@pigbankai.com.', false); }
      else { show(strDetail(data) || 'Não foi possível enviar. Escreva pra contato@pigbankai.com.', false); }
    } catch { show('Erro de conexão. Tente de novo ou escreva pra contato@pigbankai.com.', false); }
    finally { btn.disabled = false; btn.textContent = orig; }
  });
