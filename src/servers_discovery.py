#!/usr/bin/env python3
# -*- coding: utf-8 -*-

""" Prints to STDOUT list of servers in JSON format used by Zabbix """
import logging
import argparse as ap
from inventoryLogger import dLoggingConfig
# from zabbixInterface import _sListOfStringsToJSON
from redis import RedisError
from redis_utils import _oConnect2Redis
import json
import traceback
from local import REDIS_ENCODING

# Constants
SERVERS_SUPPORTED = ['aix_hmc', 'esxi_amm', 'esxi']
# OPERATIONS_SUPPORTED = ['server-name']
REDIS_PREFIX = "ServersDiscovery."


class IncorrectServerType(Exception):
    pass


def _sGetServerData(oRedis, oArgs):
    SERVER_HASHNAME = REDIS_PREFIX + "ServerKeys"
    sRet = ''
    try:
        sServerKey = oRedis.hget(SERVER_HASHNAME, oArgs.system)
        sJson = oRedis.hget(sServerKey, oArgs.query).decode(REDIS_ENCODING)
        # print("*DBG* JSON from Redis: {}".format(sJson))
        if sJson == "None":
            pass
        elif sJson is None:
            pass        # <-- Will we be here anyhow?
        else:
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


def _PushConnectionInfo(oParser, oRedis):
    ACCESS_PFX = REDIS_PREFIX + "ServersAccess"
    ZABBIX_PFX = REDIS_PREFIX + "ZabbixAccess"
    dZabbixAccess = {'zabbix_user': oParser.zabbixuser,
                     'zabbix_passwd': oParser.zabbixpassword,
                     'zabbix_IP': oParser.zabbixip,
                     'zabbix_port': oParser.zabbixport}
    if oParser.type == "power_aix":
        dConnectionInfo = {'type': oParser.server_type,
                           'sp-type': 'HMC',
                           'user': oParser.user,
                           'password': oParser.password,
                           'sp-user': oParser.sp_user,
                           'sp-pass': oParser.sp_password,
                           'srv-ip': oParser.server_ip,
                           'sp-ip': oParser.sp_ip}
    elif oParser.type == "esxi_amm":
        assert oParser.vcenter is not None
        dConnectionInfo = {'type': oParser.server_type,
                           'sp-type': 'AMM',
                           'user': oParser.user,
                           'password': oParser.password,
                           'sp-user': oParser.sp_user,
                           'sp-pass': oParser.sp_password,
                           'srv-name': oParser.server_name,
                           'sp-ip': oParser.sp_ip}
    else:
        oLog.error('Unsupported type of server')
        raise(IncorrectServerType)
    try:
        oRedis.set(ZABBIX_PFX, json.dumps(dZabbixAccess), oParser.redis_ttl)
        oRedis.hset(ACCESS_PFX, oParser.name, json.dumps(dConnectionInfo))
        oRedis.expire(ACCESS_PFX, oParser.redis_ttl)
    except RedisError:
        oLog.error('Cannot connect to Redis and set information')
        raise RedisError
    return


def _PushConnectionInfo2(dConnInfo, oParser, oRedis):
    ACCESS_PFX = REDIS_PREFIX + "ServersAccess"
    ZABBIX_PFX = REDIS_PREFIX + "ZabbixAccess"
    dZabbixAccess = {'zabbix_user': oParser.zabbixuser,
                     'zabbix_passwd': oParser.zabbixpassword,
                     'zabbix_IP': oParser.zabbixip,
                     'zabbix_port': oParser.zabbixport}
    try:
        oRedis.set(ZABBIX_PFX, json.dumps(dZabbixAccess), oParser.redis_ttl)
        oRedis.hset(ACCESS_PFX, oParser.name, json.dumps(dConnInfo))
        oRedis.expire(ACCESS_PFX, oParser.redis_ttl)
    except RedisError:
        oLog.error('Cannot connect to Redis and set information')
        raise RedisError
    return


def _PushAIX_Info(oParser, oRedis):
    print(str(oParser))
    dConnectionInfo = {'type': oParser.server_type,
                       'sp-type': 'HMC',
                       'user': oParser.user,
                       'name': oParser.name,
                       'password': oParser.password,
                       'sp-user': oParser.hmc_user,
                       'sp-pass': oParser.hmc_password,
                       'srv-ip': oParser.server_ip,
                       'sp-ip': oParser.hmc_ip}
    _PushConnectionInfo2(dConnectionInfo, oParser, oRedis)
    return


def _PushESXnAMMInfo(oParser, oRedis):
    print(str(oParser))
    dConnectionInfo = {'type': oParser.server_type,
                       'sp-type': 'AMM',
                       'user': oParser.user,
                       'password': oParser.password,
                       'vcenter': oParser.vcenter,
                       'sp-user': oParser.amm_user,
                       'sp-pass': oParser.amm_password,
                       'srv-name': oParser.name,
                       'sp-ip': oParser.amm_ip}
    _PushConnectionInfo2(dConnectionInfo, oParser, oRedis)
    return


def _PushESXInfo(oParser, oRedis):
    print(str(oParser))
    dConnectionInfo = {'type': 'esxi',
                       'user': oParser.user,
                       'password': oParser.password,
                       'vcenter': oParser.vcenter,
                       'srv-name': oParser.name}
    _PushConnectionInfo2(dConnectionInfo, oParser, oRedis)
    return


def _Main():
    """parse CLI arguments, make connection to Redis and call a worker function"""
    oParser = ap.ArgumentParser(description="Make servers list for Zabbix")
    oSubParsers = oParser.add_subparsers(title='server types',
                                         dest='server_type',
                                         description='Supported server types',
                                         help='<server type>[_<service processor type>]')
    oParserAIX = oSubParsers.add_parser('aix_hmc')
    oParserESXiAmm = oSubParsers.add_parser('esxi_amm')
    oParserESXi = oSubParsers.add_parser('esxi')

    # construct parser for AIX options group
    oParserAIX.add_argument('-n', '--name', help='Server name on HMC', type=str, required=True)

    oParserAIX.add_argument('-i', '--server-ip', help="Server interface IP or FQDN", type=str, required=True)
    oParserAIX.add_argument('-I', '--hmc-ip', help="Service processor (HMC) interface IP or FQDN",
                            type=str, required=True)
    oParserAIX.add_argument('-u', '--user', help="Host login", type=str, required=True)
    oParserAIX.add_argument('-p', '--password', help="Host password", type=str, required=False)
    oParserAIX.add_argument('-U', '--hmc-user', help="Service processor login", type=str, required=True)
    oParserAIX.add_argument('-P', '--hmc-password', help="Service processor password",
                            type=str, required=False)
    oParserAIX.add_argument('-k', '--key', help="SSH key to authenticate to host", type=str, required=False)
    oParserAIX.add_argument('-K', '--hmc-key', help="SSH key to authenticate to SP",
                            type=str, required=False)
    oParserAIX.set_defaults(func=_PushAIX_Info)

    # Parser for ESXi host with AMM service processor
    oParserESXiAmm.add_argument('-n', '--name', help='Server full domain name (FQDN)',
                                type=str, required=True)
    oParserESXiAmm.add_argument(
        '-I', '--amm-ip', type=str, required=True,
        help="Blade system service processor (AMM) interface IP or FQDN")
    oParserESXiAmm.add_argument('-u', '--user', help="vCenter login", type=str, required=True)
    oParserESXiAmm.add_argument('-p', '--password', help="vCenter password", type=str, required=True)
    oParserESXiAmm.add_argument('-v', '--vcenter', help='vCenter FQDN or IP', type=str, required=True)
    oParserESXiAmm.add_argument('-U', '--amm-user', help="Service processor login", type=str, required=True)
    oParserESXiAmm.add_argument('-P', '--amm-password', help="Service processor password",
                                type=str, required=False)
    oParserESXiAmm.add_argument('-K', '--amm-key', help="SSH key to authenticate to AMM",
                                type=str, required=False)
    oParserESXiAmm.set_defaults(func=_PushESXnAMMInfo)

    # Parser for ESXi host without SP, WBEM access only
    oParserESXi.add_argument('-n', '--name', help='Server full domain name (FQDN)',
                             type=str, required=True)
    oParserESXi.add_argument('-u', '--user', help="vCenter login", type=str, required=True)
    oParserESXi.add_argument('-p', '--password', help="vCenter password", type=str, required=True)
    oParserESXi.add_argument('-v', '--vcenter', help='vCenter FQDN or IP', type=str, required=True)
    oParserESXi.set_defaults(func=_PushESXInfo)

    # Common arguments for all parsers
    oParser.add_argument('-r', '--redis', help="Redis database host:port or socket, default=localhost:6379",
                         default='localhost:6379', type=str, required=False)
    oParser.add_argument('--redis-ttl', help="TTL of Redis-cached data", type=int,
                         default=900, required=False)
    oParser.add_argument('-z', '--zabbixip', help="IP of Zabbix server", type=str,
                         default='127.0.0.1', required=False)
    oParser.add_argument('--zabbixport', help="Port for sending data to Zabbix server",
                         type=int, default=10051, required=False)
    oParser.add_argument('--zabbixuser', help="Zabbix server user name",
                         default='Admin', required=False)
    oParser.add_argument('--zabbixpassword', help="Zabbix server password",
                         default='zabbix', required=False)

    oArgs = oParser.parse_args()
    # connect to Redis database
    oRedis = _oConnect2Redis(oArgs.redis)
    # and call a function corresponding to server's type (from 'set_defaults')
    oArgs.func(oArgs, oRedis)
    return


#
# == main ==
#
if __name__ == '__main__':
    logging.config.dictConfig(dLoggingConfig)
    oLog = logging.getLogger('Srv.Discovery')
    oLog.debug('Starting Discovery-info program')
    sRet = "Not implemented yet"
    iRetCode = -1
    try:
        _Main()
        iRetCode = 0
    except RedisError:
        oLog.error('Cannot connect to Redis DB')
        iErrCode = 2
    except Exception as e:
        oLog.error("Exception at top-level {}".format(str(e)))
        traceback.print_exc()
        iRetCode = 1
    exit(iRetCode)

# vim: expandtab : softtabstop=4 : tabstop=4 : shiftwidth=4 : autoindent
