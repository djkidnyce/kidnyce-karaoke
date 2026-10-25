// Kid Nyce: the host's address in small print in a corner of the screens on
// the Pi, for reaching Settings on the venue network. The state feed answers
// only the Pi's own browsers, so nothing shows anywhere else.
(function () {
  const script = document.currentScript;
  const noNetwork = (script && script.dataset.noNetwork) || "No network";

  const hostOf = (url) => {
    try { return new URL(url).host; } catch (e) { return ""; }
  };

  // The line to show, or "" for none.
  const hostAddressText = (host) => {
    if (!host || !host.show) return "";
    if (!host.ip) return noNetwork;
    const name = hostOf(host.url);
    return name && name !== host.ip ? `${host.ip} · ${name}` : host.ip;
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { hostAddressText };
    return;
  }

  const render = (state) => {
    let el = document.getElementById("kn-host-address");
    if (!el) {
      el = document.createElement("div");
      el.id = "kn-host-address";
      document.body.appendChild(el);
    }
    const text = hostAddressText(state && state.host);
    el.textContent = text;
    el.hidden = !text;
  };
  const basePath = (window.pikaraokeConfig && window.pikaraokeConfig.basePath) || "";
  const refresh = () =>
    fetch(`${basePath}/api/display/state`, { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : null))
      .then((state) => state && render(state))
      .catch(() => {});
  refresh();
  setInterval(refresh, 20000);
})();
