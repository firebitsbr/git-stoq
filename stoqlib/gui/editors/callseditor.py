# -*- coding: utf-8 -*-
# vi:si:et:sw=4:sts=4:ts=4

##
## Copyright (C) 2011 Async Open Source <http://www.async.com.br>
## All rights reserved
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU Lesser General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
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

import datetime

from stoqlib.domain.interfaces import IUser
from stoqlib.domain.person import Calls, Person
from stoqlib.database.runtime import get_current_user
from stoqlib.gui.editors.baseeditor import BaseEditor
from stoqlib.lib.translation import stoqlib_gettext

_ = stoqlib_gettext

class CallsEditor(BaseEditor):
    model_type = Calls
    model_name = _("Calls")
    gladefile = 'CallsEditor'
    proxy_widgets = ('date',
                     'description',
                     'message',
                     'attendant')
    size = (400, 300)

    def __init__(self, conn, model, person):
        self.person = person
        BaseEditor.__init__(self, conn, model)
        self.set_description(self.model.person.name)

    def create_model(self, conn):
        return Calls(date=datetime.date.today(),
                     description='',
                     message='',
                     person=self.person,
                     attendant=get_current_user(self.conn),
                     connection=conn)

    def setup_proxies(self):
        self._fill_attendant_combo()
        self.proxy = self.add_proxy(self.model, CallsEditor.proxy_widgets)

    def _fill_attendant_combo(self):
        attendants = [(a.person.name, a)
                     for a in Person.iselect(IUser,
                                             connection=self.conn)]
        self.attendant.prefill(sorted(attendants))