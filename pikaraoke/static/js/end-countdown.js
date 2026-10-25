// Kid Nyce: a big countdown on the singer's screen for the last seconds of a
// song, so the singer knows the end is coming. Settings, Splash screen sets
// how many seconds (0 turns it off) and where it sits; both apply live.
// Not on the room screen.
(function () {
  const script = document.currentScript;
  const POSITIONS = [
    "top-left", "top-center", "top-right", "center",
    "bottom-left", "bottom-center", "bottom-right",
  ];

  // The number to show, or "" for none: whole seconds left, 1 at the least.
  const countdownText = (duration, current, threshold) => {
    if (!threshold || !duration || !(current >= 0)) return "";
    const left = duration - current;
    if (left <= 0 || left > threshold) return "";
    return String(Math.max(1, Math.ceil(left)));
  };

  // A known position, or top center.
  const position = (value) => (POSITIONS.includes(value) ? value : "top-center");

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { countdownText, position, POSITIONS };
    return;
  }

  let threshold = parseInt((script && script.dataset.seconds) || "0", 10) || 0;
  let where = position(script && script.dataset.position);

  const start = () => {
    const video = document.getElementById("video");
    if (!video) return;
    const el = document.createElement("div");
    el.id = "kn-end-countdown";
    el.hidden = true;
    el.setAttribute("aria-hidden", "true");
    document.body.appendChild(el);
    const place = () => {
      POSITIONS.forEach((p) => el.classList.toggle(`pos-${p}`, p === where));
    };
    const update = () => {
      const playing = !video.paused && !video.ended;
      const duration = window.kidnyceSongDuration ||
        (Number.isFinite(video.duration) ? video.duration : 0);
      const text = playing ? countdownText(duration, video.currentTime, threshold) : "";
      if (el.textContent !== text) el.textContent = text;
      el.hidden = !text;
      el.classList.toggle("is-final", Boolean(text) && Number(text) <= 5);
    };
    place();
    ["timeupdate", "pause", "ended", "emptied", "seeking"].forEach((ev) => video.addEventListener(ev, update));
    // A change in Settings reaches this screen at once, no reload.
    if (typeof socket !== "undefined" && socket && socket.on) {
      socket.on("preferences_update", (data) => {
        if (!data) return;
        if (data.key === "end_countdown_seconds") {
          threshold = Math.max(0, parseInt(data.value, 10) || 0);
          update();
        } else if (data.key === "end_countdown_position") {
          where = position(data.value);
          place();
        }
      });
    }
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
