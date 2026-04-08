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
        # Prefer weight-based capacity first (Odoo sends demands based on shipping_weight).
        candidates = [
            # Standard fleet capacity fields on model category in newer Odoo versions
            "weight_capacity",
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
        # Secondary: volume capacity (if the DB models capacity as volume).
        volume_candidates = [
            "volume_capacity",
            "x_volume_capacity",
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
            val = self._first_positive_number(rec, volume_candidates)
            if val:
                return val
        return None

    def _prefill_capacities_from_vehicle(self):
        """Fill weight/volume capacities from fleet vehicle/category when empty."""
        for wiz in self:
            if not wiz.fleet_vehicle_id:
                continue
            category = getattr(getattr(wiz.fleet_vehicle_id, "model_id", None), "category_id", None)

            # Weight capacity (kg)
            if not wiz.vehicle_capacity or wiz.vehicle_capacity <= 0:
                weight_cap = None
                if category and "weight_capacity" in category._fields:
                    try:
                        weight_cap = float(category.weight_capacity or 0.0)
                    except (TypeError, ValueError):
                        weight_cap = None
                cap = weight_cap or wiz._fleet_capacity_candidates(wiz.fleet_vehicle_id)
                if cap:
                    wiz.vehicle_capacity = cap

            # Volume capacity (m³)
            if "vehicle_volume_capacity" in wiz._fields and (
                not wiz.vehicle_volume_capacity or wiz.vehicle_volume_capacity <= 0
            ):
                vol_cap = None
                if category and "volume_capacity" in category._fields:
                    try:
                        vol_cap = float(category.volume_capacity or 0.0)
                    except (TypeError, ValueError):
                        vol_cap = None
                if vol_cap and vol_cap > 0:
                    wiz.vehicle_volume_capacity = vol_cap

    @api.onchange("fleet_vehicle_id")
    def _onchange_fleet_vehicle_capacity(self):
        """
        Prefill wizard capacity from the selected fleet vehicle (if available).
        Only overrides when capacity is empty/zero.
        """
        self._prefill_capacities_from_vehicle()

    @api.model
    def default_get(self, fields_list):
        """Ensure defaults from context also prefill capacities."""
        res = super().default_get(fields_list)
        wiz = self.new(res)
        wiz._prefill_capacities_from_vehicle()
        res.update(wiz._convert_to_write(wiz._cache))
        return res

    @api.onchange("batch_id")
    def _onchange_batch_vehicle(self):
        """If the batch has a fleet vehicle field (vehicle_id), use it by default."""
        for wiz in self:
            batch = wiz.batch_id
            if batch and "vehicle_id" in batch._fields and batch.vehicle_id:
                wiz.fleet_vehicle_id = batch.vehicle_id
                wiz._prefill_capacities_from_vehicle()
