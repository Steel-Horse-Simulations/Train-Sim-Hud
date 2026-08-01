/* TSW Hud [v0.1.0] [main] [main] */
/* Last modified: 2025-01-31 */

(async function() {
  const el = document.getElementById('navContainer');
  if (!el) return;
  
  try {
    const items = await fetch('/api/pages').then(r => r.json());
    items.sort((a, b) => a.order - b.order);
    
    const path = location.pathname;
    el.innerHTML = items.map(it => {
      const active = it.url === path || (it.url !== '/' && path.endsWith(it.url.split('/').pop()));
      return `<a href="${it.url}"${active ? ' class="active"' : ''}>${it.name}</a>`;
    }).join('');
  } catch (e) {
    console.error('Error loading nav:', e);
  }
})();
