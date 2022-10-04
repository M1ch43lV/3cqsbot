import argparse
import asyncio
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from time import time

import numpy as np
import portalocker
import requests
import yfinance as yf
from babel.dates import format_timedelta
from babel.numbers import format_currency
from numpy import true_divide
from py3cw.request import Py3CW
from telethon import TelegramClient, events
from tenacity import retry, wait_fixed

from config import Config
from logger import Logger, NotificationHandler
from multibot import MultiBot
from signals import Signals
from singlebot import SingleBot

######################################################
#                       Config                       #
######################################################

program = Path(__file__).stem

# Parse and interpret options
parser = argparse.ArgumentParser(description="3CQSBot bringing 3CQS signals to 3Commas")

parser.add_argument("-d", "--datadir", help="data directory to use", type=str)
args = parser.parse_args()
if args.datadir:
    datadir = args.datadir
else:
    datadir = os.getcwd()

# load configuration file
attributes = Config(datadir, program)

# Handle timezone
if hasattr(time, "tzset"):
    os.environ["TZ"] = attributes.get("timezone", "Europe/Berlin")
    time.tzset()

# Init notification handler
notification = NotificationHandler(
    program,
    attributes.get("notifications", False),
    attributes.get("notify-urls", []),
)

# Initialise logging
logging = Logger(
    datadir,
    program,
    notification,
    attributes.get("logrotate", 7),
    attributes.get("debug", False),
    attributes.get("notifications", False),
)

logging.info(f"Loaded configuration from '{datadir}/{program}.ini' or config.ini")

######################################################
#                        Init                        #
######################################################

# Initialize 3Commas API client
p3cw = Py3CW(
    key=attributes.get("key"),
    secret=attributes.get("secret"),
    request_options={
        "request_timeout": attributes.get("timeout", 3),
        "nr_of_retries": attributes.get("retries", 5),
        "retry_backoff_factor": attributes.get("delay_between_retries", 2.0),
    },
)

# Initialize Telegram API client
client = TelegramClient(
    attributes.get("sessionfile", "tgsesssion"),
    attributes.get("api_id"),
    attributes.get("api_hash"),
)

# Initialize global variables
asyncState = type("", (), {})()
asyncState.bot_active = True
asyncState.first_topcoin_call = True
asyncState.fgi = -1
asyncState.fgi_downtrend = False
asyncState.fgi_drop = False
asyncState.fgi_allows_trading = True
asyncState.fgi_time_until_update = 1
asyncState.dca_conf = "dcabot"
asyncState.chatid = ""
asyncState.fh = 0
asyncState.account_data = {}
asyncState.pair_data = []
asyncState.symrank_success = False
asyncState.symrank_retry = 60
asyncState.multibot = {}
asyncState.pairs_volume = []
asyncState.receive_signals = (
    False  # start processing 3cqs signals after async routines are working
)
asyncState.start_time = 0
asyncState.start_signals_24h = 0
asyncState.start_signals = 0
asyncState.start_signals_bot_enabled_24h = 0
asyncState.start_signals_bot_enabled = 0
asyncState.start_signals_not_tradeable_24h = 0
asyncState.start_signals_not_tradeable = 0
asyncState.start_signals_symrank_filter_passed_24h = 0
asyncState.start_signals_symrank_filter_passed = 0
asyncState.start_signals_topcoin_filter_passed_24h = 0
asyncState.start_signals_topcoin_filter_passed = 0
asyncState.stop_signals_24h = 0
asyncState.stop_signals = 0

######################################################
#                     Methods                        #
######################################################


def single_instance_check():
    asyncState.fh = open(os.path.realpath(__file__), "r")
    try:
        portalocker.lock(asyncState.fh, portalocker.LOCK_EX | portalocker.LOCK_NB)
    except:
        sys.exit(
            "Another 3CQSBot is already running in this directory - please use another one!"
        )


def parse_tg(raw_text):
    return raw_text.split("\n")


def tg_data(text_lines):

    # Make sure the message is a signal
    if len(text_lines) == 7:
        data = {}
        signal = text_lines[1]
        token = text_lines[2].replace("#", "")
        action = text_lines[3].replace("BOT_", "")
        volatility_score = text_lines[4].replace("Volatility Score ", "")

        if volatility_score == "N/A":
            volatility_score = 9999999

        priceaction_score = text_lines[5].replace("Price Action Score ", "")

        if priceaction_score == "N/A":
            priceaction_score = 9999999

        symrank = text_lines[6].replace("SymRank #", "")

        if symrank == "N/A":
            symrank = 9999999

        if signal == "SymRank Top 10":
            signal = "top10"
        elif signal == "SymRank Top 30":
            signal = "top30"
        elif signal == "SymRank Top 50":
            signal = "top50"
        elif signal == "SymRank Top 100 Triple Tracker":
            signal = "triple100"
        elif signal == "SymRank Top 100 Quadruple Tracker":
            signal = "quadruple100"
        elif signal == "SymRank Top 250 Quadruple Tracker":
            signal = "quadruple250"
        elif signal == "Super Volatility":
            signal = "svol"
        elif signal == "Super Volatility Double Tracker":
            signal = "svoldouble"
        elif signal == "Hyper Volatility":
            signal = "hvol"
        elif signal == "Hyper Volatility Double Tracker":
            signal = "hvoldouble"
        elif signal == "Ultra Volatility":
            signal = "uvol"
        elif signal == "X-Treme Volatility":
            signal = "xvol"

        data = {
            "signal": signal,
            "pair": attributes.get("market") + "_" + token,
            "action": action,
            "volatility": float(volatility_score),
            "price_action": float(priceaction_score),
            "symrank": int(symrank),
        }
    # Symrank list
    elif len(text_lines) == 17:
        pairs = {}
        data = []

        if "Volatile" not in text_lines[0]:
            for row in text_lines:
                if ". " in row:
                    # Sort the pair list from Telegram
                    line = re.split(" +", row)
                    pairs.update(
                        {int(line[0][:-1]): line[1], int(line[2][:-1]): line[3]}
                    )

            allpairs = dict(sorted(pairs.items()))
            data = list(allpairs.values())
    # too many requests or other commands
    else:
        data = False

    return data


def bot_data():

    # Gets information about existing bots in 3Commas
    botlimit = attributes.get("system_bot_value", 300)
    pages = math.ceil(botlimit / 100)
    bots = []

    for page in range(1, pages + 1):
        if page == 1:
            offset = 0
        else:
            offset = (page - 1) * 100

        error, data = p3cw.request(
            entity="bots",
            action="",
            additional_headers={"Forced-Mode": attributes.get("trade_mode")},
            payload={"limit": 100, "offset": offset},
        )

        if error:
            sys.exit("function bot_data: " + error["msg"])
        else:
            if data:
                bots += data
            else:
                break

    return bots


def account_data():

    # Gets information about the used 3commas account (paper or real)
    account = {}

    error, data = p3cw.request(
        entity="accounts",
        action="",
        additional_headers={"Forced-Mode": attributes.get("trade_mode")},
    )

    if error:
        logging.error("function account_data: " + error["msg"])
        sys.tracebacklimit = 0
        sys.exit("Problem fetching account data from 3commas api - stopping!")
    else:
        for accounts in data:
            if accounts["name"] == attributes.get("account_name"):
                account.update({"id": str(accounts["id"])})
                account.update({"market_code": str(accounts["market_code"])})

        if "id" not in account:
            sys.tracebacklimit = 0
            sys.exit(
                "Account with name '" + attributes.get("account_name") + "' not found"
            )

    return account


async def pair_data(account, interval_sec):
    more_inform = attributes.get("extensive_notifications", False)
    while True:
        try:
            pairs = []
            asyncState.pair_data = []

            error, data = p3cw.request(
                entity="accounts",
                action="market_pairs",
                additional_headers={"Forced-Mode": attributes.get("trade_mode")},
                payload={"market_code": account["market_code"]},
            )

            if error:
                logging.error("function pair_data: " + error["msg"])
                sys.tracebacklimit = 0
                sys.exit("Problem fetching pair data from 3commas api - stopping!")

            error, blacklist_data = p3cw.request(
                entity="bots", action="pairs_black_list"
            )

            if error:
                logging.error("function pair_data: " + error["msg"])
                sys.tracebacklimit = 0
                sys.exit(
                    "Problem fetching pairs blacklist data from 3commas api - stopping!"
                )

            for pair in data:
                if attributes.get("market") in pair:
                    if (
                        pair not in attributes.get("token_denylist", [])
                        and pair not in blacklist_data["pairs"]
                    ):
                        pairs.append(pair)

            asyncState.pair_data = pairs
            logging.info(
                str(len(pairs))
                + " tradeable and non-blacklisted "
                + attributes.get("market")
                + " pairs for account '"
                + account["id"]
                + "' on '"
                + account["market_code"]
                + "' imported. Next update in approx. "
                + format_timedelta(interval_sec, locale="en_US"),
                more_inform,
            )
            notification.send_notification()
            await asyncio.sleep(interval_sec)
        except Exception as err:
            logging.error(f"Exception raised by async pair_data: {err}")
            await asyncio.sleep(interval_sec)


# Credits go to @M1ch43l
# Adjust DCA settings dynamically according to social sentiment: greed = aggressive DCA, neutral = moderate DCA, fear = conservative DCA
@retry(wait=wait_fixed(10))
def requests_call(method, url, timeout):
    response = []
    try:
        response = requests.request(method, url, timeout=timeout)
    except Exception as e:
        raise IOError(
            "Fear and greed index API actually down, retrying in 10s, Error is:" + e
        )
    return response


async def get_fgi(ema_fast, ema_slow):

    if attributes.get("fgi_pulse", False) and attributes.get("fgi_trading", False):
        logging.info(
            "********** Initialising FGI Pulse with FGI adapted DCA settings **********",
            True,
        )
    elif attributes.get("fgi_pulse", False):
        logging.info(
            "********** Initialising FGI Pulse only, without FGI adapted DCA settings **********",
            True,
        )
    elif attributes.get("fgi_trading", False):
        logging.info(
            "********** Initialising FGI adapted DCA settings **********",
            True,
        )

    logging.info(
        "Using crypto fear and greed index (FGI) from alternative.me for changing 3cqsbot DCA settings to defensive, moderate or aggressive",
        True,
    )

    while True:
        try:
            url = "https://api.alternative.me/fng/?limit=100"
            fgi_values = []
            fgi_ema_fast = []
            fgi_ema_slow = []
            response = requests_call("GET", url, 5)
            raw_data = json.loads(response.text)
            for i in range(len(raw_data["data"])):
                fgi_values.insert(0, int(raw_data["data"][i]["value"]))
            fgi_ema_fast = ema(fgi_values, ema_fast)
            fgi_ema_slow = ema(fgi_values, ema_slow)
            time_until_update = int(raw_data["data"][0]["time_until_update"])
            fmt = "{0.hours}h:{0.minutes}m:{0.seconds}s"
            # Web response sometimes slow, so proceed only if time_until_update for next web update > 10 sec
            if time_until_update < 0:
                time_until_update = 10
            elif time_until_update > 10:
                logging.info(
                    f"Current FGI: {fgi_values[-1]}"
                    + " - time till next update: "
                    + format_timedelta(time_until_update, locale="en_US"),
                    True,
                )
                asyncState.fgi = fgi_values[-1]
                # calculate EMA crosses if fgi_pulse == True
                if attributes.get("fgi_pulse", False):
                    if fgi_ema_fast[-1] < fgi_ema_slow[-1]:
                        asyncState.fgi_downtrend = True
                        asyncState.fgi_allows_trading = False
                        output_str = "FGI-EMA{0:d}: {1:.1f}".format(
                            ema_fast, fgi_ema_fast[-1]
                        ) + " less than FGI-EMA{:d}: {:.1f}".format(
                            ema_slow, fgi_ema_slow[-1]
                        )
                        if round(fgi_ema_fast[-1], 1) < round(fgi_ema_fast[-2], 1):
                            logging.info(
                                "FGI downtrending ↘️ - "
                                + output_str
                                + " - Fast EMA falling ↘️ compared to yesterday"
                                + " ("
                                + str(round(fgi_ema_fast[-2], 1))
                                + ") - trading not allowed",
                                True,
                            )
                        else:
                            logging.info(
                                "FGI still in the downtrend zone ↘️ - "
                                + output_str
                                + " - however fast EMA equal or rising ↗️ compared to yesterday"
                                " ("
                                + str(round(fgi_ema_fast[-2], 1))
                                + ") - trading still not allowed",
                                True,
                            )
                    else:
                        asyncState.fgi_downtrend = False
                        output_str = "FGI-EMA{0:d}: {1:.1f}".format(
                            ema_fast, fgi_ema_fast[-1]
                        ) + " greater than FGI-EMA{:d}: {:.1f}".format(
                            ema_slow, fgi_ema_slow[-1]
                        )
                        if round(fgi_ema_fast[-1], 1) < round(fgi_ema_fast[-2], 1):
                            logging.info(
                                "FGI still in the uptrend zone ↗️ - "
                                + output_str
                                + "  - however fast EMA falling ↘️ compared to yesterday"
                                " (" + str(round(fgi_ema_fast[-2], 1)) + ")",
                                True,
                            )
                        else:
                            logging.info(
                                "FGI uptrending ↗️ - "
                                + output_str
                                + "  - Fast EMA equal or rising ↗️ compared to yesterday"
                                " (" + str(round(fgi_ema_fast[-2], 1)) + ")",
                                True,
                            )

                    # FGI downtrend = true if FGI drops >= 10 between actual and last day
                    # OR >= 15 between actual and second to last day
                    if ((fgi_values[-2] - fgi_values[-1]) >= 10) or (
                        (fgi_values[-3] - fgi_values[-1]) >= 15
                    ):
                        asyncState.fgi_drop = True
                        asyncState.fgi_allows_trading = False
                        logging.info(
                            f"FGI actual/yesterday/before yesterday: {fgi_values[-1]}/{fgi_values[-2]}/{fgi_values[-3]}",
                            True,
                        )
                        logging.info(
                            "⬇️ Drop > 10 between actual vs. yesterday or drop > 15 between actual vs. before yesterday. Drop to large, trading not allowed for today! ⬇️",
                            True,
                        )
                    else:
                        asyncState.fgi_drop = False

                    if (
                        not asyncState.fgi_allows_trading
                        and not asyncState.fgi_downtrend
                        and not asyncState.fgi_drop
                    ):
                        if asyncState.fgi >= attributes.get(
                            "fgi_trade_min", 0
                        ) and asyncState.fgi <= attributes.get("fgi_trade_max", 100):
                            logging.info(
                                "FGI inside allowed trading range ["
                                + str(attributes.get("fgi_trade_min", 0))
                                + ".."
                                + str(attributes.get("fgi_trade_max", 100))
                                + "] - trading allowed",
                                True,
                            )
                            asyncState.fgi_allows_trading = True
                        elif asyncState.fgi < attributes.get(
                            "fgi_trade_min", 0
                        ) or asyncState.fgi > attributes.get("fgi_trade_max", 100):
                            logging.info(
                                "FGI uptrending but outside the allowed trading range ["
                                + str(attributes.get("fgi_trade_min", 0))
                                + ".."
                                + str(attributes.get("fgi_trade_max", 100))
                                + "] - trading not allowed",
                                True,
                            )

                    logging.debug(
                        "FGI downtrending: '" + str(asyncState.fgi_downtrend) + "'"
                    )
                    logging.debug("FGI drop: '" + str(asyncState.fgi_drop) + "'")
                    logging.debug(
                        "FGI allows trading: '"
                        + str(asyncState.fgi_allows_trading)
                        + "'"
                    )

                asyncState.fgi_time_until_update = time_until_update

            notification.send_notification()
            # request FGI once per day, because is is calculated only once per day
            await asyncio.sleep(time_until_update)
        except Exception as err:
            logging.error(f"Exception raised by async get_fgi: {err}")
            await asyncio.sleep(3600)


# Credits goes to @IamtheOnewhoKnocks from
# https://discord.gg/tradealts
def ema(data, period, smoothing=2):
    # Calculate EMA without dependency for TA-Lib
    ema = [sum(data[:period]) / period]

    for price in data[period:]:
        ema.append(
            (price * (smoothing / (1 + period)))
            + ema[-1] * (1 - (smoothing / (1 + period)))
        )

    for i in range(period - 1):
        ema.insert(0, np.nan)

    return ema


# Credits goes to @IamtheOnewhoKnocks from
# https://discord.gg/tradealts
@retry(wait=wait_fixed(2))
def btctechnical(symbol):
    btcusdt = yf.download(tickers=symbol, period="6h", interval="5m", progress=False)
    if len(btcusdt) > 0:
        btcusdt = btcusdt.iloc[:, :5]
        btcusdt.columns = ["Time", "Open", "High", "Low", "Close"]
        btcusdt = btcusdt.astype(float)
        btcusdt["EMA9"] = ema(btcusdt["Close"], 9)
        btcusdt["EMA50"] = ema(btcusdt["Close"], 50)
        btcusdt["per_5mins"] = (np.log(btcusdt["Close"].pct_change() + 1)) * 100
        btcusdt["percentchange_15mins"] = (
            np.log(btcusdt["Close"].pct_change(3) + 1)
        ) * 100
    else:
        raise IOError("Downloading YFinance chart broken, retry....")

    return btcusdt


# Credits goes to @IamtheOnewhoKnocks from
# https://discord.gg/tradealts
async def get_btcpulse(interval_sec):

    logging.info("********** Initialising BTC Pulse **********", True)
    i = round(3600 / interval_sec, 0)
    while True:
        try:
            ## in debug mode inform every hour on TG and after first start
            if i >= round(3600 / interval_sec, 0) and attributes.get("debug", False):
                TG_inform = True
                i = 0
            else:
                TG_inform = False
            logging.debug(
                "btc-pulse: counter i (3600/ sleep interval_sec): "
                + str(i)
                + "   TG_inform: "
                + str(TG_inform)
                + "   interval_sec: "
                + str(interval_sec)
            )

            btcusdt = btctechnical("BTC-USD")
            # if EMA 50 > EMA9 or <-1% drop then the sleep mode is activated
            # else bool is false and while loop is broken
            if (
                btcusdt.percentchange_15mins[-1] < -1
                or btcusdt.EMA9[-1] < btcusdt.EMA50[-1]
            ):
                # after 5mins getting the latest BTC data to see if it has had a sharp rise in previous 5 mins
                logging.info(
                    "BTC drop more than -1% within 15 min  or  5min EMA9 < EMA50. Waiting for confirmation in "
                    + format_timedelta(interval_sec, locale="en_US")
                )
                await asyncio.sleep(interval_sec)
                i += 1
                btcusdt = btctechnical("BTC-USD")

                # this is the golden cross check fast moving EMA
                # cuts slow moving EMA from bottom, if that is true then bool=false and break while loop
                if (
                    btcusdt.EMA9[-1] > btcusdt.EMA50[-1]
                    and btcusdt.EMA9[-2] < btcusdt.EMA50[-2]
                ):
                    # Inform about BTC trend change
                    if asyncState.btc_downtrend:
                        TG_inform = True
                    logging.info(
                        "btc-pulse signaling UPTREND ↗️ (golden cross check) - actual BTC price: "
                        + format_currency(btcusdt["Close"][-1], "USD", locale="en_US")
                        + "   EMA9-5m: "
                        + format_currency(btcusdt.EMA9[-1], "USD", locale="en_US")
                        + " more than EMA50-5m: "
                        + format_currency(btcusdt.EMA50[-1], "USD", locale="en_US")
                        + " and BTC price 5 minutes before: "
                        + format_currency(btcusdt[-2])
                        + "   EMA9-5m: "
                        + format_currency(btcusdt.EMA9[-2], "USD", locale="en_US")
                        + " less than EMA50-5m: "
                        + format_currency(btcusdt.EMA50[-2], "USD", locale="en_US"),
                        TG_inform,
                    )
                    if not attributes.get("single"):
                        logging.info(
                            "3cqsbot enabled: '"
                            + str(asyncState.multibot["is_enabled"])
                            + "'",
                            TG_inform,
                        )
                    if (
                        attributes.get("fgi_pulse", False)
                        and not asyncState.fgi_allows_trading
                    ):
                        logging.info(
                            "3cqsbot will not be enabled because FGI does not allow trading",
                            TG_inform,
                        )
                    asyncState.btc_downtrend = False
                    TG_inform = False
                else:
                    # Inform about BTC trend change
                    if not asyncState.btc_downtrend:
                        TG_inform = True
                    logging.info(
                        "btc-pulse signaling DOWNTREND ↘️ - actual BTC price: "
                        + format_currency(btcusdt["Close"][-1], "USD", locale="en_US")
                        + "   EMA9-5m: "
                        + format_currency(btcusdt.EMA9[-1], "USD", locale="en_US")
                        + " less than EMA50-5m: "
                        + format_currency(btcusdt.EMA50[-1], "USD", locale="en_US"),
                        TG_inform,
                    )
                    if not attributes.get("single"):
                        logging.info(
                            "3cqsbot enabled: '"
                            + str(asyncState.multibot["is_enabled"])
                            + "'",
                            TG_inform,
                        )
                    asyncState.btc_downtrend = True
                    TG_inform = False
            else:
                # Inform about BTC trend change
                if asyncState.btc_downtrend:
                    TG_inform = True
                logging.info(
                    "btc-pulse signaling UPTREND ↗️ - actual BTC price: "
                    + format_currency(btcusdt["Close"][-1], "USD", locale="en_US")
                    + "   EMA9-5m: "
                    + format_currency(btcusdt.EMA9[-1], "USD", locale="en_US")
                    + " more than EMA50-5m: "
                    + format_currency(btcusdt.EMA50[-1], "USD", locale="en_US"),
                    TG_inform,
                )
                if not attributes.get("single"):
                    logging.info(
                        "3cqsbot enabled: '"
                        + str(asyncState.multibot["is_enabled"])
                        + "'",
                        TG_inform,
                    )
                if (
                    attributes.get("fgi_pulse", False)
                    and not asyncState.fgi_allows_trading
                ):
                    logging.info(
                        "3cqsbot will not be enabled because FGI does not allow trading",
                        TG_inform,
                    )
                asyncState.btc_downtrend = False
                TG_inform = False

            logging.info(
                "Next btc-pulse check in "
                + format_timedelta(interval_sec, locale="en_US")
            )
            notification.send_notification()
            await asyncio.sleep(interval_sec)
            i += 1
        except Exception as err:
            logging.error(f"Exception raised by async get_btcpulse: {err}")
            await asyncio.sleep(interval_sec)
            i += 1


async def fgi_dca_conf_change(interval_sec):

    while True:
        try:
            if asyncState.fgi >= attributes.get(
                "fgi_min", 0, "fgi_defensive"
            ) and asyncState.fgi <= attributes.get("fgi_max", 30, "fgi_defensive"):
                asyncState.dca_conf = "fgi_defensive"

            if asyncState.fgi >= attributes.get(
                "fgi_min", 31, "fgi_moderate"
            ) and asyncState.fgi <= attributes.get("fgi_max", 60, "fgi_moderate"):
                asyncState.dca_conf = "fgi_moderate"

            if asyncState.fgi >= attributes.get(
                "fgi_min", 61, "fgi_aggressive"
            ) and asyncState.fgi <= attributes.get("fgi_max", 100, "fgi_aggressive"):
                asyncState.dca_conf = "fgi_aggressive"

            # Check if section fgi_defensive, fgi_moderate and fgi_aggressive are defined in config.ini, if not use standard settings of [dcabot]
            if (
                attributes.get("fgi_min", -1, "fgi_defensive") == -1
                or attributes.get("fgi_min", -1, "fgi_moderate") == -1
                or attributes.get("fgi_min", -1, "fgi_aggressive") == -1
            ):
                logging.info(
                    "DCA settings for [fgi_defensive], [fgi_moderate] or [fgi_aggressive] are not configured. Using standard settings of [dcabot] for all FGI values 0-100",
                    True,
                )
                asyncState.dca_conf = "dcabot"
            notification.send_notification()
            await asyncio.sleep(interval_sec)
        except Exception as err:
            logging.error(f"Exception raised by async fgi_dca_conf_change: {err}")
            await asyncio.sleep(interval_sec)


async def bot_switch(interval_sec):

    while True:
        try:
            logging.debug("bot_switch: begin of while loop")

            if (
                not asyncState.bot_active
                and not asyncState.btc_downtrend
                and asyncState.fgi_allows_trading
            ):
                if attributes.get("single"):
                    asyncState.bot_active = True
                    logging.info(
                        "Single bot mode activated - waiting for pair #start signals",
                        True,
                    )
                elif get_deal_mode() == "signal" or attributes.get(
                    "continuous_update", False
                ):
                    # listen continuously to 3cqs msgs on TG, avoid symrank calls
                    if asyncState.multibot == {}:
                        bot = MultiBot(
                            [],
                            bot_data(),
                            {},
                            0,
                            attributes,
                            p3cw,
                            logging,
                            asyncState,
                        )
                    else:
                        bot = MultiBot(
                            [],
                            asyncState.multibot,
                            {},
                            0,
                            attributes,
                            p3cw,
                            logging,
                            asyncState,
                        )
                    bot.enable()
                    asyncState.multibot = bot.asyncState.multibot
                    asyncState.bot_active = bot.asyncState.multibot["is_enabled"]
                    logging.info(
                        "Multi bot activated - waiting for pair #start signals",
                        True,
                    )
                # enables 3cqsbot only after sending symrank call to avoid messing up with old pairs
                else:
                    logging.info(
                        "Multi bot will be activated after processing top30 symrank list",
                        True,
                    )
                    asyncState.symrank_success = False
                    while not asyncState.symrank_success:
                        await symrank()

            elif asyncState.bot_active and (
                asyncState.btc_downtrend or not asyncState.fgi_allows_trading
            ):
                if attributes.get("single"):
                    bot = SingleBot(
                        [], bot_data(), {}, attributes, p3cw, logging, asyncState
                    )
                    # True = disable all single bots
                    bot.disable(bot_data(), True)
                    asyncState.bot_active = bot.asyncState.bot_active
                else:
                    if asyncState.multibot == {}:
                        bot = MultiBot(
                            [],
                            bot_data(),
                            {},
                            0,
                            attributes,
                            p3cw,
                            logging,
                            asyncState,
                        )
                    else:
                        bot = MultiBot(
                            [],
                            asyncState.multibot,
                            {},
                            0,
                            attributes,
                            p3cw,
                            logging,
                            asyncState,
                        )
                    bot.disable()
                    asyncState.multibot = bot.asyncState.multibot
                    asyncState.bot_active = bot.asyncState.multibot["is_enabled"]
            else:
                logging.debug("bot_switch: Nothing do to")

            notification.send_notification()
            await asyncio.sleep(interval_sec)
        except Exception as err:
            logging.error(f"Exception raised by async bot_switch: {err}")
            logging.error(f"bot_switch: Sleeping for {interval_sec}sec")
            await asyncio.sleep(interval_sec)


def _handle_task_result(task: asyncio.Task) -> None:

    try:
        task.result()
    except asyncio.CancelledError:
        pass  # Task cancellation should not be logged as an error.
    except Exception:  # pylint: disable=broad-except
        logging.error(f"Exception raised by task = {task}")


async def symrank():

    logging.info(
        "Sending /symrank command to 3C Quick Stats on Telegram to get new pairs"
    )
    while not asyncState.symrank_success:
        await client.send_message(asyncState.chatid, "/symrank")
        await asyncio.sleep(5)
        # prevent from calling the symrank command too much otherwise a timeout is caused
        if not asyncState.symrank_success:
            await asyncio.sleep(asyncState.symrank_retry)
    # reset to 60sec in case of success after topcoin filter
    asyncState.symrank_retry = 60


def get_deal_mode():
    strategy = attributes.get("deal_mode", "test", asyncState.dca_conf)
    if strategy == "test":
        strategy = attributes.get("deal_mode", "")
    return strategy


@client.on(events.NewMessage(chats=attributes.get("chatroom", "3C Quick Stats")))
async def my_event_handler(event):
    more_inform = attributes.get("extensive_notifications", False)
    tg_output = tg_data(parse_tg(event.raw_text))
    logging.debug("TG msg: " + str(tg_output))
    dealmode_signal = get_deal_mode() == "signal"

    if tg_output and asyncState.fgi_allows_trading and asyncState.receive_signals:
        account_output = asyncState.account_data
        pair_output = asyncState.pair_data

        ##### if TG message is #START or #STOP
        if tg_output and not isinstance(tg_output, list):

            logging.info(
                "'"
                + tg_output["signal"]
                + "': "
                + tg_output["action"]
                + " signal for "
                + tg_output["pair"]
                + " incoming...",
                more_inform,
            )

            # track time from START signal to deal creation
            if tg_output["action"] == "START":
                asyncState.latest_signal_time = datetime.utcnow()

            # Check if pair is in whitelist
            if attributes.get("token_whitelist", []):
                token_whitelisted = tg_output["pair"] in attributes.get(
                    "token_whitelist", []
                )
                logging.info(
                    tg_output["pair"] + " in whitelist, processing signal", more_inform
                )
            else:
                token_whitelisted = True
            if not token_whitelisted:
                logging.info(
                    "Signal ignored because pair is not whitelisted", more_inform
                )
                return

            # Check if it is the correct symrank_signal
            if not (
                tg_output["signal"] == attributes.get("symrank_signal")
                or attributes.get("symrank_signal") == "all"
            ):
                logging.info(
                    "Signal ignored because '"
                    + attributes.get("symrank_signal")
                    + "' is configured",
                    more_inform,
                )
                return

            # statistics about signals
            if tg_output["action"] == "START":
                asyncState.start_signals_24h += 1
                if asyncState.bot_active:
                    asyncState.start_signals_bot_enabled_24h += 1
            elif tg_output["action"] == "STOP":
                asyncState.stop_signals_24h += 1

            # Check if bot is active
            if not asyncState.bot_active and not attributes.get(
                "continuous_update", False
            ):
                logging.info(
                    "Signal not processed because 3cqsbot is disabled", more_inform
                )
                return

            # Check if pair is tradeable
            if not tg_output["pair"] in pair_output:
                logging.info(
                    str(tg_output["pair"])
                    + " is not traded on '"
                    + attributes.get("account_name")
                    + "'",
                    more_inform,
                )
                asyncState.start_signals_not_tradeable_24h += 1
                return

            # Check if 3cqs START signal passes optional symrank criteria
            if tg_output["volatility"] != 0 and tg_output["action"] == "START":

                if not (
                    tg_output["volatility"]
                    >= attributes.get("volatility_limit_min", 0.1)
                    and tg_output["volatility"]
                    <= attributes.get("volatility_limit_max", 100)
                    and tg_output["price_action"]
                    >= attributes.get("price_action_limit_min", 0.1)
                    and tg_output["price_action"]
                    <= attributes.get("price_action_limit_max", 100)
                    and tg_output["symrank"] >= attributes.get("symrank_limit_min", 1)
                    and tg_output["symrank"] <= attributes.get("symrank_limit_max", 100)
                ):
                    logging.info(
                        "Start signal for "
                        + str(tg_output["pair"])
                        + " with symrank: "
                        + str(tg_output["symrank"])
                        + ", volatility: "
                        + str(tg_output["volatility"])
                        + " and price action: "
                        + str(tg_output["price_action"])
                        + " not meeting config filter limits - signal ignored",
                        more_inform,
                    )
                    return
                else:
                    asyncState.start_signals_symrank_filter_passed_24h += 1

            # for single and multibot: if dealmode == signal and STOP signal is sent than ignore
            if tg_output["action"] == "STOP" and dealmode_signal:
                logging.info(
                    "STOP signal ignored - not necessary when deal_mode = signal",
                    more_inform,
                )
                return

            # Attribute variables either to single or multi bot
            if attributes.get("single") or asyncState.multibot == {}:
                bot_output = bot_data()
            else:
                bot_output = asyncState.multibot
            if attributes.get("single"):
                bot = SingleBot(
                    tg_output,
                    bot_output,
                    account_output,
                    attributes,
                    p3cw,
                    logging,
                    asyncState,
                )
            else:
                bot = MultiBot(
                    tg_output,
                    bot_output,
                    account_output,
                    pair_output,
                    attributes,
                    p3cw,
                    logging,
                    asyncState,
                )

            # for multibot: if dealmode == signal and multibot is empty create/update and enable multibot before processing deals
            if (
                dealmode_signal
                and asyncState.multibot == {}
                and not attributes.get("single")
                and not tg_output["action"] == "STOP"
            ):
                bot.create()
                asyncState.multibot = bot.asyncState.multibot
                asyncState.bot_active = bot.asyncState.multibot["is_enabled"]
                asyncState.first_topcoin_call = bot.asyncState.first_topcoin_call

            # for single and multibot: function bot.trigger() handles START and STOP signals
            if asyncState.multibot != {} or attributes.get("single"):

                bot.trigger()
                if not attributes.get("single"):
                    asyncState.multibot = bot.asyncState.multibot
                    asyncState.bot_active = bot.asyncState.multibot["is_enabled"]
                else:
                    asyncState.bot_active = bot.asyncState.bot_active

        ##### if TG message is symrank list
        elif tg_output and isinstance(tg_output, list):
            if (
                not attributes.get("single")
                and not dealmode_signal
                and not asyncState.symrank_success
            ):
                asyncState.symrank_success = True
                logging.info("New symrank list incoming - updating bot", True)
                if asyncState.multibot == {}:
                    bot_output = bot_data()
                else:
                    bot_output = asyncState.multibot

                # create/update and enable multibot with pairs from "/symrank"
                bot = MultiBot(
                    tg_output,
                    bot_output,
                    account_output,
                    pair_output,
                    attributes,
                    p3cw,
                    logging,
                    asyncState,
                )
                bot.create()
                asyncState.multibot = bot.asyncState.multibot
                asyncState.bot_active = bot.asyncState.multibot["is_enabled"]
                asyncState.first_topcoin_call = bot.asyncState.first_topcoin_call
                asyncState.symrank_retry = bot.asyncState.symrank_retry
            else:
                logging.debug(
                    "Ignoring /symrank call, because we're running in single mode!"
                )

    notification.send_notification()


def report_funds_needed(dca_conf="dcabot"):
    tp = attributes.get("tp", "", dca_conf)  # take profit
    bo = attributes.get("bo", "", dca_conf)  # base order
    so = attributes.get("so", "", dca_conf)  # safety order
    os = attributes.get("os", "", dca_conf)  # safety order volume scale
    ss = attributes.get("ss", "", dca_conf)  # safety order step scale
    sos = attributes.get("sos", "", dca_conf)  # price deviation to open safety orders
    mstc = attributes.get("mstc", "", dca_conf)  # max safety trade count

    fundsneeded = bo + so
    amount = so
    pd = sos
    cum_size_base = bo + so / (1 - (1 * sos / 100))
    for i in range(mstc - 1):
        amount = amount * os
        fundsneeded += amount
        pd = (pd * ss) + sos
        price = (100 - pd) / 100
        size_base = amount / price
        cum_size_base += size_base
        avg_price = fundsneeded / cum_size_base
        required_price = avg_price * tp / 100 + avg_price
        required_change = ((required_price / price) - 1) * 100

    if attributes.get("single"):
        maxdeals = int(attributes.get("single_count", "0", dca_conf))
    else:
        maxdeals = int(attributes.get("mad", "0", dca_conf))
    fundsneeded = fundsneeded * maxdeals

    return fundsneeded, pd, required_change


def report_dca_settings(dca_conf):
    fundsneeded, pd, rc = report_funds_needed(dca_conf)
    if dca_conf == "dcabot":
        dca_setting = "  [dcabot]"
    else:
        dca_setting = (
            "  ["
            + dca_conf
            + "]   FGI range: "
            + str(attributes.get("fgi_min", "0", dca_conf))
            + "-"
            + str(attributes.get("fgi_max", "0", dca_conf))
        )

    logging.info(
        dca_setting
        + "  name: "
        + attributes.get("prefix", "", dca_conf)
        + "_"
        + attributes.get("subprefix", "", dca_conf)
        + "_"
        + attributes.get("suffix", "", dca_conf),
        True,
    )
    if attributes.get("single"):
        logging.info(
            "  amount of single bots: "
            + str(attributes.get("single_count", "0", dca_conf))
        )
    logging.info(
        "  mad: "
        + str(attributes.get("mad", "0", dca_conf))
        + "  funds needed: "
        + format_currency(fundsneeded, "USD", locale="en_US")
        + "  cov. max price dev: "
        + f"{pd:2.1f}%"
        + "  max req. change: "
        + f"{rc:2.1f}%",
        True,
    )
    if attributes.get("topcoin_filter", False):
        logging.info(
            "  Topcoin filter: marketcap top #"
            + str(attributes.get("topcoin_limit", 3500, dca_conf))
            + " and min. daily BTC trading vol.: "
            + str(attributes.get("topcoin_volume", 0, dca_conf)),
            True,
        )


def report_config():
    logging.info("********** Config reporting **********", True)
    logging.info("Debug mode: '" + str(attributes.get("debug", False)) + "'", True)
    if attributes.get("single"):
        logging.info("Bot mode: 'single pair'", True)
    else:
        logging.info("Bot mode: 'multi pair'", True)

    logging.info(
        "Listening to 3cqs signals: '" + str(attributes.get("symrank_signal")) + "'",
        True,
    )
    logging.info(
        "Sort and limit symrank pairs to mad: '"
        + str(attributes.get("limit_inital_pairs", False))
        + "'",
        True,
    )
    if not attributes.get("single", False):
        logging.info(
            "Avoid symrank calls with continuous pair update (also when bot is disabled) for multibot: '"
            + str(attributes.get("continuous_update", False))
            + "'",
            True,
        )
    logging.info(
        "Topcoin filter: '" + str(attributes.get("topcoin_filter", False)) + "'", True
    )
    if attributes.get("topcoin_filter", False) and not attributes.get(
        "fgi_trading", False
    ):
        logging.info(
            "Marketcap top #"
            + str(attributes.get("topcoin_limit", 3500, asyncState.dca_conf))
            + " - Min. daily BTC trading volume: "
            + str(attributes.get("topcoin_volume", 0, asyncState.dca_conf)),
            True,
        )
    logging.info("BTC pulse: '" + str(attributes.get("btc_pulse", False)) + "'", True)
    logging.info(
        "FGI pulse: '"
        + str(attributes.get("fgi_pulse", False))
        + "'   Fast EMA: "
        + str(attributes.get("fgi_ema_fast", 9))
        + "d   Slow EMA: "
        + str(attributes.get("fgi_ema_slow", 20))
        + "d",
        True,
    )
    logging.info(
        "FGI trading: '"
        + str(attributes.get("fgi_trading", False))
        + "'   FGI required for trading: ["
        + str(attributes.get("fgi_trade_min", 0))
        + "-"
        + str(attributes.get("fgi_trade_max", 100))
        + "]",
        True,
    )
    if attributes.get("fgi_trading", False):
        report_dca_settings("fgi_aggressive")
        report_dca_settings("fgi_moderate")
        report_dca_settings("fgi_defensive")
    else:
        report_dca_settings("dcabot")

    logging.info(
        "External/TV bot switching: '"
        + str(attributes.get("ext_botswitch", False))
        + "'",
        True,
    )
    logging.info("Quote currency: '" + str(attributes.get("market")) + "'")
    logging.info(
        "Token whitelist: '" + str(attributes.get("token_whitelist", "No")) + "'", True
    )
    logging.info("********** End of config reporting **********", True)
    return


async def report_statistics():

    while True:

        logging.info("********** Signal & Bot statistics **********", True)

        start_delta = datetime.utcnow() - asyncState.start_time
        logging.info(
            "Python script running since "
            + format_timedelta(start_delta, locale="en_US"),
            True,
        )
        if asyncState.receive_signals:
            logging.info(
                "'"
                + attributes.get("symrank_signal")
                + "' signals received over last 24h  - #Start: "
                + str(asyncState.start_signals_24h)
                + " - #Stop: "
                + str(asyncState.stop_signals_24h),
                True,
            )
            logging.info(
                "#Start signals processed while bot was enabled last 24h: "
                + str(asyncState.start_signals_bot_enabled_24h),
                True,
            )
            logging.info(
                "#Start signals not tradeable on exchange last 24h: "
                + str(asyncState.start_signals_not_tradeable_24h),
                True,
            )
            logging.info(
                "#Start signals passing symrank filter last 24h: "
                + str(asyncState.start_signals_symrank_filter_passed_24h),
                True,
            )
            logging.info(
                "#Start signals passing topcoin filter last 24h: "
                + str(asyncState.start_signals_topcoin_filter_passed_24h),
                True,
            )

            asyncState.start_signals += asyncState.start_signals_24h
            asyncState.start_signals_bot_enabled += (
                asyncState.start_signals_bot_enabled_24h
            )
            asyncState.start_signals_not_tradeable += (
                asyncState.start_signals_not_tradeable_24h
            )
            asyncState.start_signals_symrank_filter_passed += (
                asyncState.start_signals_symrank_filter_passed_24h
            )
            asyncState.start_signals_topcoin_filter_passed += (
                asyncState.start_signals_topcoin_filter_passed_24h
            )
            asyncState.stop_signals += asyncState.stop_signals_24h

            asyncState.start_signals_24h = 0
            asyncState.start_signals_bot_enabled_24h = 0
            asyncState.start_signals_not_tradeable_24h = 0
            asyncState.start_signals_symrank_filter_passed_24h = 0
            asyncState.start_signals_topcoin_filter_passed_24h = 0
            asyncState.stop_signals_24h = 0

            start_per_day = asyncState.start_signals / (start_delta / timedelta(days=1))
            stop_per_day = asyncState.stop_signals / (start_delta / timedelta(days=1))
            logging.info(
                "Total signals processed since script start - #Start: "
                + str(asyncState.start_signals)
                + " (per day: "
                + f"{start_per_day:2.1f})"
                + " - #Stop: "
                + str(asyncState.stop_signals)
                + " (per day: "
                + f"{stop_per_day:2.1f})",
                True,
            )
            logging.info(
                "Total #Start signals while bot was enabled: "
                + str(asyncState.start_signals_bot_enabled),
                True,
            )
            logging.info(
                "Total #Start signals not tradeable on exchange: "
                + str(asyncState.start_signals_not_tradeable),
                True,
            )
            logging.info(
                "Total #Start signals passing symrank filter: "
                + str(asyncState.start_signals_symrank_filter_passed),
                True,
            )
            logging.info(
                "Total #Start signals passing topcoin filter: "
                + str(asyncState.start_signals_topcoin_filter_passed),
                True,
            )

        logging.info("Actual DCA bot setting:", True)
        report_dca_settings(asyncState.dca_conf)

        if attributes.get("single"):
            bot = SingleBot([], bot_data(), {}, attributes, p3cw, logging, asyncState)
        # True = disable all single bots
        elif asyncState.multibot == {}:
            bot = MultiBot(
                [],
                bot_data(),
                {},
                0,
                attributes,
                p3cw,
                logging,
                asyncState,
            )
        else:
            bot = MultiBot(
                [],
                asyncState.multibot,
                {},
                0,
                attributes,
                p3cw,
                logging,
                asyncState,
            )

        bot.report_deals()

        midnight = (datetime.now() + timedelta(days=1)).replace(
            hour=0, minute=0, microsecond=0, second=0
        )
        time_until_update = midnight - datetime.now()
        logging.info(
            "Next statistics update in approx. "
            + format_timedelta(time_until_update, locale="en_US"),
            True,
        )
        logging.info("********** End of statistics reporting **********", True)
        await asyncio.sleep(time_until_update.seconds + 1)


async def main():

    # Check for single instance run
    single_instance_check()

    signals = Signals(logging)

    ##### Initial reporting #####
    logging.info("********** 3CQS Bot started **********", True)
    asyncState.start_time = datetime.utcnow()
    user = await client.get_participants("The3CQSBot")
    asyncState.chatid = user[0].id

    asyncState.account_data = account_data()
    # Update available pair_data every 360 minutes for e.g. new blacklisted pairs or new tradable pairs
    pair_data_task = client.loop.create_task(
        pair_data(asyncState.account_data, 3600 * 6)
    )
    pair_data_task.add_done_callback(_handle_task_result)
    await asyncio.sleep(3)

    report_config()

    # Check for inconsistencies of bot switching before starting 3cqsbot
    if attributes.get("btc_pulse", False) and attributes.get("ext_botswitch", False):
        sys.tracebacklimit = 0
        sys.exit(
            "Check config.ini: btc_pulse AND ext_botswitch both set to true - not allowed"
        )

    # Obtain FGI values in the background
    if attributes.get("fgi_pulse", False) or attributes.get("fgi_trading", False):
        get_fgi_task = client.loop.create_task(
            get_fgi(
                attributes.get("fgi_ema_fast", 9), attributes.get("fgi_ema_slow", 20)
            )
        )
        get_fgi_task.add_done_callback(_handle_task_result)
        await asyncio.sleep(3)

    # Enable FGI dependent trading
    if attributes.get("fgi_trading", False):
        fgi_dca_conf_change_task = client.loop.create_task(
            fgi_dca_conf_change(3600)
        )  # check once per hour
        fgi_dca_conf_change_task.add_done_callback(_handle_task_result)
        await asyncio.sleep(1)

        logging.info("DCA setting: '[" + asyncState.dca_conf + "]'", True)
        logging.info(
            "Deal mode of DCA setting: '" + get_deal_mode() + "'",
            True,
        )

    # Enable btc_pulse dependent trading
    if attributes.get("btc_pulse", False):
        asyncState.btc_downtrend = True
        btcpulse_task = client.loop.create_task(get_btcpulse(300))  # check every 5 min
        btcpulse_task.add_done_callback(_handle_task_result)
    else:
        asyncState.btc_downtrend = False

    # Central Bot Switching module for btc_pulse and FGI
    if (
        attributes.get("btc_pulse", False)
        or attributes.get("fgi_pulse", False)
        or attributes.get("fgi_trading", False)
    ):
        bot_switch_task = client.loop.create_task(bot_switch(60))
        bot_switch_task.add_done_callback(_handle_task_result)

    # Search and rename 3cqsbot if multipair is configured
    if asyncState.multibot == {} and not attributes.get("single"):
        bot = MultiBot(
            [],
            bot_data(),
            asyncState.account_data,
            0,
            attributes,
            p3cw,
            logging,
            asyncState,
        )
        bot.search_rename_3cqsbot()
        asyncState.multibot = bot.asyncState.multibot
        if asyncState.multibot:
            asyncState.bot_active = bot.asyncState.multibot["is_enabled"]
        else:
            asyncState.bot_active = False

    report_statistics_task = client.loop.create_task(report_statistics())
    report_statistics_task.add_done_callback(_handle_task_result)
    await asyncio.sleep(3)

    ##### Wait for TG signals of 3C Quick Stats channel #####
    logging.info(
        "********** Waiting for 3CQS '"
        + attributes.get("symrank_signal")
        + "' signals on Telegram **********",
        True,
    )
    asyncState.receive_signals = True
    notification.send_notification()

    if get_deal_mode() != "signal":
        while (
            asyncState.fgi_allows_trading
            and not asyncState.symrank_success
            and not attributes.get("single")
        ):
            await symrank()


try:
    client.start()
    client.loop.run_until_complete(main())
    client.run_until_disconnected()
except Exception as err:
    logging.error(f"Exception raised by Telegram client: {err}")
