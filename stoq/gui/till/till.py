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
""" Implementation of till application.  """

import gettext
import decimal
from datetime import date

import pango
import gtk
from kiwi.datatypes import currency, converter
from kiwi.log import Logger
from kiwi.enums import SearchFilterPosition
from kiwi.ui.search import ComboSearchFilter
from kiwi.ui.objectlist import Column, SearchColumn

from stoqlib.exceptions import StoqlibError, TillError, SellError
from stoqlib.database.orm import AND, OR, const
from stoqlib.database.runtime import (new_transaction, get_current_branch,
                                      rollback_and_begin, finish_transaction)
from stoqlib.domain.interfaces import IStorable
from stoqlib.domain.sale import Sale, SaleView
from stoqlib.domain.till import Till
from stoqlib.lib.formatters import format_quantity
from stoqlib.lib.message import yesno, warning
from stoqlib.gui.base.dialogs import run_dialog
from stoqlib.gui.dialogs.tillhistory import TillHistoryDialog
from stoqlib.gui.dialogs.saledetails import SaleDetailsDialog
from stoqlib.gui.editors.tilleditor import CashInEditor, CashOutEditor
from stoqlib.gui.help import show_contents, show_section
from stoqlib.gui.search.personsearch import ClientSearch
from stoqlib.gui.search.salesearch import SaleSearch, SoldItemsByBranchSearch
from stoqlib.gui.search.tillsearch import TillFiscalOperationsSearch
from stoqlib.gui.slaves.saleslave import return_sale

from stoq.gui.application import SearchableAppWindow

_ = gettext.gettext
log = Logger('stoq.till')


class TillApp(SearchableAppWindow):

    app_name = _(u'Till')
    app_icon_name = 'stoq-till-app'
    gladefile = 'till'
    search_table = SaleView
    search_labels = _(u'matching:')

    def __init__(self, app):
        SearchableAppWindow.__init__(self, app)
        self.current_branch = get_current_branch(self.conn)
        self.check_till()
        self._setup_widgets()
        self.refresh()

    #
    # SearchableAppWindow hooks
    #

    def setup_focus(self):
        # Groups
        self.main_vbox.set_focus_chain([self.app_vbox])
        self.app_vbox.set_focus_chain([self.search_holder, self.list_vbox])

        # Setting up the toolbar
        self.list_vbox.set_focus_chain([self.footer_hbox])
        self.footer_hbox.set_focus_chain([self.confirm_order_button,
                                          self.return_button,
                                          self.details_button])

    def create_filters(self):
        self.executer.set_query(self._query_executer)
        self.set_text_field_columns(['client_name', 'salesperson_name'])
        status_filter = ComboSearchFilter(_(u"Show orders with status"),
                                          self._get_status_values())
        status_filter.select(Sale.STATUS_CONFIRMED)
        self.add_filter(status_filter, position=SearchFilterPosition.TOP,
                        columns=['status'])

    def _query_executer(self, query, having, conn):
        # We should only show Sales that
        # 1) In the current branch (FIXME: Should be on the same station.
                                    # See bug 4266)
        # 2) Are in the status QUOTE or ORDERED.
        # 3) For the order statuses, the date should be the same as today

        new = AND(Sale.q.branchID == self.current_branch.id,
                 OR(Sale.q.status == Sale.STATUS_QUOTE,
                    Sale.q.status == Sale.STATUS_ORDERED,
                    const.DATE(Sale.q.open_date) == date.today()))

        if query:
            query = AND(query, new)
        else:
            query = new

        return self.search_table.select(query, having=having, connection=conn)

    def get_title(self):
        return _('Stoq - Till for Branch %03d') % get_current_branch(self.conn).id

    def get_columns(self):
        return [SearchColumn('id', title=_('Number'), width=80,
                             data_type=int, format='%05d', sorted=True),
                Column('status_name', title=_(u'Status'), data_type=str,
                        visible=False),
                SearchColumn('open_date', title=_('Date Started'), width=120,
                             data_type=date, justify=gtk.JUSTIFY_RIGHT),
                SearchColumn('client_name', title=_('Client'),
                             data_type=str, width=160, expand=True,
                             ellipsize=pango.ELLIPSIZE_END),
                SearchColumn('salesperson_name', title=_('Salesperson'),
                             data_type=str, width=160,
                             ellipsize=pango.ELLIPSIZE_END),
                SearchColumn('total_quantity', title=_('Quantity'),
                             data_type=decimal.Decimal, width=100,
                             format_func=format_quantity),
                SearchColumn('total', title=_('Total'), data_type=currency,
                             width=120)]

    #
    # Private
    #

    def _get_status_values(self):
        statuses = [(v, k) for k, v in Sale.statuses.items()]
        statuses.insert(0, (_('Any'), None))
        return statuses

    def _confirm_order(self):
        rollback_and_begin(self.conn)
        selected = self.results.get_selected()
        sale = Sale.get(selected.id, connection=self.conn)
        expire_date = sale.expire_date

        if (sale.status == Sale.STATUS_QUOTE and
            expire_date and expire_date.date() < date.today() and
            not yesno(_(u"This quote has expired. Confirm it anyway?"),
                      gtk.RESPONSE_YES,
                      _(u"Confirm Quote"), _(u"Don't Confirm"))):
            return

        # Lets confirm that we can create the sale, before opening the coupon
        prod_sold = dict()
        prod_desc = dict()
        for sale_item in sale.get_items():
            # Skip services, since we don't need stock to sell.
            if sale_item.is_service():
                continue
            storable = IStorable(sale_item.sellable.product, None)
            prod_sold.setdefault(storable, 0)
            prod_sold[storable] += sale_item.quantity
            prod_desc[storable] = sale_item.sellable.get_description()

        branch = self.current_branch
        for storable in prod_sold.keys():
            stock = storable.get_full_balance(branch)
            if stock < prod_sold[storable]:
                warning(_(u'There is only %d items of "%s" and this sale '
                           'has %d items.') % (stock,
                                    prod_desc[storable], prod_sold[storable]))
                return

        coupon = self._open_coupon()
        if not coupon:
            return
        self._add_sale_items(sale, coupon)
        try:
            if coupon.confirm(sale, self.conn):
                self.conn.commit()
                self.refresh()
        except SellError as err:
            warning(err)

    def _open_coupon(self):
        coupon = self._printer.create_coupon()

        if coupon:
            while not coupon.open():
                if not yesno(
                    _(u"It is not possible to confirm the sale if the "
                       "fiscal coupon cannot be opened."),
                    gtk.RESPONSE_YES, _(u"Try Again"), _(u"Cancel")):
                    break

        return coupon

    def _add_sale_items(self, sale, coupon):
        for sale_item in sale.get_items():
            coupon.add_item(sale_item)

    def _summary(self):
        self._printer.summarize()

    def _update_total(self):
        balance = currency(self._get_till_balance())
        text = _(u"Total: %s") % converter.as_string(currency, balance)
        self.total_label.set_text(text)

    def _get_till_balance(self):
        """Returns the balance of till operations"""
        try:
            till = Till.get_current(self.conn)
        except TillError:
            till = None

        if till is None:
            return currency(0)

        return till.get_balance()

    def _setup_widgets(self):
        self.total_label.set_size('xx-large')
        self.total_label.set_bold(True)
        self.till_status_label.set_size('large')
        self.till_status_label.set_bold(True)

    def _update_toolbar_buttons(self):
        sale_view = self.results.get_selected()
        if sale_view:
            can_confirm = sale_view.sale.can_confirm()
            # when confirming sales in till, we also might want to cancel
            # sales
            can_return = (sale_view.sale.can_return() or
                          sale_view.sale.can_cancel())
        else:
            can_confirm = can_return = False

        self.details_button.set_sensitive(bool(sale_view))
        self.confirm_order_button.set_sensitive(can_confirm)
        self.return_button.set_sensitive(can_return)

    def till_status_changed(self, closed, blocked=False):
        # Three different situations;
        #
        # - Till is closed
        # - Till is opened
        # - Till was not closed the previous fiscal day (blocked)

        self.TillOpen.set_sensitive(closed)
        self.TillClose.set_sensitive(not closed or blocked)
        self.AddCash.set_sensitive(not closed and not blocked)
        self.RemoveCash.set_sensitive(not closed and not blocked)
        self.TillHistory.set_sensitive(not closed and not blocked)
        self.app_vbox.set_sensitive(not closed and not blocked)

        if closed:
            text = _(u"Till closed")
            self.clear()
            self.setup_focus()
        elif blocked:
            text = _(u"Till blocked from previous day")
        else:
            till = Till.get_current(self.conn)
            text = _(u"Till opened on %s") % till.opening_date.strftime('%x')

        self.till_status_label.set_text(text)

        self._update_toolbar_buttons()
        self._update_total()

    def _check_selected(self):
        sale_view = self.results.get_selected()
        if not sale_view:
            raise StoqlibError("You should have a selected item at "
                               "this point")
        return sale_view

    def _run_search_dialog(self, dialog_type, **kwargs):
        trans = new_transaction()
        self.run_dialog(dialog_type, trans, **kwargs)
        trans.close()

    def _run_details_dialog(self):
        sale_view = self._check_selected()
        run_dialog(SaleDetailsDialog, self, self.conn, sale_view)

    def _return_sale(self):
        sale_view = self._check_selected()
        retval = return_sale(self.get_toplevel(), sale_view, self.conn)
        if finish_transaction(self.conn, retval):
            self._update_total()

    #
    # Actions
    #

    def _on_close_till_action__clicked(self, button):
        self.close_till()

    def _on_open_till_action__clicked(self, button):
        self.open_till()

    def _on_client_search_action__clicked(self, button):
        self._run_search_dialog(ClientSearch, hide_footer=True)

    def _on_sale_search_action__clicked(self, button):
        self._run_search_dialog(SaleSearch)

    def on_SoldItemsByBranchSearch__activate(self, button):
        self._run_search_dialog(SoldItemsByBranchSearch)

    def _on_fiscal_till_operations__action_clicked(self, button):
        self._run_search_dialog(TillFiscalOperationsSearch)

    def on_TillHistory__activate(self, button):
        dialog = TillHistoryDialog(self.conn)
        self.run_dialog(dialog, self.conn)

    def on_AddCash__activate(self, action):
        model = run_dialog(CashInEditor, self, self.conn)
        if finish_transaction(self.conn, model):
            self._update_total()

    def on_RemoveCash__activate(self, action):
        model = run_dialog(CashOutEditor, self, self.conn)
        if finish_transaction(self.conn, model):
            self._update_total()

    def on_help_contents__activate(self, action):
        show_contents()

    def on_help_till__activate(self, action):
        show_section('caixa-inicio')

    #
    # Callbacks
    #

#     def on_searchbar_activate(self, slave, objs):
#         SearchableAppWindow.on_searchbar_activate(self, slave, objs)
#         self._update_toolbar_buttons()
#         self._update_total()

    #
    # Kiwi callbacks
    #

    def on_confirm_order_button__clicked(self, button):
        self._confirm_order()
        self._update_total()

    def on_results__double_click(self, results, sale):
        self._run_details_dialog()

    def on_results__selection_changed(self, results, sale):
        self._update_toolbar_buttons()

    def on_results__has_rows(self, results, has_rows):
        self._update_total()

    def on_details_button__clicked(self, button):
        self._run_details_dialog()

    def on_return_button__clicked(self, button):
        self._return_sale()