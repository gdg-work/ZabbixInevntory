#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# HP 3Par support for Zabbix inventory program
# ==============================
import logging
import re
import redis  # In-memory NoSQL DB for caching
import paramiko
import socket
import json  # serializing/deserializing of objects to stings and files
from collections import OrderedDict
from inventoryObjects import ClassicArrayClass, ControllerClass, DiskShelfClass, DASD_Class
# local constants
from local import CACHE_TIME, REDIS_ENCODING
import itertools


oLog = logging.getLogger(__name__)

# CONSTANTS
DEFAULT_SSH_PORT = 22
RE_WS = re.compile(r'\s+')


# Helper functions
def _genSlicesLst(s, lPos):
    position = 0
    for length in lPos:
        yield s[position:position + length]
        position += length


def _dDataByFormatString(sHdr, sData):
    """
    Parameter1 -- 3Par header string with fields separated by whitespace,
    width of fields is used for parsing the second parameter -- a string with data
    returns: a dictionary of type:
    { name: value, ... }
    where name is a name from header field and value is a corresponding value from data string.
    """
    dRet = {}
    lHdrFields = RE_WS.split(sHdr)
    lFieldLengths = [len(s) + 1 for s in lHdrFields]
    gHdrFields = [f.strip('-') for f in lHdrFields]
    gDataFields = [f.strip() for f in _genSlicesLst(sData, lFieldLengths)]
    dRet = dict(zip(gHdrFields, gDataFields))
    return dRet


class AuthData:
    def __init__(self, sLogin, bUseKey, sPasswd=None, sKeyFile=None):
        self.sLogin = sLogin
        self.bUseKey = bUseKey
        if self.bUseKey:
            self.sKeyFile = sKeyFile
        else:
            self.sPasswd = sPasswd
        return

    def _sLogin(self):
        return self.sLogin

    def _sKey(self):
        return self.sKeyFile

    def _sPasswd(self):
        return self.sPasswd


class MySSHConnection:
    def __init__(self, sIP, iPort, oAuth):
        self.oSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.bConnected = False
        self.oClient = paramiko.SSHClient()
        try:
            # oLog.debug("*DBG* Trying to connect to IP {} port {:d}".format(sIP, iPort))
            self.oSocket.connect((sIP, iPort))
            self.bConnected = True
        except Exception as e:
            oLog.error("Cannot create socket connection")
            pass
        if self.bConnected:
            try:
                self.oClient.set_missing_host_key_policy(paramiko.client.AutoAddPolicy())
                self.oClient.load_system_host_keys()
                # self.oClient.load_host_keys(dssParams['KnownHostsFile'])
                self.oClient.connect(hostname=sIP, port=iPort, username=oAuth._sLogin(),
                                     password=oAuth._sPasswd(), sock=self.oSocket)
            except Exception as e:
                oLog.error("*CRIT* Error connecting: " + str(e))
                self.bConnected = False
        return

    def close(self):
        try:
            if self.oClient:
                self.oClient.close()
            if self.oSocket:
                self.oSocket.close()
        except Exception:
            pass
        return

    def fsRunCmd(self, sCmd):
        lResult = []
        if self.bConnected:
            stdin, stdout, stderr = self.oClient.exec_command(sCmd)
            for l in stdout:
                lResult.append(l)
        else:
            oLog.error("fsRunCmd: isnt connected")
        return "".join(lResult)


class HP3Par_Exception(Exception):
    """Error when working wit HP 3Par"""
    def __init__(self, sStr):
        """Initialize the error with a message given as a string"""
        self.sErrMsg = sStr
        return

    def __str__(self):
        """Converts an error to string for printing"""
        return repr(self.sErrMsg)


def _sRightField(sLine):
    """returns a right field of line that has a format: <name><space>:<space><value>"""
    sRet = ''
    try:
        sRet = sLine.split(':')[1].strip()
    except IndexError:
        oLog.error("_sRightField: invalid line to split")
    return sRet


class HP3Par(ClassicArrayClass):
    # Fields: 1- ID, 2- type, 3-Model, 4-size, MB, 5-SN, 6-CagePos
    reDisk = re.compile(r'^(\d{1,5}) ([A-Z]+)\s+(\w+)\s+(\d+)\s+(\w+)\s+([0-9:]+)$')
    reTotal = re.compile(r'^(d+)\s+(\d+)+\s(total)$')
    reNodesBegin = re.compile(r'^-+Nodes-+$')
    rePCIcardsBegin = re.compile(r'^-+PCI Cards-+$')
    reCPUBegin = re.compile(r'^-+CPUs-+$')
    reRAMBegin = re.compile(r'^-+Physical Memory-+$')
    reDE_Midplane_Begin = re.compile(r'^-+Midplane-+$')
    reDE_Pwr_Begin = re.compile(r'^-+Power Supply-+$')
    reNodesHdr = re.compile(r'^Node\s+-*Name-*\s+')
    reEmptyLine = re.compile(r'^\s*$')
    reWhiteSpace = re.compile(r'\s+')
    reWSorDash = re.compile(r'[ \t-]+')
    reCPUModel = re.compile(r'\((.*)\)')

    def __init__(self, sIP, oAuth, sSysName, oRedisConn):
        super().__init__(sIP, "3Par")
        self.sRedisKeyPrefix = "pyzabbix::hp3Par::" + sSysName + "::"
        self.sSysName = sSysName
        self.sIP = sIP
        self.oAuthData = oAuth
        self.bConnected = False
        self.dQueries = {"name": self.getName,
                         "sn": self.getSN,
                         "model": self.getModel,
                         "ctrls": self.getNodes,
                         "shelves": self.getCagesAmount,
                         "disks": self.getDisksAmount,
                         "ctrl-names": self.getControllerNames,
                         "shelf-names": self.getDiskShelfNames,
                         "disk-names":  self.getDiskNames}
        self.dSysParams = {}
        self.lDisks = []
        self.lControllers = []
        self.lCages = []
        self.iRedisTimeout = CACHE_TIME
        self.oRedisConnection = oRedisConn
        return

    def __sFromArray__(self, sCommand):
        """runs SSH command on the array, return output"""
        sRet = ''
        sRedisKey = self.sRedisKeyPrefix + "__sFromArray__::" + sCommand
        try:
            sRet = json.loads(self.oRedisConnection.get(sRedisKey).decode(REDIS_ENCODING))
        except AttributeError:
            try:
                oConn = MySSHConnection(self.sIP, DEFAULT_SSH_PORT, self.oAuthData)
                sRet = oConn.fsRunCmd(sCommand)
                oConn.close()
                # save results to Redis
                self.oRedisConnection.set(sRedisKey,
                                          json.dumps(sRet).encode(REDIS_ENCODING),
                                          self.iRedisTimeout)
            except Exception as e:
                oLog.error("SSH failed on login.")
                oLog.debug(e)
        return sRet

    def __dsFromArray__(self, lsCommands):
        """
        Connects to array, runs a SERIES of commands and return results as a dictionary
        with commands as keys and returned output as values
        """
        dData = OrderedDict({})
        sRedisKey = self.sRedisKeyPrefix + "__dsFromArray__"
        try:
            oConn = MySSHConnection(self.sIP, DEFAULT_SSH_PORT, self.oAuthData)
            for sCmd in lsCommands:
                # first try to lookup data in Redis, next ask array itself
                try:
                    sData = self.oRedisConnection.hget(sRedisKey, sCmd).decode(REDIS_ENCODING)
                except AttributeError:
                    try:
                        # oLog.debug('__dsFromArray__: Command to run: {}'.format(sCmd))
                        sData = oConn.fsRunCmd(sCmd)
                        # oLog.debug('__dsFromArray__: output: {}'.format(sData))
                    except Exception as e:
                        oLog.error('__dsFromArray__: failed to exec command')
                        oLog.error('__dsFromArray__: Additional info: ' + str(e))
                        raise HP3Par_Exception
                    self.oRedisConnection.hset(sRedisKey, sCmd, sData.encode(REDIS_ENCODING))
                    self.oRedisConnection.expire(sRedisKey, self.iRedisTimeout)
                dData[sCmd] = sData
            oConn.close()
        except Exception as e:
            oLog.error('__dsFromArray__: SSH failed on login')
            oLog.debug('__dsFromArray__: Additional info: ' + str(e))
        return dData

    def __FillDisks__(self):
        """Fills a list of physical disks from the array"""
        sDisksInfo = self.__sFromArray__('showpd -showcols Id,Type,Model,Size_MB,Serial,CagePos')
        gDiskInfo = (l.strip() for l in sDisksInfo.split('\n'))   # trying to use generator
        for sDisk in gDiskInfo:
            oDiskMatch = self.reDisk.match(sDisk)
            oTotalMatch = self.reTotal.match(sDisk)
            if oDiskMatch:
                self.lDisks.append(HP3Par_Disk(
                    oDiskMatch.group(1),         # id
                    oDiskMatch.group(2),         # type
                    oDiskMatch.group(3),         # model
                    int(oDiskMatch.group(4)),    # size
                    oDiskMatch.group(5),         # sn
                    oDiskMatch.group(6)))        # Cage position
            elif oTotalMatch:
                # oLog.debug('Total disk amount'.format(oTotalMatch.group(1)))
                pass
            else:
                pass
        return

    def __FillDiskEnclosures__(self):
        """fills a list of disk enclosures"""
        dCages = OrderedDict({})
        sOut = self.__sFromArray__('showcage')
        lOut = itertools.islice((l.strip() for l in sOut.split('\n')), 2, None)
        iDECount = 0
        sPN = ''
        sType = ''
        sSN = ''
        sModel = ''
        for l in lOut:
            if self.reEmptyLine.match(l):
                break
            else:
                iDECount += 1
                lFields = self.reWhiteSpace.split(l)
                sName = lFields[1]
                sDrives = lFields[6]
                dCages[sName] = sDrives
        # oLog.debug('__FillDiskEnclosures__: cages dict: {}'.format(str(dCages)))
        lCageNames = list(dCages.keys())
        sCmdFmt = 'showcage -i -svc {}'
        lsCmds = [sCmdFmt.format(c) for c in lCageNames]    # a command for each cage
        # oLog.debug('List of commands: {}'.format(', '.join([str(l)  for l in lsCmds])))
        dsCageInv = self.__dsFromArray__(lsCmds)
        for sName, sDrives in dCages.items():
            lInvOut = [l for l in dsCageInv[sCmdFmt.format(sName)].split('\n')]
            # oLog.debug("__FillDiskEnclosures__: ouput of 'showcage' is :\n" + "\n".join(lInvOut))
            iterCageMP = itertools.dropwhile(lambda x: not(self.reDE_Midplane_Begin.match(x)), lInvOut)
            l = next(iterCageMP)  # skip header line '--- Midplane ---'
            sHdr = next(iterCageMP)  # l = midplane header
            sFields = next(iterCageMP)  # l = midplane data
            dFields = _dDataByFormatString(sHdr, sFields)
            sPN = dFields.get('Saleable_PN', '')
            sType = dFields.get('Type', '')
            sSN = dFields.get('Saleable_SN', '')
            sModel = dFields.get('Model_Name', '')

            # Power supplies
            iterCagePS = itertools.dropwhile(lambda x: not(self.reDE_Pwr_Begin.match(x)), lInvOut)
            iPS_Amount = -2  # adjust for header lines
            for l in iterCagePS:
                if self.reEmptyLine.match(l):
                    break
                else:
                    iPS_Amount += 1
            self.lCages.append(HP3ParDiskEnclosure(sName, sSN, sPN, sType, sModel, sDrives, iPS_Amount))
        # oLog.debug("Cages info: {}".format(str(self.lCages)))
        return

    def __FillControllers__(self):
        """Fill a list of controllers defined in a system"""
        lNodes = []
        sSN = ''
        sName = ''
        sType = ''
        sModel = ''
        lLines = self.__sFromArray__('showsys -d').split('\n')
        for l in lLines:
            if l.find('Nodes Online') >= 0:
                lNodes = _sRightField(l).split(',')
                break
        # we have list of nodes' numbers in lNodes (as strings). Now is time to get some information
        oLog.debug('There are {} nodes: {}'.format(len(lNodes), ', '.join(lNodes)))
        for sNode in lNodes:
            sOutput = self.__sFromArray__('shownode -i -svc {}'.format(sNode))
            # try to parse 'shownode -i' output
            iterInventory = (l for l in sOutput.split('\n'))
            # rotate until the start of nodes section
            iterNodeStart = itertools.dropwhile(lambda x: not self.reNodesBegin.match(x),
                                                iterInventory)
            next(iterNodeStart)  # skip nodes section header
            sHdr = next(iterNodeStart)
            sFields = next(iterNodeStart)
            dFields = _dDataByFormatString(sHdr, sFields)
            oLog.debug('__FillControllers__: dFields are: ' + str(dFields))
            sSN = dFields.get('Assem_Serial', '')
            sName = dFields.get('Name', '')
            sType = dFields.get('Saleable_PN', '')
            sModel = dFields.get('Assem_Part', '')
            # oLog.debug('Controller {} ({}) SN is: {}'.format(sNode, sName, sSN))

            # looping forward to PCI cards header
            iterPCIStart = itertools.dropwhile(lambda x: not self.rePCIcardsBegin.match(x),
                                               iterInventory)
            # next few lines is PCI cards header
            iterCardLines = itertools.islice(iterPCIStart, 2, None)  # from item #4 to end
            lPCICards = []
            # and next few lines are PCI cards information
            while True:
                sLine = next(iterCardLines)
                if self.reEmptyLine.match(sLine):
                    break
                else:
                    lPCICards.append(PCICardClass(sLine))
            # debug
            # oLog.debug("__FillControllers__ PCI cards information:\n{}".format(
            #            '\n'.join(str(c) for c in lPCICards)))
            # next try to find CPU information
            iterCPUStart = itertools.dropwhile(lambda x: not self.reCPUBegin.match(x),
                                               iterInventory)
            iterCPULines = itertools.islice(iterCPUStart, 2, None)  # from item #4 to end
            iCPU_Cores = 0
            while True:
                sLine = next(iterCPULines)
                # oLog.debug("__FillControllers__: CPU line: {}".format(sLine))
                if self.reEmptyLine.match(sLine):
                    break
                else:
                    iCPU_Cores += 1
                    sCPU_Model = self.reCPUModel.search(sLine).group(1)
            # oLog.debug("__FillControllers__: CPU {}, {} cores".format(sCPU_Model, iCPU_Cores))

            # find and parse memory information
            iterRAMStart = itertools.dropwhile(lambda x: not self.reRAMBegin.match(x),
                                               iterInventory)
            iterRAMLines = itertools.islice(iterRAMStart, 2, None)  # from item #4 to end
            lRAM_Modules = []
            while True:
                sLine = next(iterRAMLines)
                if self.reEmptyLine.match(sLine):
                    break
                else:
                    lRAM_Modules.append(RAM_Module(sLine))
            # oLog.debug('__FillControllers__: RAM modules: \n{}'.format(
            #     '\n'.join(str(m) for m in lRAM_Modules)))
            self.lControllers.append(HP3ParController(sNode, sName, sSN, lPCICards,
                                                      iCPU_Cores, sCPU_Model, lRAM_Modules,
                                                      sModel, sType))
        return

    def __FillSysParms__(self):
        self.dSysParams['name'] = self.sSysName
        sLines = self.__sFromArray__('showsys -d')
        for l in sLines.split('\n'):
            if l.find('Serial Number') == 0:
                self.dSysParams['sn'] = _sRightField(l)
            if l.find('System Model') == 0:
                self.dSysParams['model'] = _sRightField(l)
            if l.find('Number of Nodes') == 0:
                self.dSysParams['Ctrls'] = int(_sRightField(l))
        return

    def getName(self):
        return self.sSysName

    def getSN(self):
        if len(self.dSysParams) == 0:
            self.__FillSysParms__()
        return self.dSysParams['sn']

    def getModel(self):
        if len(self.dSysParams) == 0:
            self.__FillSysParms__()
        return self.dSysParams['model']

    def getNodes(self):
        if len(self.lControllers) == 0:
            self.__FillSysParms__()
        return self.dSysParams['Ctrls']

    def getCagesAmount(self):
        if len(self.lCages) == 0:
            self.__FillDiskEnclosures__()
        return len(self.lCages)

    def getDisksAmount(self):
        if len(self.lDisks) == 0:
            self.__FillDisks__()
        return len(self.lDisks)

    def getControllerNames(self):
        lRet = []
        if len(self.lControllers) == 0:
            self.__FillControllers__()
        for c in self.lControllers:
            lRet.append(c.dQueries['name']())
        return lRet

    def getDiskShelfNames(self):
        lRet = []
        if len(self.lCages) == 0:
            self.__FillDiskEnclosures__()
        for c in self.lCages:
            lRet.append(c.dQueries['name']())
        return lRet

    def getDiskNames(self):
        lRet = []
        if len(self.lDisks) == 0:
            self.__FillDisks__()
        for d in self.lDisks:
            lRet.append(d.dQueries['name']())
        return lRet

    #
    # Methods for receiving components' information as a list of name:value dictionaries
    #
    def _dGetSysParams(self):
        """returns some system-wide parameters as a dictionary"""
        pass

    def _ldGetDisksAsDicts(self):
        """ Return disk data as a list of Python dictionaries with fields:
        name, SN, type, model, size, position
        """
        ldRet = []
        if len(self.lDisks) == 0:
            self.__FillDisks__()
        try:
            for oDisk in self.lDisks:
                ldRet.append(oDisk._dGetDataAsDict())
        except Exception as e:
            oLog.warning("Exception when filling a disk parameters list")
            oLog.debug("Exception: " + str(e))
        return ldRet

    def _ldGetControllersInfoAsDict(self):
        ldRet = []
        if self.lControllers == []:
            self.__FillControllers__()
        try:
            for oCtrl in self.lControllers:
                ldRet.append(oCtrl._dGetDataAsDict())
            # oLog.debug('_ldGetControllersInfoAsDict: dictionary: ' + str(ldRet))
        except Exception as e:
            oLog.warning("Exception when filling array controllers' parameters list")
            oLog.debug("Exception: " + str(e))
        return ldRet

    def _ldGetShelvesAsDicts(self):
        """ Return DEs' data as a list of Python dictionaries with fields:
        name, sn, type, model etc.
        """
        ldRet = []
        if self.lCages == []:
            self.__FillDiskEnclosures__()
        try:
            for oShelfObj in self.lCages:
                ldRet.append(oShelfObj._dGetDataAsDict())
        except Exception as e:
            oLog.warning("Exception when filling disk enclosures' parameters list")
            oLog.debug("Exception: " + str(e))
        return ldRet

    def _dGetArrayInfoAsDict(self, ssKeys):
        """
        Array-wide parameters as a dictionary.
        Parameter -- a set of keys/requests
        Returns: a dictionary {key:value}
        """
        dRet = {}
        for sKey in ssKeys:
            if sKey in self.dQueries:
                dRet[sKey] = self.dQueries[sKey]()
        return dRet


class HP3ParController(ControllerClass):
    def __init__(self, sNum, sID, sSN, lCards, iCores, sCPU, lDimms, sModel='', sType=''):
        super().__init__(sID, sSN)
        self.iNum = int(sNum)
        self.sCpu = 'CPU: {} Cores: {}'.format(sCPU, iCores)
        self.lCards = lCards
        self.lDimms = lDimms
        self.sModel = sModel
        self.sType = sType
        self.dQueries = {'name':   lambda: str(self.iNum),
                         'sn':     lambda: self.sSN,
                         'cpu':    lambda: self.sCpu,
                         'model':  lambda: self.sModel,
                         'type':   lambda: self.sType,
                         'pci':    self._sGetPCICards,
                         'ram':    self._sGetDimms}
        return

    def _sGetPCICards(self):
        """return a list of PCI cards as a string separated by '\n'"""
        return "\n".join(str(c) for c in self.lCards)

    def _sGetDimms(self):
        """returns DIMM information as a string"""
        return "\n".join(str(d) for d in self.lDimms)

    def _dGetDataAsDict(self):
        dRet = {}
        for name, fun in self.dQueries.items():
            dRet[name] = fun()
        return dRet


class HP3ParDiskEnclosure(DiskShelfClass):
    def __init__(self, sID, sSN, sPN, sType, sModel, sDrives, iPS):
        """initialize an object. Parameters: fields' values"""
        super().__init__(sID, sSN)
        self.iDiskCount = int(sDrives)
        self.iPSCount = iPS
        self.sModel = sModel
        self.sPN = sPN
        self.dQueries = {'sn':        lambda: self.sSN,
                         'name':      lambda: self.sID,
                         'disks':     lambda: self.iDiskCount,
                         'ps-amount': lambda: self.iPSCount,
                         'type':      lambda: self.sModel,
                         'model':     lambda: self.sPN}
        return

    def __repr__(self):
        sFmt = "Name:{} PN:{}, Type:{}, SN:{}, Model:{}, Disks:{}, Pwr:{}"
        sRet = sFmt.format(self.sID, self.sPN, self.sType, self.sSN,
                           self.sModel, self.iDiskCount, self.iPSCount)
        return sRet

    def _dGetDataAsDict(self):
        # name, type, model, SN, position, RPM, size
        dRet = {}
        for name, fun in self.dQueries.items():
            dRet[name] = fun()
        return dRet


class HP3Par_Disk(DASD_Class):
    def __init__(self, iID, sType, sModel, iSize_MB, sSN, sCagePos):
        super().__init__(str(iID), sSN)
        self.sType = sType
        self.sModel = sModel
        self.iSize = iSize_MB // 1024
        self.sCagePos = self.__sDecodeCagePos__(sCagePos)
        self.dQueries = {'name':      lambda: self.sID,
                         'SN':        lambda: self.sSN,
                         'type':      lambda: self.sType,
                         'model':     lambda: self.sModel,
                         'size':      lambda: self.iSize,
                         'position':  lambda: self.sCagePos
                         }
        return

    # showpd -showcols Id,Type,Model,Size_MB,Serial,CagePos
    def __sDecodeCagePos__(self, sCagePos):
        # there are more than one format. I saw 3-field data in CagePos column
        sSide = ''
        lSides = ['Left', 'Right']
        if sCagePos.count(":") == 2:
            iDC, iBay, iMag = sCagePos.split(':')
            # For 3Par 7400 I saw only Magazine=0
            # sFormat = "Cage {0}, Disk {3}, Magazine {2}"
            sFormat = "Cage {0}, Disk {3}"
        elif sCagePos.count(':') == 3:
            iDC, iSide, iMag, iBay = sCagePos.split(':')
            sFormat = "Cage {0}, {1} Side, Magazine {2}, Disk {3}"
            sSide = lSides[iSide]
        else:
            oLog.info("__sDecodeCagePos__: unknown CagePos format: {}".format(sCagePos))
        return sFormat.format(iDC, sSide, iMag, iBay)

    def __repr__(self):
        return("Disk id:{} ({}, {}, {} GiB) at '{}'".format(
               self.sID, self.sType, self.sSN, self.iSize, self.sCagePos))

    def _dGetDataAsDict(self):
        # name, type, model, SN, position, RPM, size
        dRet = {}
        for name, fun in self.dQueries.items():
            dRet[name] = fun()
        return dRet


class PCICardClass:
    reWS = re.compile(r'\s+')

    def __init__(self, sLine):
        """Fill card information from a line from 'shownode -i' output"""
        oLog.debug("PCICardClass.__init__: sLine is: " + sLine)
        lFields = self.reWS.split(sLine.strip())
        oLog.debug("PCICardClass.__init__: line splits as: " + str(lFields))
        sSlot, self.sType, self.sVendor, self.sModel, self.sSN = lFields[1:]
        self.iSlot = int(sSlot)
        return

    def __str__(self):
        sFmt = "Slot:{:1d} Type:{:5s} Vendor:{:8s} Model:{:12s} SN:{}"
        return sFmt.format(self.iSlot, self.sType, self.sVendor, self.sModel, self.sSN)


class RAM_Module:
    def __init__(self, sLine):
        """Fill RAM module info from a line"""
        self.sLine = sLine

    def __str__(self):
        """return module info as a string"""
        return self.sLine

# ============================================================
if __name__ == '__main__':
    # set up logging
    oLog.setLevel(logging.DEBUG)
    oConHdr = logging.StreamHandler()
    oConHdr.setLevel(logging.DEBUG)
    oLog.addHandler(oConHdr)
    oRedis = redis.StrictRedis()
    oAuth = AuthData('zabbix', bUseKey=False, sPasswd='G47ufNDybz')
    my3Par = HP3Par('10.44.0.171', oAuth, 'hp3par02', oRedis)
    print(my3Par.dQueries['shelf-names']())
    print(my3Par.dQueries['ctrl-names']())
    print(my3Par.dQueries['disk-names']())
    print(my3Par._ldGetShelvesAsDicts())

# vim: expandtab : softtabstop=4 : tabstop=4 : shiftwidth=4
