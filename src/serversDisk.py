#!/usr/bin/env python

import inventoryObjects as inv
import zabbixInterface as zi
import logging
from local import NODATA_THRESHOLD
from i18n import _

oLog = logging.getLogger(__name__)


class Disk_Drive(inv.ComponentClass):
    """
    A class for disk drive in any server.
    Interface: __init__, .sn, .name, __repr__, _MakeAppsItems.
    """
    def __init__(self, sName, sModel, sPN, sSN, iSizeGB):
        super().__init__(sName, sSN)
        self.sName = sName
        self.dDiskData = {
            "model": sModel,
            "pn": sPN,
            "sn": sSN,
            "size": iSizeGB}
        return

    @property
    def sn(self):
        return self.dDiskData['sn']

    @sn.setter
    def sn(self, sData):
        self.dDiskData['sn'] = sData

    @property
    def name(self):
        return self.sName

    def __repr__(self):
        sFmt = "HDD {0}: model {1}, p/n {2}, s/n {3}, size {4} GiB"
        return sFmt.format(self.sName, self.dDiskData['model'],
                           self.dDiskData['pn'], self.dDiskData['sn'],
                           self.dDiskData['size'])

    def _MakeAppsItems(self, oZbxHost, oZbxSender):

        def _sKey(sParamName):
            """Makes a key string with ZabbixInterface._sMkKey() function called with pre-defined parameters.
            Analog of partial application in the functional programming"""
            return zi._sMkKey(oZbxHost._sName(), self.sName, sParamName)

        oLog.info("Disk_Drive._MakeAppsItems: " + str(self))
        oLog.info("Creating application: " + self.sName)
        oZbxHost._oAddApp(self.sName)     # Drive 65535-0
        oModelItem = oZbxHost._oAddItem(self.sName + " Model", sAppName=self.sName,
            dParams={'key': _sKey("Model"), 'value_type': 1, 'description': _('Disk model')})
        assert oModelItem
        oPN_Item = oZbxHost._oAddItem(self.sName + " Part Number", sAppName=self.sName,
            dParams={'key': _sKey("PN"), 'value_type': 1, 'description': _('Disk part number')})
        assert oPN_Item
        oSN_Item = oZbxHost._oAddItem(self.sName + " Serial Number", sAppName=self.sName,
            dParams={'key': _sKey("SN"), 'value_type': 1, 'description': _('Disk serial number')})
        assert oSN_Item
        oLog.debug('SN item: ' + str(oSN_Item))
        oLog.debug('Serial number item: ' + oSN_Item.name + " host: " + oSN_Item.host.name)
        if self.dDiskData.get('size', 0) != 0:
            oSize_Item = oZbxHost._oAddItem(self.sName + "Size", sAppName=self.sName,
                dParams={'key': _sKey("Size"), 'value_type': 3, 'units': 'GB',
                         'description': _('Disk capacity in GB')})
            oSize_Item._SendValue(self.dDiskData['size'], oZbxSender)
        if self.oTriggers:
            self.oTriggers._AddChangeTrigger(oSN_Item, _('Disk serial number is changed'), 'warning')
            self.oTriggers._AddNoDataTrigger(oSN_Item, _('Cannot receive disk serial number in two days'),
                                             'average', NODATA_THRESHOLD)
        oModelItem._SendValue(self.dDiskData['model'], oZbxSender)
        oPN_Item._SendValue(self.dDiskData['pn'], oZbxSender)
        oSN_Item._SendValue(self.dDiskData['sn'], oZbxSender)
        return


if __name__ == '__main__':
    oDrive = Disk_Drive('Test disk', 'Test model', 'PN01TEST', 'SN_TEST', 120)
    print(oDrive)
