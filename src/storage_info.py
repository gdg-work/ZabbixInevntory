#!/usr/bin/env python3

# main module of the program

import argparse as ap
import inventoryObjects
import logging
import json
# array-dependent modules
import hpeva_sssu as eva
from sys import exit
from inventoryLogger import dLoggingConfig

logging.config.dictConfig(dLoggingConfig)
oLog = logging.getLogger(__name__)

def _sGetComponentInfo(oStorageObject, sComponentName, oArgs):
    sRet = "Not Implemented"
    oComponent = oStorageObject.getComponent(sComponentName)
    if oComponent:
        if oArgs.query == "sn":
            sRet = oComponent.getSN()
        elif oArgs.query == "type":
            sRet = oComponent.getType()
        elif oArgs.query == "model":
            sRet = oComponent.getModel()
        else:
            sRet = ("Not implemented yet!")
    else:
        sRet = "Error when querying a component"
    return (sRet)

def _sProcessArgs(oStorageObject, oArgs):
    """Apply query to a storage device """
    if oArgs.element:
        return _sGetComponentInfo(oStorageObject, oArgs.element, oArgs)
    else:
        if oArgs.query == "sn":
            return (oStorageObject.getSN())
        if oArgs.query == "wwn":
            return (oStorageObject.getWWN())
        elif oArgs.query == "type":
            return (oStorageObject.getType())
        elif oArgs.query == "model":
            return (oStorageObject.getModel())
        elif oArgs.query == "ctrls":
            return (oStorageObject.getControllersAmount())
        elif oArgs.query == "ctrl-names":
            lCtrls = oStorageObject.getControllerNames()
            oLog.debug("_sProcessArgs: list of controllers: %s" % str(lCtrls))
            lCtrls = [ '{#ID}:' + n for n in lCtrls ]
            dRetDict = { "data":lCtrls }
            return json.dumps(dRetDict)
        elif oArgs.query == "shelf-names":
            lShelves = oStorageObject.getDiskShelfNames()
            oLog.debug("_sProcessArgs: list of disk shelves: %s" % str(lShelves))
            lShelves = [ '{#ID}:' + n for n in lShelves ]
            dRetDict = { "data":lShelves }
            return json.dumps(dRetDict)

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
            choices=["sn", "wwn", "type", "model", "ctrls", "ctrl-names", "shelf-names"], default="sn")
    oParser.add_argument('-e', '--element', 
            help="Component of an array the query is making to, such as controller or disk shelf",
            type=str, required=False)
    oParser.add_argument('-c', '--control_ip', help="Array control IP or FQDN", type=str, required=True)
    oParser.add_argument('-u', '--user', help="Array/control host login", type=str, required=True)
    oParser.add_argument('-p', '--password', help="password", type=str, required=False)
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
