# -*- Mode: Python; coding: iso-8859-1 -*-
# vi:si:et:sw=4:sts=4:ts=4

##
## Copyright (C) 2011 Async Open Source <http://www.async.com.br>
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
stoq/gui/financial/financial.py:

    Implementation of financial application.
"""

import datetime
import decimal
import gettext

import gobject
import gtk
from kiwi.python import Settable
from kiwi.ui.dialogs import open as open_dialog
from kiwi.ui.objectlist import ColoredColumn, Column, ObjectList
from stoqlib.database.runtime import new_transaction, finish_transaction
from stoqlib.domain.account import Account, AccountTransactionView
from stoqlib.domain.payment.views import InPaymentView, OutPaymentView
from stoqlib.gui.accounttree import AccountTree
from stoqlib.gui.base.dialogs import run_dialog
from stoqlib.gui.editors.accounteditor import AccountEditor
from stoqlib.gui.editors.accounttransactioneditor import AccountTransactionEditor
from stoqlib.gui.dialogs.importerdialog import ImporterDialog
from stoqlib.lib.message import yesno
from stoqlib.lib.parameters import sysparam
from stoq.gui.application import AppWindow
from kiwi.currency import currency

_ = gettext.gettext

class NotebookCloseButton(gtk.Button):
    pass
gobject.type_register(NotebookCloseButton)

class TransactionPage(ObjectList):
    # shows either a list of:
    #   - transactions
    #   - payments
    def __init__(self, model, app, parent):
        self.model = model
        self.app = app
        self.parent_window = parent
        self._block = False
        ObjectList.__init__(self, columns=self._get_columns(model.kind))
        self.connect('row-activated', self._on_row__activated)
        tree_view = self.get_treeview()
        tree_view.set_rules_hint(True)
        tree_view.set_grid_lines(gtk.TREE_VIEW_GRID_LINES_BOTH)
        self.refresh()

    def refresh(self):
        self.clear()
        if self.model.kind == 'account':
            self._populate_transactions()
        elif self.model.kind == 'payable':
            self._populate_payable_payments(OutPaymentView)
        elif self.model.kind == 'receivable':
            self._populate_payable_payments(InPaymentView)
        else:
            raise TypeError("unknown model kind: %r" % (self.model.kind, ))

    def _get_columns(self, kind):
        if kind in ['payable', 'receivable']:
            return self._get_payment_columns()
        else:
            return self._get_account_columns()

    def _get_account_columns(self):
        def format_withdrawal(value):
            if value < 0:
                return '%.2f'% (abs(value), )

        def format_deposit(value):
            if value > 0:
                return '%.2f' % (value, )

        if self.model.account_type == Account.TYPE_INCOME:
            color_func = lambda x: False
        else:
            color_func = lambda x: x < 0
        return [Column('date', data_type=datetime.date, sorted=True),
                Column('code', data_type=unicode),
                Column('description', data_type=unicode, expand=True),
                Column('account', data_type=unicode,
                       justify=gtk.JUSTIFY_RIGHT),
                Column('value',
                       title=self.model.account.get_type_label(out=False),
                       data_type=currency,
                       format_func=format_deposit),
                Column('value',
                       title=self.model.account.get_type_label(out=True),
                       data_type=currency,
                       format_func=format_withdrawal),
                ColoredColumn('total', data_type=currency,
                              color='red',
                              data_func=color_func)]

    def _get_payment_columns(self):
        return [Column('paid_date', data_type=datetime.date, sorted=True),
                Column('id', title=_("Code"), data_type=unicode),
                Column('description', data_type=unicode, expand=True),
                Column('value',
                       data_type=currency)]

    def _populate_transactions(self):
        for transaction in AccountTransactionView.get_for_account(
            self.model, self.app.conn):
            description = transaction.get_account_description(self.model)
            value = transaction.get_value(self.model)
            self._add_transaction(transaction, description, value)
        self._update_totals()

    def _populate_payable_payments(self, view_class):
        for view in view_class.select():
            view.kind = self.model.kind
            self.append(view)

    def _add_transaction(self, transaction, description, value):
        item = Settable(transaction=transaction, kind=self.model.kind)
        self._update_transaction(item, transaction, description, value)
        self.append(item)
        return item

    def _update_transaction(self, item, transaction, description, value):
        item.account = description
        item.date = transaction.date
        item.description = transaction.description
        item.value = value
        item.code = transaction.code

    def _update_totals(self):
        total = decimal.Decimal('0')
        for item in self:
            total += item.value
            item.total = total

    def _edit_transaction_dialog(self, item):
        trans = new_transaction()
        if isinstance(item.transaction, AccountTransactionView):
            account_transaction = trans.get(item.transaction.transaction)
        else:
            account_transaction = trans.get(item.transaction)
        model = getattr(self.model, 'account', self.model)

        transaction = run_dialog(AccountTransactionEditor, self.parent_window,
                                 trans, account_transaction, model)

        if transaction:
            transaction.syncUpdate()
            self._update_transaction(item, transaction,
                                     transaction.edited_account.description,
                                     transaction.value)
            self._update_totals()
            self.update(item)
        finish_transaction(trans, transaction)

    def add_transaction_dialog(self):
        trans = new_transaction()
        model = getattr(self.model, 'account', self.model)
        model = trans.get(model)
        transaction = run_dialog(AccountTransactionEditor, self.parent_window,
                                 trans, None, model)
        if transaction:
            transaction.sync()
            value = transaction.value
            other = transaction.get_other_account(model)
            if other == model:
                value = -value
            item = self._add_transaction(transaction, other.description, value)
            self._update_totals()
            self.update(item)
        finish_transaction(trans, transaction)

    def _on_row__activated(self, objectlist, item):
        if item.kind == 'account':
            self._edit_transaction_dialog(item)


class FinancialApp(AppWindow):

    app_name = _('Financial')
    app_icon_name = 'stoq-financial-app'
    gladefile = 'financial'

    def __init__(self, app):
        self._pages = {}

        self.accounts = AccountTree()

        AppWindow.__init__(self, app)
        self.search_holder.add(self.accounts)
        self.accounts.show()
        self._refresh_accounts()
        self._tills_account = sysparam(self.conn).TILLS_ACCOUNT
        self._create_initial_page()

    #
    # AppWindow overrides
    #

    def activate(self):
        self._refresh_accounts()
        for page in self._pages.values():
            page.refresh()

    #
    # Private
    #

    def create_actions(self):
        ui_string = """<ui>
          <menubar action="menubar">
            <menu action="FinancialMenu">
              <menuitem action="AddAccount"/>
              <menuitem action="Import"/>
              <menuitem action="Quit"/>
            </menu>
          </menubar>
          <toolbar action="toolbar">
            <toolitem action="CloseTab"/>
            <toolitem action="AddAccount"/>
            <toolitem action="DeleteAccount"/>
            <separator/>
            <toolitem action="AddTransaction"/>
            <toolitem action="DeleteTransaction"/>
          </toolbar>
        </ui>"""

        actions = [
            ('menubar', None, ''),

            # Financial
            ('FinancialMenu', None, _("_Financial")),
            ('Import', gtk.STOCK_ADD, _('Import...'),
             '<control>i', _('Import a GnuCash or OFX file')),
            ("Quit", gtk.STOCK_QUIT),

            # Toolbar
            ('toolbar', None, ''),
            ('CloseTab', gtk.STOCK_CLOSE, _('Close'), '<control>w',
             _('Close the current tab')),
            ('EditAccount', gtk.STOCK_EDIT, _('Edit Account'),
             '<control>e', _('Change the currently selected account')),
            ('AddAccount', gtk.STOCK_ADD, _('Create New Account'),
             '<control>a', _('Create New Account')),
            ('DeleteAccount', gtk.STOCK_DELETE, _('Delete Account'),
             '', _('Delete Account')),
            ('AddTransaction', gtk.STOCK_ADD, _('Create New Transaction'),
             '<control>t', _('Create New Transaction')),
            ('DeleteTransaction', gtk.STOCK_DELETE, _('Delete Transaction'),
             '', _('Delete Transaction')),
            ]
        self.add_ui_actions(ui_string, actions)
        self.add_help_ui(_("Financial help"), 'financial-inicio')
        self.add_user_ui()

    def create_ui(self):
        self._update_actions()

        menubar = self.uimanager.get_widget('/menubar')
        self.main_vbox.pack_start(menubar, False, False)
        self.main_vbox.reorder_child(menubar, 0)

        toolbar = self.uimanager.get_widget('/toolbar')
        toolbar.set_style(gtk.TOOLBAR_BOTH)
        self.main_vbox.pack_start(toolbar, False, False)
        self.main_vbox.reorder_child(toolbar, 1)

    def _update_actions(self):
        self.CloseTab.set_sensitive(self._can_close_tab())
        self.AddAccount.set_sensitive(self._can_add_account())
        self.EditAccount.set_sensitive(self._can_edit_account())
        self.DeleteAccount.set_sensitive(self._can_delete_account())
        self.AddTransaction.set_sensitive(self._can_add_transaction())
        self.DeleteTransaction.set_sensitive(self._can_delete_transaction())

        self.details_button.set_sensitive(self._can_show_details())

    def _create_initial_page(self):
        pixbuf = self.accounts.render_icon('stoq-money', gtk.ICON_SIZE_MENU)
        page = self.notebook.get_nth_page(0)
        hbox = self._create_tab_label(_('Accounts'), pixbuf)
        self.notebook.set_tab_label(page, hbox)

    def _create_new_account(self):
        parent_view = None
        if self._is_accounts_tab():
            parent_view = self.accounts.get_selected()
        else:
            page_id = self.notebook.get_current_page()
            page = self.notebook.get_nth_page(page_id)
            if page.account_view.kind == 'account':
                parent_view = page.account_view
        retval = self._run_account_editor(None, parent_view)
        if retval:
            self.accounts.refresh_accounts(self.conn)

    def _refresh_accounts(self):
        self.accounts.clear()
        self.accounts.insert_initial(self.conn)

    def _edit_existing_account(self, account_view):
        assert account_view.kind == 'account'
        retval = self._run_account_editor(account_view,
                                          self.accounts.get_parent(account_view))
        if not retval:
            return
        self.accounts.refresh_accounts(self.conn)

    def _run_account_editor(self, model, parent_account):
        trans = new_transaction()
        if model:
            model = trans.get(model.account)
        if parent_account:
            if parent_account.kind in ['payable', 'receivable']:
                parent_account = None
            if parent_account == sysparam(self.conn).IMBALANCE_ACCOUNT:
                parent_account = None
        retval = self.run_dialog(AccountEditor, trans, model=model,
                                 parent_account=parent_account)
        if finish_transaction(trans, retval):
            self.accounts.refresh_accounts(self.conn)

        return retval

    def _close_current_page(self):
        assert self._can_close_tab()
        page = self._get_current_page_widget()
        self._close_page(page)

    def _get_current_page_widget(self):
        page_id = self.notebook.get_current_page()
        return self.notebook.get_children()[page_id]

    def _close_page(self, page):
        # Do not allow the initial page to be removed
        for page_id, child in enumerate(self.notebook.get_children()):
            if child == page:
                break
        self.notebook.remove_page(page_id)
        del self._pages[page.account_view]
        self._update_actions()

    def _is_accounts_tab(self):
        page_id = self.notebook.get_current_page()
        return page_id == 0

    def _is_transaction_tab(self):
        page = self._get_current_page_widget()
        if not isinstance(page, TransactionPage):
            return False

        if page.model.kind != 'account':
            return False

        if (page.model == self._tills_account or
            page.model.parentID == self._tills_account.id):
            return False
        return True

    def _can_close_tab(self):
        # The first tab is not closable
        return not self._is_accounts_tab()

    def _create_tab_label(self, title, pixbuf, page=None):
        hbox = gtk.HBox()
        image = gtk.image_new_from_pixbuf(pixbuf)
        hbox.pack_start(image, False, False)
        label = gtk.Label(title)
        hbox.pack_start(label, True, False)
        if title != _("Accounts"):
            image = gtk.image_new_from_stock(gtk.STOCK_CLOSE, gtk.ICON_SIZE_MENU)
            button = NotebookCloseButton()
            button.set_relief(gtk.RELIEF_NONE)
            if page:
                button.connect('clicked', lambda button: self._close_page(page))
            button.add(image)
            hbox.pack_end(button, False, False)
        hbox.show_all()
        return hbox

    def _new_page(self, account_view):
        if account_view in self._pages:
            page = self._pages[account_view.account]
            page_id = self.notebook.get_children().index(page)
        else:
            pixbuf = self.accounts.get_pixbuf(account_view)
            page = TransactionPage(account_view,
                                   self, self.get_toplevel())
            page.connect('selection-changed',
                         self._on_transaction__selection_changed)
            hbox = self._create_tab_label(account_view.description, pixbuf, page)
            page_id = self.notebook.append_page(page, hbox)
            page.show()
            page.account_view = account_view
            self._pages[account_view] = page

        self.notebook.set_current_page(page_id)
        self._update_actions()

    def _import(self):
        ffilters = []

        all_filter = gtk.FileFilter()
        all_filter.set_name(_('All supported formats'))
        all_filter.add_pattern('*.ofx')
        all_filter.add_mime_type('application/xml')
        all_filter.add_mime_type('application/x-gzip')
        ffilters.append(all_filter)

        ofx_filter = gtk.FileFilter()
        ofx_filter.set_name(_('Open Financial Exchange (OFX)'))
        ofx_filter.add_pattern('*.ofx')
        ffilters.append(ofx_filter)

        gnucash_filter = gtk.FileFilter()
        gnucash_filter.set_name(_('GNUCash xml format'))
        gnucash_filter.add_mime_type('application/xml')
        gnucash_filter.add_mime_type('application/x-gzip')
        ffilters.append(gnucash_filter)

        filename, file_chooser = open_dialog("Import", parent=self.financial,
                                             filter=ffilters, with_file_chooser=True)
        if filename is None:
            file_chooser.destroy()
            return

        ffilter = file_chooser.get_filter()
        if ffilter == gnucash_filter:
            format = 'gnucash.xml'
        elif ffilter == ofx_filter:
            format = 'account.ofx'
        elif ffilter == all_filter:
            # Guess
            if filename.endswith('.ofx'):
                format = 'account.ofx'
            else:
                format = 'gnucash.xml'

        file_chooser.destroy()

        run_dialog(ImporterDialog, self.financial, format, filename)

        self.accounts.refresh_accounts(self.conn)

    def _can_show_details(self):
        if not self._is_accounts_tab():
            return False

        account_view = self.accounts.get_selected()
        if account_view is None:
            return False

        if account_view.kind != 'account':
            return False

        return True

    def _can_add_account(self):
        if not self._is_accounts_tab():
            return False

        return True

    def _can_edit_account(self):
        if not self._is_accounts_tab():
            return False

        account_view = self.accounts.get_selected()
        if account_view is None:
            return False

        # Can only remove real accounts
        if account_view.kind != 'account':
            return False

        return True

    def _can_delete_account(self):
        if not self._is_accounts_tab():
            return False

        account_view = self.accounts.get_selected()
        if account_view is None:
            return False

        # Can only remove real accounts
        if account_view.kind != 'account':
            return False

        return account_view.account.can_remove()

    def _can_add_transaction(self):
        if not self._is_transaction_tab():
            return False

        return True

    def _can_delete_transaction(self):
        if not self._is_transaction_tab():
            return False

        page = self._get_current_page_widget()
        transaction = page.get_selected()
        if transaction is None:
            return False

        return True

    def _add_transaction(self):
        page = self._get_current_page_widget()
        page.add_transaction_dialog()

    def _delete_account(self, account_view):
        msg = _(u'Are you sure you want to remove account "%s" ?' %
                (account_view.description, ))
        if yesno(msg, gtk.RESPONSE_YES,
                 _("Don't Remove"), _(u"Remove account")):
            return

        self.accounts.remove(account_view)
        self.accounts.flush()

        trans = new_transaction()
        account = trans.get(account_view.account)
        account.remove(trans)
        trans.commit(close=True)

    def _delete_transaction(self, item):
        msg = _(u'Are you sure you want to remove transaction "%s" ?' %
                (item.description))
        if yesno(msg, gtk.RESPONSE_YES,
                 _("Don't Remove"), _(u"Remove transaction")):
            return

        account_transactions = self._get_current_page_widget()
        account_transactions.remove(item)

        trans = new_transaction()
        if isinstance(item.transaction, AccountTransactionView):
            account_transaction = trans.get(item.transaction.transaction)
        else:
            account_transaction = trans.get(item.transaction)
        account_transaction.delete(account_transaction.id, connection=trans)
        trans.commit(close=True)

    #
    # Kiwi callbacks
    #
    def key_escape(self):
        if self._can_close_tab():
            self._close_current_page()
        return True

    def key_control_w(self):
        if self._can_close_tab():
            self._close_current_page()
        return True

    def on_accounts__row_activated(self, ktree, account_view):
        self._new_page(account_view)

    def _on_transaction__selection_changed(self, ktree, account_transaction):
        self._update_actions()

    def on_accounts__selection_changed(self, ktree, account_view):
        self._update_actions()

    def on_details_button__clicked(self, button):
        account_view = self.accounts.get_selected()
        self._edit_existing_account(account_view)

    def on_notebook__switch_page(self, notebook, page, page_id):
        self._update_actions()

    # Toolbar

    def on_AddAccount__activate(self, action):
        self._create_new_account()

    def on_DeleteAccount__activate(self, action):
        account_view = self.accounts.get_selected()
        self._delete_account(account_view)

    def on_DeleteTransaction__activate(self, action):
        transactions = self._get_current_page_widget()
        transaction = transactions.get_selected()
        self._delete_transaction(transaction)

    def on_AddTransaction__activate(self, action):
        self._add_transaction()

    # Financial

    def on_Import__activate(self, action):
        self._import()
