# -*- coding: utf-8 -*-
"""
Build OR-Tools time constraints from OCA stock_partner_delivery_window.

Uses res.partner.delivery_time_preference and partner.delivery.time.window
via partner._get_delivery_windows(weekday). Planned delivery datetime per stop
comes from the picking (OCA _planned_delivery_date) with fallback to the batch.
"""
import datetime

import pytz

from odoo import _, fields
from odoo.exceptions import UserError

DAY_SECONDS = 24 * 3600


def _param_bool(value, default=False):
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("1", "true", "t", "yes", "y", "on"):
        return True
    if s in ("0", "false", "f", "no", "n", "off", ""):
        return False
    return bool(default)


def _routing_timezone(env, batch):
    """Timezone for converting naive UTC datetimes and for partner windows."""
    wh = batch.warehouse_id
    if wh and wh.partner_id and wh.partner_id.tz:
        return wh.partner_id.tz
    if batch.company_id.partner_id.tz:
        return batch.company_id.partner_id.tz
    return env.user.tz or "UTC"


def localize_delivery_datetime(env, batch, dt):
    """Return aware datetime in routing TZ, or None."""
    if not dt:
        return None
    try:
        route_dt = fields.Datetime.to_datetime(dt)
    except Exception:
        return None
    tz = pytz.timezone(_routing_timezone(env, batch))
    if route_dt.tzinfo is None:
        route_dt = pytz.utc.localize(route_dt)
    return route_dt.astimezone(tz)


def planned_delivery_datetime(picking, batch, env):
    """Datetime used to pick weekday / time window (aligned with OCA picking logic)."""
    if hasattr(picking, "_planned_delivery_date"):
        raw = picking._planned_delivery_date()
    else:
        raw = picking.scheduled_date or batch.scheduled_date
    return localize_delivery_datetime(env, batch, raw)


def _time_to_seconds(t):
    if isinstance(t, datetime.time):
        return int(t.hour * 3600 + t.minute * 60 + t.second)
    return None


def _window_record_interval(window_rec):
    """Return [start_sec, end_sec] for a partner.delivery.time.window record."""
    st = en = None
    if hasattr(window_rec, "get_time_window_start_time"):
        st = _time_to_seconds(window_rec.get_time_window_start_time())
    if hasattr(window_rec, "get_time_window_end_time"):
        en = _time_to_seconds(window_rec.get_time_window_end_time())
    if st is None and "time_window_start" in window_rec._fields:
        try:
            st = int(round(float(window_rec.time_window_start) * 3600))
        except (TypeError, ValueError):
            st = None
    if en is None and "time_window_end" in window_rec._fields:
        try:
            en = int(round(float(window_rec.time_window_end) * 3600))
        except (TypeError, ValueError):
            en = None
    if st is None or en is None or en < st:
        return None
    return [st, en]


def _partner_delivery_windows(partners, weekday):
    """Map partner_id -> recordset of windows for weekday (OCA 19 API)."""
    if hasattr(partners, "_get_delivery_windows"):
        return partners._get_delivery_windows(weekday) or {}
    if hasattr(partners, "get_delivery_windows"):
        return partners.get_delivery_windows(weekday) or {}
    return {}


def _intervals_for_partner(partner, weekday, local_dt, windows_by_partner):
    """
    Return list of [start,end] second intervals for this stop, or None if infeasible
  (e.g. workdays preference on weekend).
    """
    try:
        pref = partner.delivery_time_preference
    except Exception:
        pref = "anytime"

    if pref == "anytime":
        return [[0, DAY_SECONDS]]

    if pref == "workdays":
        if weekday > 4:
            return None
        return [[0, DAY_SECONDS]]

    # time_windows
    wset = windows_by_partner.get(partner.id)
    if not wset:
        return [[0, DAY_SECONDS]]

    intervals = []
    for w in wset:
        iv = _window_record_interval(w)
        if iv:
            intervals.append(iv)
    if not intervals:
        return [[0, DAY_SECONDS]]

    if len(intervals) == 1:
        return intervals

    if local_dt:
        t_sec = _time_to_seconds(local_dt.time())
        if t_sec is not None:
            containing = [iv for iv in intervals if iv[0] <= t_sec <= iv[1]]
            if containing:
                return containing
            after = sorted([iv for iv in intervals if iv[0] > t_sec], key=lambda x: x[0])
            if after:
                return [after[0]]
            before = sorted([iv for iv in intervals if iv[1] < t_sec], key=lambda x: x[1])
            if before:
                return [before[-1]]

    return intervals


def validate_stops_delivery_windows(env, batch, stops):
    """
    Return list of warning strings for stops outside partner windows.
    Does not block optimization unless caller chooses to.
    """
    if "delivery_time_preference" not in env["res.partner"]._fields:
        return []

    warnings = []
    for s in stops:
        picking = s["picking"]
        partner = s["partner"]
        local_dt = planned_delivery_datetime(picking, batch, env)
        if not local_dt:
            continue
        if hasattr(partner, "_is_in_delivery_window"):
            if not partner._is_in_delivery_window(local_dt):
                ref = picking.name or str(picking.id)
                warnings.append(
                    _(
                        "%(picking)s — %(partner)s: planned delivery %(when)s is outside "
                        "the partner delivery window."
                    )
                    % {
                        "picking": ref,
                        "partner": partner.display_name,
                        "when": fields.Datetime.to_string(local_dt.astimezone(pytz.utc)),
                    }
                )
    return warnings


def build_vrp_time_payload(env, batch, stops, icp):
    """
    Build time-related keys for the extended OR-Tools /vrp payload.

    Returns dict (maybe empty) with:
      route_start_seconds, service_times, time_windows, time_windows_list
    """
    if not _param_bool(icp.get_param("route_optimizer.use_delivery_windows", "True"), True):
        return {}

    if "delivery_time_preference" not in env["res.partner"]._fields:
        return {}

    if not stops:
        return {}

    try:
        route_start_hour = float(icp.get_param("route_optimizer.route_start_hour", "8") or 8)
    except (TypeError, ValueError):
        route_start_hour = 8.0
    route_start_seconds = int(max(0, min(route_start_hour, 24)) * 3600)

    try:
        service_time = int(icp.get_param("route_optimizer.service_time_seconds", "600") or 600)
    except (TypeError, ValueError):
        service_time = 600
    service_time = max(0, service_time)

    partners = env["res.partner"].browse([s["partner"].id for s in stops]).exists()

    infeasible = []
    time_windows_list = []
    time_windows = []
    windows_cache = {}

    # Depot: depart at route start (vehicle may return until end of day).
    depot_tw = [route_start_seconds, DAY_SECONDS]
    time_windows.append(depot_tw)
    time_windows_list.append([depot_tw])
    service_times = [0]

    for s in stops:
        picking = s["picking"]
        partner = s["partner"]
        local_dt = planned_delivery_datetime(picking, batch, env)
        weekday = local_dt.weekday() if local_dt else 0
        if not local_dt and batch.scheduled_date:
            fallback = localize_delivery_datetime(env, batch, batch.scheduled_date)
            if fallback:
                weekday = fallback.weekday()

        if weekday not in windows_cache:
            windows_cache[weekday] = _partner_delivery_windows(partners, weekday)
        windows_by_partner = windows_cache[weekday]
        intervals = _intervals_for_partner(partner, weekday, local_dt, windows_by_partner)
        if intervals is None:
            ref = picking.name or str(picking.id)
            infeasible.append(f"{ref} ({partner.display_name})")
            intervals = [[DAY_SECONDS, DAY_SECONDS]]
        elif not intervals:
            intervals = [[0, DAY_SECONDS]]

        time_windows_list.append(intervals)
        if len(intervals) == 1:
            time_windows.append(intervals[0])
        else:
            time_windows.append([min(iv[0] for iv in intervals), max(iv[1] for iv in intervals)])
        service_times.append(service_time)

    if infeasible:
        raise UserError(
            _(
                "Cannot optimize: the following deliveries are scheduled on a non-working "
                "day for partners set to «Weekdays» only:\n%(stops)s\n"
                "Adjust the scheduled date on the transfer or the partner preference."
            )
            % {"stops": "\n".join(infeasible)}
        )

    return {
        "route_start_seconds": route_start_seconds,
        "service_times": service_times,
        "time_windows": time_windows,
        "time_windows_list": time_windows_list,
    }
