// Break music between singers. Shared by the singer screen (which plays the
// audio) and the crowd screen (which only shows the countdown).
//
// The server plans the break and sends it inside the now_playing payload as
// `intermission`: the track urls with their lengths, how far in the break is,
// and how long is left. A screen that loads mid break picks up where it is.
(function () {
  const FADE_MS = 2500;

  const formatCountdown = (seconds) => {
    const total = Math.max(0, Math.ceil(seconds));
    const m = Math.floor(total / 60);
    const s = String(total % 60).padStart(2, "0");
    return `${m}:${s}`;
  };

  const fadeTo = (audio, target, ms, done) => {
    const start = audio.volume;
    const began = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - began) / ms);
      audio.volume = Math.max(0, Math.min(1, start + (target - start) * t));
      if (t < 1) {
        audio._fadeFrame = requestAnimationFrame(step);
      } else if (done) {
        done();
      }
    };
    cancelAnimationFrame(audio._fadeFrame);
    audio._fadeFrame = requestAnimationFrame(step);
  };

  // Plays one planned break on an <audio> element. Only the master screen calls this.
  const createPlayer = (audio) => {
    let breakKey = null;
    let tracks = [];
    let index = 0;
    let volume = 0.8;
    let stopTimer = null;

    const playIndex = (i, offsetSeconds) => {
      index = i;
      if (index >= tracks.length) return;
      audio.src = tracks[index].url;
      audio.volume = 0;
      const begin = () => {
        if (offsetSeconds > 0) audio.currentTime = offsetSeconds;
        audio.play().catch((e) => console.log("Intermission autoplay blocked", e));
        fadeTo(audio, volume, offsetSeconds > 0 ? 800 : 400);
      };
      audio.addEventListener("loadedmetadata", begin, { once: true });
      audio.load();
    };

    audio.addEventListener("ended", () => playIndex(index + 1, 0));

    const stop = (fadeMs = FADE_MS) => {
      clearTimeout(stopTimer);
      stopTimer = null;
      breakKey = null;
      if (audio.paused) return;
      fadeTo(audio, 0, fadeMs, () => {
        audio.pause();
        audio.removeAttribute("src");
        audio.load();
      });
    };

    const sync = (payload) => {
      if (!payload || payload.pending || !payload.tracks || !payload.tracks.length) {
        if (breakKey) stop();
        return;
      }
      const key = payload.tracks.map((t) => t.url).join("|") + "@" + payload.planned_seconds;
      if (key === breakKey) return;
      breakKey = key;
      tracks = payload.tracks;
      volume = payload.volume ?? 0.8;

      // Find the track and offset this far into the break.
      let elapsed = payload.elapsed_seconds || 0;
      let i = 0;
      while (i < tracks.length && elapsed >= tracks[i].duration) {
        elapsed -= tracks[i].duration;
        i++;
      }
      playIndex(i, elapsed);

      // The planned end is a hard stop: a faded last track ends here, and the
      // fade starts early enough to finish on time.
      clearTimeout(stopTimer);
      const remainingMs = (payload.remaining_seconds || 0) * 1000;
      stopTimer = setTimeout(() => stop(FADE_MS), Math.max(0, remainingMs - FADE_MS));
    };

    return { sync, stop };
  };

  // Keeps a countdown element ticking between payloads.
  const createCountdown = (onTick) => {
    let endsAt = null;
    let timer = null;
    const tick = () => {
      if (endsAt === null) return;
      onTick(formatCountdown((endsAt - Date.now()) / 1000));
    };
    return {
      sync(payload) {
        clearInterval(timer);
        if (!payload || payload.pending || payload.remaining_seconds == null) {
          endsAt = null;
          return;
        }
        endsAt = Date.now() + payload.remaining_seconds * 1000;
        tick();
        timer = setInterval(tick, 500);
      },
    };
  };

  window.KidnyceIntermission = { createPlayer, createCountdown, formatCountdown };
})();
