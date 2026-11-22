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
                                **({"name": cls.product.name} if "name" in cls.env["stock.move"]._fields else {}),
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
                        **({"name": self.product.name} if "name" in self.env["stock.move"]._fields else {}),
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

    def test_reports(self):
        """Entregas a tiempo, tarde y fallidas; km planificados contra recorridos."""
        self.app().route_delivery_app_start(self.batch.id, -34.600, -58.380)
        self.assertGreater(self.batch.route_delivery_planned_km, 0)
        self.app().route_delivery_app_ping(self.batch.id, -34.609, -58.380)  # ~1 km al sur
        self.assertAlmostEqual(self.batch.route_delivery_driven_km, 1.0, delta=0.05)
        first, second, third = self.batch._route_delivery_stops()
        self.app().route_delivery_app_deliver(first.id, {"lines": self.line_vals(first, 5), "cod_amount": 100})
        # La segunda se entrega 40 minutos después de lo previsto.
        second.route_delivery_planned_eta = fields.Datetime.subtract(fields.Datetime.now(), minutes=40)
        self.app().route_delivery_app_deliver(second.id, {"lines": self.line_vals(second, 5)})
        reason = self.env.ref("route_optimizer_delivery.fail_reason_absent")
        self.app().route_delivery_app_fail(third.id, {"reason_id": reason.id})
        self.env.flush_all()

        Report = self.env["route.delivery.report"]
        rows = Report.search([("batch_id", "=", self.batch.id)])
        self.assertEqual(len(rows), 3)
        self.assertEqual(sum(rows.mapped("delivered_count")), 2)
        self.assertEqual(sum(rows.mapped("failed_count")), 1)
        self.assertEqual(sum(rows.mapped("on_time_count")), 1)
        late = rows.filtered(lambda r: r.picking_id == second)
        self.assertEqual(late.late_count, 1)
        self.assertAlmostEqual(late.delay_minutes, 40, delta=1)
        self.assertEqual(rows.filtered(lambda r: r.picking_id == third).fail_reason_id, reason)
        self.assertEqual(sum(rows.mapped("cod_amount")), 100)
        self.assertEqual(set(rows.mapped("driver_id").ids), {self.driver.id})
        grouped = Report._read_group([("batch_id", "=", self.batch.id)], ["state"], ["stop_count:sum"])
        self.assertEqual(dict(grouped), {"delivered": 2, "failed": 1})

        route = self.env["route.delivery.route.report"].search([("batch_id", "=", self.batch.id)])
        self.assertEqual((route.stop_count, route.delivered_count, route.failed_count), (3, 2, 1))
        self.assertAlmostEqual(route.planned_km, self.batch.route_delivery_planned_km, places=2)
        self.assertAlmostEqual(route.driven_km, self.batch.route_delivery_driven_km, places=2)

    def test_whatsapp_notifications(self):
        from odoo.addons.route_optimizer_delivery.services import whatsapp_client as wa

        self.assertEqual(wa.normalize_phone("+54 9 11 5555-0001"), "5491155550001")
        self.assertEqual(wa.normalize_phone("011 5555 0001", "54"), "541155550001")
        self.assertEqual(wa.normalize_phone("0054 11 5555 0001", "54"), "541155550001")
        self.assertEqual(wa.normalize_phone("123"), "")

        for key, value in {
            "whatsapp_enabled": "True", "whatsapp_phone_id": "PHONE1", "whatsapp_token": "tok",
            "whatsapp_tpl_on_the_way": "reparto_en_camino", "whatsapp_tpl_next": "reparto_proxima_parada",
            "whatsapp_tpl_delivered": "reparto_entregado", "whatsapp_language": "es_AR",
        }.items():
            self.icp.set_param("route_optimizer_delivery." + key, value)
        sent = []

        def fake_post(url, payload, token, timeout):
            sent.append((url, payload, token))
            return {"messages": [{"id": "wamid.1"}]}

        target = "odoo.addons.route_optimizer_delivery.services.whatsapp_client._post"
        with patch(target, side_effect=fake_post):
            self.app().route_delivery_app_start(self.batch.id)
        # 3 «salió a reparto» + 1 «sos la próxima parada».
        self.assertEqual([p["template"]["name"] for _u, p, _t in sent].count("reparto_en_camino"), 3)
        self.assertEqual([p["template"]["name"] for _u, p, _t in sent].count("reparto_proxima_parada"), 1)
        url, payload, token = sent[0]
        self.assertIn("/PHONE1/messages", url)
        self.assertEqual(token, "tok")
        self.assertEqual(payload["template"]["language"], {"code": "es_AR"})
        texts = [x["text"] for x in payload["template"]["components"][0]["parameters"]]
        self.assertEqual(len(texts), 3)
        self.assertIn("/reparto/t/", texts[2])
        self.assertTrue(payload["to"].startswith("54"))

        # Si Meta rechaza el envío, queda una nota y la entrega se registra igual.
        first = self.batch._route_delivery_stops()[0]
        with patch(target, side_effect=wa.WhatsappError("plantilla sin aprobar")):
            self.app().route_delivery_app_deliver(first.id, {"lines": self.line_vals(first, 5)})
        self.assertEqual(first.route_delivery_state, "delivered")
        self.assertTrue(first.message_ids.filtered(lambda m: "plantilla sin aprobar" in (m.body or "")))

    def test_sms_providers(self):
        """Cada proveedor recibe el pedido con su forma: URL, autenticación y campos."""
        from odoo.addons.route_optimizer_delivery.services import sms_client as sms

        calls = []
        replies = {
            "twilio": {"sid": "SM1"},
            "vonage": {"messages": [{"status": "0", "message-id": "V1"}]},
            "plivo": {"message_uuid": ["P1"]},
            "telnyx": {"data": {"id": "T1"}},
            "infobip": {"messages": [{"messageId": "I1", "status": {"groupName": "PENDING"}}]},
            "clicksend": {"response_code": "SUCCESS", "data": {"messages": [{"status": "SUCCESS", "message_id": "C1"}]}},
            "bird": {"id": "B1", "status": "accepted"},
        }

        def fake_request(url, headers, form=None, payload=None, timeout=10):
            calls.append({"url": url, "headers": headers, "form": form, "payload": payload})
            return replies[self.provider]

        cred = {"account": "ACC", "secret": "bk_eu1_SEC", "sender": "+541100000000", "base_url": ""}
        text = "Tu pedido salio a reparto"
        with patch.object(sms, "_request", side_effect=fake_request):
            ids = {}
            for self.provider, _label in sms.PROVIDERS:
                account = dict(cred, base_url="abc.api.infobip.com" if self.provider == "infobip" else "")
                ids[self.provider] = sms.send(self.provider, account, "5491155550001", text)
        self.assertEqual(ids, {"twilio": "SM1", "vonage": "V1", "plivo": "P1", "telnyx": "T1", "infobip": "I1", "clicksend": "C1", "bird": "B1"})
        twilio, vonage, plivo, telnyx, infobip, clicksend, bird = calls
        self.assertIn("/Accounts/ACC/Messages.json", twilio["url"])
        self.assertEqual(twilio["form"], {"To": "+5491155550001", "Body": text, "From": "+541100000000"})
        self.assertTrue(twilio["headers"]["Authorization"].startswith("Basic "))
        self.assertTrue(vonage["headers"]["Authorization"].startswith("Basic "))
        self.assertNotIn("api_secret", vonage["form"])
        self.assertEqual(vonage["form"]["to"], "5491155550001")
        self.assertNotIn("type", vonage["form"])
        self.assertIn("/Account/ACC/Message/", plivo["url"])
        self.assertEqual(plivo["payload"], {"src": "+541100000000", "dst": "5491155550001", "text": text})
        self.assertEqual(telnyx["headers"]["Authorization"], "Bearer bk_eu1_SEC")
        self.assertEqual(telnyx["payload"]["to"], "+5491155550001")
        self.assertEqual(infobip["url"], "https://abc.api.infobip.com/sms/3/messages")
        self.assertEqual(infobip["headers"]["Authorization"], "App bk_eu1_SEC")
        self.assertEqual(
            infobip["payload"]["messages"][0],
            {"sender": "+541100000000", "destinations": [{"to": "5491155550001"}], "content": {"text": text}},
        )
        # Bird: la región sale de la clave.
        self.assertEqual(bird["url"], "https://eu1.platform.bird.com/v1/sms/messages")
        self.assertEqual(bird["payload"]["category"], "transactional")
        self.assertEqual(bird["payload"]["to"], "+5491155550001")
        self.assertEqual(clicksend["payload"]["messages"][0]["to"], "+5491155550001")

        # Un rechazo dentro de una respuesta 200 también es un error.
        self.provider = "vonage"
        replies["vonage"] = {"messages": [{"status": "9", "error-text": "Quota exceeded"}]}
        with patch.object(sms, "_request", side_effect=fake_request), self.assertRaises(sms.SmsError):
            sms.send("vonage", cred, "5491155550001", "hola")
        # Los acentos sacan al texto del alfabeto básico (y Vonage necesita saberlo).
        self.assertTrue(sms.is_gsm7("Tu pedido salio a reparto: http://x.y/z?a=1"))
        self.assertFalse(sms.is_gsm7("Tu pedido salió"))
        with self.assertRaises(sms.SmsError):
            sms.send("otro", cred, "5491155550001", "hola")
        with self.assertRaises(sms.SmsError):
            sms.send("twilio", dict(cred, secret=""), "5491155550001", "hola")

    def test_sms_as_whatsapp_fallback(self):
        from odoo.addons.route_optimizer_delivery.services import sms_client as sms
        from odoo.addons.route_optimizer_delivery.services import whatsapp_client as wa

        for key, value in {
            "sms_enabled": "True", "sms_provider": "telnyx", "sms_secret": "KEY", "sms_sender": "+541100000000",
            "sms_mode": "fallback", "sms_text_on_the_way": "{empresa}: {nombre}, tu pedido {pedido} salio. {link}",
            "sms_text_next": "", "whatsapp_enabled": "True", "whatsapp_phone_id": "P", "whatsapp_token": "t",
            "whatsapp_tpl_on_the_way": "reparto_en_camino", "whatsapp_tpl_next": "",
        }.items():
            self.icp.set_param("route_optimizer_delivery." + key, value)
        first = self.batch._route_delivery_stops()[0]
        texts = []

        def fake_sms(provider, credentials, to, text, timeout=10):
            texts.append((provider, to, text))
            return "id"

        def fake_whatsapp(phone_id, token, to, *args, **kwargs):
            # WhatsApp falla solo para el primer cliente.
            if to.endswith("0001"):
                raise wa.WhatsappError("sin WhatsApp")
            return "wamid"

        with patch.object(sms, "send", side_effect=fake_sms), patch.object(wa, "send_template", side_effect=fake_whatsapp):
            self.app().route_delivery_app_start(self.batch.id)
        # Respaldo: el SMS sale solo para quien no recibió el WhatsApp.
        self.assertEqual(len(texts), 1)
        provider, number, text = texts[0]
        self.assertEqual(provider, "telnyx")
        self.assertTrue(number.endswith("0001"))
        self.assertIn(first.partner_id.name, text)
        self.assertIn("/reparto/t/", text)
        self.assertNotIn("{", text)

        # «Siempre»: SMS a todos; si el proveedor falla, queda la nota y la entrega se registra.
        self.icp.set_param("route_optimizer_delivery.sms_mode", "always")
        self.icp.set_param("route_optimizer_delivery.sms_text_delivered", "{empresa}: entregamos {pedido}")
        self.icp.set_param("route_optimizer_delivery.whatsapp_enabled", "False")
        with patch.object(sms, "send", side_effect=sms.SmsError("sin saldo")):
            self.app().route_delivery_app_deliver(first.id, {"lines": self.line_vals(first, 5)})
        self.assertEqual(first.route_delivery_state, "delivered")
        self.assertTrue(first.message_ids.filtered(lambda m: "sin saldo" in (m.body or "")))

    def test_offline_retry_is_idempotent(self):
        """Lo cargado sin señal se manda después: no se duplica y conserva su hora."""
        picking = self.batch._route_delivery_stops()[0]
        made_at = fields.Datetime.subtract(fields.Datetime.now(), minutes=40).replace(microsecond=0)
        vals = {
            "lines": self.line_vals(picking, 5),
            "receiver_name": "Juana",
            "photos": ["data:image/jpeg;base64," + PIXEL],
            "client_id": "abc-123",
            "client_time": fields.Datetime.to_string(made_at),
        }
        self.app().route_delivery_app_deliver(picking.id, vals)
        self.assertEqual(picking.route_delivery_done_at, made_at)
        messages = len(picking.message_ids)
        # El mismo envío llega de nuevo (la respuesta se había perdido).
        route = self.app().route_delivery_app_deliver(picking.id, vals)
        self.assertEqual(route["done_count"], 1)
        self.assertEqual(len(picking.route_delivery_photo_ids), 1)
        self.assertEqual(len(picking.message_ids), messages)
        # Una hora imposible (futura) no se toma.
        other = self.batch._route_delivery_stops()[1]
        future = fields.Datetime.add(fields.Datetime.now(), hours=3)
        self.app().route_delivery_app_fail(
            other.id,
            {"reason_id": self.env.ref("route_optimizer_delivery.fail_reason_absent").id,
             "client_id": "abc-124", "client_time": fields.Datetime.to_string(future)},
        )
        self.assertLessEqual(other.route_delivery_done_at, fields.Datetime.now())

    def test_break_shifts_etas(self):
        """Con descanso configurado, las paradas posteriores a su franja se corren."""
        without = [p.route_delivery_eta for p in self.batch._route_delivery_stops()]
        self.batch._route_delivery_update_etas()
        base = self.batch._route_delivery_stops().mapped("route_delivery_eta")
        # Franja de descanso que empieza antes de la primera llegada: afecta a todas.
        tz_now = fields.Datetime.context_timestamp(self.batch.with_context(tz="UTC"), base[0])
        self.icp.set_param("route_optimizer.break_duration_minutes", "30")
        self.icp.set_param("route_optimizer.break_earliest_hour", "0")
        self.icp.set_param("route_optimizer.break_latest_hour", "23.9")
        self.batch._route_delivery_update_etas()
        shifted = self.batch._route_delivery_stops().mapped("route_delivery_eta")
        self.assertEqual([(b - a).total_seconds() for a, b in zip(base, shifted)], [1800.0] * 3)
        self.assertTrue(without and tz_now)

    def test_suggestions_flag_delivery_windows(self):
        if "delivery_time_preference" not in self.env["res.partner"]._fields:
            self.skipTest("Sin el módulo OCA de ventanas horarias")
        board = self.Batch
        day = fields.Date.to_string(
            fields.Datetime.context_timestamp(board, self.batch.scheduled_date).date()
        )
        near = self._extra_picking("Con horario", -34.63, -58.41)
        options = [o for o in board.route_dispatch_suggest([near.id], day) if o["batch_id"] == self.batch.id]
        self.assertFalse(options[0]["outside_window"])
        # Solo recibe de 3 a 4 de la mañana: cualquier llegada del día queda afuera.
        weekdays = self.env["time.weekday"].search([])
        near.partner_id.write(
            {
                "delivery_time_preference": "time_windows",
                "delivery_time_window_ids": [
                    (0, 0, {"time_window_start": 3.0, "time_window_end": 4.0, "time_window_weekday_ids": [(6, 0, weekdays.ids)]})
                ],
            }
        )
        options = [o for o in board.route_dispatch_suggest([near.id], day) if o["batch_id"] == self.batch.id]
        self.assertTrue(options[0]["outside_window"])
        self.assertIn("recibe 3:00 a 4:00", options[0]["window_note"])

    def test_live_position_refreshes_etas(self):
        """Con la ruta en la calle, el GPS corrige las horas sin esperar a la próxima parada."""
        self.app().route_delivery_app_start(self.batch.id, -34.60, -58.38)
        first = self.batch._route_delivery_stops()[0]
        before = first.route_delivery_eta
        # Recién calculadas: un aviso de posición no recalcula.
        self.app().route_delivery_app_ping(self.batch.id, -34.90, -58.90)
        self.assertEqual(first.route_delivery_eta, before)
        self.assertEqual(self.batch.route_delivery_last_latitude, -34.90)
        # Pasado el intervalo, el chofer lejos de la parada atrasa la hora y se ve el atraso.
        self.batch.route_delivery_eta_refreshed_at = fields.Datetime.subtract(fields.Datetime.now(), minutes=6)
        self.app().route_delivery_app_ping(self.batch.id, -34.90, -58.90)
        self.assertGreater(first.route_delivery_eta, before)
        self.assertGreater(self.batch._route_dispatch_route_data()["delay_minutes"], 30)
        # Con 0 se apaga.
        self.icp.set_param("route_optimizer_delivery.eta_refresh_minutes", "0")
        self.batch.route_delivery_eta_refreshed_at = False
        late = first.route_delivery_eta
        self.app().route_delivery_app_ping(self.batch.id, -34.60, -58.38)
        self.assertEqual(first.route_delivery_eta, late)

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
