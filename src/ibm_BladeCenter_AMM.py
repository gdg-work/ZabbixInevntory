#!/usr/bin/env python3
# -*- coding: utf8 -*-
""" A module for IBM Blade Servers using AMM as a OS-independent control """
import inventoryObjects as inv
import itertools as it
import logging
import MySSH
import zabbixInterface as zi
import WBEM_disks as wd
import re

# Constants
DEFAULT_SSH_PORT = 22
RE_WS = re.compile(r'\s+')
RE_BLADE = re.compile(r'blade\[\d{1,2}\]\s')
# RE_BLADEINFO = re.compile(r'^system:blade\[\d{1,2}\] info')
# RE_BLADEINFO = re.compile(r'info')
RE_COMP = re.compile(r'^(\w+)\[(\d{1,3})\]')
RE_INFOSTART = re.compile(r'system> info -T system:blade')
RE_INFOCPU = re.compile(r'info -T system:[^:]+:cpu\[\d\]')
RE_INFOMEM = re.compile(r'info -T system:[^:]+:memory\[\d{1,3}\]')
RE_INFOEXP = re.compile(r'info -T system:[^:]+:exp\[\d{1,3}\]')
RE_EMPTY = re.compile(r'^\w*$')


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
    def __init__(self, sName, **dParams):
        """sName is a name of the server in AMM"""
        super().__init__(sName, IP=dParams.get('IP', None))
        self.sUser = dParams.get('User')
        self.sPass = dParams.get('Pass')
        self.sSpUser = dParams.get('SP_User')
        self.sSpPass = dParams.get('SP_Pass')
        self.sSpIP = dParams.get('AMM_IP')
        self.sSerialNum = ''
        self.sBladeNum = ''
        self.iTotalRAMgb = 0
        self.iDIMMs = 0
        self.iCPUs = 0
        self.lDIMMs = []
        self.lCPUs = []
        self.lExps = []
        self.lDisks = []
        self._FillFromAMM()
        self._FillDisksFromWBEM()
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
                if self.sName == sName:
                    self.sBladeNum = sDev
                    oLog.debug("*DBG* Blade with name {} found, ID {}".format(
                        self.sName, self.sBladeNum))
                    break
        if self.sBladeNum == '':
            oLog.error('Blade {} is not found in the enclosure'.format(self.sName))
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
        self._ParseFillAMMComponents2(lComponents, oAmmConn, oAuth)
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
        # ==== disabled for debugging ====
        lOutput = oConn._lsRunCommands2(lCommands)
        # lOutput = open(",out.txt", "r").readlines()
        # --- disabled for debugging ---
        # print("Commands output: " + str(lOutput))
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

    def _ParseFillAMMComponents2(self, lData, oConn, oAuth):
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
            self.lCPUs.append(c)
        return

    def _FillDIMMs(self, llData):
        self.lDIMMs = []
        for lsCpuDesc in llData:
            sName, sPN, sSN, sType, iSizeGB, iTotalGB = ("", "", "", "", 0, 0)
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
            self.lDIMMs.append(d)
            self.iTotalRAMgb = iTotalGB
        return

    def _FillEXPs(self, llData):
        self.lExps = []
        for lsExpDesc in llData:
            sName, sPN, sSN = ("", "", "")
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
            self.lExps.append(a)
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
                dParams={'key': "Host_{}_Memory".format(self.sName), 'units': 'GB', 'value_type': 3})
            oMemItem._SendValue(self.iTotalRAMgb, self.oZbxSender)
            oCPUItem = self.oZbxHost._oAddItem(
                "System CPUs #", sAppName='System',
                dParams={'key': "Host_{}_CPUs".format(self.sName), 'value_type': 3})
            oCPUItem._SendValue(len(self.lCPUs), self.oZbxSender)
            oCoresItem = self.oZbxHost._oAddItem(
                "System Cores #", sAppName='System',
                dParams={'key': "Host_{}_Cores".format(self.sName), 'value_type': 3})
            # number of cores in all CPUs
            oCoresItem._SendValue(self.iTotalCores, self.oZbxSender)

            # Host information
            oVendorItem = self.oZbxHost._oAddItem(
                'System Vendor', sAppName='System',
                dParams={'key': '{}_Vendor'.format(self.sName), 'value_type': 2})
            oMTM_Item = self.oZbxHost._oAddItem(
                'System Model', sAppName='System',
                dParams={'key': '{}_MTM'.format(self.sName), 'value_type': 2})
            oPN_Item = self.oZbxHost._oAddItem(
                'System Part Number', sAppName='System',
                dParams={'key': '{}_PartNo'.format(self.sName), 'value_type': 2})
            oSN_Item = self.oZbxHost._oAddItem(
                'System Serial Number', sAppName='System',
                dParams={'key': '{}_SerNo'.format(self.sName), 'value_type': 2})

            oVendorItem._SendValue(self.sVendor, self.oZbxSender)
            oMTM_Item._SendValue(self.sTypeMod, self.oZbxSender)
            oPN_Item._SendValue(self.sPN, self.oZbxSender)
            oSN_Item._SendValue(self.sSN, self.oZbxSender)

            # send components' items to Zabbix
            lComps = self.lCPUs + self.lDIMMs + self.lExps
            for o in lComps:
                o._MakeAppsItems(self.oZbxHost, self.oZbxSender)
        else:
            oLog.error("Zabbix interface isn't initialized yet")
            raise expAMM_Error("Zabbix isn't connected yet")
        return

    def _FillDisksFromWBEM(self):
        ldDicts = wd._ldConnectAndReportDisks(self.sHost, self.sUser, self.sPass, iPort=5989)
        for dDiskData in ldDicts:
            iSizeGB = int(dDiskData['MaxMediaSize']) // 2**20   # WBEM returns size in KB
            self.lDisks.append(
                Blade_Disk(dDiskData['Name'], dDiskData['Model'], dDiskData['PartNumber'],
                           dDiskData['SerialNumber'], iSizeGB))
        return


class Blade_CPU(inv.ComponentClass):
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
                     'value_type': 2})
        oCoresItem = oZbxHost._oAddItem(
            self.sName + " # Cores", sAppName=self.sName,
            dParams={'key': "{}_{}_Cores".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'value_type': 3})
        oSpeedItem = oZbxHost._oAddItem(
            self.sName + " Speed", sAppName=self.sName,
            dParams={'key': "{}_{}_Speed".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'value_type': 2})

        oTypeItem._SendValue(self.dData['family'], oZbxSender)
        oCoresItem._SendValue(self.dData['cores'], oZbxSender)
        oSpeedItem._SendValue(self.dData['speed'], oZbxSender)
        return


class Blade_DIMM(inv.ComponentClass):
    def __init__(self, sName, sPN, sSN, sType, iSizeGB):
        self.sName = sName
        self.dData = {'pn': sPN, 'sn': sSN, 'type': sType, 'size_gb': iSizeGB}
        return

    def __repr__(self):
        return ("{0}: {1}-GB {2} Module: pn {3}, sn {4}".format(
            self.sName, self.dData['size_gb'], self.dData['type'], self.dData['pn'], self.dData['sn']))

    def _MakeAppsItems(self, oZbxHost, oZbxSender):
        """Make applications, items and send values to Zabbix"""
        oZbxHost._oAddApp(self.sName)     # DIMM #
        oTypeItem = oZbxHost._oAddItem(
            self.sName + " Type", sAppName=self.sName,
            dParams={'key': "{}_{}_Type".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'value_type': 2})
        oPN_Item = oZbxHost._oAddItem(
            self.sName + " Part Number", sAppName=self.sName,
            dParams={'key': "{}_{}_PN".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'value_type': 2})
        oSN_Item = oZbxHost._oAddItem(
            self.sName + " Serial Number", sAppName=self.sName,
            dParams={'key': "{}_{}_SN".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'value_type': 2})
        oSize_Item = oZbxHost._oAddItem(
            self.sName + " Size", sAppName=self.sName,
            dParams={'key': "{}_{}_SizeGB".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'value_type': 3, 'units': 'GB'})
        oTypeItem._SendValue(self.dData['type'], oZbxSender)
        oPN_Item._SendValue(self.dData['pn'], oZbxSender)
        oSN_Item._SendValue(self.dData['sn'], oZbxSender)
        oSize_Item._SendValue(self.dData['size_gb'], oZbxSender)
        return


class Blade_EXP(inv.ComponentClass):
    def __init__(self, sName, sPN, sSN, sType):
        self.sName = sName
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
                     'value_type': 2})
        oPN_Item = oZbxHost._oAddItem(
            self.sName + " Part Number", sAppName=self.sName,
            dParams={'key': "{}_{}_PN".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'value_type': 2})
        oSN_Item = oZbxHost._oAddItem(
            self.sName + " Serial Number", sAppName=self.sName,
            dParams={'key': "{}_{}_SN".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'value_type': 2})
        oTypeItem._SendValue(self.dData['type'], oZbxSender)
        oPN_Item._SendValue(self.dData['pn'], oZbxSender)
        oSN_Item._SendValue(self.dData['sn'], oZbxSender)
        return


class Blade_Disk(inv.ComponentClass):
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
                     'value_type': 2})
        oPN_Item = oZbxHost._oAddItem(
            self.sName + " Part Number", sAppName=self.sName,
            dParams={'key': "{}_{}_PN".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'value_type': 2})
        oSN_Item = oZbxHost._oAddItem(
            self.sName + " Serial Number", sAppName=self.sName,
            dParams={'key': "{}_{}_SN".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'value_type': 2})
        oSize_Item = oZbxHost._oAddItem(
            self.sName + " Size", sAppName=self.sName,
            dParams={'key': "{}_{}_Size".format(oZbxHost._sName(), self.sName).replace(' ', '_'),
                     'value_type': 3, 'units': 'GB'})
        oModelItem._SendValue(self.dData['model'], oZbxSender)
        oPN_Item._SendValue(self.dData['pn'], oZbxSender)
        oSN_Item._SendValue(self.dData['sn'], oZbxSender)
        oSize_Item._SendValue(self.dData['size'], oZbxSender)
        return


if __name__ == '__main__':
    """testing section"""
    oLog.setLevel(logging.DEBUG)
    oConHdr = logging.StreamHandler()
    oConHdr.setLevel(logging.DEBUG)
    oLog.addHandler(oConHdr)
    # oAmm = BladeWithAMM('esxprod04', IP='127.0.0.1', SP_User='USERID',
    #                     SP_Pass='PASSW0RD', AMM_IP='10.1.128.148')
    oAmm = BladeWithAMM('vmsrv06', IP='vmsrv06.msk.protek.ru', User='zabbix', Pass='A3hHr88man01',
                        SP_User='host', SP_Pass='host123', AMM_IP='10.0.22.61')
    lOut = oAmm._lsFromAMM([])
    # print("\n".join(lOut))

# vim: expandtab : softtabstop=4 : tabstop=4 : shiftwidth=4 : autoindent
