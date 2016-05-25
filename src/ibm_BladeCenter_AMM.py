#!/usr/bin/env python3
# -*- coding: utf8 -*-
""" A module for IBM Blade Servers using AMM as a OS-independent control """
import inventoryObjects as inv
import itertools as it
import logging
import MySSH
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
        return

    def _lsFromAMM(self, lsCommands):
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
                    print("*DBG* Blade with name {} found, ID {}".format(
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
                print("MTM found: {}".format(self.sTypeMod))
            if sData[:13] == 'Manufacturer:':
                self.sVendor = sData[14:].strip()
                print("Vendor found: " + self.sVendor)
            elif sData[:19] == 'Mach serial number:':
                self.sSN = sData[20:]
                print("Serial found: {}".format(self.sSN))
            elif sData[:9] == 'Part no.:':
                self.sPN = sData[10:]
                print("P/N found: " + self.sPN)
            else:
                # skip unknown lines
                pass
        # get list of child nodes (cpu & memory & exp [adapters])
        lOut = oAmmConn.fsRunCmd('list -l 2 -T system:{}'.format(self.sBladeNum)).split('\n')
        lComponents = [l.strip() for l in lOut]
        # print("\n".join(lComponents))
        self._ParseFillAMMComponents(lComponents, oAmmConn, oAuth)
        return lOut

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

        # debug print
        print("======= CPU ========")
        # print("\n==========\n".join(["\n".join(l) for l in llCpus]))
        self._FillCPUs(llCpus)
        print("\n".join([str(p) for p in self.lCPUs]))
        print("======= Memory ========")
        self._FillDIMMs(llMem)
        print("\n".join([str(d) for d in self.lDIMMs]))
        # print("\n==========\n".join(["\n".join(l) for l in llMem]))
        print("======= Expansion cards =======")
        # print("\n==========\n".join(["\n".join(l) for l in llExp]))
        self._FillEXPs(llExp)
        print("\n".join([str(a) for a in self.lExps]))
        return

    def _FillCPUs(self, llData):
        """fills CPU information from a list of CPU descriptions from AMM
        (each description is a list of strings)"""
        self.lCPUs = []
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


class Blade_CPU(inv.ComponentClass):
    def __init__(self, sName, sSpeed, sFamily, iCores):
        self.sName = sName
        self.dData = {'speed': sSpeed, 'family': sFamily, 'cores': iCores}
        return

    def __repr__(self):
        return ("{0}: {3}-core {2} processor at {1}".format(
            self.sName, self.dData['speed'], self.dData['family'], self.dData['cores']))


class Blade_DIMM(inv.ComponentClass):
    def __init__(self, sName, sPN, sSN, sType, iSizeGB):
        self.sName = sName
        self.dData = {'pn': sPN, 'sn': sSN, 'type': sType, 'size_gb': iSizeGB}
        return

    def __repr__(self):
        return ("{0}: {1}-GB {2} Module: pn {3}, sn {4}".format(
            self.sName, self.dData['size_gb'], self.dData['type'], self.dData['pn'], self.dData['sn']))


class Blade_EXP(inv.ComponentClass):
    def __init__(self, sName, sPN, sSN, sType):
        self.sName = sName
        self.dData = {'sn': sSN, 'pn': sPN, 'type': sType}
        return

    def __repr__(self):
        return ("{0}: {3} (pn {1}, sn {2})".format(
            self.sName, self.dData['pn'], self.dData['sn'], self.dData['type']))


if __name__ == '__main__':
    """testing section"""
    oLog.setLevel(logging.DEBUG)
    oConHdr = logging.StreamHandler()
    oConHdr.setLevel(logging.DEBUG)
    oLog.addHandler(oConHdr)
    # oAmm = BladeWithAMM('esxprod04', IP='127.0.0.1', SP_User='USERID',
    #                     SP_Pass='PASSW0RD', AMM_IP='10.1.128.148')
    oAmm = BladeWithAMM('vmsrv04', IP='127.0.0.1', SP_User='host',
                        SP_Pass='host123', AMM_IP='10.0.22.61')
    lOut = oAmm._lsFromAMM([])
    # print("\n".join(lOut))
