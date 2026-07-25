# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ResPartner(models.Model):
    _inherit = "res.partner"

    geo_delivery_zone_id = fields.Many2one(
        "delivery.zone",
        string="Zona de entrega",
        index=True,
        help="Zona asignada a esta dirección. Si se deja vacía en una dirección de "
        "entrega, se usa la del contacto principal.",
    )
    geo_delivery_zone_effective_id = fields.Many2one(
        "delivery.zone",
        string="Zona efectiva",
        compute="_compute_geo_delivery_zone_effective_id",
        help="Zona propia; si está vacía, la del contacto principal.",
    )

    @api.depends("geo_delivery_zone_id", "parent_id.geo_delivery_zone_id")
    def _compute_geo_delivery_zone_effective_id(self):
        for partner in self:
            zone = partner.geo_delivery_zone_id
            if not zone and partner.parent_id:
                zone = partner.parent_id.geo_delivery_zone_id
            partner.geo_delivery_zone_effective_id = zone

    def action_delivery_zone_detect(self):
        """Detecta la zona por geolocalización contra los polígonos de My Maps."""
        zone_model = self.env["delivery.zone"]
        zones = zone_model._zones_for_lookup()
        if not zones:
            raise UserError(
                _(
                    "No hay zonas con polígonos. Sincronizá primero desde Google My Maps "
                    "en Ajustes → Inventario → Zonas de entrega."
                )
            )

        detected = 0
        no_coords = []
        no_match = []
        for partner in self:
            lat = partner.partner_latitude
            lng = partner.partner_longitude
            try:
                latf, lngf = float(lat or 0), float(lng or 0)
            except (TypeError, ValueError):
                latf = lngf = 0.0
            if latf == 0.0 and lngf == 0.0:
                no_coords.append(partner.display_name)
                continue
            zone = zone_model.find_zone_for_point(lngf, latf)
            if zone:
                partner.geo_delivery_zone_id = zone.id
                detected += 1
            else:
                no_match.append(partner.display_name)

        if len(self) == 1 and no_coords:
            raise UserError(
                _(
                    "%s no tiene coordenadas. Usá el botón «Geolocalizar» del contacto "
                    "antes de detectar la zona."
                )
                % self.display_name
            )

        msg_parts = [_("%s contacto(s) con zona asignada.") % detected]
        if no_coords:
            msg_parts.append(_("%s sin geolocalizar.") % len(no_coords))
        if no_match:
            msg_parts.append(_("%s fuera de toda zona.") % len(no_match))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Detección de zonas"),
                "message": " ".join(msg_parts),
                "type": "success" if detected else "warning",
                "sticky": False,
            },
        }
