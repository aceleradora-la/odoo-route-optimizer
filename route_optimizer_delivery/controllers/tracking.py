# -*- coding: utf-8 -*-
from datetime import timedelta

import pytz

from odoo import http
from odoo.http import request

from odoo.addons.route_optimizer.services import delivery_windows


class RouteDeliveryTracking(http.Controller):
    """Seguimiento público de una entrega. Se entra con un token imposible de
    adivinar y solo se muestra lo que el cliente ya sabe de su pedido."""

    def _tracking_values(self, token):
        picking = (
            request.env["stock.picking"]
            .sudo()
            .search([("route_delivery_token", "=", token)], limit=1)
        )
        if not token or len(token) < 16 or not picking:
            return None
        batch = picking.batch_id
        icp = request.env["ir.config_parameter"].sudo()
        try:
            margin = int(icp.get_param("route_optimizer_delivery.eta_window_minutes") or 30)
        except ValueError:
            margin = 30
        tz = pytz.timezone(delivery_windows._routing_timezone(request.env, batch) if batch else "UTC")

        def local(dt):
            return pytz.utc.localize(dt).astimezone(tz) if dt else None

        state = picking.route_delivery_state or "pending"
        started = bool(batch.route_delivery_started_at)
        ahead = 0
        if batch and state == "pending":
            pending = batch._route_delivery_stops().filtered(
                lambda p: p.route_delivery_state == "pending"
            )
            ahead = list(pending).index(picking) if picking in pending else 0
        if picking.state == "cancel":
            step = "cancel"
        elif state in ("delivered", "partial"):
            step = "delivered"
        elif state == "failed":
            step = "failed"
        elif started and ahead == 0:
            step = "next"
        elif started:
            step = "on_the_way"
        else:
            step = "scheduled"
        eta = local(picking.route_delivery_eta) if state == "pending" else None
        company = picking.company_id
        return {
            "picking": picking,
            "company": company,
            "step": step,
            "ahead": ahead,
            "reference": picking.origin or picking.name,
            "eta_from": (eta - timedelta(minutes=margin)).strftime("%H:%M") if eta else "",
            "eta_to": (eta + timedelta(minutes=margin)).strftime("%H:%M") if eta else "",
            "eta_day": eta.strftime("%d/%m") if eta else "",
            "done_at": local(picking.route_delivery_done_at).strftime("%d/%m %H:%M")
            if picking.route_delivery_done_at
            else "",
            "driver": (batch.user_id.name or "").split(" ")[0] if batch and started else "",
            "address": picking.route_optimizer_delivery_address or "",
            "phone": company.phone or "",
            "live": step in ("scheduled", "on_the_way", "next"),
        }

    @http.route("/reparto/t/<string:token>", type="http", auth="public", methods=["GET"], sitemap=False)
    def tracking(self, token, **kwargs):
        values = self._tracking_values(token)
        if values is None:
            return request.render("route_optimizer_delivery.tracking_not_found", status=404)
        response = request.render("route_optimizer_delivery.tracking_page", values)
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Cache-Control"] = "no-store"
        return response
