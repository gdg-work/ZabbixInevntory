#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Interface with Power architecture hosts, AIX and HMC"""
import inventoryObjects as inv
import MySSH
import logging
import csv    # because commands output use quoted fields

oLog = logging.getLogger(__name__)

DEFAULT_SSH_PORT = 22


class expHMC_Error(Exception):
    def __init__(self, sMsg):
        self.sMsg = sMsg
        return

    def __repr__(self):
        return self.sMsg


class expHMC_NoAnswer(expHMC_Error):
    def __init__(self, sMsg):
        super().__init__(sMsg)


class expHMC_InvalidFormat(expHMC_Error):
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
        self._Fill_HMC_Data2()
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

    def _dssFromHMC(self, sCommand):
        """connect to HMC, run a command and return results"""
        oAuth = MySSH.AuthData(self.sSpUser, bUseKey=False, sPasswd=self.sSpPass)
        oHmcConn = MySSH.MySSHConnection(self.sHmcIP, DEFAULT_SSH_PORT, oAuth)
        sFromCmd = oHmcConn.fsRunCmd(sCommand)
        oLog.debug('_dssFromHMC: output of command "{}": "{}"'.format(
                   sCommand, sFromCmd))
        # result is a long line with 'name=field' format separated by commas
        if ',' in sFromCmd:
            for lLine in csv.reader([sFromCmd], delimiter=',', quotechar='"'):
                dData = dict([tuple(t.split('=')) for t in lLine])
        else:
            oLog.error('Cannot receive data from HMC, check the server\'s name IN HMC')
            oLog.error('HMC output: ' + sFromCmd)
            raise expHMC_InvalidFormat("Invalid format (no comma) of HMC's answer")
        return dData

    def _ldFromHMCMultiLine(self, sCommand):
        """connect to HMC, run a command and return results as a list of dictionaries"""
        oAuth = MySSH.AuthData(self.sSpUser, bUseKey=False, sPasswd=self.sSpPass)
        oHmcConn = MySSH.MySSHConnection(self.sHmcIP, DEFAULT_SSH_PORT, oAuth)
        sLines = oHmcConn.fsRunCmd(sCommand)
        if not sLines:
            raise expHMC_NoAnswer("Empty answer from HMC")

        # result is a *list* of long lines with 'name=field' format separated by commas
        for sFromCmd in sLines.split('\n'):
            if ',' in sFromCmd:
                for lLine in csv.reader([sFromCmd], delimiter=',', quotechar='"'):
                    dData = dict([tuple(t.split('=')) for t in lLine])
            else:
                oLog.error('Cannot receive data from HMC, check the server\'s name IN HMC')
                oLog.error('HMC output: ' + sFromCmd)
                raise expHMC_InvalidFormat('Cannot receive data from HMC, check the server name')
        return dData

    def _Fill_HMC_Data(self):
        # Memory
        sMem = self._sFromHMC(
            'lshwres -r mem -m {} --level sys -F installed_sys_mem'.format(self.sName))
        self.iMemGBs = int(sMem.strip()) // 1024
        # Processors
        sData3 = self._sFromHMC(
            'lshwres -r proc -m {} --level sys -F installed_sys_proc_units'.format(self.sName))
        self.iTotalCores = int(float(sData3.strip()))
        # SN, MTM, etc
        lFields = ['type_model', 'ipaddr', 'serial_num']
        sFields = ','.join(lFields)
        sData = self._sFromHMC(
            'lssyscfg -r sys -m {0} -F {1}'.format(self.sName, sFields))
        lData = sData.split('\n')
        iterData = csv.DictReader(lData, fieldnames=lFields)
        dDict = iterData.__next__()     # csv.DictReader returns an iterator
        oLog.debug('_Fill_HMC_Data: system fields are {}'.format(str(dDict)))
        sTM = dDict.get('type_model')
        if sTM:
            oLog.debug("_Fill_HMC_Data: TypeModel string is " + sTM)
            self.sType, self.sModel = sTM.split('-')
        self.sHmcIP = dDict.get('ipaddr')
        self.sSerialNum = dDict.get('serial_num')
        oLog.debug('_Fill_HMC_Data: Mem:{0}, CPU:{1}, MTM:{2}-{3}, SN:{4}'.format(
            self.iMemGBs, self.iTotalCores, self.sType, self.sModel, self.sSerialNum))
        # list of adapters
        sData = self._sFromHMC(
            'lssyscfg -r sys -m {0} -F {1}'.format(self.sName, sFields))
        lFields = ['unit_phys_loc', 'description', 'drc_name', 'bus_id']
        sCmd = 'lshwres -r io -m {0} --rsubtype slot -F {1}'.format(
            self.sName, ','.join(lFields))
        sData = self._sFromHMC(sCmd)
        assert (sData.strip() != '')
        lData = sData.split('\n')
        iterData = csv.DictReader(lData, fieldnames=lFields)
        for d in iterData:
            if d['description'] == 'Empty slot':
                pass        # skip empty slots
            else:
                self.oAdapters[d['bus_id']] = IBM_Power_Adapter(
                    d['description'], d['bus_id'], d['drc_name'])
        return

    def _Fill_HMC_Data2(self):
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

    def _lGetApplications(self):
        """return a list of Zabbix 'application' names for this type of server"""
        lRet = ['System']
        for oCard in self.oAdapters.values():
            lRet.append('Adapter ' + str(oCard._sBusID()))
        return lRet

    def _lGetItems(self, sAppName):
        """returns a list of item names corresponding to sAppName"""
        # Вариант -- возвращать Tuple, (приложение, item)
        return [('System', 'System Memory'), ('System', 'CPU Cores'), ('System', 'System Type')]


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

# vim: expandtab : softtabstop=4 : tabstop=4 : shiftwidth=4
