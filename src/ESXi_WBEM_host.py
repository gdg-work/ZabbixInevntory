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
from local import NODATA_THRESHOLD

oLog = logging.getLogger(__name__)


def _sNormDimmName(sName: str):
    """The function will normalize DIMM names, such as:
    DIMM01  -> DIMM 01
    DIMM7   -> DIMM 07
    DIMM 2  -> DIMM 02
    DIMM 17 -> DIMM 17
    sName looked at as three-part string: first is a label (letters only),
    2nd is a number (digits only), rest is void.
    """
    sLabel, sNumber = ("", "")
    bFillLabel, bFillNumber = (True, True)
    sName = sName.strip()
    for sChar in sName:
        # print("*DBG* Processing char: " + sChar)
        if sChar.isalpha() and bFillLabel:
            # print("*DBG* in label")
            sLabel += sChar
        elif sChar.isnumeric() and bFillNumber:
            # print("*DBG* in number")
            bFillLabel = False  # end of label
            sNumber += sChar
        elif sChar.isspace() or sChar in '_-+:':
            # print("*DBG* space")
            if bFillLabel:
                sLabel += sChar
            elif bFillNumber:
                bFillNumber = False
        else:
            # label is competed and non-digit occurs
            bFillNumber = False
            # print("*DBG* end of processing")
    # now we have label and number separated
    iNum = int(sNumber)
    sLabel = sLabel.strip()
    sRes = "{0} {1:02d}".format(sLabel, iNum)
    return sRes


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
            oLog.warning('Server {}, requested IPMI via {}'.format(self.sName, dParams.get('sMgtIP', '')))
            self.dIPMIaccess = {'ip':   dParams.get('sMgtIP', None),
                                'user': dParams.get('sMgtUser', None),
                                'pass': dParams.get('sMgtPass', None)}
        # data fields
        self.sSerialNum = ''
        self.sProdNum = ''
        self.sVendor = ''
        self.iTotalCores = 0
        self.iDIMMs = 0
        self.iCPUs = 0
        self.iMemGB = 0
        self.lDIMMs = []
        self.lCPUs = []
        self.lExps = []
        self.lDisks = []
        self.lPCI_Adapters = []
        self.loHBA_List = []
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
        # receive info from IPMI
        return

    def _ConnectTriggerFactory(self, oTriggersFactory):
        self.oTriggers = oTriggersFactory
        return

    def __FillData(self):
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
            except wbem.WBEM_HBA_Exception as e:
                oLog.error('WBEM HBA information access exception in __FillData' + str(e))
                continue
            except wbem.WBEM_PowerSupply_Exception as e:
                oLog.error('WBEM power supply access exception in __FillData' + str(e))
                continue
            except pywbem.cim_operations.CIMError as e:
                oLog.error("CIM error in function " + str(fun.__name__))
                continue
            except wbem.WBEM_Exception as e:
                oLog.error('Unknown WBEM exception in __FillData: ' + str(e))
                continue
            except Exception as e:
                oLog.error("Unhandled exception in __FillData")
                oLog.error(str(e))
        if len(self.dIPMIaccess) > 0:
            # oLog.warning('IPMI access requested, data is: ' + str(self.dIPMIaccess))
            self.__fillFromIPMI(self.dIPMIaccess)
            self._ClarifyMemFromIPMI()
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
        oLog.warning('IPMI access called for server {}, BMC IP: {}'.format(self.sName, dIPMIaccess['ip']))
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
            oLog.error(
                'WBEM error when initializing WBEM_Disks interface of server {}, msg: {}'.format(
                    self.sName, str(e)))
            raise e

        try:
            ldDisks = self.oDisksWBEM._ldReportDisks()
        except wbem.WBEM_Disk_Exception as e:
            oLog.error('WBEM error collecting disks info from: ' + self.sName + 'error: ' + str(e))
            raise(e)

        for dDisk in ldDisks:
            # print(str(dDisk))
            iDiskSize = int(dDisk.get('MaxMediaSize', 0)) // 2**20
            self.lDisks.append(DASD(dDisk['Name'], dDisk['Model'], dDisk['PartNumber'],
                                    dDisk.get('SerialNumber'), iDiskSize))
        self.iDisksAmount = len(self.lDisks)
        # oLog.debug(str(self.lDisks))
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
            self.sName, self.sUser, self.sPass, sVCenter=self.sVCenter)
        # print(self.oHBAs)
        ldHBAs = self.oHBAs._ldReportAdapters()
        # print('*DBG* Found {} HBAs'.format(len(ldHBAs)))
        # print("\n".join([str(o) for o in ldHBAs]))
        iHbaNum = 0
        for dHBA_Data in ldHBAs:
            iHbaNum += 1
            oHBA = HBA_Class(
                # dHBA_Data['Name'],
                'HBA {}'.format(iHbaNum),
                sVendorID=dHBA_Data.get('Manufacturer', ''),
                sDeviceID=dHBA_Data.get('IdentifyingNumber', ''),
                sPosition=dHBA_Data.get('Position', ''),
                model=dHBA_Data['Model'])
            oHBA.sPartNum = dHBA_Data.get('PartNumber', '')
            oHBA.sSerNum = dHBA_Data.get('SerialNumber')
            oHBA.sWWN = dHBA_Data.get('wwn', '')
            # print(oHBA)
            self.loHBA_List.append(oHBA)
        oLog.info('HBAs list:\n{}'.format(str(self.loHBA_List)))
        return

    def _Connect2Zabbix(self, oAPI, oSender):
        self.oZbxAPI = oAPI
        self.oZbxSender = oSender
        self.oZbxHost = zi.ZabbixHost(self.sName, self.oZbxAPI)
        return

    def _MakeAppsItems(self):
        """Creates applications and items on Zabbix server and sends data to Zabbix"""
        self.__FillData()    # receive information from WBEM
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

            # Add some triggers
            if self.oTriggers is not None:
                self.oTriggers._AddChangeTrigger(oMemItem, _('Memory size changed'), 'warning')
                self.oTriggers._AddChangeTrigger(oCPUItem, _('Number of CPUs changed'), 'warning')
                self.oTriggers._AddChangeTrigger(oSN_Item, _('System SN changed'), 'average')
                self.oTriggers._AddNoDataTrigger(oSN_Item, _('Cannot receive system SN in 2 days'), 'average')

            # send components' items to Zabbix
            lComps = self.lCPUs + self.lDIMMs + self.lDisks + self.lPCI_Adapters + self.loHBA_List
            for o in lComps:
                o._ConnectTriggerFactory(self.oTriggers)
                o._MakeAppsItems(self.oZbxHost, self.oZbxSender)
            self.oZbxHost._MakeTimeStamp(self.oZbxSender)
        else:
            oLog.error("Zabbix interface isn't initialized yet")
            raise Exception("Zabbix isn't connected yet")
        return

# ================================= Component classes ======================================


class Memory_DIMM(inv.ComponentClass):
    def __init__(self, sName, sPosition, iSizeGB):
        self.sName = _sNormDimmName(sName)
        self.dData = {'pos': sPosition, 'size_gb': iSizeGB}
        return

    def _ConnectTriggerFactory(self, oTriggersFactory):
        self.oTriggers = oTriggersFactory
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
        sApplication = self.sName
        oZbxHost._oAddApp(sApplication)     # DIMM #
        oPosItem = oZbxHost._oAddItem(
            sApplication + " Position", sAppName=sApplication,
            dParams={'key': zi._sMkKey(oZbxHost._sName(), sApplication, "Pos"),
                     'value_type': 1,
                     'description': _("Position in the host")})
        oSize_Item = oZbxHost._oAddItem(
            sApplication + " Size", sAppName=sApplication,
            dParams={'key': zi._sMkKey(oZbxHost._sName(), sApplication, "SizeGB"),
                     'value_type': 3, 'units': 'GB',
                     'description': _('Size of memory unit in GiB')})
        oPosItem._SendValue(self.dData['pos'], oZbxSender)
        oSize_Item._SendValue(self.dData['size_gb'], oZbxSender)
        if self.oTriggers:
            self.oTriggers._AddNoDataTrigger(oPosItem, _('Cannot determine DIMM position in 48h'),
                                             'average', NODATA_THRESHOLD)
            self.oTriggers._AddChangeTrigger(oSize_Item, _('DIMM size changed'), 'warning')

        if 'pn' in self.dData:
            oPNItem = oZbxHost._oAddItem(
                sApplication + " Part Number", sAppName=sApplication,
                dParams={'key': zi._sMkKey(sApplication, 'PN'),
                         'value_type': 1,
                         'description': _("DIMM part number")})
            oPNItem._SendValue(self.dData['pn'], oZbxSender)
        if 'sn' in self.dData:
            oSNItem = oZbxHost._oAddItem(
                sApplication + " Serial Number", sAppName=sApplication,
                dParams={'key': zi._sMkKey(sApplication, 'SN'),
                         'value_type': 1,
                         'description': _("DIMM serial number")})
            oSNItem._SendValue(self.dData['sn'], oZbxSender)
            if self.oTriggers:
                self.oTriggers._AddChangeTrigger(oSNItem, _('DIMM serial number is changed'), 'warning')
        return


class CPU(inv.ComponentClass):
    def __init__(self, sName, sSpeed, sFamily, iCores):
        self.sName = sName
        self.dData = {'speed': sSpeed, 'family': sFamily, 'cores': iCores}
        return

    def _ConnectTriggerFactory(self, oTriggersFactory):
        self.oTriggers = oTriggersFactory
        return

    def __repr__(self):
        return ("{0}: {3}-core {2} processor at {1}".format(
            self.sName, self.dData['speed'], self.dData['family'], self.dData['cores']))

    def _MakeAppsItems(self, oZbxHost, oZbxSender):
        """Make applications, items and send values to Zabbix"""
        oZbxHost._oAddApp(self.sName)     # CPU #
        oTypeItem = oZbxHost._oAddItem(
            self.sName + " Type", sAppName=self.sName,
            dParams={'key': zi._sMkKey(oZbxHost._sName(), self.sName, "Type"),
                     'description': _('Processor type'),
                     'value_type': 1})
        if self.oTriggers:
            self.oTriggers._AddNoDataTrigger(oTypeItem, _('Cannot determine processor type'),
                                             'average', NODATA_THRESHOLD)
        oCoresItem = oZbxHost._oAddItem(
            self.sName + " # Cores", sAppName=self.sName,
            dParams={'key': zi._sMkKey(oZbxHost._sName(), self.sName, "Cores"),
                     'description': _('Number of cores'),
                     'value_type': 3})
        oSpeedItem = oZbxHost._oAddItem(
            self.sName + " Speed", sAppName=self.sName,
            dParams={'key': zi._sMkKey(oZbxHost._sName(), self.sName, "Speed"),
                     'description': _('CPU Clock speed'),
                     'value_type': 1})

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

    def _ConnectTriggerFactory(self, oTriggersFactory):
        self.oTriggers = oTriggersFactory
        return

    def _MakeAppsItems(self, oZbxHost, oZbxSender):
        oZbxHost._oAddApp(self.sName)     # Disk Drive_65535_0
        oModelItem = oZbxHost._oAddItem(
            self.sName + " Model", sAppName=self.sName,
            dParams={'key': zi._sMkKey(oZbxHost._sName(), self.sName, "Model"),
                     'value_type': 1, 'description': _('Disk model')})
        oPN_Item = oZbxHost._oAddItem(
            self.sName + " Part Number", sAppName=self.sName,
            dParams={'key': zi._sMkKey(oZbxHost._sName(), self.sName, "PN"),
                     'value_type': 1, 'description': _('Disk part number')})
        oSN_Item = oZbxHost._oAddItem(
            self.sName + " Serial Number", sAppName=self.sName,
            dParams={'key': zi._sMkKey(oZbxHost._sName(), self.sName, "SN"),
                     'value_type': 1, 'description': _('Disk serial number')})
        if self.oTriggers:
            self.oTriggers._AddChangeTrigger(oSN_Item, _('Disk serial number is changed'), 'warning')
            self.oTriggers._AddNoDataTrigger(oSN_Item, _('Cannot receive disk serial number in two days'),
                                             'average')
        oSize_Item = oZbxHost._oAddItem(
            self.sName + " Size", sAppName=self.sName,
            dParams={'key': zi._sMkKey(oZbxHost._sName(), self.sName, "Size"),
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

    def _ConnectTriggerFactory(self, oTriggersFactory):
        self.oTriggers = oTriggersFactory
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
                dParams={'key': zi._sMkKey(sAppName, "IDs"), 'value_type': 1,
                         'description': _('PCI vendor:device identifiers')})
            oIDs_item._SendValue(self.sIDs, oZbxSender)
        if 'pos' in self.dData and self.dData['pos']:
            oPosItem = oZbxHost._oAddItem(
                sAppName + ' Position', sAppName=sAppName,
                dParams={'key': zi._sMkKey(sAppName, 'Pos'), 'value_type': 1,
                         'description': _('PCI device logical position (bus/slot/function)')})
            if self.oTriggers:
                self.oTriggers._AddNoDataTrigger(oPosItem, _('Cannot determine adapter position in 2 days'),
                                                 'average', NODATA_THRESHOLD)
            oPosItem._SendValue(self.dData['pos'], oZbxSender)
        if 'sn' in self.dData:
            oSNItem = oZbxHost._oAddItem(
                sAppName + ' Serial', sAppName=sAppName,
                dParams={'key': zi._sMkKey(sAppName, 'SN'), 'value_type': 1,
                         'description': _('Device serial number')})
            if self.oTriggers:
                self.oTriggers._AddChangeTrigger(oSNItem, _('Adapter S/N changed'), 'warning')
            oSNItem._SendValue(self.dData['sn'], oZbxSender)
        if 'pn' in self.dData:
            oPN_Item = oZbxHost._oAddItem(
                sAppName + ' Part Number', sAppName=sAppName,
                dParams={'key': zi._sMkKey(sAppName, 'PN'), 'value_type': 1,
                         'description': _('Device part number')})
            oPN_Item._SendValue(self.dData['pn'], oZbxSender)
        if 'model' in self.dData:
            oModelItem = oZbxHost._oAddItem(
                sAppName + ' Model', sAppName=sAppName,
                dParams={'key': zi._sMkKey(sAppName, 'Model'), 'value_type': 1,
                         'description': _('Device model')})
            oModelItem._SendValue(self.dData['model'], oZbxSender)
        return


class HBA_Class(PCI_Adapter):
    def __init__(self, sName, sVendorID, sDeviceID, **dOther):
        super().__init__(sName, sVendorID=sVendorID, sDeviceID=sDeviceID, **dOther)

    @property
    def sWWN(self):
        return self.dData.get('wwn', '')

    @sWWN.setter
    def sWWN(self, sID):
        self.dData['wwn'] = sID

    def _ConnectTriggerFactory(self, oTriggersFactory):
        self.oTriggers = oTriggersFactory
        return

    def __repr__(self):
        return super().__repr__()

    def _MakeAppsItems(self, oZbxHost, oZbxSender):
        sAppName = "Host Bus Adapter {}".format(self.sName)
        oZbxHost._oAddApp(sAppName)     # HBA vmhba2
        super()._MakeAppsItems(oZbxHost, oZbxSender, sApp=sAppName)

        if 'PartNumber' in self.dData:
            oPN_Item = oZbxHost._oAddItem(
                sAppName + ' P/N', sAppName=sAppName,
                dParams={'key': zi._sMkKey(sAppName, 'PartNo'), 'value_type': 1,
                         'description': _('HBA Part Number')})
            oPN_Item._SendValue(self.dData['PartNumber'], oZbxSender)
            if self.oTriggers:
                self.oTriggers._AddChangeTrigger(oPN_Item, _('Host Bus Adapter P/N changed'), 'warning')

        if 'wwn' in self.dData and self.dData['wwn']:
            oWWN_Item = oZbxHost._oAddItem(
                sAppName + ' WWN', sAppName=sAppName,
                dParams={'key': zi._sMkKey(sAppName, 'WWN'), 'value_type': 1,
                         'description': _('HBA World-Wide-Name (WWN)')})
            oWWN_Item._SendValue(self.dData['wwn'], oZbxSender)
            if self.oTriggers:
                self.oTriggers._AddChangeTrigger(oWWN_Item, _('Host Bus Adapter WWN changed'), 'warning')
        return


if __name__ == "__main__":
    # host for testing
    from access import vmexchsrv01 as tsrv
    from access import zabbixAtProtek as zbx

    # Zabbix functionality
    from pyzabbix.api import ZabbixAPI
    from pyzabbix.sender import ZabbixSender
    ZABBIX_IP = "127.0.0.1"
    ZABBIX_PORT = 10051
    ZABBIX_SERVER = 'http://localhost/zabbix/'

    oLog.setLevel(logging.DEBUG)
    oConHdr = logging.StreamHandler()
    oConHdr.setLevel(logging.DEBUG)
    oLog.addHandler(oConHdr)

    iPort = 5989

    oTestHost = ESXi_WBEM_Host(tsrv.sHostLong, tsrv.sUser, tsrv.sPass, tsrv.sVCenter)
    oZbxAPI = ZabbixAPI(url=ZABBIX_SERVER, user=zbx.user, password=zbx.password)
    oZbxSender = ZabbixSender(zabbix_server='127.0.0.1', zabbix_port=ZABBIX_PORT)
    oTestHost._Connect2Zabbix(oZbxAPI, oZbxSender)
    oTestHost._MakeAppsItems()
    print(oTestHost)
