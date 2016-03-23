#!/usr/bin/env python3

# A template for HP EVA Zabbix script
#

import argparse as ap
import sys

def _sGetSN(oArgs): return ("0000-1111-2222-3333")

def _sGetType(oArgs): return ("HP EVA x400")

def _sGetModel(oArgs): return ("HSV 310")

def _sGetControllers(oArgs): return("2")

def _sProcessArgs(oArgs):
    if oArgs.query == "sn":
        return (_sGetSN(oArgs))
    elif oArgs.query == "type":
        return (_sGetType(oArgs))
    elif oArgs.query == "model":
        return (_sGetModel(oArgs))
    elif oArgs.query == "ctrls":
        return (_sGetControllers(oArgs))

if __name__ == '__main__':
    oParser = ap.ArgumentParser(description="HP EVA-Zabbix interface template")
    oParser.add_argument('-q', '--query', help="Parameter to request",
                        choices=["sn", "type", "model", "ctrls"], default="sn")
    oArgs = oParser.parse_args()

    sResult = _sProcessArgs(oArgs)
    print (sResult)
    sys.exit()
