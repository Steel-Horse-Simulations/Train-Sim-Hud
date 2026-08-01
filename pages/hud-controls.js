// Shared auto-hide controls behavior for full-screen HUD pages (Dashboard,
// Timetable, Map). Controls stay hidden by default and appear briefly on
// tap/hover/pointer movement, then fade back out after a short period of
// inactivity, so the HUD itself stays clean and uninterrupted most of the time.
(function () {
  function initHudControls(overlayId, hideDelayMs) {
    const overlay = document.getElementById(overlayId);
    if (!overlay) return;
    let hideTimer = null;

    function show() {
      overlay.classList.add('visible');
      if (hideTimer) clearTimeout(hideTimer);
      hideTimer = setTimeout(() => overlay.classList.remove('visible'), hideDelayMs || 2500);
    }

    document.addEventListener('pointermove', show);
    document.addEventListener('pointerdown', show);
    document.addEventListener('touchstart', show, { passive: true });

    show(); // brief flash on load so people know the controls exist
  }

  window.initHudControls = initHudControls;
})();
