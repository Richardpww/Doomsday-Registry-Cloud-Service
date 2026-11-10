const $ = id => document.getElementById(id);

const esc = s => String(s).replace(/[&<>"']/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[c]));

const hdr = () => ({
  'X-Access-Level': $('lvl').value,
  'Content-Type': 'application/json'
});

async function load() {
  const rows = await (await fetch('/api/products', { headers: hdr() })).json();
  $('list').innerHTML = rows.map(p => `<li>
    [${esc(p.category)}] <b>${esc(p.name)}</b> — supplier: ${esc(p.supplier)} —
    stock ${p.stock} ${p.stock_status === 'CRITICAL' ? '<span class="badge-critical">⚠️ CRITICAL</span>' : ''}
    <div class="action-btn">
      <button onclick="setStock(${p.id}, ${p.stock})">Change stock</button>
      <button onclick="del(${p.id})">Delete</button>
    </div>
  </li>`).join('');
}

async function add() {
  const r = await fetch('/api/products', {
    method: 'POST',
    headers: hdr(),
    body: JSON.stringify({
      category: $('cat').value,
      name: $('name').value,
      supplier: $('sup').value,
      description: $('desc').value,
      stock: parseInt($('stock').value, 10)
    })
  });
  $('msg').textContent = r.status + ' ' + JSON.stringify(await r.json());
  load();
}

async function setStock(id, cur) {
  const v = prompt('New stock:', cur);
  if (v === null) return;
  const r = await fetch('/api/products/' + id, {
    method: 'PATCH',
    headers: hdr(),
    body: JSON.stringify({ stock: parseInt(v, 10) })
  });
  $('msg').textContent = r.status + ' ' + JSON.stringify(await r.json());
  load();
}

async function del(id) {
  await fetch('/api/products/' + id, { method: 'DELETE' });
  load();
}

load();