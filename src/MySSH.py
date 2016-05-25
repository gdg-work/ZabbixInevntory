#!/usr/bin/env python
# -*- coding: utf-8 -*-
import socket
import paramiko
import logging
# for debugging
import traceback
import time
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
        self.sRemoteIP = sIP
        self.iRemotePort = iPort
        self.oAuth = oAuth
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
            if self.oClient.get_transport() is not None:
                stdin, stdout, stderr = self.oClient.exec_command(sCmd)
                for l in stdout:
                    lResult.append(l)
            else:
                oLog.error("No transport for exec_command")
        else:
            oLog.error("fsRunCmd: isnt connected")
            self.close()
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
            traceback.print_exc()
        finally:
            self.oClient.close()
        oLog.debug("_lsRunCommands result:" + str(lResult))
        return lResult

    def _lsRunCommands2(self, lsCmds):
        lResult = []
        SLEEP_TIME = 0.3
        sRes = '---'
        # sCommands = "\n".join(lsCmds)
        lsCmds.append('exit')
        print('_lsRunCommands2 called with commands: ' + str(lsCmds))
        try:
            oChannel = self.oClient.invoke_shell(term='dumb', width=120)
            # flush the buffer
            while not oChannel.send_ready():
                time.sleep(SLEEP_TIME)
            for sCmd in lsCmds:
                while not oChannel.send_ready():
                    time.sleep(SLEEP_TIME)
                oChannel.send(sCmd + '\r\n')
            while not oChannel.recv_ready():
                time.sleep(SLEEP_TIME)
            while oChannel.recv_ready() and not (sRes.endswith('exit\r')):
                sRes += oChannel.recv(1024 * 64).decode(SSH_ENCODING)
                time.sleep(SLEEP_TIME)
            if sRes:
                lResult = [s.strip() for s in sRes.split('\n')]
        except Exception as e:
            oLog.error('_lsRunCommands2: error executing commands')
            oLog.error('Output:' + str(e))
            traceback.print_exc()
        finally:
            self.oClient.close()
        oLog.debug("_lsRunCommands result:" + str(lResult))
        return lResult
