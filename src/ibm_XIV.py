#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A module for supporting IBM XIV scale-out storage systems.
Each component of XIV has an unique identifier (component_id) that depends of
place given for this component in the system.  For example: "1:Disk:2:10".


При рефакторинге нужно будет переписать с вызовом Java класса из Python
через какой-нибудь py/java интерфейс (pyjnius не заработал на целевой машине)
"""

import inventoryObjects as inv
from local import XCLI_PATH, REDIS_ENCODING
from subprocess import check_output, CalledProcessError, STDOUT
# from redis import StrictRedis
import csv
import os
import logging

# CONSTANTS
REDIS_PREFIX = "pyzabbix::FlashSys::"
FAKE_HOME = '/tmp'


oLog = logging.getLogger(__name__)


# -- Helper functions --
def _iGetNodeNum(sID):
    sRet = ''
    sSep = ':'
    iColonsCount = sID.count(sSep)
    if iColonsCount == 3:
        sRet = int(sID.split(sSep)[2])
    elif iColonsCount == 2:
        sRet = int(sID.split(sSep)[2])
    else:
        oLog.error("_iGetNodeNum: Incorrect Component-ID structure")
    return sRet


def _sMkNodeID(iNum):
    return "1:Module:{}".format(iNum)


class XIVError(Exception):
    def __init__(self, sMsg):
        self.sMsg = sMsg
        return

    def __str__(self):
        return self.sMsg


# service class for XIV command line processing
class XIV_Collections_Service:

    def __init__(self, oSystem):
        self.oSystem = oSystem
        return

    def _FillList(self, oList):
        """Fills in the lists in oList object from XCLI output"""
        try:
            sCmdLine = oList.sMyListCommand
            lData = self.oSystem._lsRunCommand(sCmdLine)
            if len(lData) == 0:
                raise XIVError('No output from command ' + sCmdLine)
            oCSV = csv.DictReader(lData,  delimiter=',', quotechar='"')
            # oList.lData = list(lData)
            lCSVData = list(oCSV)
            oList.oCSV = list(lCSVData)    # copy
            for dData in lCSVData:
                sID = dData['Component ID']
                bHealthy = dData['Currently Functioning'] == 'yes'
                bHealthy = bHealthy and dData['Status'] == 'OK'
                oList._AddID(sID, bHealthy)
                if not bHealthy:
                    oLog.debug('Failed component: {}'.format(sID))
        except AttributeError as e:
            oLog.error('No command defined for object: ' + str(type(oList)))
            raise(e)
        oList._PostProcessOutput()
        return


class IBM_XIV_Storage(inv.ScaleOutStorageClass):
    def __init__(self, sIP, sUser, sPass, oRedis, sName):
        self.sRedisPrefix = REDIS_PREFIX + "XIV::" + sName + "::"
        self.sIP = sIP
        self.sSysName = sName
        self.sUser = sUser
        self.sPass = sPass
        self.oRedisDB = oRedis
        self.oFillSvc = XIV_Collections_Service(self)
        self.oNodesList = IBM_XIV_NodesList(self)
        self.oDisksList = IBM_XIV_DisksList(self)
        self.oCFList = IBM_XIV_CompactFlashList(self)
        self.oDIMMs = IBM_XIV_DIMMSlist(self)
        self.oPSUs = IBM_XIV_PwrSuppliesList(self)
        self.oUPSs = IBM_XIV_UPS_List(self)
        self.oSwitches = IBM_XIV_SwitchesList(self)
        self.oMMs = IBM_XIV_MaintenanceModulesList(self)
        self.oNICs = IBM_XIV_NICsList(self)
        self.oFCs = IBM_XIV_FCPortsList(self)
        # fill in the data from an array
        for oList in [self.oNodesList, self.oDisksList, self.oCFList,
                      self.oDIMMs, self.oPSUs, self.oUPSs, self.oSwitches,
                      self.oMMs, self.oNICs, self.oFCs]:
            self.oFillSvc._FillList(oList)
        self.dQueries = {"name": lambda: self.sSysName,
                         "node-names":   self.oNodesList._lsListNames,
                         "switch-names": self.oSwitches._lsListNames,
                         "disk-names":   self.oDisksList._lsListNames,
                         "ups-names":    self.oUPSs._lsListNames,
                         "cf-names":     self.oCFList._lsListNames,
                         "nodes":        self.oNodesList._iLength,
                         "disks":        self.oDisksList._iLength,
                         "fc-ports":     self.oFCs._iLength,
                         "eth-ports":    self.oNICs._iLength,
                         "dimm-names":   self.oDIMMs._lsListNames,
                         "memory":       self.oDIMMs._iTotalGBs
                         }
        return

    def _lsRunCommand(self, sCmd):
        """runs a command, caches output in Redis"""
        sRedisKey = self.sRedisPrefix + sCmd
        try:
            sLine = self.oRedisDB.get(sRedisKey).decode(REDIS_ENCODING)
            # oLog.debug('_lsRunCommand: Data from Redis')
        except AttributeError:
            # no line in Redis
            try:
                if self.sUser:
                    os.environ['XIV_XCLIUSER'] = self.sUser
                    os.environ['XIV_XCLIPASSWORD'] = self.sPass
                os.environ['HOME'] = FAKE_HOME
                lCommand = [XCLI_PATH, '-y', '-s', '-m', self.sIP] + sCmd.split()
                # oLog.debug('Will run: {}'.format('_'.join(lCommand)))
                sLine = check_output(lCommand, stderr=STDOUT, universal_newlines=True, shell=False)
                # DBG
                self.oRedisDB.set(sRedisKey, sLine.encode(REDIS_ENCODING))
            except CalledProcessError as e:
                sLine = e.output
                oLog.error('Non-zero return code from XCli!')
                oLog.debug('Failed command output from {}: \n'.format(' '.join(lCommand)) + sLine)
        lRet = sLine.split('\n')
        return lRet

    def _ldGetInfoDict(self, sParamName):
        """returns a list of information dictionaries corresponding to parameter. For example,
        if sParamName is 'node-names', returns a result of _ldGetNodeNames() function. So, this
        method is just a dispatcher to simplify calling modules"""
        ldRet = []
        try:
            if sParamName == 'node-names':
                ldRet = self._ldGetNodesAsDicts()
            elif sParamName == 'switch-names':
                ldRet = self._ldGetSwitchesAsDicts()
            elif sParamName == 'disk-names':
                ldRet = self._ldGetDisksAsDicts()
            elif sParamName == 'ups-names':
                ldRet = self._ldGetUPSesAsDicts()
            elif sParamName == 'dimm-names':
                ldRet = self._ldGetDIMMsAsDicts()
            elif sParamName == 'cf-names':
                ldRet = self.oCFList._ldGetData()
            else:
                ldRet = [{}]
                oLog.error("_ldGetInfoDict: incorrect parameter")
        except Exception as e:
            oLog.warning("Exception when filling components' parameters list")
            oLog.warning("Exception: " + str(e))
            ldRet = [{}]
        return ldRet

    def _dGetArrayInfoAsDict(self, ssKeys):
        """
        Array-wide parameters as a dictionary.  Parameter -- a set of
        keys/requests.
        Returns: a dictionary {key:value}
        """
        dRet = {}
        for sKey in ssKeys:
            if sKey in self.dQueries:
                dRet[sKey] = self.dQueries[sKey]()
        # oLog.debug('XIV: _dGetArrayInfoAsDict results are: ' + str(dRet))
        return dRet

    def _ldGetDisksAsDicts(self):
        """ Return disk data as a list of Python dictionaries with fields:
        name, SN, type, model, size, position
        """
        ldRet = []
        try:
            ldRet = self.oDisksList._ldGetData()
        except Exception as e:
            oLog.warning("Exception when filling a disk parameters list")
            oLog.debug("Exception: " + str(e))
        return ldRet

    def _ldGetNodesAsDicts(self):
        """Return nodes' information as a list of dicts"""
        ldRet = []
        try:
            ldRet = self.oNodesList._ldGetData()
        except Exception as e:
            oLog.warning("Exception when filling a nodes parameters list")
            oLog.warning("Exception: " + str(e))
        return ldRet

    def _ldGetSwitchesAsDicts(self):
        """Return switches' information as a list of dicts"""
        ldRet = []
        try:
            ldRet = self.oSwitches._ldGetData()
        except Exception as e:
            oLog.warning("Exception when filling a switch parameters list")
            oLog.warning("Exception: " + str(e))
        return ldRet

    def _ldGetDIMMsAsDicts(self):
        """Return DIMMs' information as a list of dicts"""
        ldRet = []
        try:
            ldRet = self.oDIMMs._ldGetData()
        except Exception as e:
            oLog.warning("Exception when filling DIMMs parameters list")
            oLog.warning("Exception: " + str(e))
        return ldRet

    def _ldGetUPSesAsDicts(self):
        """Return upses' information as a list of dicts"""
        ldRet = []
        try:
            ldRet = self.oUPSs._ldGetData()
        except Exception as e:
            oLog.warning("Exception when filling an UPSes parameters list")
            oLog.warning("Exception: " + str(e))
        return ldRet


class XIV_Componens_Collection:
    def __init__(self, oParent):
        self.lComponentIDs = []
        self.lFailedIDs = []
        self.dComponents = {}
        self.oParent = oParent
        self.lData = []
        self.oCSV = None
        return

    def __repr__(self):
        """for debug printing"""
        lOut = ["\n" + "====== List of components: ======" + '\n']
        lOut.append('List of IDs:')
        lOut.append(', '.join(self.lComponentIDs))
        lOut.append('Failed components:')
        lOut.append(', '.join(self.lFailedIDs))
        lOut.append('Components:')
        lOut.append('\n'.join([str(s) for s in self.dComponents.values()]))
        return ("\n".join(lOut))

    def _AddID(self, sID, bHealthy):
        """stores an ID of a component to internal list based on bHealthy value"""
        # oLog.debug('Adding ID: {}, healthy: {}'.format(sID, bHealthy))
        self.lComponentIDs.append(sID)
        if not bHealthy:
            self.lFailedIDs.append(sID)
        return

    def _lsListNames(self):
        """return a copy of Component IDs list"""
        return list(self.lComponentIDs)

    def _ldGetData(self):
        """return collection's data as a list of dictionaries"""
        ldRet = []
        for oObj in self.dComponents.values():
            ldRet.append(oObj._dGetDataAsDict())
        return ldRet

    def _iLength(self):
        """# of elements in the collection"""
        iFailed = 0
        oLog.debug('_iLength: dComponents is: ' + ', '.join(self.dComponents.keys()))
        oLog.debug('_iLength: lComponentIDs length is {}'.format(len(self.lComponentIDs)))
        try:
            lFails = [o for o in self.dComponents.values() if not o.bHealthy]
            if len(lFails) > 0:
                oLog.debug('There are failed elements: ' + str(lFails))
                iFailed = len(lFails)
        except AttributeError:
            iFailed = 0
        return (len(self.lComponentIDs) - iFailed)

    def _AddComp(self, oComp):
        sID = oComp.id
        self.lComponentIDs.append(sID)
        self.dComponents[sID] = oComp
        return


class IBM_XIV_DisksList(XIV_Componens_Collection):
    sMyListCommand = 'disk_list -t component_id,status,currently_functioning,capacity,size,model,serial'

#     def __init__(self, oParent):
#         super().__init__(oParent)
#         oLog.debug('List of failed disks: ' + ",".join(self.lFailedIDs))
#         return

    def _PostProcessOutput(self):
        # oLog.debug('List of disk IDs: ' + ",".join(self.lComponentIDs))
        # oLog.debug('List of failed disks: ' + ",".join(self.lFailedIDs))
        for dDiskData in self.oCSV:
            # oLog.debug('Processing disk data: ' + str(dDiskData))
            sID = dDiskData['Component ID']
            bHealthy = sID not in self.lFailedIDs
            if not bHealthy:
                oLog.debug('Failed disk with ID: ' + sID)
            oDisk = XIV_Disk(sID, dDiskData, self.oParent, bHealthy)
            try:
                self.oParent.oNodesList._AddDisk(sID, oDisk)
                self.dComponents[sID] = oDisk
            except AttributeError as e:
                oLog.error('DiskList: PostProcess: no parent system')
                raise(e)
        return


class IBM_XIV_NodesList(XIV_Componens_Collection):
    """ CAUTION: you must call this method first, before other *List constructors """
    sMyListCommand = 'module_list -t all'    # there are too many fields to list

    def _PostProcessOutput(self):
        for dNodeData in self.oCSV:
            # oLog.debug("IBM_XIV_NodesList constructor: dNodeData: " + str(dNodeData))
            sID = dNodeData['Component ID']
            bHealthy = sID not in self.lFailedIDs
            self.dComponents[sID] = XIV_Node(sID, dNodeData, bHealthy)
        return

    def _AddDisk(self, sDiskID, oDisk):
        """Add a HDD to corresponding XIV node (module)"""
        iNodeNum = _iGetNodeNum(sDiskID)
        sNodeID = _sMkNodeID(iNodeNum)
        oLog.debug('Adding a disk {} to node {}'.format(sDiskID, sNodeID))
        self.dComponents[sNodeID]._AddDisk(oDisk)
        return

    def _AddCF(self, sCF_ID, oCF):
        """Adds a Compact Flash device to a corresponding node"""
        sNodeID = _sMkNodeID(_iGetNodeNum(sCF_ID))
        oLog.debug('Adding a CF {} to node {}'.format(sCF_ID, sNodeID))
        self.dComponents[sNodeID]._AddCF(oCF)
        return

    def _AddNIC(self, sID, oNic):
        """Adds a NIC to a right node based on Component ID"""
        sNodeID = _sMkNodeID(_iGetNodeNum(sID))
        oLog.debug('Adding a NIC {} to node {}'.format(sID, sNodeID))
        self.dComponents[sNodeID]._AddNIC(oNic)
        return

    def _AddFCPort(self, oFC):
        """Adds a FC port to a module that owns it"""
        sNodeID = oFC._sGetModID()
        oLog.debug('Adding a FC port {} to node {}'.format(oFC.id, sNodeID))
        self.dComponents[sNodeID]._AddFCPort(oFC)
        return

    def _AddDIMM(self, sID, oDimm):
        sNodeID = _sMkNodeID(_iGetNodeNum(sID))
        oLog.debug('Adding a DIMM {} to node {}'.format(sID, sNodeID))
        self.dComponents[sNodeID]._AddDIMM(oDimm)
        return

    def _AddPSU(self, sID, oPSU):
        sNodeID = _sMkNodeID(_iGetNodeNum(sID))
        oLog.debug('Adding a Power supply {} to node {}'.format(sID, sNodeID))
        self.dComponents[sNodeID]._AddPSU(oPSU)
        return


class IBM_XIV_CompactFlashList(XIV_Componens_Collection):
    sMyListCommand = 'cf_list -t component_id,status,currently_functioning,part_number,serial'

    def _PostProcessOutput(self):
        for dCF_Data in self.oCSV:
            sID = dCF_Data['Component ID']
            bHealthy = sID not in self.lFailedIDs
            oCF = XIV_CompFlash(sID, dCF_Data['Part #'], dCF_Data['Serial'], bHealthy)
            self.dComponents[sID] = oCF
            try:
                self.oParent.oNodesList._AddCF(sID, oCF)
            except AttributeError as e:
                oLog.error('No oNodesList attribute in self.oParent')
                raise(e)
        return


class IBM_XIV_SwitchesList(XIV_Componens_Collection):
    sMyListCommand = 'switch_list -t component_id,status,currently_functioning,serial'

    def _PostProcessOutput(self):
        for dSwitchData in self.oCSV:
            sID = dSwitchData['Component ID']
            bHealthy = sID not in self.lFailedIDs
            self.dComponents[sID] = XIV_IB_Switch(sID, dSwitchData['Serial'], bHealthy)
        return


class IBM_XIV_NICsList(XIV_Componens_Collection):
    sMyListCommand = 'nic_list -t component_id,status,currently_functioning,part_number,serial'

    def _PostProcessOutput(self):
        for dNicData in self.oCSV:
            sID = dNicData['Component ID']
            bHealthy = sID not in self.lFailedIDs

            oNic = XIV_NIC(sID, dNicData['Part #'], dNicData['Serial'], bHealthy)
            self.oParent.oNodesList._AddNIC(sID, oNic)
            self.dComponents[sID] = oNic
        return


class IBM_XIV_FCPortsList(XIV_Componens_Collection):
    sMyListCommand = 'fc_port_list -t all'

    def _PostProcessOutput(self):
        for dFCData in self.oCSV:
            sID = dFCData['Component ID']
            bHealthy = sID not in self.lFailedIDs

            oFCPort = XIV_FCPort(sID, dFCData, bHealthy)
            self.dComponents[sID] = oFCPort
            self.oParent.oNodesList._AddFCPort(oFCPort)
        return


class IBM_XIV_DIMMSlist(XIV_Componens_Collection):
    sMyListCommand = 'dimm_list -t component_id,status,currently_functioning,size,part_number,serial'

    def _PostProcessOutput(self):
        for dDIMMdata in self.oCSV:
            sID = dDIMMdata['Component ID']
            bHealthy = sID not in self.lFailedIDs
            oDIMM = XIV_DIMM(sID, dDIMMdata['Size(Mb)'],
                             dDIMMdata['Part #'], dDIMMdata['Serial'],
                             bHealthy)
            self.dComponents[sID] = oDIMM
            self.oParent.oNodesList._AddDIMM(sID, oDIMM)
        return

    def _iTotalGBs(self):
        iTotalMBs = 0
        for oDimm in self.dComponents.values():
            iTotalMBs += oDimm.dQueries['size']()
        return (iTotalMBs / 1024)    # to gigabytes


class IBM_XIV_MaintenanceModulesList(XIV_Componens_Collection):
    sMyListCommand = 'mm_list -t component_id,status,currently_functioning,part_number,serial'

    def _PostProcessOutput(self):
        for dMM_Data in self.oCSV:
            # most times, there will be only one loop
            sID = dMM_Data['Component ID']
            bHealthy =  sID not in self.lFailedIDs
            self.dComponents[sID] = XIV_MaintenanceModule(
                sID, dMM_Data['Part #'], dMM_Data['Serial'], bHealthy)
        return


class IBM_XIV_PwrSuppliesList(XIV_Componens_Collection):
    sMyListCommand = 'psu_list -t component_id,status,currently_functioning'

    def _PostProcessOutput(self):
        for dPSData in self.oCSV:
            sID = dPSData['Component ID']
            bHealthy =  sID not in self.lFailedIDs
            oPSU = XIV_PwrSupply(sID, bHealthy)
            self.oParent.oNodesList._AddPSU(sID, oPSU)
        return


class IBM_XIV_UPS_List(XIV_Componens_Collection):
    sMyListCommand = 'ups_list -t component_id,status,currently_functioning,serial,manufacture_date'

    def _PostProcessOutput(self):
        for d in self.oCSV:
            sID = d['Component ID']
            bHealthy =  sID not in self.lFailedIDs
            self.dComponents[sID] = XIV_UPS(sID, d['Serial'], d['UPS Manufacture Date'], bHealthy)
        return


#
# ============================== Components ==============================
#

class XIV_Component(inv.ComponentClass):
    def __init__(self, sID, sSN="", bHealthy=True):
        self.sID = sID
        self.sSN = sSN
        self.bHealthy = bHealthy
        self.sModel = ''
        self.dQueries = {'model': lambda: self.sModel,
                         'sn': lambda: self.sn}

    def _dGetDataAsDict(self):
        # name, type, model, etc
        dRet = {}
        for name, fun in self.dQueries.items():
            dRet[name] = fun()
        return dRet

    @property
    def model(self):
        if self.bHealthy:
            return self.sModel
        else:
            return "NO COMPONENT!"

    @property
    def sn(self):
        if self.bHealthy:
            return self.sSN
        else:
            return "NO COMPONENT!"

    @property
    def id(self):
        return self.sID


class XIV_Node(XIV_Component):
    """XIV node"""
    def __init__(self, sId, dParams, bHealthy=True):
        """Node constructor. 2nd parameter is a dictionary of data: type, disk bays amount,
        FC ports amount, Ethernet ports amount, serial number, P/N"""
        super().__init__(sId, dParams['Serial'], bHealthy)
        self.sType = dParams['Type']
        self.sModel = dParams['Part Number']
        self.iDiskBaysCount = int(dParams['Data Disks'])
        self.iFCPorts = int(dParams['FC Ports'])
        self.iEthPorts = int(dParams['iSCSI Ports'])
        self.iRAM_GBs = int(dParams['Mem'])
        self.iPwrSupplies = 0
        self.lDisks = IBM_XIV_DisksList(self)
        self.lNICs = IBM_XIV_NICsList(self)
        self.lFCPorts = IBM_XIV_FCPortsList(self)
        self.lDimms = IBM_XIV_DIMMSlist(self)
        self.lPSUs = IBM_XIV_PwrSuppliesList(self)
        self.oCF = None
        self.dQueries = {"name":       lambda: self.sID,
                         "sn":         lambda: self.sn,
                         "healthy":    lambda: self.bHealthy,
                         "disks":      self.lDisks._iLength,
                         "disk-bays":  lambda: self.iDiskBaysCount,
                         "ps-amount":  self.lPSUs._iLength,
                         "fc-ports":   self.lFCPorts._iLength,
                         "model":      lambda: self.model,
                         "type":       lambda: self.sType,
                         "eth-ports":  self.lNICs._iLength,
                         "memory":     lambda: self.iRAM_GBs
                         }
        return

    def __repr__(self):
        sCommon = "Node id: {:12s}, disks:{}, FC:{}, Eth:{}, S/N:{}, P/N:{}".format(
            self.sID, self.iDiskBaysCount, self.iFCPorts, self.iEthPorts, self.sSN, self.sModel)
        sDisks = "Disks list:\n" +  "\n".join([str(d) for d in self.lDisks])
        sDIMMS = "RAM:\n" + "\n".join([str(d) for d in self.lDimms])
        sPSUs = "Pwr Supplies:\n" + "\n".join([str(d) for d in self.lPSUs])
        sNICs = "Network Cards:\n" + "\n".join([str(n) for n in self.lNICs])
        if self.iFCPorts > 0:
            sFCs = "FC Ports:\n" + "\n".join([str(p) for p in self.lFCPorts])
        else:
            sFCs = ''
        sCF = "CF module: " + str(self.oCF)
        return("\n".join([sCommon, sDisks, sDIMMS, sNICs, sFCs, sPSUs, sCF]))

    def _iGetRAM(self):
        iRam = 0
        for oDimm in self.lDimms:
            iRam += oDimm._iGetRAM_MB()
        self.iRAM_MBs = iRam
        return iRam

    def _AddDisk(self, oDisk):
        self.lDisks._AddComp(oDisk)
        oLog.debug("Added disk {1}, new count is: {0}".format(self.lDisks._iLength(), oDisk.id))
        if not oDisk.bHealthy:
            oLog.info('Adding failed disk "{}"'.format(oDisk.id))
        return

    def _AddCF(self, oCompactFlash):
        self.oCF = oCompactFlash
        return

    def _AddNIC(self, oNic):
        self.lNICs._AddComp(oNic)
        return

    def _AddFCPort(self, oPort):
        self.lFCPorts._AddComp(oPort)
        return

    def _AddDIMM(self, oDimm):
        self.lDimms._AddComp(oDimm)
        return

    def _AddPSU(self, oPSU):
        self.iPwrSupplies += 1
        self.lPSUs._AddComp(oPSU)
        return

    def _lsGetDiskNames(self):
        """return a list of this node disks"""
        return self.lDisks._lsListNames()


class XIV_Disk(XIV_Component):
    """Physical disk in XIV"""
    def __init__(self, sID, dParams, oNode, bHealthy=True):
        # self.sSN = dParams["Serial"]
        super().__init__(sID, dParams["Serial"], bHealthy)
        self.sID = sID
        self.iSizeMB = int(dParams['Size'])
        self.sSizeH = dParams['Capacity (GB)']
        self.sModel = dParams['Model']
        self.dQueries = {"name":  lambda: self.sID,
                         # "id":    lambda: self.sID,
                         "healthy": lambda: self.bHealthy,
                         "model": lambda: self.model,
                         "position": self._sGetPosition,
                         "size":  self._iGetSize,
                         "sn":    lambda: self.sn}
        # oLog.debug('Disk ID: {}, sizeH: {}, sizeKB: {}'.format(self.sID, self.sSizeH, self.iSizeMB))
        return

    def _iGetSize(self):
        if self.bHealthy:
            return (self.iSizeMB / 1024)
        else:
            return 0

    @property
    def size(self):
        return self._iGetSize()

    def _sGetPosition(self):
        """return disk position based on ID"""
        if self.bHealthy:
            lFields = self.sID.split(':')
            return("Node {0}, bay {1}".format(lFields[2], lFields[3]))
        else:
            return "NO COMPONENT!"

    def __repr__(self):
        if self.bHealthy:
            h = ''
        else:
            h = '!FAILED '
        return "{3} Drive: ID: {0}, size:{1}, mod:{2}".format(self.sID, self.size, self.model, h)


class XIV_CompFlash(XIV_Component):
    """XIV CF device"""
    def __init__(self, sID, sPN, sSN, bHealthy=True):
        super().__init__(sID, sSN, bHealthy)
        self.sID = sID
        # Node number is 3rd colon-separated field in the component ID
        self.sModel = sPN
        self.sSN = sSN
        self.dQueries = {"name":     lambda: self.sID,
                         "sn":       lambda: self.sSN,
                         "model":    lambda: self.sModel,
                         "position": self._sGetPos}
        return

    def _sGetPos(self):
        """return module number based on ID"""
        if self.bHealthy:
            lFields = self.sID.split(':')
            return("Node {0}".format(lFields[2]))
        else:
            return "NO COMPONENT!"

    def __repr__(self):
        return "Compact Flash device, ID: {0:12s}, P/N:{1}, S/N:{2}".format(self.sID, self.sModel, self.sSN)


class XIV_NIC(XIV_Component):
    """XIV Network Interface Card (Ethernet)"""
    def __init__(self, sID, sPN, sSN, bHealthy=True):
        super().__init__(sID, sSN, bHealthy)
        # self.sID = sID
        self.sPN = sPN
        # self.sSN = sSN
        return

    def __repr__(self):
        """for debugging"""
        return("NIC: ID {}, PN: {}, SN: {}".format(self.sID, self.sPN, self.sSN))


class XIV_MaintenanceModule(XIV_Component):
    """Maintenance module. I can't receive any information from XIV abt this module"""
    def __init__(self, sID, sPN, sSN, bHealthy=True):
        super().__init__(sID, sSN, bHealthy)
        # self.sID = sID
        self.sPN = sPN
        # self.sSN = sSN
        return


class XIV_DIMM(XIV_Component):
    """DIMM module"""
    def __init__(self, sID, sSizeMB, sPN, sSN, bHealthy=True):
        super().__init__(sID, sSN, bHealthy)
        # self.sID = sID
        self.iSizeMB = int(sSizeMB)
        # self.sSN = sSN
        self.sPN = sPN
        self.dQueries = {"name":     lambda: self.sID,
                         "sn":       lambda: self.sSN,
                         "size":     lambda: self.iSizeMB,
                         "model":    lambda: self.sPN,
                         "position": self._sGetPos}
        return

    def __repr__(self):
        return("RAM Module {3}: size:{0}, P/N:{1}, S/N:{2}".format(
            self.iSizeMB, self.sPN, self.sSN, self.sID))

    def _sGetPos(self):
        """return disk position based on ID"""
        lFields = self.sID.split(':')
        return("Node {0}, Slot {1}".format(lFields[2], lFields[3]))

    def _iGetRAM_MB(self):
        return self.iSizeMB


class XIV_PwrSupply(XIV_Component):
    def __init__(self, sID, bHealthy=True):
        super().__init__(sID, '', bHealthy)    # Empty S/N
        self.sID = sID
        return

    def __repr__(self):
        return "Power supply, ID: {0}, is_healthy: {1}".format(self.sID, str(self.bHealthy))


class XIV_UPS(XIV_Component):
    def __init__(self, sID, sSN, sMFDate, bHealthy=True):
        super().__init__(sID, sSN, bHealthy)
        self.sMfgDate = sMFDate
        self.dQueries = {"name":    lambda: self.sID,
                         "sn":      lambda: self.sSN,
                         "mfgdate": lambda: self.sMfgDate}
        return


class XIV_FCPort(XIV_Component):
    def __init__(self, sID, dDataDict, bHealthy=True):
        super().__init__(sID, dDataDict['Original Serial'], bHealthy)
        self.sModel = dDataDict['Model']
        self.sModID = dDataDict['Module']
        self.sWWN = dDataDict['WWPN']
        self.sPortNum = dDataDict['Port Number']
        # self.sSN = dDataDict['Original Serial']
        return

    def _sGetModID(self):
        return self.sModID

    def __repr__(self):
        return ("FC port WWN:{0} on node {1}, model {2}, SN {3}".format(
            self.sWWN, self.sModID, self.sModel, self.sSN))


class XIV_IB_Switch(XIV_Component):
    def __init__(self, sID, sSerial, bHealthy=True):
        super().__init__(sID, sSerial, bHealthy)
        self.dQueries = {"name":       lambda: self.sID,
                         "sn":         lambda: self.sSN}
        pass

    def __repr__(self):
        return "XIV IB Switch: ID:{0:-12s}, S/N:{1}".format(self.sID, self.sSN)


# ============================================
# Testing section
# --------------------------------------------
if __name__ == '__main__':
    # access to test system
    from access import IBM_XIV_2 as tsys

    # set up all logging to console
    oLog.setLevel(logging.DEBUG)
    oConHdr = logging.StreamHandler()
    oConHdr.setLevel(logging.DEBUG)
    oLog.addHandler(oConHdr)

    # print(str(oXiv.oNodesList))
    import redis
    oRedis = redis.StrictRedis()
    # oXiv = IBM_XIV_Storage(tsys.sIP, tsys.sUser, tsys.sPass, oRedis, tsys.sName)
    oXiv = IBM_XIV_Storage(tsys.sIP, '', '', oRedis, tsys.sName)
    # oLog.debug(oXiv._ldGetDisksAsDicts())
    print(oXiv.dQueries["node-names"]())
    # print(oXiv.dQueries["switch-names"]())
    # print(oXiv._ldGetSwitchesAsDicts())
    # print(oXiv.dQueries["ups-names"]())
    # print(oXiv.oDisksList)
    print(oXiv._dGetArrayInfoAsDict(oXiv.dQueries))
#     print("Nodes:", oXiv.dQueries["nodes"]())
#     print("Disks:", oXiv.dQueries["disks"]())
#     print("FC Ports", oXiv.dQueries["fc-ports"]())
#     print("Ethernet Ports:", oXiv.dQueries["eth-ports"]())
#     print("DIMM Names:", oXiv.dQueries["dimm-names"]())
#     print("Memory GBs:", oXiv.dQueries["memory"]())
    pass
