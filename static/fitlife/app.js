// Utilidades compartidas: lectura de CSRF y llamadas a la API con fetch.
function getCookie(nombre) {
  const valor = document.cookie.split(';').map(c => c.trim()).find(c => c.startsWith(nombre + '='));
  return valor ? decodeURIComponent(valor.split('=')[1]) : null;
}

async function api(url, opciones = {}) {
  const resp = await fetch(url, {
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': getCookie('csrftoken'),
      'Accept': 'application/json',
    },
    ...opciones,
  });
  let data = null;
  try { data = await resp.json(); } catch (e) { /* sin cuerpo */ }
  if (!resp.ok) {
    const msg = (data && (data.detail || JSON.stringify(data))) || ('Error ' + resp.status);
    throw new Error(msg);
  }
  return data;
}

function escapeHtml(texto) {
  const div = document.createElement('div');
  div.textContent = texto == null ? '' : String(texto);
  return div.innerHTML;
}
