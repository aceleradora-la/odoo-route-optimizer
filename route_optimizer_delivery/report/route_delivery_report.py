# -*- coding: utf-8 -*-
from odoo import fields, models, tools

# Una entrega cuenta como «a tiempo» si llegó hasta estos minutos después de la hora
# que tenía cuando salió la ruta.
ON_TIME_TOLERANCE_MINUTES = 15


class RouteDeliveryReport(models.Model):
    """Una fila por parada de reparto: qué pasó, cuándo y con cuánto atraso."""

    _name = "route.delivery.report"
    _description = "Análisis de entregas"
    _auto = False
    _rec_name = "picking_id"
    _order = "date desc, id desc"

    picking_id = fields.Many2one("stock.picking", string="Traslado", readonly=True)
    batch_id = fields.Many2one("stock.picking.batch", string="Ruta", readonly=True)
    date = fields.Date(string="Día", readonly=True)
    driver_id = fields.Many2one("res.users", string="Chofer", readonly=True)
    partner_id = fields.Many2one("res.partner", string="Cliente", readonly=True)
    company_id = fields.Many2one("res.company", string="Compañía", readonly=True)
    state = fields.Selection(
        [
            ("pending", "Pendiente"),
            ("delivered", "Entregado"),
            ("partial", "Entrega parcial"),
            ("failed", "No entregado"),
        ],
        string="Resultado",
        readonly=True,
    )
    fail_reason_id = fields.Many2one(
        "route.delivery.fail.reason", string="Motivo de no entrega", readonly=True
    )
    stop_count = fields.Integer(string="Paradas", readonly=True)
    delivered_count = fields.Integer(string="Entregadas", readonly=True)
    partial_count = fields.Integer(string="Parciales", readonly=True)
    failed_count = fields.Integer(string="No entregadas", readonly=True)
    on_time_count = fields.Integer(string="A tiempo", readonly=True)
    late_count = fields.Integer(string="Tarde", readonly=True)
    delay_minutes = fields.Float(
        string="Atraso (min)",
        readonly=True,
        group_operator="avg",
        help="Minutos entre la hora prevista al salir la ruta y la hora real de la visita. "
        "Negativo = llegó antes.",
    )
    cod_amount = fields.Float(string="Cobrado", readonly=True)

    def init(self):
        # Vista SQL recreada en cada actualización del módulo. Se usa init() y no
        # _table_query porque es lo que funciona igual desde Odoo 17 hasta 20.
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("CREATE VIEW %s AS (%s)" % (self._table, self._view_query()))

    def _view_query(self):
        return """
            SELECT
                p.id AS id,
                p.id AS picking_id,
                p.batch_id AS batch_id,
                COALESCE(p.route_delivery_done_at, p.route_delivery_eta, p.scheduled_date)::date AS date,
                b.user_id AS driver_id,
                p.partner_id AS partner_id,
                p.company_id AS company_id,
                COALESCE(p.route_delivery_state, 'pending') AS state,
                p.route_delivery_fail_reason_id AS fail_reason_id,
                1 AS stop_count,
                CASE WHEN p.route_delivery_state IN ('delivered', 'partial') THEN 1 ELSE 0 END AS delivered_count,
                CASE WHEN p.route_delivery_state = 'partial' THEN 1 ELSE 0 END AS partial_count,
                CASE WHEN p.route_delivery_state = 'failed' THEN 1 ELSE 0 END AS failed_count,
                CASE WHEN p.route_delivery_state IN ('delivered', 'partial')
                          AND p.route_delivery_planned_eta IS NOT NULL
                          AND p.route_delivery_done_at <= p.route_delivery_planned_eta + interval '%(tol)s minutes'
                     THEN 1 ELSE 0 END AS on_time_count,
                CASE WHEN p.route_delivery_state IN ('delivered', 'partial')
                          AND p.route_delivery_planned_eta IS NOT NULL
                          AND p.route_delivery_done_at > p.route_delivery_planned_eta + interval '%(tol)s minutes'
                     THEN 1 ELSE 0 END AS late_count,
                CASE WHEN p.route_delivery_done_at IS NOT NULL AND p.route_delivery_planned_eta IS NOT NULL
                     THEN EXTRACT(EPOCH FROM (p.route_delivery_done_at - p.route_delivery_planned_eta)) / 60.0
                END AS delay_minutes,
                COALESCE(p.route_delivery_cod_amount, 0) AS cod_amount
            FROM stock_picking p
            JOIN stock_picking_batch b ON b.id = p.batch_id
            WHERE p.state != 'cancel'
        """ % {"tol": ON_TIME_TOLERANCE_MINUTES}


class RouteDeliveryRouteReport(models.Model):
    """Una fila por ruta: lo planificado al salir contra lo que pasó en la calle."""

    _name = "route.delivery.route.report"
    _description = "Análisis de rutas"
    _auto = False
    _rec_name = "batch_id"
    _order = "date desc, id desc"

    batch_id = fields.Many2one("stock.picking.batch", string="Ruta", readonly=True)
    date = fields.Date(string="Día", readonly=True)
    driver_id = fields.Many2one("res.users", string="Chofer", readonly=True)
    company_id = fields.Many2one("res.company", string="Compañía", readonly=True)
    route_count = fields.Integer(string="Rutas", readonly=True)
    stop_count = fields.Integer(string="Paradas", readonly=True)
    delivered_count = fields.Integer(string="Entregadas", readonly=True)
    failed_count = fields.Integer(string="No entregadas", readonly=True)
    planned_km = fields.Float(string="Km planificados", readonly=True)
    driven_km = fields.Float(
        string="Km recorridos (GPS)",
        readonly=True,
        help="Suma de los tramos entre las posiciones que informó la app. Si la app estuvo "
        "cerrada parte del recorrido, queda por debajo del real.",
    )
    planned_minutes = fields.Float(string="Minutos planificados", readonly=True)
    actual_minutes = fields.Float(string="Minutos reales", readonly=True)

    def init(self):
        # Vista SQL recreada en cada actualización del módulo. Se usa init() y no
        # _table_query porque es lo que funciona igual desde Odoo 17 hasta 20.
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("CREATE VIEW %s AS (%s)" % (self._table, self._view_query()))

    def _view_query(self):
        return """
            SELECT
                b.id AS id,
                b.id AS batch_id,
                COALESCE(b.route_delivery_started_at, b.scheduled_date)::date AS date,
                b.user_id AS driver_id,
                b.company_id AS company_id,
                1 AS route_count,
                COUNT(p.id) AS stop_count,
                COUNT(p.id) FILTER (WHERE p.route_delivery_state IN ('delivered', 'partial')) AS delivered_count,
                COUNT(p.id) FILTER (WHERE p.route_delivery_state = 'failed') AS failed_count,
                COALESCE(b.route_delivery_planned_km, 0) AS planned_km,
                COALESCE(b.route_delivery_driven_km, 0) AS driven_km,
                COALESCE(b.route_delivery_planned_minutes, 0) AS planned_minutes,
                CASE WHEN b.route_delivery_started_at IS NOT NULL AND b.route_delivery_finished_at IS NOT NULL
                     THEN EXTRACT(EPOCH FROM (b.route_delivery_finished_at - b.route_delivery_started_at)) / 60.0
                     ELSE 0 END AS actual_minutes
            FROM stock_picking_batch b
            LEFT JOIN stock_picking p ON p.batch_id = b.id AND p.state != 'cancel'
            WHERE b.state != 'cancel' AND b.route_delivery_started_at IS NOT NULL
            GROUP BY b.id
        """
