# -*- coding: utf-8 -*-
from odoo import api, fields, models


def _extend_domain(domain, leaves):
    """Suma condiciones a un dominio devuelto por Odoo.

    Odoo 17/18 devuelven una lista; Odoo 19 devuelve un objeto Domain.
    Se reconstruye con el mismo tipo para no romper en ninguna versión.
    """
    if not leaves:
        return domain
    if isinstance(domain, list):
        return domain + leaves
    return domain & type(domain)(leaves)


class StockPicking(models.Model):
    _inherit = "stock.picking"

    geo_delivery_zone_id = fields.Many2one(
        "delivery.zone",
        string="Zona de entrega",
        compute="_compute_geo_delivery_zone_id",
        store=True,
        readonly=False,
        index=True,
        help="Se toma del contacto de entrega (o de su contacto principal). "
        "Se puede sobrescribir manualmente en este traslado.",
    )

    @api.depends(
        "partner_id",
        "partner_id.geo_delivery_zone_id",
        "partner_id.parent_id.geo_delivery_zone_id",
    )
    def _compute_geo_delivery_zone_id(self):
        for picking in self:
            partner = picking._delivery_zone_partner()
            picking.geo_delivery_zone_id = (
                partner.geo_delivery_zone_effective_id if partner else False
            )

    def write(self, vals):
        """Recalcula la zona cuando cambia el método de entrega.

        geo_delivery_zone_id se almacena y sus depends solo miran partner_id.
        Con route_optimizer instalado la zona puede salir del transportista, y
        al cambiar carrier_id nada dispararía el recálculo: el valor guardado
        quedaría desactualizado en silencio, que es peor que un error visible.

        carrier_id lo aporta stock_delivery, opcional, por eso se comprueba
        contra vals en vez de declararlo en @api.depends.
        """
        res = super().write(vals)
        if "carrier_id" in vals and "geo_delivery_zone_id" not in vals:
            self._compute_geo_delivery_zone_id()
        return res

    def _delivery_zone_partner(self):
        """Contacto del que sale la zona.

        Si route_optimizer está instalado se reutiliza su punto de extensión, que ya
        contempla tipos de operación personalizados.
        """
        self.ensure_one()
        if hasattr(self, "_route_optimizer_delivery_partner"):
            return self._route_optimizer_delivery_partner()
        return self.partner_id

    # ------------------------------------------------------------------
    # Agrupación automática de lotes por zona
    #
    # stock_picking_batch arma dos dominios para juntar traslados: uno busca
    # lotes compatibles ya existentes y otro traslados sueltos compatibles.
    # Se agrega la zona a ambos cuando el tipo de operación lo pide.
    # ------------------------------------------------------------------

    def _dz_group_by_zone(self):
        self.ensure_one()
        picking_type = self.picking_type_id
        return "batch_group_by_geo_delivery_zone" in picking_type._fields and (
            picking_type.batch_group_by_geo_delivery_zone
        )

    def _get_possible_pickings_domain(self):
        domain = super()._get_possible_pickings_domain()
        if self._dz_group_by_zone():
            domain = _extend_domain(
                domain,
                [("geo_delivery_zone_id", "=", self.geo_delivery_zone_id.id or False)],
            )
        return domain

    def _get_possible_batches_domain(self):
        domain = super()._get_possible_batches_domain()
        if self._dz_group_by_zone():
            domain = _extend_domain(
                domain,
                [
                    (
                        "picking_ids.geo_delivery_zone_id",
                        "=",
                        self.geo_delivery_zone_id.id or False,
                    )
                ],
            )
        return domain

    def _get_auto_batch_description(self):
        """Suma la zona al nombre del lote autogenerado.

        El método no existe en Odoo 17, por eso el super() se resuelve de forma
        defensiva en lugar de llamarse directamente.
        """
        parent = getattr(super(), "_get_auto_batch_description", None)
        description = parent() if parent else ""
        if self._dz_group_by_zone() and self.geo_delivery_zone_id:
            zone_name = self.geo_delivery_zone_id.name
            return f"{description}, {zone_name}" if description else zone_name
        return description
