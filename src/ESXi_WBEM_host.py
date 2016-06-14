#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""" ESXi host via WBEM protocol (only)"""

import WBEM_vmware as wbem
import inventoryObjects as inv
import zabbixInterface as zi
import logging
import gettext

oLog = logging.getLogger(__name__)
gettext.install('inventory-Zabbix', localedir='locale')
_ = gettext.lgettext


class ESXi_WBEM_Host(inv.GenericServer):
    def __init__(self, sFQDN, sUser, sPass, sVCenter, **dParams):
        """sAMM_Name is a name of the server in AMM"""
        super().__init__(sFQDN, IP=dParams.get('IP', None))
        self.sVCenter = sVCenter
        self.sUser = sUser
        self.sPass = sPass
        # data fields
        self.sSerialNum = ''
        self.sProdNum = ''
        self.sVendor = ''
        self.iTotalRAMgb = 0
        self.iDIMMs = 0
        self.iCPUs = 0
        self.lDIMMs = []
        self.lCPUs = []
        self.lExps = []
        self.lDisks = []
        self.dHostInfo = {}
        self.oHostWBEM = None
        self.oMemWBEM = None
        self.oDisksWBEM = None
        self.oCardsWBEM = None
        self.oProcWBEM = None
        # receive information
        self.__fillData__()
        return

    def __fillData__(self):
        self._HostInfoFromWBEM()
        self._MemFromWBEM()
        self._CpuFromWBEM()
        self._DisksFromWBEM()
        return

    def __repr__(self):
        """for debugging output"""
        sRet = "ESXi server {} manufactured by {}\n ".format(self.sName, self.sVendor)
        sRet += "Under the management of vCenter: {}\n".format(self.sVCenter)
        sRet += "Access: user {}, password {}.\n".format(self.sUser, self.sPass)
        sRet += "Numbers: product {}, serial {}\n".format(self.sProdNum, self.sSerialNum)
        sRet += "Memory amount: {} GB\n".format(self.iMemGB)
        return sRet

    def _HostInfoFromWBEM(self):
        self.oHostWBEM = wbem.WBEM_System(self.sName, self.sUser, self.sPass, sVCenter=self.sVCenter)
        dWbemData = self.oHostWBEM._dGetInfo()
        oLog.debug('Host data: ' + str(dWbemData))
        self.sSerialNum = dWbemData.get('sn', '')
        self.sModel = dWbemData.get('model', '')
        self.sVendor = dWbemData.get('vendor', '')
        self.sProdNum = self.sModel
        self.sType = dWbemData.get('name', '')
        return

    def _MemFromWBEM(self):
        self.oMemWBEM = wbem.WBEM_Memory(self.sName, self.sUser, self.sPass, sVCenter=self.sVCenter)
        ldMemoryInfo = self.oMemWBEM._ldGetInfo()
        iTotalCapacity = 0
        for dData in ldMemoryInfo:
            iSize = int(dData['Capacity'])
            iTotalCapacity += iSize
            self.lDIMMs.append(Memory_DIMM(dData['Caption'], dData['BankLabel'], iSize // 2**30))
            # print(str(dData))
        self.iMemGB = iTotalCapacity // int(2**30)
        return

    def _CpuFromWBEM(self):
        self.oCPU_WBEM = wbem.WBEM_CPU(self.sName, self.sUser, self.sPass, sVCenter=self.sVCenter)
        ldCPUInfo = self.oCPU_WBEM._ldGetInfo()
        iTotalCores = 0
        for dData in ldCPUInfo:
            # print(str(dData))
            iCores = int(dData['NumberOfEnabledCores'])
            iTotalCores += iCores
            self.lCPUs.append(CPU(dData['Description'], dData['MaxClockSpeed'],
                                  dData['ModelName'], iCores))
        self.iTotalCores = iTotalCores
        return

    def _DisksFromWBEM(self):
        self.oDisksWBEM = wbem.WBEM_Disks(self.sName, self.sUser, self.sPass, sVCenter=self.sVCenter)
        ldDisks = self.oDisksWBEM._ldReportDisks()
        iDisks = 0
        for dDisk in ldDisks:
            print(str(dDisk))
            iDisks += 1
            self.lDisks.append(DASD(dDisk['Name'], dDisk['Model'], dDisk['PartNumber'],
                                    dDisk.get('SerialNumber')))
        self.iDisksAmount = iDisks
        oLog.debug(str(self.lDisks))
        return

    def _AdaptersFromWBEM(self):
        self.oAdaptersWBEM = wbem.WBEM_PCI_Adapters(
            self.sName, self.sUser,
            self.sPass, sVCenter=self.sVCenter)
        ldAdapters = self.oAdaptersWBEM._ldReportAdapters()
        for dAdapter in ldAdapters:
            self.lPCI_Adapters.append(PCI_Adapter(dAdapter['Name']))
        return

    def _Connect2Zabbix(self, oAPI, oSender):
        self.oZbxAPI = oAPI
        self.oZbxSender = oSender
        self.oZbxHost = zi.ZabbixHost(self.sName, self.oZbxAPI)
        return

    def _MakeAppsItems(self):
        """Creates applications and items on Zabbix server and sends data to Zabbix"""
        if self.oZbxHost:
            # zabbix interface is defined
            self.oZbxHost._oAddApp('System')
            # Add items
            oMemItem = self.oZbxHost._oAddItem(
                "System Memory", sAppName='System',
                dParams={'key': "Host_{}_Memory".format(self.sName), 'units': 'GB', 'value_type': 3,
                         'description': _('Total memory size in GB')})
            oMemItem._SendValue(self.iMemGB, self.oZbxSender)
            oVendorItem = self.oZbxHost._oAddItem(
                "System Vendor", sAppName='System',
                dParams={'key': 'Host_{}_Vendor'.format(self.sName), 'value_type': 1,
                         'description': _('Manufacturer of the system')})
            oCPUItem = self.oZbxHost._oAddItem(
                "System CPUs #", sAppName='System',
                dParams={'key': "Host_{}_CPUs".format(self.sName), 'value_type': 3,
                         'description': _('Number of processors in the system')})
            oCPUItem._SendValue(len(self.lCPUs), self.oZbxSender)
            #    oCoresItem = self.oZbxHost._oAddItem(
            #        "System Cores #", sAppName='System',
            #        dParams={'key': "Host_{}_Cores".format(self.sName), 'value_type': 3})
            #    # number of cores in all CPUs
            #    oCoresItem._SendValue(self.iTotalCores, self.oZbxSender)

            # Host information
            oMTM_Item = self.oZbxHost._oAddItem(
                'System Model', sAppName='System',
                dParams={'key': '{}_MTM'.format(self.sName), 'value_type': 1,
                         'description': _('Model of the system')})
            oPN_Item = self.oZbxHost._oAddItem(
                'System Part Number', sAppName='System',
                dParams={'key': '{}_PartNo'.format(self.sName), 'value_type': 1})
            oSN_Item = self.oZbxHost._oAddItem(
                'System Serial Number', sAppName='System',
                dParams={'key': '{}_SerNo'.format(self.sName), 'value_type': 1,
                         'description': _('Serial number of system')})

            oVendorItem._SendValue(self.sVendor, self.oZbxSender)
            oMTM_Item._SendValue(self.sType, self.oZbxSender)
            oPN_Item._SendValue(self.sProdNum, self.oZbxSender)
            oSN_Item._SendValue(self.sSerialNum, self.oZbxSender)

            # send components' items to Zabbix
            lComps = self.lCPUs + self.lDIMMs + self.lDisks + self.lExps
            for o in lComps:
                o._MakeAppsItems(self.oZbxHost, self.oZbxSender)
        else:
            oLog.error("Zabbix interface isn't initialized yet")
            raise Exception("Zabbix isn't connected yet")
        return

# ================================= Component classes ======================================


class Memory_DIMM(inv.ComponentClass):
    def __init__(self, sName, sPosition, iSizeGB):
        self.sName = sName
        self.dData = {'pos': sPosition, 'size_gb': iSizeGB}
        return

    def __repr__(self):
        return ("{0}: {1}-GB Module in position {2}".format(
            self.sName, self.dData['size_gb'], self.dData['pos']))

    def _MakeAppsItems(self, oZbxHost, oZbxSender):
        """Make applications, items and send values to Zabbix"""
        oZbxHost._oAddApp(self.sName)     # DIMM #
        oPosItem = oZbxHost._oAddItem(
            self.sName + " Position", sAppName=self.sName,
            dParams={'key': "{}_{}_Pos".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'value_type': 2,
                     'description': _("Position in the host")})
        oSize_Item = oZbxHost._oAddItem(
            self.sName + " Size", sAppName=self.sName,
            dParams={'key': "{}_{}_SizeGB".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'value_type': 3, 'units': 'GB',
                     'description': _('Size of memory unit in GiB')})
        oPosItem._SendValue(self.dData['pos'], oZbxSender)
        oSize_Item._SendValue(self.dData['size_gb'], oZbxSender)
        return


class CPU(inv.ComponentClass):
    def __init__(self, sName, sSpeed, sFamily, iCores):
        self.sName = sName
        self.dData = {'speed': sSpeed, 'family': sFamily, 'cores': iCores}
        return

    def __repr__(self):
        return ("{0}: {3}-core {2} processor at {1}".format(
            self.sName, self.dData['speed'], self.dData['family'], self.dData['cores']))

    def _MakeAppsItems(self, oZbxHost, oZbxSender):
        """Make applications, items and send values to Zabbix"""
        oZbxHost._oAddApp(self.sName)     # CPU #
        oTypeItem = oZbxHost._oAddItem(
            self.sName + " Type", sAppName=self.sName,
            dParams={'key': "{}_{}_Type".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'description': _('Processor type'),
                     'value_type': 1})
        oCoresItem = oZbxHost._oAddItem(
            self.sName + " # Cores", sAppName=self.sName,
            dParams={'key': "{}_{}_Cores".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'description': _('Number of cores'),
                     'value_type': 3})
        oSpeedItem = oZbxHost._oAddItem(
            self.sName + " Speed", sAppName=self.sName,
            dParams={'key': "{}_{}_Speed".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'description': _('CPU Clock speed'),
                     'value_type': 1})

        oLog.debug(_('CPU Clock speed'))
        oTypeItem._SendValue(self.dData['family'], oZbxSender)
        oCoresItem._SendValue(self.dData['cores'], oZbxSender)
        oSpeedItem._SendValue(self.dData['speed'], oZbxSender)
        return


class DASD(inv.ComponentClass):
    def __init__(self, sName, sModel, sPN, sSN, iSizeGB):
        self.sName = sName
        self.dDiskData = {
            "model": sModel,
            "pn": sPN,
            "sn": sSN,
            "size": iSizeGB}
        return

    def __repr__(self):
        sFmt = "HDD {0}: model {1}, p/n {2}, s/n {3}, size {4} GiB"
        return sFmt.format(self.sName, self.dDiskData['Model'], self.dDiskData['PN'],
                           self.dDiskData['SN'], self.dDiskData['Size'])

    def _MakeAppsItems(self, oZbxHost, oZbxSender):
        oZbxHost._oAddApp(self.sName)     # Disk Drive_65535_0
        oModelItem = oZbxHost._oAddItem(
            self.sName + " Model", sAppName=self.sName,
            dParams={'key': "{}_{}_Model".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'value_type': 1})
        oPN_Item = oZbxHost._oAddItem(
            self.sName + " Part Number", sAppName=self.sName,
            dParams={'key': "{}_{}_PN".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'value_type': 1})
        oSN_Item = oZbxHost._oAddItem(
            self.sName + " Serial Number", sAppName=self.sName,
            dParams={'key': "{}_{}_SN".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'value_type': 1})
        oSize_Item = oZbxHost._oAddItem(
            self.sName + " Size", sAppName=self.sName,
            dParams={'key': "{}_{}_Size".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'value_type': 3, 'units': 'GB'})
        oModelItem._SendValue(self.dData['model'], oZbxSender)
        oPN_Item._SendValue(self.dData['pn'], oZbxSender)
        oSN_Item._SendValue(self.dData['sn'], oZbxSender)
        oSize_Item._SendValue(self.dData['size'], oZbxSender)
        return


class PCI_Adapter(inv.ComponentClass):
    def __init__(self, sName, sPosition, sVendorID, sDeviceID, **dOther):
        """vendor and device IDs are integers, but used as a strings here"""
        self.sIDs = "{}:{}".format(sVendorID, sDeviceID)
        self.sName = sName
        self.dData = {'ids': self.sIDs, 'pos': sPosition}
        self.dData.extend(dOther)
        return

    def __repr__(self):
        sRet =  "PCI adapter {}\n".format(self.sName)
        sRet += "Vendor/device Identifiers are {}\n".format(self.sIDs)
        sRet += "Bus position: {}\n".format(self.dData.get('pos'))
        sRet += "Other data:\n========\n{}\n---------\n".format(str(self.dData))
        return

if __name__ == "__main__":
    from pyzabbix.api import ZabbixAPI
    from pyzabbix.sender import ZabbixSender
    ZABBIX_IP = "127.0.0.1"
    ZABBIX_PORT = 10051
    ZABBIX_SERVER = 'http://10.1.96.163/zabbix/'

    gettext.install('zabbix-Inventory', localedir='locale')
    oLog.setLevel(logging.DEBUG)
    oConHdr = logging.StreamHandler()
    oConHdr.setLevel(logging.DEBUG)
    oLog.addHandler(oConHdr)

    sHostName = '2demohs21.hostco.ru'    # vmsrv06.msk.protek.local'
    sUser = 'cimuser'          # 'zabbix'
    sPass = '123qweASD'      # 'A3hHr88man01'
    iPort = 5989

    oTestHost = ESXi_WBEM_Host(sHostName, sUser, sPass, sVCenter='vcenter.hostco.ru')
    oZbxAPI = ZabbixAPI(url=ZABBIX_SERVER, user='Admin', password='zabbix')
    oZbxSender = ZabbixSender(zabbix_server='127.0.0.1', zabbix_port=ZABBIX_PORT)
    oTestHost._Connect2Zabbix(oZbxAPI, oZbxSender)
    oTestHost._MakeAppsItems()
    print(oTestHost)
