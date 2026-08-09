// Applied on every page: fetches the saved theme and sets it on <html>
// so the [data-theme="..."] CSS rules in style.css take effect.
(function () {
  async function applyCurrentTheme() {
    try {
      const res = await fetch('/api/theme');
      const data = await res.json();
      if (data.theme) {
        document.documentElement.setAttribute('data-theme', data.theme);
      }
    } catch (e) {
      // If this fails (server not up yet, etc.) the CSS default (purple)
      // still applies via :root, so the page never looks broken.
    }
  }
  applyCurrentTheme();
})();
