# -*- coding: utf-8 -*-
import base64

from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

# PNG de 1x1 válido: alcanza para firma y fotos.
PIXEL = base64.b64encode(
    base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )
).decode()


class RouteDeliveryCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.icp = cls.env["ir.config_parameter"].sudo()
        # Sin servidor de ruteo: las horas se estiman en línea recta.
        cls.icp.set_param("route_optimizer.osrm_url", "")
        cls.warehouse = cls.env["stock.warehouse"].search(
            [("company_id", "=", cls.env.company.id)], limit=1
        )
        cls.warehouse.partner_id.write({"partner_latitude": -34.60, "partner_longitude": -58.38})
        cls.driver = new_test_user(
            cls.env, login="chofer_test", groups="route_optimizer_delivery.group_route_delivery_driver"
        )
        cls.other_driver = new_test_user(
            cls.env, login="chofer_otro", groups="route_optimizer_delivery.group_route_delivery_driver"
        )
        cls.product = cls.env["product.product"].create(
            {"name": "Caja de prueba", "is_storable": True, "barcode": "7790001112223"}
        )
        cls.env["stock.quant"]._update_available_quantity(
            cls.product, cls.warehouse.lot_stock_id, 100
        )
        cls.customers = cls.env["res.partner"].create(
            [
                {
                    "name": "Cliente %s" % index,
                    "street": "Calle %s" % index,
                    "city": "Buenos Aires",
                    "email": "cliente%s@example.com" % index,
                    "phone": "+54 11 5555 000%s" % index,
                    "partner_latitude": -34.60 - 0.02 * index,
                    "partner_longitude": -58.38 - 0.02 * index,
                }
                for index in (1, 2, 3)
            ]
        )
        cls.pickings = cls.env["stock.picking"]
        for customer in cls.customers:
            picking = cls.env["stock.picking"].create(
                {
                    "partner_id": customer.id,
                    "picking_type_id": cls.warehouse.out_type_id.id,
                    "location_id": cls.warehouse.lot_stock_id.id,
                    "location_dest_id": cls.env.ref("stock.stock_location_customers").id,
                    "move_ids": [
                        (
                            0,
                            0,
                            {
                                "product_id": cls.product.id,
                                "product_uom_qty": 5,
                                "location_id": cls.warehouse.lot_stock_id.id,
                                "location_dest_id": cls.env.ref("stock.stock_location_customers").id,
                            },
                        )
                    ],
                }
            )
            picking.action_confirm()
            picking.action_assign()
            cls.pickings |= picking
        cls.batch = cls.env["stock.picking.batch"].create(
            {
                "picking_type_id": cls.warehouse.out_type_id.id,
                "user_id": cls.driver.id,
                "picking_ids": [(6, 0, cls.pickings.ids)],
            }
        )
        cls.batch.action_confirm()
        cls.Batch = cls.env["stock.picking.batch"]

    def app(self, user=None):
        return self.Batch.with_user(user or self.driver)

    def line_vals(self, picking, qty):
        return [{"id": picking.move_ids.id, "qty": qty}]


@tagged("post_install", "-at_install")
class TestRouteDelivery(RouteDeliveryCommon):
    def test_driver_only_sees_own_routes(self):
        mine = self.app().route_delivery_app_routes()
        self.assertEqual([r["id"] for r in mine["routes"]], [self.batch.id])
        self.assertFalse(mine["dispatcher"])
        other = self.app(self.other_driver).route_delivery_app_routes()
        self.assertFalse(other["routes"])
        with self.assertRaises(AccessError):
            self.app(self.other_driver).route_delivery_app_route(self.batch.id)
        with self.assertRaises(AccessError):
            self.app(self.other_driver).route_delivery_app_deliver(self.pickings[0].id, {})
        # Un usuario sin el grupo no entra a la app.
        outsider = new_test_user(self.env, login="sin_reparto", groups="base.group_user")
        with self.assertRaises(AccessError):
            self.app(outsider).route_delivery_app_routes()

    def test_route_payload_and_etas(self):
        route = self.app().route_delivery_app_start(self.batch.id, -34.60, -58.38)
        self.assertTrue(route["started"])
        self.assertEqual(len(route["stops"]), 3)
        self.assertTrue(route["reasons"])
        stop = route["stops"][0]
        self.assertEqual(stop["lines"][0]["qty"], 5)
        self.assertEqual(stop["lines"][0]["barcode"], "7790001112223")
        self.assertIn("/reparto/t/", stop["tracking_url"])
        etas = [s["eta"] for s in route["stops"]]
        self.assertTrue(all(etas), "Todas las paradas pendientes tienen hora estimada")
        self.assertEqual(etas, sorted(etas), "Las horas siguen el orden de visita")
        self.assertEqual(self.batch.route_delivery_last_latitude, -34.60)

    def test_deliver_full_flow(self):
        first, second, third = self.batch._route_delivery_stops()
        route = self.app().route_delivery_app_deliver(
            first.id,
            {
                "lines": self.line_vals(first, 5),
                "receiver_name": "Juana Pérez",
                "receiver_doc": "30111222",
                "packages": 2,
                "cod_amount": 1500.5,
                "signature": "data:image/png;base64," + PIXEL,
                "photos": ["data:image/jpeg;base64," + PIXEL],
                "lat": -34.62,
                "lon": -58.40,
            },
        )
        self.assertEqual(first.route_delivery_state, "delivered")
        self.assertEqual(first.route_delivery_user_id, self.driver)
        self.assertEqual(first.route_delivery_receiver_doc, "30111222")
        self.assertEqual(first.route_delivery_packages, 2)
        self.assertEqual(first.route_delivery_cod_amount, 1500.5)
        self.assertTrue(first.route_delivery_signature)
        self.assertEqual(len(first.route_delivery_photo_ids), 1)
        self.assertEqual(first.route_delivery_latitude, -34.62)
        # Sin el ajuste, la app no toca el inventario.
        self.assertEqual(first.state, "assigned")
        self.assertTrue(self.batch.route_delivery_started_at, "Entregar arranca la ruta sola")
        self.assertEqual(route["done_count"], 1)
        self.assertEqual(route["pending_count"], 2)

        reason = self.env.ref("route_optimizer_delivery.fail_reason_absent")
        self.app().route_delivery_app_fail(second.id, {"reason_id": reason.id})
        self.assertEqual(second.route_delivery_state, "failed")
        self.assertFalse(self.batch.route_delivery_finished_at)

        route = self.app().route_delivery_app_deliver(third.id, {"lines": self.line_vals(third, 5)})
        self.assertTrue(route["finished"])
        self.assertEqual(self.batch.route_delivery_progress, 100)
        self.assertEqual(self.batch.route_delivery_failed_count, 1)

        # Corregir una parada reabre la ruta.
        route = self.app().route_delivery_app_reopen(second.id)
        self.assertEqual(second.route_delivery_state, "pending")
        self.assertFalse(route["finished"])

    def test_validations(self):
        picking = self.pickings[0]
        with self.assertRaises(UserError):  # todo en cero no es una entrega
            self.app().route_delivery_app_deliver(picking.id, {"lines": self.line_vals(picking, 0)})
        with self.assertRaises(UserError):  # más de lo pedido
            self.app().route_delivery_app_deliver(picking.id, {"lines": self.line_vals(picking, 9)})
        with self.assertRaises(UserError):  # sin motivo
            self.app().route_delivery_app_fail(picking.id, {})
        other = self.env.ref("route_optimizer_delivery.fail_reason_other")
        with self.assertRaises(UserError):  # «Otro» pide aclaración
            self.app().route_delivery_app_fail(picking.id, {"reason_id": other.id})
        self.icp.set_param("route_optimizer_delivery.require_signature", "True")
        with self.assertRaises(UserError):
            self.app().route_delivery_app_deliver(picking.id, {"lines": self.line_vals(picking, 5)})
        self.assertEqual(picking.route_delivery_state, "pending")

    def test_partial_delivery_validates_with_backorder(self):
        self.icp.set_param("route_optimizer_delivery.validate_on_delivery", "True")
        picking = self.pickings[0]
        self.app().route_delivery_app_deliver(picking.id, {"lines": self.line_vals(picking, 3)})
        self.assertEqual(picking.route_delivery_state, "partial")
        self.assertIn("3 / 5", picking.route_delivery_partial_detail)
        self.assertEqual(picking.state, "done")
        self.assertEqual(picking.move_ids.quantity, 3)
        backorder = self.env["stock.picking"].search([("backorder_id", "=", picking.id)])
        self.assertEqual(backorder.move_ids.product_uom_qty, 2)
        self.assertEqual(backorder.route_delivery_state, "pending")

        full = self.pickings[1]
        self.app().route_delivery_app_deliver(full.id, {"lines": self.line_vals(full, 5)})
        self.assertEqual(full.state, "done")
        self.assertEqual(full.route_delivery_state, "delivered")

    def test_scan(self):
        picking = self.pickings[1]
        found = self.app().route_delivery_app_scan(picking.name.lower(), self.batch.id)
        self.assertEqual(found, {"batch_id": self.batch.id, "picking_id": picking.id})
        self.assertEqual(self.app().route_delivery_app_scan(self.batch.name)["batch_id"], self.batch.id)
        self.assertIn("error", self.app().route_delivery_app_scan("NO-EXISTE", self.batch.id))
        with self.assertRaises(AccessError):
            self.app(self.other_driver).route_delivery_app_scan(picking.name)

    def test_notifications(self):
        self.icp.set_param("route_optimizer_delivery.notify_email", "True")
        mails = self.env["mail.mail"].sudo()
        before = mails.search_count([])
        self.app().route_delivery_app_start(self.batch.id)
        # 3 «salió a reparto» + 1 «sos la próxima parada».
        self.assertEqual(mails.search_count([]) - before, 4)
        first = self.batch._route_delivery_stops()[0]
        self.app().route_delivery_app_deliver(first.id, {"lines": self.line_vals(first, 5)})
        # «entregado» + «sos la próxima» para la segunda.
        self.assertEqual(mails.search_count([]) - before, 6)
        self.assertIn("wa.me/541155550001", first._route_delivery_whatsapp_url())

    def test_dispatch_board(self):
        user = new_test_user(self.env, login="despacho_test", groups="stock.group_stock_user")
        board = self.Batch.with_user(user)
        day = fields.Date.to_string(
            fields.Datetime.context_timestamp(board, self.batch.scheduled_date).date()
        )
        data = board.route_dispatch_data(day)
        route = next(r for r in data["routes"] if r["id"] == self.batch.id)
        self.assertEqual(len(route["stops"]), 3)
        self.assertTrue(data["depot"])

        first, second, third = self.batch._route_delivery_stops()
        board.route_dispatch_move(third.id, self.batch.id, 0)
        self.assertEqual(self.batch._route_delivery_stops(), third | first | second)
        etas = self.batch._route_delivery_stops().mapped("route_delivery_eta")
        self.assertEqual(etas, sorted(etas))

        new_id = board.route_dispatch_new_route(day, second.id)
        new_batch = self.Batch.browse(new_id)
        self.assertEqual(second.batch_id, new_batch)
        self.assertEqual(len(self.batch.picking_ids), 2)

        board.route_dispatch_move(first.id, new_batch.id, None)
        self.assertEqual(new_batch._route_delivery_stops(), second | first)

        board.route_dispatch_move(first.id, False)
        self.assertFalse(first.batch_id)
        self.assertIn(first.id, [s["id"] for s in board.route_dispatch_data(day)["unassigned"]])

        third.route_delivery_state = "delivered"
        with self.assertRaises(UserError):
            board.route_dispatch_move(third.id, new_batch.id, 0)


    def _extra_picking(self, name, lat, lon, qty=4):
        partner = self.env["res.partner"].create(
            {"name": name, "partner_latitude": lat, "partner_longitude": lon}
        )
        customers = self.env.ref("stock.stock_location_customers")
        picking = self.env["stock.picking"].create(
            {
                "partner_id": partner.id,
                "picking_type_id": self.warehouse.out_type_id.id,
                "location_id": self.warehouse.lot_stock_id.id,
                "location_dest_id": customers.id,
                "scheduled_date": self.batch.scheduled_date,
                "move_ids": [
                    (0, 0, {
                        "product_id": self.product.id,
                        "product_uom_qty": qty,
                        "location_id": self.warehouse.lot_stock_id.id,
                        "location_dest_id": customers.id,
                    })
                ],
            }
        )
        picking.action_confirm()
        picking.action_assign()
        return picking

    def test_dispatch_suggest_and_assign(self):
        user = new_test_user(self.env, login="despacho_plan", groups="stock.group_stock_user")
        board = self.Batch.with_user(user)
        day = fields.Date.to_string(
            fields.Datetime.context_timestamp(board, self.batch.scheduled_date).date()
        )
        first, second, third = self.batch._route_delivery_stops()
        # Una ruta lejana: la entrega nueva queda entre la 1 y la 2 de la ruta cercana.
        far = self._extra_picking("Lejano", -34.90, -58.90)
        far_route = self.Batch.browse(board.route_dispatch_new_route(day, [far.id]))
        near = self._extra_picking("Entre 1 y 2", -34.63, -58.41)

        # Puede haber otras rutas ese día en la base: se miran solo las de la prueba.
        options = [
            o for o in board.route_dispatch_suggest([near.id], day)
            if o["batch_id"] in (self.batch.id, far_route.id)
        ]
        self.assertEqual([o["batch_id"] for o in options], [self.batch.id, far_route.id])
        best = options[0]
        self.assertEqual(best["position"], 2)
        self.assertEqual(best["stop_count"], 4)
        self.assertEqual(best["before"], first.partner_id.display_name)
        self.assertEqual(best["after"], second.partner_id.display_name)
        self.assertLess(best["extra_km"], options[1]["extra_km"])
        self.assertFalse(best["over_capacity"])

        board.route_dispatch_assign([near.id], self.batch.id)
        self.assertEqual(self.batch._route_delivery_stops(), first | near | second | third)
        self.assertTrue(near.route_delivery_eta)
        self.assertTrue(near.route_delivery_token, "Al entrar a una ruta ya tiene link de seguimiento")
        self.assertGreater(self.batch.route_delivery_remaining_km, 0)
        self.assertEqual(self.batch.route_delivery_routing_source, "estimate")

        # Varias a la vez a una ruta nueva; lo ya visitado no se mueve.
        new_id = board.route_dispatch_new_route(day, [near.id, third.id])
        self.assertEqual(self.Batch.browse(new_id).route_delivery_stop_count, 2)
        first.route_delivery_state = "delivered"
        with self.assertRaises(UserError):
            board.route_dispatch_assign([first.id], new_id)
        board.route_dispatch_unassign([near.id, first.id])
        self.assertFalse(near.batch_id)
        self.assertEqual(first.batch_id, self.batch)

    def test_dispatch_publish_and_driver(self):
        user = new_test_user(self.env, login="despacho_pub", groups="stock.group_stock_user")
        board = self.Batch.with_user(user)
        day = fields.Date.to_string(
            fields.Datetime.context_timestamp(board, self.batch.scheduled_date).date()
        )
        extra = self._extra_picking("Nuevo", -34.70, -58.50)
        draft = self.Batch.browse(board.route_dispatch_new_route(day, [extra.id]))
        self.assertEqual(draft.state, "draft")
        # Un borrador no aparece en la app del chofer.
        result = board.route_dispatch_publish(day)
        self.assertIn(draft.name, result["without_driver"])
        self.assertEqual(draft.state, "draft")
        board.route_dispatch_set_driver(draft.id, self.other_driver.id)
        self.assertFalse(self.app(self.other_driver).route_delivery_app_routes()["routes"])
        self.assertEqual(board.route_dispatch_publish(day)["published"], 1)
        self.assertEqual(draft.state, "in_progress")
        routes = self.app(self.other_driver).route_delivery_app_routes()["routes"]
        self.assertEqual([r["id"] for r in routes], [draft.id])
        data = board.route_dispatch_data(day)
        self.assertIn(self.driver.id, [d["id"] for d in data["drivers"]])
        self.assertEqual(data["map"]["provider"], "osm")

    def test_incidents_retry_and_postpone(self):
        board = self.Batch
        first, second, third = self.batch._route_delivery_stops()
        self.app().route_delivery_app_start(self.batch.id)
        self.assertEqual(first.route_delivery_planned_eta, first.route_delivery_eta)
        reason = self.env.ref("route_optimizer_delivery.fail_reason_closed")
        self.app().route_delivery_app_fail(first.id, {"reason_id": reason.id})
        board.route_dispatch_retry(first.id)
        self.assertEqual(first.route_delivery_state, "pending")
        self.assertEqual(self.batch._route_delivery_stops(), second | third | first, "Vuelve al final")

        self.app().route_delivery_app_fail(second.id, {"reason_id": reason.id})
        before = second.scheduled_date
        board.route_dispatch_postpone(second.id)
        self.assertFalse(second.batch_id)
        self.assertEqual(second.route_delivery_state, "pending")
        self.assertEqual((second.scheduled_date - before).days, 1)

    def test_google_road_path(self):
        """Con Google como proveedor, un pedido por ruta trae tramos con tráfico y el dibujo."""
        self.icp.set_param("route_optimizer.matrix_provider", "google")
        self.icp.set_param("route_optimizer.google_api_key", "clave-de-prueba")
        calls = []

        def fake_http(url, payload, headers, timeout):
            calls.append((url, payload, headers))
            legs = len(payload["intermediates"]) + 1
            return {"routes": [{
                "legs": [{"duration": "600s", "distanceMeters": 4000}] * legs,
                "polyline": {"encodedPolyline": "_p~iF~ps|U_ulLnnqC_mqNvxq`@"},
            }]}

        target = "odoo.addons.route_optimizer.services.google_client._http_json"
        with patch(target, side_effect=fake_http):
            self.batch._route_delivery_update_etas()
        self.assertEqual(len(calls), 1, "Un solo pedido computeRoutes para toda la ruta")
        url, payload, headers = calls[0]
        self.assertIn("directions/v2:computeRoutes", url)
        self.assertEqual(headers["X-Goog-Api-Key"], "clave-de-prueba")
        self.assertEqual(payload["routingPreference"], "TRAFFIC_AWARE")
        self.assertEqual(len(payload["intermediates"]), 2)
        self.assertEqual(payload["origin"]["location"]["latLng"]["latitude"], -34.60)
        self.assertEqual(self.batch.route_delivery_routing_source, "google")
        self.assertEqual(self.batch.route_delivery_polyline, "_p~iF~ps|U_ulLnnqC_mqNvxq`@")
        self.assertAlmostEqual(self.batch.route_delivery_remaining_km, 12.0)
        etas = self.batch._route_delivery_stops().mapped("route_delivery_eta")
        # 10 min de viaje + 5 de atención entre paradas.
        self.assertEqual([(b - a).total_seconds() for a, b in zip(etas, etas[1:])], [900.0, 900.0])
        data = self.batch._route_dispatch_route_data()
        self.assertEqual(data["polylines"], ["_p~iF~ps|U_ulLnnqC_mqNvxq`@"])

        # Si Google falla, se estima y nadie queda frenado.
        from odoo.addons.route_optimizer.services import google_client
        with patch(target, side_effect=google_client.GoogleApiError("sin cuota")):
            self.batch._route_delivery_update_etas()
        self.assertEqual(self.batch.route_delivery_routing_source, "estimate")
        self.assertFalse(self.batch.route_delivery_polyline)

    def test_google_route_chunks(self):
        from odoo.addons.route_optimizer.services import google_client
        points = [(-58.0 - i / 100, -34.0 - i / 100) for i in range(60)]
        sizes = []

        def fake_http(url, payload, headers, timeout):
            sizes.append(len(payload["intermediates"]))
            return {"routes": [{"legs": [{"duration": "60s", "distanceMeters": 500}] * (sizes[-1] + 1),
                                "polyline": {"encodedPolyline": "abc"}}]}

        with patch.object(google_client, "_http_json", side_effect=fake_http):
            result = google_client.compute_route("k", points)
        self.assertEqual(sizes, [25, 25, 6])
        self.assertEqual(len(result["durations"]), 59)
        self.assertEqual(len(result["polylines"]), 3)


@tagged("post_install", "-at_install")
class TestRouteDeliveryHttp(RouteDeliveryCommon, HttpCase):
    def test_tracking_page(self):
        picking = self.batch._route_delivery_stops()[1]
        picking._route_delivery_ensure_token()
        url = "/reparto/t/%s" % picking.route_delivery_token
        response = self.url_open(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Entrega programada", response.text)
        self.assertIn("noindex", response.headers.get("X-Robots-Tag", ""))

        self.app().route_delivery_app_start(self.batch.id)
        response = self.url_open(url)
        self.assertIn("En camino", response.text)
        self.assertIn("Falta 1 parada", response.text)
        self.assertIn("Horario estimado", response.text)

        first = self.batch._route_delivery_stops()[0]
        self.app().route_delivery_app_deliver(first.id, {"lines": self.line_vals(first, 5)})
        self.assertIn("Sos la próxima parada", self.url_open(url).text)

        self.app().route_delivery_app_deliver(
            picking.id, {"lines": self.line_vals(picking, 5), "receiver_name": "Juana Pérez"}
        )
        response = self.url_open(url)
        self.assertIn("Entregado", response.text)
        self.assertIn("Juana Pérez", response.text)

        self.assertEqual(self.url_open("/reparto/t/" + "0" * 32).status_code, 404)
        self.assertEqual(self.url_open("/reparto/t/x").status_code, 404)

    def test_installable_app_manifest(self):
        response = self.url_open(
            "/web/manifest.scoped_app_manifest?app_id=route_optimizer_delivery"
            "&path=/scoped_app/reparto"
        )
        self.assertEqual(response.status_code, 200)
        manifest = response.json()
        self.assertEqual(manifest["name"], "Reparto")
        self.assertEqual(manifest["scope"], "/scoped_app/reparto")
        for icon in manifest["icons"]:
            self.assertEqual(self.url_open(icon["src"]).status_code, 200)
        action = self.env.ref("route_optimizer_delivery.action_route_delivery_app")
        self.assertEqual(action.path, "reparto")
