#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""A module for supporting IBM disk array DS-8000"""

import logging
import re
import itertools as it
import inventoryObjects as invobj
from subprocess import check_output, CalledProcessError, STDOUT

# CONSTANTS
SMCLI_PATH = "/opt/IBM_DS/client/SMcli"

oLog = logging.getLogger(__name__)


class IBM_DS(invobj.ClassicArrayClass):
    reControllers =      re.compile(r'^\s*CONTROLLERS-{10}-*$')
    reSummary =          re.compile(r'^\s*SUMMARY-{10}-*$')
    reEnclosures =       re.compile(r'^\s*ENCLOSURES-{10}-*$')
    reDrivesBegin =      re.compile(r'^\s*DRIVES-{10}-*$')
    reSectionDelimeter = re.compile(r'-{25,40}$')
    reArrayName =        re.compile(r'^\s*PROFILE FOR STORAGE SUBSYSTEM: (\w+)\s')
    reCtrlNumber =       re.compile(r'^\s*Number of controllers:\s+(\d{1,2})$')
    reEnclNum =          re.compile(r'^\s*Number of drive enclosures:\s+(\d{1,2})\s*$')
    reWWN =              re.compile(r'^\s*Storage Subsystem world-wide identifier \(ID\):\s+(\w+)\s*$')
    reDrives =           re.compile(r'^\s*Number of drives:\s+(\d+)\s*$')
    reCtrlBegin =        re.compile(r'^\s*Controller in Enclosure \d{1,3}, Slot ([AB])\s*$')
    reCtrlEnclName =     re.compile(r'^\s*Controller Enclosure (\d+) Overall Component Information\s*')
    reDiskEnclName =     re.compile(r'^\s*Drive Enclosure (\d+) Overall Component Information\s*')
    rePwrFanCanisters =  re.compile(r'^\s*Power Supplies Detected:\s+(\d+)\s*$')
    reTotalDrives =      re.compile(r'^\s*Number of drives:\s+(\d+)\s*$')
    reDriveDetailsHdr =  re.compile(r'\s*Drive at Enclosure (\d+), Slot (\d+)\s*')

    def __init__(self, sArrayAddr):
        self.sName = ''
        self.sSN = ''
        self.sWWN = ''
        self.iCtrls = 0
        self.iEncls = 0
        self.iDrives = 0
        self.iPwrSupplies = 0
        self.lControllers = []
        self.lDisks = []
        self.lEnclosures = []
        try:
            lCommand = [SMCLI_PATH, sArrayAddr, '-c', 'show storagesubsystem;']
            sData = check_output(lCommand, stderr=STDOUT, universal_newlines=True, shell=False)
            oLog.debug('Output from command: ' + sData)
        except CalledProcessError as e:
            sData = e.output
            oLog.info('Non-zero return status from SMcli')

        lsData = sData.split('\n')
        self.lsData = lsData
        # fill information
        self.__FillGeneral__()
        self.__FillControllers__()
        self.__FillEnclosures__()
        self.__FillDisks__()

        self.dQueries = {
            'name':        self.getName,
            'wwn':         self.getWWN,
            'ctrls':       self.getCtrls,
            'shelves':     self.getShelves,
            'disks':       self.getDrives,
            'type':        self._sGetType,
            'ps-amount':   self._iGetPSAmount,
            'ctrl-names':  self._lGetCtrls,
            'shelf-names': self._lGetShelves,
            'disk-names':  self._lGetDisks}
        return

    def getName(self):
        return self.sName

    def getWWN(self):
        return self.sWWN

    def getCtrls(self):
        return self.iCtrls

    def getShelves(self):
        return self.iEncls

    def getDrives(self):
        return self.iDrives

    def __FillGeneral__(self):
        iterName = it.dropwhile(lambda x: not(self.reArrayName.match(x)), self.lsData)
        l = next(iterName)    # array name line
        self.sName = self.reArrayName.match(l).group(1)
        iterSumm = it.dropwhile(lambda x: not(self.reSummary.match(x)), self.lsData)
        for l in iterSumm:
            if self.reEnclNum.match(l):
                self.iEncls = int(self.reEnclNum.match(l).group(1))
            elif self.reWWN.match(l):
                self.sWWN = self.reWWN.match(l).group(1)
            elif self.reDrives.match(l):
                self.iDrives = int(self.reDrives.match(l).group(1))
            elif self.reSectionDelimeter.match(l):
                break
        oLog.debug('Array name: {}, Enclosures: {} ({} drives), WWN: {}'.format(
            self.sName, self.iEncls, self.iDrives, self.sWWN))
        return

    def __FillControllers__(self):
        """fill controllers list from a text output of 'show storagesubsystem' output"""
        iterCtrls = it.dropwhile(lambda x: not(self.reControllers.match(x)), self.lsData)
        # oLog.debug('Iterator for controllers: {}'.format(str(iterCtrls)))
        next(iterCtrls)
        iLinesCounter = 0
        lsControllersData = []
        for l in (_.strip() for _ in iterCtrls):
            iLinesCounter += 1
            if self.reSectionDelimeter.search(l):
                # end of controller's section
                break
            elif self.reCtrlNumber.match(l):
                oMatch = self.reCtrlNumber.match(l)
                self.iCtrls = int(oMatch.group(1))
                oLog.debug('# of controllers: {}'.format(self.iCtrls))
            else:
                lsControllersData.append(l.strip())
                continue
        self.lControllers = [None] * self.iCtrls
        oLog.debug('Controllers data: {} lines'.format(iLinesCounter))

        # now we need to separate A and B controllers' data to separate lists
        iSecondCtrlIdx = 0
        for i in range(0, len(lsControllersData)):
            if self.reCtrlBegin.match(lsControllersData[i]):
                iSecondCtrlIdx = i
            else:
                # print(lsControllersData[i])
                pass
        self.lControllers[0] = IBM_DS_Controller(lsControllersData[:iSecondCtrlIdx])
        self.lControllers[1] = IBM_DS_Controller(lsControllersData[iSecondCtrlIdx:])
        return

    def __FillEnclosures__(self):
        """Fill disk enclosures' information from output of 'show storagesubsystem' SMcli command"""
        iterDEs = it.dropwhile(lambda x: not(self.reEnclosures.match(x)), self.lsData)
        iterCtrlEncl = it.dropwhile(lambda x: not(self.reCtrlEnclName.match(x)), iterDEs)
        # print(next(iterCtrlEncl))
        l = next(it.dropwhile(lambda x: not(self.rePwrFanCanisters.match(x)), iterCtrlEncl))
        self.iPwrSupplies = int(self.rePwrFanCanisters.match(l).group(1))
        oLog.debug("Power supplies in controllers' enclosure: {}".format(self.iPwrSupplies))
        # iterDriveEncl = it.dropwhile(lambda x: not(self.reDiskEnclName.match(x)), iterDEs)
        while True:
            try:
                iterDriveEncl = it.dropwhile(lambda x: not(self.reDiskEnclName.match(x)), iterDEs)
                iterCurr, iterRest = it.tee(iterDriveEncl)
                oEnclosure = IBM_DS_DriveEnclosure(iterCurr)
                self.lEnclosures.append(oEnclosure)    # XXX добавляются вразброс, в порядке обнаружения
                # print(next(iterRest))
                # iterDriveEncl = iterRest
            except StopIteration:
                break
        return

    def __FillDisks__(self):
        """Fill physical disks information from output of 'show storagesystem' command"""
        # local constants
        reRPM =  re.compile(r'^\s*Speed:\s+([\d,]+) RPM\s*$')
        reSize = re.compile(r'^\s*Usable capacity:\s+([\d.]+) GB\s*$')
        reType = re.compile(r'^\s*Interface type:\s+(\w.+\w)\s*$')
        reSN =   re.compile(r'^\s*Serial number:\s+(\w+)\s*$')
        reProd = re.compile(r'^\s*Product ID:\s+(\w.+\w)\s*$')
        sType = 'Type not known'
        sSerNum = 'S/N not known'
        iRPM = 0
        rSize = 0
        iterDrives = it.dropwhile(lambda x: not(self.reDrivesBegin.match(x)), self.lsData)
        iterDrives = it.dropwhile(lambda x: not(self.reTotalDrives.match(x)), iterDrives)
        sLine = next(iterDrives).strip()
        self.iDrives = int(self.reTotalDrives.match(sLine).group(1))
        oLog.debug("Total # of drives: {}".format(self.iDrives))
        # go to 'details' section
        iterDrives = it.dropwhile(lambda x: not(re.match(r'^\s*DETAILS\s*$', x)), iterDrives)
        # cycle by drive in 'details' section
        while True:
            try:
                iterNextDrive = it.dropwhile(lambda x: not(self.reDriveDetailsHdr.match(x)), iterDrives)
                # gather information
                sDriveHdrLine = next(iterNextDrive).strip()
                oMatch = self.reDriveDetailsHdr.match(sDriveHdrLine)
                iEncl, iDrive = (oMatch.group(1), oMatch.group(2))
                sDskName = "E{}:D{}".format(iEncl, iDrive)
                # oLog.debug('Drive found: ' + sDskName)
                iterDrive = it.islice(iterNextDrive, 30)  # 30 is a length of drive section
                for sLine in (_.strip() for _ in iterDrive):
                    if reSize.match(sLine):
                        rSize = float(reSize.match(sLine).group(1))
                    elif reType.match(sLine):
                        sType = reType.match(sLine).group(1)
                    elif reRPM.match(sLine):
                        sRPM = reRPM.match(sLine).group(1)
                        # sRPM contains something like aa,bbb. Need to strip out ','
                        sRPM = sRPM.replace(',', '', 1)
                        iRPM = int(sRPM)
                    elif reSN.match(sLine):
                        sSerNum = reSN.match(sLine).group(1)
                    elif reProd.match(sLine):
                        sProdNum = reProd.match(sLine).group(1)
                    else:
                        pass
                # make 'drive' object and add it to a list
                # oLog.debug('{} Drive found: name {}, P/N {}, SN: {} size {} GB, speed {} RPM'.format(
                #     sType, sDskName, sProdNum, sSerNum, rSize, iRPM))
                oDrive = IBM_DS_Drive(sDskName, sType, sProdNum, sSerNum, rSize, iRPM, iEncl, iDrive)
                self.lDisks.append(oDrive)
                # next top-lvl cycle
            except StopIteration:
                oLog.debug('End of drives section')
                break
        return

    def _lGetCtrls(self):
        """Returns a list of controller's names"""
        lRet = []
        if self.lControllers == []:
            self.__FillControllers__()
        for oCtrl in self.lControllers:
            lRet.append(oCtrl.dQueries['name']())
        return lRet

    def _lGetShelves(self):
        """Returns a list of disk enclosure's names"""
        lRet = []
        if self.lEnclosures == []:
            self.__FillEnclosures__()
        for oDE in self.lEnclosures:
            lRet.append(oDE.dQueries['name']())
        return lRet

    def _lGetDisks(self):
        lRet = []
        if self.lDisks == []:
            self.__FillDisks__()
        for oDsk in self.lDisks:
            lRet.append(oDsk.dQueries['name']())
        return lRet

    def _sGetType(self):
        if self.lControllers == []:
            self.__FillControllers__()
        return self.lControllers[0].dQueries['model']()

    def _iGetPSAmount(self):
        if self.iPwrSupplies == 0:
            self.__FillControllers__()
        return self.iPwrSupplies

#    def _dGetArrayInfoAsDict(self, ssKeys):
#        """
#        Array-wide parameters as a dictionary.
#        Parameter -- a set of keys/requests
#        Returns: a dictionary {key:value}
#        """
#        dRet = {}
#        for sKey in ssKeys:
#            if sKey in self.dQueries:
#                dRet[sKey] = self.dQueries[sKey]()
#        return dRet

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
        oLog.debug('Entered IBM DS _ldGetShelvesAsDicts')
        if self.lEnclosures == []:
            self.__FillEnclosures__()
        try:
            for oShelfObj in self.lEnclosures:
                ldRet.append(oShelfObj._dGetDataAsDict())
        except Exception as e:
            oLog.warning("Exception when filling disk enclosures' parameters list")
            oLog.debug("Exception: " + str(e))
        return ldRet


class IBM_DS_Controller(invobj.ControllerClass):
    reCtrlBegin = re.compile(r'^Controller in Enclosure \d{1,3}, Slot ([AB])$')
    reModelLine = re.compile(r'^Model name:\s+(\w+)$')
    reSN =        re.compile(r'^Serial number:\s+(\w+)$')
    reHostPort =  re.compile(r'^Host interface:')
    reProdID =    re.compile(r'^Product ID:\s+(\w.*)$')
    rePartNum =   re.compile(r'^Part number:\s+(\d.*)$')
    reEmpty =     re.compile(r'')

    def __init__(self, iterDataStream):
        """
        Takes an iterator with controller's data, makes internal structures of controller.
        Advances iterator iterDataStream!
        """
        oLog.debug("Entered IBM_DS8K_Controller.__init__")
        self.iPortCount = 0
        for l in iterDataStream:
            # print(l)
            # a line with controller's name is already used by calling procedure
            if self.reCtrlBegin.match(l):
                self.sName = self.reCtrlBegin.match(l).group(1)
                oLog.debug('Controller name: ' + self.sName)
            elif self.reModelLine.match(l):
                self.sModel = self.reModelLine.match(l).group(1)
                oLog.debug('Controller model name: {}'.format(self.sModel))
            elif self.reSN.match(l):
                self.sSN = self.reSN.match(l).group(1)
                oLog.debug('Controller S/N: {}'.format(self.sSN))
            elif self.reProdID.match(l):
                self.sProdID = self.reProdID.match(l).group(1)
                oLog.debug("Product ID: " + self.sProdID)
            elif self.rePartNum.match(l):
                self.sPartNum = self.rePartNum.match(l).group(1)
                oLog.debug("Part number: '{}'".format(self.sPartNum))
            elif self.reHostPort.match(l):
                self.iPortCount += 1
            else:
                pass
        oLog.debug("Host ports: {}".format(self.iPortCount))
        self.dQueries = {
            'name':  lambda: self.sName,
            'sn':    lambda: self.sSN,
            'model': lambda: self.sProdID,
            'type':  lambda: self.sPartNum,
            'ports': lambda: self.iPortCount}
        return


class IBM_DS_DriveEnclosure(invobj.DiskShelfClass):
    reDiskEnclName =    re.compile(r'^\s*Drive Enclosure (\d+) Overall Component Information\s*')
    reDE_PN =           re.compile(r'^\s*Part number:\s+PN\s+(\w+)\s*')
    reDE_SN =           re.compile(r'^\s*Serial number:\s+SN\s+(\w+)\s*')
    reProdID =           re.compile(r'^\s*Product ID:\s+(\w+)\s*')
    rePwrSupplies =     re.compile(r'^\s*Power Supplies Detected:\s+(\d+)\s*')

    def __init__(self, iterLines):
        # current line is an enclosure header
        self.iNum = -1
        self.sPN = ''
        self.sSN = ''
        self.sID = ''
        self.sProdID = ''
        self.iPwrSupplies = 0
        iLinesCounter = 0   # for iterator debugging
        sLine = next(iterLines)
        if self.reDiskEnclName.match(sLine):
            self.iNum = int(self.reDiskEnclName.match(sLine).group(1))
            self.sID = "Drive Enclosure {}".format(self.iNum)
            next(iterLines)    # shift pointer
        iterMyLines = it.takewhile(lambda x: not(self.reDiskEnclName.match(x)), iterLines)
        while True:
            try:
                # print(sLine)
                sLine = next(iterMyLines).strip()
                iLinesCounter += 1
                if self.sPN == '' and self.reDE_PN.match(sLine):
                    self.sPN = self.reDE_PN.match(sLine).group(1)
                elif self.sSN == '' and self.reDE_SN.match(sLine):
                    self.sSN = self.reDE_SN.match(sLine).group(1)
                elif self.sProdID == '' and self.reProdID.match(sLine):
                    # really this line is from ESM canister
                    self.sProdID = self.reProdID.match(sLine).group(1)
                elif self.rePwrSupplies.match(sLine):
                    self.iPwrSupplies = int(self.rePwrSupplies.match(sLine).group(1))
                    break    # go out of cycle
                elif self.reDiskEnclName.match(sLine):
                    break    # next enclosure begins
                elif sLine == '':
                    pass   # skip empty lines
                else:
                    # oLog.debug("Line '{}' doesn't match".format(sLine))
                    pass
            except StopIteration:
                # oLog.debug('Drive Enclosure __init__: Must not be here')
                break
        # oLog.debug('Processed {} lines'.format(iLinesCounter))
        oLog.debug("Disk enclosure {} found, PN: {}, SN:{}, # of PwrSupplies: {}".format(
            self.iNum, self.sPN, self.sSN, self.iPwrSupplies))
        self.dQueries = {'name':      lambda: self.sID,
                         'sn':        lambda: self.sSN,
                         'type':      lambda: self.sProdID,
                         'model':     lambda: self.sPN,
                         'ps-amount': lambda: self.iPwrSupplies
                         }
        return


class IBM_DS_Drive(invobj.DASD_Class):
    """Disk drive"""
    def __init__(self, sName, sType, sPN, sSN, rSize, iRPM, iEncl, iBay):
        self.sID = sName
        self.sSN = sSN
        self.sModel = sPN
        self.sType = sType
        self.rSize = rSize
        self.iRPM = iRPM
        self.sPos = "Enclosure {} Bay {}".format(iEncl, iBay)
        self.dQueries = {"name":     lambda: self.sID,
                         "sn":       lambda: self.sSN,
                         "model":    lambda: self.sModel,
                         "size":     lambda: int(self.rSize),
                         "disk-rpm": lambda: self.iRPM,
                         "position": lambda: self.sPos,
                         "type":     lambda: self.sType}
        return


if __name__ == "__main__":
    # test section: logging set-up
    oLog.setLevel(logging.DEBUG)
    oConHdr = logging.StreamHandler()
    oConHdr.setLevel(logging.DEBUG)
    oLog.addHandler(oConHdr)
    # tests
    lLines = []
    # for l in open(",ibmds8k.out", "r"):
    #     lLines.append(l)
    oDS = IBM_DS("10.44.1.18")
    oDS.__FillGeneral__()
    oDS.__FillControllers__()
    oDS.__FillEnclosures__()
    oDS.__FillDisks__()
    # print(str(oDS._lGetCtrls()))
    # print(str(oDS._lGetShelves()))
    # print(str(oDS._lGetDisks()))
    # print(str(oDS._ldGetShelvesAsDicts()))
    # print(str(oDS._ldGetControllersInfoAsDict()))
    # print(str(oDS._ldGetDisksAsDicts()))
    print(str(oDS._dGetArrayInfoAsDict(['wwn', 'sn', 'model', 'shelves', 'disks'])))

# vim: expandtab : softtabstop=4 : tabstop=4 : shiftwidth=4
