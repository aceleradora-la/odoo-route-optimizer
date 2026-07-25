# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = "stock.picking"

    delivery_zone_id = fields.Many2one(
        "delivery.zone",
        string="Zona de entrega",
        compute="_compute_delivery_zone_id",
        store=True,
        readonly=False,
        index=True,
        help="Se toma del contacto de entrega (o de su contacto principal). "
        "Se puede sobrescribir manualmente en este traslado.",
    )

    @api.depends(
        "partner_id",
        "partner_id.delivery_zone_id",
        "partner_id.parent_id.delivery_zone_id",
    )
    def _compute_delivery_zone_id(self):
        for picking in self:
            partner = picking._delivery_zone_partner()
            picking.delivery_zone_id = (
                partner.delivery_zone_effective_id if partner else False
            )

    def _delivery_zone_partner(self):
        """Contacto del que sale la zona.

        Si route_optimizer está instalado se reutiliza su punto de extensión, que ya
        contempla tipos de operación personalizados.
        """
        self.ensure_one()
        if hasattr(self, "_route_optimizer_delivery_partner"):
            return self._route_optimizer_delivery_partner()
        return self.partner_id

    def _get_auto_batch_domain(self):
        """Agrega la zona como criterio de agrupación automática de lotes.

        El nombre del método y del campo del tipo de operación vienen de
        stock_picking_batch; se accede defensivamente para no romper si cambian.
        """
        domain = super()._get_auto_batch_domain()
        picking_type = self.picking_type_id
        if "batch_group_by_delivery_zone" not in picking_type._fields:
            return domain
        if picking_type.batch_group_by_delivery_zone:
            domain = list(domain or []) + [
                ("delivery_zone_id", "=", self.delivery_zone_id.id or False)
            ]
        return domain
