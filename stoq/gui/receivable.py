# -*- Mode: Python; coding: iso-8859-1 -*-
# vi:si:et:sw=4:sts=4:ts=4

##
## Copyright (C) 2005-2007 Async Open Source <http://www.async.com.br>
## All rights reserved
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with this program; if not, write to the Free Software
## Foundation, Inc., or visit: http://www.gnu.org/.
##
## Author(s): Stoq Team <stoq-devel@async.com.br>
##
"""
stoq/gui/receivable/receivable.py:

    Implementation of receivable application.
"""

import datetime
import gettext

import pango
import gtk
from kiwi.datatypes import currency
from kiwi.enums import SearchFilterPosition
from kiwi.python import all
from kiwi.ui.search import ComboSearchFilter, DateSearchOption
from kiwi.ui.objectlist import SearchColumn, Column
from stoqlib.database.runtime import new_transaction, finish_transaction
from stoqlib.domain.payment.payment import Payment
from stoqlib.domain.payment.views import InPaymentView
from stoqlib.domain.till import Till
from stoqlib.exceptions import TillError
from stoqlib.reporting.payment import ReceivablePaymentReport
from stoqlib.reporting.receival_receipt import ReceivalReceipt
from stoqlib.gui.printing import print_report
from stoqlib.gui.base.dialogs import run_dialog
from stoqlib.gui.base.gtkadds import render_pixbuf
from stoqlib.gui.dialogs.paymentchangedialog import (PaymentDueDateChangeDialog,
                                                     PaymentStatusChangeDialog)
from stoqlib.gui.dialogs.paymentcommentsdialog import PaymentCommentsDialog
from stoqlib.gui.dialogs.paymentflowhistorydialog import PaymentFlowHistoryDialog
from stoqlib.gui.editors.paymenteditor import InPaymentEditor
from stoqlib.gui.search.paymentsearch import InPaymentBillCheckSearch
from stoqlib.gui.search.paymentsearch import CardPaymentSearch
from stoqlib.gui.slaves.installmentslave import SaleInstallmentConfirmationSlave
from stoqlib.gui.wizards.renegotiationwizard import PaymentRenegotiationWizard
from stoqlib.lib.boleto import BillReport, can_generate_bill
from stoqlib.lib.message import warning

from stoq.gui.application import SearchableAppWindow

_ = gettext.gettext

class NextMonthOption(DateSearchOption):
    name = _('Next month')
    def get_interval(self):
        today = datetime.date.today()
        year = today.year
        month = today.month + 1
        if month > 12:
            month = 1
            year += 1
        # Try 31 first then add one until date() does not complain.
        day = today.day
        while True:
            try:
                end_date = datetime.date(year, month, day)
                break
            except ValueError:
                day += 1
        return datetime.date.today(), end_date

class ReceivableApp(SearchableAppWindow):

    app_name = _('Accounts Receivable')
    app_icon_name = 'stoq-bills'
    gladefile = 'receivable'

    search_table = InPaymentView
    search_label = _('matching:')

    def __init__(self, app):
        SearchableAppWindow.__init__(self, app)
        self._setup_widgets()
        self._update_widgets()
        self.results.connect('has-rows', self._has_rows)

    #
    # Application
    #

    def create_actions(self):
        ui_string = """<ui>
          <menubar action="menubar">
            <menu action="Menu">
              <menuitem action="AddReceiving"/>
              <menuitem action="CancelPayment"/>
              <menuitem action="SetNotPaid"/>
              <menuitem action="ChangeDueDate"/>
              <menuitem action="Comments"/>
              <menuitem action="PrintBill"/>
              <separator name="sep"/>
              <menuitem action="Renegotiate"/>
              <menuitem action="Receipt"/>
              <menuitem action="PaymentFlowHistory"/>
              <separator name="sep2"/>
              <menuitem action="ExportCSV"/>
              <menuitem action="Quit"/>
            </menu>
            <menu action="SearchMenu">
              <menuitem action="BillCheckSearch"/>
              <menuitem action="CardPaymentSearch"/>
            </menu>
          </menubar>
        </ui>"""

        actions = [
            ('menubar', None, ''),

            # Payable
            ('Menu', None, 'Accounts _Receivable'),
            ('AddReceiving', gtk.STOCK_ADD, _('Add receiving...'), '<Control>p'),
            ('CancelPayment', gtk.STOCK_REMOVE, _('Cancel Payment ...')),
            ('SetNotPaid', gtk.STOCK_UNDO, _('Set as not paid...')),
            ('ChangeDueDate', gtk.STOCK_REFRESH, _('Change Due Date...')),
            ('Comments', None, _('Comments...')),
            ('PrintBill', gtk.STOCK_PRINT, _('Print Bill...')),

            ('Renegotiate', None, _('Renegotiate Payments...')),
            ('Receipt', None, _('_Receipt'), '<Control>r'),
            ('PaymentFlowHistory', None, _('Payment _Flow History ...'), '<Control>f'),

            ('ExportCSV', gtk.STOCK_SAVE_AS, _('Export CSV...')),
            ("Quit", gtk.STOCK_QUIT),

            # Search
            ('SearchMenu', None, _('_Search')),
            ('BillCheckSearch', None, _('Bill and Check...')),
            ('CardPaymentSearch', None, _('Card Payment...')),
        ]
        self.add_ui_actions(ui_string, actions)
        self.add_help_ui(_("Accounts Receivable help"), 'receber-inicio')
        self.add_user_ui()

    def create_ui(self):
        self.menubar = self.uimanager.get_widget('/menubar')
        self.main_vbox.pack_start(self.menubar, False, False)
        self.main_vbox.reorder_child(self.menubar, 0)
        self.results.set_selection_mode(gtk.SELECTION_MULTIPLE)

    def _setup_widgets(self):
        self.search.set_summary_label(
            'value', '<b>Total:</b>', '<b>%s</b>')

    def _update_widgets(self):
        selected = self.results.get_selected_rows()
        self.receive_button.set_sensitive(self._can_receive(selected))
        self.details_button.set_sensitive(self._can_show_details(selected))
        self.Comments.set_sensitive(self._can_show_comments(selected))
        self.Renegotiate.set_sensitive(self._can_renegotiate(selected))
        self.ChangeDueDate.set_sensitive(self._can_change_due_date(selected))
        self.CancelPayment.set_sensitive(self._can_cancel_payment(selected))
        self.Receipt.set_sensitive(self._can_emit_receipt(selected))
        self.SetNotPaid.set_sensitive(
            self._can_change_payment_status(selected))
        self.PrintBill.set_sensitive(self._can_print_bill(selected))

    def _has_rows(self, result_list, has_rows):
        self.print_button.set_sensitive(has_rows)

    def _get_status_values(self):
        values = [(v, k) for k, v in Payment.statuses.items()]
        values.insert(0, (_("Any"), None))
        return values

    #
    # SearchableAppWindow hooks
    #

    def create_filters(self):
        self.set_text_field_columns(['description', 'drawee'])
        self.add_filter(
            ComboSearchFilter(_('Show payments with status'),
                              self._get_status_values()),
            SearchFilterPosition.TOP, ['status'])

    def get_columns(self):
        return [SearchColumn('id', title=_('#'), long_title="Payment ID",
                             width=46, data_type=int, sorted=True,
                             format='%04d'),
                Column('color', title=_('Description'), width=20,
                       data_type=gtk.gdk.Pixbuf, format_func=render_pixbuf),
                Column('payment.comments_number', title=_(u'Comments'),
                        visible=False),
                SearchColumn('description', title=_('Description'),
                              data_type=str, expand=True,
                              ellipsize=pango.ELLIPSIZE_END, column='color'),
                SearchColumn('drawee', title=_('Drawee'), data_type=str,
                             expand=True, ellipsize=pango.ELLIPSIZE_END),
                SearchColumn('due_date', title=_('Due Date'),
                             data_type=datetime.date, width=90),
                SearchColumn('paid_date', title=_('Paid Date'),
                             data_type=datetime.date, width=90),
                SearchColumn('status_str', title=_('Status'), width=80,
                             data_type=str, search_attribute='status',
                             valid_values=self._get_status_values()),
                SearchColumn('value', title=_('Value'), data_type=currency,
                             width=80),
                SearchColumn('paid_value', title=_('Paid'),
                             long_title='Paid Value',
                             data_type=currency, width=80)]

    #
    # Private
    #

    def _show_details(self, receivable_view):
        trans = new_transaction()
        payment = trans.get(receivable_view.payment)
        retval = run_dialog(InPaymentEditor, self, trans, payment)
        if finish_transaction(trans, retval):
            self.search.refresh()
        trans.close()
        return retval


    def _show_comments(self, receivable_view):
        trans = new_transaction()
        retval = run_dialog(PaymentCommentsDialog, self, trans,
                            receivable_view.payment)
        finish_transaction(trans, retval)

    def _receive(self, receivable_views):
        """
        Receives a list of items from a receivable_views, note that
        the list of receivable_views must reference the same sale
        @param receivable_views: a list of receivable_views
        """
        assert self._can_receive(receivable_views)

        trans = new_transaction()

        payments = [trans.get(view.payment) for view in receivable_views]

        retval = run_dialog(SaleInstallmentConfirmationSlave, self, trans,
                            payments=payments)

        if finish_transaction(trans, retval):
            for view in receivable_views:
                view.sync()
                self.results.update(view)

        trans.close()
        self._update_widgets()

    def _add_receiving(self):
        trans = new_transaction()
        retval = self.run_dialog(InPaymentEditor, trans)
        if finish_transaction(trans, retval):
            self.search.refresh()

    def _change_due_date(self, receivable_view):
        """ Receives a receivable_view and change the payment due date
        related to the view.
        @param receivable_view: a InPaymentView instance
        """
        assert receivable_view.can_change_due_date()
        trans = new_transaction()
        payment = trans.get(receivable_view.payment)
        sale = trans.get(receivable_view.sale)
        retval = run_dialog(PaymentDueDateChangeDialog, self,
                            trans, payment, sale)

        if finish_transaction(trans, retval):
            receivable_view.sync()
            self.results.update(receivable_view)

        trans.close()

    def _change_status(self, receivable_view, status):
        """Show a dialog do enter a reason for status change
        @param receivable_view: a InPaymentView instance
        """
        trans = new_transaction()
        payment = trans.get(receivable_view.payment)
        order = trans.get(receivable_view.sale)
        retval = run_dialog(PaymentStatusChangeDialog, self, trans,
                            payment, status, order)

        if finish_transaction(trans, retval):
            receivable_view.sync()
            self.results.update(receivable_view)
            self.results.unselect_all()

        trans.close()

    def _can_emit_receipt(self, receivable_views):
        """
        Determines if we can emit the receipt for a list of
        receivable views.
        To do so they must meet the following conditions:
          - Be in the same sale
          - The payment status needs to be set to PAID
        """
        if not receivable_views:
            return False

        sale = receivable_views[0].sale
        if sale is None:
            return False
        return all(view.sale == sale and view.payment.is_paid()
                   for view in receivable_views)

    def _can_receive(self, receivable_views):
        """
        Determines if a list of receivable_views can be received.
        To do so they must meet the following conditions:
          - Be in the same sale
          - The payment status needs to be set to PENDING
        """

        if not receivable_views:
            return False

        # Until we fix bug 3703, don't allow receiving store credit payments
        if any(view.method_name == 'store_credit'
               for view in receivable_views):
            return False

        if len(receivable_views) == 1:
            return receivable_views[0].status == Payment.STATUS_PENDING

        sale = receivable_views[0].sale
        if sale is None:
            return False
        return all(view.sale == sale and
                   view.status == Payment.STATUS_PENDING
                   for view in receivable_views)

    def _can_renegotiate(self, receivable_views):
        """whether or not we can renegotiate this payments"""
        if not len(receivable_views):
            return False

        # Parent is a Sale or a PaymentRenegotiation
        parent = receivable_views[0].get_parent()

        if not parent:
            return False

        client = parent.client

        if not client:
            return False

        return all(view.get_parent() and
                   view.get_parent().client is client and
                   view.get_parent().can_set_renegotiated()
                   for view in receivable_views)

    def _can_change_payment_status(self, receivable_views):
        """whether or not we can change the paid status
        """
        if len(receivable_views) != 1:
            return False

        return receivable_views[0].can_change_payment_status()

    def _can_cancel_payment(self, receivable_views):
        """whether or not we can cancel the receiving.
        """
        if len(receivable_views) != 1:
            return False

        return receivable_views[0].can_cancel_payment()

    def _can_change_due_date(self, receivable_views):
        """
        Determines if a list of receivable_views can have it's due date
        changed. To do so they must meet the following conditions:
          - The list  must have only one element
          - The payment was not paid
        """
        if len(receivable_views) != 1:
            return False

        return receivable_views[0].can_change_due_date()

    def _can_show_details(self, receivable_views):
        """Determines if we can show the receiving details for a list of
        receivable views.
        To do so they must meet the following conditions:
          - Be in the same sale or
          - One receiving that not belong to any sale
        """
        if not receivable_views:
            return False

        sale = receivable_views[0].sale
        if sale is None:
            if len(receivable_views) == 1:
                return True
            else:
                return False

        return all(view.sale == sale for view in receivable_views[1:])

    def _can_show_comments(self, receivable_views):
        return len(receivable_views) == 1

    def _can_print_bill(self, receivable_views):
        return (len(receivable_views) == 1 and
                receivable_views[0].method_name == 'bill' and
                receivable_views[0].status == Payment.STATUS_PENDING)

    def _run_card_payment_search(self):
        run_dialog(CardPaymentSearch, self, self.conn)

    def _run_bill_check_search(self):
        run_dialog(InPaymentBillCheckSearch, self, self.conn)

    #
    # Kiwi callbacks
    #

    def on_results__row_activated(self, klist, receivable_view):
        self._show_details(receivable_view)

    def on_results__selection_changed(self, receivables, selected):
        self._update_widgets()

    def on_details_button__clicked(self, button):
        selected = self.results.get_selected_rows()[0]
        self._show_details(selected)

    def on_receive_button__clicked(self, button):
        self._receive(self.results.get_selected_rows())

    def on_print_button__clicked(self, button):
        self.print_report(ReceivablePaymentReport, self.results)

    def on_Comments__activate(self, action):
        receivable_view = self.results.get_selected_rows()[0]
        self._show_comments(receivable_view)

    def on_Receipt__activate(self, action):
        receivable_views = self.results.get_selected_rows()
        payments = [v.payment for v in receivable_views]
        print_report(ReceivalReceipt, payments=payments,
                     sale=receivable_views[0].sale)

    def on_PaymentFlowHistory__activate(self, action):
        self.run_dialog(PaymentFlowHistoryDialog, self.conn)

    def on_AddReceiving__activate(self, action):
        self._add_receiving()

    def on_CancelPayment__activate(self, action):
        receivable_view = self.results.get_selected_rows()[0]
        self._change_status(receivable_view, Payment.STATUS_CANCELLED)

    def on_SetNotPaid__activate(self, action):
        receivable_view = self.results.get_selected_rows()[0]
        self._change_status(receivable_view, Payment.STATUS_PENDING)

    def on_ChangeDueDate__activate(self, action):
        receivable_view = self.results.get_selected_rows()[0]
        self._change_due_date(receivable_view)

    def on_BillCheckSearch__activate(self, action):
        self._run_bill_check_search()

    def on_CardPaymentSearch__activate(self, action):
        self._run_card_payment_search()

    def on_Renegotiate__activate(self, action):
        try:
            Till.get_current(self.conn)
        except TillError, e:
            warning(str(e))
            return
        receivable_views = self.results.get_selected_rows()
        trans = new_transaction()
        groups = list(set([trans.get(v.group) for v in receivable_views]))
        retval = run_dialog(PaymentRenegotiationWizard, self, trans,
                            groups)
        if finish_transaction(trans, retval):
            self.search.refresh()

    def on_PrintBill__activate(self, action):
        if not can_generate_bill():
            return

        item = self.results.get_selected_rows()[0]
        payment = item.payment
        print_report(BillReport, [payment])