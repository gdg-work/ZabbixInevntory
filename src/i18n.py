#!/usr/bin/env python3
import gettext
from logging import getLogger

oLog = getLogger('__name__')

# this module doesn't define any classes, just does language support initialization

rus_trans = None

try:
    rus_trans = gettext.translation('inventory-Zabbix',
                                    localedir='locale',
                                    languages=['ru'],
                                    codeset='utf-8')
    rus_trans.install()
    _ = rus_trans.gettext
except Exception as e:
    oLog.error('Cannot install Russian translation', str(e))
    iRetCode = 2
    raise(e)
