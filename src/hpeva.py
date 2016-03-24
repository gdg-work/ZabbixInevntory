#!/usr/bin/env python3

# A template for HP EVA Zabbix script
#

import argparse as ap
import sssu
import json
import sys

# CONSTANTS
SSSU_PATH='/opt/HPEVA_SSSU/sssu'

def _sGetModel(oArgs): return ("HSV 310")

def _sGetControllers(oEvaConn): 
    return(json.dumps(
        { "data": { "{#TYPE}": "hsv310", "{#SERIAL}": [ "12341234", "234344534"] } }
    ))


def _sProcessArgs(oEvaConn):
    if oArgs.query == "sn":
        return (_sGetEvaWWN(oEvaConn))
    elif oArgs.query == "type":
        return (_sGetEvaType(oEvaConn))
    elif oArgs.query == "model":
        return (_sGetEvaType(oEvaConn))
    elif oArgs.query == "ctrls":
        return (_sGetControllers(oEvaConn))

def _LogDebug(s: str):
    """затычка - здесь будет выдача отладочной информации"""
    # print("*DBG* %s" % s)
    return

def _LogError(s: str):
    """затычка - здесь будет выдача отладочной информации"""
    print("*ERR* %s" % s)
    return

def _oEvaConnect(oArgs):
    ip = oArgs.commandview
    user = oArgs.user
    password = oArgs.password
    sysname = oArgs.system
    return sssu.SSSU_Iface(SSSU_PATH, ip, user, password, "/tmp", sysname, _LogDebug, _LogError)

def _sGetEvaWWN(oEVA):
    sResult = oEVA._sRunCommand("ls system %s | grep objectwwn" % oEVA._sGetSysName(),"\n")
    lsLines = [ l for l in sResult.split("\n") if ( l.find('objectwwn') >= 0 and l.find('....') > 0) ]
    if len(lsLines) == 1:
        sWwn = lsLines[0].split(':')[1].strip()
    else:
        self._Dbg("_sGetEvaWWN: Strange -- more than one (%d) WWNs of system" % len(lLines))
    return(sWwn)

def _sGetEvaType(oEVA):
    sResult = oEVA._sRunCommand("ls system %s | grep systemtype" % oEVA._sGetSysName(),"\n")
    lsLines = [ l for l in sResult.split("\n") if ( l.find('systemtype') >= 0 and l.find('....') > 0) ]
    if len(lsLines) == 1:
        sType = lsLines[0].split(':')[1].strip()
    else:
        self._Dbg("_sGetEvaType: Strange -- more than one (%d) types of system" % len(lLines))
    return(sType)

if __name__ == '__main__':
    oParser = ap.ArgumentParser(description="HP EVA-Zabbix interface template")
    oParser.add_argument('-q', '--query', help="Parameter to request",
                        choices=["sn", "type", "model", "ctrls"], default="sn")
    oParser.add_argument('-c', '--commandview', help="Command View host", type=str, required=True)
    oParser.add_argument('-u', '--user', help="Command View login", type=str, required=True)
    oParser.add_argument('-p', '--password', help="Command View password", type=str, required=True)
    oParser.add_argument('-s', '--system', help="HP EVA name in CV", type=str, required=True)
    oArgs = oParser.parse_args()
    oEvaConn = _oEvaConnect(oArgs)

    sResult = _sProcessArgs(oEvaConn)

    # sWWN = _sGetEvaWWN(oEvaConn)
    # print (sWWN)
    oEvaConn._Close()

    print (sResult)
    sys.exit()
