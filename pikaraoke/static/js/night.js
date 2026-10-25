// Kid Nyce: time left tonight on the queue page, and a line before the songs
// that may not fit. The host always sees it; guests only when Settings allows.
(function () {
  // "1h 12m", "45m", "0m".
  const formatLeft = (seconds) => {
    const minutes = Math.max(0, Math.round(seconds / 60));
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    return h ? `${h}h ${m}m` : `${m}m`;
  };

  // The banner line for this plan, or "" when there is nothing to show.
  const bannerText = (plan, isAdmin, t) => {
    if (!plan || (!isAdmin && !plan.show_guests) || !plan.items) return "";
    const late = plan.late_count
      ? t.late.replace("{count}", plan.late_count)
      : plan.ends_at && plan.items.length ? t.allFit : "";
    const est = isAdmin && plan.estimated_songs ? ` ${t.estimated.replace("{count}", plan.estimated_songs)}` : "";
    if (plan.ends_at && plan.time_left === 0) return t.over;
    if (!isAdmin) {
      if (!plan.ends_at) return "";
      return [t.guest.replace("{left}", formatLeft(plan.time_left)), late].filter(Boolean).join(" ");
    }
    if (!plan.ends_at) {
      if (!plan.items.length) return "";
      return t.hostNoEnd.replace("{length}", formatLeft(plan.queue_seconds)).replace("{queueEnd}", plan.queue_ends_at) + est;
    }
    return [
      t.host.replace("{end}", plan.ends_at).replace("{left}", formatLeft(plan.time_left)).replace("{queueEnd}", plan.queue_ends_at),
      late,
    ].filter(Boolean).join(" ") + est;
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { formatLeft, bannerText };
    return;
  }

  const cfg = window.nightConfig;
  if (!cfg) return;

  const render = (plan) => {
    const text = bannerText(plan, cfg.isAdmin, cfg.t);
    const $banner = $("#night-banner");
    $banner.text(text).prop("hidden", !text)
      .toggleClass("is-warning", Boolean(plan && plan.late_count))
      .toggleClass("is-info", !(plan && plan.late_count));
    $("#auto-refresh tr.night-cut").remove();
    $("#auto-refresh tr").removeClass("night-late");
    if (!text || plan.first_late_index === null || plan.first_late_index === undefined) return;
    const $rows = $("#auto-refresh tbody tr").not(".disabled-sort");
    const $first = $rows.eq(plan.first_late_index);
    if (!$first.length) return;
    $rows.slice(plan.first_late_index).addClass("night-late");
    const cols = $first.children("td").length || 1;
    $("<tr class='night-cut'>").append(
      $("<td>").attr("colspan", cols).text(cfg.t.cutLine.replace("{end}", plan.ends_at)),
    ).insertBefore($first);
  };

  let pending = null;
  window.nightRefresh = () => {
    clearTimeout(pending);
    // Several renders in a row ask once.
    pending = setTimeout(() => $.getJSON(cfg.url).done(render), 150);
  };
  // The queue page can be opened again without a reload; one timer is enough.
  if (!window.nightTimer) window.nightTimer = setInterval(() => window.nightRefresh(), 30000);
})();
