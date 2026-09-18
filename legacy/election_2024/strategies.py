trades = [

    {
        "trade_name": "Harris for president and all GOP electoral college margins",
        "subtitle": "This trade assumes Harris winning is the opposite of the GOP winning every category "
                    "in the electoral college. The risk is Harris could be replaced or die causing one "
                    "side of this trade to not work.",
        "side_a_trades": [
            ("2024-presidential-election-gop-wins-by-1-4", "Yes"),
            ("2024-presidential-election-gop-wins-by-5-14", "Yes"),
            ("2024-presidential-election-gop-wins-by-15-34", "Yes"),
            ("2024-presidential-election-gop-wins-by-35-64", "Yes"),
            ("2024-presidential-election-gop-wins-by-65-104", "Yes"),
            ("2024-presidential-election-gop-wins-by-105-154", "Yes"),
            ("2024-presidential-election-gop-wins-by-155-214", "Yes"),
            ("2024-presidential-election-gop-wins-by-215", "Yes"),
        ],
        "side_b_trades": [
            ("will-kamala-harris-win-the-2024-us-presidential-election", "Yes"),
        ],
        "method": "balanced"
    },



    {
        "trade_name": "DEM and REP electoral college all no",
        "subtitle": "bet no on all DEM and REP electoral college positions",
        "positions": [
            ("2024-presidential-election-gop-wins-by-215", "No"),
            ("2024-presidential-election-gop-wins-by-155-214", "No"),
            ("2024-presidential-election-gop-wins-by-65-104", "No"),
            ("2024-presidential-election-gop-wins-by-35-64", "No"),
            ("2024-presidential-election-gop-wins-by-15-34", "No"),
            ("2024-presidential-election-gop-wins-by-1-4", "No"),
            ("2024-presidential-election-gop-wins-by-5-14", "No"),
            ("2024-presidential-election-gop-wins-by-105-154", "No"),
            ("2024-presidential-election-democrats-win-by-0-4", "No"),
            ("2024-presidential-election-democrats-win-by-5-14", "No"),
            ("2024-presidential-election-democrats-win-by-15-34", "No"),
            ("2024-presidential-election-democrats-win-by-35-64", "No"),
            ("2024-presidential-election-democrats-win-by-65-104", "No"),
            ("2024-presidential-election-democrats-win-by-105-154", "No"),
            ("2024-presidential-election-democrats-win-by-155-214", "No"),
            ("2024-presidential-election-democrats-win-by-215", "No"),
        ],
        "method": "all_no"
    },

    {
        "trade_name": "DEM and GOP popular vote all no",
        "subtitle": "bet no on all DEM and GOP popular vote positions",
        "positions": [
            ("gop-wins-popular-vote-by-more-than-7", "No"),
            ("gop-wins-popular-vote-by-6-7", "No"),
            ("gop-wins-popular-vote-by-5-6", "No"),
            ("gop-wins-popular-vote-by-4-5", "No"),
            ("gop-wins-popular-vote-by-3-4", "No"),
            ("gop-wins-popular-vote-by-2-3", "No"),
            ("gop-wins-popular-vote-by-1-2", "No"),
            ("gop-wins-popular-vote-by-0-1", "No"),

            ("democrats-win-popular-vote-by-over-7", "No"),
            ("democrats-win-popular-vote-by-6-7", "No"),
            ("democrats-win-popular-vote-by-5-6", "No"),
            ("democrats-win-popular-vote-by-4-5", "No"),
            ("democrats-win-popular-vote-by-3-4", "No"),
            ("democrats-win-popular-vote-by-2-3", "No"),
            ("democrats-win-popular-vote-by-1-2", "No"),
            ("democrats-win-popular-vote-by-0-1", "No"),

        ],
        "method": "all_no"
    },

    {
        "trade_name": ""
                      "Trump for president and DEMS win presidency and popular",
        "subtitle": "If DEMS win presidency they will win popular vote so this is a direct hedge "
                    "on Trump getting elected ",
        "side_a_trades": [
            ("will-a-democrat-win-the-popular-vote-and-the-presidency", "Yes"),
        ],
        "side_b_trades": [
            ("will-donald-trump-win-the-2024-us-presidential-election", "Yes"),
        ],
        "method": "balanced"
    },

    {
        "trade_name": ""
                      "DEM win presidency hedged on Trump",
        "subtitle": "REP win presidency hedged on Kamala winning",
        "side_a_trades": [
            ("which-party-will-win-the-2024-united-states-presidential-election", "Democratic"),
        ],
        "side_b_trades": [
            ("will-donald-trump-win-the-2024-us-presidential-election", "Yes"),
        ],
        "method": "balanced"
    },

    {
        "trade_name": ""
                      "REP win presidency hedged on Kamala",
        "subtitle": "REP win presidency hedged on Kamala winning",
        "side_a_trades": [
            ("which-party-will-win-the-2024-united-states-presidential-election", "Republican"),
        ],
        "side_b_trades": [
            ("will-kamala-harris-win-the-2024-us-presidential-election", "Yes"),
        ],
        "method": "balanced"
    },

    {
        "trade_name": ""
                      "Trump popular vote hedged with popular vote margins",
        "subtitle": "Trump wins popular vote and then buy all the margins for DEMS on the popular vote",
        "side_a_trades": [
            ("will-donald-trump-win-the-popular-vote-in-the-2024-presidential-election", "Yes"),
        ],
        "side_b_trades": [
            ("democrats-win-popular-vote-by-0-1", "Yes"),
            ("democrats-win-popular-vote-by-1-2", "Yes"),
            ("democrats-win-popular-vote-by-2-3", "Yes"),
            ("democrats-win-popular-vote-by-3-4", "Yes"),
            ("democrats-win-popular-vote-by-4-5", "Yes"),
            ("democrats-win-popular-vote-by-5-6", "Yes"),
            ("democrats-win-popular-vote-by-6-7", "Yes"),
            ("democrats-win-popular-vote-by-over-7", "Yes"),

        ],
        "method": "balanced"
    },

    {
        "trade_name": ""
                      "Kamala popular vote hedged with popular vote margins",
        "subtitle": "Kamala wins popular vote and then buy all the margins for REP on the popular vote",
        "side_a_trades": [
            ("will-kamala-harris-win-the-popular-vote-in-the-2024-presidential-election", "Yes"),
        ],
        "side_b_trades": [
            ("gop-wins-popular-vote-by-0-1", "Yes"),
            ("gop-wins-popular-vote-by-1-2", "Yes"),
            ("gop-wins-popular-vote-by-2-3", "Yes"),
            ("gop-wins-popular-vote-by-3-4", "Yes"),
            ("gop-wins-popular-vote-by-4-5", "Yes"),
            ("gop-wins-popular-vote-by-5-6", "Yes"),
            ("gop-wins-popular-vote-by-6-7", "Yes"),
            ("gop-wins-popular-vote-by-more-than-7", "Yes"),

        ],
        "method": "balanced"
    },

    {
    "trade_name": "DEM popular vote all no",
    "subtitle": "bet no on all DEM popular vote positions",
    "positions": [
        ("democrats-win-popular-vote-by-over-7", "No"),
        ("democrats-win-popular-vote-by-6-7", "No"),
        ("democrats-win-popular-vote-by-5-6", "No"),
        ("democrats-win-popular-vote-by-4-5", "No"),
        ("democrats-win-popular-vote-by-3-4", "No"),
        ("democrats-win-popular-vote-by-2-3", "No"),
        ("democrats-win-popular-vote-by-1-2", "No"),
        ("democrats-win-popular-vote-by-0-1", "No"),

    ],
    "method": "all_no"
    },

    {
    "trade_name": "GOP popular vote all no",
    "subtitle": "bet no on all GOP popular vote positions",
    "positions": [
        ("gop-wins-popular-vote-by-more-than-7", "No"),
        ("gop-wins-popular-vote-by-6-7", "No"),
        ("gop-wins-popular-vote-by-5-6", "No"),
        ("gop-wins-popular-vote-by-4-5", "No"),
        ("gop-wins-popular-vote-by-3-4", "No"),
        ("gop-wins-popular-vote-by-2-3", "No"),
        ("gop-wins-popular-vote-by-1-2", "No"),
        ("gop-wins-popular-vote-by-0-1", "No"),
    ],
    "method": "all_no"
    },



    {
    "trade_name": "Trump for president and all DEM electoral college margins",
    "subtitle" : "This trade assumes Trump winning is the opposite of the DEM winning every category "
                 "in the electoral college. The risk is Trump could be replaced or die causing one "
                 "side of this trade to not work.",
    "side_a_trades": [
        ("2024-presidential-election-democrats-win-by-0-4", "Yes"),
        ("2024-presidential-election-democrats-win-by-5-14", "Yes"),
        ("2024-presidential-election-democrats-win-by-15-34", "Yes"),
        ("2024-presidential-election-democrats-win-by-35-64", "Yes"),
        ("2024-presidential-election-democrats-win-by-65-104", "Yes"),
        ("2024-presidential-election-democrats-win-by-105-154", "Yes"),
        ("2024-presidential-election-democrats-win-by-155-214", "Yes"),
        ("2024-presidential-election-democrats-win-by-215", "Yes"),
    ],
    "side_b_trades": [
        ("will-donald-trump-win-the-2024-us-presidential-election", "Yes"),
    ],
    "method": "balanced"

    },



    {
    "trade_name": "Electoral College All GOP ALL DEM YES",
    "subtitle": "This is a truely hedged trade. Bet all REP electoral college slots and bet all"
                "DEM electoral college slots",
    "side_a_trades": [
        ("2024-presidential-election-gop-wins-by-1-4", "Yes"),
        ("2024-presidential-election-gop-wins-by-5-14", "Yes"),
        ("2024-presidential-election-gop-wins-by-15-34", "Yes"),
        ("2024-presidential-election-gop-wins-by-35-64", "Yes"),
        ("2024-presidential-election-gop-wins-by-65-104", "Yes"),
        ("2024-presidential-election-gop-wins-by-105-154", "Yes"),
        ("2024-presidential-election-gop-wins-by-155-214", "Yes"),
        ("2024-presidential-election-gop-wins-by-215", "Yes"),
    ],
    "side_b_trades": [
        ("2024-presidential-election-democrats-win-by-0-4", "Yes"),
        ("2024-presidential-election-democrats-win-by-5-14", "Yes"),
        ("2024-presidential-election-democrats-win-by-15-34", "Yes"),
        ("2024-presidential-election-democrats-win-by-35-64", "Yes"),
        ("2024-presidential-election-democrats-win-by-65-104", "Yes"),
        ("2024-presidential-election-democrats-win-by-105-154", "Yes"),
        ("2024-presidential-election-democrats-win-by-155-214", "Yes"),
        ("2024-presidential-election-democrats-win-by-215", "Yes"),
    ],
    "method": "balanced"
    },

    {
    "trade_name": "DEM electoral college all no",
    "subtitle": "bet no on all DEM electoral college positions",
    "positions": [
        ("2024-presidential-election-democrats-win-by-0-4", "No"),
        ("2024-presidential-election-democrats-win-by-5-14", "No"),
        ("2024-presidential-election-democrats-win-by-15-34", "No"),
        ("2024-presidential-election-democrats-win-by-35-64", "No"),
        ("2024-presidential-election-democrats-win-by-65-104", "No"),
        ("2024-presidential-election-democrats-win-by-105-154", "No"),
        ("2024-presidential-election-democrats-win-by-155-214", "No"),
        ("2024-presidential-election-democrats-win-by-215", "No"),
    ],
    "method": "all_no"
    },

    {
    "trade_name": "REP electoral college all no",
    "subtitle": "bet no on all REP electoral college positions",
    "positions": [
        ("2024-presidential-election-gop-wins-by-1-4", "No"),
        ("2024-presidential-election-gop-wins-by-5-14", "No"),
        ("2024-presidential-election-gop-wins-by-15-34", "No"),
        ("2024-presidential-election-gop-wins-by-35-64", "No"),
        ("2024-presidential-election-gop-wins-by-65-104", "No"),
        ("2024-presidential-election-gop-wins-by-105-154", "No"),
        ("2024-presidential-election-gop-wins-by-155-214", "No"),
        ("2024-presidential-election-gop-wins-by-215", "No"),
        ],
    "method": "all_no"
    },

    {
        "trade_name": "FED Rates in Sept all no",
        "subtitle": "bet no on all FED possibilities in Sept",
        "positions": [
            ("fed-decreases-interest-rates-by-50-bps-after-september-2024-meeting", "No"),
            ("fed-decreases-interest-rates-by-25-bps-after-september-2024-meeting", "No"),
            ("no-change-in-fed-interest-rates-after-2024-september-meeting", "No"),
        ],
        "method": "all_no"
    },

    {
        "trade_name": "Balance of power all no",
        "subtitle": "bet no on some of the balance of power outcomes",
        "positions": [
            ("2024-balance-of-power-r-prez-r-senate-r-house", "No"),
            ("2024-election-democratic-presidency-and-house-republican-senate", "No"),
            ("democratic-sweep-in-2024-election", "No"),
            ("2024-balance-of-power-republican-presidency-and-senate-democratic-house", "No"),
        ],
        "method": "all_no"
    },

    {
    "trade_name": "Presidential party D President Trump",
    "subtitle": "Bet on the candidate to win and their party to lose hedging the bet",
    "side_a_trades": [
        ("which-party-will-win-the-2024-united-states-presidential-election", "Democratic"),
        ],
    "side_b_trades": [
        ("will-kamala-harris-win-the-2024-us-presidential-election", "No"),

        ],
    "method": "balanced"
    },

    {
        "trade_name": "Presidential party R President Harris",
        "subtitle": "Bet on the candidate to win and their party to lose hedging the bet",
        "side_a_trades": [
            ("which-party-will-win-the-2024-united-states-presidential-election", "Republican"),
        ],
        "side_b_trades": [
            ("will-kamala-harris-win-the-2024-us-presidential-election", "Yes"),

        ],
        "method": "balanced"
    },


    # {
    #     "trade_name": "Trump Vance ticket and vance replaced",
    #     "subtitle": "Betting on the trump and vance ticket as well as vanced replaced",
    #     "side_a_trades": [
    #         ("will-trump-vance-be-gop-ticket-on-election-day", "Yes"),
    #     ],
    #     "side_b_trades": [
    #         ("jd-vance-steps-down-as-republican-vp-nominee", "Yes"),
    #
    #     ],
    #     "method": "balanced"
    # },

    {
        "trade_name": "Other DEM wins election no and DEM wins election Yes",
        "subtitle": "Taking trade on other dem besides biden to win but then saying DEMS win Presidency",
        "side_a_trades": [
            ("democrat-other-than-biden-wins-the-presidential-election", "No"),
        ],
        "side_b_trades": [
            ("which-party-will-win-the-2024-united-states-presidential-election", "Democratic"),

        ],
        "method": "balanced"
    },

    {
        "trade_name": "538 call election no and buy predicted candidate",
        "subtitle": "538 will probably call the election correctly as indicated by the numbers. But if the day before"
                    "the election as long as you buy the same number of shares as the winning candidate cheaper than"
                    "the inverse of the price you paid for these shares you will profit",
        "side_a_trades": [
            ("will-538-correctly-call-the-presidential-election", "No"),
        ],
        "side_b_trades": [
            ("which-party-will-win-the-2024-united-states-presidential-election", "Republican"),

        ],
        "method": "balanced"
    },


    {
        "trade_name": "Nate Silver call election no and buy Nates predicted candidate",
        "subtitle": "Nate Silver will probably call the election correctly as indicated by the numbers. But if the day before"
                    "the election as long as you buy the same number of shares as the winning candidate cheaper than"
                    "the inverse of the price you paid for these shares you will profit",
        "side_a_trades": [
            ("will-nate-silver-correctly-call-the-presidential-election", "No"),
        ],
        "side_b_trades": [
            ("which-party-will-win-the-2024-united-states-presidential-election", "Republican"),

        ],
        "method": "balanced"
    },

    # {
    #     "trade_name": "DEM solid red No",
    #     "subtitle": "The main state the DEMS have a chance in is OH. So the opposite of DEMS winning a"
    #                 "solid red state, no is the DEMS actually winning the most probable state OH(10.5%)"
    #                 ". All of the rest of these states carry a < 10% chance",
    #     "side_a_trades": [
    #         ("us-presidential-election-democrats-win-a-solid-red-state", "No"),
    #     ],
    #     "side_b_trades":
    #         [
    #             # ("will-a-democrat-win-alabama-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-democrat-win-alaska-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-democrat-win-arkansas-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-democrat-win-idaho-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-democrat-win-indiana-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-democrat-win-iowa-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-democrat-win-kansas-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-democrat-win-kentucky-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-democrat-win-louisiana-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-democrat-win-mississippi-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-democrat-win-missouri-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-democrat-win-montana-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-democrat-win-nebraska-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-democrat-win-north-dakota-in-the-2024-us-presidential-election", "Yes"),
    #             ("will-a-democrat-win-ohio-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-democrat-win-oklahoma-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-democrat-win-south-carolina-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-democrat-win-south-dakota-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-democrat-win-tennessee-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-democrat-win-utah-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-democrat-win-west-virginia-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-democrat-win-wyoming-in-the-2024-us-presidential-election", "Yes")
    #         ],
    #     "method": "balanced"
    #
    # },

    # {
    #     "trade_name": "REP solid blue No",
    #     "subtitle": "The main state the REPS have a chance in is VA. So the opposite of REPS winning a"
    #                 "solid blue state, no is the REPS actually winning the most probable state VA(15%). All"
    #                 "of the rest of these states carry a < 10% chance",
    #     "side_a_trades": [
    #         ("presidential-election-republicans-win-a-solid-blue-state", "No"),
    #     ],
    #     "side_b_trades":
    #         [
    #             # ("will-a-republican-win-california-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-republican-win-colorado-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-republican-win-connecticut-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-republican-win-delaware-presidential-election", "Yes"),
    #             # ("will-a-republican-win-hawaii-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-republican-win-illinois-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-republican-win-maryland-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-republican-win-massachusetts-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-republican-win-new-jersey-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-republican-win-new-mexico-presidential-election", "Yes"),
    #             # ("will-a-republican-win-new-york-presidential-election", "Yes"),
    #             # ("will-a-republican-win-oregon-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-republican-win-rhode-island-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-republican-win-vermont-in-the-2024-us-presidential-election", "Yes"),
    #             ("will-a-republican-win-virginia-in-the-2024-us-presidential-election", "Yes"),
    #             # ("will-a-reoublican-win-washington-in-the-2024-us-presidential-election", "Yes")
    #         ],
    #     "method": "balanced"
    #
    # },


    # {
    #     "trade_name": "Trump wins every swing state yes",
    #     "subtitle": "This trade bets that trump will win every swing state(cheap). Then it bets that "
    #                 "harris will win MI(swing state). Harris is most favored to win MI 46.5/43.6 by "
    #                 "Nate Silver and 61% on Polymarket. The theory here is if Trump wins MI then he"
    #                 "should also win the other swing states given that this is the largest margin state.",
    #     "side_a_trades": [
    #         ("trump-wins-every-swing-state", "Yes"),
    #     ],
    #     "side_b_trades": [  # ("will-a-democrat-win-arizona-presidential-election", "Yes"),
    #         # ("will-a-democrat-win-georgia-presidential-election", "Yes"),
    #         ("will-a-democrat-win-michigan-presidential-election", "Yes"),
    #         # ("will-a-democrat-win-nevada-presidential-election", "Yes"),
    #         # ("will-a-democrat-win-north-carolina-presidential-election", "Yes"),
    #         # ("will-a-democrat-win-pennsylvania-presidential-election", "Yes"),
    #         # ("will-a-democrat-win-wisconsin-presidential-election", "Yes"),
    #
    #     ],
    #     "method": "balanced"
    #
    # },


    # {
    # "trade_name": "Harris wins every swing state yes",
    # "subtitle": "This trade bets that Harris will win every swing state(cheap). Then it bets that "
    #             "Trump will win GA(swing state). Trump is most favored to win GA 46.8/45.3 by "
    #             "Nate Silver and 61% on Polymarket. The theory here is if Harris wins GA then she"
    #             "should also win the other swing states given that this is the largest margin state.",            "side_a_trades": [
    #     ("will-kamala-harris-win-every-swing-state", "Yes"),
    # ],
    # "side_b_trades": [
    #     # ("will-a-democrat-win-arizona-presidential-election", "No"),
    #     ("will-a-democrat-win-georgia-presidential-election", "No"),
    #     # ("will-a-democrat-win-michigan-presidential-election", "No"),
    #     # ("will-a-democrat-win-nevada-presidential-election", "No"),
    #     # ("will-a-democrat-win-pennsylvania-presidential-election", "No"),
    #     # ("will-a-democrat-win-wisconsin-presidential-election", "No"),
    # ],
    # "method": "balanced"
    #
    # },
    #
    # {
    #     "trade_name": "REP flip Biden State",
    #     "subtitle": "test",
    #     "side_a_trades": [
    #         ("republicans-flip-a-2020-biden-state", "No"),
    #     ],
    #     "side_b_trades": [
    #         ("will-a-democrat-win-georgia-presidential-election", "No"),
    #     ],
    #     "method": "balanced"
    #
    # },
    # {
    #     "trade_name": "DEM flip Trump State",
    #     "subtitle": "test",
    #     "side_a_trades": [
    #         ("dems-flip-a-2020-trump-state", "No"),
    #     ],
    #     "side_b_trades": [
    #         ("will-a-democrat-win-north-carolina-presidential-election", "Yes"),
    #     ],
    #     "method": "balanced"
    # },

    ]