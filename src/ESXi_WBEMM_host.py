#!/usr/bin/env python3
# -*- coding: utf8 -*-
""" ESXi host via WBEM protocol (only)"""

import WBEM_vmware as wbem
import inventoryObjects as inv


class ESXi_WBEM_Host(inv.GenericServer):
    def __init__(self, sFQDN, sUser, sPass, sVCenter, **dParams):
        """sAMM_Name is a name of the server in AMM"""
        super().__init__(sFQDN, IP=dParams.get('IP', None))
        self.sVCenter = sVCenter
        self.sUser = sUser
        self.sPass = sPass
        # data fields
        self.sSerialNum = ''
        self.sBladeNum = ''
        self.iTotalRAMgb = 0
        self.iDIMMs = 0
        self.iCPUs = 0
        self.lDIMMs = []
        self.lCPUs = []
        self.lExps = []
        self.lDisks = []
        self.dHostInfo = {}
        # receive information
        # self._FillDisksFromWBEM()
        return

    def _HostInfoFromWBEM(self):
        self.oHostWBEM = wbem.WBEM_System(self.sName, self.sUser, self.sPass, sVCenter=self.sVCenter)
        dWbemData = self.oHostWBEM._dGetInfo()
        self.sSerialNum = dWbemData.get('sn')
        self.sModel = dWbemData.get('model')
        self.sType = dWbemData.get('name')
        return

    def _MemFromWBEM(self):
        self.oMemWBEM = wbem.WBEM_Memory(self.sName, self.sUser, self.sPass, sVCenter=self.sVCenter)
        ldMemoryInfo = self.oMemWBEM._ldReportDisks
        return
