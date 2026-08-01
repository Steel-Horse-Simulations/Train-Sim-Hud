// Shared "Add to Home Screen" helper, used by any page that links its own
// manifest. Not every browser/Android version fires the automatic
// beforeinstallprompt event without a Service Worker registered (which this
// app doesn't have yet - that's part of the separate, not-yet-built offline
// sync feature). So this offers a real one-tap Install button when the
// browser does support it, and falls back to plain instructions otherwise -
// never leaves the person with a broken/missing button.
(function () {
  let deferredPrompt = null;

  function makeBanner() {
    const banner = document.createElement('div');
    banner.id = 'install-banner';
    banner.style.cssText =
      'position:fixed; left:16px; right:16px; bottom:16px; z-index:999; ' +
      'background:rgba(20,18,24,0.95); border:1px solid rgba(255,255,255,0.08); ' +
      'border-radius:10px; padding:12px 14px; font-family:Barlow,sans-serif; ' +
      'font-size:13px; color:#eef0f4; display:flex; align-items:center; gap:12px; ' +
      'box-shadow:0 10px 30px rgba(0,0,0,0.4);';
    return banner;
  }

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
    const banner = makeBanner();
    banner.innerHTML =
      '<span style="flex:1;">Add this to your home screen for a full-screen app icon.</span>' +
      '<button id="install-btn" style="background:#a06bf0; color:#1a1013; border:none; ' +
      'padding:7px 16px; border-radius:6px; font-weight:700; font-size:13px; cursor:pointer;">Install</button>' +
      '<button id="install-dismiss" style="background:transparent; border:1px solid rgba(255,255,255,0.15); ' +
      'color:#eef0f4; padding:7px 12px; border-radius:6px; font-size:13px; cursor:pointer;">Not now</button>';
    document.body.appendChild(banner);

    document.getElementById('install-btn').addEventListener('click', async () => {
      banner.remove();
      if (deferredPrompt) {
        deferredPrompt.prompt();
        await deferredPrompt.userChoice;
        deferredPrompt = null;
      }
    });
    document.getElementById('install-dismiss').addEventListener('click', () => banner.remove());
  });

  // If the automatic prompt never fires (common without a Service Worker),
  // show simple manual instructions instead once the page has settled -
  // better than silently offering nothing.
  setTimeout(() => {
    if (deferredPrompt || document.getElementById('install-banner')) return;
    if (window.matchMedia('(display-mode: standalone)').matches) return; // already installed
    const banner = makeBanner();
    banner.innerHTML =
      '<span style="flex:1;">To add this to your home screen: tap ⋮ in your browser, then "Add to Home screen".</span>' +
      '<button id="install-dismiss2" style="background:transparent; border:1px solid rgba(255,255,255,0.15); ' +
      'color:#eef0f4; padding:7px 12px; border-radius:6px; font-size:13px; cursor:pointer;">Got it</button>';
    document.body.appendChild(banner);
    document.getElementById('install-dismiss2').addEventListener('click', () => banner.remove());
  }, 2500);
})();
