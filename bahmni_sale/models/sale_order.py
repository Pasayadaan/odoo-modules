# -*- coding: utf-8 -*-
from datetime import datetime

from odoo import fields, models, api, _
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DSDF
from odoo.tools import float_is_zero
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    @api.depends('order_line.price_total', 'discount', 'chargeable_amount')
    def _amount_all(self):
        """
        Compute the total amounts of the SO.
        """
        for order in self:
            amount_untaxed = amount_tax = 0.0
            for line in order.order_line:
                amount_untaxed += line.price_subtotal
                # FORWARDPORT UP TO 10.0
                if order.company_id.tax_calculation_rounding_method == 'round_globally':
                    price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
                    taxes = line.tax_id.compute_all(price, line.order_id.currency_id, line.product_uom_qty, product=line.product_id, partner=order.partner_shipping_id)
                    amount_tax += sum(t.get('amount', 0.0) for t in taxes.get('taxes', []))
                else:
                    amount_tax += line.price_tax
            amount_total = amount_untaxed + amount_tax
            if self.chargeable_amount > 0.0:
                discount = amount_total - self.chargeable_amount
            else:
                discount = self.discount
            amount_total = amount_total - discount
            round_off_amount = self.env['rounding.off'].round_off_value_to_nearest(amount_total)
            order.update({
                'amount_untaxed': order.pricelist_id.currency_id.round(amount_untaxed),
                'amount_tax': order.pricelist_id.currency_id.round(amount_tax),
                'amount_total': amount_total + round_off_amount,
                'round_off_amount': round_off_amount,
                'total_outstanding_balance': order.prev_outstanding_balance + amount_total + round_off_amount
            })

    @api.depends('partner_id')
    def _calculate_balance(self):
        for order in self:
            order.prev_outstanding_balance = 0.0
            order.total_outstanding_balance = 0.0
#             res[order.id] = {'prev_amount_outstanding':0.0,'total_outstanding':0.0}
            total_receivable = order._total_receivable()
            order.prev_outstanding_balance = total_receivable
#             order.total_outstanding_balance = order.amount_total + total_receivable
#             res[order.id]['prev_amount_outstanding'] = total_receivable
#             res[order.id]['total_outstanding'] = total_receivable + order.amount_total

    def _total_receivable(self):
        receivable = 0.0
        if self.partner_id:
            self._cr.execute("""SELECT l.partner_id, at.type, SUM(l.debit-l.credit)
                          FROM account_move_line l
                          LEFT JOIN account_account a ON (l.account_id=a.id)
                          LEFT JOIN account_account_type at ON (a.user_type_id=at.id)
                          WHERE at.type IN ('receivable','payable')
                          AND l.partner_id = %s
                          AND l.full_reconcile_id IS NULL
                          GROUP BY l.partner_id, at.type
                          """, (self.partner_id.id,))
            for pid, type, val in self._cr.fetchall():
                if val is None:
                    val=0
                receivable = (type == 'receivable') and val or -val
        return receivable

    external_id = fields.Char(string="External Id",
                              help="This field is used to store encounter ID of bahmni api call")
    dispensed = fields.Boolean(string="Dispensed",
                               help="Flag to identify whether drug order is dispensed or not.")
    partner_village = fields.Many2one("village.village", string="Partner Village")
    care_setting = fields.Selection([('ipd', 'IPD'),
                                     ('opd', 'OPD')], string="Care Setting")
    provider_name = fields.Many2one('res.partner', string="Provider Name")
    discount_percentage = fields.Float(string="Discount Percentage")
    default_quantity = fields.Integer(string="Default Quantity")
    # above field is used to allow setting quantity as -1 in sale order line, when it is created through bahmni
    discount_type = fields.Selection([('none', 'No Discount'),
                                      ('fixed', 'Fixed'),
                                      ('percentage', 'Percentage')], string="Discount Type",
                                     default='none')
    discount = fields.Monetary(string="Discount")
    disc_acc_id = fields.Many2one('account.account', string="Discount Account Head")
    round_off_amount = fields.Float(string="Round Off Amount", compute=_amount_all)
    prev_outstanding_balance = fields.Monetary(string="Previous Outstanding Balance",
                                               compute=_calculate_balance)
    total_outstanding_balance = fields.Monetary(string="Total Outstanding Balance",
                                                compute=_amount_all)
    chargeable_amount = fields.Float(string="Chargeable Amount")
    amount_round_off = fields.Float(string="Round Off Amount")

    @api.onchange('discount_percentage', 'order_line')
    def onchange_discount_percentage(self):
        '''Calculate discount amount, when discount is entered in terms of %'''
        print "onchange_discount_percentage :::::::::", self.discount_percentage
        amount_total = self.amount_untaxed + self.amount_tax
        if self.discount_type == 'percentage':
            self.discount = (amount_total * self.discount_percentage) / 100
            print "self.discount >>>>>>>>>>>>>>>>>", self.discount

    @api.onchange('discount_type')
    def onchange_discount_type(self):
        '''Method to set values of fields to zero, when
        those are  not considerable in calculation'''
        print "onchange_discount_type :::::::::::::::::", self.discount_type
        if self.chargeable_amount and self.chargeable_amount <= self.amount_total:
            if self.discount_type == 'fixed':
                print ">>>>>>>>>>>>>>>>>>>>", (self.discount / self.amount_total ) * 100
#                 self.discount = self.amount_untaxed + self.amount_tax - self.chargeable_amount
                self.discount_percentage = (self.discount / self.amount_total) * 100
            elif self.discount_type == 'percentage':
                discount = self.amount_total + self.amount_tax - self.chargeable_amount
                self.discount_percentage = (discount / self.amount_total) * 100

    @api.onchange('chargeable_amount')
    def onchange_chargeable_amount(self):
        # when chargeable amount is set less than total_amount, remaining amount is converted as discount
        if self.chargeable_amount > 0.0:
            if self.discount_type == 'none' and self.chargeable_amount:
                self.discount_type = 'fixed'
            elif self.discount_type == 'fixed':
                self.discount = self.amount_untaxed + self.amount_tax - self.chargeable_amount
                print "sself.discount:::::::onchange_chargeable_amount>>>>>>>>>>>>>>", self.discount 
                self.discount_percentage = (self.discount / self.amount_total) * 100
            elif self.discount_type == 'percentage':
                discount = self.amount_untaxed + self.amount_tax - self.chargeable_amount
                self.discount_percentage = (discount / self.amount_total) * 100

    @api.multi
    def _prepare_invoice(self):
        """
        Prepare the dict of values to create the new invoice for a sales order. This method may be
        overridden to implement custom invoice generation (making sure to call super() to establish
        a clean extension chain).
        """
        self.ensure_one()
        journal_id = self.env['account.invoice'].default_get(['journal_id'])['journal_id']
        if not journal_id:
            raise UserError(_('Please define an accounting sale journal for this company.'))
        invoice_vals = {
            'name': self.client_order_ref or '',
            'origin': self.name,
            'type': 'out_invoice',
            'account_id': self.partner_invoice_id.property_account_receivable_id.id,
            'partner_id': self.partner_invoice_id.id,
            'partner_shipping_id': self.partner_shipping_id.id,
            'journal_id': journal_id,
            'currency_id': self.pricelist_id.currency_id.id,
            'comment': self.note,
            'payment_term_id': self.payment_term_id.id,
            'fiscal_position_id': self.fiscal_position_id.id or self.partner_invoice_id.property_account_position_id.id,
            'company_id': self.company_id.id,
            'user_id': self.user_id and self.user_id.id,
            'team_id': self.team_id.id,
            'discount_type': self.discount_type,
            'discount_percentage': self.discount_percentage,
            'disc_acc_id': self.disc_acc_id.id
        }
        return invoice_vals