#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2011 yinhm

import datetime
import numpy as np
import pandas as pd

from pandas import DataFrame
from pandas import TimeSeries
from pandas import DatetimeIndex


class Dividend(object):
    def __init__(self, div):
        """
        Paramaters:
          div: numpy dividend data.
        """
        assert div['time'] > 0
        assert abs(div['split']) > 0 or \
               abs(div['purchase']) > 0 or \
               abs(div['dividend']) > 0

        self._npd = div

    def adjust(self, frame):
        '''Adjust price, volume of quotes data.
    
        Paramaters
        ----------
        frame: DataFrame of OHLCs.
        '''
        if self.ex_date <= frame.index[0].date():  # no adjustment needed
            return True

        if self.ex_date > datetime.date.today():  # not mature
            return True

        self._divide(frame)
        self._split(frame)

    def _divide(self, frame):
        """divided close price to adjclose column

        WARNING
        =======
        frame should be chronological ordered otherwise wrong backfill.
        """
        if self.cash_afterward == 0:
            return

        cashes = [self.cash_afterward, 0.0]
        adj_day = self.ex_date - datetime.timedelta(days=1)
        indexes = []
        indexes.append(self.d2t(adj_day))
        indexes.append(self.d2t(datetime.date.today()))

        cashes = TimeSeries(cashes, index=indexes)
        ri_cashes = cashes.reindex(frame.index, method='backfill')

        frame['adjclose'] = frame['adjclose'] - ri_cashes

    def _split(self, frame):
        if self.share_afterward == 1:
            return

        splits = [self.share_afterward, 1.0]
        adj_day = self.ex_date - datetime.timedelta(days=1)
        indexes = []
        indexes.append(self.d2t(adj_day))
        indexes.append(self.d2t(datetime.date.today()))

        splits = TimeSeries(splits, index=indexes)
        ri_splits = splits.reindex(frame.index, method='backfill')

        frame['adjclose'] = frame['adjclose'] / ri_splits

    @property
    def ex_date(self):
        return datetime.date.fromtimestamp(self._npd['time'])

    @property
    def cash_afterward(self):
        return self._npd['dividend'] - self._npd['purchase'] * self._npd['purchase_price']

    @property
    def share_afterward(self):
        return 1 + self._npd['purchase'] + self._npd['split']

    def d2t(self, date):
        return datetime.datetime.combine(date, datetime.time())


def adjust(y, divs, capitalize=False):
    """Return fully adjusted OHLCs data base on dividends

    Paramaters:
    y: numpy
    divs: numpy of dividends

    Return:
    DataFrame objects
    """
    # 从通达信中取出来的已经做过处理了
    # index = DatetimeIndex([datetime.datetime.fromtimestamp(v) for v in y['time']])
    # y = DataFrame.from_records(y, index=index, exclude=['time'])
    y['adjclose'] = y['close']

    for div in divs:
        if div['split'] + div['purchase'] + div['dividend'] == 0:
            continue
        d = Dividend(div)
        d.adjust(y)

    factor = y['adjclose'] / y['close']
    frame = y.copy()
    frame['open'] = frame['open'] * factor
    frame['high'] = frame['high'] * factor
    frame['low'] = frame['low'] * factor
    frame['close'] = frame['close'] * factor
    frame['volume'] = frame['volume'] * (1 / factor)

    if capitalize:
        columns = [k.capitalize() for k in frame.columns]
        columns[-1] = 'Adjusted'
        frame.columns = columns
        del (frame['Amount'])
    return frame


def datetime_2_long(dt):
    t = dt.timetuple()
    return float((t.tm_year*10000 + t.tm_mon*100 + t.tm_mday)*10000L)


def sort_dividend(divs):
    if len(divs) > 0:
        df = DataFrame(divs)
        df = df.sort_values(by='time')

        df.time = df.time.apply(lambda x: pd.datetime.utcfromtimestamp(x))
        df = df.set_index('time')

    return df


# 计算得到向后复权因子
# 发现这种算法没有错，但很多股票还是有一些与万得对应不上
# 除权应子的算法应当是 交易所会发布前收盘价与收盘价进行比较就是除权因子
# 但上交所网站上前收盘价并不好查，因为按分类，有些还是不好做
# 可以对照一下通达信与万得的行情，哪种价格对应得上
def factor(daily, divs):
    # 排序复权因子
    df = sort_dividend(divs)

    # 过滤一下，用来计算除权价
    daily_part = daily[['time','time', 'close']]

    # 无语，停牌会选不出来，比如说SZ000001，会有日期对应不上,所以只能先合并然后再处理
    daily_div = pd.merge(daily_part, df, how='outer', left_index=True, right_index=True, sort=False)
    # 由于可能出现在停牌期公布除权除息，所以需要补上除权那天的收盘价
    daily_div['close'] = daily_div['close'].fillna(method='pad', limit=1)
    daily_div = daily_div.shift(1)
    daily_div[['split', 'purchase', 'purchase_price', 'dividend']] = daily_div[['split', 'purchase', 'purchase_price', 'dividend']].fillna(method='bfill', limit=1)

    # 预处理后只取需要的部分
    df = daily_div.loc[df.index]
    # 注意：换了两个列名
    df.columns = ['time', 'pre_day', 'pre_close', 'split', 'purchase', 'purchase_price', 'dividend']

     # 除权价
    df['dr_pre_close'] = (df['pre_close'] - df['dividend'] + df['purchase'] * df['purchase_price']) / (
    1 + df['split'] + df['purchase'])
    # 要做一次四舍五入,不然除权因子不对,2是不是不够，需要用到3呢？
    df['dr_pre_close'] = df['dr_pre_close'].apply(lambda x: round(x, 2))
    # 除权因子
    df['dr_factor'] = df['pre_close'] / df['dr_pre_close']

    # 在最前插件一条特殊的记录，用于表示在第一次除权之前系数为1
    # 由于不知道上市是哪一天，只好用最小日期
    first_ = pd.DataFrame({'dr_factor': 1}, index=[pd.datetime(1900, 1, 1)])
    df = df.append(first_)
    df = df.sort_index()

    df['time'] = df.index
    df['time'] = df['time'].apply(datetime_2_long)

    # 向后复权因子，注意对除权因子的累乘
    df['backward_factor'] = df['dr_factor'].cumprod()
    # 向前复权因子
    df['forward_factor'] = df['backward_factor'] / float(df['backward_factor'][-1:])

    df = df[['time', 'pre_day', 'pre_close',
             'split', 'purchase', 'purchase_price', 'dividend',
             'dr_pre_close', 'dr_factor', 'backward_factor', 'forward_factor']]

    return df


# 通过复权因子列表计算价格,注意因子列表最好是完整的
def adjust_with_factor(y, f, start_index_datetime):

    insert_ = pd.DataFrame({'dr_factor': 1}, index=[start_index_datetime])
    try:
        f = f.append(insert_, verify_integrity=True)
        f = f.sort_index()
    except ValueError:
        pass

    # 向后复权因子
    f['backward_factor'] = f['dr_factor'].cumprod()
    # 向前复权因子
    f['forward_factor'] = f['backward_factor'] / float(f['backward_factor'][-1:])

    # 只取这两列进行后面的操作，因为其它部分可能是nan，fillna后再dropna可能将数据全删了
    f2 = f[['backward_factor', 'forward_factor']]

    # 注意，日线与分钟线合并，由于时间点不对应，只能使用outer方法，然后看情况删除nan
    df = pd.merge(y, f2, how='outer', left_index=True, right_index=True, sort=True)

    # 只对两种复权因子进行填充
    df[['backward_factor', 'forward_factor']] = df[['backward_factor', 'forward_factor']].fillna(method='pad')

    df = df.dropna()

    return df

if __name__ == '__main__':
    from datafeed.providers.dzh import *
    from datafeed.dividend import *
    from datafeed.providers.tdx import *

    io = DzhDividend(r'D:\dzh2\Download\PWR\full.PWR')
    r = io.read()

    tdx_day = tdx_read(r'D:\new_hbzq\vipdoc\sh\lday\sh600000.day')

    for data in r:
        symbol = data[0]
        if symbol != 'SH600000':
            continue

        print symbol
        divs = data[1]
        df = factor(tdx_day, divs)

        #a = adjust_with_factor(y, df, y.index[0])

        break