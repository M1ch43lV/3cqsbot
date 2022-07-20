import json
import random
import sys
from datetime import datetime

from babel.dates import format_timedelta
from babel.numbers import format_currency

from signals import Signals


class MultiBot:
    def __init__(
        self,
        tg_data,
        bot_data,
        account_data,
        pair_data,
        attributes,
        p3cw,
        logging,
        asyncState,
    ):
        self.tg_data = tg_data
        self.bot_data = bot_data
        self.account_data = account_data
        self.pair_data = pair_data
        self.attributes = attributes
        self.p3cw = p3cw
        self.logging = logging
        self.asyncState = asyncState
        self.signal = Signals(logging)
        self.config_botid = str(self.attributes.get("botid", "", "dcabot"))
        self.botname = (
            self.attributes.get(
                "prefix",
                self.attributes.get("prefix", "3CQSBOT", "dcabot"),
                self.asyncState.dca_conf,
            )
            + "_"
            + self.attributes.get(
                "subprefix",
                self.attributes.get("subprefix", "MULTI", "dcabot"),
                self.asyncState.dca_conf,
            )
            + "_"
            + self.attributes.get(
                "suffix",
                self.attributes.get("suffix", "dcabot", "dcabot"),
                self.asyncState.dca_conf,
            )
        )

    def report_deals(self, report_latency=False):
        self.logging.info(
            "Deals active: "
            + str(self.bot_data["active_deals_count"])
            + "/"
            + str(self.bot_data["max_active_deals"]),
            True,
        )
        self.logging.info(
            "Profits of "
            + str(self.bot_data["finished_deals_count"])
            + " finished deals: "
            + format_currency(
                self.bot_data["finished_deals_profit_usd"], "USD", locale="en_US"
            ),
            True,
        )
        self.logging.info(
            "uPNL of active deals: "
            + format_currency(
                self.bot_data["active_deals_usd_profit"], "USD", locale="en_US"
            ),
            True,
        )

        error, data = self.p3cw.request(
            entity="deals",
            action="",
            action_id="",
            additional_headers={"Forced-Mode": self.attributes.get("trade_mode")},
            payload={"limit": 100, "bot_id": self.bot_data["id"], "scope": "active"},
        )
        if error:
            self.logging.error(error["msg"])
        else:
            i = 1
            for deals in data:
                if (
                    deals["bought_volume"] == None
                ):  # if no bought_volume, then use base_order_volume for bought_volume
                    bought_volume = format_currency(
                        deals["base_order_volume"], "USD", locale="en_US"
                    )
                else:
                    bought_volume = format_currency(
                        deals["bought_volume"], "USD", locale="en_US"
                    )

                self.logging.info(
                    "Deal "
                    + deals["pair"]
                    + " open since "
                    + format_timedelta(
                        datetime.utcnow()
                        - datetime.strptime(
                            deals["created_at"], "%Y-%m-%dT%H:%M:%S.%fZ"
                        ),
                        locale="en_US",
                    )
                    + "   Actual profit: "
                    + format_currency(deals["actual_usd_profit"], "USD", locale="en_US")
                    + " ("
                    + deals["actual_profit_percentage"]
                    + "%)"
                    + "   Bought volume: "
                    + bought_volume
                    + "   Deal error: "
                    + str(deals["deal_has_error"]),
                    True,
                )

                if i == 1 and report_latency:
                    self.logging.info(
                        "Time delta between 3cqs signal and actual deal creation: "
                        + format_timedelta(
                            datetime.strptime(
                                deals["created_at"], "%Y-%m-%dT%H:%M:%S.%fZ"
                            )
                            - self.asyncState.latest_signal_time,
                            locale="en_US",
                        ),
                        True,
                    )
                i += 1

        return

    def report_funds_needed(self, maxdeals):

        self.logging.info(
            "Deal start condition(s): "
            + self.attributes.get("deal_mode", "", self.asyncState.dca_conf),
            True,
        )

        tp = self.attributes.get("tp", "", self.asyncState.dca_conf)
        bo = self.attributes.get("bo", "", self.asyncState.dca_conf)
        so = self.attributes.get("so", "", self.asyncState.dca_conf)
        os = self.attributes.get("os", "", self.asyncState.dca_conf)
        ss = self.attributes.get("ss", "", self.asyncState.dca_conf)
        sos = self.attributes.get("sos", "", self.asyncState.dca_conf)
        mstc = self.attributes.get("mstc", "", self.asyncState.dca_conf)

        fundsneeded = bo + so
        socalc = so
        pd = sos
        for i in range(mstc - 1):
            socalc = socalc * os
            fundsneeded += socalc
            pd = (pd * ss) + sos

        self.logging.info(
            "Using DCA settings ["
            + self.asyncState.dca_conf
            + "]:  TP: "
            + str(tp)
            + "%  BO: $"
            + str(bo)
            + "  SO: $"
            + str(so)
            + "  OS: "
            + str(os)
            + "  SS: "
            + str(ss)
            + "  SOS: "
            + str(sos)
            + "%  MSTC: "
            + str(mstc)
            + " - covering max. price deviation: "
            + f"{pd:2.1f}"
            + "%",
            True,
        )
        self.logging.info(
            "Max active deals (mad) allowed: "
            + str(maxdeals)
            + "   Max funds per active deal (all SO filled): "
            + format_currency(fundsneeded, "USD", locale="en_US")
            + "   Total funds needed: "
            + format_currency(maxdeals * fundsneeded, "USD", locale="en_US"),
            True,
        )

        return

    def strategy(self):
        if self.attributes.get("deal_mode", "", self.asyncState.dca_conf) == "signal":
            strategy = [{"strategy": "manual"}]
        else:
            try:
                strategy = json.loads(
                    self.attributes.get("deal_mode", "", self.asyncState.dca_conf)
                )
            except ValueError:
                self.logging.error(
                    "Either missing ["
                    + self.asyncState.dca_conf
                    + "] section with DCA settings or decoding JSON string of deal_mode failed. "
                    + "Please check https://jsonformatter.curiousconcept.com/ for correct format"
                )
                sys.exit("Aborting script!")

        return strategy

    def payload(self, pairs, mad, new_bot):

        payload = {
            "name": self.botname,
            "account_id": self.account_data["id"],
            "pairs": pairs,
            "max_active_deals": mad,
            "base_order_volume": self.attributes.get(
                "bo", "", self.asyncState.dca_conf
            ),
            "take_profit": self.attributes.get("tp", "", self.asyncState.dca_conf),
            "safety_order_volume": self.attributes.get(
                "so", "", self.asyncState.dca_conf
            ),
            "martingale_volume_coefficient": self.attributes.get(
                "os", "", self.asyncState.dca_conf
            ),
            "martingale_step_coefficient": self.attributes.get(
                "ss", "", self.asyncState.dca_conf
            ),
            "max_safety_orders": self.attributes.get(
                "mstc", "", self.asyncState.dca_conf
            ),
            "safety_order_step_percentage": self.attributes.get(
                "sos", "", self.asyncState.dca_conf
            ),
            "take_profit_type": "total",
            "active_safety_orders_count": self.attributes.get(
                "max", "", self.asyncState.dca_conf
            ),
            "cooldown": self.attributes.get("cooldown", 0, self.asyncState.dca_conf),
            "strategy_list": self.strategy(),
            "trailing_enabled": self.attributes.get(
                "trailing", False, self.asyncState.dca_conf
            ),
            "trailing_deviation": self.attributes.get(
                "trailing_deviation", 0.2, self.asyncState.dca_conf
            ),
            "allowed_deals_on_same_pair": self.attributes.get(
                "sdsp", "", self.asyncState.dca_conf
            ),
            "min_volume_btc_24h": self.attributes.get(
                "btc_min_vol", 0, self.asyncState.dca_conf
            ),
            "disable_after_deals_count": self.attributes.get(
                "deals_count", 0, self.asyncState.dca_conf
            ),
        }

        if new_bot:
            if payload["disable_after_deals_count"] == 0:
                self.logging.debug(
                    "This is a new bot and deal_count set to 0, removing from payload"
                )
                payload.pop("disable_after_deals_count")

        if self.attributes.get("trade_future", False):
            payload.update(
                {
                    "leverage_type": self.attributes.get("leverage_type"),
                    "leverage_custom_value": self.attributes.get("leverage_value"),
                    "stop_loss_percentage": self.attributes.get("stop_loss_percent"),
                    "stop_loss_type": self.attributes.get("stop_loss_type"),
                    "stop_loss_timeout_enabled": self.attributes.get(
                        "stop_loss_timeout_enabled"
                    ),
                    "stop_loss_timeout_in_seconds": self.attributes.get(
                        "stop_loss_timeout_seconds"
                    ),
                }
            )

        return payload

    def adjust_mad(self, pairs, mad):
        # Lower max active deals, when pairs are under mad
        if len(pairs) * self.attributes.get("sdsp") < mad:
            self.logging.debug(
                "Pairs are under 'mad' - Lower max active deals to actual pairs"
            )
            mad = len(pairs)
        # Raise max active deals to minimum pairs or mad if possible
        elif len(pairs) * self.attributes.get("sdsp") >= mad:
            self.logging.debug("Pairs are equal or over 'mad' - nothing to do")
            mad = self.attributes.get("mad")

        return mad

    def search_rename_3cqsbot(self):

        bot_by_id = False
        bot_by_name = False

        # Check for existing multibot id
        if self.config_botid != "":
            botnames = []
            self.logging.info("Searching for 3cqsbot with botid: " + self.config_botid)
            for bot in self.bot_data:
                botnames.append(bot["name"])

                if self.config_botid == str(bot["id"]):
                    bot_by_id = True
                    self.logging.info(
                        "Botid "
                        + self.config_botid
                        + " with name '"
                        + bot["name"]
                        + "' found"
                    )
                    # if 3cqsbot found by id, rename bot if needed according to config name settings
                    if self.botname != bot["name"]:
                        self.logging.info(
                            "Renaming bot name from '"
                            + bot["name"]
                            + "' to '"
                            + self.botname
                            + "' (botid: "
                            + str(bot["id"])
                            + ")",
                            True,
                        )
                    bot["name"] = self.botname

                    mad = self.attributes.get("mad")
                    mad = self.adjust_mad(bot["pairs"], mad)

                    error, data = self.p3cw.request(
                        entity="bots",
                        action="update",
                        action_id=str(bot["id"]),
                        additional_headers={
                            "Forced-Mode": self.attributes.get("trade_mode")
                        },
                        payload=self.payload(bot["pairs"], mad, new_bot=False),
                    )

                    if error:
                        self.logging.error(error["msg"])
                    else:
                        self.bot_data = data

                    break

        # Check for existing name
        if not bot_by_id:
            if self.attributes.get("fearandgreed", False) and self.config_botid == "":
                self.logging.error(
                    "Please add 'botid = xxxxxxx' to [dcabot] for using FGI. FGI guided DCA settings will only applied "
                    + "to existent 3cqsbot. \n Script will be aborted if no botid is found by botname"
                )

            botnames = []
            if self.config_botid != "":
                self.logging.info("3cqsbot not found with botid: " + self.config_botid)

            self.logging.info(
                "Searching for 3cqsbot with name '" + self.botname + "' to get botid"
            )
            for bot in self.bot_data:
                botnames.append(bot["name"])

                if self.botname == bot["name"]:
                    bot_by_name = True
                    self.logging.info(
                        "3cqsbot '"
                        + bot["name"]
                        + "' with botid "
                        + str(bot["id"])
                        + " found"
                    )

                mad = self.attributes.get("mad")
                mad = self.adjust_mad(bot["pairs"], mad)
                # always get a status update when searching first time for the bot
                error, data = self.p3cw.request(
                    entity="bots",
                    action="update",
                    action_id=str(bot["id"]),
                    additional_headers={
                        "Forced-Mode": self.attributes.get("trade_mode")
                    },
                    payload=self.payload(bot["pairs"], mad, new_bot=False),
                )

                if error:
                    self.logging.error(error["msg"])
                else:
                    self.bot_data = data
                break

            if not bot_by_name:
                self.logging.info("3cqsbot not found with this name")
                bot["name"] = ""

        self.logging.debug(
            "Checked bot ids/names till config id/name found: " + str(botnames)
        )

        # If FGI is used and botid is not set in [dcabot], which is mandatory to prevent creating new bots with different botids,
        # abort program for security reasons
        if self.attributes.get("fearandgreed", False) and self.config_botid == "":
            self.logging.error(
                "No botid set in [dcabot] and no 3cqsbot '"
                + self.botname
                + "' found on 3commas"
            )
            self.logging.error(
                "Please get botid on 3commas for an existent 3cqsbot and add 'botid = <botid of 3cqsbot>' under [dcabot] in config.ini"
            )
            self.logging.error(
                "If first time run of this script with enabled FGI and no 3cqsbot has been created so far,"
            )
            self.logging.error(
                "create manually one on 3commas, get botid and leave the bot disabled"
            )
            sys.exit("Aborting script!")

    def enable(self):
        # search for 3cqsbot by id or by name if bot not given
        if not isinstance(self.bot_data, dict):
            self.search_rename_3cqsbot()

        if not self.bot_data["is_enabled"]:
            self.logging.info(
                "Enabling bot: "
                + self.bot_data["name"]
                + " (botid: "
                + str(self.bot_data["id"])
                + ")",
                True,
            )

            error, data = self.p3cw.request(
                entity="bots",
                action="enable",
                action_id=str(self.bot_data["id"]),
                additional_headers={"Forced-Mode": self.attributes.get("trade_mode")},
            )

            if error:
                self.logging.error(error["msg"])
            else:
                self.bot_data = data
                self.logging.info("Enabling successful", True)
                self.asyncState.bot_active = True

        elif self.bot_data["is_enabled"]:
            self.logging.info(
                "'"
                + self.bot_data["name"]
                + "' (botid: "
                + str(self.bot_data["id"])
                + ") already enabled",
                True,
            )
            self.asyncState.bot_active = True
        else:
            self.logging.info(
                "'"
                + self.botname
                + "' or botid: "
                + str(self.config_botid)
                + " not found to enable"
            )

    def disable(self):
        # search for 3cqsbot by id or by name if bot not given
        if not isinstance(self.bot_data, dict):
            self.search_rename_3cqsbot()

        if self.bot_data["is_enabled"]:
            self.logging.info(
                "Disabling bot: "
                + self.bot_data["name"]
                + " (botid: "
                + str(self.bot_data["id"])
                + ")",
                True,
            )

            error, data = self.p3cw.request(
                entity="bots",
                action="disable",
                action_id=str(self.bot_data["id"]),
                additional_headers={"Forced-Mode": self.attributes.get("trade_mode")},
            )

            if error:
                self.logging.error(error["msg"])
            else:
                self.bot_data = data
                self.logging.info("Disabling successful", True)
                self.asyncState.bot_active = False

        elif not self.bot_data["is_enabled"]:
            self.logging.info(
                "'"
                + self.bot_data["name"]
                + "' (botid: "
                + str(self.bot_data["id"])
                + ") already disabled",
                True,
            )
            self.asyncState.bot_active = False
        else:
            self.logging.info(
                "'"
                + self.botname
                + "' or botid: "
                + str(self.config_botid)
                + " not found to disable"
            )

    def new_deal(self, triggerpair):
        # Triggers a new deal
        if triggerpair:
            pair = triggerpair
        else:
            if self.attributes.get("random_pair", "False"):
                pair = random.choice(self.bot_data["pairs"])
                self.logging.info(pair + " is the randomly chosen pair to start")
            else:
                pair = ""

        if pair:
            error, data = self.p3cw.request(
                entity="bots",
                action="start_new_deal",
                action_id=str(self.bot_data["id"]),
                additional_headers={"Forced-Mode": self.attributes.get("trade_mode")},
                payload={"pair": pair},
            )

            if error:
                self.logging.info(
                    "Triggering new deal for pair " + pair + " - unsuccessful", True
                )
                if (
                    self.bot_data["active_deals_count"]
                    >= self.bot_data["max_active_deals"]
                ):
                    self.logging.info(
                        "Max active deals of "
                        + str(self.bot_data["max_active_deals"])
                        + " reached, not adding a new one.",
                        True,
                    )
                else:
                    # modified output because this will be the most common error
                    self.logging.error(
                        "No deal triggered: " + error["msg"].split(":")[1].split(" ")[1]
                    )
                return False
            else:
                self.logging.info(
                    "Triggering new deal for pair " + pair + " - successful", True
                )
                self.bot_data["active_deals_count"] += 1
                return True

    def create(self):
        # Check if data of 3cqsbot is given (dict format), else search for existing one in the list before creating a new one
        if not isinstance(self.bot_data, dict):
            self.search_rename_3cqsbot()
        # if 3cqsbot was found use bot's pair list
        if isinstance(self.bot_data, dict):
            pairs = self.bot_data["pairs"]
        else:
            pairs = []

        mad = self.attributes.get("mad")
        maxdeals = mad
        dealmode_signal = (
            self.attributes.get("deal_mode", "", self.asyncState.dca_conf) == "signal"
        )
        # if dealmode_signal use signal pair to create/update bot, else check the 30 symrank pairs obtained by symrank call
        if dealmode_signal:
            pairlist = self.tg_data["pair"]
        else:
            # Initial pair list
            pairlist = self.tg_data

        # Filter topcoins (if set)
        # if first_topcoin_call == true then CG API requests are processed with latency of 2.2sec to avoid API timeout erros
        if self.attributes.get("topcoin_filter", False):
            pairlist = self.signal.topcoin(
                pairlist,
                self.attributes.get("topcoin_limit", 3500),
                self.attributes.get("topcoin_volume", 0),
                self.attributes.get("topcoin_exchange", "binance"),
                self.attributes.get("market"),
                self.asyncState.first_topcoin_call,
            )
            if isinstance(pairlist, list):
                self.asyncState.first_topcoin_call = False
        else:
            self.logging.info("Topcoin filter disabled, not filtering pairs!")

        # if no filtered coins left exit
        if not pairlist:
            self.logging.info("No pair(s) left after topcoin filter")
            return

        if pairlist and dealmode_signal:
            pair = pairlist
            if isinstance(self.bot_data, dict):
                if pair in self.bot_data["pairs"]:
                    self.logging.info(pair + " is already included in the pair list")
            elif pair in self.pair_data:
                self.logging.debug(pair + " added to the pair list")
                pairs.append(pair)
            else:
                self.logging.info(
                    pair
                    + " not included because pair is blacklisted on 3commas or in token_denylist "
                    + "or not tradable on '"
                    + self.attributes.get("account_name")
                    + "'"
                )
        elif pairlist:
            for pair in pairlist:
                pair = self.attributes.get("market") + "_" + pair
                # Traded on our exchange?
                if pair in self.pair_data:
                    self.logging.debug(pair + " added to the list")
                    pairs.append(pair)
                else:
                    self.logging.info(
                        pair
                        + " not included because pair is blacklisted on 3commas or in token_denylist "
                        + "or not tradable on '"
                        + self.attributes.get("account_name")
                        + "'"
                    )

        self.logging.debug("Pairs after topcoin filter " + str(pairs))

        # Run filters to adapt mad according to pair list - multibot creation with mad=1 possible
        if self.attributes.get("limit_initial_pairs", False):
            # Limit pairs to the maximal deals (mad)
            if self.attributes.get("mad") == 1:
                maxpairs = 1
            elif len(pairs) >= self.attributes.get("mad"):
                maxpairs = self.attributes.get("mad")
            else:
                maxpairs = len(pairs)
            pairs = pairs[0:maxpairs]
            self.logging.debug("Pairs after limit initial pairs filter " + str(pairs))

        # Adapt mad if pairs are under value
        mad = self.adjust_mad(pairs, mad)
        if not dealmode_signal:
            self.logging.info(
                str(len(pairs))
                + " out of 30 symrank pairs selected "
                + str(pairs)
                + ". Maximum active deals (mad) set to "
                + str(mad)
                + " out of "
                + str(maxdeals),
                True,
            )

        # Create new multibot
        if self.bot_data["name"] == "" and mad > 0:
            self.logging.info(
                "Creating multi bot '" + self.botname + "'",
                True,
            )
            self.report_funds_needed(maxdeals)
            # for creating a multibot at least 2 pairs needed
            if mad == 1:
                pairs.append(self.attributes.get("market") + "_BTC")
                self.logging.info(
                    "For creating a multipair bot at least 2 pairs needed, adding "
                    + pairs[1]
                    + " to signal pair "
                    + pairs[0],
                    True,
                )
                mad = 2

            error, data = self.p3cw.request(
                entity="bots",
                action="create_bot",
                additional_headers={"Forced-Mode": self.attributes.get("trade_mode")},
                payload=self.payload(pairs, mad, new_bot=True),
            )

            if error:
                self.logging.error(error["msg"])
                if error["msg"].find("Read timed out") > -1:
                    self.logging.error(
                        "HTTPS connection problems to 3commas - exiting program - retry later",
                        True,
                    )
                    sys.exit(-1)
            else:
                self.bot_data = data
                if (
                    not self.attributes.get("ext_botswitch", False)
                    and not self.asyncState.btc_downtrend
                    and self.asyncState.fgi_allows_trading
                ):
                    self.enable()

                elif self.attributes.get("ext_botswitch", False):
                    self.logging.info(
                        "ext_botswitch set to true, bot has to be enabled by external TV signal",
                        True,
                    )

                if dealmode_signal:
                    successful_deal = self.new_deal(pair)
                elif self.attributes.get("random_pair", "False"):
                    successful_deal = self.new_deal(triggerpair="")

        # Update existing multibot
        elif mad > 0:
            self.logging.info(
                "Updating multi bot '"
                + self.bot_data["name"]
                + "' (botid: "
                + str(self.bot_data["id"])
                + ") with filtered pair(s)",
                True,
            )
            self.report_funds_needed(maxdeals)

            error, data = self.p3cw.request(
                entity="bots",
                action="update",
                action_id=str(self.bot_data["id"]),
                additional_headers={"Forced-Mode": self.attributes.get("trade_mode")},
                payload=self.payload(pairs, mad, new_bot=False),
            )

            if error:
                self.logging.error(error["msg"])
            else:
                self.bot_data = data
                self.logging.debug("Pairs: " + str(pairs))
                if (
                    not self.attributes.get("ext_botswitch", False)
                    and not self.asyncState.btc_downtrend
                    and self.asyncState.fgi_allows_trading
                ):
                    self.enable()
                elif self.attributes.get("ext_botswitch", False):
                    self.logging.info(
                        "ext_botswitch set to true, bot enabling/disabling has to be managed by external TV signal",
                        True,
                    )
        else:
            self.logging.info(
                "No (filtered) pairs left for multi bot. Either weak market phase or symrank/topcoin filter too strict. Bot will be disabled to wait for better times",
                True,
            )
            self.disable()

    def trigger(self, random_only=False):
        # Updates multi bot with new pairs
        pair = ""
        mad = self.attributes.get("mad")
        dealmode_signal = (
            self.attributes.get("deal_mode", "", self.asyncState.dca_conf) == "signal"
        )

        # Check if data of 3cqsbot is given (dict format), else search for existing one in the list before creating a new one
        if not isinstance(self.bot_data, dict):
            self.search_rename_3cqsbot()

        if not random_only and (
            self.asyncState.bot_active
            or self.attributes.get("continuous_update", False)
        ):
            pair = self.tg_data["pair"]

            if (
                self.attributes.get("continuous_update", False)
                and not self.asyncState.bot_active
            ):
                self.logging.info("Continuous update active for disabled bot")
            else:
                self.logging.info(
                    "Got new 3cqs " + self.tg_data["action"] + " signal for " + pair
                )

            if self.tg_data["action"] == "START":

                # Filter pair according to topcoin criteria if set
                if self.attributes.get("topcoin_filter", False):
                    pair = self.signal.topcoin(
                        pair,
                        self.attributes.get("topcoin_limit", 3500),
                        self.attributes.get("topcoin_volume", 0),
                        self.attributes.get("topcoin_exchange", "binance"),
                        self.attributes.get("market"),
                        self.asyncState.first_topcoin_call,
                    )
                else:
                    self.logging.info("Topcoin filter disabled, not filtering pairs!")
                if pair:
                    if pair in self.bot_data["pairs"]:
                        self.logging.info(
                            pair + " is already included in the pair list"
                        )
                    else:
                        if self.attributes.get("topcoin_filter", False):
                            self.logging.info(
                                "Adding " + pair + " after passing topcoin filter", True
                            )
                        else:
                            self.logging.info("Adding " + pair, True)
                        self.bot_data["pairs"].append(pair)

            # do not remove pairs when deal_mode == "signal" to trigger deals faster when next START signal is received
            elif self.tg_data["action"] == "STOP":

                if not dealmode_signal:
                    if pair in self.bot_data["pairs"]:
                        self.logging.info("Removing " + pair, True)
                        self.bot_data["pairs"].remove(pair)
                    else:
                        self.logging.info(
                            pair + " not removed because it was not in the pair list"
                        )
                else:
                    self.logging.info(
                        pair
                        + " not removed from pair list because deal_mode is 'signal'. Keeping it for faster deals"
                    )

            mad_before = mad
            mad = self.adjust_mad(self.bot_data["pairs"], mad_before)
            if mad > mad_before:
                self.logging.info("Adjusting mad to: " + str(mad), True)

            # even with no pair, always update get an update of active / finished deals
            error, data = self.p3cw.request(
                entity="bots",
                action="update",
                action_id=str(self.bot_data["id"]),
                additional_headers={"Forced-Mode": self.attributes.get("trade_mode")},
                payload=self.payload(self.bot_data["pairs"], mad, new_bot=False),
            )

            if error:
                self.logging.error(error["msg"])
            else:
                self.bot_data = data

            # avoid triggering a deal if STOP signal
            if self.tg_data["action"] == "STOP":
                pair = ""

        # if random_only == true and deal_mode == "signal" then
        # initiate deal with a random coin (random_pair=true) from the filtered symrank pair list
        # if pair not empty and deal_mode == "signal" then initiate new deal
        # btc_downtrend always set to false if btc_pulse not used
        if (
            (random_only or pair)
            and dealmode_signal
            and self.bot_data
            and self.asyncState.bot_active
        ):
            if self.bot_data["active_deals_count"] < self.bot_data["max_active_deals"]:
                successful_deal = self.new_deal(pair)
            else:
                successful_deal = False
                if self.bot_data["max_active_deals"] == self.attributes.get(
                    "mad", "", self.asyncState.dca_conf
                ):
                    self.logging.info(
                        "Max active deals of reached, not triggering a new one.",
                        True,
                    )
                else:
                    self.logging.info(
                        "Deal with this pair already active, not triggering a new one."
                    )
            self.report_deals(successful_deal)
