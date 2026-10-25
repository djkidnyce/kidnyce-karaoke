// Kid Nyce: a big countdown on the singer's screen for the last seconds of a
// song, so the singer knows the end is coming. Settings, Splash screen sets
// how many seconds (0 turns it off). Not on the room screen.
(function () {
  const script = document.currentScript;

  // The number to show, or "" for none: whole seconds left, 1 at the least.
  const countdownText = (duration, current, threshold) => {
    if (!threshold || !duration || !(current >= 0)) return "";
    const left = duration - current;
    if (left <= 0 || left > threshold) return "";
    return String(Math.max(1, Math.ceil(left)));
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { countdownText };
    return;
  }

  const threshold = parseInt((script && script.dataset.seconds) || "0", 10) || 0;
  if (!threshold) return;

  const start = () => {
    const video = document.getElementById("video");
    if (!video) return;
    const el = document.createElement("div");
    el.id = "kn-end-countdown";
    el.hidden = true;
    el.setAttribute("aria-hidden", "true");
    document.body.appendChild(el);
    const update = () => {
      const playing = !video.paused && !video.ended;
      const duration = window.kidnyceSongDuration ||
        (Number.isFinite(video.duration) ? video.duration : 0);
      const text = playing ? countdownText(duration, video.currentTime, threshold) : "";
      if (el.textContent !== text) el.textContent = text;
      el.hidden = !text;
      el.classList.toggle("is-final", Boolean(text) && Number(text) <= 5);
    };
    ["timeupdate", "pause", "ended", "emptied", "seeking"].forEach((ev) => video.addEventListener(ev, update));
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
