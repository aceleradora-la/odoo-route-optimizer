# -*- coding: utf-8 -*-
import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services import mymaps_kml


class DeliveryZone(models.Model):
    _name = "delivery.zone"
    _description = "Zona de entrega"
    _order = "sequence, name"

    name = fields.Char(string="Zona", required=True, translate=True)
    code = fields.Char(string="Código", help="Referencia corta para listados y reportes.")
    sequence = fields.Integer(default=10)
    color = fields.Integer(string="Color")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        string="Compañía",
        default=lambda self: self.env.company,
    )
    source = fields.Selection(
        [("manual", "Manual"), ("mymaps", "Google My Maps")],
        string="Origen",
        default="manual",
        required=True,
        help="Las zonas sincronizadas desde My Maps tienen polígonos y permiten "
        "autodetectar la zona de un contacto por su geolocalización.",
    )
    kml_polygons = fields.Text(
        string="Polígonos (JSON)",
        help="Anillos [[lon, lat], ...] importados desde My Maps. Solo lectura.",
    )
    partner_count = fields.Integer(
        string="Contactos", compute="_compute_partner_count"
    )
    note = fields.Text(string="Notas")

    _sql_constraints = [
        (
            "name_company_uniq",
            "unique(name, company_id)",
            "Ya existe una zona de entrega con ese nombre en esta compañía.",
        )
    ]

    def _compute_partner_count(self):
        grouped = self.env["res.partner"]._read_group(
            [("geo_delivery_zone_id", "in", self.ids)],
            groupby=["geo_delivery_zone_id"],
            aggregates=["__count"],
        )
        counts = {zone.id: count for zone, count in grouped}
        for zone in self:
            zone.partner_count = counts.get(zone.id, 0)

    def action_view_partners(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Contactos de %s") % self.name,
            "res_model": "res.partner",
            "view_mode": "tree,form",
            "domain": [("geo_delivery_zone_id", "=", self.id)],
            "context": {"default_geo_delivery_zone_id": self.id},
        }

    # ------------------------------------------------------------------
    # Sincronización con Google My Maps
    # ------------------------------------------------------------------

    @api.model
    def _get_mymaps_zones(self):
        """Descarga y parsea el KML configurado. Devuelve la lista de zonas."""
        icp = self.env["ir.config_parameter"].sudo()
        mid = icp.get_param("delivery_zone.mymaps_mid") or ""
        if not mymaps_kml.extract_mid(mid):
            raise UserError(
                _(
                    "Configurá primero el mapa de Google My Maps en "
                    "Ajustes → Inventario → Zonas de entrega."
                )
            )
        try:
            kml = mymaps_kml.fetch_kml(mid)
            return mymaps_kml.parse_zones(kml)
        except mymaps_kml.MyMapsError as e:
            raise UserError(_("Google My Maps: %s") % str(e)) from e

    @api.model
    def action_sync_from_mymaps(self):
        """Upsert de zonas desde My Maps. No toca ni borra las zonas manuales."""
        zones = self._get_mymaps_zones()
        if not zones:
            raise UserError(
                _(
                    "El mapa no tiene polígonos. En My Maps, dibujá las zonas con la "
                    "herramienta de línea/forma (no como marcadores de punto)."
                )
            )
        created = updated = 0
        for zone_data in zones:
            polygons = json.dumps(zone_data["rings"])
            existing = self.with_context(active_test=False).search(
                [("name", "=", zone_data["name"]), ("source", "=", "mymaps")], limit=1
            )
            if existing:
                existing.write({"kml_polygons": polygons, "active": True})
                updated += 1
            else:
                self.create(
                    {
                        "name": zone_data["name"],
                        "source": "mymaps",
                        "kml_polygons": polygons,
                    }
                )
                created += 1
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Zonas sincronizadas"),
                "message": _("%(c)s creadas, %(u)s actualizadas.")
                % {"c": created, "u": updated},
                "type": "success",
                "sticky": False,
            },
        }

    @api.model
    def _zones_for_lookup(self):
        """Zonas con polígonos, en el formato que espera point_in_zone()."""
        result = []
        for zone in self.search([("kml_polygons", "!=", False)]):
            try:
                rings = json.loads(zone.kml_polygons)
            except (TypeError, ValueError):
                continue
            if rings:
                result.append({"name": zone.name, "rings": rings, "zone_id": zone.id})
        return result

    @api.model
    def find_zone_for_point(self, longitude, latitude):
        """Devuelve el recordset de la zona que contiene el punto, o vacío."""
        zones = self._zones_for_lookup()
        if not zones:
            return self.browse()
        icp = self.env["ir.config_parameter"].sudo()
        tie_breaker = icp.get_param("delivery_zone.point_lookup") or "smallest_area"
        name = mymaps_kml.point_in_zone(longitude, latitude, zones, tie_breaker)
        if not name:
            return self.browse()
        for zone in zones:
            if zone["name"] == name:
                return self.browse(zone["zone_id"])
        return self.browse()
