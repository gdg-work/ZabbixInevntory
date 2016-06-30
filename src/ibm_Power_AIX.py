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

import re

oLog = logging.getLogger(__name__)

DEFAULT_SSH_PORT = 22
RE_HDISK = re.compile(r'^\s*hdisk\d+\s')
RE_PWRSUPPLY = re.compile(r'^\s*A IBM AC PS\s*:$')
RE_WS = re.compile(r'\s+')
RE_DOTS = re.compile(r'\.\.+')
RE_RAM_MODULE = re.compile(r'\s*Memory DIMM:$')


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
        self.sSerialNum = ''
        self.oAdapters = inv.AdaptersList()
        self.lDisks = []
        self.lPwrSupplies = []
        self.lDIMMs = []
        self.oZbxHost = None
        self._Fill_HMC_Data()
        self._FillFromAIX()
        # print(self)
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
        # print(str(lOut))
        self.iMemGBs = int(lOut[0]) // 1024
        self.iTotalCores = int(float(lOut[1]))
        sTM, self.sHmcIP, self.sSerialNum = lOut[2].split(',')
        self.sType, self.sModel = sTM.split('-')
        oLog.debug('_Fill_HMC_Data: Mem:{0}, CPU:{1}, MTM:{2}-{3}, SN:{4}'.format(
            self.iMemGBs, self.iTotalCores, self.sType, self.sModel, self.sSerialNum))
        # adapters
        iterData = csv.DictReader(lOut[3].split('\n'), fieldnames=lFields)
        for d in iterData:
            if d['description'] == 'Empty slot':
                pass        # skip empty slots
            else:
                # oLog.debug('Current self.oAdapters content: ' + str(self.oAdapters.items()))
                self.oAdapters._append(IBM_Power_Adapter(d['description'], d['bus_id'], d['drc_name']))
        return

    def _FillFromAIX(self):
        sRet = self._sFromHost('lscfg -vp')
        # oLog.info("_FillFromAIX: lscfg output is {}".format(sRet))
        lsCfgData = sRet.split('\n')
        self._FillDisks(lsCfgData)
        self._FillPwrSupplies(lsCfgData)
        self._FillDIMMs(lsCfgData)
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
                    else:
                        # non-interesting line
                        pass
                    sL = next(iterDiskData).strip()
                    # end while not...
                oLog.debug('Disk found: {} at {}, pn {}, sn {}, model {}'.format(
                    sDskName, sHWLoc, sPN, sSN, sModel))

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
                sL1 = next(iterPSData).strip()
                self.iPwrSupplies += 1
                oLog.debug('_FillPwrSupply: 1st line is {}'.format(sL1))
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
                sL1 = next(iterDIMMsData).strip()
                oLog.debug('_FillDIMMs: 1st line is {}'.format(sL1))
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
        """return a list of Zabbix 'application' names for this type of server"""
        if self.oZbxHost:
            # zabbix interface is defined
            self.oZbxHost._oAddApp('System')
            # Add items
            oMemItem = self.oZbxHost._oAddItem(
                "System Memory", sAppName='System',
                dParams={'key': "Host_{}_Memory".format(self.sName), 'units': 'GB', 'value_type': 3,
                         'description': _('Total memory size in GB')})
            oMemItem._SendValue(self.iMemGBs, self.oZbxSender)
            oCPUItem = self.oZbxHost._oAddItem(
                "System Total Cores", sAppName='System',
                dParams={'key': "Host_{}_Cores".format(self.sName), 'value_type': 3,
                         'description': _('Total amount of cores in all CPUs')})
            oCPUItem._SendValue(self.iTotalCores, self.oZbxSender)
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
            oTotPS_Item = self.oZbxHost._oAddItem(
                "System Pwr Supplies", sAppName='System',
                dParams={'key': "Host_{}_NPwrSupplies".format(self.sName), 'value_type': 3,
                         'description': _('Number of power supplies')})
            oTotPS_Item._SendValue(self.iPwrSupplies, self.oZbxSender)
            oTotDIMMs_Item = self.oZbxHost._oAddItem(
                "System DIMMs #", sAppName='System',
                dParams={'key': "Host_{}_NDIMMs".format(self.sName), 'value_type': 3,
                         'description': _('Number of memory modules')})
            oTotDIMMs_Item._SendValue(self.iDIMMs, self.oZbxSender)
            # Adapters, disks, PS, etc.
            for oObj in (list(self.oAdapters.values()) + self.lDisks + self.lPwrSupplies + self.lDIMMs):
                oObj._MakeAppsItems(self.oZbxHost, self.oZbxSender)
        else:
            oLog.error("Zabbix interface isn't initialized yet")
            raise expHMC_Error("Zabbix isn't connected yet")
        return


class IBM_Power_Adapter(inv.ComponentClass):
    def __init__(self, sName, sBusID, sLocation):
        self.sName = sName
        self.sBusID = sBusID
        self.sLocation = sLocation
        return

    def __repr__(self):
        return str('Adapter: type:{0}, Bus ID:{1}, location:{2}'.format(
            self.sName, self.sBusID, self.sLocation))

    def _dGetDataAsDict(self):
        return {'name': self.sName, 'bus_id': self.sBusID, 'location': self.sLocation}

    def _sGetName(self):
        return self.sName

    def _sBusID(self):
        return self.sBusID

    def _MakeAppsItems(self, oZbxHost, oZbxSender):
        sAppName = "Adapter " + self.sBusID
        oZbxHost._oAddApp(sAppName)
        oNameItem = oZbxHost._oAddItem(
            sAppName + " Type", sAppName,
            dParams={'key': "Adapter_{}_of_{}_Type".format(self.sBusID, oZbxHost._sName()),
                     'description': _('Type of adapter')})
        oNameItem._SendValue(self.sName, oZbxSender)
        oPosItem = oZbxHost._oAddItem(
            sAppName + " Position", sAppName,
            dParams={'key': "Adapter_{}_of_{}_Pos".format(self.sBusID, oZbxHost._sName()),
                     'description': _('Position of adapter in the machine')})
        oPosItem._SendValue(self.sLocation, oZbxSender)
        return


class IBM_Power_Disk(inv.ComponentClass):
    dDescriptions = {
        'Type':           _('Disk type'),
        'Model':          _('Disk model'),
        'Part Number':    _('Disk part number'),
        'Serial Number':  _('Disk serial number'),
        'Location':       _('Disk location')}

    def __init__(self, sName, sType, sModel, sPN, sSN, sLoc):
        self.sName = sName
        self.dData = {'Type':           sType,      # keys must contain only valid chars for Zabbix key
                      'Model':          sModel,     # + spaces
                      'Part Number':    sPN,
                      'Serial Number':  sSN,
                      'Location':       sLoc}
        return

    def _MakeAppsItems(self, oZbxHost, oZbxSender):
        sAppName = 'Disk ' + self.sName
        oZbxHost._oAddApp(sAppName)
        # all parameters are strings, so we can use loop
        for sN, sV in self.dData.items():
            # oLog.debug('Parameter name:{}, value:{}'.format(sN, sV))
            sItemName = sAppName + ' ' + sN
            sItemKey = 'Disk_{}_of_{}_{}'.format(self.sName, oZbxHost._sName(), sN).replace(' ', '_')
            oItem = oZbxHost._oAddItem(
                sItemName, sAppName,
                dParams={'key': sItemKey, 'description': self.dDescriptions[sN]})
            oItem._SendValue(sV, oZbxSender)
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

    def _MakeAppsItems(self, oZbxHost, oZbxSender):
        sAppName = self.sName               # 'Power supply {}'
        oZbxHost._oAddApp(sAppName)
        # all parameters are strings, so we can use loop
        for sN, sV in self.dData.items():
            # oLog.debug('Parameter name:{}, value:{}'.format(sN, sV))
            sItemName = sAppName + ' ' + sN
            sItemKey = '{}_of_{}_{}'.format(
                sAppName, oZbxHost._sName(), sN).replace(' ', '_')
            oItem = oZbxHost._oAddItem(sItemName, sAppName,
                                       dParams={'key': sItemKey, 'description': self.dDescriptions[sN]})
            # oLog.debug('IBM_Power_Supply._MakeAppsItems: created item is ' + str(oItem))
            oItem._SendValue(sV, oZbxSender)
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
            sItemKey = '{}_of_{}_{}'.format(
                sAppName, oZbxHost._sName(), sN).replace(' ', '_')
            oItem = oZbxHost._oAddItem(
                sItemName, sAppName,
                dParams={'key': sItemKey, 'description': self.dDescriptions[sN]})
            # oLog.debug('IBM_DIMM_Module._MakeAppsItems: created item is ' + str(oItem))
            oItem._SendValue(sV, oZbxSender)
        # and integer item: size
        sItemName = sAppName + ' Size'
        sItemKey = '{}_of_{}_{}'.format(
            sAppName, oZbxHost._sName(), 'Size').replace(' ', '_')
        oItem = oZbxHost._oAddItem(sItemName, sAppName,
                                   dParams={'key': sItemKey, 'value_type': 3, 'units': 'GB'})
        # oLog.debug('IBM_DIMM_Module._MakeAppsItems: created item is ' + str(oItem))
        oItem._SendValue(self.iSize, oZbxSender)

        return

# vim: expandtab : softtabstop=4 : tabstop=4 : shiftwidth=4
