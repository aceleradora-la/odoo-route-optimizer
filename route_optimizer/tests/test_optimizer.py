# -*- coding: utf-8 -*-
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

SERVICE = "odoo.addons.route_optimizer.services.route_service"


@tagged("post_install", "-at_install")
class TestOptimizerPayload(TransactionCase):
    """Qué le pide Odoo al solver y cómo aplica su respuesta (sin servicios externos)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.icp = cls.env["ir.config_parameter"].sudo()
        cls.icp.set_param("route_optimizer.ortools_simple_api", "False")
        cls.icp.set_param("route_optimizer.ortools_url", "http://solver.test/vrp")
        cls.icp.set_param("route_optimizer.solver_provider", "ortools")
        cls.icp.set_param("route_optimizer.matrix_provider", "osrm")
        cls.warehouse = cls.env["stock.warehouse"].search(
            [("company_id", "=", cls.env.company.id)], limit=1
        )
        cls.warehouse.partner_id.write({"partner_latitude": -34.60, "partner_longitude": -58.38})
        product = cls.env["product.product"].create(
            {
                "name": "Bulto",
                "weight": 10,
                **(
                    {"is_storable": True}
                    if "is_storable" in cls.env["product.template"]._fields
                    else {"detailed_type": "product"}
                ),
            }
        )
        customers = cls.env.ref("stock.stock_location_customers")
        cls.pickings = cls.env["stock.picking"]
        for index in range(1, 5):
            partner = cls.env["res.partner"].create(
                {
                    "name": "Cliente %s" % index,
                    "partner_latitude": -34.60 - 0.01 * index,
                    "partner_longitude": -58.38 - 0.01 * index,
                }
            )
            picking = cls.env["stock.picking"].create(
                {
                    "partner_id": partner.id,
                    "picking_type_id": cls.warehouse.out_type_id.id,
                    "location_id": cls.warehouse.lot_stock_id.id,
                    "location_dest_id": customers.id,
                    "move_ids": [(0, 0, {
                        "product_id": product.id,
                        **({"name": product.name} if "name" in cls.env["stock.move"]._fields else {}),
                        "product_uom_qty": 1,
                        "location_id": cls.warehouse.lot_stock_id.id,
                        "location_dest_id": customers.id,
                    })],
                }
            )
            picking.action_confirm()
            cls.pickings |= picking
        cls.pickings[3].priority = "1"
        cls.batch = cls.env["stock.picking.batch"].create(
            {
                "picking_type_id": cls.warehouse.out_type_id.id,
                "picking_ids": [(6, 0, cls.pickings.ids)],
            }
        )

    def _optimize(self, wizard_vals, routes):
        """Corre el asistente con la matriz y el solver simulados; devuelve el pedido.

        `routes`: una lista de traslados por vehículo, en orden de visita (vacía = sin usar).
        """
        sent = {}
        size = len(self.pickings) + 1
        table = {
            "durations": [[0 if i == j else 600 for j in range(size)] for i in range(size)],
            "distances": [[0 if i == j else 4000 for j in range(size)] for i in range(size)],
        }

        def fake_solve(url, payload, timeout=60, api_key=None):
            sent.update(payload)
            node = {pid: index + 1 for index, pid in enumerate(payload["picking_ids"])}
            return {
                "success": True,
                "routes": [
                    {"node_indices": [0] + [node[p.id] for p in stops] + [0], "vehicle_index": vehicle}
                    for vehicle, stops in enumerate(routes)
                ],
            }

        wizard = self.env["route.optimizer.wizard"].create(dict(wizard_vals, batch_id=self.batch.id))
        with patch(SERVICE + "._fetch_matrix", return_value=table), patch(
            SERVICE + ".ortools_client.solve_vrp", side_effect=fake_solve
        ):
            wizard.action_optimize()
        return sent

    def test_fleet_with_different_capacities(self):
        """Un renglón por vehículo: capacidades propias y cada ruta sabe cuál le tocó."""
        p1, p2, p3, p4 = self.pickings
        sent = self._optimize(
            {
                "num_vehicles": 1,  # lo pisan los renglones
                "vehicle_line_ids": [
                    (0, 0, {"sequence": 1, "name": "Camión", "capacity": 600, "volume_capacity": 12}),
                    (0, 0, {"sequence": 2, "name": "Utilitario", "capacity": 150}),
                    (0, 0, {"sequence": 3, "name": "Moto", "capacity": 20}),
                ],
            },
            [[p4, p3], [], [p2, p1]],  # el segundo vehículo queda sin usar
        )
        self.assertEqual(sent["num_vehicles"], 3)
        self.assertEqual(sent["vehicle_capacities_weight"], [600.0, 150.0, 20.0])
        # 0 en volumen = sin tope: se manda un valor holgado, no cero.
        self.assertEqual(sent["vehicle_capacities_volume"][0], 12.0)
        self.assertGreater(sent["vehicle_capacities_volume"][1], 0)
        # La ruta vacía no genera lote; quedan dos.
        self.assertEqual(self.batch.picking_ids.sorted("batch_sequence"), p4 | p3)
        self.assertEqual(p1.batch_id, p2.batch_id)
        self.assertNotEqual(p1.batch_id, self.batch)
        self.assertEqual(p1.batch_id.picking_ids.sorted("batch_sequence"), p2 | p1)

    def test_pickup_in_delivery_route(self):
        """Un retiro entra al lote de entregas solo con el ajuste, y el solver sabe que suma carga."""
        suppliers = self.env.ref("stock.stock_location_suppliers")
        partner = self.env["res.partner"].create(
            {"name": "Proveedor", "partner_latitude": -34.65, "partner_longitude": -58.45}
        )
        product = self.pickings[0].move_ids.product_id
        pickup = self.env["stock.picking"].create(
            {
                "partner_id": partner.id,
                "picking_type_id": self.warehouse.in_type_id.id,
                "location_id": suppliers.id,
                "location_dest_id": self.warehouse.lot_stock_id.id,
                "move_ids": [(0, 0, {
                    "product_id": product.id,
                    **({"name": product.name} if "name" in self.env["stock.move"]._fields else {}),
                    "product_uom_qty": 2,
                    "location_id": suppliers.id,
                    "location_dest_id": self.warehouse.lot_stock_id.id,
                })],
            }
        )
        pickup.action_confirm()
        self.assertTrue(pickup._route_optimizer_is_pickup())
        self.assertFalse(self.pickings[0]._route_optimizer_is_pickup())
        with self.assertRaises(UserError):
            self.batch.write({"picking_ids": [(4, pickup.id)]})
        self.icp.set_param("route_optimizer.allow_pickups_in_batch", "True")
        self.batch.invalidate_recordset(["allowed_picking_ids"])
        self.batch.write({"picking_ids": [(4, pickup.id)]})
        self.assertIn(pickup, self.batch.picking_ids)

        p1, p2, p3, p4 = self.pickings
        self.pickings = self.pickings | pickup  # la matriz simulada se arma con esto
        sent = self._optimize({"num_vehicles": 1}, [[p1, p2, pickup, p3, p4]])
        node = sent["picking_ids"].index(pickup.id) + 1
        self.assertEqual(sum(sent["pickups"]), 1)
        self.assertEqual(sent["pickups"][node], 1)
        self.assertEqual(self.batch.picking_ids.sorted("batch_sequence")[2], pickup)

    def test_priority_and_break(self):
        self.icp.set_param("route_optimizer.break_duration_minutes", "45")
        self.icp.set_param("route_optimizer.break_earliest_hour", "12.5")
        self.icp.set_param("route_optimizer.break_latest_hour", "13.5")
        self.icp.set_param("route_optimizer.route_start_hour", "8")
        p1, p2, p3, p4 = self.pickings
        sent = self._optimize({"num_vehicles": 1}, [[p4, p1, p2, p3]])
        # Solo la 4.ª entrega es urgente.
        urgent_node = sent["picking_ids"].index(p4.id) + 1
        self.assertEqual(sum(sent["priorities"]), 1)
        self.assertEqual(sent["priorities"][urgent_node], 1)
        self.assertEqual(sent["break_duration_seconds"], 45 * 60)
        self.assertEqual(sent["break_earliest_seconds"], int(12.5 * 3600))
        self.assertEqual(sent["break_latest_seconds"], int(13.5 * 3600))
        self.assertEqual(sent["matrix_metric"], "duration")
        # El descanso necesita la hora de salida y la atención por parada.
        self.assertIn("route_start_seconds", sent)
        self.assertEqual(len(sent["service_times"]), 5)
        self.assertEqual(self.batch.picking_ids.sorted("batch_sequence")[0], p4)

    def test_plain_optimization_unchanged(self):
        """Sin renglones, prioridad ni descanso, el pedido es el de siempre."""
        self.pickings[3].priority = "0"
        sent = self._optimize({"num_vehicles": 1, "vehicle_capacity": 100}, [list(self.pickings)])
        self.assertEqual(sent["vehicle_capacities_weight"], [100.0])
        self.assertNotIn("priorities", sent)
        self.assertNotIn("break_duration_seconds", sent)
