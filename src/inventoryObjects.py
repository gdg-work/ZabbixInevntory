#!/usr/bin/env python3

import itertools

#
# NetObject: a base class of Zabbix-controlled objects
#

class NetObjectClass:
    def __init__(self, sIP :str, sType :str):
        self.sIP = sIP
        self.sType = sType

    def getType(self) ->str: return self.sType

    def getIP(self) ->str: return self.sIP

#
# A base class for storage systems
#

class StorageClass(NetObjectClass):
    def __init__(self, sIP: str, sType: str):
        super().__init__(sIP, sType)

    def getSN(self):
        return "Not implemented at super-class"

class ClassicArrayClass(StorageClass):
    """A classic disk array with 1..8 controllers, some amount of disk shelves and disks"""
    def __init__(self, sIP: str, sType: str):
        super().__init__(sIP, sType)
        self.lControllers = []    # list of ControllerClass
        self.lShelves = []  # list of 'DiskShelfClass'
        self.lDisks = []    # list of DiskClass
        self.dQueries = {          # possible queries
                "ctrls": self.getControllersAmount,
                "disks": self.getDisksAmount}

    def getControllersAmount(self):
        return len(self.lControllers)

    def getShelvesAmount(self):
        return len(self.lShelves)

    def getDisksAmount(self):
        return len(self.lDisks)

    def getControllersSN(self):
        """returns serial numbers of controllers"""
        lRet = []
        for oCtr in self.lControllers:
            lRet.append(oCtrl.getSN())
        return lRet

    def getShelvesSN(self):
        """returns a list of disk shelves' serial numbers"""
        lRet = [ s.getSN() for s in self.lShelves]
        return lRet

    def getDisksSN(self):
        """returns a list of disk drives' serial numbers"""
        lRet = [ s.getSN() for s in self.lDisks]
        return lRet

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

class ScaleOutStorageClass(StorageClass):
    def __init__(self, sIP: str, sType: str):
        super().__init__(sIP, sType)
        self.sType=""
        self.lNodes=[]

    def getType(self) ->str:
        return self.sType

    def getNodesAmount(self) ->int:
        return len(self.lNodes)

    def getDisksAmount(self) ->int:
        return sum([n.getDisksAmount() for n in self.lNodes])

    def getPortsAmount(self) ->int:
        return sum([n.getPortsAmount() for n in self.lNodes])

    def getDisksSN(self) -> list:
        lSNs = list(itertools.chain.from_iterable(    # flatten a list
            [n.getDisksSN for n in self.lNodes]
            ))
        return lSNs

#
# --- Array components ---
#

class ComponentClass:
    """ Common properties of storage system's components """
    def __init__(self, sID :str, sSN=""):
        self.sID = sID
        self.sSN = sSN
        self.dQueries = {"name": self.getID,
                         "sn" : self.getSN }

    def getID(self) -> str:
        return self.sID

    def getSN(self) -> str:
        return self.sSN

    def _dGetDataAsDict(self):
        # name, type, model, SN, position, RPM, size
        dRet = {}
        for name, fun in self.dQueries.items():
            dRet[name] = fun()
        return dRet


class ControllerClass(ComponentClass):
    """Classic array's disk controller"""
    def __init__(self, sID :str, sSN=""):
        super().__init__(sID, sSN)
        self.sType = ""
        self.sModel = ""
        self.lUpstreamPorts = ""
        self.sIP = ""

    def getType(self):
        return (self.sType)

    def getModel(self):
        return self.sModel

    def getPortsAmount(self):
        return len(self.lUpstreamPorts)

    def getPortIDs(self):
        lRet = [p.getID() for p in self.lUpstreamPorts]
        return lRet

    def getIP(self):
        return self.sIP


class DiskShelfClass(ComponentClass):
    def __init__(self, sID :str, sSN=""):
        super().__init__(sID, sSN)
        self.sType=""
        self.sModel=""
        self.lPwrSupplies=[]
        self.lDisks=[]

    def getType(self):
        return (self.sType)

    def getModel(self):
        return self.sModel

    def getPwrSupplyAmount(self):
        return len(self.lPwrSupplies)

    def getDisksAmount(self):
        return len(self.lDisks)

    def getDisksVolume(self):
        return sum([d.getSize() for d in self.lDisks])

class DASD_Class(ComponentClass):
    def __init__(self, sID :str, sSN=""):
        super().__init__(sID, sSN)
        self.sType=""
        self.sModel=""
        self.rSize=""   # disk size in GB's
        self.sPosition=""

    def getType(self) ->str:
        return self.sType

    def getModel(self) ->str:
        return self.sModel

    def getSize(self) ->int: 
        return self.iSize

    def getPosition(self) ->str:
        return self.sPosition

class PortClass(ComponentClass):
    def __init__(self, sID :str, sSN=""):
        super().__init__(sID, sSN)
        self.sType=""
        self.sSpeed=""

    def getType(self) ->str: 
        return self.sType

    def getSpeed(self) ->str:
        return self.sSpeed

class NodeClass(ComponentClass):
    def __init__(self, sID :str, sSN=""):
        super().__init__(sID, sSN)
        self.sType=""
        self.sModel=""
        self.lPwrSupplies = []
        self.lPorts = []
        self.lDisks = []

