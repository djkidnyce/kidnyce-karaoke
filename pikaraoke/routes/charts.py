"""Charts: the crowd's most requested songs, and every Hot 100 number one."""

import logging

import flask_babel
import requests
from flask import jsonify, render_template, request
from flask_smorest import Blueprint

from pikaraoke.lib.auth import public
from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name, is_admin

_ = flask_babel.gettext

charts_bp = Blueprint("charts", __name__)

_MAX_LIMIT = 200


def _limit(default: int) -> int:
    return max(1, min(request.args.get("limit", default, type=int), _MAX_LIMIT))


@charts_bp.route("/charts")
@public
def charts():
    """Most requested songs and the number one hits."""
    k = get_karaoke_instance()
    return render_template(
        "charts.html",
        site_title=get_site_name(),
        # MSG: Title of the charts page.
        title=_("Charts"),
        admin=is_admin(),
        decades=k.number_ones.decades(),
        number_one_count=k.number_ones.count(),
        last_chart=k.number_ones.last_chart,
        has_session=k.current_session_uuid() is not None,
    )


@charts_bp.route("/api/charts/most_requested")
@public
def most_requested():
    """Songs ranked by how often guests queued them. scope=session or all."""
    k = get_karaoke_instance()
    scope = request.args.get("scope", "all")
    session_uuid = k.current_session_uuid() if scope == "session" else None
    if scope == "session" and session_uuid is None:
        return jsonify({"scope": scope, "total": 0, "songs": []})
    return jsonify(
        {
            "scope": scope,
            "total": k.request_stats.total_requests(session_uuid),
            "songs": k.request_stats.top_requested(_limit(25), session_uuid),
        }
    )


@charts_bp.route("/api/charts/number_ones")
@public
def number_ones():
    """Hot 100 number ones, newest first. Filter with decade=1990 or q=text."""
    k = get_karaoke_instance()
    return jsonify(
        {
            "last_chart": k.number_ones.last_chart,
            "songs": k.number_ones.search(
                decade=request.args.get("decade", type=int),
                query=request.args.get("q") or None,
                limit=_limit(100),
                offset=max(0, request.args.get("offset", 0, type=int)),
            ),
        }
    )


@charts_bp.route("/api/charts/number_ones/refresh", methods=["POST"])
def refresh_number_ones():
    """Pull the newest Hot 100 charts. Host only."""
    k = get_karaoke_instance()
    try:
        added = k.number_ones.refresh()
    except (requests.RequestException, ValueError) as e:
        logging.error(f"Number ones refresh failed: {e}")
        return jsonify({"error": "Could not reach the chart data source"}), 502
    return jsonify({"added": added, "last_chart": k.number_ones.last_chart})
