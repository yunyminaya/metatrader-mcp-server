import pandas as pd
from datetime import datetime
from typing import Optional
import MetaTrader5 as mt5
from ..exceptions import MarketDataError, SymbolNotFoundError
from .get_symbols import get_symbols


def get_ticks_latest(connection, symbol_name: str, count: int = 100) -> pd.DataFrame:
    if not get_symbols(connection, symbol_name):
        raise SymbolNotFoundError(f"Symbol '{symbol_name}' not found")
    ticks = mt5.copy_ticks_from(symbol_name, datetime.now(), count)
    if ticks is None or len(ticks) == 0:
        raise MarketDataError(f"Failed to retrieve ticks for '{symbol_name}'")
    df = pd.DataFrame(ticks)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    df['time_msc'] = pd.to_datetime(df['time_msc'], unit='ms', utc=True)
    df = df.sort_values('time', ascending=False)
    return df


def get_ticks_range(connection, symbol_name: str, from_date: datetime, to_date: Optional[datetime] = None, count: int = 100000) -> pd.DataFrame:
    if not get_symbols(connection, symbol_name):
        raise SymbolNotFoundError(f"Symbol '{symbol_name}' not found")
    if to_date is None:
        to_date = datetime.now()
    ticks = mt5.copy_ticks_range(symbol_name, from_date, to_date)
    if ticks is None or len(ticks) == 0:
        raise MarketDataError(f"Failed to retrieve ticks for '{symbol_name}' in date range")
    df = pd.DataFrame(ticks)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    df['time_msc'] = pd.to_datetime(df['time_msc'], unit='ms', utc=True)
    return df.sort_values('time', ascending=False).head(count)


def get_market_depth(connection, symbol_name: str, depth: int = 10) -> dict:
    if not get_symbols(connection, symbol_name):
        raise SymbolNotFoundError(f"Symbol '{symbol_name}' not found")
    if not mt5.market_book_add(symbol_name):
        raise MarketDataError(f"Failed to add market book for '{symbol_name}'")
    book = mt5.market_book_get(symbol_name)
    mt5.market_book_release(symbol_name)
    if book is None:
        raise MarketDataError(f"No market depth data for '{symbol_name}'")
    bids = [{'price': b.price, 'volume': b.volume, 'volume_dbl': b.volume_dbl} for b in book if b.type == 1][:depth]
    asks = [{'price': b.price, 'volume': b.volume, 'volume_dbl': b.volume_dbl} for b in book if b.type == 2][:depth]
    bid_volume = sum(b['volume_dbl'] for b in bids)
    ask_volume = sum(a['volume_dbl'] for a in asks)
    imbalance = (bid_volume - ask_volume) / (bid_volume + ask_volume + 0.0001) if (bid_volume + ask_volume) > 0 else 0
    return {
        "bids": bids,
        "asks": asks,
        "bid_volume": round(bid_volume, 2),
        "ask_volume": round(ask_volume, 2),
        "imbalance": round(imbalance, 4),
        "depth": len(bids) + len(asks),
    }
