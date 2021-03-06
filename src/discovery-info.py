#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Программа для получения списка объектов из базы Redis. Точнее -- в базу сначала отправляется
информация для доступа к массиву и к серверу Zabbix, затем запрашиваются списки контроллеров,
дисковых полок и дисков (в зависимости от того, что спросил пользователь).

Если в базе ещё нет информации -- выдаются пустые списки.

Интерфейс пользователя - через CLI.
Интерфейс Zabbix - через потоки (stdin/stdout)
"""
import argparse as ap
import logging
import json
# from pathlib import Path
from inventoryLogger import dLoggingConfig
# from redis import StrictRedis, RedisError
from redis_utils import _oConnect2Redis
# from zabbixInterface import _sListOfStringsToJSON
from local import CACHE_TIME
# import sys         #  <--- for debugging

# ============================== CONSTANTS ==============================
STORAGE_OPS = set(["ctrl-names",    # list of controllers' names
                   "shelf-names",   # list of disk enclosures' names
                   "disk-names",    # list of disks' names (ID's)
                   "node-names",    # list of nodes (Scale-Out arrays)
                   "ups-names",     # list of UPSes (XIV)
                   "cf-names",      # list of Compact Flash modules (XIV)
                   "dimm-names",    # list of RAM modules (XIV)
                   "switch-names"   # list of IB switches (XIV)
                   ])

ARRAYS_SUPPORTED = set(["EVA",
                        "3Par",
                        "FlashSys",
                        "XIV",
                        "IBM_DS"
                        ])
REDIS_PREFIX = "ArraysDiscovery."
D_KEYS = {'ctrl-names':   'LIST_OF_CONTROLLER_NAMES',
          'shelf-names':  'LIST OF DISK ENCLOSURE NAMES',
          'disk-names':   'LIST OF DISK NAMES',
          "node-names":   'LIST OF NODE NAMES',
          "ups-names":    'LIST OF UPSes',
          "dimm-names":   'LIST OF RAM MODULES',
          "cf-names":     'LIST OF COMPACT FLASH MODULES',
          "switch-names": 'LIST OF SWITCHES'}
REDIS_ENCODING = 'utf-8'


def _SendArrayInfo(oRedis, oArgs):
    ACCESS_PFX = REDIS_PREFIX + "ArrayAccess"
    ZABBIX_PFX = REDIS_PREFIX + "ZabbixAccess"
    sArrayName = oArgs.system
    dArrayAccess = {'type': oArgs.type, 'ip': oArgs.control_ip}
    if oArgs.type == "EVA":
        dArrayAccess['access'] = {'user':   oArgs.user,
                                  'pass':   oArgs.password,
                                  'system': oArgs.system}
    elif oArgs.type == "3Par":
        dArrayAccess['access'] = {'user':   oArgs.user,
                                  'pass':   oArgs.password,
                                  'system': oArgs.system}
    elif oArgs.type == "IBM_DS":
        dArrayAccess['access'] = {}
    elif oArgs.type == "XIV":
        dArrayAccess['access'] = {'user':   oArgs.user,
                                  'pass':   oArgs.password,
                                  'system': oArgs.system}
    elif oArgs.type == "FlashSys":
        dArrayAccess['access'] = {'user':   oArgs.user,
                                  'pass':   oArgs.password,
                                  'system': oArgs.system}
    else:
        pass

    dZabbixAccess = {'zabbix_user': oArgs.zabbixuser,
                     'zabbix_passwd': oArgs.zabbixpassword,
                     'zabbix_IP': oArgs.zabbixip,
                     'zabbix_port': oArgs.zabbixport}
    oRedis.set(ZABBIX_PFX, json.dumps(dZabbixAccess), oArgs.redis_ttl)
    oRedis.hset(ACCESS_PFX, sArrayName, json.dumps(dArrayAccess))
    oRedis.expire(ACCESS_PFX, oArgs.redis_ttl)
    return


def _sGetArrayData(oRedis, oArgs):
    ARRINFO_HASHNAME = REDIS_PREFIX + "ArrayKeys"
    sRet = ''
    try:
        sArrayKey = oRedis.hget(ARRINFO_HASHNAME, oArgs.system)
        sJson = oRedis.hget(sArrayKey, D_KEYS[oArgs.query]).decode(REDIS_ENCODING)
        # print("*DBG* JSON from Redis: {}".format(sJson))
        if sJson:
            if sJson == "None":
                pass
            else:
                # sJson = sJson.decode(REDIS_ENCODING)
                oLog.debug('JSon from Redis: {}'.format(sJson))
                sRet = sJson
    except AttributeError:
        # no data in Redis, nothing to decode
        pass
    except TypeError:
        # no data in Redis
        pass
    except Exception as e:
        oLog.error(str(e))
        pass
    return sRet


def _sProcessArgs(oArgs):
    """Process the CLI arguments and return results as a JSON for Zabbix"""
    sRet = "Not implemented yet"
    oRedis = _oConnect2Redis(oArgs.redis)
    _SendArrayInfo(oRedis, oArgs)
    sRet = _sGetArrayData(oRedis, oArgs)
    return sRet


def _oGetCLIParser():
    """parse CLI arguments, returns argparse.ArgumentParser object"""
    oParser = ap.ArgumentParser(description="Storage Array-Zabbix interface program")
    oParser.add_argument('-t', '--type', help="Storage device type", required=True,
                         choices=ARRAYS_SUPPORTED)
    oParser.add_argument('-q', '--query', choices=STORAGE_OPS, default="ctrl-names")
    oParser.add_argument('-c', '--control-ip', help="Array control IP or FQDN", type=str, required=True)
    oParser.add_argument('-u', '--user', help="Array/control host login", type=str, required=True)
    oParser.add_argument('-p', '--password', help="password", type=str, required=False)
    oParser.add_argument('--dummy', help="Dummy unique key (not used)", type=str, required=False)
    oParser.add_argument('-k', '--key', help="SSH private key to authenticate", type=str, required=False)
    oParser.add_argument('-s', '--system', help="Array name", type=str, required=False)
    oParser.add_argument('-r', '--redis', help="Redis database host:port or socket, default=localhost:6379",
                         default='localhost:6379', type=str, required=False)
    oParser.add_argument('--redis-ttl', help="TTL of Redis-cached data", type=int,
                         default=CACHE_TIME, required=False)
    oParser.add_argument('-z', '--zabbixip', help="IP of Zabbix server", type=str,
                         default='127.0.0.1', required=False)
    oParser.add_argument('-S', '--zabbixport', help="Port for sending data to Zabbix server",
                         type=int, default=10051, required=False)
    oParser.add_argument('-U', '--zabbixuser', help="Zabbix server user name",
                         default='Admin', required=False)
    oParser.add_argument('-P', '--zabbixpassword', help="Zabbix server password",
                         default='zabbix', required=False)
    return (oParser.parse_args())

if __name__ == '__main__':
    logging.config.dictConfig(dLoggingConfig)
    oLog = logging.getLogger('Discovery')
    oLog.info('<<< Starting Discovery-info program')
    # oLog.debug(" ".join(sys.argv))
    oParser = _oGetCLIParser()
    sRet = "Not implemented yet"
    iRetCode = -1
    try:
        sRet = _sProcessArgs(oParser)
        print(sRet)
        iRetCode = 0
    except Exception as e:
        oLog.error("Exception at top-level {}".format(str(e)))
        iRetCode = 1
    oLog.info('>>> Discovery-info program finished')
    exit(iRetCode)

# vim: expandtab : softtabstop=4 : tabstop=4 : shiftwidth=4 : autoindent
