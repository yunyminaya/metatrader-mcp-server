"""
Remote MT5 Client — connects to a Windows machine running metatrader-http-server.

Allows Mac/Linux to use the full MCP server by proxying calls to a Windows
machine that has MetaTrader5 installed.

Usage:
    export MT5_REMOTE_URL=http://192.168.1.100:8080/api
"""
import json
import logging
import urllib.request
import urllib.parse
from typing import Dict, Any, Optional, List

logger = logging.getLogger("RemoteMT5")

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False


class RemoteAPI:
    """Low-level HTTP client for the metatrader-openapi REST API."""

    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._connected = True

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _get(self, path: str, params: dict = None) -> Any:
        url = self._url(path)
        if params:
            filtered = {k: v for k, v in params.items() if v is not None}
            url += "?" + urllib.parse.urlencode(filtered)
        if _HAS_HTTPX:
            try:
                r = httpx.get(url, timeout=self.timeout)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                return {"_error": str(e), "_success": False}
        else:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "MT5Remote/1.0"})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                return {"_error": str(e), "_success": False}

    def _post(self, path: str, data: dict = None) -> Any:
        url = self._url(path)
        body = json.dumps(data or {}).encode("utf-8")
        if _HAS_HTTPX:
            try:
                r = httpx.post(url, json=data or {}, timeout=self.timeout)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                return {"_error": str(e), "_success": False}
        else:
            try:
                req = urllib.request.Request(
                    url, data=body,
                    headers={"Content-Type": "application/json", "User-Agent": "MT5Remote/1.0"},
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                return {"_error": str(e), "_success": False}

    def _put(self, path: str, data: dict = None) -> Any:
        url = self._url(path)
        body = json.dumps(data or {}).encode("utf-8")
        if _HAS_HTTPX:
            try:
                r = httpx.put(url, json=data or {}, timeout=self.timeout)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                return {"_error": str(e), "_success": False}
        else:
            return {"_error": "PUT requires httpx", "_success": False}

    def _delete(self, path: str) -> Any:
        url = self._url(path)
        if _HAS_HTTPX:
            try:
                r = httpx.delete(url, timeout=self.timeout)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                return {"_error": str(e), "_success": False}
        else:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "MT5Remote/1.0"})
                req.method = "DELETE"
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                return {"_error": str(e), "_success": False}


class RemoteAccount:
    def __init__(self, api: RemoteAPI):
        self._api = api

    def get_account_info(self) -> Dict[str, Any]:
        return self._api._get("/accounts/info")

    def get_trade_statistics(self) -> Dict[str, Any]:
        return self._api._get("/accounts/info")


class RemoteMarket:
    def __init__(self, api: RemoteAPI):
        self._api = api

    def get_candles_latest(self, symbol_name: str, timeframe: str = "H1",
                           count: int = 100) -> Any:
        import pandas as pd
        result = self._api._get("/market/candles/latest", {
            "symbol_name": symbol_name,
            "timeframe": timeframe,
            "count": count,
        })
        if isinstance(result, list):
            try:
                return pd.DataFrame(result)
            except Exception:
                return pd.DataFrame()
        return pd.DataFrame()

    def get_candles_by_date(self, symbol_name: str, timeframe: str = "H1",
                            from_date=None, to_date=None) -> Any:
        import pandas as pd
        params = {"symbol_name": symbol_name, "timeframe": timeframe}
        if from_date:
            params["date_from"] = from_date.isoformat() if hasattr(from_date, "isoformat") else str(from_date)
        if to_date:
            params["date_to"] = to_date.isoformat() if hasattr(to_date, "isoformat") else str(to_date)
        result = self._api._get("/market/candles/date", params)
        if isinstance(result, list):
            try:
                return pd.DataFrame(result)
            except Exception:
                return pd.DataFrame()
        return pd.DataFrame()

    def get_symbol_price(self, symbol_name: str) -> Dict[str, Any]:
        return self._api._get(f"/market/price/{symbol_name}")

    def get_symbol_info(self, symbol_name: str) -> Any:
        return self._api._get(f"/market/symbol/info/{symbol_name}")

    def get_symbols(self, group: Optional[str] = None) -> List[str]:
        if group:
            result = self._api._get("/market/symbols/filter", {"group": group})
        else:
            result = self._api._get("/market/symbols")
        return result if isinstance(result, list) else []

    def get_ticks(self, symbol_name: str, count: int = 200) -> Any:
        import pandas as pd
        return pd.DataFrame()


class RemoteOrder:
    def __init__(self, api: RemoteAPI):
        self._api = api

    def place_market_order(self, symbol: str, volume: float, order_type: str,
                           stop_loss: float = 0.0, take_profit: float = 0.0) -> Dict[str, Any]:
        return self._api._post("/orders/market", {
            "symbol": symbol,
            "volume": volume,
            "type": order_type.upper(),
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        })

    def place_pending_order(self, symbol: str, volume: float, order_type: str,
                            price: float, stop_loss: float = 0.0,
                            take_profit: float = 0.0) -> Dict[str, Any]:
        return self._api._post("/orders/pending", {
            "symbol": symbol,
            "volume": volume,
            "type": order_type.upper(),
            "price": price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        })

    def get_all_positions(self) -> Any:
        import pandas as pd
        result = self._api._get("/positions")
        if isinstance(result, list):
            return pd.DataFrame(result)
        return pd.DataFrame()

    def get_positions_by_symbol(self, symbol: str) -> Any:
        import pandas as pd
        result = self._api._get(f"/positions/symbol/{symbol}")
        if isinstance(result, list):
            return pd.DataFrame(result)
        return pd.DataFrame()

    def get_positions_by_id(self, id: Any) -> Any:
        import pandas as pd
        result = self._api._get(f"/positions/{id}")
        if isinstance(result, list):
            return pd.DataFrame(result)
        return pd.DataFrame()

    def get_positions(self, symbol: Optional[str] = None, ticket: Optional[Any] = None,
                      group: Optional[str] = None) -> Any:
        import pandas as pd
        if ticket:
            result = self._api._get(f"/positions/{ticket}")
        elif symbol:
            result = self._api._get(f"/positions/symbol/{symbol}")
        else:
            result = self._api._get("/positions")
        if isinstance(result, list):
            return pd.DataFrame(result)
        return pd.DataFrame()

    def get_all_pending_orders(self) -> Any:
        import pandas as pd
        result = self._api._get("/orders/pending")
        if isinstance(result, list):
            return pd.DataFrame(result)
        return pd.DataFrame()

    def get_pending_orders_by_symbol(self, symbol: str) -> Any:
        import pandas as pd
        result = self._api._get(f"/orders/pending/symbol/{symbol}")
        if isinstance(result, list):
            return pd.DataFrame(result)
        return pd.DataFrame()

    def get_pending_orders_by_id(self, id: Any) -> Any:
        import pandas as pd
        result = self._api._get(f"/orders/pending/{id}")
        if isinstance(result, list):
            return pd.DataFrame(result)
        return pd.DataFrame()

    def close_position(self, id: Any) -> Dict[str, Any]:
        return self._api._delete(f"/positions/{id}")

    def close_all_positions(self) -> Dict[str, Any]:
        return self._api._delete("/positions")

    def close_all_positions_by_symbol(self, symbol: str) -> Dict[str, Any]:
        return self._api._delete(f"/positions/symbol/{symbol}")

    def close_all_profitable_positions(self) -> Dict[str, Any]:
        return self._api._delete("/positions/profitable")

    def close_all_losing_positions(self) -> Dict[str, Any]:
        return self._api._delete("/positions/losing")

    def cancel_pending_order(self, id: Any) -> Dict[str, Any]:
        return self._api._delete(f"/orders/pending/{id}")

    def cancel_all_pending_orders(self) -> Dict[str, Any]:
        return self._api._delete("/orders/pending")

    def cancel_pending_orders_by_symbol(self, symbol: str) -> Dict[str, Any]:
        return self._api._delete(f"/orders/pending/symbol/{symbol}")

    def modify_position(self, id: Any, stop_loss: float = None,
                        take_profit: float = None) -> Dict[str, Any]:
        data = {}
        if stop_loss is not None:
            data["stop_loss"] = stop_loss
        if take_profit is not None:
            data["take_profit"] = take_profit
        return self._api._put(f"/positions/{id}", data)

    def modify_pending_order(self, id: Any, price: float = None,
                             stop_loss: float = None,
                             take_profit: float = None) -> Dict[str, Any]:
        data = {}
        if price is not None:
            data["price"] = price
        if stop_loss is not None:
            data["stop_loss"] = stop_loss
        if take_profit is not None:
            data["take_profit"] = take_profit
        return self._api._put(f"/orders/pending/{id}", data)


class RemoteHistory:
    def __init__(self, api: RemoteAPI):
        self._api = api

    def get_deals(self, from_date=None, to_date=None,
                  symbol: Optional[str] = None) -> Any:
        import pandas as pd
        params = {}
        if from_date:
            params["from_date"] = from_date.isoformat() if hasattr(from_date, "isoformat") else str(from_date)
        if to_date:
            params["to_date"] = to_date.isoformat() if hasattr(to_date, "isoformat") else str(to_date)
        if symbol:
            params["symbol"] = symbol
        result = self._api._get("/history/deals", params)
        if isinstance(result, list):
            return pd.DataFrame(result)
        return pd.DataFrame()

    def get_deals_as_dataframe(self, from_date=None, to_date=None,
                               group: Optional[str] = None) -> Any:
        import pandas as pd
        params = {}
        if from_date:
            params["from_date"] = from_date.isoformat() if hasattr(from_date, "isoformat") else str(from_date)
        if to_date:
            params["to_date"] = to_date.isoformat() if hasattr(to_date, "isoformat") else str(to_date)
        if group:
            params["symbol"] = group
        result = self._api._get("/history/deals", params)
        if isinstance(result, list):
            return pd.DataFrame(result)
        return pd.DataFrame()

    def get_orders(self, from_date=None, to_date=None,
                   symbol: Optional[str] = None) -> Any:
        import pandas as pd
        params = {}
        if from_date:
            params["from_date"] = from_date.isoformat() if hasattr(from_date, "isoformat") else str(from_date)
        if to_date:
            params["to_date"] = to_date.isoformat() if hasattr(to_date, "isoformat") else str(to_date)
        if symbol:
            params["symbol"] = symbol
        result = self._api._get("/history/orders", params)
        if isinstance(result, list):
            return pd.DataFrame(result)
        return pd.DataFrame()

    def get_orders_as_dataframe(self, from_date=None, to_date=None,
                                group: Optional[str] = None) -> Any:
        import pandas as pd
        params = {}
        if from_date:
            params["from_date"] = from_date.isoformat() if hasattr(from_date, "isoformat") else str(from_date)
        if to_date:
            params["to_date"] = to_date.isoformat() if hasattr(to_date, "isoformat") else str(to_date)
        if group:
            params["symbol"] = group
        result = self._api._get("/history/orders", params)
        if isinstance(result, list):
            return pd.DataFrame(result)
        return pd.DataFrame()


class RemoteMT5Client:
    """Remote client compatible with MT5Client interface.

    Proxies all calls to a Windows machine running metatrader-http-server.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        config = config or {}
        remote_url = config.get("remote_url", "")
        self._api = RemoteAPI(remote_url)
        self.account = RemoteAccount(self._api)
        self.market = RemoteMarket(self._api)
        self.order = RemoteOrder(self._api)
        self.history = RemoteHistory(self._api)
        self._connected = False

    def connect(self) -> bool:
        try:
            info = self.account.get_account_info()
            if info and not info.get("_error"):
                self._connected = True
                logger.info(f"Connected to remote MT5 server")
                return True
            return False
        except Exception as e:
            logger.error(f"Remote connection failed: {e}")
            return False

    def disconnect(self) -> bool:
        self._connected = False
        return True

    def is_connected(self) -> bool:
        return self._connected

    def get_terminal_info(self) -> Dict[str, Any]:
        return {"platform": "remote", "connected": self._connected}

    def get_version(self) -> tuple:
        return (5, 0, 0, 0)

    def last_error(self) -> tuple:
        return (0, "no_error")
