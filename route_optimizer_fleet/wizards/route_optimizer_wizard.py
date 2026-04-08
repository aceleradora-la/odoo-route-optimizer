# -*- coding: utf-8 -*-
from odoo import api, fields, models


class RouteOptimizerWizard(models.TransientModel):
    _inherit = "route.optimizer.wizard"

    fleet_vehicle_id = fields.Many2one(
        "fleet.vehicle",
        string="Fleet vehicle",
        help="Optional link for traceability; set capacity below for the solver.",
    )

    @staticmethod
    def _first_positive_number(record, field_names):
        """Return the first positive numeric value found on record for the given fields."""
        if not record:
            return None
        for fname in field_names:
            if fname in record._fields:
                try:
                    val = record[fname]
                except Exception:
                    continue
                try:
                    fval = float(val)
                except (TypeError, ValueError):
                    continue
                if fval > 0:
                    return fval
        return None

    def _fleet_capacity_candidates(self, vehicle):
        """
        Try to infer a capacity from common fields in Fleet customizations.

        We look in this order: vehicle → type → model → category.
        This keeps the bridge compatible with different localizations/custom modules.
        """
        candidates = [
            # very common custom names
            "capacity",
            "vehicle_capacity",
            "load_capacity",
            "max_load",
            "payload",
            "max_weight",
            "weight_capacity",
            "route_optimizer_capacity",
            # studio-style fields (common in real dbs)
            "x_capacity",
            "x_vehicle_capacity",
            "x_load_capacity",
            "x_payload",
            "x_max_weight",
        ]
        for rec in (
            vehicle,
            getattr(vehicle, "vehicle_type_id", None),
            getattr(vehicle, "model_id", None),
            getattr(getattr(vehicle, "model_id", None), "category_id", None),
        ):
            val = self._first_positive_number(rec, candidates)
            if val:
                return val
        return None

    @api.onchange("fleet_vehicle_id")
    def _onchange_fleet_vehicle_capacity(self):
        """
        Prefill wizard capacity from the selected fleet vehicle (if available).
        Only overrides when capacity is empty/zero.
        """
        for wiz in self:
            if wiz.fleet_vehicle_id and (not wiz.vehicle_capacity or wiz.vehicle_capacity <= 0):
                cap = wiz._fleet_capacity_candidates(wiz.fleet_vehicle_id)
                if cap:
                    wiz.vehicle_capacity = cap
