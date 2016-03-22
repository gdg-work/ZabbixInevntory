#!/usr/bin/env python3

import paramiko
from binascii import hexlify
import socket
import yaml
import sys
import traceback

# CONSTANTS
DEFAULT_SSH_PORT=22

oData = yaml.load ("""
        name: gdgbook
        type: server
        access: ssh
        # IP: 192.168.135.133:ssh
        # IP:Port pairs must be quoted when in [] and CAN be unquoted in an itemized list
        IP: 
            - 192.168.34.2
            - 192.168.34.254:222
            - 192.168.135.133:2200
            - 127.0.0.1:2200
            - 192.168.40.66:2200
        sshMethod:
            login: dgolub
            ssh-key: /home/dgolub/.ssh/gdg@home.key
            KnownHostsFile: /home/dgolub/.ssh/known_hosts
        """)

print (oData)


#
# --- SSH connection/commands class ---
#

class MySSHConnection:
    def __init__(self, ltIP_Port_Pairs:list, dssParams: dict):
        self.oSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.bConnected = False
        self.oClient = paramiko.SSHClient()
        for tIP_Port in ltIP_Port_Pairs:
            try:
                print("*DBG* Trying to connect to IP %s port %d" % tIP_Port)
                self.oSocket.connect(tIP_Port)
                self.bConnected=True
                break
            except Exception as e:
                pass
        if self.bConnected:
            try:
                self.oClient.set_missing_host_key_policy(paramiko.client.AutoAddPolicy())
                self.oClient.load_system_host_keys()
                self.oClient.load_host_keys(dssParams['KnownHostsFile'])
                self.oClient.connect(hostname=tIP_Port[0], port=tIP_Port[1], username=dssParams['login'],
                                     key_filename=dssParams["ssh-key"],sock=self.oSocket)
            except Exception as e:
                print("*CRIT* Error connecting: " + str(e) )
                self.bConnected=False
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

    def fsRunCmd(self, sCmd: str) -> list:
        lResult = []
        if self.bConnected:
            stdin, stdout, stderr = self.oClient.exec_command('ls -l')
            for l in stdout:
                lResult.append(l)
        return "".join(lResult)

def ftSplitIPandPort(sIpPort: str) -> tuple:
    if ':' in sIpPort:
        sIP, sPort = sIpPort.split(":",1)
        if sPort.isnumeric(): iPort = int(sPort)
        else: iPort = DEFAULT_SSH_PORT
    else:
        sIP = sIpPort
        iPort = DEFAULT_SSH_PORT
    return ((sIP, iPort))

#
# --- SSSU access method class ---
#





#
# === M A I N ===
#
if isinstance(oData['IP'],str):
    lsIPs = [ oData['IP'] ]
elif isinstance(oData['IP'],list):
    lsIPs = oData['IP']
else:
    print("*DBG* Unknown type of oData['IP']: %s" % type(oData['IP']))
    raise Exception()

ltHostPortPairs = [ ftSplitIPandPort(s) for s in lsIPs ]
oSSHObject = MySSHConnection(ltHostPortPairs, oData['sshMethod'])
print (oSSHObject.fsRunCmd('ls -l'))
oSSHObject.close()

# vim: expandtab:tabstop=4:softtabstop=4:shiftwidth=4
