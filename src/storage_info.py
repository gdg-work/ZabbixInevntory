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

def _sProcessArgs(oStorageObject, oArgs):
    """Apply query to a storage device"""
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
        lCtrls = [ '{#CTRLNAME}:' + n for n in lCtrls ]
        oLog.debug("_sProcessArgs: list of controller-name pairs: %s" % str(lCtrls))
        dRetDict = { "data":lCtrls }
        return json.dumps(dRetDict)
    elif oArgs.query == "ctrl-sns":
        lCtrls = oStorageObject.getControllersSN()
        oLog.debug("_sProcessArgs: list of controller serials: %s" % str(lCtrls))
        lCtrls = [ '{#CTRLSN}:' + n for n in lCtrls ]
        dRetDict = { "data":lCtrls }
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
            choices=["sn", "wwn", "type", "model", "ctrls", "ctrl-names", "ctrl-sns"], default="sn")
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
