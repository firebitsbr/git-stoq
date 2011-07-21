# -*- coding: utf-8 -*-
# vi:si:et:sw=4:sts=4:ts=4
##
## Copyright (C) 2011 Async Open Source
##
## This program is free software; you can redistribute it and/or
## modify it under the terms of the GNU Lesser General Public License
## as published by the Free Software Foundation; either version 2
## of the License, or (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU Lesser General Public License for more details.
##
## You should have received a copy of the GNU Lesser General Public License
## along with this program; if not, write to the Free Software
## Foundation, Inc., or visit: http://www.gnu.org/.
##
## Author(s): Stoq Team <stoq-devel@async.com.br>
##

import gettext

from kiwi.environ import environ
import gtk
import webkit

from stoqlib.database.runtime import get_connection
from stoqlib.lib.parameters import sysparam

_ = gettext.gettext

class WelcomeDialog(gtk.Dialog):
    def __init__(self):
        gtk.Dialog.__init__(self)
        self.set_size_request(800, 480)
        self.set_deletable(False)

        sw = gtk.ScrolledWindow()
        sw.set_shadow_type(gtk.SHADOW_ETCHED_IN)
        self.get_content_area().pack_start(sw)

        self._view = webkit.WebView()
        self._view.connect('navigation-policy-decision-requested', self._on_view__navigation_requested)
        sw.add(self._view)

        self.button = self.add_button(_("Start using Stoq"), gtk.RESPONSE_OK)

        self.set_title(_("Welcome to Stoq"))
        self.show_all()

    def run(self):
        content = environ.find_resource('html', 'welcome-pt_BR.html')

        if sysparam(get_connection()).DEMO_MODE:
            content += '?demo-mode'
        self._view.load_uri('file://' + content)
        self.button.grab_focus()
        return super(WelcomeDialog, self).run()

    def _on_view__navigation_requested(self, view, frame, request, action, policy):
        uri = request.props.uri
        if not uri.startswith('file:///'):
            policy.ignore()
            gtk.show_uri(self.get_screen(), uri, gtk.get_current_event_time())