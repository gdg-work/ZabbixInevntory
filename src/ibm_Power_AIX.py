#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Interface with Power architecture hosts, AIX and HMC"""
import inventoryObjects as inv
import MySSH
import logging
import csv    # because commands output use quoted fields
import zabbixInterface as zi
import itertools as it
from i18n import _
from serversDisk import Disk_Drive

from local import NODATA_THRESHOLD
import re

oLog = logging.getLogger(__name__)

DEFAULT_SSH_PORT = 22
RE_HDISK = re.compile(r'^\s*hdisk\d+\s')
RE_PWRSUPPLY = re.compile(r'^\s*A IBM AC PS\s*:$')
RE_WS = re.compile(r'\s+')
RE_DOTS = re.compile(r'\.\.+')
RE_RAM_MODULE = re.compile(r'\s*Memory DIMM:$')
RE_CPU_TYPE = re.compile(r'^Processor Type:\s(.*)$')
RE_CPU_FREQ = re.compile(r'^Processor Clock Speed:\s(.*)$')


class expHMC_Error(Exception):
    def __init__(self, sMsg):
        self.sMsg = sMsg
        return

    def __repr__(self):
        return self.sMsg


class expAIX_Error(expHMC_Error):
    def __init__(self, sMsg):
        super().__init__(sMsg)


class expHMC_NoAnswer(expHMC_Error):
    def __init__(self, sMsg):
        super().__init__(sMsg)


class expHMC_InvalidFormat(expHMC_Error):
    def __init__(self, sMsg):
        super().__init__(sMsg)


class expAIX_NoAnswer(expHMC_Error):
    def __init__(self, sMsg):
        super().__init__(sMsg)


class PowerHostClass(inv.GenericServer):
    def __init__(self, sName, **dFields):
        """dParams MUST contain some fields like IP, Login, Password etc"""
        oLog.debug("PowerHostClass constructor called with params: " + str(dFields))
        super().__init__(sName, IP=dFields.get('IP'))
        self.sUser = dFields.get('User')
        self.sPass = dFields.get('Pass')
        self.sSpUser = dFields.get('SP_User')
        self.sSpPass = dFields.get('SP_Pass')
        self.sHmcIP = dFields.get('HMC_IP')
        self.oTriggers = None
        self.sSerialNum = ''
        self.oAdapters = inv.AdaptersList()
        self.lDisks = []
        self.lPwrSupplies = []
        self.lDIMMs = []
        self.oZbxHost = None
        # print(self)
        return

    def _ConnectTriggerFactory(self, oTriggersFactory):
        # oLog.debug('Connecting triggers factory {} to host {}'.format(str(oTriggersFactory), self.sName))
        self.oTriggers = oTriggersFactory
        return

    def _sFromHMC(self, sCommand):
        """connect to HMC, run a command and return results"""
        oAuth = MySSH.AuthData(self.sSpUser, bUseKey=False, sPasswd=self.sSpPass)
        oHmcConn = MySSH.MySSHConnection(self.sHmcIP, DEFAULT_SSH_PORT, oAuth)
        sFromCmd = oHmcConn.fsRunCmd(sCommand)
        # oLog.debug('_sFromHMC: output of command "{}": "{}"'.format(
        #            sCommand, sFromCmd))
        if not sFromCmd:
            oLog.error('Cannot receive data from HMC, check the server\'s name IN HMC')
            oLog.error('HMC output: ' + sFromCmd)
            raise expHMC_NoAnswer("No answer from HMC")
        return sFromCmd

    def _sFromHost(self, sCommand):
        """Connect to AIX host, run a command and return results"""
        oAuth = MySSH.AuthData(self.sUser, bUseKey=False, sPasswd=self.sPass)
        oAIXConn = MySSH.MySSHConnection(self.sIP, DEFAULT_SSH_PORT, oAuth)
        sFromCmd = oAIXConn.fsRunCmd(sCommand)
        # oLog.debug('_sFromHost: output of command "{}": "{}"'.format(
        #            sCommand, sFromCmd))
        if not sFromCmd:
            oLog.error('Cannot receive data from AIX host, check the connection info')
            oLog.error('Command string: {}, output: {}'.format(sCommand, sFromCmd))
            raise expAIX_NoAnswer("No answer from OS SSH")
        return sFromCmd

    def _Fill_HMC_Data(self):

        def _dDrcName_to_ID(lAdapterNames):
            """convert DRC name from format like "U789D.001.DQD81N6-P1-C2" to something like
            "box1-P1-C2"""
            dNamesByBoxes = {}
            for sName in lAdapterNames:
                sBoxID = sName.split('-', maxsplit=1)[0]
                if sBoxID in dNamesByBoxes:
                    dNamesByBoxes[sBoxID].append(sName)
                else:
                    dNamesByBoxes[sBoxID] = [sName]
            # now we have we have names arranged by boxes. Let's sort and rename the boxes.
            lBoxNames = list(dNamesByBoxes.keys())
            lBoxNames.sort()
            dResult = {}
            for iBoxNum in range(0, len(lBoxNames)):
                lItems = dNamesByBoxes[lBoxNames[iBoxNum]]
                for i in lItems:
                    sSuffix = "".join(i.split('-')[-2:])
                    dResult[i] = "Bx{}-{}".format(iBoxNum + 1, sSuffix)
            return dResult

        oAuth = MySSH.AuthData(self.sSpUser, bUseKey=False, sPasswd=self.sSpPass)
        oHmcConn = MySSH.MySSHConnection(self.sHmcIP, DEFAULT_SSH_PORT, oAuth)
        # Memory
        sMemCmd = 'lshwres -r mem -m {} --level sys -F installed_sys_mem'.format(self.sName)
        # Processors
        sProcCmd = 'lshwres -r proc -m {} --level sys -F installed_sys_proc_units'.format(self.sName)
        # SN, MTM, etc
        lFields = ['type_model', 'ipaddr', 'serial_num']
        sSysCmd = 'lssyscfg -r sys -m {0} -F {1}'.format(self.sName, ','.join(lFields))
        # list of adapters
        lFields = ['unit_phys_loc', 'description', 'drc_name', 'bus_id']
        sAdaptersCmd = 'lshwres -r io -m {0} --rsubtype slot -F {1}'.format(
            self.sName, ','.join(lFields))
        lOut = oHmcConn._lsRunCommands([sMemCmd, sProcCmd, sSysCmd, sAdaptersCmd])
        oLog.debug("Memory cfg:" + str(lOut))
        self.iMemGBs = int(lOut[0]) // 1024
        self.iTotalCores = int(float(lOut[1]))
        sTM, self.sHmcIP, self.sSerialNum = lOut[2].split(',')
        self.sType, self.sModel = sTM.split('-')
        oLog.debug('_Fill_HMC_Data: Mem:{0}, CPU:{1}, MTM:{2}-{3}, SN:{4}'.format(
            self.iMemGBs, self.iTotalCores, self.sType, self.sModel, self.sSerialNum))
        # adapters
        iterData = csv.DictReader(lOut[3].split('\n'), fieldnames=lFields)
        lAdapters = []
        for d in iterData:
            if d['description'] == 'Empty slot':
                pass        # skip empty slots
            else:
                lAdapters.append(d)
        # now we have a list of dictionaries with adapters data, but we need to alter our BUS IDs
        # so there will be no duplicates. I think the good format will be as in RAM, box-id.
        # drc_name is of kind: U789D.001.DQD81N6-P1-C2, 'DQD81N6' is a s/n of the box.
        dConversion = _dDrcName_to_ID([a['drc_name'] for a in lAdapters])
        for d in lAdapters:
            # oLog.debug('Current self.oAdapters content: ' + str(self.oAdapters.items()))
            oAdapter = IBM_Power_Adapter(d['description'], dConversion[d['drc_name']], d['drc_name'])
            oAdapter._ConnectTriggerFactory(self.oTriggers)
            self.oAdapters._append(oAdapter)
        return

    def _FillFromAIX(self):
        sRet = self._sFromHost('lscfg -vp')
        # oLog.info("_FillFromAIX: lscfg output is {}".format(sRet))
        lsCfgData = sRet.split('\n')
        self._FillDisks(lsCfgData)
        self._FillPwrSupplies(lsCfgData)
        self._FillDIMMs(lsCfgData)
        # Processor information from 'prtconf' output
        sRet = self._sFromHost('prtconf').split('\n')
        sProcType = ''
        for sLine in sRet:
            oMatch = RE_CPU_TYPE.match(sLine)
            if oMatch:
                sProcType = oMatch.group(1)
            oMatch = RE_CPU_FREQ.match(sLine)
            if oMatch:
                sFreq = oMatch.group(1)
        self.sProcType = "{0} at {1}".format(sProcType, sFreq)
        oLog.debug('Processor type: ' + self.sProcType)
        for oElem in self.lDisks + self.lDIMMs + self.lPwrSupplies:
            # oLog.debug('Connecting trigger factory to element: ' + str(oElem))
            oElem._ConnectTriggerFactory(self.oTriggers)
        return

    def _FillDisks(self, lsCfgData):
        """extract disks information from a list of strings and fills in diskList object"""
        iterDiskData = it.dropwhile(lambda x: not RE_HDISK.match(x), lsCfgData)
        try:
            while True:
                sPN = ''                # no P/N on non-local drives
                bIsLocal = False        # reset variable
                iterDiskData = it.dropwhile(lambda x: not RE_HDISK.match(x), iterDiskData)
                # we are at first line of disk's description. Let's parse it.
                sL1 = next(iterDiskData).strip()
                oLog.debug('_FillDisks: 1st line is {}'.format(sL1))
                sDskName, sHWLoc, sDesc = RE_WS.split(sL1, maxsplit=2)
                sL = '--------'   # initialize loop variable
                bInDiskDescription = False
                while not(bInDiskDescription and sL == ''):
                    if sL[:13] == 'Serial Number':
                        # 'Serial Number...............6XN42PQM':
                        sSN = RE_DOTS.split(sL)[1]
                        # oLog.debug('Disk {} S/N is {}'.format(sDskName, sSN))
                    if sL == '':    # first empty string
                        bInDiskDescription = True
                    elif sL[:11] == 'Part Number':
                        # Part Number.................74Y6486
                        sPN = RE_DOTS.split(sL)[1]
                        # oLog.debug('Disk {} P/N is {}'.format(sDskName, sPN))
                    elif sL[:22] == 'Machine Type and Model':
                        # Machine Type and Model......ST9300653SS
                        sModel = RE_DOTS.split(sL)[1]
                        # oLog.debug('Disk {} MTM is {}'.format(sDskName, sModel))
                    elif sL[:22] == 'Hardware Location Code':  # this line finishes the disk description
                        bIsLocal = True
                        oLog.debug("HW LC line found, end of disk data")
                    else:
                        # non-interesting line
                        pass
                    sL = next(iterDiskData).strip()
                    # end while not...
                # oLog.debug('Disk found: {} at {}, pn {}, sn {}, model {}'.format(
                #     sDskName, sHWLoc, sPN, sSN, sModel))

                # create Disk object
                if bIsLocal:
                    self.lDisks.append(IBM_Power_Disk(sDskName, sDesc, sModel, sPN, sSN, sHWLoc))
                continue   # out of never-ending While cycle
        except StopIteration:
            # end of list, no more disks
            pass
        return

    def _FillPwrSupplies(self, lsCfgData):
        """ Fills power supplies list from output of 'lscfg -vp' saved in a list of strings """
        iterPSData = it.dropwhile(lambda x: not RE_PWRSUPPLY.match(x), lsCfgData)
        self.iPwrSupplies = 0
        try:
            while True:
                sPN = ''                # no P/N on non-local drives
                iterPSData = it.dropwhile(lambda x: not RE_PWRSUPPLY.match(x), iterPSData)
                # we are at first line of disk's description. Let's parse it.
                # sL1 = next(iterPSData).strip()
                next(iterPSData)
                self.iPwrSupplies += 1
                # oLog.debug('_FillPwrSupply: 1st line is {}'.format(sL1))
                sName = 'Power Supply {}'.format(self.iPwrSupplies)
                sL = '--------'   # initialize loop variable
                while sL != '':        # empty line is end of PS record
                    sL = next(iterPSData).strip()
                    if sL[:22] == "Hardware Location Code":
                        sHWLoc = RE_DOTS.split(sL)[1]
                    elif sL[:13] == "Serial Number":
                        sSN = RE_DOTS.split(sL)[1]
                    elif sL[:11] == "Part Number":
                        sPN = RE_DOTS.split(sL)[1]
                    else:
                        pass   # skip unknown lines
                # create PwrSupply object
                self.lPwrSupplies.append(IBM_Power_Supply(sName, sPN, sSN, sHWLoc))
                continue   # while true
        except StopIteration:
            # end of lscfg output, no more Power Supplies
            pass
        return

    def _FillDIMMs(self, lsCfgData):
        """Fills RAM modules information from 'lscfg -vp' output stored in lsCfgData list"""
        iterDIMMsData = it.dropwhile(lambda x: not RE_RAM_MODULE.match(x), lsCfgData)
        dDIMMs = {}
        self.iDIMMs = 0
        try:
            while True:
                sHWLoc, sName, sSN, sPN, iSize = ('', '', '', '', 0)   # empty variables
                iterDIMMsData = it.dropwhile(lambda x: not RE_RAM_MODULE.match(x), iterDIMMsData)
                # we are at first line of disk's description. Let's parse it.
                # sL1 = next(iterDIMMsData).strip()
                next(iterDIMMsData)
                # oLog.debug('_FillDIMMs: 1st line is {}'.format(sL1))
                self.iDIMMs += 1
                sL = '--------'   # initialize loop variable
                while sL != '':
                    sL = next(iterDIMMsData).strip()
                    if sL[:22] == "Hardware Location Code":
                        sHWLoc = RE_DOTS.split(sL)[1]
                        sName = 'RAM Module {}'.format(sHWLoc.split('.')[-1])
                    elif sL[:13] == "Serial Number":
                        sSN = RE_DOTS.split(sL)[1]
                    elif sL[:11] == "Part Number":
                        sPN = RE_DOTS.split(sL)[1]
                    elif sL[:6] == "Size..":
                        iSize = int(RE_DOTS.split(sL)[1]) // 1024
                    else:
                        pass   # skip unknown lines
                # collect all the information to one data structure
                dDIMM_Dict = {'SN': sSN, 'PN': sPN, 'Loc': sHWLoc, 'Size': iSize}
                dDIMMs[sName] = dDIMM_Dict
                continue   # while true
        except StopIteration:
            # end of lscfg output, no more DIMMs
            pass

        # now dDIMMs dictionary contains our information, but the
        # dictionary's key is not perfect for Zabbix item name, we need to
        # shorten it and remove uniqueness linked with usage of box S/N in
        # DIMM position. First, we need to arrange modules by boxes
        dDimmsByBoxes = {}
        for sName, dValue in dDIMMs.items():
            sBoxName, sOther = sName.split('-', maxsplit=1)
            # if adding a first element, create a dictionary
            if dDimmsByBoxes.get(sBoxName, None) is None:
                dDimmsByBoxes[sBoxName] = {sOther: dValue}
            else:
                dDimmsByBoxes[sBoxName][sOther] = dValue
        # Now (hopefully) all DIMMs are grouped by a box. Just sort and number these boxes
        lBoxNames = list(dDimmsByBoxes.keys())
        lBoxNames.sort()        # <-- in place
        for iBoxNum in range(0, len(lBoxNames)):
            dInBox = dDimmsByBoxes[lBoxNames[iBoxNum]]
            for sOther, dValue in dInBox.items():
                sName = "Box{}-{}".format(iBoxNum + 1, sOther)
                oDIMM = IBM_DIMM_Module(sName, dValue['PN'], dValue['SN'], dValue['Loc'],
                                        dValue['Size'])
                # oLog.debug('DIMM object created: ' + str(oDIMM))
                self.lDIMMs.append(oDIMM)
        return

    def _Connect2Zabbix(self, oAPI, oSender):
        self.oZbxAPI = oAPI
        self.oZbxSender = oSender
        self.oZbxHost = zi.ZabbixHost(self.sName, self.oZbxAPI)
        return

    def _MakeAppsItems(self):
        """Make objects in Zabbix, requesting the data from AIX"""
        self._Fill_HMC_Data()
        self._FillFromAIX()
        if self.oZbxHost:
            # zabbix interface is defined
            self.oZbxHost._oAddApp('System')
            # Add items
            oMemItem = self.oZbxHost._oAddItem(
                "System Memory", sAppName='System',
                dParams={'key': "Host_{}_Memory".format(self.sName), 'units': 'GB', 'value_type': 3,
                         'description': _('Total memory size in GB')})
            oMemItem._SendValue(self.iMemGBs, self.oZbxSender)
            self.oTriggers._AddChangeTrigger(oMemItem, _('Memory size changed'), 'warning')
            oCPUTypeItem = self.oZbxHost._oAddItem(
                "CPU Type", sAppName='System',
                dParams={'key': zi._sMkKey("CPU", "Type"),
                         'description': _('Processor type')})
            oCPUTypeItem._SendValue(self.sProcType, self.oZbxSender)
            oCPUCoresItem = self.oZbxHost._oAddItem(
                "System Total Cores", sAppName='System',
                dParams={'key': "Host_{}_Cores".format(self.sName), 'value_type': 3,
                         'description': _('Total amount of cores in all CPUs')})
            oCPUCoresItem._SendValue(self.iTotalCores, self.oZbxSender)
            # self.sType, self.sModel
            oTypeItem = self.oZbxHost._oAddItem(
                "System Type", sAppName='System',
                dParams={'key': "Host_{}_Type".format(self.sName),
                         'description': _('Type of system (4-symbol code)')})
            oTypeItem._SendValue(self.sType, self.oZbxSender)
            oModelItem = self.oZbxHost._oAddItem(
                "System Model", sAppName='System',
                dParams={'key': "Host_{}_Model".format(self.sName),
                         'description': _('Model of the system')})
            oModelItem._SendValue(self.sModel, self.oZbxSender)
            oSN_Item = self.oZbxHost._oAddItem(
                "System Serial Number", sAppName='System',
                dParams={'key': "Host_{}_Serial".format(self.sName),
                         'description': _('Serial number of system')})
            oSN_Item._SendValue(self.sSerialNum, self.oZbxSender)
            self.oTriggers._AddChangeTrigger(oSN_Item, _('System SN changed'), 'average')
            self.oTriggers._AddNoDataTrigger(oSN_Item, _('Cannot receive system SN in 2 days'),
                                             'average', NODATA_THRESHOLD)
            oTotPS_Item = self.oZbxHost._oAddItem(
                "System Pwr Supplies", sAppName='System',
                dParams={'key': "Host_{}_NPwrSupplies".format(self.sName), 'value_type': 3,
                         'description': _('Number of power supplies')})
            oTotPS_Item._SendValue(self.iPwrSupplies, self.oZbxSender)
            self.oTriggers._AddChangeTrigger(oTotPS_Item, _('Number of Power Supplies changed'), 'warning')
            oTotDIMMs_Item = self.oZbxHost._oAddItem(
                "System DIMMs #", sAppName='System',
                dParams={'key': "Host_{}_NDIMMs".format(self.sName), 'value_type': 3,
                         'description': _('Number of memory modules')})
            oTotDIMMs_Item._SendValue(self.iDIMMs, self.oZbxSender)
            self.oTriggers._AddChangeTrigger(oTotDIMMs_Item, _('Number of Memory DIMMs changed'), 'warning')
            # Adapters, disks, PS, etc.
            for oObj in (list(self.oAdapters.values()) + self.lDisks + self.lPwrSupplies + self.lDIMMs):
                oObj._MakeAppsItems(self.oZbxHost, self.oZbxSender)
            self.oZbxHost._MakeTimeStamp(self.oZbxSender)
        else:
            oLog.error("Zabbix interface isn't initialized yet")
            raise expHMC_Error("Zabbix isn't connected yet")
        return


class IBM_Power_Adapter(inv.ComponentClass):

    def __init__(self, sName, sBusID, sLocation):
        self.sName = sName
        self.sBusID = sBusID
        self.sLocation = sLocation
        self.oTriggers = None
        return

#     def _ConnectTriggerFactory(self, oTriggersFactory):
#         # oLog.debug('Connecting triggers factory to Adapter {}'.format(self.sName))
#         self.oTriggers = oTriggersFactory
#         return

    def __repr__(self):
        return str('Adapter: type:{0}, Bus ID:{1}, location:{2}'.format(
            self.sName, self.sBusID, self.sLocation))

    def _dGetDataAsDict(self):
        return {'name': self.sName, 'bus_id': self.sBusID, 'location': self.sLocation}

    def _sGetName(self):
        return self.sName

    @property
    def busId(self):
        return self.sBusID

    def _MakeAppsItems(self, oZbxHost, oZbxSender):
        sAppName = "Adapter " + self.sBusID
        oZbxHost._oAddApp(sAppName)
        oNameItem = oZbxHost._oAddItem(
            sAppName + " Type", sAppName,
            dParams={'key': zi._sMkKey(self.sBusID, oZbxHost.name, 'Type'),
                     'description': _('Type of adapter')})
        oNameItem._SendValue(self.sName, oZbxSender)
        oPosItem = oZbxHost._oAddItem(
            sAppName + " Position", sAppName,
            dParams={'key': zi._sMkKey(self.sBusID, oZbxHost._sName(), 'Pos'),
                     'description': _('Position of adapter in the machine')})
        oPosItem._SendValue(self.sLocation, oZbxSender)
        if self.oTriggers is not None:
            # oLog.debug('Adding change trigger to adapter: ' + self.sBusID)
            self.oTriggers._AddChangeTrigger(oNameItem, _('Adapter type changed'), 'warning')
        return


class IBM_Power_Disk(Disk_Drive):
    """A disk drive of Power server, inherited from Server_Disk and modified"""
    def __init__(self, sName, sType, sModel, sPN, sSN, sLoc):
        sName = "Disk " + sName
        super().__init__(sName, sModel, sPN, sSN, iSizeGB=0)
        self.dDiskData['type'] = sType
        self.dDiskData['location'] = sLoc
        oLog.warning("IBM_Power_Disk.init: self.dDiskData=" + str(self.dDiskData))
        return

    def _MakeAppsItems(self, oZbxHost, oZbxSender):
        oLog.debug('IBM_Pwr_Dsk._MkAppsItems, self = ' + str(self))
        sAppName = self.sName
        super()._MakeAppsItems(oZbxHost, oZbxSender)

        oTypeItem = oZbxHost._oAddItem(self.sName + " Type", sAppName=sAppName,
                dParams={'key': zi._sMkKey(oZbxHost._sName(), self.sName, "Type"),
                         'value_type': 1, 'description': _('Disk type')})
        oLocationItem = oZbxHost._oAddItem(self.sName + " Location", sAppName=sAppName,
                dParams={'key': zi._sMkKey(oZbxHost._sName(), self.sName, "Location"),
                         'value_type': 1, 'description': _('Disk location')})
        oTypeItem._SendValue(self.dDiskData['type'], oZbxSender)
        oLocationItem._SendValue(self.dDiskData['location'], oZbxSender)
        return


class IBM_Power_Supply(inv.ComponentClass):
    dDescriptions = {
        'Part Number': _('Power supply part number'),
        'Serial Number': _('Power supply serial number'),
        'HW Location': _('Power supply location')}

    def __init__(self, sName, sPN, sSN, sHWLoc):
        self.sName = sName
        self.dData = {'Part Number': sPN,
                      'Serial Number': sSN,
                      'HW Location': sHWLoc}
        return

    def _ConnectTriggerFactory(self, oTriggersFactory):
        # oLog.debug('Connecting triggers factory to Power Supply {}'.format(self.sName))
        self.oTriggers = oTriggersFactory
        return

    def _MakeAppsItems(self, oZbxHost, oZbxSender):
        sAppName = self.sName               # 'Power supply {}'
        oZbxHost._oAddApp(sAppName)
        # all parameters are strings, so we can use loop
        for sN, sV in self.dData.items():
            # oLog.debug('Parameter name:{}, value:{}'.format(sN, sV))
            sItemName = sAppName + ' ' + sN
            sItemKey = zi._sMkKey(sAppName, oZbxHost._sName(), sN)
            oItem = oZbxHost._oAddItem(sItemName, sAppName,
                                       dParams={'key': sItemKey, 'description': self.dDescriptions[sN]})
            # oLog.debug('IBM_Power_Supply._MakeAppsItems: created item is ' + str(oItem))
            oItem._SendValue(sV, oZbxSender)
            if sN == 'Serial Number' and self.oTriggers is not None:
                # add trigger for changed SN and for no data
                self.oTriggers._AddChangeTrigger(oItem, _('Power supply serial number is changed'), 'warning')
                self.oTriggers._AddNoDataTrigger(
                    oItem, _('Cannot receive power supply serial number in two days'),
                    'warning', NODATA_THRESHOLD)
        return


class IBM_DIMM_Module(inv.ComponentClass):
    dDescriptions = {
        'Part Number': _('DIMM part number'),
        'Serial Number': _('DIMM serial number'),
        'HW Location': _('DIMM location')}

    def __init__(self, sName, sPN, sSN, sHWLoc, iSize):
        self.sName = 'RAM module ' + sName
        self.dStrData = {'Part Number': sPN,
                         'Serial Number': sSN,
                         'HW Location': sHWLoc}
        self.iSize = iSize
        return

#     def _ConnectTriggerFactory(self, oTriggersFactory):
#         # oLog.debug('Connecting triggers factory to DIMM {}'.format(self.sName))
#         self.oTriggers = oTriggersFactory
#         return

    def __repr__(self):
        return str('DIMM module: name:{0}, Serial:{1} at HW Loc:{2}'.format(
            self.sName, self.dStrData['Serial Number'], self.dStrData['HW Location']))

    def _MakeAppsItems(self, oZbxHost, oZbxSender):
        sAppName = self.sName               # 'RAM Module #######-P#-C## {}'
        oZbxHost._oAddApp(sAppName)
        # all parameters are strings, so we can use loop
        for sN, sV in self.dStrData.items():
            # oLog.debug('Parameter name:{}, value:{}'.format(sN, sV))
            sItemName = sAppName + ' ' + sN
            sItemKey = zi._sMkKey(sAppName, oZbxHost._sName(), sN)
            oItem = oZbxHost._oAddItem(
                sItemName, sAppName,
                dParams={'key': sItemKey, 'description': self.dDescriptions[sN]})
            # oLog.debug('IBM_DIMM_Module._MakeAppsItems: created item is ' + str(oItem))
            oItem._SendValue(sV, oZbxSender)
            if sN == 'Serial Number' and self.oTriggers is not None:
                # add trigger for changed SN and for no data
                self.oTriggers._AddChangeTrigger(oItem, _('DIMM serial number is changed'), 'warning')
                self.oTriggers._AddNoDataTrigger(oItem, _("Can't receive DIMM S/N for two days"),
                                                 'warning', NODATA_THRESHOLD)
        # and integer item: size
        sItemName = sAppName + ' Size'
        sItemKey = '{}_of_{}_{}'.format(
            sAppName, oZbxHost._sName(), 'Size').replace(' ', '_')
        oItem = oZbxHost._oAddItem(sItemName, sAppName,
                                   dParams={'key': sItemKey,
                                            'description': _('Size of memory unit in GiB'),
                                            'value_type': 3, 'units': 'GB'})
        # oLog.debug('IBM_DIMM_Module._MakeAppsItems: created item is ' + str(oItem))
        oItem._SendValue(self.iSize, oZbxSender)
        return


if __name__ == '__main__':
    # access for a test system
    from access import midgard as tsys
    from access import zabbixAtProtek as zbx
    import pyzabbix.api
    import pyzabbix.sender
    from serversDisk import oLog as srvLog

    # logging setup
    oLog.setLevel(logging.DEBUG)
    srvLog.setLevel(logging.DEBUG)
    oConHdr = logging.StreamHandler()
    oConHdr.setLevel(logging.DEBUG)
    oLog.addHandler(oConHdr)
    srvLog.addHandler(oConHdr)

    # connect to test server
    oTestHost = PowerHostClass(tsys.name, User=tsys.user, Pass=tsys.passwd, SP_User=tsys.sp_user,
                               SP_Pass=tsys.sp_pass, IP=tsys.ip, HMC_IP=tsys.hmc_ip)
    # oTestHost._Fill_HMC_Data()
    # oTestHost._FillFromAIX()
    sZabbixURL = 'http://' + zbx.ip + "/zabbix"
    oAPI = pyzabbix.api.ZabbixAPI(url=sZabbixURL, user=zbx.user, password=zbx.password)
    oSnd = pyzabbix.sender.ZabbixSender(zabbix_server=zbx.ip)
    oTestHost._Connect2Zabbix(oAPI, oSnd)
    oTriggers = zi.TriggerFactory()
    oTestHost._ConnectTriggerFactory(oTriggers)
    oTestHost._MakeAppsItems()


# vim: expandtab : softtabstop=4 : tabstop=4 : shiftwidth=4
