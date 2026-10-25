// The crowd screen: a muted copy of the lyrics video that follows the singer
// screen, the QR code with tonight's join code, and the singing order.
//
// Everything a guest typed (names, titles) is rendered with .text(), never as HTML.
(function () {
  const withBasePath = (path) => `${window.pikaraokeConfig.basePath}${path}`;
  const t = CrowdConfig.translations;
  const socket = io({ path: window.pikaraokeConfig.socketioPath });

  let nowPlaying = {};
  let currentUrl = null;
  let hls = null;
  let octopus = null;
  let lastQrVersion = null;

  const isChrome = /chrome/i.test(navigator.userAgent) && !/edg/i.test(navigator.userAgent);
  const isEdge = /edg/i.test(navigator.userAgent);
  const video = () => document.getElementById("crowd-video");
  const isPlaying = (v) => v.currentTime > 0 && !v.paused && !v.ended && v.readyState > 2;
  const countdown = KidnyceIntermission.createCountdown((text) => $("#break-countdown").text(text));

  const formatCode = (code) => (code && code.length === 6 ? `${code.slice(0, 3)} ${code.slice(3)}` : code || "");
  const hostOf = (url) => {
    try { return new URL(url).host; } catch (e) { return url || ""; }
  };

  const renderState = (state) => {
    const join = state.join;
    $("#join-code").text(join.required ? formatCode(join.code) : "").toggle(join.required);
    $("#join-url").text(hostOf(join.url));
    if (join.qr_version !== lastQrVersion) {
      lastQrVersion = join.qr_version;
      $("#crowd-qr").attr("src", withBasePath(`/qrcode?v=${join.qr_version}`));
    }

    const $list = $("#queue-list").empty();
    state.queue.forEach((item) => {
      $list.append(
        $("<li>").append(
          $("<div class='q-singer'>").text(item.user),
          $("<div class='q-song'>").text(item.title),
        ),
      );
    });
    if (!state.queue.length) {
      $list.append($("<li class='queue-empty'>").text(t.emptyQueue));
    }
    const night = state.night;
    if (night) {
      const minutes = Math.max(0, Math.round(night.time_left / 60));
      const left = minutes >= 60 ? `${Math.floor(minutes / 60)}h ${minutes % 60}m` : `${minutes}m`;
      const late = night.late_count ? ` · ${t.nightLate.replace("{count}", night.late_count)}` : "";
      $("#night-left").text(t.nightLeft.replace("{left}", left) + late);
    } else {
      $("#night-left").text("");
    }
    const extra = state.queue_length - state.queue.length;
    $("#queue-more").text(extra > 0 ? t.more.replace("{count}", extra) : "");

    const top = state.top_requested_tonight || state.top_requested_all_time;
    if (top) {
      $("#idle-top-label").text(state.top_requested_tonight ? t.topTonight : t.topAllTime);
      $("#idle-top-title").text(top.title);
      $("#idle-top").show();
    } else {
      $("#idle-top").hide();
    }
  };

  const refreshState = () => $.getJSON(withBasePath("/api/display/state")).done(renderState);

  const stopVideo = () => {
    currentUrl = null;
    if (hls) { hls.destroy(); hls = null; }
    if (octopus) { octopus.dispose(); octopus = null; }
    const v = video();
    v.pause();
    v.removeAttribute("src");
    v.load();
    $("#stage-video-wrap").hide();
  };

  const loadVideo = (np) => {
    const v = video();
    v.muted = true;
    v.volume = 0;
    currentUrl = np.now_playing_url;
    if (hls) { hls.destroy(); hls = null; }
    const resumeAt = np.now_playing_position || 0;
    if (currentUrl.endsWith(".m3u8") && !(v.canPlayType("application/vnd.apple.mpegurl") && !isChrome && !isEdge)) {
      hls = new Hls({ startPosition: resumeAt });
      hls.loadSource(currentUrl);
      hls.attachMedia(v);
    } else {
      v.src = currentUrl;
      if (resumeAt) v.addEventListener("loadedmetadata", () => { v.currentTime = resumeAt; }, { once: true });
    }
    if (octopus) { octopus.dispose(); octopus = null; }
    if (np.now_playing_subtitle_url) {
      try {
        octopus = new SubtitlesOctopus({
          video: v,
          subUrl: np.now_playing_subtitle_url,
          fonts: [withBasePath("/static/fonts/Arial.ttf"), withBasePath("/static/fonts/DroidSansFallback.ttf")],
          workerUrl: withBasePath("/static/js/subtitles-octopus-worker.js"),
        });
      } catch (e) { console.error(e); }
    }
    $("#stage-video-wrap").show();
    v.play().catch(() => setTimeout(() => v.play().catch((e) => console.log("Crowd video did not start", e)), 1000));
  };

  const handleNowPlaying = (np) => {
    nowPlaying = np;
    if (np.now_playing) {
      $("#now-singer").text(np.now_playing_user || "");
      $("#now-song").text(np.now_playing);
      $("#now-card").show();
    } else {
      $("#now-card").hide();
    }

    const brk = np.intermission;
    const onBreak = Boolean(brk && !brk.pending && brk.tracks && brk.tracks.length);
    countdown.sync(onBreak ? brk : null);
    $("#break-next-singer").text(np.next_user || "");
    $("#break-next-song").text(np.up_next || "");
    $("#break-panel").toggle(onBreak);
    $("#idle-panel").toggle(!onBreak && !np.now_playing_url);

    if (!np.now_playing_url && currentUrl) stopVideo();
    if (np.now_playing_url && np.now_playing_url !== currentUrl) loadVideo(np);
    refreshState();
  };

  // Follow the master screen. Same rule as a second splash screen: jump when far
  // off, otherwise nudge the playback rate so the two stay locked.
  socket.on("playback_position", (position) => {
    const v = video();
    if (!isPlaying(v)) return;
    const drift = position - v.currentTime;
    if (Math.abs(drift) > 2) {
      v.currentTime = position;
    } else {
      v.playbackRate = Math.abs(drift) < 0.05 ? 1 : 1 + Math.max(-0.1, Math.min(0.1, drift / 2));
    }
  });
  socket.on("connect", () => socket.emit("register_splash", "crowd"));
  socket.on("now_playing", handleNowPlaying);
  socket.on("queue_update", refreshState);
  socket.on("join_code_changed", refreshState);
  socket.on("pause", () => video().pause());
  socket.on("play", () => { if (currentUrl) video().play(); });
  socket.on("skip", () => stopVideo());
  socket.on("restart", () => { video().currentTime = 0; });

  const tickClock = () => {
    $("#crowd-clock").text(new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }));
  };

  $(function () {
    document.body.style.cursor = "none";
    tickClock();
    setInterval(tickClock, 10000);
    // A fallback in case a socket event is missed while the network blips.
    setInterval(refreshState, 30000);
    $.getJSON(withBasePath("/api/now_playing")).done(handleNowPlaying);
  });
})();
