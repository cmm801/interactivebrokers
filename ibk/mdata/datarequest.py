import datetime
import time
import copy
import numpy as np
import pandas as pd
import pytz
import tempfile
import warnings

from abc import ABC, abstractmethod
from collections import Iterable

import xml.etree.ElementTree as ET

from ibapi.contract import Contract

import ibk.constants
import ibk.helper
import ibk.requestmanager

# Default arguments
DEFAULT_USE_RTH = False

# The number of rows that the market scanner returns by default
DEFAULT_NUMBER_OF_SCAN_ROWS = 50

# The tick data code used to obtain fundamental data in a MarketDataRequest
FUNDAMENTAL_TICK_DATA_CODE = '47'

# What bar size defines a 'small bar' (in seconds)
SMALL_BAR_CUTOFF_SIZE = 30


def create_market_data_request(self, contractList, is_snapshot, fields=""):
    """ Create a MarketDataRequest object for getting  current market data.

        Arguments:
            contractList: (list) a list of Contract objects
            is_snapshot: (bool) if False, then a stream will be opened with 
                IB that will continuously update the latest market price info.
                If True, then only the latest price info will be returned.
            fields: (str) additional tick data codes that are requested,
                in additional to the default data codes. A list of available
                additional tick data codes are available on the IB website.
    """
    _args = [MarketDataRequest, contractList, is_snapshot]
    _kwargs = dict(fields=fields)
    return self._create_data_request(*_args, **_kwargs)

def create_historical_data_request(self, contractList, is_snapshot, frequency,
                                   use_rth=DEFAULT_USE_RTH, data_type="TRADES",
                                   start="", end="", duration=""):
    """ Create a HistoricalDataRequest object for getting historical data.
    """         
    _args = [HistoricalDataRequest, contractList, is_snapshot]
    _kwargs = dict(frequency=frequency, start=start, end=end, duration=duration,
                                            use_rth=use_rth, data_type=data_type)
    return self._create_data_request(*_args, **_kwargs)

def create_streaming_bar_data_request(self, contractList, frequency='5s', 
                                      use_rth=DEFAULT_USE_RTH, data_type="TRADES"):
    is_snapshot = False
    _args = [StreamingBarRequest, contractList, is_snapshot]
    _kwargs = dict(frequency=frequency, use_rth=use_rth, data_type=data_type)
    return self._create_data_request(*_args, **_kwargs)

def create_streaming_tick_data_request(self, contractList, data_type="Last",
                                    number_of_ticks=1000, ignore_size=True):
    is_snapshot = False
    _args = [StreamingTickDataRequest, contractList, is_snapshot]
    _kwargs = dict(data_type=data_type, number_of_ticks=number_of_ticks, ignore_size=ignore_size)
    return self._create_data_request(*_args, **_kwargs)

def create_historical_tick_data_request(self, contractList, use_rth=DEFAULT_USE_RTH, data_type="TRADES",
                                   start="", end="", number_of_ticks=1000):
    is_snapshot = True
    _args = [HistoricalTickDataRequest, contractList, is_snapshot]
    _kwargs = dict(data_type=data_type, start=start, end=end, use_rth=use_rth,
                                            number_of_ticks=number_of_ticks)
    return self._create_data_request(*_args, **_kwargs)

def create_first_date_request(self, contractList, use_rth=DEFAULT_USE_RTH, data_type='TRADES'):
    is_snapshot = True
    _args = [HeadTimeStampDataRequest, contractList, is_snapshot]
    _kwargs = dict(use_rth=use_rth, data_type=data_type)
    return self._create_data_request(*_args, **_kwargs)

def create_fundamental_data_request(self, contractList, report_type, options=None):
    if report_type == "ratios":
        # If the user specifies to get fundamental ratios, use 
        #    the MarketDataRequest object with fields == FUNDAMENTAL_TICK_DATA_CODE
        is_snapshot = False
        _args = [MarketDataRequest, contractList, is_snapshot]
        _kwargs = dict(fields=FUNDAMENTAL_TICK_DATA_CODE)
        return self._create_data_request(*_args, **_kwargs)
    else:
        return FundamentalDataRequest(self, contractList, is_snapshot=True,
                              report_type=report_type, options=options)

def create_scanner_data_request(self, scanSubObj, options=None, filters=None):
    return ScannerDataRequest(self, scanSubObj, is_snapshot=False, 
                              options=options, filters=filters)

def get_scanner_parameters(self, max_wait_time=2):
    self._xml_params = None
    self.reqScannerParameters()

    # Sleep until the client returns a response
    t0 = time.time()
    while self._xml_params is None and t0 < time.time() + max_wait_time:
        time.sleep(0.1)

    # Save the XML to a text file so that ElementTree can access the data
    with tempfile.NamedTemporaryFile(mode='w', suffix='.xml') as tmp_file:
        tmp_file.writelines(self._xml_params)
        tmp_file.seek(0)

        # Use the ElementTree to read in the XML
        tree = ET.parse(tmp_file.name)

    # Parse the data into dict of dicts by going through branches
    root = tree.getroot()
    root_dict = {}
    for group in root:
        root_dict[group.tag] = {}
        for instrument in group:
            if instrument.tag not in root_dict[group.tag]:
                root_dict[group.tag][instrument.tag] = []

            entry = {}
            for child in instrument:
                entry[child.tag] = child.text
            root_dict[group.tag][instrument.tag].append(entry)

    # Return the parsed data
    return root_dict

def _create_data_request(self, cls, contractList, is_snapshot, **kwargs):
    # Make sure arguments are not included in kwargs
    kwargs.pop('contractList', None)
    kwargs.pop('is_snapshot', None)

    # Create a request object for eqch contract
    requestObjList = []
    for contract in contractList:
        request_obj = cls(self, contract, is_snapshot, **kwargs)
        requestObjList.append(request_obj)
    return requestObjList


class AbstractDataRequest(ABC):
    def __init__(self, parent_app, dataObj, is_snapshot, **kwargs):
        self.parent_app = parent_app
        self.is_snapshot = is_snapshot
        self.dataObj = dataObj
        
        self.status = ibk.requestmanager.STATUS_REQUEST_NEW
        self.reset()

    def reset(self):
        """ Reset a request to its initial state.
        
            Active or Queued requests must first be cancelled before
            one can call reset(). Otherwise, an exception will be raised
            for Active/Queued requests on which reset() is called.
        """
        self._reset_attributes()

        # Call method to split into valid subrequests if necessary
        sub_reqs = self._split_into_valid_subrequests()
        self.subrequests = sub_reqs

    def _reset_attributes(self):
        if self.status in (ibk.requestmanager.STATUS_REQUEST_ACTIVE, 
                           ibk.requestmanager.STATUS_REQUEST_QUEUED):
            raise ValueError('Active or Queued requests must be cancelled before they can be reset.')
        else:
            self.status = ibk.requestmanager.STATUS_REQUEST_NEW
            self.__subrequests = None
            self.__is_request_complete = False
            self.set_req_ids(None)
            self.initialize_data()

    @abstractmethod
    def initialize_data(self):
        pass

    @abstractmethod
    def append_data(self, new_data):
        pass

    @abstractmethod
    def _request_data(self):
        pass

    @abstractmethod
    def get_data(self):
        pass

    @abstractmethod
    def restriction_class(self):
        pass

    @property
    def request_manager(self):
        return self.parent_app.request_manager

    @property
    def subrequests(self):
        return self.__subrequests

    @subrequests.setter
    def subrequests(self, sub_vals):
        self.__subrequests = sub_vals

    def is_valid_request(self):
        is_valid, msg = True, ""
        return is_valid, msg

    def is_request_complete(self):
        if not self.__is_request_complete:
            req_ids = self.get_req_ids()
            self.__is_request_complete = all([self.request_manager.is_request_complete(req_id) \
                                                  for req_id in req_ids])
        return self.__is_request_complete

    def get_req_ids(self):
        if all([req_id is None for req_id in self.__req_ids]):
            if len(self.subrequests) > 1:
                self.__req_ids = [s.get_req_ids()[0] for s in self.subrequests]
        return self.__req_ids

    def set_req_ids(self, req_ids):
        if not isinstance(req_ids, Iterable):
            self.__req_ids = [req_ids]
        else:
            self.__req_ids = req_ids

    def place_request(self, priority=0):
        """ Place a request with the RequestManager.
        
            Arguments:
                priority: (float) indicates the relative priority with which the request
                will be processed, compared to other requests in the queue. The requests
                with the lowest priority are processed first.
        """
        if self.status != ibk.requestmanager.STATUS_REQUEST_NEW:
            raise ValueError(f'Only new requests can be placed. This request has status "{self.status}."')
        elif len(self.subrequests) > 1:
            [reqObj.place_request() for reqObj in self.subrequests]
        else:
            self.status = ibk.requestmanager.STATUS_REQUEST_SENT_TO_REQUEST_MANAGER
            self.request_manager.place_request(self, priority=priority)

    def cancel_request(self):
        """ This method implements the functionality necessary to cancel a request. """
        pass

    def copy(self):
        return copy.copy(self)

    def _split_into_valid_subrequests(self):
        """Split a request that is too large to be processed by IB.
           The default version (implemented here) is to not split any requests.
           Subclasses may need to override this with more sophisticated logic.

           Returns:
           split_request_objects (list): a list of valid request objects, which can
                   be combined to provide the data implicit in the original object.
        """
        if self.subrequests is None:
            self.subrequests = [self]
        return self.subrequests

    def __lt__(self, other):
        return False

    def __le__(self, other):
        return self == other

    def __gt__(self, other):
        return False

    def __ge__(self, other):
        return self == other
    

class AbstractDataRequestForContract(AbstractDataRequest):
    """ Overload the AbstractDataRequest object to work with Contract objects.
    """
    @property
    def contract(self):
        return self.dataObj
    
    @contract.setter
    def contract(self, ct):
        if not isinstance(ct, ibapi.contract.Contract):
            raise ValueError(f'Expected a Contract object, but received a "{ct.__class__}".')
        else:
            self.dataObj = ct


class ScannerDataRequest(AbstractDataRequest):
    def __init__(self, parent_app, dataObj, is_snapshot, options=None, filters=None):
        super(ScannerDataRequest, self).__init__(parent_app, dataObj, is_snapshot)
        self.options = options
        self.filters = filters

    @property
    def scanSubObj(self):
        return self.dataObj
    
    @scanSubObj.setter
    def scanSubObj(self, scan_obj):
        self.dataObj = scan_obj

    @property
    def n_rows(self):
        if self.scanSubObj.numberOfRows == -1:
            return DEFAULT_NUMBER_OF_SCAN_ROWS
        else:
            return self.scanSubObj.numberOfRows

    # abstractmethod
    def initialize_data(self):
        """ Create a list with one element for each ranked instrument.
        """
        self._market_data = [{} for _ in range(self.n_rows)]

    # abstractmethod
    def append_data(self, new_data):
        self._market_data[new_data['rank']] = new_data

    # abstractmethod
    def _request_data(self, req_id):
        # Assemble the arguments for the request
        args = dict(reqId=req_id,
                    subscription=self.scanSubObj,
                    scannerSubscriptionOptions=self.options,
                    scannerSubscriptionFilterOptions=self.filters)
            
        # Make the scanner subscription request
        self.parent_app.reqScannerSubscription(**args)
    
    # abstractmethod
    def get_data(self):
        return self._market_data

    # abstractmethod
    @property
    def restriction_class(self):
        return ibk.requestmanager.RESTRICTION_CLASS_SCANNER
        
    def cancel_request(self):
        """ Method to cancel the scanner subscription.
        """
        for req_id in self.get_req_ids():
            self.parent_app.cancelScannerSubscription(req_id)


class MarketDataRequest(AbstractDataRequestForContract):
    def __init__(self, parent_app, contract, is_snapshot, fields=""):
        super(MarketDataRequest, self).__init__(parent_app, contract, is_snapshot)
        self.fields = fields

    @property
    def is_fundamental_data_request(self):
        return self.fields == FUNDAMENTAL_TICK_DATA_CODE
        
    # abstractmethod
    def initialize_data(self):
        self.__market_data = dict()

    # abstractmethod
    def append_data(self, new_data):
        # Save the data
        self.__market_data.update(new_data)
        
        # If it is a fundamental data request, we can close the stream and request
        if self.is_fundamental_data_request and 'FUNDAMENTAL_RATIOS' in new_data:
            # Register the request as Completed once we get the fundamental data
            for req_id in self.get_req_ids():
                self.status = ibk.requestmanager.STATUS_REQUEST_COMPLETE
                self.request_manager.register_request_complete(req_id)

            # Close the stream
            self.cancel_request()

    # abstractmethod
    def _request_data(self, req_id):
        self.parent_app.reqMktData(reqId=req_id,
                                   contract=self.contract,
                                   genericTickList=self.fields,
                                   snapshot=self.is_snapshot,
                                   regulatorySnapshot=False,
                                   mktDataOptions=[])
    # abstractmethod
    def get_data(self):
        assert len(self.get_req_ids()) == 1, 'Market Data Requests should not have to be split.'
        if self.is_fundamental_data_request:
            # If this is a fundamental data request, then we must parse the data
            raw_data = self._parse_fundamental_data()
            return pd.Series(raw_data, name=self.contract.localSymbol)
        else:
            return self.__market_data

    # abstractmethod
    @property
    def restriction_class(self):
        return ibk.requestmanager.RESTRICTION_CLASS_MKT_DATA_LINES

    def cancel_request(self):
        for req_id in self.get_req_ids():
            self.parent_app.cancelMktData(req_id)

    def _parse_fundamental_data(self):
        """ Parse the fundamental data that is returned.
        """
        date_cols = ['LATESTADATE']
        string_cols = ['CURRENCY']

        data = dict()
        
        # Get the raw data that has been returned by IB
        raw_data = self.__market_data.get('FUNDAMENTAL_RATIOS', None)
        if raw_data is not None:
            # Parse any fundamental data that has been returned
            for _item in raw_data.split(';'):
                if _item:
                    k, v = _item.split('=')
                    if k in string_cols:
                        data[k] = v
                    elif k in date_cols:
                        try:
                            data[k] = pd.Timestamp(v)
                        except:
                            data[k] = np.datetime64('NaT')
                    elif v == "-99999.99":
                        data[k] = np.nan
                    elif v == '':
                        data[k] = np.nan
                    else:
                        data[k] = float(v)

        # Return the parsed fundamental data
        return data


class FundamentalDataRequest(AbstractDataRequestForContract):
    def __init__(self, parent_app, contract, is_snapshot, report_type="", options=None):
        assert is_snapshot, 'Fundamental Data is not available as a streaming service.'
        super(FundamentalDataRequest, self).__init__(parent_app, contract, is_snapshot)
        self.report_type = report_type
        self.options = options

    # abstractmethod
    def initialize_data(self):
        self.__market_data = None

    # abstractmethod
    def append_data(self, new_data):
        assert self.__market_data is None, 'Only expected a single update.'
        self.__market_data = new_data

    # abstractmethod
    def _request_data(self, req_id):
        self.parent_app.reqFundamentalData(reqId=req_id,
                                           contract=self.contract,
                                           reportType=self.report_type,
                                           fundamentalDataOptions=self.options)
    # abstractmethod
    def get_data(self):
        assert len(self.get_req_ids()) == 1, 'Market Data Requests should not have to be split.'
        return self.__market_data

    # abstractmethod
    @property
    def restriction_class(self):
        return ibk.requestmanager.RESTRICTION_CLASS_FUNDAMENTAL


class HistoricalDataRequest(AbstractDataRequestForContract):
    def __init__(self, parent_app, contract, is_snapshot, frequency="",
                                 start="", end="",
                                 duration="", use_rth=DEFAULT_USE_RTH, data_type='TRADES'):
        # Initialize some private variables
        self._start = self._end = None
        
        # Save the input variables
        self.start = start
        self.end = end
        self.duration = duration             # e.g., 1s, 1M (1 minute), 1d, 1h, etc.
        self.frequency = frequency           # e.g., 1s, 1M (1 minute), 1d, 1h, etc.
        self.useRTH = use_rth               # True/False - only return regular trading hours
        self.data_type = data_type           # TRADES, ASK, BID, ASK_BID, etc.
        
        # Call the superclass contructor
        super(HistoricalDataRequest, self).__init__(parent_app, contract, is_snapshot)

    @property
    def start(self):
        return self._start
    
    @start.setter
    def start(self, d):
        if d is None or not d:
            self._start = ''
        else:
            dt = ibk.helper.convert_to_datetime(d, tz_name=ibk.constants.TIMEZONE_TWS)
            self._start = dt.replace(tzinfo=None)

    @property
    def end(self):
        return self._end
    
    @end.setter
    def end(self, d):
        if d is None or not d:
            self._end = ''
        else:
            dt = ibk.helper.convert_to_datetime(d, tz_name=ibk.constants.TIMEZONE_TWS)
            self._end = dt.replace(tzinfo=None)

    @property
    def endDateTime(self):
        if not self.end:
            return ''
        else:
            return ibk.helper.convert_datetime_to_tws_date(self.end)

    # abstractmethod
    def initialize_data(self):
        self.__market_data = []

    # abstractmethod
    def append_data(self, new_data):
        self.__market_data.append(new_data)

    def update_data(self, new_data):
        """Only works for single request objects, and is used for handling streaming updates.
           If the new row has the same date as the previously received row, then replace it.
           Otherwise, just append the new data as normal.
       """
        if self.__market_data and new_data['date'] == self.__market_data[-1]['date']:
            self.__market_data[-1] = new_data
        else:
            self.__market_data.append(new_data)

    # abstractmethod
    def _request_data(self, req_id):
        self.parent_app.reqHistoricalData(req_id,
                                          contract=self.contract,
                                          endDateTime=self.endDateTime,
                                          durationStr=self.durationStr(),
                                          barSizeSetting=self.barSizeSetting(),
                                          whatToShow=self.data_type,
                                          useRTH=self.useRTH,
                                          formatDate=1,  # 1 corresponds to string format
                                          keepUpToDate=self.keepUpToDate(),
                                          chartOptions=[])
    # abstractmethod
    def get_data(self):
        if len(self.get_req_ids()) == 1:
            return [self.__market_data]
        else:
            return [s.get_data()[0] for s in self.subrequests]

    # abstractmethod
    @property
    def restriction_class(self):
        bar_size = ibk.helper.TimeHelper(self.frequency, 'frequency').total_seconds()
        if SMALL_BAR_CUTOFF_SIZE >= bar_size:
            return ibk.requestmanager.RESTRICTION_CLASS_HISTORICAL_LF
        else:
            return ibk.requestmanager.RESTRICTION_CLASS_HISTORICAL_HF

    def get_dataframe(self, timestamp=False):
        """ Get a DataFrame with the time series data returned from the request.
        
            Arguments:
                timestamp: (bool) whether to return the index in timestamp format (True)
                    or as datetime objects (False).
        """
        df = self._get_dataframe_from_raw_data()

        # Remove observations outside of the range
        est_datetimes = pd.DatetimeIndex(df.date).to_pydatetime()
        if self.get_start_tws() is not None and self.get_end_tws() is not None:
            start_time = self.get_start_tws().replace(tzinfo=None)
            end_time = self.get_end_tws().replace(tzinfo=None)
            rows_to_keep = (start_time <= est_datetimes) & (est_datetimes <= end_time)
            df = df.iloc[rows_to_keep]
            est_datetimes = est_datetimes[rows_to_keep]

        if timestamp:
            # Add a UTC timestamp index
            est_tz = pytz.timezone(ibk.constants.TIMEZONE_EST)
            utc_tz = pytz.utc
            utc_datetimes = [est_tz.localize(d).astimezone(utc_tz) for d in est_datetimes]
            utc_timestamps = [d.timestamp() for d in utc_datetimes]
            df.index = pd.Index(utc_timestamps, name='utc_timestamp')
        else:
            df.set_index('date', inplace=True)
            df.index = pd.DatetimeIndex(df.index)

        return df

    def is_valid_request(self):
        is_valid, msg = True, ""
        if not self.is_snapshot:
            if self.end:
                is_valid = False
                msg = 'End date cannot be specified for streaming historical data requests.'
            #elif 5 > ibk.helper.TimeHelper(self.frequency, 'frequency').total_seconds():
                #is_valid = False
                #msg = 'Bar frequency for streaming historical data requests must be >= 5 seconds.'

        return is_valid, msg

    def _split_into_valid_subrequests(self):
        # Find the timedelta between start and end dates
        if self.subrequests is None:
            start_tws = self.get_start_tws()
            end_tws = self.get_end_tws()
            if start_tws is None:
                # If 'start' is not specified, then we just use 'duration' and 'end'
                warnings.warn('This request may be invalid. Need to add tests that duration is valid.')
                return [self]
            else:
                delta = end_tws - start_tws
                # Split the period into multiple valid periods if necessary
                valid_periods = self._split_into_valid_periods(start_tws, end_tws)
                if len(valid_periods) == 1:
                    return [self]
                else:
                    requestObjList = []
                    for period in valid_periods:
                        period_start, period_end = period
                        requestObj = self.copy()
                        requestObj._reset_attributes()
                        requestObj.subrequests = [requestObj]
                        requestObj.start = ibk.helper.convert_datetime_to_tws_date(period_start, ibk.constants.TIMEZONE_TWS)
                        requestObj.end = ibk.helper.convert_datetime_to_tws_date(period_end, ibk.constants.TIMEZONE_TWS)
                        requestObj.duration = ""
                        requestObjList.append(requestObj)
                    return requestObjList

    def keepUpToDate(self):
        return not self.is_snapshot

    def durationStr(self):
        if self.start and self.duration:
            raise ValueError('Duration and start cannot both be specified.')
        elif self.duration:
            return ibk.helper.TimeHelper(self.duration, time_type='frequency').to_tws_durationStr()
        elif self.start:
            # Get a TimeHelper object corresponding to the interval btwn start/end dates
            start_tws, end_tws = self.get_start_tws(), self.get_end_tws()
            delta = end_tws - start_tws
            return ibk.helper.TimeHelper.from_timedelta(delta).get_min_tws_duration()
        else:
            return ""

    def barSizeSetting(self):
        if self.frequency:
            return ibk.helper.TimeHelper(self.frequency, time_type='frequency').to_tws_barSizeSetting()
        else:
            return ""

    def get_start_tws(self):
        if self.start:
            s = ibk.helper.convert_to_datetime(self.start, tz_name=ibk.constants.TIMEZONE_TWS)
            if s.tzinfo is None:
                tzinfo = pytz.timezone(ibk.constants.TIMEZONE_TWS)
                s = tzinfo.localize(s)
            return s
        else:
            return None

    def get_end_tws(self):
        if self.end:
            ET = self.end
        else:
            end_utc = pytz.utc.localize(datetime.datetime.utcnow())
            tws_tzone = pytz.timezone(ibk.constants.TIMEZONE_TWS)
            ET = end_utc.astimezone(tws_tzone)
        
        d = ibk.helper.convert_to_datetime(ET, tz_name=ibk.constants.TIMEZONE_TWS)
        if d.tzinfo is None:
            tzinfo = pytz.timezone(ibk.constants.TIMEZONE_TWS)
            d = tzinfo.localize(d)
        return d

    def cancel_request(self):
        for req_id in self.get_req_ids():
            self.parent_app.cancelHistoricalData(req_id)

    def _get_period_end(self, _start, _delta):
        if _delta.total_seconds() >= (3600 * 24):
            # For daily frequency, TWS defines the days to begin and end at 18:00
            if _start.hour < 18:
                new_date = datetime.datetime.combine(_start.date(), datetime.time(18,0))
            else:
                next_date = _start.date() + _delta
                new_date = datetime.datetime.combine(next_date, datetime.time(18,0))
            # Keep the previous time zone information
            return _start.tzinfo.localize(new_date)
        else:
            return _start + _delta

    def _is_duration_daily_frequency_or_lower(self, _delta):
        th = ibk.helper.TimeHelper.from_timedelta(_delta)
        dur = ibk.helper.TimeHelper(th.get_min_tws_duration(), 'duration')
        return dur.total_seconds() / dur.n >= 24 * 3600

    def _split_into_valid_periods(self, start_tws, end_tws):
        bar_freq = ibk.helper.TimeHelper(self.frequency, time_type='frequency')
        delta = end_tws - start_tws

        if bar_freq.units == 'days':
            # TWS convention seems to be that days begin and end at 18:00 EST
            tz_info = pytz.timezone(ibk.constants.TIMEZONE_TWS)
            start_tws = tz_info.localize(datetime.datetime.combine(start_tws.date() - datetime.timedelta(days=1),
                                                  datetime.time(18,0)))
            end_tws = tz_info.localize(datetime.datetime.combine(end_tws.date(), datetime.time(18,0)))


        assert delta.total_seconds() > 0, 'Start time must precede end time.'
        max_delta = bar_freq.get_max_tws_duration_timedelta()
        period_start = start_tws
        periods = []
        while self._get_period_end(period_start, max_delta) < end_tws:
            period_end = min(end_tws, self._get_period_end(period_start, max_delta))
            periods.append((period_start, period_end))
            period_start = period_end
        if period_start < end_tws:
            periods.append((period_start, end_tws))
        return periods

    def _get_dataframe_from_raw_data(self):
        """ Turn the requested data into a dataframe.
        """
        df = pd.DataFrame()
        for d in self.get_data():
            df = pd.concat([df, pd.DataFrame(d)])

        df.sort_values('date', inplace=True)
        df.reset_index(drop=True, inplace=True)
        df.drop_duplicates(inplace=True)

        if self.data_type in ['BID', 'ASK']:
            df = df.drop(['average', 'barCount', 'volume'], axis=1)

            idx = np.zeros((df.shape[0],), dtype=bool)
            idx[0] = True
            vals = df.drop('date', axis=1).to_numpy()
            # Only keep rows where something has changed
            idx[1:] = np.any(vals[1:] != vals[:-1], axis=1)
        elif self.data_type == 'TRADES':
            idx = np.zeros((df.shape[0],), dtype=bool)
            idx[0] = True
            # Only keep rows with a non-zero volume (e.g., a trade occurred in this bar)
            idx[1:] = (df.volume.values[1:] != 0)
        else:
            raise NotImplementedError('Not implemented for data type {}'.format(self.data_type))
        return df[idx]


class StreamingBarRequest(AbstractDataRequestForContract):
    def __init__(self, parent_app, contract, is_snapshot, data_type="TRADES", 
                 use_rth=DEFAULT_USE_RTH, frequency='5s'):
        assert not is_snapshot, 'Streaming requests must have is_snapshot == False.'
        super(StreamingBarRequest, self).__init__(parent_app, contract, is_snapshot)
        self.frequency = frequency
        self.data_type = data_type
        self.useRTH = use_rth

    # abstractmethod
    def initialize_data(self):
        self.__market_data = []

    # abstractmethod
    def append_data(self, new_data):
        self.__market_data.append(new_data)

    # abstractmethod
    def _request_data(self, req_id):
        self.parent_app.reqRealTimeBars(
                                        req_id,
                                        contract=self.contract,
                                        barSize=self.barSizeInSeconds(),
                                        whatToShow=self.data_type,
                                        useRTH=self.useRTH,
                                        realTimeBarsOptions=[]
                                       )

    # abstractmethod
    def get_data(self):
        assert len(self.get_req_ids()) == 1, 'Streaming Tick Data Requests should not have to be split.'
        return self.__market_data

    # abstractmethod
    @property
    def restriction_class(self):
        bar_size = ibk.helper.TimeHelper(self.frequency, 'frequency').total_seconds()
        if SMALL_BAR_CUTOFF_SIZE >= bar_size:
            return ibk.requestmanager.RESTRICTION_CLASS_HISTORICAL_LF
        else:
            return ibk.requestmanager.RESTRICTION_CLASS_HISTORICAL_HF

    def get_dataframe(self):
        cols = ['time', 'price', 'size']
        prices = [{c: d[c] for c in cols} for d in self.get_data()]
        df = pd.DataFrame.from_dict(prices)
        df.rename({'time': 'local_time'}, inplace=True)
        df.set_index('local_time', inplace=True)
        return df

    def barSizeInSeconds(self):
        if self.frequency:
            return int(ibk.helper.TimeHelper(self.frequency, time_type='frequency').total_seconds())
        else:
            return -1

    def cancel_request(self):
        for req_id in self.get_req_ids():
            self.parent_app.cancelRealTimeBars(req_id)


class StreamingTickDataRequest(AbstractDataRequestForContract):
    """ Create a streaming tick data request.
    
        Arguments:
            data_type: (str) allowed values are  "Last", "AllLast", "BidAsk" or "MidPoint"
    """
    def __init__(self, parent_app, contract, is_snapshot, data_type="Last",
                                     number_of_ticks=0, ignore_size=True):
        assert not is_snapshot, 'A Streaming tick request must have is_snapshot == False.'
        super(StreamingTickDataRequest, self).__init__(parent_app, contract, is_snapshot)
        self.tickType = data_type
        self.numberOfTicks = number_of_ticks
        self.ignoreSize = ignore_size     # Ignore ticks with just size updates (no price chg.)

    # abstractmethod
    def initialize_data(self):
        self.__market_data = []

    # abstractmethod
    def append_data(self, new_data):
        self.__market_data.append(new_data)

    # abstractmethod
    def _request_data(self, req_id):
        self.parent_app.reqTickByTickData(
                                           req_id,
                                           contract=self.contract,
                                           tickType=self.tickType,
                                           numberOfTicks=self.numberOfTicks,
                                           ignoreSize=self.ignoreSize
                                        )

    # abstractmethod
    def get_data(self):
        assert len(self.get_req_ids()) == 1, 'Streaming Tick Data Requests should not have to be split.'
        return self.__market_data

    # abstractmethod
    @property
    def restriction_class(self):
        return ibk.requestmanager.RESTRICTION_CLASS_TICK_DATA

    @property
    def data_type(self):
        if self.tickType in ("Last", "AllLast"):
            return 'LAST'
        elif self.tickType == 'BidAsk':
            return 'BID_ASK'
        elif self.tickType == 'MidPoint':
            return 'MIDPOINT'
        else:
            raise ValueError(f'Unknown tick type: "{self.tickType}".')
        
    def get_dataframe(self):
        cols = ['time', 'price', 'size']
        prices = [{c: d[c] for c in cols} for d in self.get_data()]
        df = pd.DataFrame.from_dict(prices)
        df.set_index('time', inplace=True)
        return df

    def cancel_request(self):
        for req_id in self.get_req_ids():
            self.parent_app.cancelTickByTickData(req_id)


class HistoricalTickDataRequest(AbstractDataRequestForContract):
    """ Create a historical tick data request.
    
        Arguments:
            data_type: (str) allowed values are 'BID_ASK', 'MIDPOINT', 'TRADES'
    """
    def __init__(self, parent_app, contract, is_snapshot, start="", end="", use_rth=DEFAULT_USE_RTH,
                                 data_type="TRADES", number_of_ticks=1000, ignore_size=True):
        super(HistoricalTickDataRequest, self).__init__(parent_app, contract, is_snapshot)
        
        # Initialize private variables
        self._start = self._end = None

        #
        self.start = start
        self.end = end
        self.whatToShow = data_type
        self.numberOfTicks = number_of_ticks
        self.useRTH = use_rth
        self.ignoreSize = ignore_size   # Ignore ticks with just size updates (no price chg.)

    @property
    def start(self):
        return self._start
    
    @start.setter
    def start(self, d):
        if d is None or not d:
            self._start = ''
        else:
            dt = ibk.helper.convert_to_datetime(d)
            self._start = dt.replace(tzinfo=None)

    @property
    def end(self):
        return self._end
    
    @end.setter
    def end(self, d):
        if d is None or not d:
            self._end = ''
        else:
            dt = ibk.helper.convert_to_datetime(d)
            self._end = dt.replace(tzinfo=None)

    @property
    def startDateTime(self):
        if not self.start:
            return ''
        else:
            return ibk.helper.convert_datetime_to_tws_date(self.start)

    @property
    def endDateTime(self):
        if not self.end:
            return ''
        else:
            return ibk.helper.convert_datetime_to_tws_date(self.end)

    # abstractmethod
    def initialize_data(self):
        self.__market_data = []

    # abstractmethod
    def append_data(self, new_data):
        self.__market_data.extend(new_data)

    # abstractmethod
    def _request_data(self, req_id):
        self.parent_app.reqHistoricalTicks(req_id,
                                           contract=self.contract,
                                           startDateTime=self.startDateTime,
                                           endDateTime=self.endDateTime,
                                           numberOfTicks=self.numberOfTicks,
                                           whatToShow=self.whatToShow,
                                           useRth=self.useRTH,
                                           ignoreSize=self.ignoreSize,
                                           miscOptions=[])

    # abstractmethod
    def get_data(self):
        assert len(self.get_req_ids()) == 1, 'Historical Tick Data Requests should not have to be split.'
        return self.__market_data

    @property
    def data_type(self):
        return self.whatToShow.upper()

    # abstractmethod
    @property
    def restriction_class(self):
        return ibk.requestmanager.RESTRICTION_CLASS_HISTORICAL_HF

    def get_dataframe(self):
        cols = ['time', 'price', 'size']
        prices = [{c: d.__getattribute__(c) for c in cols} for d in self.get_data()]
        df = pd.DataFrame.from_dict(prices)
        df.set_index('time', inplace=True)
        return df

    def cancel_request(self):
        for req_id in self.get_req_ids():
            self.parent_app.cancelTickByTickData(req_id)


class HeadTimeStampDataRequest(AbstractDataRequestForContract):
    def __init__(self, parent_app, contract, is_snapshot=True, data_type='TRADES', use_rth=DEFAULT_USE_RTH):
        self.useRTH = use_rth               # True/False - only return regular trading hours
        self.data_type = data_type           # TRADES, ASK, BID, ASK_BID, etc.
        super(HeadTimeStampDataRequest, self).__init__(parent_app, contract, is_snapshot)

    # abstractmethod
    def initialize_data(self):
        self.__market_data = None

    # abstractmethod
    def append_data(self, new_data):
        dt = ibk.helper.convert_datestr_to_datetime(new_data)
        self.__market_data = dt.replace(tzinfo=None)

    # abstractmethod
    def _request_data(self, req_id):
        assert self.is_snapshot, 'HeadTimeStamp is only available for non-streaming data requests.'
        self.parent_app.reqHeadTimeStamp(req_id,
                                         contract=self.contract,
                                         whatToShow=self.data_type,
                                         useRTH=self.useRTH,
                                         formatDate=1)  # 1 corresponds to string format

    # abstractmethod
    def get_data(self):
        return self.__market_data

    # abstractmethod
    @property
    def restriction_class(self):
        return ibk.requestmanager.RESTRICTION_CLASS_NONE

