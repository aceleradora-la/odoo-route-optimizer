# -*- coding: utf-8 -*-
from odoo import api, fields, models


class RouteOptimizerWizard(models.TransientModel):
    _inherit = "route.optimizer.wizard"

    fleet_vehicle_id = fields.Many2one(
        "fleet.vehicle",
        string="Vehículo de flota",
        help="Vínculo opcional para trazabilidad; definí la capacidad más abajo para el solver.",
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

        The field lists below are intentionally heuristic: Odoo and its localizations
        use many different naming conventions for vehicle payload capacity (weight_capacity,
        capacity, x_payload, etc.). Adding a new field name here is safe — it only fires
        when no earlier candidate matched. To guarantee a specific field is used, add it
        to the top of the relevant candidates list.
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
        """Ensure defaults from context also prefill capacities.

        IMPORTANT: Do not access wizard field getters here (may recurse into default_get).
        Work only with the returned dict + browsed vehicle record.
        """
        res = super().default_get(fields_list)

        fleet_vehicle_id = res.get("fleet_vehicle_id") or self.env.context.get("default_fleet_vehicle_id")
        if not fleet_vehicle_id:
            return res

        vehicle = self.env["fleet.vehicle"].browse(fleet_vehicle_id)
        if not vehicle.exists():
            return res

        category = getattr(getattr(vehicle, "model_id", None), "category_id", None)

        # Weight capacity (kg)
        current_weight = res.get("vehicle_capacity") or 0.0
        if (not current_weight) or float(current_weight) <= 0:
            weight_cap = None
            if category and "weight_capacity" in category._fields:
                try:
                    weight_cap = float(category.weight_capacity or 0.0)
                except (TypeError, ValueError):
                    weight_cap = None
            cap = weight_cap or self._fleet_capacity_candidates(vehicle)
            if cap:
                res["vehicle_capacity"] = cap

        # Volume capacity (m³)
        # Do not rely on fields_list here; in some onchange/default_get flows Odoo may call
        # default_get with a reduced set of fields and still render the full form later.
        if "vehicle_volume_capacity" in self._fields:
            current_vol = res.get("vehicle_volume_capacity") or 0.0
            if (not current_vol) or float(current_vol) <= 0:
                vol_cap = None
                if category and "volume_capacity" in category._fields:
                    try:
                        vol_cap = float(category.volume_capacity or 0.0)
                    except (TypeError, ValueError):
                        vol_cap = None
                if vol_cap and vol_cap > 0:
                    res["vehicle_volume_capacity"] = vol_cap

        return res

    @api.onchange("batch_id")
    def _onchange_batch_vehicle(self):
        """If the batch has a fleet vehicle field (vehicle_id), use it by default."""
        for wiz in self:
            batch = wiz.batch_id
            if batch and "vehicle_id" in batch._fields and batch.vehicle_id:
                wiz.fleet_vehicle_id = batch.vehicle_id
                wiz._prefill_capacities_from_vehicle()
