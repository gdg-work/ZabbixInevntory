#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""IBM Storwize/FlashSystem support (newer FlashSystems). Works via SSH connection to target array"""
import logging
import MySSH
import json
import itertools as it
from redis import StrictRedis
# import re
from collections import OrderedDict
import inventoryObjects as inv
# CONSTANTS from a separate module
from local import CACHE_TIME, REDIS_ENCODING, DEFAULT_SSH_PORT

# CONSTANTS
SEP = ','

oLog = logging.getLogger(__name__)


class TabbedValues:
    """helps to parse comma-separated tables"""
    def __init__(self, sHeader, sSep=','):
        """Initializes class, remember number and sequence of fields"""
        self.sSep = sSep
        self.lsFields = None
        self.lsHdr = sHeader.split(self.sSep)
        return

    def _ParseLine(self, sLine):
        """splits a line to fields and associates these fields to header fields"""
        self.lsFields = sLine.split(self.sSep)
        return

    def _sGetField(self, sFName):
        """returns a field from a line parsed before by name or '' if name not found"""
        sRet = ''
        try:
            iIdx = self.lsHdr.index(sFName)
            sRet = self.lsFields[iIdx]
        except ValueError:     # no field found
            sRet = ''
        return sRet

    def _dssParseToDict(self, sLine=None):
        dRet = {}
        if sLine is None:
            # return saved data
            if self.lsFields is None:
                self.lsFields = it.repeat('', len(self.lsHdr))
        else:
            self.lsFields = sLine.split(self.sSep)
        dRet = dict(zip(self.lsHdr, self.lsFields))
        return dRet


class IBMFSException(Exception):
    def __init__(self, sDescr):
        super().__init__()
        self.sDesc = str(sDescr)
        return

    def __str__(self):
        return self.sDescr


class IBMFlashSystem(inv.ClassicArrayClass):
    """IBM FlashSystem object class. Encapsulates main data of the array"""

    def __init__(self, sIP, oAuth, sSysName, oRedisConn):
        super().__init__(sIP, "FlashSystem")
        self.sRedisKeyPrefix = "pyzabbix::FlashSys::" + sSysName + "::"
        self.sSysName = sSysName
        self.sIP = sIP
        self.sSN = 'S/N not determined'
        self.sModel = 'Model not defined'
        self.oAuthData = oAuth
        self.bConnected = False
        self.iEnclosures = 0
        self.iNodes = 0
        self.dSysParams = {}
        self.lDisks = []
        self.lControllers = []
        self.lEnclosures = []
        self.iRedisTimeout = CACHE_TIME
        self.oRedisConnection = oRedisConn
        self.dQueries = {"name": self._sGetName,
                         "sn": self._sGetSN,
                         "model": self._sGetModel,
                         "ctrls": self._iGetNodes,
                         "shelves": self._iGetShelvesN,
                         "disks": self._iNDisks,
                         "ctrl-names": self._lsControllerNames,
                         "shelf-names": self._lsShelfNames,
                         "disk-names":  self._lsDiskNames}
        # fill the real parameters
        self.__FillNodes2__()
        self.__FillEnclosures__()
        self.__FillDisks2__()
        self.__FillArrayParams__()
        return

    def _sGetName(self):
        return self.sSysName

    def _sGetSN(self):
        return self.sSN

    def _sGetModel(self):
        return self.sModel

    def _iGetNodes(self):
        return len(self.lControllers)

    def _iGetShelvesN(self):
        return len(self.lEnclosures)

    def _iNDisks(self):
        return len(self.lDisks)

    def _lsControllerNames(self):
        lRet = []
        for oCtrl in self.lControllers:
            lRet.append(oCtrl.dQueries['name']())
        return lRet

    def _lsShelfNames(self):
        lRet = []
        for oEnc in self.lEnclosures:
            lRet.append(oEnc.dQueries['name']())
        return lRet

    def _lsDiskNames(self):
        lRet = []
        for oDsk in self.lDisks:
            lRet.append(oDsk.dQueries['name']())
        return lRet

    def __sFromArray__(self, sCommand):
        """runs SSH command on the array, return output and caches results"""
        sRet = ''
        sRedisKey = self.sRedisKeyPrefix + "__sFromArray__::" + sCommand
        try:
            sRet = json.loads(self.oRedisConnection.get(sRedisKey).decode(REDIS_ENCODING))
        except AttributeError:     # no data in Redis
            try:    # connect
                oConn = MySSH.MySSHConnection(self.sIP, DEFAULT_SSH_PORT, self.oAuthData)
            except Exception as e:
                oLog.error("SSH failed on login.")
                oLog.debug(e)
                raise(IBMFSException('Failed to login, terminating'))
            try:            # run command
                sRet = oConn.fsRunCmd(sCommand)
                oConn.close()
                # save results to Redis
                self.oRedisConnection.set(sRedisKey,
                                          json.dumps(sRet).encode(REDIS_ENCODING),
                                          self.iRedisTimeout)
            except Exception as e:
                oLog.error("Error when running command by SSH.")
                oLog.debug(e)
            finally:
                oConn.close()
        return sRet

    def __dsFromArray__(self, lsCommands):
        """
        Connects to array, runs a SERIES of commands and return results as a dictionary
        with commands as keys and returned output as values
        """
        dData = OrderedDict({})
        sRedisKey = self.sRedisKeyPrefix + "__dsFromArray__"
        try:
            oConn = MySSH.MySSHConnection(self.sIP, DEFAULT_SSH_PORT, self.oAuthData)
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
                        raise IBMFSException
                    self.oRedisConnection.hset(sRedisKey, sCmd, sData.encode(REDIS_ENCODING))
                    self.oRedisConnection.expire(sRedisKey, self.iRedisTimeout)
                dData[sCmd] = sData
            oConn.close()
        except Exception as e:
            oLog.error('__dsFromArray__: SSH failed on login')
            oLog.debug('__dsFromArray__: Additional info: ' + str(e))
        return dData

    def __FillArrayParams__(self):
        # common information (name and model)
        lCommonParams = self.__sFromArray__('lssystem -delim {}'.format(SEP)).split('\n')
        # oLog.debug("__FillArrayParams__: lCommonParams: " + str(lCommonParams))
        for sName, sVal in (l.split(SEP) for l in lCommonParams if SEP in l):
            if sName == 'product_name':
                self.sModel = sVal
            elif sName == 'name':
                self.sSysName = sVal
            else:
                pass
        oLog.debug('Array info: name:"{}", model "{}"'.format(self.sSysName, self.sModel))
        return

    def __FillEnclosures__(self):
        # enclosure information
        lEnclInfo = [l for l in self.__sFromArray__('lsenclosure -delim {}'.format(SEP)).split('\n')
                     if len(l.strip()) > 0]
        sHeader = lEnclInfo.pop(0)
        self.iEnclosures = len(lEnclInfo)
        oEnclTable = TabbedValues(sHeader)
        for sEData in lEnclInfo:
            oEnclTable._ParseLine(sEData)
            self.lEnclosures.append(IBMFlashEnclosure(
                oEnclTable._sGetField('id'),
                oEnclTable._sGetField('product_MTM'),
                oEnclTable._sGetField('serial_number'),
                oEnclTable._sGetField('drive_slots'),
                oEnclTable._sGetField('online_PSUs'), self))
        # oLog.debug('Encl. header: ' + str(sHeader))
        oLog.debug('Encl. info list: ' + str(lEnclInfo))
        return

    def __FillNodes__(self):
        """Fills nodes information"""
        lsNodesInfo = [l for l in self.__sFromArray__('lsnode -delim {}'.format(SEP)).split('\n')
                       if len(l.strip()) > 0]
        sHdr = lsNodesInfo.pop(0)
        self.iNodes = len(lsNodesInfo)
        oNodesTable = TabbedValues(sHdr)
        for sNodeData in lsNodesInfo:
            iPortsCount = 0
            lAdapters = []
            lCPUs = []
            oNodesTable._ParseLine(sNodeData)
            iNum = oNodesTable._sGetField('id')
            # details
            lsThisNodeData = [l for l in
                              self.__sFromArray__('lsnode -delim {0} {1}'.format(SEP, iNum)).split('\n')
                              if len(l.strip()) > 0]
            for sKey, sVal in (l.split(SEP) for l in lsThisNodeData):
                if sKey == 'port_id':
                    iPortsCount += 1
                elif sKey == 'product_mtm':
                    sMTM = sVal
                else:
                    pass
            # hardware components
            lsThisNodeData = [l for l in
                              self.__sFromArray__('lsnodehw -delim {0} {1}'.format(SEP, iNum)).split('\n')
                              if len(l.strip()) > 0]
            for sKey, sVal in (l.split(SEP) for l in lsThisNodeData):
                if sKey == 'cpu_count':
                    self.iCPUs = int(sVal)
                elif sKey == 'cpu_actual':
                    lCPUs.append(sVal)
                elif sKey == 'adapter_actual':
                    lAdapters.append(sVal)
                elif sKey == 'memory_actual':
                    iMem = int(sVal)
                else:
                    pass
            self.lControllers.append(IBMFlashNode(
                iNum, oNodesTable._sGetField('name'),
                oNodesTable._sGetField('panel_name'), iPortsCount, sMTM, lCPUs,
                lAdapters, iMem))
        return

    def __FillNodes2__(self):
        """Fills nodes information, updated version (with a sequence of command from one connection"""
        lsNodesInfo = [l for l in self.__sFromArray__('lsnode -delim {}'.format(SEP)).split('\n')
                       if len(l.strip()) > 0]
        sHdr = lsNodesInfo.pop(0)
        self.iNodes = len(lsNodesInfo)
        oNodesTable = TabbedValues(sHdr)
        lCmds = []
        lNodes = []
        for sNodeData in lsNodesInfo:
            oNodesTable._ParseLine(sNodeData)
            iNum = oNodesTable._sGetField('id')
            lNodes.append(int(iNum))
            lCmds.append('lsnode -delim {0} {1}'.format(SEP, iNum))
            lCmds.append('lsnodehw -delim {0} {1}'.format(SEP, iNum))
        dArrayReplies = self.__dsFromArray__(lCmds)

        self.lControllers = []
        for iNodeNum in lNodes:
            iPortsCount = 0
            lAdapters = []
            lCPUs = []
            sCmd1 = 'lsnode -delim {0} {1}'.format(SEP, iNum)
            sCmd2 = 'lsnodehw -delim {0} {1}'.format(SEP, iNum)
            sReply = dArrayReplies[sCmd1]
            lsThisNodeData = [l for l in sReply.split("\n") if len(l.strip()) > 0]
            for sKey, sVal in (l.split(SEP) for l in lsThisNodeData):
                if sKey == 'port_id':
                    iPortsCount += 1
                elif sKey == 'name':
                    sNodeName = sVal
                elif sKey == 'product_mtm':
                    sMTM = sVal
                elif sKey == 'panel_name':
                    sNodeSN = sVal
                else:
                    pass
            sReply = dArrayReplies[sCmd2]
            lsThisNodeData = [l for l in sReply.split("\n") if len(l.strip()) > 0]
            for sKey, sVal in (l.split(SEP) for l in lsThisNodeData if SEP in l):
                if sKey == 'cpu_count':
                    self.iCPUs = int(sVal)
                elif sKey == 'cpu_actual':
                    lCPUs.append(sVal)
                elif sKey == 'adapter_actual':
                    lAdapters.append(sVal)
                elif sKey == 'memory_actual':
                    iMem = int(sVal)
                else:
                    pass
            self.lControllers.append(IBMFlashNode(
                iNodeNum, sNodeName, sNodeSN, iPortsCount, sMTM, lCPUs,
                lAdapters, iMem))
            # end for
        return

    def __FillDisks__(self):
        """Fills list of disks"""
        sDiskIDs = set([])
        lAllDisks = [l.strip() for l in
                     self.__sFromArray__('lsdrive -bytes -delim {}'.format(SEP)).split('\n')
                     if len(l.strip()) > 0]
        sHdr = lAllDisks.pop(0)
        oDisksTable = TabbedValues(sHdr)
        for sDsk in lAllDisks:
            # parse summary output
            dssDiskData = oDisksTable._dssParseToDict(sDsk)
            sId = dssDiskData['id']
            sDiskIDs.add(sId)
            # next, receive each disk's individual information for additional fields
            lsOut = self.__sFromArray__("lsdrive -bytes -delim {0} {1}".format(SEP, sId)).split('\n')
            for sKey, sVal in [l.split(SEP) for l in lsOut if SEP in l]:
                dssDiskData[sKey] = sVal
            # make the disk object
            sPosition = "Enclosure {}, slot {}".format(dssDiskData['enclosure_id'], dssDiskData['slot_id'])
            self.lDisks.append(IBMFlashCard(sId, sPosition, dssDiskData))
        return

    def __FillDisks2__(self):
        """Fills list of disks, improved version"""
        sDiskIDs = set([])
        dCmds = {}
        dSummary = {}
        lAllDisks = [l.strip() for l in
                     self.__sFromArray__('lsdrive -bytes -delim {}'.format(SEP)).split('\n')
                     if len(l.strip()) > 0]
        sHdr = lAllDisks.pop(0)
        oDisksTable = TabbedValues(sHdr)
        # make list of disks and commands querying disks parameters
        for sDsk in lAllDisks:
            oLog.debug("__FillDisks2__: disk info {}".format(sDsk))
            dssDiskData = oDisksTable._dssParseToDict(sDsk)
            sId = dssDiskData['id']
            sDiskIDs.add(sId)
            dSummary[sId] = dssDiskData
            dCmds[sId] = "lsdrive -bytes -delim {0} {1}".format(SEP, sId)
        # execute commands on the array
        dReplies = self.__dsFromArray__(dCmds.values())
        for sId in sDiskIDs:
            lsReply = dReplies[dCmds[sId]].split('\n')
            dssDiskData = dSummary[sId]
            for sKey, sVal in [l.split(SEP) for l in lsReply if SEP in l]:
                dssDiskData[sKey] = sVal
            sPosition = "Enclosure {}, slot {}".format(dssDiskData['enclosure_id'], dssDiskData['slot_id'])
            self.lDisks.append(IBMFlashCard(sId, sPosition, dssDiskData))
        return

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
            self.__FillNodes__()
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
        if self.lEnclosures == []:
            self.__FillEnclosures__()
        try:
            for oShelfObj in self.lEnclosures:
                ldRet.append(oShelfObj._dGetDataAsDict())
        except Exception as e:
            oLog.warning("Exception when filling disk enclosures' parameters list")
            oLog.debug("Exception: " + str(e))
        return ldRet


class IBMFlashEnclosure(inv.DiskShelfClass):
    """IBM FlashSystem enclosure class"""
    def __init__(self, iNum, sMTM, sSN, iSlots, iPSUs, oArray):
        self.iNum = iNum
        self.sType, self.sModel = sMTM.split('-')
        self.sSN = sSN
        self.iNPwr = iPSUs
        # check free/occupied slots information
        lSlotsData = [l for l in
                      oArray.__sFromArray__("lsenclosureslot -delim , {}".format(self.iNum)).split('\n')
                      if len(l.strip()) > 0]
        oLog.debug("IBMFlashEnclosure constructor: lSlotsData: {}".format(str(lSlotsData)))
        sSlotsHdr = lSlotsData.pop(0)
        oSlotsTable = TabbedValues(sSlotsHdr)
        iTotalSlots = len(lSlotsData)
        self.iDiskSlots = iTotalSlots
        iOccupiedSlots = 0
        for sLine in lSlotsData:
            oSlotsTable._ParseLine(sLine)
            if oSlotsTable._sGetField('drive_present') == 'yes':
                iOccupiedSlots += 1
        self.dQueries = {'name':       lambda: 'Enclosure {}'.format(self.iNum),
                         'type':       lambda: self.sType,
                         'model':      lambda: self.sModel,
                         'sn':         lambda: self.sSN,
                         'disks':      lambda: iOccupiedSlots,
                         'disk-slots': lambda: self.iDiskSlots,
                         'ps-amount':  lambda: self.iNPwr}
        return


class IBMFlashNode(inv.ControllerClass):
    """IBM FlashSystem node (controller) class"""
    def __init__(self, iNum, sName, sSN, iPorts, sMTM, lProcs, lAdapters, iMemGBs):

        def all_equal(iterable):
            "Returns True if all the elements are equal to each other"
            g = it.groupby(iterable)
            return next(g, True) and not next(g, False)

        self.iNum = iNum
        self.sName = sName
        self.sSN = sSN
        self.iPorts = iPorts
        self.iMemGBs = iMemGBs
        self.sType, self.sModel = sMTM.split('-')
        assert all_equal(lProcs)
        self.sCPUs = '{} * {}'.format(len(lProcs), lProcs[0])
        # Pack a list of adapters to a string
        lAds = zip(it.count(1), lAdapters)
        self.sAdapters = "\n".join(["{}: {}".format(n, s) for n, s in lAds])

        self.dQueries = {'name':   lambda: str(self.iNum),
                         'sn':     lambda: self.sSN,
                         'ports':  lambda: self.iPorts,
                         'type':   lambda: self.sType,
                         'model':  lambda: self.sModel,
                         'cpu':    lambda: self.sCPUs,
                         'pci':    lambda: self.sAdapters,
                         'ram':    lambda: self.iMemGBs}
        return


class IBMFlashCard(inv.DASD_Class):
    """IBM flashSystem flash card, an equivalent of a disk drive"""
    def __init__(self, sId, sPos, dssDiskDict):
        self.sID = sId
        self.sPos = sPos
        # oLog.debug('IBMFlashCard constructor called with params: id:{0}, pos:{1}, dict:{2}'.format(
        #            sId, sPos, str(dssDiskDict)))
        self.sProdID = dssDiskDict['product_id']
        self.sFruPn = dssDiskDict['FRU_part_number']
        self.sSN = dssDiskDict['FRU_identity']
        self.sType = dssDiskDict['tech_type']
        # we need size it GBs
        self.sSizeGB = int(int(dssDiskDict['capacity']) / 2**30)    # GiB
        self.dQueries = {'name':     lambda: self.sID,
                         'sn':       lambda: self.sSN,
                         'type':     lambda: self.sType,
                         'model':    lambda: self.sProdID,
                         'size':     lambda: self.sSizeGB,
                         'position': lambda: self.sPos}
        return

    # def _dGetDataAsDict() moved to Component superclass

if __name__ == '__main__':
    print("This is a library, not an executable")
    # access information
    # from access import IBM_FS as tsys
    from access import IBM_FS2 as tsys
    # test section: logging set-up
    oLog.setLevel(logging.DEBUG)
    oConHdr = logging.StreamHandler()
    oConHdr.setLevel(logging.DEBUG)
    oLog.addHandler(oConHdr)
    # testing
    oRedisConn = StrictRedis()
    oAuth = MySSH.AuthData(tsys.sUser, tsys.bUseKey, sPasswd=tsys.sPass)
    oFS = IBMFlashSystem(tsys.sIP, oAuth, tsys.sSysName, oRedisConn)
    # print("Array parameters: {}".format(oFS._dGetArrayInfoAsDict(oFS.dQueries.keys())))
    print("==== Array parameters =====")
    print(str(oFS._dGetArrayInfoAsDict(oFS.dQueries.keys())))
    print("===== Controllers =======")
    print('\n'.join(str(d) for d in oFS._ldGetControllersInfoAsDict()))
    print("====== Enclosures =======")
    print('\n'.join(str(d) for d in oFS._ldGetShelvesAsDicts()))
    print("========= Disks ========")
    print('\n'.join(str(d) for d in oFS._ldGetDisksAsDicts()))


# vim: expandtab : softtabstop=4 : tabstop=4 : shiftwidth=4
