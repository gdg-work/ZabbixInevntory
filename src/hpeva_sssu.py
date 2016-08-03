#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import re
import pexpect
# for XML parsing
import bs4      # BeautifulSoup v4
# import redis  # In-memory NoSQL DB for caching
import pickle   # serializing/deserializing of objects to stings and files

# Storage classes
from inventoryObjects import ClassicArrayClass, ControllerClass, DiskShelfClass, DASD_Class
# local constants
from local import SSSU_PATH, CACHE_TIME, REDIS_ENCODING

import sys
sys.setrecursionlimit(5000)   # for 'pickle' module to correctly encode/decode BeautifulSoup objects
oLog = logging.getLogger(__name__)


#
# Helper functions
#
def __lRecursiveSoupQuery__(oObj, lElements):
    """helper function: recursive search by list of elements in XML tree"""
    lRet = []
    lFirstLvl = oObj.find_all(lElements[0], recursive=False)
    if len(lElements) == 1:
        lRet = [e.string for e in lFirstLvl]
    else:
        for oNextObj in lFirstLvl:
            lRet.append(__lRecursiveSoupQuery__(oNextObj, lElements[1:]))
    return lRet


def __lFlattenListOfLists__(x):
    result = []
    for el in x:
        if hasattr(el, "__iter__") and not isinstance(el, str):
            result.extend(__lFlattenListOfLists__(el))
        else:
            result.append(el)
    return result


#
#  EVA interface via SSSU
#
class SSSU_Error(Exception):
    """Error when running SSSU"""
    def __init__(self, sStr):
        """Initialize the error with a message given as a string"""
        self.sErrMsg = sStr
        return

    def __str__(self):
        """Converts an error to string for printing"""
        return repr(self.sErrMsg)

# CONSTANTS
MAXREAD = 100 * 1024   # bytes read at a time
SEARCHBUF = 2048     # bytes backward from the current position
TIMEOUT = 60         # seconds


class SSSU_Iface:
    # {{{
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
            self.pSSSU = pexpect.spawn(self.SSSU, maxread=MAXREAD,
                                       searchwindowsize=SEARCHBUF, timeout=TIMEOUT)
            self.pSSSU.expect("Manager:")
            self.pSSSU.send(sMgmtIP + "\n")
            self.pSSSU.expect("Username:")
            self.pSSSU.send(sLogin + "\n")
            self.pSSSU.expect("Password:")
            self.pSSSU.send(sPasswd + "\n")
            iIdx = self.pSSSU.expect(["NoSystemSelected>", "Error opening https connection"], 5)
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
                    self.pSSSU.send("SELECT SYSTEM %s\n" % sSystemName)   # good, continue our work
                    self.pSSSU.expect_exact(self.sPrompt)
                    # здесь ничего не должно случиться, мы уже проверили, что такое имя существует
                    self.pSSSU.send(
                        "SET OPTIONS on_error=Continue display_status noretries display_width=200\n")
                    self.pSSSU.expect("Status : 0")
                    self.pSSSU.expect_exact(self.sPrompt)
                    self.sSystemName = sSystemName
                    self._Dbg("Name of system and options are set")
                else:
                    self._Err("The array name '%s' is unknown to Command View on the management server %s" %
                              (sSystemName, sMgmtIP))
                    raise SSSU_Error("Unknown or invalid array name %s" % sSystemName)
            else:
                # Не смогли создать соединение с Command View
                self._Err("Cannot connect to Command View with credentials used")
                raise SSSU_Error("Cannot log in Command View. Are the credentials valid?")
        except pexpect.TIMEOUT:
            self._Close()
            raise SSSU_Error("__init__: Cannot connect to Command View (timeout)")
        except pexpect.EOF:
            self._Close()
            raise SSSU_Error("__init__: Cannot read data from SSSU utility (EOF)")
        return

    def _sGetSysName(self):
        return self.sSystemName

    def _sRunCommand(self, sCommand, sSeparator="\n"):
        """Runs a command on SSSU, returns the command's output as a string. Raises
        SSSU_Error on error conditions such as timeout or loss of connection"""
        self._Dbg("_sRunCommand called with command: '%s'" % sCommand)
        lReturn = []
        try:
            self.pSSSU.send("\n")
            self.pSSSU.expect_exact(self.sPrompt)
            self.pSSSU.send(sCommand + "\n")
            self.pSSSU.expect_exact(sCommand)
            self.pSSSU.expect_exact(self.sPrompt)
            lReturn = [s.strip() for s in self.pSSSU.before.decode('utf-8').split("\r\n")]
        except pexpect.TIMEOUT:
            self._Close()
            raise SSSU_Error("_sRunCommand(): Connection to SSSU lost (timeout)")
        except pexpect.EOF:
            self._Close()
            raise SSSU_Error("_sRunCommand(): Connection to SSSU lost (EOF while reading")
        return sSeparator.join(lReturn)

    def _Close(self):
        """Closes the interface"""
        self._Dbg("Closing SSSU connection")
        self.pSSSU.send("\n")
        self.pSSSU.sendline("exit")
        self.pSSSU.expect([pexpect.EOF, pexpect.TIMEOUT], 10)
        if self.pSSSU.isalive():
            self.pSSSU.terminate()
        return


class EVA_Exception(Exception):
    def __init__(self, sData):
        self.__str__ = lambda: sData


class HP_EVA_Class(ClassicArrayClass):
    def __init__(self, sIP, sUser, sPassword, sSysName, oRedisConn, sType="HP EVA"):
        super().__init__(sIP, sType)
        self.sSysName = sSysName
        self.dDiskByShelfPos = {}
        self.dDiskByName = {}
        self.dDiskByID = {}
        self.dDiskShelves = {}
        self.dControllers = {}
        self.oEvaConnection = SSSU_Iface(SSSU_PATH, sIP, sUser, sPassword, sSysName, oLog.debug, oLog.error)
        # сюда просится инициализация:
        #   - проверка наличия кэш-файлов в каталоге кэша и
        #   - либо запрос всей информации у CV и создание нового кэша
        #   - либо загрузка кэша
        self.sRedisKeyPrefix = "pyzabbix::hpeva_sssu::" + self.sSysName + "::"
        self.oRedisConnection = oRedisConn
        # dictionary of available queries and methods of the object
        self.dQueries = {"name": self.getName,
                         "sn": self.getSN,
                         "wwn": self.getWWN,
                         "type": self.getType,
                         "model": self.getModel,
                         "ctrls": self.getControllersAmount,
                         "shelves": self.getShelvesAmount,
                         "disks": self.getDisksAmount,
                         "ctrl-names": self.getControllerNames,
                         "shelf-names": self.getDiskShelfNames,
                         "disk-names":  self.getDiskNames,
                         "ps-amount": self.getControllerShelfPSUAmount}

    def __sFromSystem__(self, sParam):
        """returns information from 'ls system <name>' output as a *string*"""
        REDIS_KEY = self.sRedisKeyPrefix + "__sFromSystem__::lssystem"
        sReturn = ""
        reDots = re.compile(r"{0} \.+: ".format(sParam))
        # try to get cached version
        try:
            sResult = self.oRedisConnection.get(REDIS_KEY).decode(REDIS_ENCODING)
        except AttributeError:
            # Redis return nothing, make a request again
            sResult = self.oEvaConnection._sRunCommand("ls system {}".format(self.sSysName), "\n")
            self.oRedisConnection.set(REDIS_KEY, sResult.encode(REDIS_ENCODING), CACHE_TIME)
        # parameter name begins with position 0 and then a space and a row of dots follows
        lsLines = [l for l in sResult.split("\n") if reDots.search(l)]
        sReturn = lsLines[0].split(':')[1].strip()
        if len(lsLines) != 1:
            oLog.warning("__sFromSystem__: Strange -- more than one (%d) instance of parameter '%s'" %
                         (len(lsLines), sParam))
        return (sReturn)

    def __lsFromControllers__(self, sParam):
        """Returns information from EVA's controllers as a *list* object"""
        REDIS_KEY = self.sRedisKeyPrefix + "__lsFromControllers__::lscontroller"
        reDots = re.compile(r" \.+: ")
        try:
            sResult = self.oRedisConnection.get(REDIS_KEY).decode("utf-8")
        except AttributeError:
            # Redis return nothing, make a request again
            sResult = self.oEvaConnection._sRunCommand("ls controller full", "\n")
            self.oRedisConnection.set(REDIS_KEY, sResult, CACHE_TIME)
        lsLines = [l for l in sResult.split("\n") if reDots.search(l)]
        lsRet =  [l.split(':')[-1].strip() for l in lsLines]
        return lsRet

    def __lsFromControllersRecursive__(self, lParams):
        """A recursive query of elements from list lParams, first going  to lParams[0], then
        lParams[1] etc. until lParams[len(lParams)-1]. The last element(s) returns
        as a list of (list of…, list of… etc.) strings"""
        REDIS_SUFFIX = "pyzabbix::hpeva_sssu::__lsFromControllersRecursive__::lscontroller_xml::soup"
        REDIS_KEY = self.sRedisKeyPrefix + REDIS_SUFFIX

        # XXX такое чувство, что эта функция никогда не работает - в ней явные ошибки XXX
        oSoup = bs4.BeautifulSoup(self.oRedisConnection.get(REDIS_KEY), 'xml')
        if not oSoup:
            sResult = self.oEvaConnection._sRunCommand("ls controller full xml", "\n")
            self.oRedisConnection.set(REDIS_KEY, sResult)
            iFirstTagPos = sResult.find('<')
            sResult = sResult[iFirstTagPos - 1:]
            oSoup = bs4.BeautifulSoup(sResult, 'xml')
            self.oRedisConnection.set(REDIS_KEY, oSoup.encode(REDIS_ENCODING), CACHE_TIME)
        return (__lRecursiveSoupQuery__(oSoup, ['object'] + lParams))

    def __lsFromDiskShelf__(self, sName, sParam):
        """ Tries to find some information in the 'ls diskshelf' element.
        First, the function searches in <object> element, if the parameter isn't found,
        it is searched in child elements """
        REDIS_KEY = "{0}__lsFromDiskShelf__::lsdiskshelf_xml::{1}::soup".format(self.sRedisKeyPrefix, sName)
        oSoup = bs4.BeautifulSoup(self.oRedisConnection.get(REDIS_KEY), 'xml')
        if not oSoup:
            sResult = self.oEvaConnection._sRunCommand(
                'ls diskshelf "%s" xml' % sName, "\n")
            lRet = []
            # skip sResult string to first '<'
            iFirstTagPos = sResult.find('<')
            sResult = sResult[iFirstTagPos - 1:]
            oSoup = bs4.BeautifulSoup(sResult, 'xml')
            self.oRedisConnection.set(REDIS_KEY, oSoup.encode(REDIS_ENCODING), CACHE_TIME)
        # now I can parse this objects by any method
        for oDiskShelf in oSoup.find_all('object', recursive=False, limit=32):
            oElem = oDiskShelf.find(sParam, recursive=False)
            if oElem:
                lRet.append(oElem.string)
            else:
                lElems = oDiskShelf.find_all(sParam)
                for oElem in lElems:
                    lRet.append(oElem.string)
        return lRet

    def __lsFromDiskShelves__(self, sParam):
        """ Tries to find some information in the 'ls diskshelf' element.
        First, the function searches in <object> element, if the parameter isn't found,
        it is searched in child elements """
        REDIS_KEY = self.sRedisKeyPrefix + "__lsFromDiskShelves__::lsdiskshelf_nofull"
        try:
            sResult = self.oRedisConnection.get(REDIS_KEY).decode(REDIS_ENCODING)
        except AttributeError:
            sResult = self.oEvaConnection._sRunCommand("ls diskshelf nofull", "\n")
            self.oRedisConnection.set(REDIS_KEY, sResult.encode(REDIS_ENCODING), CACHE_TIME)
        lsDE_Names = [l for l in sResult.split("\n") if l.find("Disk Enclosure") >= 0]
        oLog.debug("Disk enclosures found: %s" % ", ".join(lsDE_Names))
        lRet = []
        for sDE_Name in lsDE_Names:
            lRet.append(self.__lsFromDiskShelf__(sDE_Name, sParam))
        return lRet

    def __lsFromDiskShelfRecursive__(self, lParams):
        """Рекурсивный запрос в сложный объект - дисковую полку.
        Параметр: _список_ имён элементов по порядку: сначала составляется
        список элементов, отвечающих первому имени в списке, затем в каждом
        из них ищется второй и т.д, пока список не окажется пуст - из последнего
        возвращается строковое значение"""
        REDIS_KEY = self.sRedisKeyPrefix + "__lsFromDiskShelfRecursive__::lsdiskshelf::{0}"
        try:
            sResult = self.oRedisConnection.get(REDIS_KEY.format("nofull")).decode(REDIS_ENCODING)
        except AttributeError:
            sResult = self.oEvaConnection._sRunCommand("ls diskshelf nofull", "\n")
            self.oRedisConnection.set(REDIS_KEY.format("nofull"), sResult.encode(REDIS_ENCODING), CACHE_TIME)
        lsDE_Names = [l for l in sResult.split("\n") if l.find("Disk Enclosure") >= 0]
        oLog.debug("Disk enclosures found: %s" % ", ".join(lsDE_Names))
        lRet = []
        for sDE_Name in lsDE_Names:
            sShelfRedisKey = REDIS_KEY.format(sDE_Name)
            try:
                oSoup = bs4.BeautifulSoup(self.oRedisConnection.get(sShelfRedisKey), 'xml')
            except AttributeError:
                oLog.debug('Querying disk shelf %s for %s' % (sDE_Name, ','.join(lParams)))
                sRes = self.oEvaConnection._sRunCommand('ls diskshelf "%s" xml' % sDE_Name, "\n")
                # skip sResult string to first '<'
                iFirstTagPos = sRes.find('<')
                sRes = sRes[iFirstTagPos - 1:]
                oSoup = bs4.BeautifulSoup(sRes, 'xml')
                self.oRedisConnection.set(sShelfRedisKey, oSoup.encode(REDIS_ENCODING), CACHE_TIME)

            oLog.debug('Serial # of disk shelf is %s' % oSoup.object.serialnumber.string)
            lFromShelf = __lRecursiveSoupQuery__(oSoup, ['object'] + lParams)
            lRet.append(lFromShelf)
        return (lRet)

    # public methods

    def getName(self):
        return(self.sSysName)

    def getSN(self):
        return("")

    def getWWN(self):
        return(self.__sFromSystem__('objectwwn'))

    def getType(self):
        return(self.__sFromSystem__('systemtype'))

    def getModel(self):
        return(self.__sFromSystem__('systemtype'))

    def getControllersAmount(self):
        REDIS_KEY = self.sRedisKeyPrefix + "lscontroller_nofull"
        try:
            sRes = self.oRedisConnection.get(REDIS_KEY).decode(REDIS_ENCODING)
        except AttributeError:
            # request information from the array
            sRes = self.oEvaConnection._sRunCommand("ls controller nofull")
            self.oRedisConnection.set(REDIS_KEY, sRes.encode(REDIS_ENCODING), CACHE_TIME)
        lsLines = [l for l in sRes.split("\n") if l.find('Controller') >= 0]
        iRet = len(lsLines)
        return iRet

    def getControllerNames(self):
        # redis key is the same as in previous function (getControllerAmount)
        REDIS_KEY = self.sRedisKeyPrefix + "lscontroller_nofull"
        try:
            sRes = self.oRedisConnection.get(REDIS_KEY).decode(REDIS_ENCODING)
        except AttributeError:
            # no information in cache, request information from the array
            sRes = self.oEvaConnection._sRunCommand("ls controller nofull")
            self.oRedisConnection.set(REDIS_KEY, sRes.encode(REDIS_ENCODING), CACHE_TIME)
        lsLines = [l.split('\\')[-1] for l in sRes.split("\n") if l.find('Controller') >= 0]
        oLog.debug("List of controller names: %s" % lsLines)
        return lsLines

    def getControllersSN(self):
        return self.__lsFromControllers__('serialnumber')

    def getControllerShelfPSUAmount(self):
        """Power supply amount of controller shelf. Works only for arrays
        with a controller shelf (4400?)"""
        REDIS_KEY = self.sRedisKeyPrefix + "ls_controller_enclusure"
        iRet = -1
        try:
            sOut = self.oRedisConnection.get(REDIS_KEY).decode(REDIS_ENCODING)
        except AttributeError:
            sOut = self.oEvaConnection._sRunCommand("ls controller_enclosure")
            if sOut.find('\\Hardware\\Controller Enclosure') >= 0:
                sOut = self.oEvaConnection._sRunCommand("ls controller_enclosure full xml", " ")
                iFirstTagPos = sOut.find('<') - 1
                sOut = sOut[iFirstTagPos:]
                self.oRedisConnection.set(REDIS_KEY, sOut.encode(REDIS_ENCODING), CACHE_TIME)
            else:
                sOut = ''
        # The enclosure exists
        if sOut:
            oSoup = bs4.BeautifulSoup(sOut, "xml")
            try:
                iRet = len(oSoup.object.powersources.find_all(name='source'))
            except AttributeError:
                # there are no 'powersources' attribute
                oLog.debug('getControllerShelfPSUAmount: Controller enclosure without power sources!')
                iRet = 0
        else:
            iRet = 0
        return iRet

    def getDiskShelfNames(self):
        REDIS_KEY = self.sRedisKeyPrefix + "lsdiskshelf_nofull"
        sOut = self.oRedisConnection.get(REDIS_KEY)  # bytes sting
        if sOut:
            sOut = sOut.decode(REDIS_ENCODING)
        else:
            sOut = self.oEvaConnection._sRunCommand("ls diskshelf nofull")
            self.oRedisConnection.set(REDIS_KEY, sOut.encode(REDIS_ENCODING), CACHE_TIME)
        lsLines = [l.split('\\')[-1] for l in sOut.split("\n") if l.find('Disk Enclosure') >= 0]
        oLog.debug('list of disk shelves names: %s' % ', '.join(lsLines))
        return lsLines

    def getShelvesAmount(self):
        REDIS_KEY = self.sRedisKeyPrefix + "lsdiskshelf_nofull"
        try:
            sRes = self.oRedisConnection.get(REDIS_KEY).decode(REDIS_ENCODING)
        except AttributeError:
            # request info from the array
            sRes = self.oEvaConnection._sRunCommand("ls diskshelf nofull")
            self.oRedisConnection.set(REDIS_KEY, sRes.encode(REDIS_ENCODING), CACHE_TIME)
        lsLines = [l.split('\\')[-1] for l in sRes.split("\n") if l.find('\\Disk Enclosure') >= 0]
        return len(lsLines)

    def getShelvesSN(self):
        """returns serial numbers of disk shelves attached to EVA"""
        lRet = (self.__lsFromDiskShelves__('serialnumber'))
        return __lFlattenListOfLists__(lRet)

    def getShelvesPwrSupplyAmount(self):
        """returns a list of power supplies amount for the disk shelves"""
        lShelvesPwrSNs = (self.__lsFromDiskShelfRecursive__(['powersupply', 'name']))
        return [len(__lFlattenListOfLists__(l)) for l in lShelvesPwrSNs]

    def getHostPortsCount(self):
        """returns a number of array's host side ports as an integer"""
        lPorts = self.__lsFromControllersRecursive__(['hostports', 'hostport', 'portname'])
        return len(__lFlattenListOfLists__(lPorts))

    def getPortIDs(self):
        """returns a list of host-side port names"""
        lPorts = self.__lsFromControllersRecursive__(['hostports', 'hostport', 'portname'])
        return __lFlattenListOfLists__(lPorts)

    def getHostPortWWNs(self):
        """returns a list of port WWNs (host side) as a list of strings"""
        lPorts = self.__lsFromControllersRecursive__(['hostports', 'hostport', 'wwid'])
        return __lFlattenListOfLists__(lPorts)

    def getHostPortSpeed(self):
        """returns a list of port WWNs (host side) as a list of strings"""
        lPorts = self.__lsFromControllersRecursive__(['hostports', 'hostport', 'speed'])
        return __lFlattenListOfLists__(lPorts)

    def getDisksAmount(self):
        """returns a total amount of disks as an integer"""
        iRet = len(self.getDiskNames())
        return iRet

    def getDiskNames(self, bShort=True):
        """returns a list of short disk names (like 'Disk 023'). These names are
        unique on a given array"""
        REDIS_KEY = self.sRedisKeyPrefix + "ls_disk_nofull"
        sFromRedis = self.oRedisConnection.get(REDIS_KEY)
        if sFromRedis:
            lsDiskNames = pickle.loads(sFromRedis)
        else:
            lsDiskNames = [d for d in self.oEvaConnection._sRunCommand("ls disk nofull", "||").split("||")
                           if d.find("\\Disk Groups\\") >= 0]
            self.oRedisConnection.set(REDIS_KEY, pickle.dumps(lsDiskNames), CACHE_TIME)
        #
        # Make a list of all drives and drive parameters and feed them to Zabbix via TCP
        # sArrayName, sZabbixIP, iZabbixPort, sZabUser, sZabPwd
        if bShort:
            lsShortNames = [d.split("\\")[-1] for d in lsDiskNames]
        else:
            lsShortNames = lsDiskNames
        # oLog.debug("list of disk names: {0}".format(str(lsShortNames)))
        return lsShortNames

    def __FillListOfDisks2__(self):
        """fills a list of storage array's disks in object's dictionaries
        (dDiskByID, dDiskByName, dDiskByShelfPos)"""
        REDIS_KEY_FORMAT = self.sRedisKeyPrefix + "__FillListOfDisks__::{0}"
        try:
            lDiskIDs = pickle.loads(
                self.oRedisConnection.get(REDIS_KEY_FORMAT.format("lDiskIDs")))
            sDisksKey = REDIS_KEY_FORMAT.format("dDiskByID")
            for sDiskID in lDiskIDs:
                sFromRedis = self.oRedisConnection.hget(sDisksKey, sDiskID)
                self.dDiskByID[sDiskID] = bs4.BeautifulSoup(sFromRedis, 'xml')
            self.dDiskByName = pickle.loads(
                self.oRedisConnection.get(REDIS_KEY_FORMAT.format("dDiskID_ByName")))
            self.dDiskByShelfPos = pickle.loads(
                self.oRedisConnection.get(REDIS_KEY_FORMAT.format("dDiskByShelfPos")))
        except TypeError:
            # TypeError: a bytes-like object is required, not 'NoneType'
            sDisksInfo = self.oEvaConnection._sRunCommand('ls disk full xml', ' ')
            sShelvesInfo =  self.oEvaConnection._sRunCommand('ls diskshelf full xml', ' ')
            # одеваем получившийся XML в корневые элементы и парсим в Beautiful Soup
            sDiskInfo = "<diskList> " + sDisksInfo + " </diskList>"
            sShelvesInfo = "<diskEnclosuresList> " + sShelvesInfo + " </diskEnclosuresList>"
            oDiskSoup = bs4.BeautifulSoup(sDiskInfo, 'xml')
            oShelvesSoup =  bs4.BeautifulSoup(sShelvesInfo, 'xml')
            # fill dictionaries disks by IDs and name
            for oDisk in oDiskSoup.find_all(name='object'):
                sID = str(oDisk.objecthexuid.string)
                oLog.debug("Found a disk with ID: <{0}>".format(sID))
                sName = str(oDisk.objectname.string)
                self.dDiskByID[sID] = oDisk
                self.dDiskByName[sName] = sID

            # disks for shelf position
            for oShelf in oShelvesSoup.find_all(name='object'):
                # iterate over disk shelves
                sShelf = str(oShelf.objectname.string)
                for oDiskBay in oShelf.find_all('diskslot'):
                    # iterate over disk slots
                    sPosition = "{0}\{1}".format(sShelf, str(oDiskBay.find('name').string))
                    sId = str(oDiskBay.diskwwn.string)
                    if sId in self.dDiskByID:
                        self.dDiskByShelfPos[sPosition] = sId
                    elif sId == '0000-0000-0000-0000-0000-0000-0000-0000':
                        # Empty slot has UID of all zeroes
                        oLog.debug('Empty slot {}'.format(sPosition))
                        pass
                    else:
                        oLog.info(
                            "There is a slot {0} with strange disk ID {1} that is not cataloged!".format(
                                sPosition, sId))

            # a dictionary is too large for pickle module so I'll store individual
            # elements of this dictionary and a list of keys.
            lDiskIDs = [str(l) for l in self.dDiskByID.keys()]
            oLog.debug("List of disk IDs: " + ','.join(lDiskIDs))
            # debug
            self.oRedisConnection.set(REDIS_KEY_FORMAT.format("lDiskIDs"), pickle.dumps(lDiskIDs), CACHE_TIME)
            sKey = REDIS_KEY_FORMAT.format("dDiskByID")
            for sID, oSoup in self.dDiskByID.items():
                # store disk soups in a hash structure in Redis to avoid recursion depth problems
                self.oRedisConnection.hset(sKey, sID, oSoup.encode(REDIS_ENCODING))
            self.oRedisConnection.set(
                REDIS_KEY_FORMAT.format("dDiskID_ByName"),
                pickle.dumps(self.dDiskByName), CACHE_TIME)
            self.oRedisConnection.set(
                REDIS_KEY_FORMAT.format("dDiskByShelfPos"),
                pickle.dumps(self.dDiskByShelfPos), CACHE_TIME)
        return

    def __FillDiskEnclosures__(self):
        """Requests and caches disk enclosure data"""
        REDIS_KEY = self.sRedisKeyPrefix + "ls_diskshelf_full_xml"
        sFromRedis = self.oRedisConnection.get(REDIS_KEY)
        if sFromRedis:
            # data present in the cache
            oSoup = bs4.BeautifulSoup(sFromRedis, 'xml')
        else:
            sXMLOut = self.oEvaConnection._sRunCommand('ls diskshelf full xml')
            sXMLOut = '<diskShelves> ' + sXMLOut + ' </diskShelves>'
            oSoup = bs4.BeautifulSoup(sXMLOut, "xml")
            self.oRedisConnection.set(REDIS_KEY, oSoup.encode(REDIS_ENCODING))
        for oShelf in oSoup.find_all(name='object'):
            sShelfName = oShelf.find('diskshelfname').string
            self.dDiskShelves[sShelfName] = EVA_DiskShelfClass(sShelfName, oShelf, self)
        oLog.debug('Found disk shelves: {}'.format(self.dDiskShelves.keys()))
        return

    def __FillControllers__(self):
        """Requests and caches disk enclosure data"""
        REDIS_KEY = self.sRedisKeyPrefix + "ls_controller_full_xml"
        sFromRedis = self.oRedisConnection.get(REDIS_KEY)
        if sFromRedis:
            # data present in the cache
            oSoup = bs4.BeautifulSoup(sFromRedis, 'xml')
        else:
            sXMLOut = self.oEvaConnection._sRunCommand('ls controller full xml')
            sXMLOut = '<EVAControllers> ' + sXMLOut + ' </EVAControllers>'
            oSoup = bs4.BeautifulSoup(sXMLOut, "xml")
            self.oRedisConnection.set(REDIS_KEY, oSoup.encode(REDIS_ENCODING))
        for oCtrl in oSoup.find_all(name='object'):
            sCtrlName = oCtrl.find('controllername').string
            self.dControllers[sCtrlName] = EVA_ControllerClass(sCtrlName, oCtrl, self)
        oLog.debug('Found Controllers: {}'.format(self.dControllers.keys()))
        return

    #
    # Methods for receiving components' information as a list of name:value dictionaries
    #
    def _ldGetDisksAsDicts(self):
        """ Return disk data as a list of Python dictionaries with fields:
        name, type, model, SN, position, RPM, size
        """
        ldRet = []
        if len(self.dDiskByID) == 0 or len(self.dDiskByName) == 0:
            self.__FillListOfDisks2__()
        try:
            for sDiskName, sDiskID in self.dDiskByName.items():
                oDiskSoup = self.dDiskByID[sDiskID]
                oDrive = EVA_DiskDriveClass(sDiskName, oDiskSoup, self)
                ldRet.append(oDrive._dGetDataAsDict())
        except Exception as e:
            oLog.warning("Exception when filling a disk parameters list")
            oLog.debug("Exception: " + str(e))
        return ldRet

    def _ldGetShelvesAsDicts(self):
        """ Return DEs' data as a list of Python dictionaries with fields:
        name, sn, type, model etc.
        """
        ldRet = []
        if self.dDiskShelves == {}:
            self.__FillDiskEnclosures__()
        try:
            for sName, oShelfObj in self.dDiskShelves.items():
                ldRet.append(oShelfObj._dGetDataAsDict())
        except Exception as e:
            oLog.warning("Exception when filling disk enclosures' parameters list")
            oLog.debug("Exception: " + str(e))
        return ldRet

    def _ldGetControllersInfoAsDict(self):
        ldRet = []
        if self.dControllers == {}:
            self.__FillControllers__()
        try:
            for sName, oCtrl in self.dControllers.items():
                ldRet.append(oCtrl._dGetDataAsDict())
        except Exception as e:
            oLog.warning("Exception when filling array controllers' parameters list")
            oLog.debug("Exception: " + str(e))
        return ldRet

    def getComponent(self, sCompName):
        """returns an object corresponding to an array component by name"""
        oLog.debug("getComponent called with name <%s>" % sCompName)
        reDiskNamePattern = re.compile(r'Disk \d{2,3}')
        # select the type of component
        oRetObj = None
        if sCompName.find('Controller') >= 0:   # disk controller
            if self.dControllers == {}:
                self.__FillControllers__()
            # now in dictionary 'self.dControllers' are name:soup pairs
            if sCompName in self.dControllers:
                oRetObj = self.dControllers(sCompName)
            else:
                oLog.info('Incorrect array controller name')
                oRetObj = None

            lsCtrls = self.getControllerNames()
            lsLines = [l for l in lsCtrls if l.find(sCompName) >= 0]
            # this list must be of length 1
            if len(lsLines) == 1:
                sObjName = lsLines[0]
                sXMLOut = self.oEvaConnection._sRunCommand('ls controller "%s" xml' % sObjName)
                oRetObj = EVA_ControllerClass(sObjName, sXMLOut, self)
            else:
                oLog.error("Incorrect controller object ID")
                oRetObj = None
        elif sCompName.find('Disk Enclosure') >= 0:    # disk enclosure
            # lsLines = [l for l in self.getDiskShelfNames() if l.find('\\' + sCompName + '\\') >= 0]
            if self.dDiskShelves == {}:
                self.__FillDiskEnclosures__()
            if sCompName in self.dDiskShelves:
                oRetObj = self.dDiskShelves[sCompName]
            else:
                oLog.info("Incorrect disk shelf name")
                oRetObj = None
        elif reDiskNamePattern.search(sCompName):   # disk drive name
            if len(self.dDiskByName) == 0:
                self.__FillListOfDisks2__()
            # lsDiskNames = self.getDiskNames(bShort = False)
            lsLines = [l for l in self.dDiskByName.keys() if l.find(sCompName) >= 0]
            # there must be only one disk with a given name
            if len(lsLines) == 1:
                sObjName = lsLines[0]
                sObjID = self.dDiskByName[sObjName]
                oLog.debug("Disk ID: " + sObjID)
                oLog.debug("Disk object name: {0}, disk ID: {1}, disk position: {2}".format(
                    sObjName, sObjID, (sObjID in self.dDiskByID)))
                oObjSoup = self.dDiskByID[sObjID]
                oRetObj = EVA_DiskDriveClass(sObjName, oObjSoup, self)
            else:
                oLog.error("Incorrect disk drive name '{0}'".format(sCompName))
        else:
            pass
        return (oRetObj)

    def _Close(self):
        self.oEvaConnection._Close()


class EVA_ControllerClass(ControllerClass):
    def __init__(self, sID, oSoup, oArrayObj):
        """
        creates an object from XML data returned by 'ls controller "<ID>" xml'
        Parameters: ID, BeautifulSoup of 'object' element of 'ls controller full xml',
        parent array
        """
        self.sName = sID
        self.oSoup = oSoup
        self.oParentArray = oArrayObj
        self.dQueries = {
            "name":       self.getName,
            "sn":         self.getSN,
            "type":       self.getType,
            "model":      self.getModel,
            "cpu-cores":  self.getCPUCores,
            "port-count": self.getPortCount}

    # "port-names": self.getPortNames,

    def _dGetDataAsDict(self):
        # name, type, model, SN, position, RPM, size
        dRet = {}
        for name, fun in self.dQueries.items():
            dRet[name] = fun()
        return dRet

    def getName(self):
        return self.sName

    def getSN(self):
        sRet = 'S/N not known'
        if self.oSoup:
            try:
                sRet = self.oSoup.serialnumber.string
            except AttributeError:
                oLog.info("EVA_ControllerClass.getSN: Can't receive serial number")
        else:
            oLog.debug("getSN: empty BS4 element")
        return sRet

    def getType(self):
        sRet = 'N/A'
        if self.oSoup:
            try:
                sRet = self.oSoup.productnumber.string
            except AttributeError:      # no element in XML tree
                oLog.info('EVA_ControllerClass.getType: no productnumber in XML')
        else:
            pass
        return sRet

    def getCPUCores(self):
        return "N/A"

    def getModel(self):
        sRet = "Model isn't known"
        if self.oSoup:
            try:
                sRet = self.oSoup.modelnumber.string
            except AttributeError:
                pass
        return sRet

    def getPortNames(self):
        lPortNames = []
        if self.oSoup:
            lPorts = self.oSoup.find_all("hostport")
            lPortNames = [p.portname.string for p in lPorts]
        else:
            pass
        return lPortNames

    def getPortCount(self):
        iRet = 0
        if self.oSoup:
            iRet = len(self.oSoup.find_all("hostport"))
        else:
            pass
        return iRet


class EVA_DiskShelfClass(DiskShelfClass):
    def __init__(self, sID, oSoup, oArrayObj):
        """creates an object. Parameters: 1) string ID,
        2) XML data as BeautifulSoup4 object,
        3) parent object (disk array) """
        self.oSoup = oSoup
        self.sName = str(self.oSoup.find('objectname').string)
        self.sShortName = self.sName.split('\\')[-1]
        if self.sName.find(sID) < 0:
            raise EVA_Exception("Invalid name of shelf in EVA_DiskShelfClass.init")
        self.oParentArray = oArrayObj
        self.dQueries = {   # permitted queries
            "name":   self.getName,
            "sn":     self.getSN,
            "type":   self.getType,
            "model":  self.getModel,
            "disks":  self.getDisksAmount,
            "disk-slots":  self.getSlotsAmount,
            "ps-amount":  self.getPwrSupplyAmount}

        #   "disk-names": self.getDiskNames, # <--- isn't needed now

    def _dGetDataAsDict(self):
        # name, type, model, SN, position, RPM, size
        dRet = {}
        for name, fun in self.dQueries.items():
            dRet[name] = fun()
        return dRet

    def getName(self):
        return self.sShortName

    def getSN(self):
        sRet = 'S/N not known'
        if self.oSoup:
            try:
                sRet = self.oSoup.serialnumber.string
            except AttributeError:
                pass
        return sRet

    def getType(self):
        sRet = "Can't determine type"
        if self.oSoup:
            try:
                sRet = self.oSoup.productid.string
            except AttributeError:
                pass
        return sRet

    def getModel(self):
        sRet = 'P/N not known'
        if self.oSoup:
            try:
                sRet = self.oSoup.productnum.string
            except AttributeError:
                pass
        return sRet

    def getDisksAmount(self):
        """return a number of occupied disk slots"""
        iRet = 0
        if self.oSoup:
            for sDS in self.oSoup.find_all('diskslot'):
                if sDS.state.string.find('installed') == 0 and sDS.diskstatus.string == "normal":
                    iRet += 1
        return iRet

    def getSlotsAmount(self):
        iRet = 0
        if self.oSoup:
            iRet = len(self.oSoup.find_all('diskslot'))
        return iRet

    def getDiskNames1(self):
        """return a list of disk slot names"""
        lRet = []
        if self.oSoup:
            for sDS in self.oSoup.find_all('diskslot'):
                lRet.append(self.sName + '\\' + sDS.find("name").string)
        return lRet

    def getDiskNames(self):
        """return a list of DISK names (not slot names)"""
        # fill dictionary of disks if it is empty
        if not self.oParentArray.dDiskByShelfPos:
            self.oParentArray.__FillListOfDisks2__()
        lRet = []
        if self.oSoup:
            # XXX по идее, полка не должна знать внутренние методы массива
            if str(self.oSoup.find('objectname').string).find(self.sName) >= 0:
                sShelfName = str(self.oSoup.find('objectname').string)
            for sDS in self.oSoup.find_all('diskslot'):
                # expand short name of shelf to full name with str.find
                sDiskPos = sShelfName + '\\' + sDS.find("name").string
                sDiskID = self.oParentArray.dDiskByShelfPos[sDiskPos]
                sDiskName = self.oParentArray.dDiskByID[sDiskID].find('objectname').string
                lRet.append(sDiskName)
        return lRet

    def getPwrSupplyAmount(self):
        """Amount of power supplies in this enclosure (typically 2)"""
        lPwrSupplies = self.oSoup.find_all('powersupply')
        iRet = 0
        for oPS in lPwrSupplies:
            iRet += 1
        oLog.debug("getPwrSupplyAmount: list of power supplies {:d}".format(iRet))
        return iRet


class EVA_DiskDriveClass(DASD_Class):

    def __init__(self, sID, oDiskSoup, oArrayObj):
        """Initializes an object. Parameters:
        1) sID: name of disk  (\Disk Groups\Default Disk Group\Disk 021)
        2) sXmlData: xml output of 'ls disk "<NAME>" xml'
        3) Parent object"""
        self.sName = sID
        self.sShortName = sID.split("\\")[-1]
        oLog.debug("EVA_DriveClass.__init__: disk name is {0}".format(self.sShortName))
        self.oSoup = oDiskSoup
        # oLog.debug('EVA_DriveClass.__init__: drive soup: \n {0}'.format(self.oSoup.prettify()))
        # search for unique-id identifier
        self.sDiskUID = self.oSoup.find(name='uid').string
        oLog.debug('EVA_DriveClass.__init__: unique ID of disk: \n {0}'.format(self.sDiskUID))
        self.dQueries = {   # permitted queries
            "sn":         self.getSN,
            "type":       self.getType,
            "model":      self.getModel,
            "disk-rpm":   self.getRPM,
            "disk-size":  self.getSize,
            "disk-pos":   self.getPosition}

    def getSN(self):
        sRet = "S/N not set"
        if self.oSoup:
            try:
                sRet = self.oSoup.find(name='serialnumber').string
            except AttributeError:
                pass
        return sRet

    def getSize(self):
        iRet = 0
        if self.oSoup:
            try:
                iRet = int(self.oSoup.formattedcapacity.string) * 512 // 2**30
            except AttributeError:
                pass
        return iRet

    def getModel(self):
        sRet = "Can't determine model"
        if self.oSoup:
            try:
                sRet = self.oSoup.modelnumber.string
            except AttributeError:
                pass
        return sRet

    def getType(self):
        sRet = "Type not known"
        if self.oSoup:
            try:
                sRet = self.oSoup.disktype.string
            except AttributeError:
                pass
        return sRet

    def getPosition(self):
        sRet = "Position not known"
        if self.oSoup:
            try:
                sRet = "Shelf {0} Slot {1}".format(
                    self.oSoup.shelfnumber.string,
                    self.oSoup.diskbaynumber.string)
            except AttributeError:
                pass
        return sRet

    def getRPM(self):
        return 0

    def _dGetDataAsDict(self):
        # name, type, model, SN, position, RPM, size
        return {'name': self.sShortName,
                'type': self.getType(),
                'model': self.getModel(),
                'SN': self.getSN(),
                'position': self.getPosition(),
                'RPM': self.getRPM(),
                'size': self.getSize()}

#
# some checks when this module isn't imported but is called with python directly
#
if __name__ == '__main__':
    oLog.error("hpeva_sssu: This is a library, not an executable")
    # set up logging
    oLog.setLevel(logging.DEBUG)
    oConHdr = logging.StreamHandler()
    oConHdr.setLevel(logging.DEBUG)
    oLog.addHandler(oConHdr)


# vim: expandtab : softtabstop=4 : tabstop=4 : shiftwidth=4
