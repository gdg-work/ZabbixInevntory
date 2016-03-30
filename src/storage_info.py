#!/usr/bin/env python3
""" main module of the storage system Zabbix inventory program """

import argparse as ap
import logging
import json
# array-dependent modules
import hpeva_sssu as eva
from inventoryLogger import dLoggingConfig

logging.config.dictConfig(dLoggingConfig)
oLog = logging.getLogger(__name__)

def _sListOfStringsToJSON(lsStrings):
    ID = '{#ID}'
    lRetList = [ {ID:n} for n in lsStrings ]
    dRetDict = { "data":lRetList }
    return json.dumps(dRetDict)
    
def _sGetComponentInfo(oStorageObject, sComponentName, oArgs):
    sRet = "Not Implemented"
    oComponent = oStorageObject.getComponent(sComponentName)
    if oComponent:
        try:
            if oArgs.query == "sn":
                sRet = oComponent.getSN()
            elif oArgs.query == "type":
                sRet = oComponent.getType()
            elif oArgs.query == "model":
                sRet = oComponent.getModel()
            elif oArgs.query == "disk-names":
                sRet = oComponent.getDiskNames()
            elif oArgs.query == "ports":
                sRet = str(len(oComponent.getPortNames()))
            elif oArgs.query == "port-names":
                sRet = _sListOfStringsToJSON(oComponent.getPortNames())
            elif oArgs.query == "ps-amount":
                sRet = oComponent.getPwrSupplyAmount()
            else:
                sRet = ("Not implemented yet!")
        except AttributeError as e:
            oLog.info(e.args[0])
            oLog.info("Error when querying '{0}' of object '{1}'".format(oArgs.query, sComponentName))
            sRet = "N/A"
    else:
        sRet = "Error when querying a component"
    return (sRet)

def _sProcessArgs(oStorageObject, oArgs):
    """Apply query to a storage device """
    sRet = "N/A"
    oLog.debug("Request: {0}".format(oArgs.query))
    if oArgs.element:
        sRet = _sGetComponentInfo(oStorageObject, oArgs.element, oArgs)
    else:
        try:
            if oArgs.query == "sn":
                sRet = oStorageObject.getSN()
            elif oArgs.query == "wwn":
                sRet = oStorageObject.getWWN()
            elif oArgs.query == "type":
                sRet = oStorageObject.getType()
            elif oArgs.query == "model":
                sRet = oStorageObject.getModel()
            elif oArgs.query == "ctrls":
                sRet = oStorageObject.getControllersAmount()
            elif oArgs.query == "ctrl-names":
                lCtrls = oStorageObject.getControllerNames()
                oLog.debug("_sProcessArgs: list of controllers: %s" % str(lCtrls))
                sRet = _sListOfStringsToJSON(lCtrls)
            elif oArgs.query == "shelf-names":
                lShelves = oStorageObject.getDiskShelfNames()
                oLog.debug("_sProcessArgs: list of disk shelves: %s" % str(lShelves))
                sRet = _sListOfStringsToJSON(lShelves)
            elif oArgs.query == 'disk-names':
                lsDisks = oStorageObject.getDiskNames()
                sRet = _sListOfStringsToJSON(lsDisks)
            else:
                oLog.error("Invalid request")
        except AttributeError as e:
            oLog.error(e.args[0])
            oLog.info("Error when querying {0} of storage device".format(oArgs.query))
    return sRet

def _oEvaConnect(oArgs):
    ip = oArgs.control_ip
    user = oArgs.user
    password = oArgs.password
    sysname = oArgs.system
    return eva.HP_EVA_Class(ip, user, password, sysname)

def _oGetCLIParser():
    oParser = ap.ArgumentParser(description="Storage Array-Zabbix interface program")
    oParser.add_argument('-t', '--type', help="Storage device type", required=True,
            choices=["EVA", "Storwize", "3Par", "XIV"])
    oParser.add_argument('-q', '--query', help="Parameter to request",
            choices=["sn", "wwn", "type", "model", "ctrls", "ports", "ctrl-names", 
                    "shelf-names", "disk-names", "ps-amount", "port-names"], default="sn")
    oParser.add_argument('-e', '--element', 
            help="Component of an array the query is making to, such as controller or disk shelf",
            type=str, required=False)
    oParser.add_argument('-c', '--control_ip', help="Array control IP or FQDN", type=str, required=True)
    oParser.add_argument('-u', '--user', help="Array/control host login", type=str, required=True)
    oParser.add_argument('-p', '--password', help="password", type=str, required=False)
    oParser.add_argument('--dummy', help="Dummy unique key (not used)", type=str, required=False)
    oParser.add_argument('-k', '--key', help="SSH private key to authenticate", type=str, required=False)
    oParser.add_argument('-s', '--system', help="HP EVA name in CV (EVA only)", type=str, required=False)
    return (oParser.parse_args())

if __name__ == '__main__':
    # set up logger
    oArgs = _oGetCLIParser()
    if oArgs.type == "EVA":
        oArrayConn = _oEvaConnect(oArgs)
    else:
        oLog.error("Not implemented yet!")
        exit(3)

    sResult = _sProcessArgs(oArrayConn, oArgs)
    oArrayConn._Close()
    print (sResult)
    exit(0)

# vim: expandtab : softtabstop=4 : tabstop=4 : shiftwidth=4
