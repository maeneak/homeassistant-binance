from datetime import timedelta
import logging

from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException
import voluptuous as vol

from homeassistant.const import CONF_API_KEY, CONF_NAME
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.discovery import load_platform
from homeassistant.util import Throttle

__version__ = "1.0.1"
REQUIREMENTS = ["python-binance==1.0.10"]

DOMAIN = "binance"

DEFAULT_NAME = "Binance"
DEFAULT_DOMAIN = "us"
DEFAULT_CURRENCY = "USD"
CONF_API_SECRET = "api_secret"
CONF_BALANCES = "balances"
CONF_EXCHANGES = "exchanges"
CONF_DOMAIN = "domain"
CONF_NATIVE_CURRENCY = "native_currency"

SCAN_INTERVAL = timedelta(minutes=1)
MIN_TIME_BETWEEN_UPDATES = timedelta(minutes=1)

DATA_BINANCE = "binance_cache"

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
                vol.Optional(CONF_DOMAIN, default=DEFAULT_DOMAIN): cv.string,
                vol.Optional(CONF_NATIVE_CURRENCY, default=DEFAULT_CURRENCY): cv.string,
                vol.Required(CONF_API_KEY): cv.string,
                vol.Required(CONF_API_SECRET): cv.string,
                vol.Optional(CONF_BALANCES, default=[]): vol.All(
                    cv.ensure_list, [cv.string]
                ),
                vol.Optional(CONF_EXCHANGES, default=[]): vol.All(
                    cv.ensure_list, [cv.string]
                ),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


def setup(hass, config):
    api_key = config[DOMAIN][CONF_API_KEY]
    api_secret = config[DOMAIN][CONF_API_SECRET]
    name = config[DOMAIN].get(CONF_NAME)
    balances = config[DOMAIN].get(CONF_BALANCES)
    tickers = config[DOMAIN].get(CONF_EXCHANGES)
    native_currency = config[DOMAIN].get(CONF_NATIVE_CURRENCY).upper()
    tld = config[DOMAIN].get(CONF_DOMAIN)

    hass.data[DATA_BINANCE] = binance_data = BinanceData(api_key, api_secret, tld)

    if not hasattr(binance_data, "balances"):
        pass
    else:
        for balance in binance_data.balances:
            if not balances or balance["asset"] in balances:
                balance["name"] = name
                balance["native"] = native_currency
                load_platform(hass, "sensor", DOMAIN, balance, config)

    if not hasattr(binance_data, "tickers"):
        pass
    else:
        for ticker in binance_data.tickers:
            if not tickers or ticker["symbol"] in tickers:
                ticker["name"] = name
                load_platform(hass, "sensor", DOMAIN, ticker, config)

    load_platform(hass, "sensor", DOMAIN, binance_data.spot_balance, config)

    return True


class BinanceData:
    def __init__(self, api_key, api_secret, tld):
        """Initialize."""
        self.client = Client(api_key, api_secret, tld=tld)
        self.balances = []
        self.spot_balance = 0.0
        self.tickers = {}
        self.tld = tld
        self.update()

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def update(self):
        _LOGGER.debug(f"Fetching data from binance.{self.tld}")
        try:
            account_info = self.client.get_account()
            ticker_info = self.client.get_ticker()
            balances = account_info.get("balances", [])
            if balances:
                self.balances = balances
                _LOGGER.debug(f"Balances updated from binance.{self.tld}")

            prices = self.client.get_all_tickers()
            if prices:
                self.tickers = prices
                _LOGGER.debug(f"Exchange rates updated from binance.{self.tld}")

            self.spot_balance = self.async_spotbalance(account_info, ticker_info)

        except (BinanceAPIException, BinanceRequestException) as e:
            _LOGGER.error(f"Error fetching data from binance.{self.tld}: {e.message}")
            return False

    def async_spotbalance(self, account, tickers) -> float:
        balances = {
            b["asset"]: float(b["free"]) + float(b["locked"])
            for b in account["balances"]
        }
        in_btc = self.getTickerMapIn("BTC", tickers)
        in_bnb = self.getTickerMapIn("BNB", tickers)
        in_usdt = self.getTickerMapIn("USDT", tickers)
        btc_usdt = 0.0
        for ticker in tickers:
            if ticker["symbol"] == "BTCUSDT":
                btc_usdt = float(ticker["lastPrice"])

        # Calculate balances in BTC, and 24-hour % changes.
        balances_in_btc = {}
        for k, v in balances.items():
            if k == "BTC":
                balances_in_btc[k] = {"$": v}
            elif k == "USDT":
                balances_in_btc[k] = {"$": v * (1 / in_usdt["BTC"]["$"])}
            elif in_btc.get(k):
                balances_in_btc[k] = {"$": v * in_btc[k]["$"]}
            elif not in_btc.get(k) and in_bnb.get(k):
                balances_in_btc[k] = {"$": v * in_bnb[k]["$"] * in_btc["BNB"]["$"]}

        relevant_balances_in_btc = {
            k: v for k, v in balances_in_btc.items() if v["$"] > 0.0001
        }
        return round(
            sum(d["$"] for d in relevant_balances_in_btc.values() if d) * btc_usdt, 2
        )


    def getTickerMapIn(self, symbol, tickers):
        return {
            t["symbol"][: -len(symbol)]: {
                "$": float(t["lastPrice"]),
                "%": float(t["priceChangePercent"]),
            }
            for t in tickers
            if t["symbol"][-len(symbol) :] == symbol
        }
