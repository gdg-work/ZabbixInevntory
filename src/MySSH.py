#!/usr/bin/env python
# -*- coding: utf-8 -*-
import socket
import paramiko
import logging
# from time import sleep

SLEEP_DURATION = 0.5
SSH_ENCODING = 'utf-8'

oLog = logging.getLogger(__name__)


class MySSH_Error(Exception):
    def __init__(self, sMsg):
        super().__init__(sMsg)
        return


class AuthData:
    def __init__(self, sLogin, bUseKey, sPasswd=None, sKeyFile=None):
        self.sLogin = sLogin
        self.bUseKey = bUseKey
        if self.bUseKey:
            self.sKeyFile = sKeyFile
        else:
            self.sPasswd = sPasswd
        return

    def _sLogin(self):
        return self.sLogin

    def _sKey(self):
        return self.sKeyFile

    def _sPasswd(self):
        return self.sPasswd


class MySSHConnection:
    def __init__(self, sIP, iPort, oAuth):
        self.oSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.bConnected = False
        self.oClient = paramiko.SSHClient()
        try:
            # oLog.debug("*DBG* Trying to connect to IP {} port {:d}".format(sIP, iPort))
            self.oSocket.connect((sIP, iPort))
            self.bConnected = True
        except Exception as e:
            oLog.error("Cannot create socket connection")
            pass
        if self.bConnected:
            try:
                self.oClient.set_missing_host_key_policy(paramiko.client.AutoAddPolicy())
                self.oClient.load_system_host_keys()
                # self.oClient.load_host_keys(dssParams['KnownHostsFile'])
                self.oClient.connect(hostname=sIP, port=iPort, username=oAuth._sLogin(),
                                     password=oAuth._sPasswd(), sock=self.oSocket)
            except Exception as e:
                oLog.error("*CRIT* Error connecting: " + str(e))
                self.bConnected = False
        return

    def close(self):
        try:
            if self.oClient:
                self.oClient.close()
            if self.oSocket:
                self.oSocket.close()
        except Exception:
            pass
        return

    def fsRunCmd(self, sCmd):
        lResult = []
        if self.bConnected:
            stdin, stdout, stderr = self.oClient.exec_command(sCmd)
            for l in stdout:
                lResult.append(l)
            self.close()
        else:
            oLog.error("fsRunCmd: isnt connected")
        return "".join(lResult)

    def _lsRunCommands(self, lsCmds):
        lResult = []
        # sCommands = "\n".join(lsCmds)
        oLog.debug('_lsRunCommands called with commands: ' + str(lsCmds))
        try:
            for sCmd in lsCmds:
                stdin, stdout, stderr = self.oClient.exec_command(sCmd)
                sRes = stdout.read()
                if sRes:
                    lResult.append(sRes.decode(SSH_ENCODING).strip())
        except Exception as e:
            oLog.error('_lsRunCommands: error executing commands')
            oLog.error('Output:' + str(e))
        finally:
            self.oClient.close()
        oLog.debug("_lsRunCommands result:" + str(lResult))
        return lResult
