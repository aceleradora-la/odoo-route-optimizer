# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services import mymaps_kml


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    delivery_zone_mymaps_mid = fields.Char(
        string="Mapa de Google My Maps",
        config_parameter="delivery_zone.mymaps_mid",
        help="ID del mapa (mid) o la URL completa. El mapa debe estar compartido "
        "como «cualquiera con el enlace».",
    )
    delivery_zone_point_lookup = fields.Selection(
        [
            ("smallest_area", "La zona más chica"),
            ("first_match", "La primera coincidencia"),
        ],
        string="Si el punto cae en varias zonas",
        config_parameter="delivery_zone.point_lookup",
        default="smallest_area",
        help="Criterio de desempate cuando los polígonos se superponen.",
    )

    def action_delivery_zone_test_mymaps(self):
        """Descarga el KML y reporta cuántas zonas/polígonos encontró."""
        self.ensure_one()
        mid = (self.delivery_zone_mymaps_mid or "").strip()
        if not mymaps_kml.extract_mid(mid):
            raise UserError(_("Cargá primero el ID o la URL del mapa."))
        try:
            zones = mymaps_kml.parse_zones(mymaps_kml.fetch_kml(mid))
        except mymaps_kml.MyMapsError as e:
            raise UserError(_("Google My Maps: %s") % str(e)) from e
        if not zones:
            raise UserError(
                _(
                    "El mapa se descargó pero no tiene polígonos. En My Maps dibujá las "
                    "zonas con la herramienta de forma, no con marcadores de punto."
                )
            )
        names = ", ".join(z["name"] for z in zones[:5])
        if len(zones) > 5:
            names += "…"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Google My Maps"),
                "message": _("%(n)s zona(s) con polígonos: %(names)s")
                % {"n": len(zones), "names": names},
                "type": "success",
                "sticky": False,
            },
        }

    def action_delivery_zone_sync_mymaps(self):
        """Guarda la configuración y sincroniza las zonas."""
        self.ensure_one()
        self.set_values()
        return self.env["delivery.zone"].action_sync_from_mymaps()
