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
    # print(str(oParser))
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
    # print(str(oParser))
    dConnectionInfo = {'type': oParser.server_type,
                       'sp-type': 'AMM',
                       'user': oParser.user,
                       'password': oParser.password,
                       'vcenter': oParser.vcenter,
                       'sp-user': oParser.amm_user,
                       'sp-pass': oParser.amm_password,
                       'srv-name': oParser.name,
                       'amm_name': oParser.amm_name,
                       'sp-ip': oParser.amm_ip}
    _PushConnectionInfo2(dConnectionInfo, oParser, oRedis)
    return


def _PushESXInfo(oParser, oRedis):
    # print(str(oParser))
    dConnectionInfo = {'type': 'esxi',
                       'user': oParser.user,
                       'password': oParser.password,
                       'vcenter': oParser.vcenter,
                       'srv-name': oParser.name}
    if oParser.ipmi_ip:
        dIPMIaccess = {'ipmi.ip': oParser.ipmi_ip,
                       'ipmi.user': oParser.ipmi_user,
                       'ipmi.pass': oParser.ipmi_pwd}
        dConnectionInfo.update(dIPMIaccess)
    _PushConnectionInfo2(dConnectionInfo, oParser, oRedis)
    return


def _ConfigureAIXParser(oParser):
    # construct parser for AIX options group
    oParser.add_argument('-n', '--name', help='Server name on HMC', type=str, required=True)

    oParser.add_argument('-i', '--server-ip', help="Server interface IP or FQDN", type=str, required=True)
    oParser.add_argument('-I', '--hmc-ip', help="Service processor (HMC) interface IP or FQDN",
                         type=str, required=True)
    oParser.add_argument('-u', '--user', help="Host login", type=str, required=True)
    oParser.add_argument('-p', '--password', help="Host password", type=str, required=False)
    oParser.add_argument('-U', '--hmc-user', help="Service processor login", type=str, required=True)
    oParser.add_argument('-P', '--hmc-password', help="Service processor password",
                         type=str, required=False)
    oParser.add_argument('-k', '--key', help="SSH key to authenticate to host", type=str, required=False)
    oParser.add_argument('-K', '--hmc-key', help="SSH key to authenticate to SP",
                         type=str, required=False)
    oParser.set_defaults(func=_PushAIX_Info)
    return oParser


def _oConfigireESXi_AMM_Parser(oParser):
    """configures CLI parser for ESXi hosts with AMM/CMM service processor (blades)"""
    # Parser for ESXi host with AMM service processor
    oParser.add_argument('-n', '--name', help='Server full domain name (FQDN)',
                         type=str, required=True)
    oParser.add_argument(
        '-I', '--amm-ip', type=str, required=True,
        help="Blade system service processor (AMM) interface IP or FQDN")
    oParser.add_argument('-u', '--user', help="vCenter login", type=str, required=True)
    oParser.add_argument('-p', '--password', help="vCenter password", type=str, required=True)
    oParser.add_argument('-v', '--vcenter', help='vCenter FQDN or IP', type=str, required=True)
    oParser.add_argument('-N', '--amm-name', help="Name of blade in AMM", type=str, required=True)
    oParser.add_argument('-U', '--amm-user', help="Service processor login", type=str, required=True)
    oParser.add_argument('-P', '--amm-password', help="Service processor password",
                         type=str, required=False)
    oParser.add_argument('-K', '--amm-key', help="SSH key to authenticate to AMM",
                         type=str, required=False)
    oParser.set_defaults(func=_PushESXnAMMInfo)
    return oParser


def _oConfigureESXiParser(oParser):
    oParser.add_argument('-n', '--name', help='Server full domain name (FQDN)',
                         type=str, required=True)
    oParser.add_argument('-u', '--user', help="vcenter login", type=str, required=True)
    oParser.add_argument('-p', '--password',   help="vcenter password", type=str, required=True)
    oParser.add_argument('-v', '--vcenter',   help='vcenter fqdn or ip', type=str, required=True)
    oParser.add_argument('-I', '--ipmi-ip',
                         help="IP of IPMI (management) interface", type=str, required=False)
    oParser.add_argument('-U', '--ipmi-user', help="User for IPMI access", type=str, required=False)
    oParser.add_argument('-P', '--ipmi-pwd',  help="Password for IPMI access", type=str, required=False)
    oParser.set_defaults(func=_PushESXInfo)
    return oParser


def _Main():
    """parse CLI arguments, make connection to Redis and call a worker function"""
    oParser = ap.ArgumentParser(description="Make servers list for Zabbix")
    oSubParsers = oParser.add_subparsers(title='server types',
                                         dest='server_type',
                                         description='Supported server types',
                                         help='<server type>[_<service processor type>]')
    oParserAIX = oSubParsers.add_parser('aix_hmc')
    oParserAIX = _ConfigureAIXParser(oParserAIX)
    oParserESXiAmm = oSubParsers.add_parser('esxi_amm')
    oParserESXiAmm = _oConfigireESXi_AMM_Parser(oParserESXiAmm)
    oParserESXi = oSubParsers.add_parser('esxi')
    oParserESXi = _oConfigureESXiParser(oParserESXi)

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
    print('{"data":[]}')
    return


#
# == main ==
#
if __name__ == '__main__':
    logging.config.dictConfig(dLoggingConfig)
    oLog = logging.getLogger('Srv.Discovery')
    oLog.info('<<< Starting servers discovery program')
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
    oLog.info('>>> End of servers discovery program')
    exit(iRetCode)

# vim: expandtab : softtabstop=4 : tabstop=4 : shiftwidth=4 : autoindent
