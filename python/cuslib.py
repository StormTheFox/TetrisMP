import sys, datetime

sm = staticmethod ## too lazy for 'staticmethod'

class Log:
    _DEFAULT_OUTPUT = sys.stdout
    _ver = '0.4'
    _esc = '\x1b'
    _fgc = '38;2'
    _fc = f"{_esc}[{_fgc};"
    _reset = f"{_esc}[0m"
    _mf = "[{} | {}] {}"
    
    class Parse:
        @sm
        def info(msg: str, timestamp: bool = True) -> str: 
            """See `Log.info`"""
            if timestamp:
                ts = datetime.datetime.now().strftime("%Y.%m.%d %H:%M:%S")
                msg = Log._mf.format(ts, 'I', msg)
            r = f'{Log._fc}128;128;255m{msg}{Log._reset}'
            return r

        @sm
        def error(msg: str, timestamp: bool = True) -> str:
            """See `Log.error`"""
            if timestamp:
                ts = datetime.datetime.now().strftime("%Y.%m.%d %H:%M:%S")
                msg = Log._mf.format(ts, 'E', msg)
            return f'{Log._fc}255;0;0m{msg}{Log._reset}'

        @sm
        def warning(msg: str, timestamp: bool = True) -> str:
            """See `Log.warning`"""
            if timestamp:
                ts = datetime.datetime.now().strftime("%Y.%m.%d %H:%M:%S")
                msg = Log._mf.format(ts, 'W', msg)
            return f'{Log._fc}255;255;0m{msg}{Log._reset}'

        @sm
        def debug(msg: str, timestamp: bool = True) -> str:
            '''See `Log.debug`'''
            if timestamp:
                ts = datetime.datetime.now().strftime("%Y.%m.%d %H:%M:%S")
                msg = Log._mf.format(ts, 'D', msg)
            return f"{Log._fc}0;255;0m{msg}{Log._reset}"

    @staticmethod
    def info(msg: str, timestamp: bool = True, out = _DEFAULT_OUTPUT):
        """for useful information
        ```
        import pygame
        
        Log.info('import finished')
        ```
        """
        print(Log.Parse.info(msg, timestamp), file = out)

    @staticmethod
    def error(msg: str, timestamp: bool = True, out = _DEFAULT_OUTPUT):
        """for unexpected behavior of script
        ```
        def _(call:telebot.types.CallbackQuery):
            if call.data: ...
            else: Log.error(f"call.data is empty in '_' function. call:\\n{call}")
        ```
        """
        print(Log.Parse.error(msg, timestamp), file = out)
    
    @staticmethod
    def warning(msg: str, timestamp: bool = True, out = _DEFAULT_OUTPUT) -> None:
        """Usualy it used in `try..except` blocks:
        ```
        try: 0/0
        except ZeroDivisionError: Log.warning("u divided 0 by 0, that's not good")
        ```
        Or for other possible problems
        ```
        def EitherFirstOrSecond(_1: bool = True, _2 = False):
            if _1 and _2:
                Log.warning("U turned on both parameters! Dont do that!")
                _2 = False
                ...
        ```
        """
        print(Log.Parse.warning(msg, timestamp), file = out)
    
    @staticmethod
    def debug(msg: str, timestamp: bool = True, out = _DEFAULT_OUTPUT) -> None:
        """For debugging ur spagetti code. Don't use it for logging/warnings (I said so)"""
        print(Log.Parse.debug(msg, timestamp), file = out)

    def __init__(self, RR: int = 255, GG: int = 255, BB: int = 255):
        """Ur awesome tag
        
        WARNING: if ur value of rr, gg, or bb will be greater than 255, it'll change to 255 (same to lover than 0 will became 0)

        Args:
            RR (int, optional): How much red (0 - 255). Defaults to 255.
            GG (int, optional): How much green (0 - 255). Defaults to 255.
            BB (int, optional): How much blue (0 - 255). Defaults to 255.
        """
        RR = max(0, min(255, RR))
        GG = max(0, min(255, GG))
        BB = max(0, min(255, BB))
        
        self.MSG = f"{Log._fc}{RR};{GG};{BB}m{{}}{Log._reset}"
    
    def __call__(self, msg: str, type: str = 'CUSTOM', timestamp: bool = True, out = _DEFAULT_OUTPUT):
        if timestamp:
            ts = datetime.datetime.now().strftime("%Y.%m.%d %H:%M:%S")
            msg = Log._mf.format(ts, type, msg)
        print(self.MSG.format(msg), file = out)
    
    @staticmethod
    def _test():
        Log.error("Test error")
        Log.warning("Test warning")
        Log.debug("Test log")
        Log.info("Test info")
        Log()("Test custom (default)")
        Log(255, 128, 0)("Test custom (orange)")
        Log(255, 0, 255)("Test custom (pink)")

if __name__ == "__main__": Log._test()
