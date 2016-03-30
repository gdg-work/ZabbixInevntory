#!/usr/bin/env python

import logging
import re
import pexpect
# for XML parsing
import bs4    # BeautifulSoup v4

# Storage classes
from inventoryObjects import ClassicArrayClass, ControllerClass, DiskShelfClass, PortClass, DASD_Class
# local constants
from local import SSSU_PATH

# import time
# import pickle

oLog = logging.getLogger(__name__)

# -- Constants --

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

# def __lFlattenListOfLists__(lComplexList):
#     return list(it_chain(* lComplexList))

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
    def __init__(self,sStr):
        """Initialize the error with a message given as a string"""
        self.sErrMsg=sStr
        return

    def __str__(self):
        """Converts an error to string for printing"""
        return repr(self.sErrMsg)


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
        try:
            self.pSSSU.send("\n")
            self.pSSSU.expect_exact(self.sPrompt)
            self.pSSSU.send(sCommand + "\n")
            self.pSSSU.expect_exact(sCommand)
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

    # }}} SSSU_IFace


class HP_EVA_Class(ClassicArrayClass):
    def __init__(self, sIP, sUser, sPassword, sSysName, sType="HP EVA"):
        super().__init__(sIP, sType)
        self.sSysName = sSysName
        self.dDiskByShelfPos = {}
        self.dDiskByName = {}
        self.dDiskByID = {}
        self.lDiskShelves = []
        self.lControllers = []
        self.oEvaConnection = SSSU_Iface(SSSU_PATH, sIP, sUser, sPassword, sSysName, oLog.debug, oLog.error)
        # сюда просится инициализация:
        #   - проверка наличия кэш-файлов в каталоге кэша и
        #   - либо запрос всей информации у CV и создание нового кэша
        #   - либо загрузка кэша
        pass

    def __sFromSystem__(self, sParam):
        """returns information from 'ls system <name>' output as a *string*"""
        sReturn = ""
        reDots = re.compile(r"{0} \.+: ".format(sParam))
        sResult = self.oEvaConnection._sRunCommand("ls system %s | grep %s" % (self.sSysName, sParam),"\n")
        # parameter name begins with position 0 and then a space and a row of dots follows
        lsLines = [ l for l in sResult.split("\n") if reDots.search(l) ]
                # if ( l.find(sParam + ' ..') == 0 and l.find('....') > 0) ]
        sReturn = lsLines[0].split(':')[1].strip()
        if len(lsLines) != 1:
            oLog.warning("__sFromSystem__: Strange -- more than one (%d) instance of parameter '%s'" % (len(lsLines), sParam))
        return (sReturn)

    def __lsFromControllers__(self, sParam):
        """Returns information from EVA's controllers as a *list* object"""
        reDots = re.compile(r" \.+: ")
        sResult = self.oEvaConnection._sRunCommand("ls controller full | grep %s" % sParam,"\n")
        lsLines = [ l for l in sResult.split("\n") if reDots.search(l) ]
                # if ( l.find(sParam + ' ..') == 0 and l.find('....') > 0) ]
        lsRet =  [ l.split(':')[-1].strip() for l in lsLines ]
        return lsRet

    def __lsFromControllersRecursive__(self, lParams):
        """A recursive query of elements from list lParams, first going  to lParams[0], then
        lParams[1] etc. until lParams[len(lParams)-1]. The last element(s) returns 
        as a list of (list of…, list of… etc.) strings"""
        sResult = self.oEvaConnection._sRunCommand("ls controller full xml","\n")
        # oLog.debug("__lsFromControllersRecursive__: output of 'ls controller full xml'")
        # oLog.debug(sResult)
        # oLog.debug("__lsFromControllersRecursive__: end of output")
        iFirstTagPos = sResult.find('<')
        sResult = sResult[iFirstTagPos-1:]
        oSoup = bs4.BeautifulSoup(sResult,'xml')
        return (__lRecursiveSoupQuery__(oSoup, ['object'] + lParams))


    def __lsFromDiskShelf__(self, sName, sParam):
        """ Tries to find some information in the 'ls diskshelf' element.
        First, the function searches in <object> element, if the parameter isn't found,
        it is searched in child elements """
        sResult = self.oEvaConnection._sRunCommand(
                'ls diskshelf "%s" xml' % sName,"\n")
        lRet = []
        # skip sResult string to first '<'
        iFirstTagPos = sResult.find('<')
        sResult = sResult[iFirstTagPos-1:]
        oSoup = bs4.BeautifulSoup(sResult,'xml')
        # now I can parse this objects by any method
        for oDiskShelf in oSoup.find_all('object',recursive=False, limit=32):
            oElem = oDiskShelf.find(sParam,recursive=False)
            if oElem:
                lRet.append( oElem.string )
            else:
                lElems = oDiskShelf.find_all(sParam)
                for oElem in lElems:
                    lRet.append( oElem.string )
        return lRet

    def __lsFromDiskShelves__(self, sParam):
        """ Tries to find some information in the 'ls diskshelf' element.
        First, the function searches in <object> element, if the parameter isn't found,
        it is searched in child elements """
        sResult = self.oEvaConnection._sRunCommand("ls diskshelf nofull","\n")
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
        sResult = self.oEvaConnection._sRunCommand("ls diskshelf nofull","\n")
        lsDE_Names = [l for l in sResult.split("\n") if l.find("Disk Enclosure") >= 0]
        oLog.debug("Disk enclosures found: %s" % ", ".join(lsDE_Names))
        lRet = []
        for sDE_Name in lsDE_Names:
            oLog.debug('Querying disk shelf %s for %s' % (sDE_Name, ','.join(lParams)))
            sRes = self.oEvaConnection._sRunCommand('ls diskshelf "%s" xml' % sDE_Name,"\n")
            # skip sResult string to first '<'
            iFirstTagPos = sRes.find('<')
            sRes = sRes[iFirstTagPos-1:]
            oSoup = bs4.BeautifulSoup(sRes,'xml')
            oLog.debug('Serial # of disk shelf is %s' % oSoup.object.serialnumber.string)
            lFromShelf = __lRecursiveSoupQuery__(oSoup, ['object'] + lParams)
            lRet.append(lFromShelf)
        return (lRet)

    # public methods

    def getName(self):  return(self.sSysName)

    def getSN(self):  return("")

    def getWWN(self):
        return(self.__sFromSystem__('objectwwn'))

    def getType(self):
        return(self.__sFromSystem__('systemtype'))

    def getModel(self):
        return(self.__sFromSystem__('systemtype'))

    def getControllersAmount(self) ->int:
        if self.lControllers == []:
            # request information from the array
            sRes = self.oEvaConnection._sRunCommand("ls controller nofull")
            lsLines = [l for l in sRes.split("\n") if l.find('Controller') >= 0]
            return len(lsLines)
        else:
            return len(self.lControllers)
        pass

    def getControllerNames(self) ->list:
        if self.lControllers == []:
            # request information from the array
            sRes = self.oEvaConnection._sRunCommand("ls controller nofull")
            lsLines = [l.split('\\')[-1] for l in sRes.split("\n") if l.find('Controller') >= 0]
            oLog.debug("List of controller names: %s" % lsLines)
            return lsLines
        else:
            return [c.getID() for c in self.lControllers]
        pass

    def getControllersSN(self) ->list:
        return self.__lsFromControllers__('serialnumber')

    def getControllerShelfPSUAmount(self) -> int:
        """Power supply amount of controller shelf. Works only for arrays 
        with a controller shelf (4400?)"""
        iRet = 0
        sOut = self.oEvaConnection._sRunCommand("ls controller_enclosure")
        if sOut.find('\\Hardware\\Controller Enclosure') >= 0:
            # The enclosure exists
            sOut = self.oEvaConnection._sRunCommand("ls controller_enclosure full xml", " ")
            iFirstTagPos=sOut.find('<') - 1
            oSoup = bs4.BeautifulSoup(sOut[iFirstTagPos:],"xml")
            iRet = len(oSoup.object.powersources.find_all(name='source'))
        else:
            iRet = 0
        return iRet
        
    def getDiskShelfNames(self):
        sOut = self.oEvaConnection._sRunCommand("ls diskshelf nofull")
        lsLines = [l.split('\\')[-1] for l in sOut.split("\n") if l.find('Disk Enclosure') >= 0]
        oLog.debug('list of disk shelves names: %s' % ', '.join(lsLines))
        return lsLines

    def getShelvesAmount(self) ->int:
        # iRet = ( __lsFromDiskShelves__('serialnumber'))
        if self.lDiskShelves == []:
            # request info from the array
            sRes = self.oEvaConnection._sRunCommand("ls diskshelf nofull")
            lsLines = [l.split('\\')[-1] for l in sRes.split("\n") if l.find('Disk Enclosure') >= 0]
            iRet = len(lsLines)
        else:
            iRet = len(self.lDiskShelves)
        return iRet

    def getShelvesSN(self) ->list:
        """returns serial numbers of disk shelves attached to EVA"""
        lRet = ( self.__lsFromDiskShelves__('serialnumber') )
        return __lFlattenListOfLists__(lRet)

    def getShelvesPwrSupplyAmount(self) ->list:
        """returns a list of power supplies amount for the disk shelves"""
        lShelvesPwrSNs = ( self.__lsFromDiskShelfRecursive__(['powersupply', 'name']))
        return [len(__lFlattenListOfLists__(l)) for l in lShelvesPwrSNs]

    def getHostPortsCount(self) ->int:
        """returns a number of array's host side ports as an integer"""
        lPorts = self.__lsFromControllersRecursive__(['hostports', 'hostport', 'portname'])
        return len(__lFlattenListOfLists__(lPorts))

    def getPortIDs(self) ->list:
        """returns a list of host-side port names"""
        lPorts = self.__lsFromControllersRecursive__(['hostports', 'hostport', 'portname'])
        return __lFlattenListOfLists__(lPorts)

    def getHostPortWWNs(self) ->list:
        """returns a list of port WWNs (host side) as a list of strings"""
        lPorts = self.__lsFromControllersRecursive__(['hostports', 'hostport', 'wwid'])
        return __lFlattenListOfLists__(lPorts)

    def getHostPortSpeed(self) ->list:
        """returns a list of port WWNs (host side) as a list of strings"""
        lPorts = self.__lsFromControllersRecursive__(['hostports', 'hostport', 'speed'])
        return __lFlattenListOfLists__(lPorts)

    def getDisksAmount(self) ->int:
        """returns a total amount of disks as an integer"""
        iRet = len(self.getDiskNames())
        return iRet

    def getDiskNames(self) -> list:
        """returns a list of short disk names (like 'Disk 023'). These names are
        unique on a given array"""
        lsDiskNames = [ d for d in self.oEvaConnection._sRunCommand("ls disk nofull","||").split("||")
                           if d.find("\\Disk Groups\\") >= 0 ]
        lsShortNames = [d.split("\\")[-1] for d in lsDiskNames  ] 
        oLog.debug("list of disk names: {0}".format(str(lsShortNames)))
        return lsShortNames


    def __fillListOfDisks__(self):
        """fill the internal list of disks with data"""
        # First, create an object for all the disks in the array and fill this arry with data

        # next, make a list of disks' names
        lsDiskNames = [ d for d in self.oEvaConnection._sRunCommand("ls disk nofull","||").split("||")
                           if d.find("\\Disk Groups\\") >= 0 ]
        oLog.debug("list of disk names: {0}".format(str(lsDiskNames)))
        for sDiskName in lsDiskNames:
            # get information from an array
            sLines = self.oEvaConnection._sRunCommand('ls disk "{0}" xml'.format(sDiskName)," ")
            iFirstTagPos = sLines.find('<') - 1
            oDiskSoup = bs4.BeautifulSoup(sLines[iFirstTagPos:],'xml')           # trim the disk name before XML
            # add the disk to dictionaries
            sDiskID = oDiskSoup.object.objecthexuid.string
            self.dDiskByID[sDiskID] = oDiskSoup
            self.dDiskByName[sDiskName] = sDiskID
        oLog.debug("Disks list: {0}".format(str(self.dDiskByID.keys())))
        # Now check the output of 'ls diskshelf xml' for all disk shelves 
        # and fill the other dictionary (shelf position -> disk ID) 
        lsShelves = [ s for s in self.oEvaConnection._sRunCommand(
            'ls diskshelf nofull'.format(sDiskName),"||"
            ).split("||") if s.find('Disk Enclosure') >= 0 ]
        oLog.debug("Number of disk shelves found: {0:d}".format(len(lsShelves)))
        for sShelf in lsShelves:
            sShelfInfo = self.oEvaConnection._sRunCommand('ls diskshelf "{0}" xml'.format(sShelf)," ")
            iFirstTagPos = sShelfInfo.find('<') - 1
            oShelfSoup = bs4.BeautifulSoup(sShelfInfo[iFirstTagPos:],'xml')
            for oDiskBay in oShelfSoup.find_all('diskslot'):
                sPosition = "{0}\{1}".format(sShelf, oDiskBay.find('name').string)
                sId = oDiskBay.diskwwn.string
                if sId in self.dDiskByID:
                    self.dDiskByShelfPos[sPosition] = sId
                else:
                    oLog.info(
                    "There is a slot {0} with strange disk ID {1} that is not cataloged!".format(
                        sPosition, sId   
                    ))
            oLog.debug("Dictionary of shelf pos:disk ID is {0}".format(str(self.dDiskByShelfPos)))

        return


    def getComponent(self, sCompName) -> object:
        """returns an object corresponding to an array component by name"""
        oLog.debug("getComponent called with name <%s>" % sCompName)
        reDiskNamePattern = re.compile(r'Disk \d{2,3}')
        # select the type of component
        oRetObj = None
        if sCompName.find('Controller') >= 0:   # disk controller
            sCtrls = self.oEvaConnection._sRunCommand("ls controller nofull")
            lsLines = [l for l in sCtrls.split("\n") if l.find('Controller') >= 0 and l.find(sCompName) >= 0]
            oLog.debug("List of controller names: %s" % lsLines)
            # this list must be of length 1
            if len(lsLines) == 1:
                sObjName = lsLines[0]
                sXMLOut = self.oEvaConnection._sRunCommand('ls controller "%s" xml' % sObjName)
                oRetObj = EVA_ControllerClass(sObjName, sXMLOut)
            else:
                oLog.error("Incorrect controller object ID")
                oRetObj = None
        elif sCompName.find('Disk Enclosure') >= 0: # disk enclosure
            sDEs = self.oEvaConnection._sRunCommand("ls diskshelf nofull")
            lsLines = [l for l in sDEs.split("\n") if l.find('Disk Enclosure') >= 0 and l.find(sCompName) >= 0]
            oLog.debug("List of disk enclosure names: %s" % lsLines)
            # this list must be of length 1
            if len(lsLines) == 1:
                sObjName = lsLines[0]
                sXMLOut = self.oEvaConnection._sRunCommand('ls diskshelf "%s" xml' % sObjName)
                oRetObj = EVA_DiskShelfClass(sObjName, sXMLOut)
            else:
                oLog.error("Incorrect disk enclosure object ID")
                oRetObj = None
        elif reDiskNamePattern.search(sCompName): # disk drive name
            sDisks = self.oEvaConnection._sRunCommand("ls disk nofull","\n")
            lsLines = [l for l in sDisks.split("\n") if l.find('\\Disk Groups\\') >= 0 and l.find(sCompName) >= 0]
            # there must be only one disk with a given name
            if len(lsLines) == 1:
                sObjName=lsLines[0]
                sXmlOut = self.oEvaConnection._sRunCommand('ls disk "%s" xml' % sObjName)
                oRetObj = EVA_DiskDriveClass(sObjName, sXmlOut)
            else:
                oLog.error("Incorrect disk drive name '{0}'.format(sCompName)")
            # self.__fillListOfDisks__()
        else:
            pass
        return (oRetObj)


    def _Close(self):
        self.oEvaConnection._Close()


class EVA_ControllerClass(ControllerClass):
    def __init__(self, sID, sEvaXMLData):
        """creates an object from XML data returned by 'ls controller "<ID>" xml' """
        # make a well-formed XML string from sEvaXMLData and a BeautifulSoup object from this string
        # skip sResult string to first '<'
        self.sName = sID
        iFirstTagPos = sEvaXMLData.find('<')
        sEvaXMLData = sEvaXMLData[iFirstTagPos-1:]
        self.oSoup = bs4.BeautifulSoup(sEvaXMLData,'xml')

    def getName(self): return self.sName

    def getSN(self):
        if self.oSoup:
            return self.oSoup.object.serialnumber.string
        else:
            return ''

    def getType(self):
        if self.oSoup:
            return self.oSoup.object.productnumber.string
        else:
            return ''
        pass

    def getCPUCores(self): return "N/A"

    def getModel(self):
        if self.oSoup:
            return self.oSoup.object.modelnumber.string
        else:
            return ''
        pass

    def getPortNames(self):
        if self.oSoup:
            lPorts=self.oSoup.find_all("hostport")
            lPortNames = [p.portname.string for p in lPorts]
        else:
            pass
        return lPortNames

class EVA_DiskShelfClass(DiskShelfClass):
    def __init__(self, sID:str, sEvaXMLData):
        """creates an object. Parameters: 1) string ID, 2) XML data from 'ls diskshelf "<NAME>" xml' """
        # make a well-formed XML string from sEvaXMLData and a BeautifulSoup object from this string
        # skip sResult string to first '<'
        iFirstTagPos = sEvaXMLData.find('<')
        sEvaXMLData = sEvaXMLData[iFirstTagPos-1:]
        self.sName = sID
        self.oSoup = bs4.BeautifulSoup(sEvaXMLData,'xml')

    def getName(self): return self.sName

    def getSN(self):
        if self.oSoup:
            return self.oSoup.object.serialnumber.string
        else:
            return ''

    def getType(self):
        if self.oSoup:
            return self.oSoup.object.productid.string
        else:
            return ''
        pass

    def getModel(self):
        if self.oSoup:
            return self.oSoup.object.productnum.string
        else:
            return ''
        pass

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

    def getDiskNames(self):
        """return a list of disk slot names"""
        lRet = []
        if self.oSoup:
            for sDS in self.oSoup.find_all('diskslot'):
                lRet.append(self.sName + '\\' + sDS.find("name").string)
        return lRet

    def getPwrSupplyAmount(self):
        """Amount of power supplies in this enclosure (typically 2)"""
        lFromShelf = __lRecursiveSoupQuery__(self.oSoup, ['object', 'powersupply', 'name'])
        oLog.debug("getPwrSupplyAmount: list of power supplies %s" % str(lFromShelf))
        return len(__lFlattenListOfLists__(lFromShelf))


class EVA_DiskDriveClass(DASD_Class):
    def __init__(self, sID:str, sXmlData):
        """Initializes an object. Parameters: 
        sID: name of disk  (\Disk Groups\Default Disk Group\Disk 021)
        sXmlData: xml output of 'ls disk "<NAME>" xml' """
        self.sName = sID
        self.sShortName = sID.split("\\")[-1]
        oLog.debug("EVA_DriveClass.__init__: disk name is {0}".format(self.sShortName))
        # self.oSlotSoup = oDiskShelfSoup.find(name='name', string=self.sSlotName).parent
        iFirstTagPos = sXmlData.find('<') - 1
        self.oSoup = bs4.BeautifulSoup(sXmlData[iFirstTagPos:],"xml")
        # oLog.debug('EVA_DriveClass.__init__: drive soup: \n {0}'.format(self.oSoup.prettify()))
        # search for unique-id identifier
        self.sDiskUID = self.oSoup.find(name='uid').string
        oLog.debug('EVA_DriveClass.__init__: unique ID of disk: \n {0}'.format(self.sDiskUID))
        
    def getSN(self):
        sRet = "N/A"
        if self.oSoup:
            sRet = self.oSoup.find(name='serialnumber').string
        return sRet

    def getSize(self):
        iRet = 0
        if self.oSoup:
            iRet = int(self.oSoup.formattedcapacity.string) * 512 // 2**30
        return iRet

    def getModel(self):
        sRet = "N/A"
        if self.oSoup:
            sRet = self.oSoup.modelnumber.string
        return sRet

    def getType(self):
        sRet = "N/A"
        if self.oSoup:
            sRet = self.oSoup.disktype.string
        return sRet

    def getPosition(self):
        sRet = "N/A"
        if self.oSoup:
            sRet = "Shelf {0} Slot {1}".format(
                    self.oSoup.shelfnumber.string,
                    self.oSoup.diskbaynumber.string)
        return sRet

    def getRPM(self): return "N/A"
    
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
    # 
    # some testing
    # print(a.getModel())
#    print(a.getControllerNames())
#    print(a.getControllersSN())
#    print(a.getDiskShelfNames())
#    print(a.getShelvesSN())
#    print(a.getShelvesPwrSupplyAmount())
    # a.__fillListOfDisks__()
    # print(a.getPortIDs())
    # print(a.getHostPortsCount())
    # print(a.getHostPortWWNs())
    # print(a.getHostPortSpeed())
    # print (a.getComponent("Controller 2").getSN())
    # a._Close()

# vim: expandtab : softtabstop=4 : tabstop=4 : shiftwidth=4
