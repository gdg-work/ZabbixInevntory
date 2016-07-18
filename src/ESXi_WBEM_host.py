#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""" ESXi host via WBEM protocol (only)"""

import WBEM_vmware as wbem
import inventoryObjects as inv
import zabbixInterface as zi
import logging
import pywbem
import IPMIhost as ipmi
from i18n import _

oLog = logging.getLogger(__name__)


def _sMkKey_(*args):
    """Utility function: make string suitable for key"""
    sPreKey = "_".join(args)
    if ' ' in sPreKey:
        return sPreKey.replace(' ', '_')
    else:
        return sPreKey


class ESXi_WBEM_Host(inv.GenericServer):
    def __init__(self, sFQDN, sUser, sPass, sVCenter, **dParams):
        """sAMM_Name is a name of the server in AMM"""
        super().__init__(sFQDN, IP=dParams.get('IP', None))
        self.sVCenter = sVCenter
        self.sUser = sUser
        self.sPass = sPass
        # IPMI information from dParams dictionary
        self.dIPMIaccess = {}
        if dParams.get('sMgtIP', None) is not None:
            self.dIPMIaccess = {'ip':   dParams.get('sMgtIP', None),
                                'user': dParams.get('sMgtUser', None),
                                'pass': dParams.get('sMgtPass', None)}
        # data fields
        self.sSerialNum = ''
        self.sProdNum = ''
        self.sVendor = ''
        self.iTotalRAMgb = 0
        self.iTotalCores = 0
        self.iDIMMs = 0
        self.iCPUs = 0
        self.lDIMMs = []
        self.lCPUs = []
        self.lExps = []
        self.lDisks = []
        self.lPCI_Adapters = []
        self.dHostInfo = {}
        self.oTriggers = None
        self.oHostWBEM = None
        self.oMemWBEM = None
        self.oDisksWBEM = None
        self.oCardsWBEM = None
        self.oProcWBEM = None
        self.oAdaptersWBEM = None
        self.iPSAmount = 0
        # receive information
        self.__fillData()
        # receive info from IPMI
        if len(self.dIPMIaccess) > 0:
            self.__fillFromIPMI(self.dIPMIaccess)
            self._ClarifyMemFromIPMI()
        return

    def _ConnectTriggerFactory(self, oTriggersFactory):
        self.oTriggers = oTriggersFactory
        return

    def __fillData(self):
        for fun in [self._HostInfoFromWBEM,
                    self._MemFromWBEM,
                    self._CpuFromWBEM,
                    self._DisksFromWBEM,
                    self._HBAs_from_WBEM,
                    self._PwrSuppliesFromWBEM]:
            try:
                fun()
            except pywbem.cim_http.AuthError as e:
                oLog.error('Authentication error trying to access WBEM')
                oLog.error(str(e))
                raise e
            except wbem.WBEM_Disk_Exception as e:
                oLog.error("WBEM disk interface exception in {}".format(str(fun)))
                continue
            except pywbem.cim_operations.CIMError as e:
                oLog.error("CIM error in function " + str(fun))
                continue
            except Exception as e:
                oLog.error("Unhandled exception in __fillData")
                oLog.error(str(e))
        return

    def __repr__(self):
        """for debugging output"""
        sRet = "ESXi server {} manufactured by {}\n ".format(self.sName, self.sVendor)
        sRet += "Under the management of vCenter: {}\n".format(self.sVCenter)
        sRet += "Access: user {}, password {}.\n".format(self.sUser, self.sPass)
        sRet += "Numbers: product {}, serial {}\n".format(self.sProdNum, self.sSerialNum)
        sRet += "Memory amount: {} GB\n".format(self.iMemGB)
        return sRet

    def __fillFromIPMI(self, dIPMIaccess):
        """ create IPMI object in this host, fill in data for that object"""
        self.oIPMI_Interface = ipmi.IPMIhost(dIPMIaccess['ip'], dIPMIaccess['user'], dIPMIaccess['pass'])
        self.lFruList = self.oIPMI_Interface._loFruList()
        return

    def _ClarifyMemFromIPMI(self):
        """Add additional information to DIMM records based on IPMI FRU list"""
        if len(self.lFruList) > 0:    # are there any information ?
            for oFru in self.lFruList:
                if oFru.name[0:4] == 'DIMM':
                    for oDimm in self.lDIMMs:
                        if int(oFru.name[5:7]) == int(oDimm.name[4:]):
                            oDimm.sn = oFru.sn
                            oDimm.pn = oFru.pn
                            break
                else:
                    pass   # this FRU is not a DIMM
        else:
            pass   # no FRU list
        return

    def _PwrSuppliesFromWBEM(self):
        oPS_Wbem = wbem.WBEM_PowerSupplySet(self.sName, self.sUser, self.sPass, sVCenter=self.sVCenter)
        self.iPSAmount = oPS_Wbem._iGetPwrSuppliesAmount()
        return

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
        try:
            self.oDisksWBEM = wbem.WBEM_Disks(self.sName, self.sUser, self.sPass, sVCenter=self.sVCenter)
        except wbem.WBEM_Disk_Exception as e:
            oLog.error("_sDiskFromWBEM: cannot retrieve controller's namespace")
            raise e

        ldDisks = self.oDisksWBEM._ldReportDisks()
        iDisks = 0
        for dDisk in ldDisks:
            # print(str(dDisk))
            iDisks += 1
            iDiskSize = int(dDisk.get('MaxMediaSize', 0)) // 2**20
            self.lDisks.append(DASD(dDisk['Name'], dDisk['Model'], dDisk['PartNumber'],
                                    dDisk.get('SerialNumber'), iDiskSize))
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

    def _HBAs_from_WBEM(self):
        self.oHBAs = wbem.WBEM_HBAs(
            self.sName, self.sUser,
            self.sPass, sVCenter=self.sVCenter)
        # print(self.oHBAs)
        ldHBAs = self.oHBAs._ldReportAdapters()
        # print('*DBG* Found {} HBAs'.format(len(ldHBAs)))
        # print("\n".join([str(o) for o in ldHBAs]))
        for dHBA_Data in ldHBAs:
            oHBA = HBA_Class(
                dHBA_Data['id'], sPosition=dHBA_Data['pos'], sVendorID='', sDeviceID='',
                model=dHBA_Data['model'])
            oHBA.sPartNum = dHBA_Data.get('pn')
            oHBA.sSerNum = dHBA_Data.get('sn')
            oHBA.sWWN = dHBA_Data.get('wwn')
            # print(oHBA)
            self.lPCI_Adapters.append(oHBA)
        oLog.info('PCI Adapters list:\n{}'.format(str(self.lPCI_Adapters)))
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
            if self.iTotalCores > 0:
                oCoresItem = self.oZbxHost._oAddItem(
                    "System Cores #", sAppName='System',
                    dParams={'key': "Host_{}_Cores".format(self.sName), 'value_type': 3,
                             'description': _('Total number of cores in the system')})
                # number of cores in all CPUs
                oCoresItem._SendValue(self.iTotalCores, self.oZbxSender)

            # Host information
            oMTM_Item = self.oZbxHost._oAddItem(
                'System Model', sAppName='System',
                dParams={'key': '{}_MTM'.format(self.sName), 'value_type': 1,
                         'description': _('Model of the system')})
            oPN_Item = self.oZbxHost._oAddItem(
                'System Part Number', sAppName='System',
                dParams={'key': '{}_PartNo'.format(self.sName), 'value_type': 1,
                         'description': _('Part Number of the system')})
            oSN_Item = self.oZbxHost._oAddItem(
                'System Serial Number', sAppName='System',
                dParams={'key': '{}_SerNo'.format(self.sName), 'value_type': 1,
                         'description': _('Serial number of system')})
            oPS_Item = self.oZbxHost._oAddItem(
                'System Pwr.Sup. #', sAppName='System',
                dParams={'key': 'Pwr_Supplies_Count', 'value_type': 3,
                         'description': _('Number of power supplies')})
            oVendorItem._SendValue(self.sVendor, self.oZbxSender)
            oMTM_Item._SendValue(self.sType, self.oZbxSender)
            oPN_Item._SendValue(self.sProdNum, self.oZbxSender)
            oSN_Item._SendValue(self.sSerialNum, self.oZbxSender)
            oPS_Item._SendValue(self.iPSAmount, self.oZbxSender)

            # send components' items to Zabbix
            lComps = self.lCPUs + self.lDIMMs + self.lDisks + self.lPCI_Adapters
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

    @property
    def name(self):
        return self.sName

    @property
    def pn(self):
        return self.dData.get('pn', None)

    @pn.setter
    def pn(self, x):
        self.dData['pn'] = x

    @property
    def sn(self):
        return self.dData.get('sn', None)

    @sn.setter
    def sn(self, x):
        self.dData['sn'] = x

    @property
    def vendor(self):
        return self.dData.get('vendor', None)

    @vendor.setter
    def vendor(self, x):
        self.dData['vendor'] = x

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
        if 'pn' in self.dData:
            oPNItem = oZbxHost._oAddItem(
                self.sName + " Part Number", sAppName=self.sName,
                dParams={'key': _sMkKey_(self.sName, 'PN'),
                         'value_type': 2,
                         'description': _("DIMM part number")})
            oPNItem._SendValue(self.dData['pn'], oZbxSender)
        if 'sn' in self.dData:
            oSNItem = oZbxHost._oAddItem(
                self.sName + " Serial Number", sAppName=self.sName,
                dParams={'key': _sMkKey_(self.sName, 'SN'),
                         'value_type': 2,
                         'description': _("DIMM serial number")})
            oSNItem._SendValue(self.dData['sn'], oZbxSender)
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
        return sFmt.format(self.sName, self.dDiskData['model'], self.dDiskData['pn'],
                           self.dDiskData['sn'], self.dDiskData['size'])

    def _MakeAppsItems(self, oZbxHost, oZbxSender):
        oZbxHost._oAddApp(self.sName)     # Disk Drive_65535_0
        oModelItem = oZbxHost._oAddItem(
            self.sName + " Model", sAppName=self.sName,
            dParams={'key': "{}_{}_Model".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'value_type': 1, 'description': _('Disk model')})
        oPN_Item = oZbxHost._oAddItem(
            self.sName + " Part Number", sAppName=self.sName,
            dParams={'key': "{}_{}_PN".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'value_type': 1, 'description': _('Disk part number')})
        oSN_Item = oZbxHost._oAddItem(
            self.sName + " Serial Number", sAppName=self.sName,
            dParams={'key': "{}_{}_SN".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'value_type': 1, 'description': _('Disk serial number')})
        oSize_Item = oZbxHost._oAddItem(
            self.sName + " Size", sAppName=self.sName,
            dParams={'key': "{}_{}_Size".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'value_type': 3, 'units': 'GB', 'description': _('Disk capacity in GB')})
        oModelItem._SendValue(self.dDiskData['model'], oZbxSender)
        oPN_Item._SendValue(self.dDiskData['pn'], oZbxSender)
        oSN_Item._SendValue(self.dDiskData['sn'], oZbxSender)
        oSize_Item._SendValue(self.dDiskData['size'], oZbxSender)
        return


class PCI_Adapter(inv.ComponentClass):
    def __init__(self, sName, sPosition, sVendorID, sDeviceID, **dOther):
        """vendor and device IDs are integers, but used as a strings here"""
        self.sIDs = "{}:{}".format(sVendorID, sDeviceID)
        self.sName = sName
        self.dData = {'ids': self.sIDs, 'pos': sPosition}
        self.dData.update(dOther)
        self.sPartNum = ''
        self.sSerNum = ''
        return

    @property
    def sPartNum(self):
        return self.dData.get('pn')

    @property
    def sSerNum(self):
        return self.dData.get('sn')

    @sPartNum.setter
    def sPartNum(self, sPN):
        self.dData['pn'] = sPN

    @sSerNum.setter
    def sSerNum(self, sSN):
        self.dData['sn'] = sSN

    def __repr__(self):
        sRet =  "PCI adapter {}\n".format(self.sName)
        sRet += "Vendor/device Identifiers are {}\n".format(self.sIDs)
        sRet += "Bus position: {}\n".format(self.dData.get('pos'))
        sRet += "Other data:\n========\n{}\n---------\n".format(str(self.dData))
        return sRet

    def _MakeAppsItems(self, oZbxHost, oZbxSender, sApp=''):

        if sApp == '':
            sAppName = "PCI Device {}".format(self.sName)
        else:
            sAppName = sApp
        oZbxHost._oAddApp(sAppName)     # PCI device: vmhba2
        if self.sIDs != ':':
            oIDs_item = oZbxHost._oAddItem(
                sAppName + ' IDs', sAppName=sAppName,
                dParams={'key': _sMkKey_(sAppName, "IDs"), 'value_type': 1,
                         'description': _('PCI vendor:device identifiers')})
            oIDs_item._SendValue(self.sIDs, oZbxSender)
        oPosItem = oZbxHost._oAddItem(
            sAppName + ' Position', sAppName=sAppName,
            dParams={'key': _sMkKey_(sAppName, 'Pos'), 'value_type': 1,
                     'description': _('PCI device logical position (bus/slot/function)')})
        oPosItem._SendValue(self.dData['pos'], oZbxSender)
        if 'sn' in self.dData:
            oSNItem = oZbxHost._oAddItem(
                sAppName + ' Serial', sAppName=sAppName,
                dParams={'key': _sMkKey_(sAppName, 'SN'), 'value_type': 1,
                         'description': _('Device serial number')})
            oSNItem._SendValue(self.dData['sn'], oZbxSender)
        if 'pn' in self.dData:
            oPN_Item = oZbxHost._oAddItem(
                sAppName + ' Part Number', sAppName=sAppName,
                dParams={'key': _sMkKey_(sAppName, 'PN'), 'value_type': 1,
                         'description': _('Device part number')})
            oPN_Item._SendValue(self.dData['pn'], oZbxSender)
        if 'model' in self.dData:
            oModelItem = oZbxHost._oAddItem(
                sAppName + ' Model', sAppName=sAppName,
                dParams={'key': _sMkKey_(sAppName, 'Model'), 'value_type': 1,
                         'description': _('Device model')})
            oModelItem._SendValue(self.dData['model'], oZbxSender)
        return


class HBA_Class(PCI_Adapter):
    def __init__(self, sName, sPosition, sVendorID, sDeviceID, **dOther):
        super().__init__(sName, sPosition, sVendorID, sDeviceID, **dOther)

    @property
    def sWWN(self):
        return self.dData.get('wwn', '')

    @sWWN.setter
    def sWWN(self, sID):
        self.dData['wwn'] = sID

    def __repr__(self):
        return super().__repr__() + "\nWWN: " + self.dData.get('wwn', '')

    def _MakeAppsItems(self, oZbxHost, oZbxSender):
        sAppName = "Host Bus Adapter {}".format(self.sName)
        oZbxHost._oAddApp(sAppName)     # HBA vmhba2
        super()._MakeAppsItems(oZbxHost, oZbxSender, sApp=sAppName)
        if 'wwn' in self.dData:
            oModelItem = oZbxHost._oAddItem(
                sAppName + ' WWN', sAppName=sAppName,
                dParams={'key': _sMkKey_(sAppName, 'WWN'), 'value_type': 1,
                         'description': _('HBA World-Wide-Name (WWN)')})
            oModelItem._SendValue(self.dData['wwn'], oZbxSender)
        return


if __name__ == "__main__":
    # host for testing
    from access import demohs21_host as tsrv

    # Zabbix functionality
    from pyzabbix.api import ZabbixAPI
    from pyzabbix.sender import ZabbixSender
    ZABBIX_IP = "127.0.0.1"
    ZABBIX_PORT = 10051
    ZABBIX_SERVER = 'http://10.1.96.163/zabbix/'

    oLog.setLevel(logging.DEBUG)
    oConHdr = logging.StreamHandler()
    oConHdr.setLevel(logging.DEBUG)
    oLog.addHandler(oConHdr)

    iPort = 5989

    oTestHost = ESXi_WBEM_Host(tsrv.sHostLong, tsrv.sUser, tsrv.sPass, tsrv.sVCenter)
    oZbxAPI = ZabbixAPI(url=ZABBIX_SERVER, user=tsrv.sZbxUser, password=tsrv.sZbxPass)
    oZbxSender = ZabbixSender(zabbix_server='127.0.0.1', zabbix_port=ZABBIX_PORT)
    oTestHost._Connect2Zabbix(oZbxAPI, oZbxSender)
    oTestHost._MakeAppsItems()
    print(oTestHost)
