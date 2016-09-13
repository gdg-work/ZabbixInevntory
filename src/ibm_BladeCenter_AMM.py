#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""" A module for IBM Blade Servers using AMM as a OS-independent control """
import inventoryObjects as inv
import itertools as it
import logging
import MySSH
import zabbixInterface as zi
import WBEM_vmware as wd
from i18n import _
from local import NODATA_THRESHOLD
from ESXi_WBEM_host import _sNormDimmName
import re

# Constants
DEFAULT_SSH_PORT = 22
RE_WS = re.compile(r'\s+')
RE_BLADE = re.compile(r'blade\[\d{1,2}\]\s')
RE_COMP = re.compile(r'^(\w+)\[(\d{1,3})\]')
RE_INFOSTART = re.compile(r'system> info -T system:blade')
RE_INFOCPU = re.compile(r'info -T system:[^:]+:cpu\[\d\]')
RE_INFOMEM = re.compile(r'info -T system:[^:]+:memory\[\d{1,3}\]')
RE_INFOEXP = re.compile(r'info -T system:[^:]+:exp\[\d{1,3}\]')
RE_EMPTY = re.compile(r'^\w*$')
RE_IBM_HOST_MODEL = re.compile(r'^\[(\w+)\]$')


# Classes
class expMy_Error(Exception):
    def __init__(self, sMsg):
        self.sMsg = sMsg
        return

    def __repr__(self):
        return self.sMsg


class expAMM_Error(expMy_Error):
    def __init__(self, sMsg):
        super().__init__(sMsg)


class expAMM_NoAnswer(expAMM_Error):
    def __init__(self, sMsg):
        super().__init__(sMsg)


class expWBEM_Error(expMy_Error):
    def __init__(self, sMsg):
        super().__init__(sMsg)

oLog = logging.getLogger(__name__)


class BladeWithAMM(inv.GenericServer):
    def __init__(self, sFQDN, sAMM_Name, **dParams):
        """sAMM_Name is a name of the server in AMM"""
        super().__init__(sFQDN, IP=dParams.get('IP', None))
        self.sAmmName = sAMM_Name
        self.sVCenter = dParams.get('vCenter')
        self.sUser = dParams.get('User')
        self.sPass = dParams.get('Pass')
        self.sSpUser = dParams.get('SP_User')
        self.sSpPass = dParams.get('SP_Pass')
        self.sSpIP = dParams.get('AMM_IP')
        self.oTriggers = None
        self.sSerialNum = ''
        self.sBladeNum = ''
        self.iTotalRAMgb = 0
        self.iDIMMs = 0
        self.iCPUs = 0
        self.lDIMMs = []
        self.lCPUs = []
        self.lExps = []
        self.lDisks = []
        return

    def _FillData(self):
        """Fill inventory data from all the sources.
        WBEM has problems so fill WBEM last and trap exceptions
        """
        self._FillFromAMM()
        # Disabled due to WBEM debugging
        try:
            # self.loAvailableNameSpaces = self._loListCIM_Namespaces()
            self._FillDisksFromWBEM()
        except wd.WBEM_Disk_Exception:
            oLog.error('Error getting data from about disk subsystem via WBEM')
        except wd.WBEM_Exception:
            oLog.error('CIM error trying to collect information from server ' + self.sAmmName)
        return

    def _ConnectTriggerFactory(self, oTriggersFactory):
        self.oTriggers = oTriggersFactory
        return

    def _FillFromAMM(self):
        """connect to AMM, run a series of commands and return results"""
        oAuth = MySSH.AuthData(self.sSpUser, bUseKey=False, sPasswd=self.sSpPass)
        oAmmConn = MySSH.MySSHConnection(self.sSpIP, DEFAULT_SSH_PORT, oAuth)

        # get all blades names and some configuration
        lBladeNames = oAmmConn.fsRunCmd('list -l 2').split('\n')
        for s in lBladeNames:
            s = s.strip()
            if RE_WS.search(s) and RE_BLADE.search(s):
                sDev, sName = RE_WS.split(s, maxsplit=1)
                if self.sAmmName == sName:
                    self.sBladeNum = sDev
                    oLog.debug("Blade with name {} found, ID {}".format(
                        self.sAmmName, self.sBladeNum))
                    break
        if self.sBladeNum == '':
            oLog.error('Blade {} is not found in the enclosure'.format(self.sAmmName))
            raise expAMM_Error('Unknown blade')

        lOut = oAmmConn.fsRunCmd("info -T system:{}".format(self.sBladeNum)).split('\n')
        iterBladeInfo = iter(lOut)
        for sData in iterBladeInfo:
            # print('Checking line: ' + sData)
            if sData[:16] == 'Mach type/model:':
                self.sTypeMod = sData[17:].strip()
            if sData[:13] == 'Manufacturer:':
                self.sVendor = sData[14:].strip()
            elif sData[:19] == 'Mach serial number:':
                self.sSN = sData[20:]
            elif sData[:9] == 'Part no.:':
                self.sPN = sData[10:]
            else:
                # skip unknown lines
                pass
        # get list of child nodes (cpu & memory & exp [adapters])
        lOut = oAmmConn.fsRunCmd('list -l 2 -T system:{}'.format(self.sBladeNum)).split('\n')
        lComponents = [l.strip() for l in lOut]
        # print("\n".join(lComponents))
        self._ParseFillAMMComponents(lComponents, oAmmConn, oAuth)
        return

    def _ParseFillAMMComponents(self, lData, oConn, oAuth):
        """Parameters: list of components, connection and auth info
        Actions: groups lines of components by class and makes a list of
        each component, runs a series of commands to discover components."""
        self.dComps = {'cpu': [], 'exp': [], 'memory': []}

        def _lsCollectLines(iterData, l):
            """collects lines of iterData up to empty string in a list, returns that list.
            2nd parameter is a first line to collect (from calling function)
            """
            lBuf = []
            while not RE_EMPTY.match(l):
                lBuf.append(l)
                l = next(iterData).strip()
            return lBuf

        for s in lData:
            oMG = RE_COMP.match(s)
            if oMG:
                sClass, sNum = oMG.groups()
                if sClass in self.dComps:
                    self.dComps[sClass].append(sNum)
        self.iCPUs = len(self.dComps['cpu'])
        self.iDIMMs = len(self.dComps['memory'])
        self.iExps = len(self.dComps['exp'])
        # make a list of commands for SSH and execute these commands
        lCommands = []
        for k in sorted(self.dComps.keys()):
            for n in self.dComps[k]:
                lCommands.append('info -T system:{}:{}[{}]'.format(self.sBladeNum, k, n))
        lOutput = []
        for sCmd in lCommands:
            lOutput.extend(oConn.fsRunCmd(sCmd).split('\n'))
        iterData = it.dropwhile(lambda x: not RE_INFOSTART.match(x), lOutput)
        llCpus = []     # lists of lists (will contain groups of strings)
        llMem = []
        llExp = []
        try:
            while True:
                l = next(iterData).strip()      # one of cpu, memory, expansion or unknown
                if RE_INFOCPU.search(l):        # processing CPU data
                    llCpus.append(_lsCollectLines(iterData, l))
                elif RE_INFOMEM.search(l):      # processing memory data
                    llMem.append(_lsCollectLines(iterData, l))
                elif RE_INFOEXP.search(l):      # expansion card
                    llExp.append(_lsCollectLines(iterData, l))
                else:
                    pass   # unknown line
        except StopIteration:
            pass        # end of output

        self._FillCPUs(llCpus)
        self._FillDIMMs(llMem)
        self._FillEXPs(llExp)
        return

    def _FillCPUs(self, llData):
        """fills CPU information from a list of CPU descriptions from AMM
        (each description is a list of strings)"""
        self.lCPUs = []
        self.iTotalCores = 0
        for lsCpuDesc in llData:
            sName, sSpeed, sFamily, iCores = ("", "", "", 0)
            for s in lsCpuDesc:
                if s[:16] == 'Mach type/model:':
                    sName = s[17:]
                elif s[:17] == 'Processor family:':
                    sFamily = s[18:]
                elif s[:6] == 'Speed:':
                    sSpeed = s[7:]
                elif s[:16] == 'Processor cores:':
                    iCores = int(s[17:])
                    self.iTotalCores += iCores
                else:
                    # unknown line
                    pass
            # make CPU object
            c = Blade_CPU(sName, sSpeed, sFamily, iCores)
            c._ConnectTriggerFactory(self.oTriggers)
            self.lCPUs.append(c)
        return

    def _FillDIMMs(self, llData):
        self.lDIMMs = []
        iTotalGB = 0
        for lsCpuDesc in llData:
            sName, sPN, sSN, sType, iSizeGB = ("", "", "", "", 0)
            oLog.debug('Found DIMMs with size: ' + str(iTotalGB))
            for s in lsCpuDesc:
                if s[:16] == 'Mach type/model:':
                    sName = s[17:]
                elif s[:9] == 'Part no.:':
                    sPN = s[10:]
                elif s[:15] == 'FRU serial no.:':
                    sSN = s[16:]
                elif s[:12] == 'Memory type:':
                    sType = s[13:]
                elif s[:5] == 'Size:':
                    iSizeGB = int(s[6:].split(' ')[0])
                    iTotalGB += iSizeGB
                else:
                    # unknown line
                    pass
            # make CPU object
            d = Blade_DIMM(sName, sPN, sSN, sType, iSizeGB)
            d._ConnectTriggerFactory(self.oTriggers)
            self.lDIMMs.append(d)
            self.iTotalRAMgb = iTotalGB
        return

    def _FillEXPs(self, llData):
        self.lExps = []
        for lsExpDesc in llData:
            sName, sPN, sSN, sType = ("", "", "", "")
            for s in lsExpDesc:
                if s[:13] == 'Product Name:':
                    sType = s[14:]
                elif s[:16] == 'Mach type/model:':
                    sName = s[17:]
                elif s[:9] == 'Part no.:':
                    sPN = s[10:]
                elif s[:15] == 'FRU serial no.:':
                    sSN = s[16:]
                else:
                    # unknown line
                    pass
            # make CPU object
            a = Blade_EXP(sName, sPN, sSN, sType)
            a._ConnectTriggerFactory(self.oTriggers)
            self.lExps.append(a)
        return

    def _Connect2Zabbix(self, oAPI, oSender):
        self.oZbxAPI = oAPI
        self.oZbxSender = oSender
        self.oZbxHost = zi.ZabbixHost(self.sName, self.oZbxAPI)
        return

    def _MakeAppsItems(self):
        """Creates applications and items on Zabbix server and sends data to Zabbix"""
        # collect data
        self._FillData()
        if self.oZbxHost:
            # zabbix interface is defined
            self.oZbxHost._oAddApp('System')
            # Add items
            oMemItem = self.oZbxHost._oAddItem(
                "System Memory", sAppName='System',
                dParams={'key': zi._sMkKey("Host", self.sName, "Memory"), 'units': 'GB', 'value_type': 3,
                         'description': _('Total memory size in GB')})
            oMemItem._SendValue(self.iTotalRAMgb, self.oZbxSender)
            oCPUItem = self.oZbxHost._oAddItem(
                "System CPUs #", sAppName='System',
                dParams={'key': zi._sMkKey("Host", self.sName, "CPUs"), 'value_type': 3,
                         'description': _('Host CPU count')})
            oCPUItem._SendValue(len(self.lCPUs), self.oZbxSender)
            oCoresItem = self.oZbxHost._oAddItem(
                "System Cores #", sAppName='System',
                dParams={'key': "Host_{}_Cores".format(self.sName), 'value_type': 3,
                         'description': _('Total number of cores in the system')})
            # number of cores in all CPUs
            oCoresItem._SendValue(self.iTotalCores, self.oZbxSender)

            # Host information
            oVendorItem = self.oZbxHost._oAddItem(
                'System Vendor', sAppName='System',
                dParams={'key': '{}_Vendor'.format(self.sName), 'value_type': 1,
                         'description': _('Manufacturer of the system')})
            oMTM_Item = self.oZbxHost._oAddItem(
                'System Model', sAppName='System',
                dParams={'key': '{}_MTM'.format(self.sName), 'value_type': 1,
                         'description': _('Machine type and model as TYPE-MDL')})
            oPN_Item = self.oZbxHost._oAddItem(
                'System Part Number', sAppName='System',
                dParams={'key': '{}_PartNo'.format(self.sName), 'value_type': 1,
                         'description': _('Part Number of the system')})
            oSN_Item = self.oZbxHost._oAddItem(
                'System Serial Number', sAppName='System',
                dParams={'key': '{}_SerNo'.format(self.sName), 'value_type': 1,
                         'description': _('Serial number of system')})

            oVendorItem._SendValue(self.sVendor, self.oZbxSender)
            oMTM_Item._SendValue(self.sTypeMod, self.oZbxSender)
            oPN_Item._SendValue(self.sPN, self.oZbxSender)
            oSN_Item._SendValue(self.sSN, self.oZbxSender)

            # set up triggers if triggerFactory object exists
            if self.oTriggers is not None:
                self.oTriggers._AddChangeTrigger(oMemItem, _('Memory size changed'), 'warning')
                self.oTriggers._AddChangeTrigger(oCPUItem, _('Number of CPUs changed'), 'warning')
                self.oTriggers._AddChangeTrigger(oSN_Item, _('System SN changed'), 'average')
                self.oTriggers._AddNoDataTrigger(oSN_Item, _('Cannot receive system SN in 2 days'), 'average')

            # send components' items to Zabbix
            lComps = self.lCPUs + self.lDIMMs + self.lExps + self.lDisks
            for o in lComps:
                o._MakeAppsItems(self.oZbxHost, self.oZbxSender)
            # make a time stamp
            self.oZbxHost._MakeTimeStamp(self.oZbxSender)
            oLog.info('Finished making Zabbix items and triggers')
        else:
            oLog.error("Zabbix interface isn't initialized yet")
            raise expAMM_Error("Zabbix isn't connected yet")
        return

    def _loListCIM_Namespaces(self):
        """Retrieve list of available namespaces from the server with WBEM"""
        lRet = []
        return lRet

    def _FillDisksFromWBEM(self):
        ldDicts = []
        try:
            if self.sVCenter:
                self.oWBEM_Disks = wd.WBEM_Disks(self.sName, self.sUser, self.sPass, sVCenter=self.sVCenter)
            else:
                self.oWBEM_Disks = wd.WBEM_Disks(self.sName, self.sUser, self.sPass)
        except wd.WBEM_Disk_Exception as e:
            oLog.error(
                'WBEM error when initializing WBEM_Disks interface of server {}, msg: {}'.format(
                    self.sName, str(e)))
            ldDicts = []
            raise(e)
        try:
            ldDicts = self.oWBEM_Disks._ldReportDisks()
        except wd.WBEM_Disk_Exception as e:
            oLog.error('WBEM error when collecting information: ' + self.sName)
            raise(e)

        for dDiskData in ldDicts:
            # if previous try-except clause throws an exception, ldDicts will be empty
            try:
                iSizeGB = int(dDiskData['MaxMediaSize']) // 2**20   # WBEM returns size in KB
                self.lDisks.append(
                    Blade_Disk(dDiskData['Name'], dDiskData['Model'], dDiskData['PartNumber'],
                               dDiskData['SerialNumber'], iSizeGB))
            except KeyError as e:
                oLog.error('Error accessing disk data: {}'.format(e))
                raise expWBEM_Error('Error accessing disk data: {}'.format(e))
        oLog.debug("_FillDisksFromWBEM: {} disks found".format(len(self.lDisks)))
        return

    def _FillSysFromWBEM(self):
        if self.sVCenter:
            self.oWBEM_Sys = wd.WBEM_System(self.sName, self.sUser, self.sPass, sVCenter=self.sVCenter)
        else:
            self.oWBEM_Sys = wd.WBEM_System(self.sName, self.sUser, self.sPass)
        dDict = self.oWBEM_Sys._dGetInfo()
        print("\n".join([str(d) for d in dDict.items()]))
        oMatchModel = RE_IBM_HOST_MODEL.match(dDict.get('model'))
        if oMatchModel:
            sModel = oMatchModel.group(1)
            oLog.debug("Server model: " + sModel)
        return


class Blade_CPU(inv.ComponentClass):
    def __init__(self, sName, sSpeed, sFamily, iCores):
        self.sName = sName
        super().__init__(sName)
        # self.oTriggers = None
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
            dParams={'key': zi._sMkKey(oZbxHost._sName(), self.sName, 'Type'),
                     'value_type': 1, 'description': _('Type of the processor')})
        oCoresItem = oZbxHost._oAddItem(
            self.sName + " # Cores", sAppName=self.sName,
            dParams={'key': zi._sMkKey(oZbxHost._sName(), self.sName, "Cores"),
                     'value_type': 3, 'description': _('Number of cores')})
        oSpeedItem = oZbxHost._oAddItem(
            self.sName + " Speed", sAppName=self.sName,
            dParams={'key': zi._sMkKey(oZbxHost._sName(), self.sName, "Speed"),
                     'value_type': 1, 'description': _('CPU Clock speed')})

        if self.oTriggers:
            self.oTriggers._AddNoDataTrigger(oTypeItem, _('Cannot determine processor type'),
                                             'average', NODATA_THRESHOLD)
        oTypeItem._SendValue(self.dData['family'], oZbxSender)
        oCoresItem._SendValue(self.dData['cores'], oZbxSender)
        oSpeedItem._SendValue(self.dData['speed'], oZbxSender)
        return


class Blade_DIMM(inv.ComponentClass):
    def __init__(self, sName, sPN, sSN, sType, iSizeGB):
        self.sName = _sNormDimmName(sName)
        super().__init__(sName, sSN)
        self.dData = {'pn': sPN, 'sn': sSN, 'type': sType, 'size_gb': iSizeGB}
        return

    def __repr__(self):
        return ("{0}: {1}-GB {2} Module: pn {3}, sn {4}".format(
            self.sName, self.dData['size_gb'], self.dData['type'], self.dData['pn'], self.dData['sn']))

    def _MakeAppsItems(self, oZbxHost, oZbxSender):
        """Make applications, items and send values to Zabbix"""
        oZbxHost._oAddApp(self.sName)     # DIMM XX
        oTypeItem = oZbxHost._oAddItem(
            self.sName + " Type", sAppName=self.sName,
            dParams={'key': zi._sMkKey(oZbxHost._sName(), self.sName, "Type"),
                     'value_type': 1, 'description': _('Memory module type')})
        oPN_Item = oZbxHost._oAddItem(
            self.sName + " Part Number", sAppName=self.sName,
            dParams={'key': zi._sMkKey(oZbxHost._sName(), self.sName, "PN"),
                     'value_type': 1, 'description': _('DIMM part number')})
        oSN_Item = oZbxHost._oAddItem(
            self.sName + " Serial Number", sAppName=self.sName,
            dParams={'key': zi._sMkKey(oZbxHost._sName(), self.sName, "SN"),
                     'value_type': 1, 'description': _('DIMM serial number')})
        oSize_Item = oZbxHost._oAddItem(
            self.sName + " Size", sAppName=self.sName,
            dParams={'key': zi._sMkKey(oZbxHost._sName(), self.sName, "SizeGB"),
                     'value_type': 3, 'units': 'GB', 'description': _('DIMM size in GB')})
        if self.oTriggers:
            self.oTriggers._AddChangeTrigger(oSN_Item, _('DIMM serial number is changed'), 'average')
            self.oTriggers._AddNoDataTrigger(oSN_Item, _("Can't receive DIMM S/N for two days"),
                                             'average', NODATA_THRESHOLD)
        oTypeItem._SendValue(self.dData['type'], oZbxSender)
        oPN_Item._SendValue(self.dData['pn'], oZbxSender)
        oSN_Item._SendValue(self.dData['sn'], oZbxSender)
        oSize_Item._SendValue(self.dData['size_gb'], oZbxSender)
        return


class Blade_EXP(inv.ComponentClass):
    def __init__(self, sName, sPN, sSN, sType):
        self.sName = sName
        super().__init__(sName, sSN)
        self.dData = {'sn': sSN, 'pn': sPN, 'type': sType}
        return

    def __repr__(self):
        return ("{0}: {3} (pn {1}, sn {2})".format(
            self.sName, self.dData['pn'], self.dData['sn'], self.dData['type']))

    def _MakeAppsItems(self, oZbxHost, oZbxSender):
        oZbxHost._oAddApp(self.sName)     # Fibre Channel EC
        # XXX there will be problems when we have more than one similar expansion cards XXX
        oTypeItem = oZbxHost._oAddItem(
            self.sName + " Type", sAppName=self.sName,
            dParams={'key': "{}_{}_Type".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'value_type': 1, 'description': _('Adapter card type')})
        oPN_Item = oZbxHost._oAddItem(
            self.sName + " Part Number", sAppName=self.sName,
            dParams={'key': "{}_{}_PN".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'value_type': 1, 'description': _('Adapter card part number')})
        oSN_Item = oZbxHost._oAddItem(
            self.sName + " Serial Number", sAppName=self.sName,
            dParams={'key': "{}_{}_SN".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'value_type': 1, 'description': _('Adapter card serial number')})
        if self.oTriggers:
            self.oTriggers._AddChangeTrigger(oSN_Item, _('Serial # of expansion card changed'), 'warning')
            self.oTriggers._AddNoDataTrigger(oSN_Item, _("Can't determine S/N of expansion card"),
                                             'average', NODATA_THRESHOLD)
        oTypeItem._SendValue(self.dData['type'], oZbxSender)
        oPN_Item._SendValue(self.dData['pn'], oZbxSender)
        oSN_Item._SendValue(self.dData['sn'], oZbxSender)
        return


class Blade_Disk(inv.ComponentClass):
    def __init__(self, sName, sModel, sPN, sSN, iSizeGB):
        super().__init__(sName, sSN)
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
        if self.oTriggers:
            self.oTriggers._AddChangeTrigger(oSN_Item, _('Disk serial number is changed'), 'warning')
            self.oTriggers._AddNoDataTrigger(oSN_Item, _('Cannot receive disk serial number in two days'),
                                             'average', NODATA_THRESHOLD)
        oModelItem._SendValue(self.dDiskData['model'], oZbxSender)
        oPN_Item._SendValue(self.dDiskData['pn'], oZbxSender)
        oSN_Item._SendValue(self.dDiskData['sn'], oZbxSender)
        oSize_Item._SendValue(self.dDiskData['size'], oZbxSender)
        return


if __name__ == '__main__':
    """testing section"""
    from access import vmsrv06 as srv

    oLog.setLevel(logging.DEBUG)
    oConHdr = logging.StreamHandler()
    oConHdr.setLevel(logging.DEBUG)
    oLog.addHandler(oConHdr)
    oAmm = BladeWithAMM(srv.sHostLong, srv.sHostShort, IP=srv.sHostLong, User=srv.sUser,
                        Pass=srv.sPass, vCenter=srv.sVCenter, SP_User=srv.sSPUser,
                        SP_Pass=srv.sSPPass, AMM_IP=srv.sSPIP)
    oAmm._FillData()
    # lOut = oAmm._lsFromAMM([])
    # print("\n".join(lOut))

# vim: expandtab : softtabstop=4 : tabstop=4 : shiftwidth=4 : autoindent
