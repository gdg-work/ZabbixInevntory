#!/usr/bin/env python

import logging
import pexpect
import re
import os
import time
import inventoryObjects
from inventoryObjects import ClassicArrayClass

from local import SSSU_PATH

oLog = logging.getLogger(__name__)

# -- Constants --
asVDisks = "\\Virtual Disks\\"   # Название "фолдера" с виртуальными дисками в CV/EVA
aiVDLen = len(asVDisks)          # Используется при сравнениях


#
#  EVA interface via SSSU
# 
class SSSU_Error(Exception):
    """Error when running SSSU"""
    def __init__(self,sStr):
        """Initialize the error with a message given as a string"""
        self.sErrMsg=sStr
        return

    def __str__(self):
        """Converts an error to string for printing"""
        return repr(self.sErrMsg)


class SSSU_Iface:
    def __init__(self, sSSSU_Path, sMgmtIP, sLogin, sPasswd, 
                 sSystemName,  _Debug, _Error):
        """Инициализация класса. Параметры:
        sSSSU_Path = полное имя программы 'sssu'
        sMgmtIP    = IP адрес сервера управления (SMA)
        sLogin     = имя пользователя для входа на SMA
        sPasswd    = пароль для входа
        sSystemName = имя машины (EVA) в Command View
        fDbg       = функция, записывающая в лог отладочное сообщение
        fError     = функция, записывающая в лог сообщение об ошибке.
        """
        self.SSSU = sSSSU_Path
        self._Dbg = _Debug
        self._Err = _Error
        self.sPrompt = sSystemName + ">"
        self._Dbg("Initializing SSSU connection")
        self.sSystemName = ''
        try:
            self.pSSSU = pexpect.spawn(self.SSSU)
            self.pSSSU.expect("Manager:")
            self.pSSSU.send(sMgmtIP + "\n")
            self.pSSSU.expect("Username:")
            self.pSSSU.send(sLogin + "\n")
            self.pSSSU.expect("Password:")
            self.pSSSU.send(sPasswd + "\n")
            iIdx = self.pSSSU.expect(["NoSystemSelected>", "Error opening https connection"],5)
            if iIdx == 0:
                # Залогинились успешно.
                self._Dbg("Logged into SSSU")
                self.pSSSU.send("LS SYSTEM\n")
                self.pSSSU.expect("NoSystemSelected")
                # В списке систем пропускаем первые два элемента, а из остальных строк удаляем
                # пробелы и берём непустую часть
                lSystemNames = [l.strip() for l in self.pSSSU.before.decode('utf-8').split("\r\n")[3:] 
                                if l.strip() != ""]
                self._Dbg("Systems available: " + ", ".join(lSystemNames))
                if sSystemName in lSystemNames:
                    self.pSSSU.send("SELECT SYSTEM %s\n" % sSystemName) # good, continue our work
                    self.pSSSU.expect_exact(self.sPrompt)
                    # здесь ничего не должно случиться, мы уже проверили, что такое имя существует
                    self.pSSSU.send("SET OPTIONS on_error=Continue display_status noretries display_width=200\n")
                    self.pSSSU.expect("Status : 0")
                    self.pSSSU.expect_exact(self.sPrompt)
                    self.sSystemName = sSystemName
                    self._Dbg("Name of system and options are set")
                else:
                    self._Err("The array name '%s' is unknown to Command View on the management server %s" %
                              (sSystemName,sMgmtIP))
                    raise SSSU_Error("Unknown or invalid array name %s" % sSystemName)
            else:
                # Не смогли создать соединение с Command View
                self._Err("Cannot connect to Command View with credentials used")
                raise SSSU_Error("Cannot log in Command View. Are the credentials valid?")
        except pexpect.TIMEOUT:
            raise SSSU_Error("__init__: Cannot connect to Command View (timeout)")
        except pexpect.EOF:
            raise SSSU_Error("__init__: Cannot read data from SSSU utility (EOF)")
        return

    def _sGetSysName(self): return self.sSystemName

    def _sRunCommand(self, sCommand, sSeparator="\n"):
        """Runs a command on SSSU, returns the command's output as a string. Raises
        SSSU_Error on error conditions such as timeout or loss of connection"""
        self._Dbg("_sRunCommand called with command: '%s'" % sCommand)
        lReturn = []
        reDots = re.compile(r" \.+: ")
        try:
            self.pSSSU.send("\n")
            self.pSSSU.expect_exact(self.sPrompt)
            self.pSSSU.send(sCommand + "\n")
            self.pSSSU.expect(sCommand)
            self.pSSSU.expect_exact(self.sPrompt)
            lReturn = [s.strip() for s in self.pSSSU.before.decode('utf-8').split("\r\n")]
        except pexpect.TIMEOUT:
            raise SSSU_Error("_sRunCommand(): Connection to SSSU lost (timeout)")
        except pexpect.EOF:
            raise SSSU_Error("_sRunCommand(): Connection to SSSU lost (EOF while reading")
        return sSeparator.join(lReturn)

    def _Close(self):
        """Closes the interface"""
        self._Dbg("Closing SSSU connection")
        self.pSSSU.send("\n")
        self.pSSSU.sendline("exit")
        self.pSSSU.expect([ pexpect.EOF, pexpect.TIMEOUT ],10)
        if self.pSSSU.isalive():
            self.pSSSU.terminate()
        return


class HP_EVA_Class(ClassicArrayClass):
    def __init__(self, sIP, sUser, sPassword, sSysName, sType="HP EVA"):
        super().__init__(sIP, sType)
        self.sSysName = sSysName
        self.oEvaConnection = SSSU_Iface(SSSU_PATH, sIP, sUser, sPassword, sSysName, oLog.debug, oLog.error)
        pass

    def __sFromSystem__(self, sParam):
        """returns information from 'ls system <name>' output as a *string*"""
        sReturn = ""
        sResult = self.oEvaConnection._sRunCommand("ls system %s | grep %s" % (self.sSysName, sParam),"\n")
        # parameter name begins with position 0 and then a space and a row of dots follows
        lsLines = [ l for l in sResult.split("\n") 
                if ( l.find(sParam + ' ..') == 0 and l.find('....') > 0) ]
        sReturn = lsLines[0].split(':')[1].strip()
        if len(lsLines) != 1:
            oLog.warning("__sFromSystem__: Strange -- more than one (%d) instance of parameter '%s'" % (len(lLines), sParam))
        return (sReturn)

    def __lsFromControllers__(self, sParam):
        """Returns information from EVA's controllers as a *list* object"""
        sResult = self.oEvaConnection._sRunCommand("ls controller full | grep %s" % sParam,"\n")
        print (sResult)
        lsLines = [ l for l in sResult.split("\n") 
                if ( l.find(sParam + ' ..') == 0 and l.find('....') > 0) ]
        lsRet =  [ l.split(':')[-1].strip() for l in lsLines ]
        return lsRet


    def getSN(self):  return("")

    def getWWN(self):
        return(self.__sFromSystem__('objectwwn'))

    def getType(self):
        return(self.__sFromSystem__('systemtype'))

    def getModel(self):
        return(self.__sFromSystem__('systemtype'))

    def getControllersAmount(self):
        if self.lControllers == []:
            # request information from the array
            sRes = self.oEvaConnection._sRunCommand("ls controller nofull")
            oLog.debug("result: " + sRes)
            lsLines = [l for l in sRes.split("\n") if l.find('Controller') >= 0]
            return len(lsLines)
        else:
            return len(self.lControllers)
        pass

    def getControllerNames(self):
        if self.lControllers == []:
            # request information from the array
            sRes = self.oEvaConnection._sRunCommand("ls controller nofull")
            lsLines = [l.split('\\')[-1] for l in sRes.split("\n") if l.find('Controller') >= 0]
            return lsLines
        else:
            return [c.getID() for c in self.lControllers]
        pass

    def getControllersSN(self):
        return self.__lsFromControllers__('serialnumber')

    def getShelvesAmount(self):
        return 0

    def _Close(self):
        self.oEvaConnection._Close()


if __name__ == '__main__':
    oLog.error("This is a library, not an executable")

    # some testing
    oLog.setLevel(logging.DEBUG)
    # a = HP_EVA_Class("eva.hostco.ru", "dgolub", "Dtd-Iun2vp", "Primary_EVA6300")
    a = HP_EVA_Class("eva.hostco.ru", "dgolub", "Dtd-Iun2vp", "[HOST]EVA-4400")
    print(a.getModel())
    print(a.getControllerNames())
    print(a.getControllersSN())
    a._Close()
