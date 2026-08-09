(async function () {
  const container = document.getElementById('navContainer');
  if (!container) return;
  let items = [];
  try {
    const res = await fetch('/api/pages');
    items = await res.json();
  } catch (e) {
    return;
  }
  items.sort((a, b) => a.order - b.order);
  const path = location.pathname;
  container.innerHTML = items.map(it => {
    const active = (it.url === path) || (it.url !== '/' && path.endsWith(it.url.replace('/pages/', '')));
    return `<a href="${it.url}"${active ? ' class="active"' : ''}>${it.name}</a>`;
  }).join('\n');
})();
