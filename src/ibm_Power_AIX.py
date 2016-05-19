#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Interface with Power architecture hosts, AIX and HMC"""
import inventoryObjects as inv
import MySSH
import logging
import csv    # because commands output use quoted fields
import zabbixInterface as zi
import itertools as it
import re

oLog = logging.getLogger(__name__)

DEFAULT_SSH_PORT = 22
RE_HDISK = re.compile(r'^\s*hdisk\d+\s')


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
        super().__init__(sName, IP=dFields['IP'])
        self.sUser = dFields['User']
        self.sPass = dFields['Pass']
        self.sSpUser = dFields['SP_User']
        self.sSpPass = dFields['SP_Pass']
        self.sHmcIP = dFields['HMC_IP']
        self.sSerialNum = ''
        self.oAdapters = inv.AdaptersList()
        self.lDisks = []
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
        oLog.debug('_sFromHMC: output of command "{}": "{}"'.format(
                   sCommand, sFromCmd))
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

#     def _dssFromHMC(self, sCommand):
#         """connect to HMC, run a command and return results"""
#         oAuth = MySSH.AuthData(self.sSpUser, bUseKey=False, sPasswd=self.sSpPass)
#         oHmcConn = MySSH.MySSHConnection(self.sHmcIP, DEFAULT_SSH_PORT, oAuth)
#         sFromCmd = oHmcConn.fsRunCmd(sCommand)
#         oLog.debug('_dssFromHMC: output of command "{}": "{}"'.format(
#                    sCommand, sFromCmd))
#         # result is a long line with 'name=field' format separated by commas
#         if ',' in sFromCmd:
#             for lLine in csv.reader([sFromCmd], delimiter=',', quotechar='"'):
#                 dData = dict([tuple(t.split('=')) for t in lLine])
#         else:
#             oLog.error('Cannot receive data from HMC, check the server\'s name IN HMC')
#             oLog.error('HMC output: ' + sFromCmd)
#             raise expHMC_InvalidFormat("Invalid format (no comma) of HMC's answer")
#         return dData

#     def _ldFromHMCMultiLine(self, sCommand):
#         """connect to HMC, run a command and return results as a list of dictionaries"""
#         oAuth = MySSH.AuthData(self.sSpUser, bUseKey=False, sPasswd=self.sSpPass)
#         oHmcConn = MySSH.MySSHConnection(self.sHmcIP, DEFAULT_SSH_PORT, oAuth)
#         sLines = oHmcConn.fsRunCmd(sCommand)
#         if not sLines:
#             raise expHMC_NoAnswer("Empty answer from HMC")
#
#         # result is a *list* of long lines with 'name=field' format separated by commas
#         for sFromCmd in sLines.split('\n'):
#             if ',' in sFromCmd:
#                 for lLine in csv.reader([sFromCmd], delimiter=',', quotechar='"'):
#                     dData = dict([tuple(t.split('=')) for t in lLine])
#             else:
#                 oLog.error('Cannot receive data from HMC, check the server\'s name IN HMC')
#                 oLog.error('HMC output: ' + sFromCmd)
#                 raise expHMC_InvalidFormat('Cannot receive data from HMC, check the server name')
#         return dData

    def _Fill_HMC_Data(self):
        oAuth = MySSH.AuthData(self.sSpUser, bUseKey=False, sPasswd=self.sSpPass)
        oHmcConn = MySSH.MySSHConnection(self.sHmcIP, DEFAULT_SSH_PORT, oAuth)

        oLog.debug('_Fill_HMC_Data2 called')
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

        return

    def _FillDisks(self, lsCfgData):
        """extract disks information from a list of strings and fills in diskList object"""
        RE_WS = re.compile(r'\s+')
        RE_DOTS = re.compile(r'\.+')
        # XXX a good place for 'stopIteration' catch XXX
        iterDiskData = it.dropwhile(lambda x: not RE_HDISK.match(x), lsCfgData)
        try:
            while True:
                sPN = ''                # no P/N on non-local drives
                bIsLocal = False        # reset variable
                iterDiskData = it.dropwhile(lambda x: not RE_HDISK.match(x), iterDiskData)
                # we are at first line of disk's description. Let's parse it.
                sL1 = iterDiskData.__next__().strip()
                oLog.debug('_FillDisks: 1st line is {}'.format(sL1))
                sDskName, sHWLoc, sDesc = RE_WS.split(sL1, maxsplit=2)
                sL = '--------'   # initialize loop variable
                bInDiskDescription = False
                while not(bInDiskDescription and sL == ''):
                    if sL[:13] == 'Serial Number':
                        # 'Serial Number...............6XN42PQM':
                        sSN = RE_DOTS.split(sL)[1]
                        oLog.debug('Disk {} S/N is {}'.format(sDskName, sSN))
                    if sL == '':    # first empty string
                        bInDiskDescription = True
                    elif sL[:11] == 'Part Number':
                        # Part Number.................74Y6486
                        sPN = RE_DOTS.split(sL)[1]
                        oLog.debug('Disk {} P/N is {}'.format(sDskName, sPN))
                    elif sL[:22] == 'Machine Type and Model':
                        # Machine Type and Model......ST9300653SS
                        sModel = RE_DOTS.split(sL)[1]
                        oLog.debug('Disk {} MTM is {}'.format(sDskName, sModel))
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
                dParams={'key': "Host_{}_Memory".format(self.sName), 'units': 'GB', 'value_type': 3})
            oMemItem._SendValue(self.iMemGBs, self.oZbxSender)
            oCPUItem = self.oZbxHost._oAddItem(
                "System Total Cores", sAppName='System',
                dParams={'key': "Host_{}_Cores".format(self.sName), 'value_type': 3})
            oCPUItem._SendValue(self.iTotalCores, self.oZbxSender)
            # self.sType, self.sModel
            oTypeItem = self.oZbxHost._oAddItem(
                "System Type", sAppName='System', dParams={'key': "Host_{}_Type".format(self.sName)})
            oTypeItem._SendValue(self.sType, self.oZbxSender)
            oModelItem = self.oZbxHost._oAddItem(
                "System Model", sAppName='System', dParams={'key': "Host_{}_Model".format(self.sName)})
            oModelItem._SendValue(self.sModel, self.oZbxSender)
            oSN_Item = self.oZbxHost._oAddItem(
                "System Serial Number", sAppName='System',
                dParams={'key': "Host_{}_Serial".format(self.sName)})
            oSN_Item._SendValue(self.sSerialNum, self.oZbxSender)
            # Adapters
            for oAdapter in self.oAdapters.values():
                oAdapter._MakeAppsItems(self.oZbxHost, self.oZbxSender)
            for oObj in self.lDisks:
                oObj._MakeAppsItems(self.oZbxHost, self.oZbxSender)
        else:
            oLog.error("Zabbix interface isn't initialized yet")
            raise expHMC_Error("Zabbix isn't connected yet")
        # for oCard in self.oAdapters.values():
        #     lRet.append('Adapter ' + str(oCard._sBusID()))
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
            dParams={'key': "Adapter_{}_of_{}_Type".format(self.sBusID, oZbxHost._sName())})
        oNameItem._SendValue(self.sName, oZbxSender)
        oPosItem = oZbxHost._oAddItem(
            sAppName + " Position", sAppName,
            dParams={'key': "Adapter_{}_of_{}_Pos".format(self.sBusID, oZbxHost._sName())})
        oPosItem._SendValue(self.sLocation, oZbxSender)
        return


class IBM_Power_Disk(inv.ComponentClass):
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
            oLog.debug('Parameter name:{}, value:{}'.format(sN, sV))
            sItemName = sAppName + ' ' + sN
            sItemKey = 'Disk_{}_of_{}_{}'.format(self.sName, oZbxHost._sName(), sN).replace(' ', '_')
            oItem = oZbxHost._oAddItem(sItemName, sAppName, dParams={'key': sItemKey})
            oLog.debug('IBM_Power_Disk._MakeAppsItems: created item is ' + str(oItem))
            oItem._SendValue(sV, oZbxSender)
        return


# vim: expandtab : softtabstop=4 : tabstop=4 : shiftwidth=4
