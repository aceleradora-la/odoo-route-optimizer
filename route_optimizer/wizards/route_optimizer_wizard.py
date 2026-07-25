# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services import delivery_windows
from ..services.route_service import _param_bool, optimize_batch


class RouteOptimizerWizard(models.TransientModel):
    _name = "route.optimizer.wizard"
    _description = "Optimizar ruta de entrega (OSRM + OR-Tools)"

    batch_id = fields.Many2one(
        "stock.picking.batch",
        string="Traslado por lote",
        required=True,
        ondelete="cascade",
    )
    num_vehicles = fields.Integer(
        string="Vehículos",
        default=1,
        help="Cantidad de vehículos para el VRP. 1 = solo reordenar las paradas dentro de este lote.",
    )
    use_duration = fields.Boolean(
        string="Optimizar por duración",
        default=True,
        help="Si está activo, usa la matriz de tiempos de viaje; si no, la de distancias.",
    )
    depot_partner_id = fields.Many2one(
        "res.partner",
        string="Dirección del depósito",
        help="Por defecto, la dirección del almacén. Se usa como inicio/fin de la ruta.",
    )
    vehicle_capacity = fields.Float(
        string="Capacidad del vehículo",
        help="Capacidad máxima opcional por vehículo (misma unidad que el peso del traslado). "
        "Dejar vacío para usar un valor no restrictivo en el solver.",
    )
    vehicle_volume_capacity = fields.Float(
        string="Capacidad de volumen del vehículo",
        help="Capacidad máxima de volumen opcional por vehículo (misma unidad que el volumen del "
        "traslado, normalmente m³). Dejar vacío para usar un valor no restrictivo en el solver.",
    )
    max_stops_per_vehicle = fields.Integer(
        string="Máx. paradas por vehículo",
        help="Límite duro opcional para forzar la división de paradas entre vehículos.",
    )
    max_route_duration_minutes = fields.Integer(
        string="Duración máx. de ruta (minutos)",
        help="Límite duro opcional de duración por ruta de vehículo (requiere optimizar por duración).",
    )

    @api.onchange("batch_id")
    def _onchange_batch_depot(self):
        for wiz in self:
            wh = wiz.batch_id.warehouse_id
            if wh and wh.partner_id:
                wiz.depot_partner_id = wh.partner_id
            elif wiz.batch_id.company_id.partner_id:
                wiz.depot_partner_id = wiz.batch_id.company_id.partner_id

    def action_optimize(self):
        self.ensure_one()
        if self.num_vehicles < 1:
            raise UserError(_("La cantidad de vehículos debe ser al menos 1."))
        if self.use_duration:
            pickings = self.batch_id.picking_ids.filtered(lambda p: p.state != "cancel")
            stops = [
                {"picking": p, "partner": p._route_optimizer_delivery_partner()}
                for p in pickings
                if p._route_optimizer_delivery_partner()
            ]
            warnings = delivery_windows.validate_stops_delivery_windows(
                self.env, self.batch_id, stops
            )
            if warnings:
                icp = self.env["ir.config_parameter"].sudo()
                block = _param_bool(
                    icp.get_param("route_optimizer.block_outside_windows", "False"),
                    default=False,
                )
                body = _("Advertencias de ventana horaria:\n") + "\n".join(f"• {w}" for w in warnings)
                if block:
                    raise UserError(body)
                self.batch_id.message_post(body=body)
        res = optimize_batch(
            self.env,
            self.batch_id,
            num_vehicles=self.num_vehicles,
            use_duration=self.use_duration,
            depot_partner=self.depot_partner_id,
            vehicle_capacity=self.vehicle_capacity or None,
            vehicle_volume_capacity=self.vehicle_volume_capacity or None,
            max_stops_per_vehicle=self.max_stops_per_vehicle or None,
            max_route_duration_minutes=self.max_route_duration_minutes or None,
        )
        msg = res.get("message") or ""
        summary = (self.batch_id.route_optimizer_visit_summary or "").strip()
        if msg:
            body = f"{msg}\n\n{summary}" if summary else msg
            self.batch_id.message_post(body=body)
        return {"type": "ir.actions.act_window_close"}
